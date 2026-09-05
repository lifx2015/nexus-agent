"""流量统计明细库：usage_records + usage_sync_state。

设计（参照 cc-switch proxy_request_logs，但按本项目裁剪）：
- 一条 usage_records = 一次模型调用（一条 assistant 响应）；
- **token 语义在写入侧归一**：input_tokens 恒为 fresh(不含 cache)，
  cache_read/cache_creation 单列，读侧聚合无需语义分支；
- usage_sync_state 存各来源文件的增量游标(mtime_ns/offset/seq)，
  保证会话文件只读新增部分、重复同步幂等；
- request_id 全局唯一 + INSERT OR IGNORE 天然去重；
- 第一期只统计 token 数，不折算成本。
"""
from __future__ import annotations

import sqlite3
import threading
from dataclasses import dataclass
from datetime import datetime

from app.core.config import Settings

_SCHEMA = """
CREATE TABLE IF NOT EXISTS usage_records (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    request_id            TEXT NOT NULL UNIQUE,
    agent                 TEXT NOT NULL,
    model                 TEXT NOT NULL,
    session_id            TEXT,
    ts                    INTEGER NOT NULL,          -- unix 秒
    input_tokens          INTEGER NOT NULL DEFAULT 0, -- 归一 fresh，不含 cache
    output_tokens         INTEGER NOT NULL DEFAULT 0,
    cache_read_tokens     INTEGER NOT NULL DEFAULT 0,
    cache_creation_tokens INTEGER NOT NULL DEFAULT 0,
    reasoning_tokens      INTEGER NOT NULL DEFAULT 0,
    source_path           TEXT,
    project               TEXT NOT NULL DEFAULT '',  -- 所属项目路径（可空串，扫描时回填）
    created_at            TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_usage_ts       ON usage_records(ts);
CREATE INDEX IF NOT EXISTS idx_usage_agent_ts ON usage_records(agent, ts);
CREATE INDEX IF NOT EXISTS idx_usage_model_ts ON usage_records(model, ts);
-- idx_usage_project 由 _migrate 在建列之后创建：旧库缺 project 列时在此处建索引会直接报错

CREATE TABLE IF NOT EXISTS usage_sync_state (
    source     TEXT NOT NULL,   -- 适配器标识，如 claude/codex
    path       TEXT NOT NULL,   -- 来源文件绝对路径
    mtime_ns   INTEGER NOT NULL DEFAULT 0,
    size       INTEGER NOT NULL DEFAULT 0,
    offset     INTEGER NOT NULL DEFAULT 0,  -- JSONL 字节游标
    seq        INTEGER NOT NULL DEFAULT 0,  -- 已导入条数(Codex 续编号用)
    updated_at TEXT NOT NULL,
    PRIMARY KEY (source, path)
);
"""


def _now() -> str:
    """系统时区当前时间，格式 yyyy-MM-dd HH:mm:ss。"""
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


@dataclass
class UsageRecord:
    """一条归一后的模型调用用量。"""

    request_id: str
    agent: str
    model: str
    ts: int                        # unix 秒
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_creation_tokens: int = 0
    reasoning_tokens: int = 0
    session_id: str | None = None
    source_path: str | None = None
    project: str = ""     # 所属项目路径（适配器能取到就填，否则留空待回填）

    def has_billable(self) -> bool:
        """任一计费维度 > 0 才算有效用量（过滤全 0 空行）。"""
        return (
            self.input_tokens > 0
            or self.output_tokens > 0
            or self.cache_read_tokens > 0
            or self.cache_creation_tokens > 0
            or self.reasoning_tokens > 0
        )

    def real_total(self) -> int:
        """真实处理 token = fresh 输入 + 输出 + 缓存建 + 缓存读。"""
        return (
            self.input_tokens
            + self.output_tokens
            + self.cache_read_tokens
            + self.cache_creation_tokens
        )


class UsageStore:
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
                self._migrate(conn)
                conn.execute("PRAGMA journal_mode=WAL")
                conn.execute("PRAGMA busy_timeout=5000")
                conn.commit()
                self._conn = conn
        return self._conn

    @staticmethod
    def _migrate(conn: sqlite3.Connection) -> None:
        """旧库增量迁移：给已存在的 usage_records 补 project 列（幂等）。"""
        cols = {r[1] for r in conn.execute("PRAGMA table_info(usage_records)")}
        if cols and "project" not in cols:
            conn.execute(
                "ALTER TABLE usage_records ADD COLUMN project TEXT NOT NULL DEFAULT ''"
            )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_usage_project ON usage_records(project, ts)"
        )

    def close(self) -> None:
        with self._lock:
            if self._conn is not None:
                self._conn.close()
                self._conn = None

    # ---- 游标 ----------------------------------------------------------
    def get_state(self, source: str, path: str) -> sqlite3.Row | None:
        with self._lock:
            cur = self.conn.execute(
                "SELECT mtime_ns, size, offset, seq FROM usage_sync_state "
                "WHERE source = ? AND path = ?",
                (source, path),
            )
            return cur.fetchone()

    def set_state(
        self, source: str, path: str, *, mtime_ns: int, size: int,
        offset: int, seq: int,
    ) -> None:
        with self._lock:
            self.conn.execute(
                """
                INSERT INTO usage_sync_state(source, path, mtime_ns, size, offset, seq, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(source, path) DO UPDATE SET
                  mtime_ns=excluded.mtime_ns, size=excluded.size,
                  offset=excluded.offset, seq=excluded.seq,
                  updated_at=excluded.updated_at
                """,
                (source, path, mtime_ns, size, offset, seq, _now()),
            )
            self.conn.commit()

    # ---- 写入 ----------------------------------------------------------
    def add_record(self, rec: UsageRecord) -> bool:
        """幂等插入一条用量；返回是否真正写入(未被 UNIQUE 忽略)。"""
        if not rec.has_billable():
            return False
        with self._lock:
            before = self._total_changes()
            self.conn.execute(
                """
                INSERT OR IGNORE INTO usage_records(
                    request_id, agent, model, session_id, ts,
                    input_tokens, output_tokens, cache_read_tokens,
                    cache_creation_tokens, reasoning_tokens, source_path,
                    project, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    rec.request_id, rec.agent, rec.model, rec.session_id, rec.ts,
                    rec.input_tokens, rec.output_tokens, rec.cache_read_tokens,
                    rec.cache_creation_tokens, rec.reasoning_tokens,
                    rec.source_path, rec.project, _now(),
                ),
            )
            self.conn.commit()
            return self._total_changes() > before

    def _total_changes(self) -> int:
        return int(self.conn.execute("SELECT total_changes()").fetchone()[0])

    # ---- 查询：过滤条件构造 --------------------------------------------
    @staticmethod
    def _where(
        start_ts: int | None, end_ts: int | None,
        agent: str | None, model: str | None,
    ) -> tuple[str, list]:
        conds: list[str] = []
        params: list = []
        if start_ts is not None:
            conds.append("ts >= ?")
            params.append(start_ts)
        if end_ts is not None:
            conds.append("ts <= ?")
            params.append(end_ts)
        if agent:
            conds.append("agent = ?")
            params.append(agent)
        if model:
            conds.append("model = ?")
            params.append(model)
        where = ("WHERE " + " AND ".join(conds)) if conds else ""
        return where, params

    # ---- 查询：汇总 ----------------------------------------------------
    def summary(
        self, *, start_ts: int | None = None, end_ts: int | None = None,
        agent: str | None = None, model: str | None = None,
    ) -> dict:
        where, params = self._where(start_ts, end_ts, agent, model)
        sql = f"""
            SELECT
                COUNT(*)                       AS requests,
                COALESCE(SUM(input_tokens), 0) AS input_tokens,
                COALESCE(SUM(output_tokens), 0) AS output_tokens,
                COALESCE(SUM(cache_read_tokens), 0) AS cache_read_tokens,
                COALESCE(SUM(cache_creation_tokens), 0) AS cache_creation_tokens,
                COALESCE(SUM(reasoning_tokens), 0) AS reasoning_tokens,
                COUNT(DISTINCT session_id)     AS sessions
            FROM usage_records {where}
        """
        with self._lock:
            row = self.conn.execute(sql, params).fetchone()
        d = dict(row)
        inp = d["input_tokens"]
        cr = d["cache_read_tokens"]
        cc = d["cache_creation_tokens"]
        out = d["output_tokens"]
        d["real_total_tokens"] = inp + out + cr + cc
        cacheable = inp + cr + cc
        d["cache_hit_rate"] = (cr / cacheable) if cacheable > 0 else 0.0
        return d

    # ---- 查询：趋势分桶 ------------------------------------------------
    def trends(
        self, *, start_ts: int | None = None, end_ts: int | None = None,
        agent: str | None = None, model: str | None = None,
        granularity: str = "hour",
    ) -> list[dict]:
        """按本地时间分桶（SQL localtime 与前端展示同为系统时区，避免 UTC 错位）。

        返回 [{bucket: 标签串, requests, tokens, input_tokens, output_tokens}]；
        缺口补零由调用方（路由层）负责。
        """
        fmt = "%Y-%m-%d %H:00" if granularity == "hour" else "%Y-%m-%d"
        where, params = self._where(start_ts, end_ts, agent, model)
        sql = f"""
            SELECT strftime('{fmt}', ts, 'unixepoch', 'localtime') AS bucket,
                   COUNT(*) AS requests,
                   COALESCE(SUM(input_tokens + output_tokens + cache_read_tokens + cache_creation_tokens), 0) AS tokens,
                   COALESCE(SUM(input_tokens), 0) AS input_tokens,
                   COALESCE(SUM(output_tokens), 0) AS output_tokens,
                   COALESCE(SUM(cache_read_tokens), 0) AS cache_read_tokens,
                   COALESCE(SUM(cache_creation_tokens), 0) AS cache_creation_tokens
            FROM usage_records {where}
            GROUP BY bucket ORDER BY bucket ASC
        """
        with self._lock:
            rows = self.conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]

    # ---- 查询：分组统计 ------------------------------------------------
    def group_stats(
        self, dim: str, *, start_ts: int | None = None, end_ts: int | None = None,
        limit: int = 100, offset: int = 0,
    ) -> list[dict]:
        """dim ∈ {agent, model}：按维度聚合 token/请求，按真实总量降序，支持分页。"""
        if dim not in ("agent", "model"):
            raise ValueError(f"非法分组维度: {dim}")
        where, params = self._where(start_ts, end_ts, None, None)
        sql = f"""
            SELECT {dim} AS key,
                   COUNT(*) AS requests,
                   COALESCE(SUM(input_tokens), 0) AS input_tokens,
                   COALESCE(SUM(output_tokens), 0) AS output_tokens,
                   COALESCE(SUM(cache_read_tokens), 0) AS cache_read_tokens,
                   COALESCE(SUM(cache_creation_tokens), 0) AS cache_creation_tokens,
                   COALESCE(SUM(input_tokens + output_tokens + cache_read_tokens + cache_creation_tokens), 0) AS real_total_tokens
            FROM usage_records {where}
            GROUP BY {dim} ORDER BY real_total_tokens DESC LIMIT ? OFFSET ?
        """
        with self._lock:
            rows = self.conn.execute(sql, [*params, limit, offset]).fetchall()
        return [dict(r) for r in rows]

    def count_group_stats(
        self, dim: str, *, start_ts: int | None = None, end_ts: int | None = None,
    ) -> int:
        """分组分页总数：该时间范围内不同 {dim} 的个数。"""
        if dim not in ("agent", "model"):
            raise ValueError(f"非法分组维度: {dim}")
        where, params = self._where(start_ts, end_ts, None, None)
        with self._lock:
            return int(
                self.conn.execute(
                    f"SELECT COUNT(DISTINCT {dim}) FROM usage_records {where}", params
                ).fetchone()[0]
            )

    # ---- 查询：明细列表 + 分页 -----------------------------------------
    def records(
        self, *, start_ts: int | None = None, end_ts: int | None = None,
        agent: str | None = None, model: str | None = None,
        limit: int = 50, offset: int = 0,
    ) -> tuple[list[dict], int]:
        where, params = self._where(start_ts, end_ts, agent, model)
        with self._lock:
            total = int(
                self.conn.execute(
                    f"SELECT COUNT(*) FROM usage_records {where}", params
                ).fetchone()[0]
            )
            rows = self.conn.execute(
                f"""
                SELECT request_id, agent, model, session_id, ts,
                        input_tokens, output_tokens, cache_read_tokens,
                        cache_creation_tokens, reasoning_tokens, source_path, project
                FROM usage_records {where}
                ORDER BY ts DESC LIMIT ? OFFSET ?
                """,
                [*params, limit, offset],
            ).fetchall()
        return [dict(r) for r in rows], total

    def backfill_projects(self, mapping: list[tuple[str, str, str]]) -> int:
        """按 (agent, session_id) → 项目路径 回填 usage_records.project。

        用临时表做单次 JOIN-UPDATE（万级明细 × 数千映射，逐条 UPDATE 会太慢），
        幂等：只动 project 仍为空串的行；重复执行 rowcount 为 0。
        返回被回填的明细条数。
        """
        if not mapping:
            return 0
        with self._lock:
            c = self.conn
            c.execute("CREATE TEMP TABLE IF NOT EXISTS _nx_pf(agent TEXT, session_id TEXT, project TEXT)")
            c.execute("DELETE FROM _nx_pf")
            c.executemany("INSERT INTO _nx_pf VALUES (?, ?, ?)", mapping)
            cur = c.execute(
                """
                UPDATE usage_records
                SET project = (
                    SELECT f.project FROM _nx_pf f
                    WHERE f.agent = usage_records.agent
                      AND f.session_id = usage_records.session_id
                      LIMIT 1
                )
                WHERE project = ''
                  AND EXISTS (
                    SELECT 1 FROM _nx_pf f
                    WHERE f.agent = usage_records.agent
                      AND f.session_id = usage_records.session_id
                  )
                """
            )
            c.execute("DROP TABLE _nx_pf")
            n = cur.rowcount
            c.commit()
        return n if n and n > 0 else 0

    def usage_by_project(self) -> dict[str, dict]:
        """按项目聚合：{项目路径: {requests, tokens}}（仅已归属项目的明细）。"""
        with self._lock:
            rows = self.conn.execute(
                """
                SELECT project,
                       COUNT(*) AS requests,
                       COALESCE(SUM(input_tokens + output_tokens
                                     + cache_read_tokens + cache_creation_tokens), 0) AS tokens
                FROM usage_records
                WHERE project != ''
                GROUP BY project
                """
            ).fetchall()
        return {r["project"]: {"requests": int(r["requests"]), "tokens": int(r["tokens"])} for r in rows}

    def all_agents(self) -> list[str]:
        with self._lock:
            rows = self.conn.execute(
                "SELECT DISTINCT agent FROM usage_records ORDER BY agent"
            ).fetchall()
        return [r["agent"] for r in rows]

    def all_models(self) -> list[str]:
        with self._lock:
            rows = self.conn.execute(
                "SELECT DISTINCT model FROM usage_records ORDER BY model"
            ).fetchall()
        return [r["model"] for r in rows]

    def reset(self) -> int:
        """清空全部用量与游标（用于重建）；返回删除的明细条数。"""
        with self._lock:
            n = int(self.conn.execute("SELECT COUNT(*) FROM usage_records").fetchone()[0])
            self.conn.execute("DELETE FROM usage_records")
            self.conn.execute("DELETE FROM usage_sync_state")
            self.conn.commit()
        return n
