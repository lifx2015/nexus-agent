"""流量统计数据层测试：幂等写入、游标、聚合口径。"""
from __future__ import annotations

import time

import pytest

from app.core.config import Settings
from app.usage.store import UsageRecord, UsageStore


@pytest.fixture()
def store(tmp_path):
    s = UsageStore(Settings(data_home=tmp_path))
    yield s
    s.close()


def _rec(rid="r1", agent="claude-code", model="m1",
         input_tokens=1, output_tokens=1, **kw) -> UsageRecord:
    # 默认给非零 token，避免误被 has_billable 闸门过滤；需测零用量时显式传 input_tokens=0
    return UsageRecord(
        request_id=rid, agent=agent, model=model,
        ts=kw.pop("ts", int(time.time())),
        input_tokens=input_tokens, output_tokens=output_tokens, **kw,
    )


def test_add_and_idempotent(store: UsageStore):
    rec = _rec(input_tokens=100, output_tokens=50)
    assert store.add_record(rec) is True
    assert store.add_record(rec) is False          # UNIQUE 去重
    summary = store.summary()
    assert summary["requests"] == 1
    assert summary["input_tokens"] == 100
    assert summary["real_total_tokens"] == 150


def test_zero_usage_rejected(store: UsageStore):
    assert store.add_record(_rec(input_tokens=0, output_tokens=0)) is False
    assert store.summary()["requests"] == 0


def test_cache_hit_rate(store: UsageStore):
    # fresh 输入 100，缓存读 300，缓存建 100 → 命中率 300/500=0.6
    store.add_record(_rec(
        input_tokens=100, output_tokens=10,
        cache_read_tokens=300, cache_creation_tokens=100,
    ))
    s = store.summary()
    assert s["cache_hit_rate"] == pytest.approx(0.6)
    assert s["real_total_tokens"] == 510  # 100+10+300+100


def test_filters_by_agent_and_time(store: UsageStore):
    now = int(time.time())
    store.add_record(_rec("a", agent="claude-code", ts=now))
    store.add_record(_rec("b", agent="codex", ts=now - 10 * 86400, input_tokens=7))
    assert store.summary()["requests"] == 2
    assert store.summary(agent="codex")["input_tokens"] == 7
    assert store.summary(start_ts=now - 3600)["requests"] == 1


def test_group_stats_and_records(store: UsageStore):
    store.add_record(_rec("a", input_tokens=100, output_tokens=10))
    store.add_record(_rec("b", agent="codex", model="gpt-5", input_tokens=50))
    rows = store.group_stats("agent")
    assert rows[0]["key"] == "claude-code"          # 按真实总量降序
    assert rows[0]["real_total_tokens"] == 110
    by_model = store.group_stats("model")
    assert {r["key"] for r in by_model} == {"m1", "gpt-5"}
    items, total = store.records(limit=1)
    assert total == 2 and len(items) == 1


def test_trends_bucket_labels(store: UsageStore):
    now = int(time.time())
    store.add_record(_rec("a", ts=now - 100))
    store.add_record(_rec("b", ts=now - 200))
    rows = store.trends(granularity="hour")
    assert sum(r["requests"] for r in rows) == 2
    days = store.trends(granularity="day")
    assert sum(r["requests"] for r in days) == 2


def test_state_roundtrip_and_reset(store: UsageStore):
    store.set_state("claude", "f1", mtime_ns=1, size=2, offset=3, seq=4)
    row = store.get_state("claude", "f1")
    assert (row["mtime_ns"], row["size"], row["offset"], row["seq"]) == (1, 2, 3, 4)
    store.set_state("claude", "f1", mtime_ns=9, size=8, offset=7, seq=6)
    assert store.get_state("claude", "f1")["mtime_ns"] == 9
    store.add_record(_rec())
    assert store.reset() == 1
    assert store.get_state("claude", "f1") is None
