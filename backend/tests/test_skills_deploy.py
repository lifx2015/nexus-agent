"""技能跨智能体复用测试：目标列表 / 部署 / 覆盖 / 卸载 / 状态探测。

通过临时 USERPROFILE/HOME 把 ~/.claude 等目录隔离到 tmp_path，
NEXUS_DATA_HOME 不需要 —— deploy/undeploy 均显式传入 Settings(data_home=tmp)。
"""
from __future__ import annotations

import pytest

from app.core.config import Settings
from app.skills import ShareError, deploy, deployment_status, list_targets, undeploy
from app.skills.deploy import _do_backup  # noqa: PLC2701


@pytest.fixture()
def fake_home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    # expanduser 在 Windows 读 USERPROFILE，POSIX 读 HOME
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setenv("HOME", str(home))
    return home


def make_skill(root: Path, name: str = "demo-skill") -> Path:
    d = root / name
    (d / "scripts").mkdir(parents=True)
    (d / "SKILL.md").write_text("---\nname: demo\n---\n# demo", encoding="utf-8")
    (d / "scripts" / "run.py").write_text("print('hi')", encoding="utf-8")
    return d


def test_list_targets_covers_known_agents():
    items = list_targets()
    keys = {t["key"] for t in items}
    assert {"claude-code", "codex", "gemini-cli", "opencode", "openclaw", "hermes", "pi"} <= keys
    for t in items:
        assert t["name"] and t["primary"]


def test_deploy_and_status(fake_home, tmp_path):
    # 来源直接放在某目标的 skills 目录里（模拟「已装在 Claude 下」的真实场景）
    src = make_skill(fake_home / ".claude" / "skills")
    settings = Settings(data_home=tmp_path / "data")

    results = deploy(settings, src, ["claude-code", "codex"])
    by_key = {r["key"]: r for r in results}
    # 来源即 claude-code → 跳过；codex 正常复制
    assert by_key["claude-code"]["ok"] and by_key["claude-code"].get("skipped")
    assert by_key["codex"]["ok"] and not by_key["codex"].get("skipped")
    assert (fake_home / ".codex" / "skills" / "demo-skill" / "SKILL.md").is_file()

    st = {t["key"]: t for t in deployment_status(src)}
    assert st["claude-code"]["installed"] and st["codex"]["installed"]
    assert not st["gemini-cli"]["installed"]
    assert st["claude-code"]["is_source"] and not st["codex"]["is_source"]


def test_deploy_refuses_existing_without_overwrite(fake_home, tmp_path):
    src = make_skill(tmp_path / "src")
    settings = Settings(data_home=tmp_path / "data")

    assert all(r["ok"] for r in deploy(settings, src, ["gemini-cli"]))
    (fake_home / ".gemini" / "skills" / "demo-skill" / "SKILL.md").write_text(
        "---\nname: old\n---\nold", encoding="utf-8"
    )
    results = deploy(settings, src, ["gemini-cli"])
    assert not results[0]["ok"] and "覆盖" in results[0]["error"]

    # 覆盖安装：先备份再替换
    results = deploy(settings, src, ["gemini-cli"], overwrite=True)
    assert results[0]["ok"] and results[0]["backup_dir"]
    assert "demo" in (fake_home / ".gemini" / "skills" / "demo-skill" / "SKILL.md").read_text(encoding="utf-8")
    backups = list((tmp_path / "data" / "skill-share" / "backups" / "demo-skill").iterdir())
    assert len(backups) == 1


def test_undeploy_backs_up_and_removes(fake_home, tmp_path):
    src = make_skill(tmp_path / "src")
    settings = Settings(data_home=tmp_path / "data")
    deploy(settings, src, ["pi"])

    result = undeploy(settings, src, "pi")
    assert result["ok"] and result["backup_dir"]
    assert not (fake_home / ".pi" / "agent" / "skills" / "demo-skill").exists()

    with pytest.raises(ShareError):
        undeploy(settings, src, "pi")  # 已卸载再卸载 → 404 语义


def test_deploy_rejects_generic_container(tmp_path):
    settings = Settings(data_home=tmp_path / "data")
    container = tmp_path / "skills"
    container.mkdir()
    (container / "SKILL.md").write_text("x", encoding="utf-8")
    with pytest.raises(ShareError):
        deploy(settings, container, ["claude-code"])
