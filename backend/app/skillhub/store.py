"""SkillHub 对比快照存储（与资产索引同库 nexus.db，单行快照）。

对比一次全量本机技能约数百条，整包 JSON 存一行即可：
  - POST /compare 计算完成后整包覆写
  - GET  /compare 直接读快照（不访问网络），并过滤已失效的发现项
  - 更新技能成功后仅修正对应条目，避免整页快照与实际版本脱节
"""
from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime

from app.core.config import Settings

SUMMARY_KEYS = ("total", "matched", "updatable", "up_to_date", "ahead", "unknown_local", "not_found")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS skillhub_compare (
    id         INTEGER PRIMARY KEY CHECK (id = 1),
    payload    TEXT NOT NULL DEFAULT '[]',
    summary    TEXT NOT NULL DEFAULT '{}',
    elapsed    REAL NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT ''
);
"""


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def recount_states(items: list[dict]) -> dict:
    """按条目状态重算汇总（快照过滤/修正后保持口径一致）。"""
    s = dict.fromkeys(SUMMARY_KEYS, 0)
    s["total"] = len(items)
    for it in items:
        st = it.get("state")
        if st == "update-available":
            s["matched"] += 1
            s["updatable"] += 1
        elif st == "up-to-date":
            s["matched"] += 1
            s["up_to_date"] += 1
        elif st == "ahead":
            s["matched"] += 1
            s["ahead"] += 1
        elif st == "unknown-local":
            s["matched"] += 1
            s["unknown_local"] += 1
        else:
            s["not_found"] += 1
    return s


def empty_summary() -> dict:
    return dict.fromkeys(SUMMARY_KEYS, 0)


class SkillHubStore:
    def __init__(self, settings: Settings):
        self.settings = settings
        self._conn: sqlite3.Connection | None = None
        self._lock = threading.RLock()

    @property
    def conn(self) -> sqlite3.Connection:
        if self._conn is None:
            with self._lock:
                if self._conn is None:
                    self.settings.ensure_layout()
                    conn = sqlite3.connect(str(self.settings.db_path), check_same_thread=False)
                    conn.row_factory = sqlite3.Row
                    conn.executescript(_SCHEMA)
                    conn.execute("PRAGMA journal_mode=WAL")
                    conn.execute("PRAGMA busy_timeout=5000")
                    conn.commit()
                    self._conn = conn
        return self._conn

    def close(self) -> None:
        with self._lock:
            if self._conn is not None:
                self._conn.close()
                self._conn = None

    # ---- 快照 ----------------------------------------------------------
    def save_snapshot(self, items: list[dict], summary: dict, elapsed: float) -> str:
        """整包覆写快照，返回保存时间。"""
        ts = _now()
        with self._lock:
            self.conn.execute(
                "INSERT INTO skillhub_compare (id, payload, summary, elapsed, created_at) VALUES (1, ?, ?, ?, ?) "
                "ON CONFLICT(id) DO UPDATE SET payload=excluded.payload, summary=excluded.summary, "
                "elapsed=excluded.elapsed, created_at=excluded.created_at",
                (json.dumps(items, ensure_ascii=False), json.dumps(summary, ensure_ascii=False), elapsed, ts),
            )
            self.conn.commit()
        return ts

    def load_snapshot(self) -> dict | None:
        with self._lock:
            row = self.conn.execute(
                "SELECT payload, summary, elapsed, created_at FROM skillhub_compare WHERE id = 1"
            ).fetchone()
        if row is None or not row["created_at"]:
            return None
        return {
            "items": json.loads(row["payload"] or "[]"),
            "summary": json.loads(row["summary"] or "{}") or empty_summary(),
            "elapsed": float(row["elapsed"] or 0),
            "created_at": row["created_at"],
        }

    def patch_snapshot_item(self, record_id: int, **fields) -> bool:
        """修正快照中单条记录（如更新后置为已最新）。无快照或无此条返回 False。"""
        snap = self.load_snapshot()
        if snap is None:
            return False
        hit = False
        for it in snap["items"]:
            if it.get("record_id") == record_id:
                it.update(fields)
                hit = True
        if not hit:
            return False
        self.save_snapshot(snap["items"], recount_states(snap["items"]), snap["elapsed"])
        return True

    def clear_snapshot(self) -> None:
        with self._lock:
            self.conn.execute("DELETE FROM skillhub_compare WHERE id = 1")
            self.conn.commit()
