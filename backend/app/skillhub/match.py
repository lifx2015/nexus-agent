"""本地技能 ↔ SkillHub 关联与版本对比。

匹配策略：
  1. 收集本机全部「技能」发现项（kind=skill, status=active）
  2. 候选标识 = 归一化(frontmatter 名称) + 归一化(目录名) + 归一化(去版本后缀的目录名)
  3. 一次性批量查询 SkillHub（/api/v1/skills/batch），在内存中按 slug / 名称建索引
  4. 版本对比：解析语义化版本（允许 1.0 / 2.23.2 / v2.33.0 / 1.0.0-beta.1）

只读：本模块不修改任何本地文件（更新动作在 updater 中，需显式触发）。
"""
from __future__ import annotations

import re
import time
from pathlib import Path

from app.scanners.engine import read_frontmatter
from app.skillhub.client import SkillHubClient

# 状态
UP_AVAILABLE = "update-available"  # 本地 < 仓库
UP_TO_DATE = "up-to-date"          # 相同
AHEAD = "ahead"                    # 本地 > 仓库
UNKNOWN_LOCAL = "unknown-local"    # 已关联但本地无版本号
NOT_FOUND = "not-found"            # 仓库未收录

_VERSION_RE = re.compile(r"^(\d+(?:\.\d+)*)([-+].*)?$")
_VER_SUFFIX_RE = re.compile(
    r"[-_]?v?\d+(?:\.\d+)*(?:[-.]?(?:alpha|beta|rc|pre|dev|nightly|stable|final|release))?\d*$",
    re.IGNORECASE,
)


def normalize_slug(s: str) -> str:
    """归一化技能标识：小写、去尾部版本号、非字母数字折叠为 '-'。

    'Memory 1.0.2' → 'memory'；'aminer-open-academic-1.0.5' → 'aminer-open-academic'
    """
    s = (s or "").strip().lower()
    s = _VER_SUFFIX_RE.sub("", s)
    s = re.sub(r"[^a-z0-9]+", "-", s)
    return s.strip("-")


def parse_version(v: str) -> tuple[tuple[int, ...], str] | None:
    """解析版本号 → (核心数字元组, 预发布后缀)。无法解析返回 None。"""
    s = (v or "").strip().lstrip("vV").strip()
    m = _VERSION_RE.match(s)
    if not m:
        return None
    core = tuple(int(x) for x in m.group(1).split("."))
    pre = (m.group(2) or "").lstrip("-+").lower()
    return core, pre


def compare_versions(a: str, b: str) -> int | None:
    """a vs b：-1 落后 / 0 相同 / 1 领先 / None 无法比较。"""
    pa, pb = parse_version(a), parse_version(b)
    if pa is None or pb is None:
        return None
    ca, prea = pa
    cb, preb = pb
    n = max(len(ca), len(cb))
    ca, cb = ca + (0,) * (n - len(ca)), cb + (0,) * (n - len(cb))
    if ca != cb:
        return -1 if ca < cb else 1
    if prea == preb:
        return 0
    if not prea:
        return 1  # 正式版 > 预发布版
    if not preb:
        return -1
    return -1 if prea < preb else 1


# ---- 本地技能读取 --------------------------------------------------------
def local_skill_dir(rec: dict) -> Path | None:
    """由发现项定位技能包目录（聚合记录的 extra.dir 优先）。"""
    extra = rec.get("extra") or {}
    if extra.get("dir"):
        p = Path(extra["dir"])
        if p.is_dir():
            return p
    path = Path(rec.get("path") or "")
    if not path.exists():
        return None
    return path.parent if path.is_file() else path


def read_local_version(skill_dir: Path) -> str | None:
    """读取技能包 SKILL.md frontmatter 中的 version。"""
    skill_md = skill_dir / "SKILL.md"
    if not skill_md.is_file():
        return None
    fm = read_frontmatter(skill_md)
    v = fm.get("version")
    if v is None or str(v).strip() == "":
        return None
    return str(v).strip()


# ---- 对比 ----------------------------------------------------------------
def _hub_url(slug: str, namespace: str = "") -> str:
    if namespace:
        return f"https://skillhub.cn/#/skills/{namespace}/{slug}"
    return f"https://skillhub.cn/#/skills/{slug}"


def _item_from_batch(b: dict) -> dict:
    """batch 响应单条 → 精简信息。"""
    skill = b.get("skill") or {}
    ns = b.get("namespace") or {}
    latest = b.get("latestVersion") or {}
    stats = skill.get("stats") or {}
    return {
        "slug": skill.get("slug") or "",
        "namespace": ns.get("handle") or "",
        "hub_name": skill.get("displayName") or skill.get("name") or "",
        "hub_version": latest.get("version") or skill.get("version") or "",
        "changelog": latest.get("changelog") or "",
        "downloads": int(stats.get("downloads") or 0),
        "publisher": (b.get("publisher") or {}).get("name") or (b.get("owner") or {}).get("displayName") or "",
        "verified": bool((b.get("publisher") or {}).get("verified")),
        "category": skill.get("category") or "",
        "icon_url": skill.get("iconUrl") or "",
        "summary": skill.get("summary_zh") or skill.get("summary") or "",
    }


def compare_local_skills(
    store,
    client: SkillHubClient,
    only_ids: list[int] | None = None,
    limit: int = 500,
) -> dict:
    """对比本机技能与 SkillHub 仓库。

    返回 {"items": [...], "summary": {...}, "elapsed": float}。
    """
    started = time.time()
    recs = store.list(kind="skill", status="active", limit=limit)
    if only_ids:
        wanted = set(only_ids)
        recs = [r for r in recs if r["id"] in wanted]

    # 1) 本地信息 + 候选 slug
    locals: list[dict] = []
    candidates: set[str] = set()
    for rec in recs:
        sdir = local_skill_dir(rec)
        local_ver = read_local_version(sdir) if sdir else None
        dir_name = sdir.name if sdir else Path(rec["path"]).parent.name
        cands = {normalize_slug(rec.get("name") or ""), normalize_slug(dir_name or "")}
        candidates.update(c for c in cands if c)
        locals.append(
            {"rec": rec, "skill_dir": sdir, "local_version": local_ver, "dir_name": dir_name}
        )

    # 2) 批量查询仓库
    hub_by_slug: dict[str, dict] = {}
    hub_by_name: dict[str, dict] = {}
    hub_items = client.batch(sorted(candidates)) if candidates else []
    for b in hub_items:
        info = _item_from_batch(b)
        if not info["slug"]:
            continue
        # 同一 slug 被多个命名空间占用时，保留下载量更高者
        cur = hub_by_slug.get(info["slug"])
        if cur is None or info["downloads"] > cur["downloads"]:
            hub_by_slug[info["slug"]] = info
        for key in {normalize_slug(info["hub_name"]), info["slug"]}:
            if key:
                hub_by_name.setdefault(key, info)

    # 3) 逐条判定
    items: list[dict] = []
    summary = {
        "total": len(locals),
        "matched": 0,
        "updatable": 0,
        "up_to_date": 0,
        "ahead": 0,
        "unknown_local": 0,
        "not_found": 0,
    }
    for entry in locals:
        rec = entry["rec"]
        base = {
            "record_id": rec["id"],
            "name": rec.get("name") or entry["dir_name"],
            "agent": rec.get("agent") or "",
            "path": rec.get("path") or "",
            "skill_dir": str(entry["skill_dir"]) if entry["skill_dir"] else "",
            "local_version": entry["local_version"],
            "state": NOT_FOUND,
            "slug": "",
            "namespace": "",
            "hub_name": "",
            "hub_version": "",
            "changelog": "",
            "downloads": 0,
            "publisher": "",
            "verified": False,
            "hub_url": "",
        }
        cands = [normalize_slug(rec.get("name") or ""), normalize_slug(entry["dir_name"] or "")]
        info = next(
            (hub_by_slug.get(c) or hub_by_name.get(c) for c in cands if c and (hub_by_slug.get(c) or hub_by_name.get(c))),
            None,
        )
        if info is None:
            items.append(base)
            summary["not_found"] += 1
            continue

        base.update(
            slug=info["slug"],
            namespace=info["namespace"],
            hub_name=info["hub_name"],
            hub_version=info["hub_version"],
            changelog=info["changelog"][:500],
            downloads=info["downloads"],
            publisher=info["publisher"],
            verified=info["verified"],
            hub_url=_hub_url(info["slug"], info["namespace"]),
        )
        summary["matched"] += 1
        if not entry["local_version"]:
            base["state"] = UNKNOWN_LOCAL
            summary["unknown_local"] += 1
        else:
            cmp = compare_versions(entry["local_version"], info["hub_version"])
            if cmp is None:
                base["state"] = UNKNOWN_LOCAL
                summary["unknown_local"] += 1
            elif cmp < 0:
                base["state"] = UP_AVAILABLE
                summary["updatable"] += 1
            elif cmp == 0:
                base["state"] = UP_TO_DATE
                summary["up_to_date"] += 1
            else:
                base["state"] = AHEAD
                summary["ahead"] += 1
        items.append(base)

    return {
        "items": items,
        "summary": summary,
        "elapsed": round(time.time() - started, 2),
    }
