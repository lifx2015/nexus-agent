"""从 SkillHub 更新本地技能包（显式用户触发的写操作）。

安全设计：
  1. 只更新 kind=skill 且路径存在（status=active）的发现项
  2. zip 校验：zipfile 完整性 + 拒绝路径穿越（zip-slip）+ 必须含 SKILL.md
  3. 完整性：对比签名接口的 package_md5（缺失或不一致仅提示，不阻断）
  4. 先备份：旧技能包完整复制到 data_home/skillhub/backups/<slug>/<时间戳>/，
     每技能保留最近 BACKUP_KEEP 份
  5. 替换后刷新发现项（size/mtime/fingerprint/summary），并在 extra.hub 记录来源
"""
from __future__ import annotations

import hashlib
import io
import re
import shutil
import threading
import zipfile
from datetime import datetime
from pathlib import Path

from app.core.config import Settings
from app.skillhub.client import SkillHubClient
from app.skillhub.match import local_skill_dir, normalize_slug, read_local_version

BACKUP_KEEP = 3
_UPDATE_LOCK = threading.Lock()
_TS_RE = re.compile(r"^\d{8}-\d{6}$")


class UpdateError(RuntimeError):
    """更新失败（可携带已完成的备份路径用于提示）。"""

    def __init__(self, message: str, backup_dir: str = ""):
        super().__init__(message)
        self.backup_dir = backup_dir


def backup_root(settings: Settings) -> Path:
    return settings.data_home / "skillhub" / "backups"


def _zip_root_prefix(zf: zipfile.ZipFile) -> str:
    """若全部文件位于同一顶层目录下，返回该前缀；否则返回 ''（平铺结构）。"""
    files = [n for n in zf.namelist() if not n.endswith("/")]
    if not files:
        return ""
    tops = {n.split("/", 1)[0] for n in files}
    if len(tops) == 1:
        top = tops.pop()
        if all(n.startswith(top + "/") for n in files):
            return top + "/"
    return ""


def _validate_zip(data: bytes, expect_skill_md: bool = True) -> int:
    """校验 zip 合法性与安全，返回文件数；失败抛 UpdateError。"""
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as e:
        raise UpdateError(f"zip 包损坏: {e}") from e
    if zf.testzip() is not None:
        raise UpdateError("zip 包内部校验失败（CRC 错误）")
    root = _zip_root_prefix(zf)
    count = 0
    for info in zf.infolist():
        if info.is_dir():
            continue
        rel = info.filename[len(root):] if root else info.filename
        # zip-slip：规范化后必须仍在目标目录内
        if rel.startswith("/") or ".." in rel.split("/"):
            raise UpdateError(f"zip 含非法路径（拒绝更新）: {info.filename}")
        count += 1
    if expect_skill_md:
        names = {n[len(root):].lower() if root else n.lower() for n in zf.namelist()}
        if "skill.md" not in names:
            raise UpdateError("技能包内未找到 SKILL.md，已拒绝更新")
    return count


def _do_backup(settings: Settings, slug: str, skill_dir: Path) -> str:
    """复制旧技能包到备份目录，清理超额旧备份。返回备份路径。"""
    root = backup_root(settings)
    safe_slug = re.sub(r"[^A-Za-z0-9._-]+", "_", slug or "skill")
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    dest = root / safe_slug / ts
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(skill_dir, dest, symlinks=True)
    # 保留最近 BACKUP_KEEP 份（目录名即时间戳，字典序=时间序）
    snaps = sorted(
        (p for p in dest.parent.iterdir() if p.is_dir() and _TS_RE.match(p.name)),
        reverse=True,
    )
    for old in snaps[BACKUP_KEEP:]:
        shutil.rmtree(old, ignore_errors=True)
    return str(dest)


def _replace_dir(skill_dir: Path, data: bytes) -> int:
    """清空技能目录并解包；返回写入文件数。调用前须已完成备份。"""
    zf = zipfile.ZipFile(io.BytesIO(data))
    root = _zip_root_prefix(zf)

    # 清空现有内容（保留目录本身）
    for entry in skill_dir.iterdir():
        if entry.is_dir() and not entry.is_symlink():
            shutil.rmtree(entry, ignore_errors=False)
        else:
            entry.unlink(missing_ok=True)

    count = 0
    for info in zf.infolist():
        rel = info.filename[len(root):] if root else info.filename
        target = (skill_dir / rel).resolve()
        if not str(target).startswith(str(skill_dir.resolve())):
            raise UpdateError(f"解包路径越界（拒绝）: {info.filename}")
        if info.is_dir():
            target.mkdir(parents=True, exist_ok=True)
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        with zf.open(info) as src, target.open("wb") as dst:
            shutil.copyfileobj(src, dst)
        count += 1
    return count


def resolve_hub(rec: dict, skill_dir: Path, client: SkillHubClient) -> tuple[str, str, str]:
    """解析记录的仓库坐标 (slug, namespace, latest_version)；未收录返回 ("", "", "")。

    下载必须使用仓库 slug（可能与本地目录名不同，如目录带版本后缀）。
    """
    cands = list(dict.fromkeys(
        c for c in (normalize_slug(rec.get("name") or ""), normalize_slug(skill_dir.name)) if c
    ))
    if not cands:
        return "", "", ""
    by_slug: dict[str, dict] = {}
    for b in client.batch(cands):
        sk = b.get("skill") or {}
        if sk.get("slug"):
            by_slug[sk["slug"]] = b
    for c in cands:
        b = by_slug.get(c)
        if b is None:
            continue
        sk = b.get("skill") or {}
        ns = b.get("namespace") or {}
        latest = (b.get("latestVersion") or {}).get("version") or sk.get("version") or ""
        return sk.get("slug") or c, ns.get("handle") or "", latest
    return "", "", ""


def update_skill(
    settings: Settings,
    store,
    client: SkillHubClient,
    record_id: int,
    version: str = "",
    backup: bool = True,
) -> dict:
    """更新一条发现技能记录到 SkillHub 指定版本（默认 latest）。"""
    rec = store.get(record_id)
    if rec is None:
        raise UpdateError("记录不存在")
    if rec.get("kind") != "skill":
        raise UpdateError("仅支持技能（skill）类型记录")
    skill_dir = local_skill_dir(rec)
    if skill_dir is None or not skill_dir.is_dir():
        raise UpdateError(f"本地技能目录不存在: {rec.get('path')}")
    if skill_dir.name.lower() in {"skills", "skill"}:
        raise UpdateError(
            "技能目录为通用 skills 容器目录（单文件布局），为避免误删同级其他技能，拒绝更新"
        )

    slug, ns, latest = resolve_hub(rec, skill_dir, client)
    if not slug:
        raise UpdateError("SkillHub 未收录该技能")
    if not version:
        version = latest
        if not version:
            raise UpdateError("无法确定 SkillHub 最新版本号")
    # batch 响应可能缺 namespace；签名校验接口需要它 —— 用详情接口补齐
    if not ns:
        detail = client.skill(slug)
        ns = ((detail.get("namespace") or {}).get("handle")) or ""

    with _UPDATE_LOCK:
        from_version = read_local_version(skill_dir)
        data = client.download_bytes(slug, version, ns)

        # 完整性（建议性）：package_md5 不一致只提示
        md5 = hashlib.md5(data).hexdigest()
        md5_verified = None
        sig = client.signature(slug, version, ns)
        if sig and sig.get("package_md5"):
            md5_verified = sig["package_md5"].lower() == md5

        # 校验 → 备份 → 替换
        file_count = _validate_zip(data)
        backup_dir = _do_backup(settings, slug, skill_dir) if backup else ""
        try:
            written = _replace_dir(skill_dir, data)
        except Exception as e:  # noqa: BLE001
            raise UpdateError(f"写入失败: {e}", backup_dir=backup_dir) from e

    # 刷新跟踪记录（体积/时间/指纹），并记录来源
    store.refresh_path(rec["path"])
    store.merge_extra(record_id, {
        "hub": {
            "slug": slug,
            "namespace": ns,
            "version": version,
            "updated_at": datetime.now().isoformat(timespec="seconds"),
            "md5": md5,
            "md5_verified": md5_verified,
        }
    })
    return {
        "ok": True,
        "record_id": record_id,
        "slug": slug,
        "namespace": ns,
        "skill_dir": str(skill_dir),
        "from_version": from_version,
        "to_version": version,
        "backup_dir": backup_dir,
        "files": written,
        "file_count_expected": file_count,
        "md5": md5,
        "md5_verified": md5_verified,
    }
