"""Codex CLI 会话用量适配器。

来源: ~/.codex/sessions/YYYY/MM/DD/rollout-*.jsonl（支持 CODEX_HOME 覆盖）
关键事件：
- `session_meta` → session_id
- `turn_context` → 当前 model
- `event_msg` + payload.type == "token_count" → `info.last_token_usage`
  （OpenAI 语义：input_tokens **包含** cached_input_tokens → 写入侧扣减归一）

增量策略：字节 offset 游标 + seq 续编号。request_id = codex:{session}:{seq}，
seq 只在遇到 token_count 事件时递增（即使该事件 usage 全 0 也占号），
保证跨轮次编号稳定、绝不误伤后序事件。
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


def _head_cwd(path: Path) -> str:
    """快速取文件首行 session_meta 的 cwd（增量续读时 meta 已越过游标，需回看）。"""
    try:
        with path.open("r", encoding="utf-8", errors="ignore") as f:
            line = f.readline(65536)
        value = json.loads(line) if line.strip() else {}
    except (OSError, ValueError):
        return ""
    payload = value.get("payload") if isinstance(value, dict) else None
    if isinstance(payload, dict) and isinstance(payload.get("cwd"), str):
        return str(Path(payload["cwd"]))
    return ""


@register
class CodexAdapter(BaseUsageAdapter):
    source = "codex"
    agent = "codex"
    name = "Codex CLI"

    def roots(self) -> list[Path]:
        return ["$CODEX_HOME/sessions", "~/.codex/sessions"]

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
        seq = int(cur["seq"]) if cur else 0
        if cur and int(cur["mtime_ns"]) == st.st_mtime_ns and int(cur["size"]) == st.st_size:
            stats.skipped += 1
            return
        if st.st_size < offset:
            offset, seq = 0, 0

        consumed = offset
        session_id = path.stem  # rollout-<ts>-<uuid>.jsonl，文件名兜底
        model = "unknown"
        project = _head_cwd(path)  # 首行回看，增量续读也不丢项目归属

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
                if not isinstance(value, dict):
                    continue
                payload = value.get("payload")
                if not isinstance(payload, dict):
                    continue
                etype = value.get("type")

                if etype == "session_meta":
                    sid = payload.get("session_id") or payload.get("id")
                    if isinstance(sid, str) and sid:
                        session_id = sid
                    cwd = payload.get("cwd")
                    if isinstance(cwd, str) and cwd:
                        project = str(Path(cwd))
                elif etype == "turn_context":
                    m = payload.get("model")
                    if isinstance(m, str) and m:
                        model = m
                elif etype == "event_msg" and payload.get("type") == "token_count":
                    seq += 1  # 占号事件，无论是否可计费
                    info = payload.get("info")
                    if not isinstance(info, dict):
                        continue
                    last = info.get("last_token_usage")
                    if not isinstance(last, dict):
                        continue
                    input_all = _u(last, "input_tokens")
                    cached = _u(last, "cached_input_tokens")
                    rec = UsageRecord(
                        request_id=f"codex:{session_id}:{seq}",
                        agent=self.agent,
                        model=model,
                        ts=parse_ts(value.get("timestamp")) or int(st.st_mtime),
                        # 归一：OpenAI input 含 cache → 扣出 fresh
                        input_tokens=max(0, input_all - cached),
                        output_tokens=_u(last, "output_tokens"),
                        cache_read_tokens=cached,
                        reasoning_tokens=_u(last, "reasoning_output_tokens"),
                        session_id=session_id,
                        source_path=key,
                        project=project,
                    )
                    if store.add_record(rec):
                        stats.imported += 1
                    else:
                        stats.skipped += 1

        store.set_state(
            self.source, key,
            mtime_ns=st.st_mtime_ns, size=st.st_size, offset=consumed, seq=seq,
        )
