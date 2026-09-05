"""M2 数据层测试：文件存储引擎 + SQLite 索引 + 服务层写穿。"""
from __future__ import annotations

import pytest

from app.core.config import Settings
from app.domain import AssetKind, AssetStatus
from app.stores.service import AssetService, StoreError


@pytest.fixture()
def service(tmp_path) -> AssetService:
    s = AssetService(Settings(data_home=tmp_path))
    yield s
    s.close()


def test_create_and_roundtrip(service: AssetService):
    asset = service.create(
        kind=AssetKind.MEMORY,
        name="用户偏好：简洁回复",
        body="用户偏好中文、直接给出结论。",
        tags=["偏好", "沟通"],
        metadata={"source": "onboarding"},
    )
    assert asset.id
    assert asset.rel_path.startswith("memory/")

    # 文件确实存在且可被解析回同一资产
    got = service.get(AssetKind.MEMORY, asset.id)
    assert got is not None
    assert got.name == asset.name
    assert got.tags == ["偏好", "沟通"]
    assert got.status is AssetStatus.ENABLED
    assert got.body == "用户偏好中文、直接给出结论。"
    assert service.count() == 1


def test_duplicate_name_rejected(service: AssetService):
    service.create(kind=AssetKind.RULE, name="禁止裸奔")
    with pytest.raises(StoreError):
        service.create(kind=AssetKind.RULE, name="禁止裸奔")


def test_update_rename_moves_file(service: AssetService):
    a = service.create(kind=AssetKind.TOOL, name="old-name", body="v1", metadata={"runner": "shell"})
    old_path = a.rel_path
    b = service.update(AssetKind.TOOL, a.id, {"name": "new-name", "body": "v2"})
    assert b.rel_path != old_path
    assert b.version == 2
    # 旧文件不存在、新文件存在
    assert not service.fs.path_of(old_path).exists()
    assert service.fs.path_of(b.rel_path).exists()
    assert service.get(AssetKind.TOOL, a.id).body == "v2"


def test_status_toggle(service: AssetService):
    a = service.create(kind=AssetKind.RULE, name="每周复盘")
    service.set_status(AssetKind.RULE, a.id, AssetStatus.DISABLED)
    assert service.get(AssetKind.RULE, a.id).status is AssetStatus.DISABLED


def test_search_like(service: AssetService):
    service.create(kind=AssetKind.MEMORY, name="数据库密码", body="本地 sqlite 无需密码")
    service.create(kind=AssetKind.RULE, name="提交规范", body="每次提交写中文描述")
    rows = service.search("密码")
    assert len(rows) == 1 and rows[0]["kind"] == "memory"
    rows = service.search("提交")
    assert len(rows) == 1 and rows[0]["name"] == "提交规范"


def test_skill_directory_form(service: AssetService):
    a = service.create(kind=AssetKind.SKILL, name="pdf-处理", body="处理 PDF 的 skill")
    files = service.fs.read_skill_files(a.rel_path)
    assert "SKILL.md" in files
    got = service.get(AssetKind.SKILL, a.id)
    assert got.name == "pdf-处理"
    # 改名应移动整个目录
    b = service.update(AssetKind.SKILL, a.id, {"name": "pdf-tool"})
    assert b.rel_path.startswith("skills/pdf-tool/")


def test_delete(service: AssetService):
    a = service.create(kind=AssetKind.SKILL, name="temp-skill")
    assert service.delete(AssetKind.SKILL, a.id) is True
    assert service.get(AssetKind.SKILL, a.id) is None
    assert not service.fs.path_of("skills/temp-skill").exists()
    assert service.delete(AssetKind.SKILL, a.id) is False


def test_reconcile_rebuilds_index(tmp_path):
    # 直接用 fs 写入文件（模拟外部编辑器直接改文件系统）
    s = AssetService(Settings(data_home=tmp_path))
    s.create(kind=AssetKind.TOOL, name="alpha")
    s.create(kind=AssetKind.TOOL, name="beta")
    s.close()

    s2 = AssetService(Settings(data_home=tmp_path))
    # 删除一个文件来模拟漂移，再重建
    (tmp_path / "assets/tools/alpha.md").unlink()
    stat = s2.reconcile()
    assert stat["indexed"] == 1
    rows = s2.list(kind=AssetKind.TOOL)
    assert len(rows) == 1 and rows[0]["name"] == "beta"
    s2.close()


def test_stats(service: AssetService):
    for i in range(3):
        service.create(kind=AssetKind.MEMORY, name=f"记忆{i}", status=AssetStatus.ENABLED)
    service.create(kind=AssetKind.RULE, name="规范", status=AssetStatus.ARCHIVED)
    stats = service.stats()
    assert stats["total"] == 4
    assert stats["by_kind"]["memory"]["enabled"] == 3
    assert stats["by_kind"]["rule"]["archived"] == 1
