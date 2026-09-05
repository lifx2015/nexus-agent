"""ProjectStore 测试：upsert、排除/恢复、校验、会话映射与标注。"""
from __future__ import annotations

from pathlib import Path

import pytest

from app.core.config import Settings
from app.projects.models import ProjectRef
from app.projects.store import ProjectStore


@pytest.fixture()
def store(tmp_path):
    s = ProjectStore(Settings(data_home=tmp_path))
    yield s
    s.close()


def test_sync_refs_add_and_merge(store):
    s1 = store.sync_refs([ProjectRef(path="E:/demo", agent="claude-code", session_id="a1", ts=100)])
    assert s1["added"] == 1 and s1["sessions"] == 1
    row = store.get_by_path("E:/demo")
    assert row["agents"] == ["claude-code"] and row["sessions"] == 1
    assert row["last_activity_ts"] == 100

    # 同一项目（分隔符/大小写差异归一）再来一家 agent → 并集 + ts 取 max
    s2 = store.sync_refs([ProjectRef(path="E:\\Demo", agent="codex", session_id="b1", ts=200)])
    assert s2["added"] == 0
    row = store.get_by_path("E:/demo")
    assert set(row["agents"]) == {"claude-code", "codex"}
    assert row["sessions"] == 2
    assert row["last_activity_ts"] == 200


def test_multi_agent_same_batch_merge(store):
    # 同一批线索里，同一路径被多个 Agent 命中：agents 必须合并而非只认第一条
    store.sync_refs([
        ProjectRef(path="E:/demo", agent="codex", session_id="c1"),
        ProjectRef(path="E:/demo", agent="opencode", session_id="o1"),
        ProjectRef(path="E:/demo", agent="opencode", session_id="o2"),
    ])
    row = store.get_by_path("E:/demo")
    assert set(row["agents"]) == {"codex", "opencode"}
    assert row["sessions"] == 3


def test_manual_source_not_overwritten(store):
    store.sync_refs([ProjectRef(path="E:/demo", agent="", source="manual")])
    store.sync_refs([ProjectRef(path="E:/demo", agent="claude-code", source="log")])
    assert store.get_by_path("E:/demo")["source"] == "manual"


def test_excluded_not_resurrected(store):
    store.sync_refs([ProjectRef(path="E:/demo", agent="claude-code", session_id="a1")])
    row = store.get_by_path("E:/demo")
    store.exclude(row["id"])
    store.sync_refs([ProjectRef(path="E:/demo", agent="codex", session_id="b2")])
    row = store.get_by_path("E:/demo")
    assert row["status"] == "excluded"
    assert row["agents"] == ["claude-code"]  # 排除期间不更新
    store.restore(row["id"])
    assert store.get(row["id"])["status"] == "active"


def test_validate_all(store, tmp_path):
    live = tmp_path / "live"
    live.mkdir()
    store.sync_refs([
        ProjectRef(path=str(live), agent="a"),
        ProjectRef(path=str(tmp_path / "gone"), agent="a"),
    ])
    res = store.validate_all()
    assert res["active"] == 1 and res["missing"] == 1


def test_list_filters_and_stats(store):
    store.sync_refs([
        ProjectRef(path="E:/a", agent="claude-code", ts=300),
        ProjectRef(path="E:/b", agent="codex", ts=100),
        ProjectRef(path="E:/c", agent="claude-code"),
    ])
    assert len(store.list(agent="claude-code")) == 2
    assert len(store.list(q="a")) >= 1
    # 排除项缺省不出现
    row = store.get_by_path("E:/b")
    store.exclude(row["id"])
    assert len(store.list()) == 2
    assert len(store.list(status="excluded")) == 1

    stats = store.stats()
    assert stats["total"] == 2 and stats["excluded"] == 1
    assert stats["by_agent"]["claude-code"] == 2


def test_session_mapping(store):
    store.sync_refs([ProjectRef(path="E:/demo", agent="claude-code", session_id="a1")])
    mapping = store.session_mapping()
    assert len(mapping) == 1
    agent, sid, path = mapping[0]
    assert (agent, sid) == ("claude-code", "a1")
    assert path == str(Path("E:/demo"))


def test_patch_and_add_manual(store, tmp_path):
    d = tmp_path / "proj"
    d.mkdir()
    rec = store.add_manual(str(d))
    assert rec["source"] == "manual" and rec["agents"] == []  # 归属留待扫描补全

    with pytest.raises(FileNotFoundError):
        store.add_manual(str(tmp_path / "nope"))

    patched = store.patch(rec["id"], starred=True, note="重点", tags=["work"])
    assert patched["starred"] == 1 and patched["note"] == "重点"
    assert patched["tags"] == ["work"]


def test_path_key_normalization():
    from app.projects.store import path_key

    assert path_key("E:/A/B/") == path_key("e:\\a\\b")
    assert path_key("relative/x") == path_key("relative\\x")
