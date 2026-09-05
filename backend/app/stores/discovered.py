"""discovered 表：扫描发现的「外部资产」索引。

与自建资产(assets)严格分离——这里只记录路径与元数据快照，用于跟踪管理，
**不存放正文、不复制任何原文件**。
"""
from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path

from app.core.config import Settings
from app.scanners.models import DiscoveredItem

_SCHEMA = """
CREATE TABLE IF NOT EXISTS discovered (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    path        TEXT NOT NULL UNIQUE,
    agent       TEXT NOT NULL,
    kind        TEXT NOT NULL,
    name        TEXT NOT NULL,
    size        INTEGER NOT NULL DEFAULT 0,
    mtime       REAL NOT NULL DEFAULT 0,
    fingerprint TEXT NOT NULL DEFAULT '',
    summary     TEXT NOT NULL DEFAULT '',
    tags        TEXT NOT NULL DEFAULT '[]',
    note        TEXT NOT NULL DEFAULT '',
    starred     INTEGER NOT NULL DEFAULT 0,
    extra       TEXT NOT NULL DEFAULT '{}',
    status      TEXT NOT NULL DEFAULT 'active',   -- active / missing
    last_seen   TEXT NOT NULL DEFAULT '',
    created_at  TEXT NOT NULL DEFAULT '',
    updated_at  TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_discovered_agent ON discovered(agent, kind);
CREATE INDEX IF NOT EXISTS idx_discovered_status ON discovered(status);
CREATE INDEX IF NOT EXISTS idx_discovered_kind ON discovered(kind, starred DESC, mtime DESC);
"""

_COLS = (
    "id, path, agent, kind, name, size, mtime, fingerprint, summary, tags, "
    "note, starred, extra, status, last_seen, created_at, updated_at"
)


def _now() -> str:
    from datetime import datetime

    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _row_to_dict(r: sqlite3.Row) -> dict:
    d = dict(r)
    d["tags"] = json.loads(d["tags"] or "[]")
    d["extra"] = json.loads(d["extra"] or "{}")
    return d


class DiscoveredStore:
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

    # ---- 写入 ----------------------------------------------------------
    def sync(self, items: list[DiscoveredItem]) -> int:
        """upsert 一批发现项（按 path），返回写入条数。"""
        now = _now()
        with self._lock:
            c = self.conn
            n = 0
            for it in items:
                fp = it.fingerprint()
                c.execute(
                    """
                    INSERT INTO discovered
                      (path, agent, kind, name, size, mtime, fingerprint, summary, extra,
                       status, last_seen, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'active', ?, ?, ?)
                    ON CONFLICT(path) DO UPDATE SET
                      agent=excluded.agent, kind=excluded.kind, name=excluded.name,
                      size=excluded.size, mtime=excluded.mtime, fingerprint=excluded.fingerprint,
                      summary=excluded.summary, extra=excluded.extra,
                      status='active', last_seen=excluded.last_seen, updated_at=excluded.updated_at
                    """,
                    (
                        str(it.path),
                        it.agent,
                        it.kind.value,
                        it.name,
                        it.size,
                        it.mtime,
                        fp,
                        it.summary,
                        json.dumps(it.extra, ensure_ascii=False),
                        now,
                        now,
                        now,
                    ),
                )
                n += 1
            c.commit()
        return n

    def validate_all(self) -> dict:
        """按路径是否仍存在，刷新 status；返回统计。"""
        active = missing = 0
        with self._lock:
            rows = self.conn.execute("SELECT id, path, mtime, size, fingerprint FROM discovered").fetchall()
            for r in rows:
                p = Path(r["path"])
                if p.exists() and p.is_file():
                    st = p.stat()
                    fp = f"{r['path']}|{st.st_size}|{int(st.st_mtime)}"
                    new_status = "active"
                    new_fp = fp
                    new_size = st.st_size
                    new_mtime = st.st_mtime
                else:
                    new_status = "missing"
                    new_fp = r["fingerprint"]
                    new_size = r["size"]
                    new_mtime = r["mtime"]
                if new_status == "active":
                    active += 1
                else:
                    missing += 1
                self.conn.execute(
                    "UPDATE discovered SET status=?, fingerprint=?, size=?, mtime=?, updated_at=? WHERE id=?",
                    (new_status, new_fp, new_size, new_mtime, _now(), r["id"]),
                )
            self.conn.commit()
        return {"active": active, "missing": missing}

    def refresh_path(self, path: str) -> bool:
        """文件被外部修改后（如技能更新）按磁盘实况刷新对应记录。"""
        p = Path(path)
        with self._lock:
            if p.exists() and p.is_file():
                st = p.stat()
                fp = f"{p}|{st.st_size}|{int(st.st_mtime)}"
                self.conn.execute(
                    "UPDATE discovered SET size=?, mtime=?, fingerprint=?, "
                    "status='active', updated_at=? WHERE path=?",
                    (st.st_size, st.st_mtime, fp, _now(), path),
                )
                self.conn.commit()
                return True
            self.conn.execute(
                "UPDATE discovered SET status='missing', updated_at=? WHERE path=?",
                (_now(), path),
            )
            self.conn.commit()
            return False

    def merge_extra(self, record_id: int, patch: dict) -> dict | None:
        """增量合并 extra 字段（如 hub 来源信息），保留其余键。"""
        rec = self.get(record_id)
        if rec is None:
            return None
        extra = dict(rec.get("extra") or {})
        for k, v in patch.items():
            if isinstance(v, dict) and isinstance(extra.get(k), dict):
                extra[k] = {**extra[k], **v}
            else:
                extra[k] = v
        with self._lock:
            self.conn.execute(
                "UPDATE discovered SET extra=?, updated_at=? WHERE id=?",
                (json.dumps(extra, ensure_ascii=False), _now(), record_id),
            )
            self.conn.commit()
        return self.get(record_id)

    def mark_missing(self, agent_keys: list[str], last_seen_before: str) -> int:
        """本次未扫描到的 agent 旧记录仍保留，仅标记状态由调用方按需处理。"""
        with self._lock:
            cur = self.conn.execute(
                "SELECT COUNT(*) FROM discovered WHERE status='active' AND agent IN ({}) "
                "AND last_seen < ?".format(",".join("?" * len(agent_keys))),
                [*agent_keys, last_seen_before],
            )
            return int(cur.fetchone()[0])

    # ---- 查询 ----------------------------------------------------------
    def list(
        self,
        agent: str | None = None,
        kind: str | None = None,
        status: str | None = None,
        q: str | None = None,
        starred: bool | None = None,
        limit: int = 200,
        offset: int = 0,
    ) -> list[dict]:
        sql = [f"SELECT {_COLS} FROM discovered WHERE 1=1"]
        params: list = []
        if agent:
            sql.append("AND agent = ?")
            params.append(agent)
        if kind:
            sql.append("AND kind = ?")
            params.append(kind)
        if status:
            sql.append("AND status = ?")
            params.append(status)
        if starred:
            sql.append("AND starred = 1")
        if q:
            like = f"%{q}%"
            sql.append("AND (name LIKE ? OR summary LIKE ? OR path LIKE ? OR agent LIKE ?)")
            params += [like, like, like, like]
        sql.append("ORDER BY starred DESC, kind, mtime DESC LIMIT ? OFFSET ?")
        params += [limit, offset]
        with self._lock:
            cur = self.conn.execute(" ".join(sql), params)
            return [_row_to_dict(r) for r in cur.fetchall()]

    def get(self, record_id: int) -> dict | None:
        with self._lock:
            cur = self.conn.execute(f"SELECT {_COLS} FROM discovered WHERE id = ?", (record_id,))
            rows = [_row_to_dict(r) for r in cur.fetchall()]
        return rows[0] if rows else None

    def patch(self, record_id: int, *, starred: bool | None = None,
              note: str | None = None, tags: list[str] | None = None,
              agent: str | None = None, kind: str | None = None) -> dict | None:
        sets: list[str] = []
        params: list = []
        if starred is not None:
            sets.append("starred = ?")
            params.append(1 if starred else 0)
        if note is not None:
            sets.append("note = ?")
            params.append(note)
        if tags is not None:
            sets.append("tags = ?")
            params.append(json.dumps(tags, ensure_ascii=False))
        if agent is not None:
            sets.append("agent = ?")
            params.append(agent)
        if kind is not None:
            sets.append("kind = ?")
            params.append(kind)
        if not sets:
            return self.get(record_id)
        sets.append("updated_at = ?")
        params.append(_now())
        params.append(record_id)
        with self._lock:
            self.conn.execute(f"UPDATE discovered SET {', '.join(sets)} WHERE id = ?", params)
            self.conn.commit()
        return self.get(record_id)

    def stats(self) -> dict:
        with self._lock:
            by_agent_rows = self.conn.execute(
                "SELECT agent, COUNT(*) AS n FROM discovered GROUP BY agent ORDER BY n DESC"
            ).fetchall()
            by_kind_rows = self.conn.execute(
                "SELECT kind, COUNT(*) AS n FROM discovered GROUP BY kind"
            ).fetchall()
            total = int(self.conn.execute("SELECT COUNT(*) FROM discovered").fetchone()[0])
            missing = int(self.conn.execute("SELECT COUNT(*) FROM discovered WHERE status='missing'").fetchone()[0])
            starred = int(self.conn.execute("SELECT COUNT(*) FROM discovered WHERE starred=1").fetchone()[0])
        return {
            "total": total,
            "missing": missing,
            "starred": starred,
            "by_agent": {r["agent"]: int(r["n"]) for r in by_agent_rows},
            "by_kind": {r["kind"]: int(r["n"]) for r in by_kind_rows},
        }
