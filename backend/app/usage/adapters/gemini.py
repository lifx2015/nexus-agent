"""Gemini CLI 会话用量适配器。

来源: ~/.gemini/tmp/<项目hash>/chats/session-*.json
与 Claude/Codex 不同：每个文件是**单个 JSON 对象**（非 JSONL），
messages 数组中 type=="gemini" 的条目带 per-message `tokens`：
{input(含 cached), output, cached, thoughts}。
input 含 cache → 写入侧扣减归一。消息自带唯一 id，天然幂等，
文件 mtime 未变即整体跳过；变了就重扫（INSERT OR IGNORE 去重）。
"""
from __future__ import annotations

import json
from pathlib import Path

from app.usage.adapters.base import MAX_FILES_PER_SYNC, BaseUsageAdapter, register
from app.usage.common import SyncStats, parse_ts
from app.usage.store import UsageRecord, UsageStore


@register
class GeminiAdapter(BaseUsageAdapter):
    source = "gemini"
    agent = "gemini-cli"
    name = "Gemini CLI"

    def roots(self) -> list[Path]:
        return ["$GEMINI_CONFIG_DIR/tmp", "~/.gemini/tmp"]

    def collect(self, store: UsageStore) -> SyncStats:
        stats = SyncStats(self.source)
        files: list[Path] = []
        for root in self.detected_roots():
            for proj in root.iterdir():
                chats = proj / "chats"
                if chats.is_dir():
                    files.extend(p for p in chats.glob("session-*.json") if p.is_file())
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
        session_id = data.get("sessionId") or path.stem
        messages = data.get("messages")
        if not isinstance(messages, list):
            return

        for msg in messages:
            if not isinstance(msg, dict) or msg.get("type") != "gemini":
                continue
            mid = msg.get("id")
            tokens = msg.get("tokens")
            if not mid or not isinstance(tokens, dict):
                continue
            input_all = int(tokens.get("input") or 0)
            cached = int(tokens.get("cached") or 0)
            rec = UsageRecord(
                request_id=f"gemini_session:{session_id}:{mid}",
                agent=self.agent,
                model=str(msg.get("model") or "unknown"),
                ts=parse_ts(msg.get("timestamp")) or int(st.st_mtime),
                input_tokens=max(0, input_all - cached),
                output_tokens=int(tokens.get("output") or 0),
                cache_read_tokens=cached,
                reasoning_tokens=int(tokens.get("thoughts") or 0),
                session_id=str(session_id),
                source_path=key,
            )
            if store.add_record(rec):
                stats.imported += 1
            else:
                stats.skipped += 1

        store.set_state(
            self.source, key,
            mtime_ns=st.st_mtime_ns, size=st.st_size, offset=0, seq=0,
        )
