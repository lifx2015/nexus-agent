"""华为码道 Space（~/.codeartswork kernel JSONL 会话库）用量适配器测试。"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.core.config import Settings
from app.usage.adapters.codearts import CodeArtsAdapter
from app.usage.store import UsageStore


@pytest.fixture()
def store(tmp_path):
    s = UsageStore(Settings(data_home=tmp_path))
    yield s
    s.close()


def _make_opencode_db(path: Path, sid: str, mid: str, *, model="GLM-4.6", inp=120, out=30):
    import sqlite3
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.executescript(
        "CREATE TABLE session(id TEXT PRIMARY KEY, directory TEXT, time_updated INTEGER);"
        "CREATE TABLE message(id TEXT, session_id TEXT, time_created INTEGER, "
        "time_updated INTEGER, data TEXT);"
    )
    conn.execute("INSERT INTO session VALUES(?, 'D:/work/demo', 1000)", (sid,))
    conn.execute("INSERT INTO message VALUES(?, ?, 1700000000, 2000, ?)", (
        mid, sid,
        json.dumps({
            "role": "assistant", "modelID": model,
            "time": {"created": 1700000000000, "completed": 1700000001000},
            "tokens": {"input": inp, "output": out, "reasoning": 5,
                       "cache": {"read": 800, "write": 40}},
        }),
    ))
    conn.commit()
    conn.close()


def _make_space_session(
    home: Path, proj: str, sid: str, *, model="glm-5.3-flash",
    extra_line: dict | None = None,
) -> Path:
    sdir = home / "kernel" / "sessions" / proj / sid
    sdir.mkdir(parents=True, exist_ok=True)
    (sdir / "meta.json").write_text(json.dumps({
        "session_id": sid,
        "created_at": "2026-09-07T09:41:54.887350500Z",
        "updated_at": "2026-09-07T11:00:00Z",
        "agent_name": "built_in_agent",
        "working_directory": "E:/workspace/demo",
        "current_model": {"provider_id": "inferhub-provider", "model_id": model},
    }), encoding="utf-8")
    rows = [
        {"id": "msg_u1", "role": "User", "timestamp": "2026-09-07T09:41:54.887350500Z",
         "model_ref": {"provider_id": "inferhub-provider", "model_id": model},
         "token_usage": None},
        {"id": "msg_a1", "role": "Assistant", "timestamp": "2026-09-07T09:42:03.485007200Z",
         "model_ref": {"provider_id": "inferhub-provider", "model_id": model},
         "token_usage": {"prompt_tokens": 6137, "completion_tokens": 105,
                          "total_tokens": 6242, "max_tokens": 196608,
                          "reasoning_tokens": 54, "cached_tokens": None}},
        {"id": "msg_t1", "role": "Tool", "timestamp": "2026-09-07T09:42:05.876032600Z",
         "token_usage": None},
        {"id": "msg_a2", "role": "Assistant", "timestamp": "2026-09-07T09:42:11.161470900Z",
         "model_ref": {"provider_id": "inferhub-provider", "model_id": model},
         "token_usage": {"prompt_tokens": 6371, "completion_tokens": 41,
                          "total_tokens": 6412, "max_tokens": 196608,
                          "reasoning_tokens": None, "cached_tokens": 6080}},
    ]
    if extra_line is not None:
        rows.append(extra_line)
    (sdir / "history.jsonl").write_text(
        "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows),
        encoding="utf-8",
    )
    return sdir


def test_codearts_space_jsonl_usage(store, tmp_path, monkeypatch):
    home = tmp_path / "codeartswork"
    _make_space_session(home, "proj-key", "sid-1")
    monkeypatch.setenv("CODEARTS_WORK_HOME", str(home))
    ad = CodeArtsAdapter()
    assert ad.detect()

    s = ad.collect(store)
    assert s.imported == 2  # 只有带 token_usage 的 Assistant 行入库
    rows, _ = store.records(limit=10)
    a1 = next(r for r in rows if r["request_id"] == "codearts_session:sid-1:msg_a1")
    assert a1["input_tokens"] == 6137 and a1["cache_read_tokens"] == 0
    assert a1["output_tokens"] == 105 and a1["reasoning_tokens"] == 54
    a2 = next(r for r in rows if r["request_id"] == "codearts_session:sid-1:msg_a2")
    # OpenAI 口径：prompt 含缓存 → 写入侧扣减为 fresh
    assert a2["input_tokens"] == 6371 - 6080 and a2["cache_read_tokens"] == 6080
    assert a2["model"] == "glm-5.3-flash"
    assert a2["session_id"] == "sid-1" and a2["project"] == str(Path("E:/workspace/demo"))
    assert a2["source_path"].endswith("history.jsonl")
    # 纳秒级时间戳解析成功（>0 的 unix 秒）
    assert a1["ts"] > 1_700_000_000

    # 第二轮：文件未变 → 跳过
    s2 = ad.collect(store)
    assert s2.imported == 0 and s2.skipped == 1

    # 追加一条消息 → 仅新增那条入库
    sdir = _make_space_session(
        home, "proj-key", "sid-1",
        extra_line={
            "id": "msg_a3", "role": "Assistant",
            "timestamp": "2026-09-07T09:43:00Z",
            "model_ref": {"provider_id": "inferhub-provider", "model_id": "glm-5.3-flash"},
            "token_usage": {"prompt_tokens": 10, "completion_tokens": 2,
                             "total_tokens": 12, "max_tokens": 196608,
                             "reasoning_tokens": 0, "cached_tokens": 5},
        },
    )
    assert sdir.is_dir()
    s3 = ad.collect(store)
    assert s3.imported == 1
    rows, _ = store.records(limit=10)
    assert sum(1 for r in rows if r["request_id"].startswith("codearts_session:sid-1:")) == 3
    assert store.summary()["requests"] == 3


def test_codearts_space_no_meta_falls_back(store, tmp_path, monkeypatch):
    # 无 meta.json：会话 ID 取目录名、项目留空、模型取 unknown
    home = tmp_path / "codeartswork"
    sdir = home / "kernel" / "sessions" / "sid-orphan"
    sdir.mkdir(parents=True)
    (sdir / "history.jsonl").write_text(json.dumps({
        "id": "msg_x1", "role": "Assistant", "timestamp": "2026-09-07T10:00:00Z",
        "token_usage": {"prompt_tokens": 100, "completion_tokens": 10,
                          "total_tokens": 110, "max_tokens": 1024,
                          "reasoning_tokens": 0, "cached_tokens": None},
    }) + "\n", encoding="utf-8")
    monkeypatch.setenv("CODEARTS_WORK_HOME", str(home))
    s = CodeArtsAdapter().collect(store)
    assert s.imported == 1
    r = store.records(limit=1)[0][0]
    assert r["request_id"] == "codearts_session:sid-orphan:msg_x1"
    assert r["model"] == "unknown" and r["project"] == ""


def test_codearts_both_sources_coexist(store, tmp_path, monkeypatch):
    # Space JSONL 与 opencode.db 两源并存，互不串号
    doer = tmp_path / ".codeartsdoer"
    _make_opencode_db(doer / "codearts-data" / "opencode.db", "db-sid", "db-mid")
    home = tmp_path / "codeartswork"
    _make_space_session(home, "proj", "space-sid")
    monkeypatch.setenv("CODEARTS_DOER_HOME", str(doer))
    monkeypatch.setenv("CODEARTS_WORK_HOME", str(home))
    s = CodeArtsAdapter().collect(store)
    assert s.imported == 3  # db 1 条 + space 2 条
    rows, _ = store.records(limit=10)
    assert any(r["request_id"].startswith("codearts_session:db-sid:") for r in rows)
    assert any(r["request_id"].startswith("codearts_session:space-sid:") for r in rows)
