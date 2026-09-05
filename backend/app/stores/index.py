"""SQLite 索引库：元数据镜像 + 关键字检索。

- 文件是事实源，本库只是便于 list/filter/search/stats 的镜像，可用 reconcile 全量重建。
- 本地资产规模(百~千条)下 LIKE 检索简单可靠，无需 FTS5 及其触发器等复杂度。
- FastAPI 同步路由运行在线程池中，因此连接以 check_same_thread=False 创建并加锁保护。
"""
from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path

from app.core.config import Settings
from app.domain import Asset

_SCHEMA = """
CREATE TABLE IF NOT EXISTS assets (
    id         TEXT PRIMARY KEY,
    kind       TEXT NOT NULL,
    name       TEXT NOT NULL,
    status     TEXT NOT NULL DEFAULT 'enabled',
    tags       TEXT NOT NULL DEFAULT '[]',
    meta       TEXT NOT NULL DEFAULT '{}',
    file_path  TEXT NOT NULL,
    body       TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    version    INTEGER NOT NULL DEFAULT 1
);
CREATE INDEX IF NOT EXISTS idx_assets_kind_status ON assets(kind, status);
CREATE INDEX IF NOT EXISTS idx_assets_updated ON assets(updated_at DESC);
"""

_ITEM_COLUMNS = "id, kind, name, status, tags, meta, file_path, created_at, updated_at, version"


class IndexStore:
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
                    # WAL 让「Web 服务」与「MCP server」两个进程可安全并发读写
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
    def upsert_asset(self, asset: Asset) -> None:
        with self._lock:
            self.conn.execute(
                """
                INSERT INTO assets(id, kind, name, status, tags, meta, file_path, body, created_at, updated_at, version)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                  name=excluded.name, kind=excluded.kind, status=excluded.status,
                  tags=excluded.tags, meta=excluded.meta, file_path=excluded.file_path,
                  body=excluded.body, created_at=excluded.created_at,
                  updated_at=excluded.updated_at, version=excluded.version
                """,
                (
                    asset.id,
                    asset.kind.value,
                    asset.name,
                    asset.status.value,
                    json.dumps(asset.tags, ensure_ascii=False),
                    json.dumps(asset.metadata, ensure_ascii=False),
                    asset.rel_path,
                    asset.body,
                    asset.created_at,
                    asset.updated_at,
                    int(asset.version),
                ),
            )
            self.conn.commit()

    def delete_asset(self, asset_id: str) -> None:
        with self._lock:
            self.conn.execute("DELETE FROM assets WHERE id = ?", (asset_id,))
            self.conn.commit()

    def clear(self) -> None:
        with self._lock:
            self.conn.execute("DELETE FROM assets")
            self.conn.commit()

    # ---- 查询 ----------------------------------------------------------
    def _rows_to_assets(self, rows: list[sqlite3.Row]) -> list[dict]:
        out = []
        for r in rows:
            d = dict(r)
            d["tags"] = json.loads(d["tags"] or "[]")
            d["metadata"] = json.loads(d["meta"] or "{}")
            d.pop("meta", None)
            # 统一外部字段名：file_path -> rel_path
            d["rel_path"] = d.pop("file_path", "")
            out.append(d)
        return out

    def list_assets(
        self,
        kind: str | None = None,
        status: str | None = None,
        tag: str | None = None,
        q: str | None = None,
        limit: int = 200,
        offset: int = 0,
        include_body: bool = False,
    ) -> list[dict]:
        # 列表场景默认不取正文，减少传输与渲染开销
        columns = "*" if include_body else _ITEM_COLUMNS
        sql = [f"SELECT {columns} FROM assets WHERE 1=1"]
        params: list = []
        if kind:
            sql.append("AND kind = ?")
            params.append(kind)
        if status:
            sql.append("AND status = ?")
            params.append(status)
        if tag:
            sql.append("AND EXISTS (SELECT 1 FROM json_each(assets.tags) WHERE json_each.value = ?)")
            params.append(tag)
        if q:
            like = f"%{q}%"
            sql.append("AND (name LIKE ? OR body LIKE ? OR tags LIKE ?)")
            params += [like, like, like]
        sql.append("ORDER BY updated_at DESC LIMIT ? OFFSET ?")
        params += [limit, offset]
        with self._lock:
            cur = self.conn.execute(" ".join(sql), params)
            return self._rows_to_assets(cur.fetchall())

    def get_asset(self, asset_id: str) -> dict | None:
        with self._lock:
            cur = self.conn.execute("SELECT * FROM assets WHERE id = ?", (asset_id,))
            rows = self._rows_to_assets(cur.fetchall())
        return rows[0] if rows else None

    def count_assets(self) -> int:
        with self._lock:
            return int(self.conn.execute("SELECT COUNT(*) FROM assets").fetchone()[0])

    def stats(self) -> dict:
        """按类型与状态统计，供仪表盘使用。"""
        with self._lock:
            rows = self.conn.execute(
                "SELECT kind, status, COUNT(*) AS n FROM assets GROUP BY kind, status"
            ).fetchall()
            total = int(self.conn.execute("SELECT COUNT(*) FROM assets").fetchone()[0])
        by_kind: dict[str, dict[str, int]] = {}
        totals: dict[str, int] = {}
        for r in rows:
            by_kind.setdefault(r["kind"], {})[r["status"]] = int(r["n"])
            totals[r["kind"]] = totals.get(r["kind"], 0) + int(r["n"])
        return {"by_kind": by_kind, "totals": totals, "total": total}
