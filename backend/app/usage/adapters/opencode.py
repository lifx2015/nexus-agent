"""OpenCode 会话用量适配器（含 OpenCode 同构家族基类）。

来源: opencode.db（SQLite），路径优先级 OPENCODE_DB > XDG_DATA_HOME > ~/.local/share。
message.data(JSON) 中 role==assistant 且 time.completed 存在（跳过半截的
进行中消息，否则 usage 不完整且 UNIQUE 无法回填）时，取 tokens：
{input(不含 cache), output, reasoning, cache:{read,write}}—— Anthropic 语义。

WAL 坑：opencode.db 运行在 WAL 模式，新提交先落在 -wal，主库文件要 checkpoint
才更新；因此门控 mtime 取 db 与 db-wal 二者较大值。增量按 session.time_updated
水位（key 用 db_path:session_id）。

家族基类 `_OpenCodeFamilyAdapter`：华为码道（CodeArts Agent）等 OpenCode 系
产品的会话库与此完全同构，子类只需给出各自的 db_paths()（支持多库并存，
增量游标按 (db_path, session_id) 命名空间天然隔离）。
"""
from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path

from app.usage.adapters.base import BaseUsageAdapter, register
from app.usage.common import SyncStats, expand_path
from app.usage.store import UsageRecord, UsageStore


def _resolve_db_path() -> Path | None:
    env = os.environ.get("OPENCODE_DB")
    if env:
        p = expand_path(env)
        return p if p.exists() else None
    xdg = os.environ.get("XDG_DATA_HOME")
    base = Path(xdg) if xdg else Path.home() / ".local" / "share"
    p = base / "opencode" / "opencode.db"
    return p if p.exists() else None


class _OpenCodeFamilyAdapter(BaseUsageAdapter):
    """OpenCode 同构会话库家族基类（opencode.db 口径）。"""

    source: str = ""
    agent: str = ""
    name: str = ""

    def db_paths(self) -> list[Path]:
        """候选 opencode.db 列表（存在的）。子类覆写。"""
        return []

    def roots(self) -> list[Path]:
        return [p.parent for p in self.db_paths()]

    def collect(self, store: UsageStore) -> SyncStats:
        stats = SyncStats(self.source)
        for db_path in self.db_paths():
            self._collect_db(store, db_path, stats)
        return stats

    def _collect_db(self, store: UsageStore, db_path: Path, stats: SyncStats) -> None:
        # 门控 mtime：WAL 模式下取主库与 -wal 较大值
        file_mtime = self._combined_mtime(db_path)
        gate_key = str(db_path)
        cur = store.get_state(self.source, gate_key)
        if cur and int(cur["seq"]) == file_mtime:
            stats.skipped += 1
            stats.files_scanned += 1
            return
        stats.files_scanned += 1

        try:
            conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
            conn.row_factory = sqlite3.Row
        except sqlite3.Error as e:
            stats.errors.append(f"{db_path}: {e}")
            return

        dir_by_session: dict[str, str] = {}
        try:
            dir_by_session = {
                r[0]: (str(Path(r[1])) if r[1] else "")
                for r in conn.execute("SELECT id, directory FROM session").fetchall()
            }
        except sqlite3.Error:
            pass

        try:
            for sid, water in self._sessions(conn):
                skey = f"{db_path}:{sid}"
                scur = store.get_state(self.source, skey)
                if scur and int(scur["size"]) >= water:
                    continue  # 会话水位未推进
                for rec in self._assistant_records(conn, sid, db_path):
                    rec.project = dir_by_session.get(sid, "")
                    if store.add_record(rec):
                        stats.imported += 1
                    else:
                        stats.skipped += 1
                store.set_state(
                    self.source, skey,
                    mtime_ns=0, size=water, offset=0, seq=0,
                )
            store.set_state(
                self.source, gate_key,
                mtime_ns=0, size=0, offset=0, seq=file_mtime,
            )
        except sqlite3.Error as e:
            stats.errors.append(f"{db_path}: {e}")
        finally:
            conn.close()

    @staticmethod
    def _combined_mtime(db_path: Path) -> int:
        m = db_path.stat().st_mtime_ns
        wal = db_path.with_suffix(".db-wal")
        if wal.exists():
            m = max(m, wal.stat().st_mtime_ns)
        return m

    @staticmethod
    def _sessions(conn: sqlite3.Connection) -> list[tuple[str, int]]:
        try:
            rows = conn.execute(
                """
                SELECT s.id,
                       MAX(s.time_updated, COALESCE(MAX(m.time_updated), s.time_updated)) AS watermark
                FROM session s
                LEFT JOIN message m ON m.session_id = s.id
                GROUP BY s.id
                """
            ).fetchall()
        except sqlite3.Error:
            return []  # 表结构未就绪（opencode 未初始化）
        return [(r["id"], int(r["watermark"] or 0)) for r in rows]

    def _assistant_records(
        self, conn: sqlite3.Connection, session_id: str, db_path: Path,
    ) -> list[UsageRecord]:
        try:
            rows = conn.execute(
                "SELECT id, data FROM message WHERE session_id = ? ORDER BY time_created",
                (session_id,),
            ).fetchall()
        except sqlite3.Error:
            return []

        out: list[UsageRecord] = []
        for r in rows:
            try:
                value = json.loads(r["data"])
            except (ValueError, TypeError):
                continue
            if not isinstance(value, dict) or value.get("role") != "assistant":
                continue
            time_obj = value.get("time")
            if not isinstance(time_obj, dict) or "completed" not in time_obj:
                continue  # 进行中：半截 usage，跳过等下轮补全
            tokens = value.get("tokens")
            if not isinstance(tokens, dict):
                continue
            cache = tokens.get("cache") if isinstance(tokens.get("cache"), dict) else {}
            ts_ms = time_obj.get("completed") or time_obj.get("created") or 0
            try:
                v = int(ts_ms)
            except (TypeError, ValueError):
                v = 0
            ts = v // 1000 if v > 100_000_000_000 else v  # 毫秒 → 秒（>1e11 判定为毫秒）
            rec = UsageRecord(
                request_id=f"{self.source}_session:{session_id}:{r['id']}",
                agent=self.agent,
                model=str(value.get("modelID") or "unknown"),
                ts=ts,
                input_tokens=int(tokens.get("input") or 0),
                output_tokens=int(tokens.get("output") or 0),
                cache_read_tokens=int(cache.get("read") or 0),
                cache_creation_tokens=int(cache.get("write") or 0),
                reasoning_tokens=int(tokens.get("reasoning") or 0),
                session_id=session_id,
                source_path=str(db_path),
            )
            if rec.has_billable():
                out.append(rec)
        return out


@register
class OpenCodeAdapter(_OpenCodeFamilyAdapter):
    source = "opencode"
    agent = "opencode"
    name = "OpenCode"

    def db_paths(self) -> list[Path]:
        p = _resolve_db_path()
        return [p] if p else []
