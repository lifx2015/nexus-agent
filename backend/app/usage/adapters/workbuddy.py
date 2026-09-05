"""WorkBuddy 会话用量适配器（轮次聚合口径）。

来源: ~/.workbuddy/traces/<pid>/trace_*.json
每个文件是一次「Agent workflow」轮次的 OTel 风格 trace，顶层 trace.modelInfo
携带本轮聚合用量：
  {models: [...], totalInputTokens(含缓存), totalOutputTokens,
   totalCachedTokens, callCount}
无逐调用明细（一轮可能含 N 次 LLM 调用），故按 **轮次** 落一条记录——
与 cc-switch 对 Grok Build 会话的聚合口径一致：token 总量真实，
请求数是轮次数（低估调用数），model 为该轮模型列表（混合轮以 + 连接）。

增量: 文件 mtime+size 门控，未变即整体跳过；traceId 唯一键保证重放幂等。
"""
from __future__ import annotations

import json
from pathlib import Path

from app.usage.adapters.base import MAX_FILES_PER_SYNC, BaseUsageAdapter, register
from app.usage.common import SyncStats, parse_ts
from app.usage.store import UsageRecord, UsageStore


@register
class WorkBuddyAdapter(BaseUsageAdapter):
    source = "workbuddy"
    agent = "workbuddy"
    name = "WorkBuddy"

    def roots(self) -> list[Path]:
        return ["$WORKBUDDY_HOME/traces", "~/.workbuddy/traces"]

    def collect(self, store: UsageStore) -> SyncStats:
        stats = SyncStats(self.source)
        files: list[Path] = []
        for root in self.detected_roots():
            files.extend(p for p in root.rglob("trace_*.json") if p.is_file())
            if len(files) > MAX_FILES_PER_SYNC:
                break
        stats.files_scanned = len(files)
        for path in sorted(files)[:MAX_FILES_PER_SYNC]:
            try:
                self._sync_file(store, path, stats)
            except (OSError, ValueError) as e:
                stats.errors.append(f"{path}: {e}")
        return stats

    def _sync_file(self, store: UsageStore, path: Path, stats: SyncStats) -> None:
        st = path.stat()
        key = str(path)
        cur = store.get_state(self.source, key)
        if cur and int(cur["mtime_ns"]) == st.st_mtime_ns and int(cur["size"]) == st.st_size:
            stats.skipped += 1
            return

        data = json.loads(path.read_text(encoding="utf-8"))
        trace = data.get("trace") if isinstance(data, dict) else None
        mi = trace.get("modelInfo") if isinstance(trace, dict) else None
        if isinstance(trace, dict) and isinstance(mi, dict):
            trace_id = trace.get("traceId") or path.stem
            models = [str(m) for m in (mi.get("models") or []) if m]
            input_all = int(mi.get("totalInputTokens") or 0)
            cached = int(mi.get("totalCachedTokens") or 0)
            rec = UsageRecord(
                request_id=f"workbuddy_session:{trace_id}",
                agent=self.agent,
                model="+".join(models) if models else "unknown",
                ts=parse_ts(trace.get("startedAt")) or int(st.st_mtime),
                input_tokens=max(0, input_all - cached),   # 含缓存 → 归一 fresh
                output_tokens=int(mi.get("totalOutputTokens") or 0),
                cache_read_tokens=cached,
                session_id=str(trace.get("sessionId") or ""),
                source_path=key,
            )
            if store.add_record(rec):
                stats.imported += 1
            else:
                stats.skipped += 1

        # 无 modelInfo 的轮次（0 token / 非 LLM workflow）也要落游标，避免每轮重读
        store.set_state(
            self.source, key,
            mtime_ns=st.st_mtime_ns, size=st.st_size, offset=0, seq=0,
        )
