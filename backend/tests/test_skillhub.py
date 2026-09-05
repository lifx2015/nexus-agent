"""M9 SkillHub 技能仓库：版本逻辑 / 匹配 / 更新（离线，fake client + 临时目录）。"""
from __future__ import annotations

import hashlib
import io
import zipfile
from pathlib import Path

import pytest

from app.core.config import Settings
from app.scanners.models import DiscoveredItem, ItemKind
from app.skillhub.client import SkillHubError
from app.skillhub.match import (
    compare_local_skills,
    compare_versions,
    normalize_slug,
    parse_version,
)
from app.skillhub.updater import UpdateError, _validate_zip, update_skill
from app.skillhub.store import SkillHubStore, empty_summary, recount_states
from app.stores.discovered import DiscoveredStore


# ---- 版本解析与比较 --------------------------------------------------------
def test_parse_version_basic():
    assert parse_version("2.23.2") == ((2, 23, 2), "")
    assert parse_version("v1.0") == ((1, 0), "")
    assert parse_version("1") == ((1,), "")
    assert parse_version("1.0.0-beta.1") == ((1, 0, 0), "beta.1")
    assert parse_version("abc") is None
    assert parse_version("") is None


def test_compare_versions():
    assert compare_versions("2.23.2", "2.33.0") == -1
    assert compare_versions("2.33.0", "2.33.0") == 0
    assert compare_versions("3.0.0", "2.99.99") == 1
    assert compare_versions("1.0", "1.0.0") == 0  # 位数补齐
    assert compare_versions("1.0.0", "1.0.0-beta.1") == 1  # 正式版 > 预发布
    assert compare_versions("x", "1.0") is None  # 无法解析


def test_normalize_slug():
    assert normalize_slug("cloudbase") == "cloudbase"
    assert normalize_slug("Memory 1.0.2") == "memory"
    assert normalize_slug("aminer-open-academic-1.0.5") == "aminer-open-academic"
    assert normalize_slug("1password-1.0.1") == "1password"
    assert normalize_slug("Find Skills") == "find-skills"
    assert normalize_slug("") == ""


# ---- zip 校验 --------------------------------------------------------------
def _make_zip(entries: dict[str, str], arc_prefix: str = "") -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, content in entries.items():
            zf.writestr(arc_prefix + name, content)
    return buf.getvalue()


def test_validate_zip_ok():
    data = _make_zip({"SKILL.md": "---\nname: x\n---\n", "references/a.md": "hi"})
    assert _validate_zip(data) == 2


def test_validate_zip_requires_skill_md():
    with pytest.raises(UpdateError):
        _validate_zip(_make_zip({"README.md": "no skill md"}))


def test_validate_zip_rejects_traversal():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("SKILL.md", "ok")
        zf.writestr("../evil.txt", "pwn")
    with pytest.raises(UpdateError, match="非法路径"):
        _validate_zip(buf.getvalue())


# ---- 更新流程（fake client + 真实 store）-----------------------------------
class FakeClient:
    def __init__(self, zip_data: bytes, latest: str = "2.33.0", pkg_md5: str | None = None):
        self.zip_data = zip_data
        self.latest = latest
        self.pkg_md5 = pkg_md5
        self.downloaded: list[tuple] = []

    def batch(self, slugs):
        return [
            {
                "skill": {"slug": "cloudbase", "version": self.latest},
                "namespace": {"handle": "tencent-adm"},
                "latestVersion": {"version": self.latest, "changelog": "ch"},
            }
        ]

    def download_bytes(self, slug, version="", namespace=""):
        self.downloaded.append((slug, version, namespace))
        return self.zip_data

    def signature(self, slug, version, namespace=""):
        if self.pkg_md5 is None:
            return None
        return {"package_md5": self.pkg_md5}

    def skill(self, slug):
        return {"namespace": {"handle": "tencent-adm"}}


def _setup(tmp_path: Path):
    settings = Settings(data_home=tmp_path)
    store = DiscoveredStore(settings)
    skill_dir = tmp_path / "skills" / "cloudbase"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\nname: cloudbase\nversion: 2.23.2\n---\n# old\n", encoding="utf-8"
    )
    (skill_dir / "local-only.txt").write_text("local change", encoding="utf-8")
    item = DiscoveredItem(
        agent="opencode", kind=ItemKind.SKILL, name="cloudbase",
        path=skill_dir / "SKILL.md", size=10, mtime=1.0,
        extra={"dir": str(skill_dir), "files": 2},
    )
    store.sync([item])
    rec = store.list(kind="skill")[0]
    return settings, store, skill_dir, rec


def test_update_skill_replaces_and_backups(tmp_path):
    settings, store, skill_dir, rec = _setup(tmp_path)
    new_zip = _make_zip({
        "SKILL.md": "---\nname: cloudbase\nversion: 2.33.0\n---\n# new\n",
        "references/a.md": "doc",
        "_meta.json": "{}",
    })
    md5 = hashlib.md5(new_zip).hexdigest()
    client = FakeClient(new_zip, latest="2.33.0", pkg_md5=md5)

    result = update_skill(settings, store, client, rec["id"], backup=True)

    # 本地内容被替换
    assert "2.33.0" in (skill_dir / "SKILL.md").read_text(encoding="utf-8")
    assert (skill_dir / "references" / "a.md").is_file()
    assert not (skill_dir / "local-only.txt").exists()
    # 下载坐标正确（仓库 slug + namespace）
    assert client.downloaded[0] == ("cloudbase", "2.33.0", "tencent-adm")
    # 备份包含旧版本
    backup = Path(result["backup_dir"])
    assert backup.is_dir()
    assert "2.23.2" in (backup / "SKILL.md").read_text(encoding="utf-8")
    assert (backup / "local-only.txt").is_file()
    # md5 校验通过；跟踪记录 extra.hub 已记录
    assert result["md5_verified"] is True
    assert result["from_version"] == "2.23.2"
    got = store.get(rec["id"])
    assert got["extra"]["hub"]["version"] == "2.33.0"
    assert got["size"] != 0  # refresh_path 已刷新


def test_update_skill_zip_slip_no_side_effects(tmp_path):
    settings, store, skill_dir, rec = _setup(tmp_path)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("SKILL.md", "ok")
        zf.writestr("../evil.txt", "pwn")
    client = FakeClient(buf.getvalue())

    with pytest.raises(UpdateError):
        update_skill(settings, store, client, rec["id"], backup=True)

    # 本地未被改动，也未产生备份
    assert "2.23.2" in (skill_dir / "SKILL.md").read_text(encoding="utf-8")
    assert (settings.data_home / "skillhub" / "backups").exists() is False


def test_update_skill_top_level_folder_stripped(tmp_path):
    settings, store, skill_dir, rec = _setup(tmp_path)
    client = FakeClient(_make_zip({"SKILL.md": "v1"}, arc_prefix="pkg/"))
    result = update_skill(settings, store, client, rec["id"], backup=False)
    assert (skill_dir / "SKILL.md").read_text() == "v1"
    assert not (skill_dir / "pkg").exists()
    assert result["backup_dir"] == ""


def test_update_skill_refuses_skills_container_dir(tmp_path):
    settings = Settings(data_home=tmp_path)
    store = DiscoveredStore(settings)
    container = tmp_path / "skills"  # 通用容器目录
    container.mkdir()
    (container / "SKILL.md").write_text("---\nname: x\n---\n", encoding="utf-8")
    item = DiscoveredItem(
        agent="opencode", kind=ItemKind.SKILL, name="x",
        path=container / "SKILL.md", size=1, mtime=1.0,
        extra={"dir": str(container)},
    )
    store.sync([item])
    rec = store.list(kind="skill")[0]
    client = FakeClient(_make_zip({"SKILL.md": "v1"}))
    with pytest.raises(UpdateError, match="容器目录"):
        update_skill(settings, store, client, rec["id"])
    assert (container / "SKILL.md").exists()


# ---- 对比快照持久化 ---------------------------------------------------------
def test_snapshot_roundtrip_and_patch(tmp_path):
    store = SkillHubStore(Settings(data_home=tmp_path))
    assert store.load_snapshot() is None

    items = [
        {"record_id": 1, "state": "up-to-date", "local_version": "1.0.0", "hub_version": "1.0.0"},
        {"record_id": 2, "state": "update-available", "local_version": "0.9.0", "hub_version": "1.0.0"},
        {"record_id": 3, "state": "not-found"},
    ]
    ts = store.save_snapshot(items, recount_states(items), 1.23)
    assert ts

    snap = store.load_snapshot()
    assert snap["created_at"] == ts
    assert snap["summary"]["updatable"] == 1
    assert snap["summary"]["matched"] == 2
    assert snap["summary"]["not_found"] == 1

    # 更新技能后修正单条：2 号变为已最新，汇总随之重算
    assert store.patch_snapshot_item(2, local_version="1.0.0", hub_version="1.0.0", state="up-to-date")
    snap = store.load_snapshot()
    it = next(i for i in snap["items"] if i["record_id"] == 2)
    assert it["state"] == "up-to-date" and it["local_version"] == "1.0.0"
    assert snap["summary"]["updatable"] == 0
    assert snap["summary"]["up_to_date"] == 2

    # 不存在的条目 → False；清空快照后再修正 → False
    assert store.patch_snapshot_item(999, state="up-to-date") is False
    store.clear_snapshot()
    assert store.load_snapshot() is None
    assert store.patch_snapshot_item(1, state="up-to-date") is False


def test_recount_states():
    items = [{"state": "update-available"}, {"state": "ahead"}, {"state": "unknown-local"}]
    s = recount_states(items)
    assert s["total"] == 3 and s["matched"] == 3 and s["not_found"] == 0
    assert empty_summary()["total"] == 0

class FakeBatchClient:
    """只收录 cloudbase（2.33.0），其余 slug 查无。"""

    def batch(self, slugs):
        if "cloudbase" not in slugs:
            return []
        return [
            {
                "skill": {
                    "slug": "cloudbase", "displayName": "CloudBase",
                    "stats": {"downloads": 100},
                },
                "namespace": {"handle": "tencent-adm"},
                "latestVersion": {"version": "2.33.0", "changelog": "new stuff"},
            }
        ]


def test_compare_local_skills(tmp_path):
    settings = Settings(data_home=tmp_path)
    store = DiscoveredStore(settings)
    d1 = tmp_path / "skills" / "cloudbase"
    d2 = tmp_path / "skills" / "my-custom"
    for d in (d1, d2):
        d.mkdir(parents=True)
    (d1 / "SKILL.md").write_text("---\nname: cloudbase\nversion: 2.23.2\n---\n", encoding="utf-8")
    (d2 / "SKILL.md").write_text("---\nname: my-custom\nversion: 9.9.9\n---\n", encoding="utf-8")
    store.sync([
        DiscoveredItem(agent="a", kind=ItemKind.SKILL, name="cloudbase",
                       path=d1 / "SKILL.md", size=1, mtime=1.0, extra={"dir": str(d1)}),
        DiscoveredItem(agent="a", kind=ItemKind.SKILL, name="my-custom",
                       path=d2 / "SKILL.md", size=1, mtime=1.0, extra={"dir": str(d2)}),
    ])

    result = compare_local_skills(store, FakeBatchClient())
    items = {it["name"]: it for it in result["items"]}
    assert items["cloudbase"]["state"] == "update-available"
    assert items["cloudbase"]["hub_version"] == "2.33.0"
    assert items["cloudbase"]["local_version"] == "2.23.2"
    assert items["cloudbase"]["hub_url"].startswith("https://skillhub.cn/#/skills/tencent-adm/cloudbase")
    assert items["my-custom"]["state"] == "not-found"
    assert result["summary"]["updatable"] == 1
    assert result["summary"]["not_found"] == 1
