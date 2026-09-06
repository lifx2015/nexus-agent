"""技能跨智能体复用：把一个已安装的技能包部署到其他智能体的 skills 目录。

参照 cc-switch 的思路（单一技能包 → 多智能体分发 + 按目标记录安装状态），
但遵循 Nexus 的边界约定：扫描层只索引不搬运，**只有用户显式触发时才复制**。

- 目标目录数据驱动（SKILL_DIR_TEMPLATES），支持 ~ 与 %VAR% 占位，运行时展开
- 覆盖安装 / 卸载前先完整备份到 data_home/skill-share/backups/<技能名>/<时间戳>/
- 安装状态每次实时从文件系统推断（目标目录存在同名技能包即视为已安装），
  不维护第二份状态，避免与真实目录脱节
"""
from __future__ import annotations

import re
import shutil
import threading
from datetime import datetime
from pathlib import Path

from app.core.config import Settings
from app.scanners.agents import SPECS_BY_KEY
from app.scanners.engine import expand_path

# 各智能体的技能目录候选（主目录在前）。agent key 与 scanners.agents.SPECS 对齐。
SKILL_DIR_TEMPLATES: dict[str, list[str]] = {
    "claude-code": ["~/.claude/skills"],
    "codex": ["~/.codex/skills"],
    "gemini-cli": ["~/.gemini/skills"],
    "opencode": ["~/.config/opencode/skills", "~/.opencode/skills"],
    "openclaw": ["~/.openclaw/skills"],
    "hermes": ["~/.hermes/skills", "%LOCALAPPDATA%/hermes/skills"],
    "pi": ["~/.pi/agent/skills"],
    # agentskills 跨工具共享约定目录（一个目录多家智能体读取）
    "agent-skills": ["~/.agents/skills"],
}

BACKUP_KEEP = 3
_TS_RE = re.compile(r"^\d{8}-\d{6}$")
_LOCK = threading.Lock()


class ShareError(RuntimeError):
    """部署/卸载失败（不修改任何文件即抛出）。"""


def _target_name(key: str) -> str:
    spec = SPECS_BY_KEY.get(key)
    return spec.name if spec else key


def backup_root(settings: Settings) -> Path:
    return settings.data_home / "skill-share" / "backups"


def _do_backup(settings: Settings, skill_name: str, skill_dir: Path) -> str:
    """完整复制技能包到备份目录，每技能保留最近 BACKUP_KEEP 份。返回备份路径。"""
    root = backup_root(settings)
    safe = re.sub(r"[^A-Za-z0-9._-]+", "_", skill_name or "skill")
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    dest = root / safe / ts
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(skill_dir, dest, symlinks=True)
    snaps = sorted(
        (p for p in dest.parent.iterdir() if p.is_dir() and _TS_RE.match(p.name)),
        reverse=True,
    )
    for old in snaps[BACKUP_KEEP:]:
        shutil.rmtree(old, ignore_errors=True)
    return str(dest)


# ---- 目标智能体 -------------------------------------------------------------

def list_targets() -> list[dict]:
    """所有可部署目标及其 skills 目录（存在与否均列出，前端置灰可选）。"""
    items = []
    for key, templates in SKILL_DIR_TEMPLATES.items():
        dirs = [str(expand_path(t)) for t in templates]
        items.append({
            "key": key,
            "name": _target_name(key),
            "dirs": dirs,
            "primary": dirs[0],
            "exists": any(expand_path(t).is_dir() for t in templates),
        })
    return items


def _installed_dir(skill_name: str, target_key: str) -> Path | None:
    """在目标的候选目录里找同名技能包（须含 SKILL.md），返回其路径。"""
    for t in SKILL_DIR_TEMPLATES.get(target_key, []):
        cand = expand_path(t) / skill_name
        if (cand / "SKILL.md").is_file():
            return cand
    return None


def _same_path(a: Path, b: Path) -> bool:
    try:
        return a.resolve() == b.resolve()
    except OSError:
        return a == b


# ---- 来源解析 ---------------------------------------------------------------

def resolve_skill_dir(settings: Settings, discovered_store, src: str, rec_id) -> Path:
    """把 (src, id) 解析为本地技能包目录；无效来源抛 ShareError。

    - track: 扫描发现项（复用 skillhub.match.local_skill_dir 的定位规则）
    - own:   自建资产（assets/skills/<name>/SKILL.md，取其所在目录）
    """
    if src == "track":
        rec = discovered_store.get(int(rec_id))
        if rec is None:
            raise ShareError("记录不存在")
        if rec.get("kind") != "skill":
            raise ShareError("仅支持技能（skill）类型记录")
        from app.skillhub.match import local_skill_dir
        d = local_skill_dir(rec)
        if d is None or not d.is_dir():
            raise ShareError(f"本地技能目录不存在: {rec.get('path')}")
        return d
    if src == "own":
        from app.deps import get_service
        from app.domain import AssetKind
        asset = get_service().get(AssetKind.SKILL, str(rec_id))
        if asset is None:
            raise ShareError("资产不存在")
        d = (settings.assets_root / asset.rel_path).parent
        if not (d / "SKILL.md").is_file():
            raise ShareError(f"技能目录缺少 SKILL.md: {d}")
        return d
    raise ShareError(f"未知来源: {src}（可用: track/own）")


def _validate_source(skill_dir: Path) -> str:
    """校验来源技能包并返回目录名（作为部署后的技能名）。"""
    if skill_dir.name.lower() in {"skills", "skill"}:
        raise ShareError("来源为通用 skills 容器目录（单文件布局），无法按包部署")
    if not (skill_dir / "SKILL.md").is_file():
        raise ShareError(f"来源目录缺少 SKILL.md: {skill_dir}")
    return skill_dir.name


# ---- 状态 / 部署 / 卸载 ------------------------------------------------------

def deployment_status(skill_dir: Path) -> list[dict]:
    """该技能在各目标的安装状态（实时探测，不落库）。"""
    name = skill_dir.name
    return [
        {
            "key": t["key"],
            "name": t["name"],
            "dir": t["primary"],
            "exists": t["exists"],
            "installed": _installed_dir(name, t["key"]) is not None,
            "is_source": any(_same_path(expand_path(tpl), skill_dir.parent)
                             for tpl in SKILL_DIR_TEMPLATES.get(t["key"], [])),
        }
        for t in list_targets()
    ]


def deploy(
    settings: Settings,
    skill_dir: Path,
    target_keys: list[str],
    overwrite: bool = False,
) -> list[dict]:
    """复制技能包到各目标智能体的 skills 目录，返回逐目标结果。"""
    name = _validate_source(skill_dir)
    results = []
    with _LOCK:
        for key in target_keys:
            templates = SKILL_DIR_TEMPLATES.get(key)
            if templates is None:
                results.append({"key": key, "ok": False, "error": "不支持的目标"})
                continue
            root = expand_path(templates[0])
            dest = root / name
            if any(_same_path(expand_path(t), skill_dir.parent) for t in templates):
                results.append({"key": key, "ok": True, "skipped": True,
                                "dir": str(dest), "detail": "来源即该目标，跳过"})
                continue
            try:
                backup_dir = ""
                if dest.exists():
                    if not overwrite:
                        results.append({"key": key, "ok": False, "dir": str(dest),
                                        "error": "目标已存在同名技能（可勾选覆盖安装）"})
                        continue
                    backup_dir = _do_backup(settings, name, dest)
                    shutil.rmtree(dest)
                root.mkdir(parents=True, exist_ok=True)
                shutil.copytree(skill_dir, dest, symlinks=True)
                results.append({"key": key, "ok": True, "dir": str(dest),
                                "backup_dir": backup_dir})
            except Exception as e:  # noqa: BLE001
                results.append({"key": key, "ok": False, "dir": str(dest), "error": str(e)})
    return results


def undeploy(settings: Settings, skill_dir: Path, target_key: str) -> dict:
    """从目标智能体卸载技能包（备份后删除）。返回 {ok, dir, backup_dir}。"""
    name = _validate_source(skill_dir)
    installed = _installed_dir(name, target_key)
    if installed is None:
        raise ShareError(f"{_target_name(target_key)} 未安装该技能")
    with _LOCK:
        backup_dir = _do_backup(settings, name, installed)
        shutil.rmtree(installed)
    return {"key": target_key, "ok": True, "dir": str(installed), "backup_dir": backup_dir}
