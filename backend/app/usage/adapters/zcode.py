"""ZCode CLI 会话用量适配器。

来源: ~/.zcode/cli/rollout/model-io-<session>.jsonl
ZCode 是 Codex 系工具：每行一条 `type=model_io` 记录（一次模型 API 调用，
含错误尝试），响应 usage 为 AI-SDK 归一 camelCase：
{inputTokens(含缓存), outputTokens, totalTokens, cacheReadTokens, cacheWriteTokens}
→ 与 Codex 同样在写入侧扣减归一为 fresh input。

去重键: (requestId, attempt) 实测唯一；attempt>1（重试）单独成号。
增量: 字节 offset 游标（同 Claude 适配器）。
"""
from __future__ import annotations

import json
from pathlib import Path

from app.usage.adapters.base import MAX_FILES_PER_SYNC, BaseUsageAdapter, register
from app.usage.common import SyncStats, parse_ts
from app.usage.store import UsageRecord, UsageStore


def _u(obj: dict, key: str) -> int:
    try:
        return int(obj.get(key) or 0)
    except (TypeError, ValueError):
        return 0


@register
class ZCodeAdapter(BaseUsageAdapter):
    source = "zcode"
    agent = "zcode"
    name = "ZCode"

    def roots(self) -> list[Path]:
        return ["$ZCODE_HOME/cli/rollout", "~/.zcode/cli/rollout"]

    def collect(self, store: UsageStore) -> SyncStats:
        stats = SyncStats(self.source)
        files: list[Path] = []
        for root in self.detected_roots():
            files.extend(p for p in root.rglob("*.jsonl") if p.is_file())
            if len(files) > MAX_FILES_PER_SYNC:
                break
        stats.files_scanned = len(files)
        for path in sorted(files)[:MAX_FILES_PER_SYNC]:
            try:
                self._sync_file(store, path, stats)
            except OSError as e:
                stats.errors.append(f"{path}: {e}")
        return stats

    def _sync_file(self, store: UsageStore, path: Path, stats: SyncStats) -> None:
        st = path.stat()
        key = str(path)
        cur = store.get_state(self.source, key)
        offset = int(cur["offset"]) if cur else 0
        if cur and int(cur["mtime_ns"]) == st.st_mtime_ns and int(cur["size"]) == st.st_size:
            stats.skipped += 1
            return
        if st.st_size < offset:
            offset = 0

        consumed = offset
        with path.open("rb") as f:
            f.seek(offset)
            while True:
                line = f.readline()
                if not line or not line.endswith(b"\n"):
                    break
                consumed += len(line)
                try:
                    value = json.loads(line)
                except ValueError:
                    continue
                if not isinstance(value, dict) or value.get("type") != "model_io":
                    continue
                response = value.get("response")
                usage = response.get("usage") if isinstance(response, dict) else None
                if not isinstance(usage, dict):
                    continue  # 错误行（无 usage）或格式外记录
                req_id = value.get("requestId") or f"{key}:{consumed}"
                attempt = _u(value, "attempt")
                model = (value.get("model") or {}).get("modelId") if isinstance(value.get("model"), dict) else None
                input_all = _u(usage, "inputTokens")
                cached = _u(usage, "cacheReadTokens")
                cache_write = _u(usage, "cacheWriteTokens")
                rec = UsageRecord(
                    request_id=(
                        f"zcode:{req_id}" if attempt <= 1 else f"zcode:{req_id}:a{attempt}"
                    ),
                    agent=self.agent,
                    model=str(model or (response.get("modelId")) or "unknown"),
                    ts=parse_ts(value.get("startedAt") or value.get("completedAt")) or int(st.st_mtime),
                    input_tokens=max(0, input_all - cached - cache_write),
                    output_tokens=_u(usage, "outputTokens"),
                    cache_read_tokens=cached,
                    cache_creation_tokens=cache_write,
                    session_id=str(value.get("sessionId") or ""),
                    source_path=key,
                )
                if store.add_record(rec):
                    stats.imported += 1
                else:
                    stats.skipped += 1

        store.set_state(
            self.source, key,
            mtime_ns=st.st_mtime_ns, size=st.st_size, offset=consumed, seq=0,
        )
