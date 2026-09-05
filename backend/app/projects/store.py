"""projects 表：AI 开发/维护过的项目索引。

与 discovered 一样只跟踪不搬运；额外维护 project_sessions
(agent, session_id → 项目) 映射，供流量统计回填/按项目聚合。

status 三态:
  active    路径存在（或尚未经校验）
  missing   路径已不存在
  excluded  用户手动排除——扫描发现不会将其复活，可恢复
"""
from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path

from app.core.config import Settings
from app.projects.models import ProjectRef

_SCHEMA = """
CREATE TABLE IF NOT EXISTS projects (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    path_key         TEXT NOT NULL UNIQUE,   -- 小写归一路径，跨平台唯一键
    path             TEXT NOT NULL,          -- 展示用原始路径
    name             TEXT NOT NULL,
    agents           TEXT NOT NULL DEFAULT '[]',
    source           TEXT NOT NULL DEFAULT 'log',   -- log / marker / manual
    status           TEXT NOT NULL DEFAULT 'active', -- active / missing / excluded
    sessions         INTEGER NOT NULL DEFAULT 0,
    last_activity_ts INTEGER NOT NULL DEFAULT 0,
    first_seen       TEXT NOT NULL DEFAULT '',
    last_seen        TEXT NOT NULL DEFAULT '',
    note             TEXT NOT NULL DEFAULT '',
    starred          INTEGER NOT NULL DEFAULT 0,
    tags             TEXT NOT NULL DEFAULT '[]',
    extra            TEXT NOT NULL DEFAULT '{}',
    created_at       TEXT NOT NULL DEFAULT '',
    updated_at       TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_projects_status ON projects(status);
CREATE INDEX IF NOT EXISTS idx_projects_activity ON projects(last_activity_ts DESC);

CREATE TABLE IF NOT EXISTS project_sessions (
    agent       TEXT NOT NULL,
    session_id  TEXT NOT NULL,
    project_key TEXT NOT NULL,
    PRIMARY KEY (agent, session_id)
);
CREATE INDEX IF NOT EXISTS idx_psessions_project ON project_sessions(project_key);
"""

_COLS = (
    "id, path_key, path, name, agents, source, status, sessions, "
    "last_activity_ts, first_seen, last_seen, note, starred, tags, extra, "
    "created_at, updated_at"
)


def _now() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def path_key(path: str) -> str:
    """归一化为唯一键：绝对化、去尾分隔符、小写（Windows 路径大小写不敏感）。"""
    return str(Path(path)).rstrip("/\\").lower()


def _row_to_dict(r: sqlite3.Row) -> dict:
    d = dict(r)
    d["agents"] = json.loads(d["agents"] or "[]")
    d["tags"] = json.loads(d["tags"] or "[]")
    d["extra"] = json.loads(d["extra"] or "{}")
    return d


class ProjectStore:
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
    def sync_refs(self, refs: list[ProjectRef]) -> dict:
        """upsert 一批项目线索（按 path 归一），返回统计。

        规则：
        - excluded 的行不被扫描复活（agents/sessions 也不更新）；
        - agents 取并集；source 首次发现为准（manual 一旦登记不被 log/marker 覆盖）；
        - sessions 计数 = project_sessions 中该项目不同会话数。
        """
        now = _now()
        stats = {"projects": 0, "added": 0, "sessions": 0}
        if not refs:
            return stats
        with self._lock:
            c = self.conn

            # 第一遍：按归一键聚合（agents 并集 / ts 取 max / source 定级），
            # 同项目多 Agent 的线索必须合并，不能只认第一条
            agg: dict[str, dict] = {}
            for ref in refs:
                if not ref.path:
                    continue
                key = path_key(ref.path)
                if not key:
                    continue
                a = agg.get(key)
                if a is None:
                    a = agg[key] = {"agents": [], "ts": 0, "source": ref.source, "ref": ref}
                if ref.agent and ref.agent not in a["agents"]:
                    a["agents"].append(ref.agent)
                a["ts"] = max(a["ts"], ref.ts)
                if a["source"] != "manual" and ref.source == "manual":
                    a["source"] = "manual"  # 手动登记优先
                if ref.session_id and ref.agent:
                    cur = c.execute(
                        """
                        INSERT OR IGNORE INTO project_sessions(agent, session_id, project_key)
                        VALUES (?, ?, ?)
                        """,
                        (ref.agent, ref.session_id, key),
                    )
                    if cur.rowcount:
                        stats["sessions"] += 1

            # 第二遍：逐项目 upsert（excluded 不被扫描复活）
            for key, a in agg.items():
                row = c.execute(
                    "SELECT agents, source, status FROM projects WHERE path_key = ?", (key,)
                ).fetchone()
                if row is not None and row["status"] == "excluded":
                    continue
                agents = json.loads(row["agents"] or "[]") if row else []
                for agent in a["agents"]:
                    if agent not in agents:
                        agents.append(agent)
                source = a["source"]
                if row and row["source"] == "manual" and source != "manual":
                    source = "manual"
                display_path = str(Path(a["ref"].path))  # 归一分隔符（Windows 下 / → \）
                if row is None:
                    c.execute(
                        """
                        INSERT INTO projects
                          (path_key, path, name, agents, source, sessions,
                           last_activity_ts, first_seen, last_seen, created_at, updated_at)
                        VALUES (?, ?, ?, ?, ?, 0, ?, ?, ?, ?, ?)
                        """,
                        (
                            key, display_path, Path(a["ref"].path).name or a["ref"].path,
                            json.dumps(agents, ensure_ascii=False), source,
                            a["ts"], now, now, now, now,
                        ),
                    )
                    stats["added"] += 1
                else:
                    c.execute(
                        """
                        UPDATE projects SET
                          agents = ?, source = ?,
                          last_activity_ts = MAX(last_activity_ts, ?),
                          last_seen = ?, updated_at = ?
                        WHERE path_key = ?
                        """,
                        (
                            json.dumps(agents, ensure_ascii=False), source,
                            a["ts"], now, now, key,
                        ),
                    )
                stats["projects"] += 1

            # 刷新受影响项目的会话计数
            for key in agg:
                n = int(
                    c.execute(
                        "SELECT COUNT(*) FROM project_sessions WHERE project_key = ?", (key,)
                    ).fetchone()[0]
                )
                c.execute("UPDATE projects SET sessions = ? WHERE path_key = ?", (n, key))
            c.commit()
        return stats

    def add_manual(self, raw_path: str) -> dict | None:
        """手动登记项目（必须是已存在目录）。已存在则刷新并返回。"""
        p = Path(raw_path).expanduser()
        if not p.is_dir():
            raise FileNotFoundError(f"目录不存在: {raw_path}")
        p = p.resolve()
        # agent 留空：归属 Agent 由后续扫描的日志回溯补全，source 已标记 manual
        ref = ProjectRef(path=str(p), agent="", source="manual", ts=0)
        self.sync_refs([ref])
        return self.get_by_path(str(p))

    def get_by_path(self, raw_path: str) -> dict | None:
        key = path_key(raw_path)
        with self._lock:
            cur = self.conn.execute(f"SELECT {_COLS} FROM projects WHERE path_key = ?", (key,))
            rows = [_row_to_dict(r) for r in cur.fetchall()]
        return rows[0] if rows else None

    # ---- 排除 / 恢复 ----------------------------------------------------
    def exclude(self, record_id: int) -> dict | None:
        return self._set_status(record_id, "excluded")

    def restore(self, record_id: int) -> dict | None:
        return self._set_status(record_id, "active")

    def _set_status(self, record_id: int, status: str) -> dict | None:
        with self._lock:
            self.conn.execute(
                "UPDATE projects SET status = ?, updated_at = ? WHERE id = ?",
                (status, _now(), record_id),
            )
            self.conn.commit()
        return self.get(record_id)

    # ---- 校验 ----------------------------------------------------------
    def validate_all(self) -> dict:
        """按路径是否存在刷新 status；excluded 保持不变。返回统计。"""
        active = missing = excluded = 0
        with self._lock:
            rows = self.conn.execute("SELECT id, path, status FROM projects").fetchall()
            for r in rows:
                if r["status"] == "excluded":
                    excluded += 1
                    continue
                new_status = "active" if Path(r["path"]).is_dir() else "missing"
                if new_status == "active":
                    active += 1
                else:
                    missing += 1
                if new_status != r["status"]:
                    self.conn.execute(
                        "UPDATE projects SET status = ?, updated_at = ? WHERE id = ?",
                        (new_status, _now(), r["id"]),
                    )
            self.conn.commit()
        return {"active": active, "missing": missing, "excluded": excluded}

    # ---- 标注 ----------------------------------------------------------
    def patch(
        self, record_id: int, *, starred: bool | None = None,
        note: str | None = None, tags: list[str] | None = None,
        name: str | None = None,
    ) -> dict | None:
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
        if name is not None:
            sets.append("name = ?")
            params.append(name)
        if not sets:
            return self.get(record_id)
        sets.append("updated_at = ?")
        params.append(_now())
        params.append(record_id)
        with self._lock:
            self.conn.execute(f"UPDATE projects SET {', '.join(sets)} WHERE id = ?", params)
            self.conn.commit()
        return self.get(record_id)

    # ---- 查询 ----------------------------------------------------------
    def get(self, record_id: int) -> dict | None:
        with self._lock:
            cur = self.conn.execute(f"SELECT {_COLS} FROM projects WHERE id = ?", (record_id,))
            rows = [_row_to_dict(r) for r in cur.fetchall()]
        return rows[0] if rows else None

    def list(
        self, *, agent: str | None = None, status: str | None = None,
        q: str | None = None, starred: bool | None = None,
        limit: int = 200, offset: int = 0,
    ) -> list[dict]:
        sql = [f"SELECT {_COLS} FROM projects WHERE 1=1"]
        params: list = []
        # status 缺省：排除 excluded（用户明确要时才显示）
        if status:
            sql.append("AND status = ?")
            params.append(status)
        else:
            sql.append("AND status != 'excluded'")
        if agent:
            sql.append("AND agents LIKE ?")
            params.append(f'%"{agent}"%')
        if starred:
            sql.append("AND starred = 1")
        if q:
            like = f"%{q}%"
            sql.append("AND (name LIKE ? OR path LIKE ? OR note LIKE ?)")
            params += [like, like, like]
        sql.append("ORDER BY starred DESC, last_activity_ts DESC, name ASC LIMIT ? OFFSET ?")
        params += [limit, offset]
        with self._lock:
            cur = self.conn.execute(" ".join(sql), params)
            return [_row_to_dict(r) for r in cur.fetchall()]

    def stats(self) -> dict:
        with self._lock:
            total = int(self.conn.execute("SELECT COUNT(*) FROM projects").fetchone()[0])
            missing = int(self.conn.execute(
                "SELECT COUNT(*) FROM projects WHERE status = 'missing'").fetchone()[0])
            excluded = int(self.conn.execute(
                "SELECT COUNT(*) FROM projects WHERE status = 'excluded'").fetchone()[0])
            starred = int(self.conn.execute(
                "SELECT COUNT(*) FROM projects WHERE starred = 1 AND status != 'excluded'").fetchone()[0])
            sessions = int(self.conn.execute(
                "SELECT COUNT(*) FROM project_sessions").fetchone()[0])
            agents_rows = self.conn.execute(
                "SELECT agents FROM projects WHERE status != 'excluded'").fetchall()
        by_agent: dict[str, int] = {}
        for r in agents_rows:
            for a in json.loads(r["agents"] or "[]"):
                by_agent[a] = by_agent.get(a, 0) + 1
        return {
            "total": total - excluded,
            "missing": missing,
            "excluded": excluded,
            "starred": starred,
            "sessions": sessions,
            "by_agent": dict(sorted(by_agent.items(), key=lambda x: -x[1])),
        }

    # ---- 会话映射（供用量回填）------------------------------------------
    def session_mapping(self) -> list[tuple[str, str, str]]:
        """[(agent, session_id, 项目路径)]——回填 usage_records.project 用。"""
        with self._lock:
            rows = self.conn.execute(
                """
                SELECT ps.agent, ps.session_id, p.path
                FROM project_sessions ps JOIN projects p ON p.path_key = ps.project_key
                """
            ).fetchall()
        return [(r["agent"], r["session_id"], r["path"]) for r in rows]
