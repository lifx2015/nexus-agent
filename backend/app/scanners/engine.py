"""扫描引擎：按 AgentSpec 在本机发现资产。

严格遵守约束——只读取文件元信息与少量摘要，**不复制、不移动、不修改任何被扫描文件**。

「加工」三件事：
  1. 噪声过滤：lock/log/backup/cache/隐藏文件等运行时垃圾不入库
  2. 价值分级：skill/rule/tool/memory 优先，config 次之，session/other 兜底
  3. 分类配额：会话、杂项按每 Agent 限量，避免海量日志淹没真正的资产
"""
from __future__ import annotations

import os
import re
import time
from pathlib import Path

import yaml

from app.scanners.agents import SPECS, SPECS_BY_KEY
from app.scanners.models import AgentSpec, DiscoveredItem, ItemKind

MAX_ITEMS_PER_AGENT = 800
MAX_SUMMARY_SIZE = 2 * 1024 * 1024
SUMMARY_CHARS = 240
DEFAULT_PROJECT_SCAN_DEPTH = 4

TEXT_SUFFIXES = {
    ".md", ".mdc", ".txt", ".json", ".jsonl", ".yaml", ".yml",
    ".toml", ".ini", ".cfg", ".py", ".js", ".ts", ".sh", ".ps1",
}

_FRONT_MATTER_RE = re.compile(r"\A---[ \t]*\r?\n.*?\r?\n---[ \t]*\r?\n?", re.DOTALL)

# ---- 噪声规则 ----------------------------------------------------------
NOISE_DIRS = {
    "cache", "backups", "backup", "logs", "log", "tmp", "temp", "telemetry",
    "node_modules", "shell-snapshots", "session-env", "file-history", "statsig",
    ".git", ".svn", ".venv", "venv", "__pycache__", "dist", "build", ".next",
    "plugins-cache", "extensions-cache", "gpu-cache", "crash-reports",
    # Electron/Chromium 运行时缓存（如 workbuddy/VSCode 系的 app/session 目录）
    "gpucache", "code cache", "local storage", "session storage", "cookies",
    "indexeddb", "blob_storage", "shared dictionary", "webrtc event logs",
    "crashpad", "sentry", "dawngraphite", "dawnwebgpu", "script cache",
}
NOISE_SUFFIXES = {
    ".lock", ".log", ".key", ".pid", ".tmp", ".bak", ".backup", ".swp",
    ".pyc", ".class", ".sock", ".old",
    # 数据库/二进制运行时文件，无跟踪管理价值
    ".db", ".db-wal", ".db-shm", ".sqlite", ".sqlite3", ".sqlite-wal",
    ".bin", ".dll", ".exe", ".pak", ".dat", ".node", ".wasm",
    ".woff", ".woff2", ".ttf", ".otf", ".eot", ".ico", ".icns", ".png", ".jpg",
}
NOISE_NAMES = {".ds_store", "thumbs.db", "daemon.lock", "pipe.key", "desktop.ini"}
# 以点开头但**有管理价值**的规则文件名
KEEP_DOT_NAMES = {
    ".cursorrules", ".windsurfrules", ".clinerules", ".roorules", ".continuerules",
}

# 价值分级（越大越优先）
_PRIORITY: dict[ItemKind, int] = {
    ItemKind.SKILL: 5,
    ItemKind.RULE: 5,
    ItemKind.TOOL: 5,
    ItemKind.MEMORY: 4,
    ItemKind.CONFIG: 2,
    ItemKind.SESSION: 1,
    ItemKind.OTHER: 0,
}
# 每 Agent 分类配额（None 表示不限，仅受 MAX_ITEMS_PER_AGENT 约束）
_QUOTA: dict[ItemKind, int | None] = {
    ItemKind.SESSION: 20,
    ItemKind.OTHER: 40,
    ItemKind.CONFIG: 60,
}


def expand_path(raw: str) -> Path:
    """展开 ~ 与 %APPDATA% 等环境变量。"""
    return Path(os.path.expanduser(os.path.expandvars(raw)))


def is_noise(path: Path) -> bool:
    """是否为运行时噪声（不值得跟踪管理的文件）。"""
    name = path.name.lower()
    if name in KEEP_DOT_NAMES:  # 有价值的点开头规则文件
        return False
    if name in NOISE_NAMES:
        return True
    if path.suffix.lower() in NOISE_SUFFIXES:
        return True
    if name.startswith(".") and name not in KEEP_DOT_NAMES:
        return True
    parts = {p.lower() for p in path.parts}
    return bool(parts & NOISE_DIRS)


def classify(path: Path) -> ItemKind:
    """根据路径特征判定资产类型。"""
    name = path.name.lower()
    parts = {p.lower() for p in path.parts}
    suffix = path.suffix.lower()

    if name in {"mcp.json", "tools.json"} or "tools" in parts:
        return ItemKind.TOOL
    if "skills" in parts or name == "skill.md":
        return ItemKind.SKILL
    if "memory" in parts or "memories" in parts or name in {"memory.md", "memories.md"}:
        return ItemKind.MEMORY
    if name in {
        "claude.md", "agents.md", "gemini.md", "codex.md", "opencode.md",
        "soul.md", "copilot-instructions.md", "ai-rules.md",
    } or name.endswith((".cursorrules", ".windsurfrules", ".clinerules", ".roorules")):
        return ItemKind.RULE
    if suffix == ".mdc" or {"rules", "rule", "steering", "instructions", "prompts"} & parts:
        return ItemKind.RULE
    if "sessions" in parts or suffix == ".jsonl":
        return ItemKind.SESSION
    if suffix in {".json", ".toml", ".yaml", ".yml", ".ini", ".cfg"}:
        return ItemKind.CONFIG
    return ItemKind.OTHER


def read_frontmatter(path: Path, max_bytes: int = 4096) -> dict:
    """读取 Markdown 文件的 YAML frontmatter（用于取 Skill 的真实名称/描述）。"""
    try:
        with path.open("r", encoding="utf-8", errors="ignore") as f:
            head = f.read(max_bytes)
    except OSError:
        return {}
    m = re.match(r"\A---[ \t]*\r?\n(.*?)\r?\n---", head, re.DOTALL)
    if not m:
        return {}
    try:
        data = yaml.safe_load(m.group(1))
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def read_summary(path: Path, size: int) -> str:
    """读取文本文件开头作为摘要（超长或非文本则跳过）。"""
    if size > MAX_SUMMARY_SIZE or path.suffix.lower() not in TEXT_SUFFIXES:
        return ""
    try:
        with path.open("r", encoding="utf-8", errors="ignore") as f:
            head = f.read(SUMMARY_CHARS * 4)
    except OSError:
        return ""
    return re.sub(r"\s+", " ", head).strip()[:SUMMARY_CHARS]


def _walk(root: Path, excludes: set[str], max_depth: int | None = None):
    """受控目录遍历：可限制深度并跳过噪声目录。"""
    base_depth = len(root.parts)
    for dirpath, dirnames, filenames in os.walk(root):
        cur = Path(dirpath)
        if max_depth is not None and len(cur.parts) - base_depth >= max_depth:
            dirnames[:] = []
        dirnames[:] = [d for d in dirnames if d.lower() not in excludes]
        yield cur, dirnames, filenames


class ScanEngine:
    def __init__(self, specs: list[AgentSpec] | None = None):
        self.specs = specs or SPECS

    # ---- 检测 ----------------------------------------------------------
    def agent_status(self) -> list[dict]:
        """各 Agent 是否已在本机留下数据（仅看全局目录，速度快）。"""
        return [
            {
                "key": spec.key,
                "name": spec.name,
                "vendor": spec.vendor,
                "detected": any(expand_path(d).exists() for d in spec.global_dirs),
                "roots": [str(expand_path(d)) for d in spec.global_dirs if expand_path(d).exists()],
            }
            for spec in self.specs
        ]

    # ---- 收集 ----------------------------------------------------------
    def _make_item(self, spec: AgentSpec, fpath: Path) -> DiscoveredItem | None:
        if is_noise(fpath):
            return None
        try:
            st = fpath.stat()
        except OSError:
            return None
        if st.st_size == 0:  # 空占位文件无管理价值
            return None
        return DiscoveredItem(
            agent=spec.key,
            kind=classify(fpath),
            name=fpath.stem or fpath.name,
            path=fpath,
            size=st.st_size,
            mtime=st.st_mtime,
            summary=read_summary(fpath, st.st_size),
        )

    def _collect_dir(self, spec: AgentSpec, directory: Path, found: dict[str, DiscoveredItem]) -> None:
        excludes = set(spec.exclude) | NOISE_DIRS
        for cur, _dirs, files in _walk(directory, excludes):
            if len(found) >= MAX_ITEMS_PER_AGENT:
                return
            for fname in files:
                if len(found) >= MAX_ITEMS_PER_AGENT:
                    return
                key = str(cur / fname).lower()
                if key in found:
                    continue
                item = self._make_item(spec, cur / fname)
                if item is not None:
                    found[key] = item

    def _collect_project(
        self, spec: AgentSpec, root: Path, found: dict[str, DiscoveredItem], depth: int
    ) -> None:
        """在项目根下寻找 project_dirs / project_files 标记。"""
        want_dirs = {d.strip("/").split("/")[-1].lower() for d in spec.project_dirs}
        want_files = {f.strip("/").split("/")[-1].lower() for f in spec.project_files}
        if not want_dirs and not want_files:
            return
        excludes = set(spec.exclude) | NOISE_DIRS

        for cur, dirnames, files in _walk(root, excludes, max_depth=depth):
            if len(found) >= MAX_ITEMS_PER_AGENT:
                return
            hits = [d for d in dirnames if d.lower() in want_dirs]
            for d in hits:
                self._collect_dir(spec, cur / d, found)
            dirnames[:] = [d for d in dirnames if d.lower() not in want_dirs]

            for fname in files:
                if fname.lower() not in want_files:
                    continue
                fpath = cur / fname
                key = str(fpath).lower()
                if key in found or is_noise(fpath):
                    continue
                item = self._make_item(spec, fpath)
                if item is not None:
                    found[key] = item

    # ---- 加工：Skill 聚合 ---------------------------------------------
    @staticmethod
    def aggregate_skills(items: list[DiscoveredItem]) -> list[DiscoveredItem]:
        """一个 Skill（目录）聚合为一条记录，内部脚本/参考文件记为附属件数。

        skill-creator/scripts/*.py 这类文件不应各自成项——用户要管理的是 Skill 本身。
        """
        # 第一轮：SKILL.md 本身即一个 Skill 包（名称/描述优先取 frontmatter）
        main_by_dir: dict[str, DiscoveredItem] = {}
        rest: list[DiscoveredItem] = []
        for it in items:
            if it.kind is ItemKind.SKILL and it.path.name.lower() == "skill.md":
                fm = read_frontmatter(it.path)
                it.name = str(fm.get("name") or ScanEngine._fallback_skill_name(it.path))
                # 概要：优先 frontmatter description；否则剥离原文 frontmatter 后取正文开头
                desc = str(fm.get("description") or "").strip()
                if desc:
                    it.summary = re.sub(r"\s+", " ", desc)[:SUMMARY_CHARS]
                elif it.summary.startswith("---"):
                    it.summary = re.sub(r"\s+", " ", _FRONT_MATTER_RE.sub("", it.summary, count=1)).strip()[:SUMMARY_CHARS]
                it.extra["description"] = desc[:200]
                it.extra["dir"] = str(it.path.parent)
                it.extra["files"] = 1
                main_by_dir[str(it.path.parent).lower()] = it
            else:
                rest.append(it)

        # 第二轮：Skill 包内其它文件（scripts/、references/…）沿路径向上并入所属包
        out: list[DiscoveredItem] = []
        for it in rest:
            if it.kind is ItemKind.SKILL:
                owner = next(
                    (main_by_dir[str(p).lower()] for p in it.path.parents if str(p).lower() in main_by_dir),
                    None,
                )
                if owner is not None:
                    owner.extra["files"] = int(owner.extra.get("files", 1)) + 1
                    continue
            out.append(it)
        return out + list(main_by_dir.values())

    @staticmethod
    def _fallback_skill_name(path: Path) -> str:
        parent = path.parent.name
        if parent.lower() == "skills":
            return path.parent.parent.name or parent
        return parent

    @staticmethod
    def _skill_dir(path: Path) -> tuple[str, Path] | None:
        """定位 Skill 目录与其名称，兼容两种布局：
        skills/<name>/SKILL.md  → 名称为 <name>
        skills/SKILL.md         → 名称取 skills 的父目录名
        """
        parts = path.parts
        idxs = [i for i, seg in enumerate(parts) if seg.lower() == "skills"]
        if not idxs:
            return None
        i = idxs[-1]
        if i + 1 <= len(parts) - 2:  # skills/<name>/...
            return parts[i + 1], Path(*parts[: i + 2])
        if i + 1 == len(parts) - 1:  # skills/<file>
            name = parts[i - 1] if i > 0 else parts[i + 1]
            return name, Path(*parts[: i + 1])
        return None

    # ---- 加工：价值分级 + 配额截断 -------------------------------------
    @staticmethod
    def finalize(found: dict[str, DiscoveredItem]) -> list[DiscoveredItem]:
        items = sorted(
            found.values(), key=lambda x: (-_PRIORITY[x.kind], -x.mtime)
        )
        used: dict[ItemKind, int] = {}
        out: list[DiscoveredItem] = []
        for it in items:
            cap = _QUOTA.get(it.kind)
            if cap is not None and used.get(it.kind, 0) >= cap:
                continue
            used[it.kind] = used.get(it.kind, 0) + 1
            out.append(it)
        return out

    # ---- 扫描 ----------------------------------------------------------
    def scan(
        self,
        agent_keys: list[str] | None = None,
        project_roots: list[str] | None = None,
        depth: int = DEFAULT_PROJECT_SCAN_DEPTH,
        on_progress=None,
    ) -> dict:
        """执行扫描，返回发现项与统计（不改动任何被扫描文件）。"""
        started = time.time()
        specs = (
            self.specs
            if not agent_keys
            else [SPECS_BY_KEY[k] for k in agent_keys if k in SPECS_BY_KEY]
        )
        roots = [Path(r) for r in (project_roots or []) if Path(r).exists()]

        by_agent: dict[str, list[DiscoveredItem]] = {}
        for spec in specs:
            found: dict[str, DiscoveredItem] = {}
            for d in spec.global_dirs:
                p = expand_path(d)
                if p.exists():
                    self._collect_dir(spec, p, found)
            for root in roots:
                self._collect_project(spec, root, found, depth)
            by_agent[spec.key] = self.aggregate_skills(self.finalize(found))
            if on_progress:
                on_progress(spec.key, len(by_agent[spec.key]))

        # 具体 Agent 优先，generic 仅补漏（按路径去重）
        items: list[DiscoveredItem] = []
        seen: set[str] = set()
        ordered_keys = [s.key for s in specs if s.key != "generic"]
        if any(s.key == "generic" for s in specs):
            ordered_keys.append("generic")
        for key in ordered_keys:
            for it in by_agent.get(key, []):
                k = str(it.path).lower()
                if k in seen:
                    continue
                seen.add(k)
                items.append(it)

        counts_by_agent: dict[str, int] = {}
        counts_by_kind: dict[str, int] = {}
        for it in items:
            counts_by_agent[it.agent] = counts_by_agent.get(it.agent, 0) + 1
            counts_by_kind[it.kind.value] = counts_by_kind.get(it.kind.value, 0) + 1

        return {
            "items": items,
            "counts_by_agent": counts_by_agent,
            "counts_by_kind": counts_by_kind,
            "scanned_roots": [str(r) for r in roots],
            "agents": [s.key for s in specs],
            "total": len(items),
            "elapsed": round(time.time() - started, 2),
        }
