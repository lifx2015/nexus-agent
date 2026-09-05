"""Claude Code 会话用量适配器。

来源: ~/.claude/projects/<转义项目路径>/<会话uuid>.jsonl（也支持 CLAUDE_CONFIG_DIR 覆盖）
每行一条事件；`type=assistant` 行携带 `message.usage`（Anthropic 原生语义：
input 不含 cache，cache_read/cache_creation 单列）。

增量策略：字节 offset 游标，只推进到最后一个以换行结尾的完整行
（会话进行中的尾行可能半截，读半截会造成 JSON 解析失败与游标错乱）。
文件内按 message.id 去重：同一条响应会被流式写多行快照，
保留「有 stop_reason 优先、其次 output 最大」的那条 —— 与 cc-switch 一致。
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from app.projects.paths import decode_project_dir
from app.usage.adapters.base import MAX_FILES_PER_SYNC, BaseUsageAdapter, register
from app.usage.common import SyncStats, parse_ts
from app.usage.store import UsageRecord, UsageStore


@register
class ClaudeCodeAdapter(BaseUsageAdapter):
    source = "claude"
    agent = "claude-code"
    name = "Claude Code"

    def roots(self) -> list[Path]:
        return ["$CLAUDE_CONFIG_DIR/projects", "~/.claude/projects"]

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
            stats.skipped += 1  # 文件无变化
            return
        if st.st_size < offset:
            offset = 0  # 文件被截断/重写，从头再来（UNIQUE 幂等兜底）

        consumed = offset
        session_id: str | None = None
        best: dict[str, UsageRecord] = {}      # message.id → 代表行
        has_stop: dict[str, bool] = {}

        with path.open("rb") as f:
            f.seek(offset)
            while True:
                line = f.readline()
                if not line or not line.endswith(b"\n"):
                    break  # EOF 或不完整尾行：游标停在原地，下轮续读
                line_start = consumed
                consumed += len(line)
                try:
                    value = json.loads(line)
                except ValueError:
                    continue
                if not isinstance(value, dict):
                    continue
                if session_id is None and isinstance(value.get("sessionId"), str):
                    session_id = value["sessionId"]
                if value.get("type") != "assistant":
                    continue
                msg = value.get("message")
                usage = msg.get("usage") if isinstance(msg, dict) else None
                if not isinstance(usage, dict):
                    continue

                msg_id = msg.get("id") or ""
                rec = UsageRecord(
                    request_id=(
                        f"session:{msg_id}" if msg_id
                        # 无 id 的防御路径：文件+字节位置确定性散列，重放仍幂等
                        else f"claude:{hashlib.sha1(f'{key}:{line_start}'.encode()).hexdigest()[:24]}"
                    ),
                    agent=self.agent,
                    model=str(msg.get("model") or "unknown"),
                    ts=parse_ts(value.get("timestamp")) or int(st.st_mtime),
                    input_tokens=int(usage.get("input_tokens") or 0),
                    output_tokens=int(usage.get("output_tokens") or 0),
                    cache_read_tokens=int(usage.get("cache_read_input_tokens") or 0),
                    cache_creation_tokens=int(usage.get("cache_creation_input_tokens") or 0),
                    session_id=session_id or path.stem,
                    source_path=key,
                    # 会话文件位于 projects/<转义项目路径>/ 下，目录名即项目
                    project=str(Path(decode_project_dir(path.parent.name))),
                )
                stopped = bool(msg.get("stop_reason"))
                old = best.get(rec.request_id) if msg_id else None
                if old is None:
                    best[rec.request_id] = rec
                    has_stop[rec.request_id] = stopped
                elif stopped and not has_stop[rec.request_id]:
                    best[rec.request_id] = rec
                    has_stop[rec.request_id] = True
                elif stopped == has_stop[rec.request_id] and rec.output_tokens > old.output_tokens:
                    best[rec.request_id] = rec

        for rec in best.values():
            if store.add_record(rec):
                stats.imported += 1
            else:
                stats.skipped += 1
        store.set_state(
            self.source, key,
            mtime_ns=st.st_mtime_ns, size=st.st_size, offset=consumed, seq=0,
        )
