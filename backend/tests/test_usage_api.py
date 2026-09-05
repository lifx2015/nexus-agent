"""流量统计 API 测试：范围解析、补零趋势、同步闭环、明细分页。"""
from __future__ import annotations

import json
import time
from datetime import datetime

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import app.deps as deps
import app.usage.sync as sync_mod
from app.api.v1.routes import usage as usage_routes
from app.core.config import Settings
from app.usage.adapters.claude_code import ClaudeCodeAdapter
from app.usage.store import UsageRecord


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(deps, "_settings", Settings(data_home=tmp_path))
    deps._usage_stores.clear()
    monkeypatch.setattr(sync_mod.usage_sync_manager, "_job", None)
    app_ = FastAPI()
    app_.include_router(usage_routes.router)
    with TestClient(app_) as c:
        yield c
    deps._usage_stores.clear()


def _seed(store, *recs):
    for r in recs:
        store.add_record(r)


def _rec(rid, agent="claude-code", model="m1", ts=None,
         input_tokens=1, output_tokens=1, **kw):
    return UsageRecord(
        request_id=rid, agent=agent, model=model,
        ts=ts or int(time.time()),
        input_tokens=input_tokens, output_tokens=output_tokens, **kw,
    )


def test_summary_and_facets(client):
    store = deps.get_usage_store()
    _seed(store, _rec("a", input_tokens=100, output_tokens=50, cache_read_tokens=30),
          _rec("b", agent="codex", model="gpt-5", input_tokens=10, output_tokens=0))
    s = client.get("/api/v1/usage/summary", params={"days": 0}).json()
    assert s["requests"] == 2
    assert s["real_total_tokens"] == 190
    facets = client.get("/api/v1/usage/facets").json()
    assert set(facets["agents"]) == {"claude-code", "codex"}


def test_trends_zero_fill_hourly(client):
    store = deps.get_usage_store()
    now = int(time.time())
    # 锚点取「最近的半点」：距离小时边界至少 30 分钟，桶数与分钟相位无关
    anchor = (now - 1800) // 3600 * 3600 + 1800
    _seed(store, _rec("a", ts=anchor), _rec("b", ts=anchor - 3 * 3600))
    r = client.get("/api/v1/usage/trends", params={"days": 1}).json()
    assert r["granularity"] == "hour"
    buckets = r["buckets"]
    assert len(buckets) >= 4
    assert sum(b["requests"] for b in buckets) == 2
    assert any(b["requests"] == 0 for b in buckets)  # 空洞被补零


def test_stats_dim(client):
    store = deps.get_usage_store()
    _seed(store, _rec("a", input_tokens=100), _rec("b", agent="codex", input_tokens=1))
    rows = client.get("/api/v1/usage/stats", params={"dim": "agent"}).json()["rows"]
    assert rows[0]["key"] == "claude-code"
    # pattern 校验先行：非法维度 422（FastAPI Query 声明式拒绝）
    assert client.get("/api/v1/usage/stats", params={"dim": "bad"}).status_code == 422


def test_records_pagination(client):
    store = deps.get_usage_store()
    base = int(time.time())
    _seed(store, *[_rec(f"r{i}", ts=base - i * 60) for i in range(5)])
    page = client.get("/api/v1/usage/records", params={"limit": 2}).json()
    assert page["total"] == 5 and len(page["items"]) == 2
    assert page["items"][0]["ts"] >= page["items"][1]["ts"]


def test_sync_flow_end_to_end(client, tmp_path, monkeypatch):
    root = tmp_path / "claude-projects" / "p"
    root.mkdir(parents=True)
    line = {
        "type": "assistant", "sessionId": "s1", "timestamp": "2026-01-01T00:00:00Z",
        "message": {"id": "m1", "model": "claude-3-5-sonnet", "usage": {
            "input_tokens": 10, "output_tokens": 5,
            "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0,
        }},
    }
    (root / "s1.jsonl").write_text(json.dumps(line) + "\n", encoding="utf-8")

    ad = ClaudeCodeAdapter()
    monkeypatch.setattr(ad, "roots", lambda: [str(root)])
    monkeypatch.setattr(sync_mod, "ADAPTERS", [ad])

    job = client.post("/api/v1/usage/sync").json()["job"]
    assert job["state"] == "running"

    for _ in range(100):  # 轮询直至完成（最多 5s）
        status = client.get("/api/v1/usage/sync/status").json()
        if status.get("done"):
            break
        time.sleep(0.05)
    assert status["done"] and status["state"] == "done"
    assert status["sources"]["claude"]["imported"] == 1

    s = client.get("/api/v1/usage/summary", params={"days": 0}).json()
    assert s["requests"] == 1 and s["input_tokens"] == 10


def test_adapters_endpoint(client):
    store = deps.get_usage_store()
    _seed(store, _rec("a", agent="claude-code"))
    d = client.get("/api/v1/usage/adapters").json()
    assert {a["source"] for a in d["adapters"]} == {
        "claude", "codex", "gemini", "opencode", "zcode", "workbuddy",
    }
    claude = next(a for a in d["adapters"] if a["source"] == "claude")
    assert claude["records"] == 1          # group_stats 的 key 列映射到此
    assert isinstance(claude["detected"], bool)
    assert claude["roots"] == [] or claude["detected"]  # 无 roots 必未检测


def test_reset(client):
    store = deps.get_usage_store()
    _seed(store, _rec("a"))
    assert client.post("/api/v1/usage/reset").json()["deleted"] == 1
    assert client.get("/api/v1/usage/summary", params={"days": 0}).json()["requests"] == 0


def test_resolve_range_helpers():
    start, end = usage_routes._resolve_range(7)
    assert usage_routes._resolve_range(0) == (None, None)
    # days=1：起始为今天0点
    from datetime import datetime as _dt
    start1, _ = usage_routes._resolve_range(1)
    start1_dt = _dt.fromtimestamp(start1)
    today = _dt.now()
    assert start1_dt.year == today.year and start1_dt.month == today.month and start1_dt.day == today.day
    # days=-1：过去24小时
    start_m1, end_m1 = usage_routes._resolve_range(-1)
    assert end_m1 - start_m1 == 86400
    assert usage_routes._granularity(1) == "hour"
    assert usage_routes._granularity(-1) == "hour"
    assert usage_routes._granularity(30) == "day"
    rows = [
        {"bucket": datetime.now().strftime("%Y-%m-%d %H:00"), "requests": 3,
         "tokens": 1, "input_tokens": 1, "output_tokens": 0,
         "cache_read_tokens": 0, "cache_creation_tokens": 0},
    ]
    filled = usage_routes._fill_trends(rows, None, None, "hour")
    assert filled == rows  # 单点无空洞
