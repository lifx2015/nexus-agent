"""项目发现：从本机会话日志回溯各 Agent 实际工作过的项目路径。

只读、不搬运；每个会话文件只读首行（或仅看目录结构 / 查索引库），
不做全文扫描，成本可控，可随每次「扫描本机」执行。

数据驱动：新增 Agent 只需加一个 BaseProjectExtractor 子类并 @register。
日志行内带显式 cwd 时以其为准；否则回退到「转义目录名」解码
（Claude/Qoder/WorkBuddy 用「路径非字母数字全替换为 -」命名目录，
解码有损——仅兜底，不作权威）。
"""
from __future__ import annotations

import json
import os
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from app.scanners.engine import NOISE_DIRS, _walk
from app.scanners.agents import SPECS
from app.usage.common import expand_path, parse_ts
from app.projects.models import ProjectRef
from app.projects.paths import decode_project_dir

_FIRST_LINE_MAX = 256 * 1024
_FIRST_LINE_SCAN = 8  # 首行未命中时最多再往后看几行（Codex meta 可能不在首行）


def _first_line(path: Path, max_scan: int = _FIRST_LINE_SCAN) -> dict | None:
    """读取 JSONL 开头若干行，返回第一条可解析的 dict 行。"""
    try:
        with path.open("r", encoding="utf-8", errors="ignore") as f:
            for i in range(max_scan):
                line = f.readline(_FIRST_LINE_MAX)
                if not line:
                    break
                line = line.strip()
                if not line:
                    continue
                try:
                    value = json.loads(line)
                except ValueError:
                    continue
                if isinstance(value, dict):
                    return value
    except OSError:
        return None
    return None


@dataclass
class SessionRef:
    """提取器输出：一条「会话 → 项目」线索。"""

    project: str
    session_id: str | None = None
    ts: int = 0


class BaseProjectExtractor:
    agent: str = ""

    def extract(self) -> list[SessionRef]:  # pragma: no cover - 接口
        raise NotImplementedError

    def _existing_roots(self, candidates: list[str]) -> list[Path]:
        return [p for p in (expand_path(c) for c in candidates) if p.is_dir()]


EXTRACTORS: list[BaseProjectExtractor] = []


def register(cls: type[BaseProjectExtractor]) -> type[BaseProjectExtractor]:
    EXTRACTORS.append(cls())
    return cls


# ---- Claude Code ---------------------------------------------------------
# ~/.claude/projects/<转义路径>/<会话uuid>.jsonl，行内带 cwd / sessionId / timestamp
@register
class ClaudeCodeProjects(BaseProjectExtractor):
    agent = "claude-code"

    def extract(self) -> list[SessionRef]:
        env = os.environ.get("CLAUDE_CONFIG_DIR")
        base = [Path(env).expanduser() if env else Path.home() / ".claude"]
        out: list[SessionRef] = []
        for root in (b / "projects" for b in base):
            if not root.is_dir():
                continue
            for d in root.iterdir():
                if not d.is_dir():
                    continue
                fallback = decode_project_dir(d.name)
                for f in d.glob("*.jsonl"):
                    if not f.is_file():
                        continue
                    line = _first_line(f)
                    cwd = line.get("cwd") if line else None
                    project = cwd if isinstance(cwd, str) and cwd else fallback
                    sid = line.get("sessionId") if line else None
                    ts = parse_ts(line.get("timestamp")) if line else None
                    if isinstance(project, str) and project:
                        out.append(SessionRef(
                            project=project,
                            session_id=sid if isinstance(sid, str) else None,
                            ts=ts or int(f.stat().st_mtime),
                        ))
        return out


# ---- Codex CLI -----------------------------------------------------------
# ~/.codex/sessions/YYYY/MM/DD/rollout-*.jsonl，首行 session_meta.payload 带 cwd / session_id
@register
class CodexProjects(BaseProjectExtractor):
    agent = "codex"

    def extract(self) -> list[SessionRef]:
        env = os.environ.get("CODEX_HOME")
        base = [Path(env).expanduser() if env else Path.home() / ".codex"]
        out: list[SessionRef] = []
        for root in (b / "sessions" for b in base):
            if not root.is_dir():
                continue
            for f in root.rglob("*.jsonl"):
                if not f.is_file():
                    continue
                line = _first_line(f)
                payload = line.get("payload") if line else None
                if not isinstance(payload, dict):
                    continue
                cwd = payload.get("cwd")
                if not isinstance(cwd, str) or not cwd:
                    continue
                sid = payload.get("session_id") or payload.get("id")
                ts = parse_ts(line.get("timestamp")) if line else None
                out.append(SessionRef(
                    project=cwd,
                    session_id=sid if isinstance(sid, str) else None,
                    ts=ts or int(f.stat().st_mtime),
                ))
        return out


# ---- OpenCode ------------------------------------------------------------
# opencode.db（SQLite）：session 表直接带 directory 列，无需读文件
@register
class OpenCodeProjects(BaseProjectExtractor):
    agent = "opencode"

    def extract(self) -> list[SessionRef]:
        from app.usage.adapters.opencode import _resolve_db_path

        db_path = _resolve_db_path()
        if db_path is None:
            return []
        out: list[SessionRef] = []
        try:
            conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
            try:
                rows = conn.execute(
                    "SELECT id, directory, time_updated FROM session "
                    "WHERE directory IS NOT NULL AND directory != ''"
                ).fetchall()
            finally:
                conn.close()
        except sqlite3.Error:
            return []
        for sid, directory, time_updated in rows:
            ts = int(time_updated or 0)
            if ts > 100_000_000_000:  # opencode 用毫秒
                ts //= 1000
            out.append(SessionRef(project=str(Path(directory)), session_id=sid, ts=ts))
        return out


# ---- WorkBuddy -----------------------------------------------------------
# ~/.workbuddy/projects/<转义工作目录>/<会话uuid>.jsonl，行内带 cwd（= 实际工作目录）
@register
class WorkBuddyProjects(BaseProjectExtractor):
    agent = "workbuddy"

    def extract(self) -> list[SessionRef]:
        env = os.environ.get("WORKBUDDY_HOME")
        base = [Path(env).expanduser() if env else Path.home() / ".workbuddy"]
        out: list[SessionRef] = []
        for root in (b / "projects" for b in base):
            if not root.is_dir():
                continue
            for d in root.iterdir():
                if not d.is_dir():
                    continue
                fallback = decode_project_dir(d.name)
                for f in d.glob("*.jsonl"):
                    if not f.is_file():
                        continue
                    line = _first_line(f)
                    cwd = line.get("cwd") if line else None
                    project = cwd if isinstance(cwd, str) and cwd else fallback
                    sid = line.get("sessionId") if line else None
                    if not isinstance(sid, str) or not sid:
                        sid = f.stem  # 文件名即会话 uuid
                    if isinstance(project, str) and project:
                        out.append(SessionRef(
                            project=project,
                            session_id=sid,
                            ts=int(f.stat().st_mtime),
                        ))
        return out


# ---- Qoder ---------------------------------------------------------------
# ~/.qoder/projects/<转义路径>/：本机未见行级 cwd，仅按目录名解码
@register
class QoderProjects(BaseProjectExtractor):
    agent = "qoder"

    def extract(self) -> list[SessionRef]:
        out: list[SessionRef] = []
        root = Path.home() / ".qoder" / "projects"
        if not root.is_dir():
            return out
        for d in root.iterdir():
            if not d.is_dir():
                continue
            project = decode_project_dir(d.name)
            if project:
                out.append(SessionRef(project=project, ts=int(d.stat().st_mtime)))
        return out


# ---- 标记文件扫描（用户指定项目根时）---------------------------------------
# 文件名 → 归属 Agent：只被一家具体 Agent 认领的归它（CLAUDE.md → claude-code），
# 多家共用的（AGENTS.md）归 generic
_SPEC_FILE_OWNER: dict[str, str] = {}
for _spec in SPECS:
    if _spec.key == "generic":
        continue
    for _f in _spec.project_files:
        _name = _f.strip("/").split("/")[-1].lower()
        if _name:
            _SPEC_FILE_OWNER.setdefault(_name, _spec.key)

def _marker_agent(fname: str) -> str:
    return _SPEC_FILE_OWNER.get(fname.lower(), "generic")


def discover_markers(roots: list[str], depth: int = 4) -> list[ProjectRef]:
    """在用户指定的项目根下，按 AI 标记文件反查项目目录。

    项目目录 = 直接包含标记文件的那个目录（<root>/foo/CLAUDE.md → <root>/foo）。
    """
    want_files: set[str] = set()
    for spec in SPECS:
        for f in spec.project_files:
            name = f.strip("/").split("/")[-1].lower()
            if name:
                want_files.add(name)
    if not want_files:
        return []

    out: list[ProjectRef] = []
    seen: set[str] = set()
    for raw in roots:
        root = expand_path(raw)
        if not root.is_dir():
            continue
        for cur, dirnames, files in _walk(root, NOISE_DIRS, max_depth=depth):
            for fname in files:
                if fname.lower() not in want_files:
                    continue
                fpath = cur / fname
                key = str(fpath).lower()
                if key in seen:
                    continue
                seen.add(key)
                try:
                    ts = int(fpath.stat().st_mtime)
                except OSError:
                    ts = 0
                out.append(ProjectRef(
                    path=str(cur),
                    agent=_marker_agent(fname),
                    source="marker",
                    ts=ts,
                ))
    return out


def discover_projects(project_roots: list[str] | None = None) -> list[ProjectRef]:
    """汇总所有提取器 + 标记文件扫描；单提取器故障不影响整体。"""
    refs: list[ProjectRef] = []
    for ext in EXTRACTORS:
        try:
            for sr in ext.extract():
                refs.append(ProjectRef(
                    path=sr.project,
                    agent=ext.agent,
                    source="log",
                    session_id=sr.session_id,
                    ts=sr.ts,
                ))
        except Exception:  # noqa: BLE001 - 单个提取器故障不拖垮扫描
            continue
    if project_roots:
        refs.extend(discover_markers(project_roots))
    return refs
