"""流量统计 API：会话用量同步与聚合查询。

口径说明（与 cc-switch Dashboard 对齐）：
- input_tokens 恒为 fresh 输入（不含缓存），cache_read/cache_creation 单列；
- real_total_tokens = 输入 + 输出 + 缓存建 + 缓存读（模型真实处理量）；
- cache_hit_rate = cache_read / (输入 + 缓存建 + 缓存读)，0~1。
"""
from __future__ import annotations

import time
from datetime import datetime, timedelta

from fastapi import APIRouter, HTTPException, Query

from app.deps import get_usage_store
from app.scanners.agents import SPECS
from app.usage.adapters import ADAPTERS
from app.usage.sync import usage_sync_manager

router = APIRouter(prefix="/api/v1", tags=["usage"])


# ---- 时间范围与补零 ----------------------------------------------------
def _resolve_range(days: int) -> tuple[int | None, int | None]:
    """days=-1 表示过去24小时；days=0 表示全部时间；days>=1 表示从N天前0点（含当天）到现在。"""
    if days == -1:
        end = int(time.time())
        return end - 86400, end
    if days <= 0:
        return None, None
    end = int(time.time())
    start_dt = (datetime.now().replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=days - 1))
    return int(start_dt.timestamp()), end


def _granularity(days: int) -> str:
    """<=48h 按小时，其余按天（同 cc-switch 滑动窗口规则）。"""
    return "hour" if (0 < days <= 2 or days == -1) else "day"


_FMT = {"hour": "%Y-%m-%d %H:00", "day": "%Y-%m-%d"}


def _floor(dt: datetime, granularity: str) -> datetime:
    if granularity == "hour":
        return dt.replace(minute=0, second=0, microsecond=0)
    return dt.replace(hour=0, minute=0, second=0, microsecond=0)


def _zero_bucket(label: str) -> dict:
    return {
        "bucket": label, "requests": 0, "tokens": 0,
        "input_tokens": 0, "output_tokens": 0,
        "cache_read_tokens": 0, "cache_creation_tokens": 0,
    }


def _fill_trends(
    rows: list[dict], start_ts: int | None, end_ts: int | None, granularity: str,
) -> list[dict]:
    """缺口补零：在 [请求范围 ∩ 数据范围] 内逐桶输出，图表不跳日。"""
    if not rows:
        return []
    fmt = _FMT[granularity]
    step = timedelta(hours=1) if granularity == "hour" else timedelta(days=1)
    by = {r["bucket"]: r for r in rows}
    lo = datetime.strptime(min(by), fmt)
    hi = datetime.strptime(max(by), fmt)
    if start_ts is not None:
        lo = max(lo, _floor(datetime.fromtimestamp(start_ts), granularity))
    if end_ts is not None:
        hi = min(hi, _floor(datetime.fromtimestamp(end_ts), granularity))
    out: list[dict] = []
    cur = lo
    while cur <= hi:
        label = cur.strftime(fmt)
        out.append(by.get(label) or _zero_bucket(label))
        cur += step
    return out


# ---- 适配器状态 --------------------------------------------------------
@router.get("/usage/adapters", summary="支持的会话来源及检测状态")
def usage_adapters():
    store = get_usage_store()
    counts = {r["key"]: r["requests"] for r in store.group_stats("agent")}
    covered = {a.agent for a in ADAPTERS}
    adapters = [
        {
            "source": a.source,
            "agent": a.agent,
            "name": a.name,
            "detected": a.detect(),
            "roots": [str(p) for p in a.detected_roots()],
            "records": counts.get(a.agent, 0),
        }
        for a in ADAPTERS
    ]
    unsupported = [
        {"key": s.key, "name": s.name} for s in SPECS if s.key not in covered
    ]
    return {"adapters": adapters, "unsupported": unsupported}


# ---- 同步 --------------------------------------------------------------
@router.post("/usage/sync", summary="触发一轮增量同步（后台执行）")
def usage_sync():
    return {"job": usage_sync_manager.start()}


@router.get("/usage/sync/status", summary="当前同步任务状态")
def usage_sync_status():
    job = usage_sync_manager.status()
    if job is None:
        return {"state": "idle", "done": True, "sources": {}, "total": {}}
    return job


@router.post("/usage/reset", summary="清空用量明细与游标（下次同步全量重建）")
def usage_reset():
    return {"deleted": get_usage_store().reset()}


# ---- 聚合查询 ----------------------------------------------------------
@router.get("/usage/summary", summary="用量汇总")
def usage_summary(
    days: int = Query(30, ge=-1, le=3650),
    agent: str | None = None,
    model: str | None = None,
    start_ts: int | None = Query(None, description="显式起始 unix 秒，优先于 days"),
    end_ts: int | None = Query(None, description="显式结束 unix 秒，优先于 days"),
):
    if start_ts is not None or end_ts is not None:
        now = int(time.time())
        start_ts = start_ts if start_ts is not None else 0
        end_ts = end_ts if end_ts is not None else now
    else:
        start_ts, end_ts = _resolve_range(days)
    return get_usage_store().summary(start_ts=start_ts, end_ts=end_ts, agent=agent, model=model)


@router.get("/usage/trends", summary="用量趋势（缺口补零）")
def usage_trends(
    days: int = Query(30, ge=-1, le=3650),
    agent: str | None = None,
    model: str | None = None,
):
    granularity = _granularity(days)
    start_ts, end_ts = _resolve_range(days)
    rows = get_usage_store().trends(
        start_ts=start_ts, end_ts=end_ts, agent=agent, model=model,
        granularity=granularity,
    )
    return {
        "granularity": granularity,
        "buckets": _fill_trends(rows, start_ts, end_ts, granularity),
    }


@router.get("/usage/stats", summary="按维度分组统计（分页）")
def usage_stats(
    dim: str = Query("agent", pattern="^(agent|model)$"),
    days: int = Query(30, ge=-1, le=3650),
    limit: int = Query(10, le=100),
    offset: int = Query(0, ge=0),
):
    start_ts, end_ts = _resolve_range(days)
    store = get_usage_store()
    try:
        return {
            "rows": store.group_stats(dim, start_ts=start_ts, end_ts=end_ts, limit=limit, offset=offset),
            "total": store.count_group_stats(dim, start_ts=start_ts, end_ts=end_ts),
        }
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.get("/usage/records", summary="用量明细（分页，倒序）")
def usage_records(
    days: int = Query(0, ge=-1, le=3650),
    agent: str | None = None,
    model: str | None = None,
    limit: int = Query(50, le=500),
    offset: int = 0,
):
    start_ts, end_ts = _resolve_range(days)
    items, total = get_usage_store().records(
        start_ts=start_ts, end_ts=end_ts, agent=agent, model=model,
        limit=limit, offset=offset,
    )
    return {"items": items, "total": total}


@router.get("/usage/facets", summary="筛选用 agent/model 取值")
def usage_facets():
    store = get_usage_store()
    return {"agents": store.all_agents(), "models": store.all_models()}
