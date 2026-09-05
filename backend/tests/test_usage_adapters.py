"""会话适配器解析测试：造本地会话文件，验证增量游标、语义归一、幂等。

所有用例把 adapter.roots() 重定向到 tmp_path，绝不触碰真实 ~/.claude 等目录。
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.core.config import Settings
from app.projects.paths import decode_project_dir
from app.usage.adapters.claude_code import ClaudeCodeAdapter
from app.usage.adapters.codex import CodexAdapter
from app.usage.adapters.gemini import GeminiAdapter
from app.usage.adapters.opencode import OpenCodeAdapter
from app.usage.adapters.workbuddy import WorkBuddyAdapter
from app.usage.adapters.zcode import ZCodeAdapter
from app.usage.store import UsageStore


@pytest.fixture()
def store(tmp_path):
    s = UsageStore(Settings(data_home=tmp_path))
    yield s
    s.close()


def _one(store, agent):
    rows, _ = store.records(limit=10)
    return rows[0]


# ---- Claude Code -------------------------------------------------------
def _claude_line(mid, ts, *, inp=100, out=50, cr=200, cc=10, stop=None, model="claude-3-5-sonnet"):
    msg = {
        "id": mid, "model": model,
        "usage": {
            "input_tokens": inp, "output_tokens": out,
            "cache_read_input_tokens": cr, "cache_creation_input_tokens": cc,
        },
    }
    if stop:
        msg["stop_reason"] = stop
    return json.dumps({"type": "assistant", "sessionId": "s1", "timestamp": ts, "message": msg}) + "\n"


def test_claude_parse_and_incremental(store, tmp_path, monkeypatch):
    root = tmp_path / "projects"
    (root / "proj").mkdir(parents=True)
    f = root / "proj" / "s1.jsonl"
    f.write_text(
        _claude_line("msg1", "2026-01-01T00:00:00Z")
        + _claude_line("msg2", "2026-01-01T00:01:00Z"),
        encoding="utf-8",
    )
    ad = ClaudeCodeAdapter()
    monkeypatch.setattr(ad, "roots", lambda: [str(root)])
    stats = ad.collect(store)
    assert stats.imported == 2
    assert stats.files_scanned == 1

    # 文件未变 → 全跳过
    stats2 = ad.collect(store)
    assert stats2.imported == 0 and stats2.skipped == 1

    # 追加一行 → 只导入新增（offset 游标续读）
    with f.open("a", encoding="utf-8") as fh:
        fh.write(_claude_line("msg3", "2026-01-01T00:02:00Z"))
    stats3 = ad.collect(store)
    assert stats3.imported == 1
    assert store.summary()["requests"] == 3


def test_claude_semantics_passthrough(store, tmp_path, monkeypatch):
    # Anthropic 语义：input 不含 cache，原样落库
    root = tmp_path / "projects"
    (root / "p").mkdir(parents=True)
    (root / "p" / "s.jsonl").write_text(
        _claude_line("m", "2026-01-01T00:00:00Z", inp=100, cr=5000), encoding="utf-8",
    )
    ad = ClaudeCodeAdapter()
    monkeypatch.setattr(ad, "roots", lambda: [str(root)])
    ad.collect(store)
    r = _one(store, ad)
    assert r["input_tokens"] == 100 and r["cache_read_tokens"] == 5000
    # 项目归属：来自会话文件所在目录名的解码
    assert r["project"] == str(Path(decode_project_dir("p")))


def test_claude_dedup_prefers_stop_reason(store, tmp_path, monkeypatch):
    # 同一 message.id 流式写多行快照：应只入库一条，且取带 stop_reason 的版本
    root = tmp_path / "projects"
    (root / "p").mkdir(parents=True)
    (root / "p" / "s.jsonl").write_text(
        _claude_line("dup", "2026-01-01T00:00:00Z", out=1)              # 半截快照
        + _claude_line("dup", "2026-01-01T00:00:01Z", out=999, stop="end_turn"),  # 完整
        encoding="utf-8",
    )
    ad = ClaudeCodeAdapter()
    monkeypatch.setattr(ad, "roots", lambda: [str(root)])
    ad.collect(store)
    assert store.summary()["requests"] == 1
    assert _one(store, ad)["output_tokens"] == 999


# ---- Codex -------------------------------------------------------------
def test_codex_normalizes_and_continues_seq(store, tmp_path, monkeypatch):
    root = tmp_path / "sessions"
    root.mkdir()
    f = root / "r1.jsonl"
    f.write_text(
        json.dumps({"type": "session_meta", "payload": {"session_id": "t1", "cwd": "F:/proj"}}) + "\n"
        + json.dumps({"type": "turn_context", "payload": {"model": "gpt-5"}}) + "\n"
        + json.dumps({
            "timestamp": "2026-01-01T00:00:00Z", "type": "event_msg",
            "payload": {"type": "token_count", "info": {"last_token_usage": {
                "input_tokens": 1000, "cached_input_tokens": 600,
                "output_tokens": 50, "reasoning_output_tokens": 20,
            }}},
        }) + "\n",
        encoding="utf-8",
    )
    ad = CodexAdapter()
    monkeypatch.setattr(ad, "roots", lambda: [str(root)])
    ad.collect(store)
    r = _one(store, ad)
    # OpenAI 语义归一：fresh = 1000 - 600 = 400，cache_read = 600
    assert r["input_tokens"] == 400 and r["cache_read_tokens"] == 600
    assert r["output_tokens"] == 50 and r["reasoning_tokens"] == 20
    assert r["model"] == "gpt-5"
    assert r["project"] == str(Path("F:/proj"))  # session_meta.cwd → 项目归属

    # 第二轮追加一个 token_count，seq 应续到 2、request_id 不冲突
    with f.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps({
            "timestamp": "2026-01-01T00:05:00Z", "type": "event_msg",
            "payload": {"type": "token_count", "info": {"last_token_usage": {
                "input_tokens": 200, "cached_input_tokens": 0, "output_tokens": 10,
            }}},
        }) + "\n")
    stats = ad.collect(store)
    assert stats.imported == 1
    assert store.summary()["requests"] == 2


# ---- Gemini ------------------------------------------------------------
def test_gemini_normalize_and_mtime_gate(store, tmp_path, monkeypatch):
    root = tmp_path / "tmp" / "projhash" / "chats"
    root.mkdir(parents=True)
    f = root / "session-a.json"
    f.write_text(json.dumps({
        "sessionId": "gs1",
        "messages": [
            {"type": "user", "id": "u1"},
            {"type": "gemini", "id": "g1", "model": "gemini-3-pro", "timestamp": "2026-01-01T00:00:00Z",
             "tokens": {"input": 1000, "output": 40, "cached": 300, "thoughts": 15}},
        ],
    }), encoding="utf-8")
    ad = GeminiAdapter()
    # roots 指向 chats 的上上级（tmp 目录）：适配器按 <root>/<项目hash>/chats/*.json 遍历
    monkeypatch.setattr(ad, "roots", lambda: [str(root.parent.parent)])
    ad.collect(store)
    r = _one(store, ad)
    assert r["input_tokens"] == 700 and r["cache_read_tokens"] == 300
    assert r["reasoning_tokens"] == 15

    stats = ad.collect(store)   # 未改动 → 跳过
    assert stats.imported == 0


# ---- OpenCode ----------------------------------------------------------
def test_opencode_reads_assistant_messages(store, tmp_path):
    import sqlite3
    db = tmp_path / "opencode.db"
    conn = sqlite3.connect(db)
    conn.executescript(
        "CREATE TABLE session(id TEXT PRIMARY KEY, time_updated INTEGER);"
        "CREATE TABLE message(id TEXT, session_id TEXT, time_created INTEGER, "
        "time_updated INTEGER, data TEXT);"
    )
    conn.execute("INSERT INTO session VALUES('os1', 1000)")
    data = json.dumps({
        "role": "assistant", "modelID": "kimi-k2",
        "time": {"created": 1700000000000, "completed": 1700000001000},
        "tokens": {"input": 120, "output": 30, "reasoning": 5, "cache": {"read": 800, "write": 40}},
    })
    conn.execute("INSERT INTO message VALUES('om1','os1',1700000000,2000,?)", (data,))
    # 未完成消息（无 completed）应被跳过
    conn.execute("INSERT INTO message VALUES('om2','os1',1700000002,2000,?)", (
        json.dumps({"role": "assistant", "tokens": {"input": 1, "output": 1}}),
    ))
    conn.commit()
    conn.close()

    ad = OpenCodeAdapter()
    monkeypatch_env = {"OPENCODE_DB": str(db)}
    import os
    for k, v in monkeypatch_env.items():
        os.environ[k] = v
    try:
        ad.collect(store)
    finally:
        os.environ.pop("OPENCODE_DB", None)

    assert store.summary()["requests"] == 1   # 未完成那条未计入
    r = _one(store, ad)
    assert r["model"] == "kimi-k2"
    assert r["input_tokens"] == 120 and r["cache_read_tokens"] == 800
    assert r["cache_creation_tokens"] == 40 and r["reasoning_tokens"] == 5


# ---- ZCode -------------------------------------------------------------
def _zcode_line(rid, *, attempt=1, inp=37277, out=51, rd=35968, wr=0, model="GLM-5.3-Flash", sess="sess_z1"):
    return json.dumps({
        "type": "model_io", "requestId": rid, "attempt": attempt,
        "sessionId": sess, "startedAt": "2026-09-01T10:00:00Z",
        "model": {"modelId": model},
        "response": {"modelId": model, "usage": {
            "inputTokens": inp, "outputTokens": out, "totalTokens": inp + out,
            "cacheReadTokens": rd, "cacheWriteTokens": wr,
        }},
    }) + "\n"


def test_zcode_normalizes_cache_inclusive_input(store, tmp_path, monkeypatch):
    root = tmp_path / "rollout"
    root.mkdir()
    f = root / "model-io-sess_z1.jsonl"
    f.write_text(
        _zcode_line("req1"),                          # 含 rd：37277-35968=1309 fresh
        encoding="utf-8",
    )
    ad = ZCodeAdapter()
    monkeypatch.setattr(ad, "roots", lambda: [str(root)])
    ad.collect(store)
    r = _one(store, ad)
    assert r["input_tokens"] == 1309 and r["cache_read_tokens"] == 35968
    assert r["output_tokens"] == 51 and r["model"] == "GLM-5.3-Flash"
    assert store.summary()["requests"] == 1


def test_zcode_error_line_skipped_and_incremental(store, tmp_path, monkeypatch):
    root = tmp_path / "rollout"
    root.mkdir()
    f = root / "model-io-sess_z2.jsonl"
    # 错误行（无 usage）应跳过；随后追加正常行应增量导入
    f.write_text(
        json.dumps({"type": "model_io", "requestId": "e1", "attempt": 1,
                    "sessionId": "sess_z2", "error": "boom",
                    "response": None}) + "\n",
        encoding="utf-8",
    )
    ad = ZCodeAdapter()
    monkeypatch.setattr(ad, "roots", lambda: [str(root)])
    s1 = ad.collect(store)
    assert s1.imported == 0 and s1.skipped == 0     # 错误行既不计导入也不报错
    with f.open("a", encoding="utf-8") as fh:
        fh.write(_zcode_line("ok1", sess="sess_z2", inp=100, out=5, rd=0, wr=0))
    s2 = ad.collect(store)
    assert s2.imported == 1
    assert store.summary()["requests"] == 1


def test_zcode_retry_attempt_distinct_id(store, tmp_path, monkeypatch):
    root = tmp_path / "rollout"
    root.mkdir()
    (root / "model-io-sess_z3.jsonl").write_text(
        _zcode_line("same", attempt=1, sess="s3")
        + _zcode_line("same", attempt=2, sess="s3"),
        encoding="utf-8",
    )
    ad = ZCodeAdapter()
    monkeypatch.setattr(ad, "roots", lambda: [str(root)])
    ad.collect(store)
    assert store.summary()["requests"] == 2   # attempt 2 单独成号，不被去重


# ---- WorkBuddy ---------------------------------------------------------
def test_workbuddy_turn_aggregation(store, tmp_path, monkeypatch):
    root = tmp_path / "traces" / "10592"
    root.mkdir(parents=True)
    trace = {
        "trace": {
            "traceId": "tr1", "sessionId": "ws1",
            "startedAt": "2026-09-03T11:22:11.111Z", "totalTokens": 34900,
            "modelInfo": {
                "models": ["qwen-3.8"], "totalInputTokens": 34263,
                "totalOutputTokens": 637, "totalCachedTokens": 3000, "callCount": 1,
            },
        },
        "spans": [],
    }
    (root / "trace_tr1.json").write_text(json.dumps(trace), encoding="utf-8")
    ad = WorkBuddyAdapter()
    monkeypatch.setattr(ad, "roots", lambda: [str(tmp_path / "traces")])
    ad.collect(store)
    r = _one(store, ad)
    assert r["input_tokens"] == 34263 - 3000 and r["cache_read_tokens"] == 3000
    assert r["output_tokens"] == 637 and r["model"] == "qwen-3.8"
    assert store.summary()["requests"] == 1   # 一轮一条（聚合口径）


def test_workbuddy_no_modelinfo_cursor_advances(store, tmp_path, monkeypatch):
    root = tmp_path / "traces" / "99"
    root.mkdir(parents=True)
    (root / "trace_empty.json").write_text(
        json.dumps({"trace": {"traceId": "e", "startedAt": "2026-09-03T00:00:00Z"}, "spans": []}),
        encoding="utf-8",
    )
    ad = WorkBuddyAdapter()
    monkeypatch.setattr(ad, "roots", lambda: [str(tmp_path / "traces")])
    ad.collect(store)
    assert store.summary()["requests"] == 0
    assert store.get_state("workbuddy", str(root / "trace_empty.json")) is not None
    # 第二轮：mtime 未变应整体跳过（游标已推进）
    s2 = ad.collect(store)
    assert s2.skipped == 1
