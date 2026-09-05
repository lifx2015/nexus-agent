"""项目管理 API：AI 开发/维护过的项目跟踪（只跟踪不搬运）。

项目由「扫描本机」时从各 Agent 会话日志回溯发现（也可手动登记），
每行附带该项目已归因的 token 用量（来自流量统计的 project 列）。
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from app.deps import get_project_store, get_usage_store
from app.projects import openers as openers_mod
from app.projects.store import ProjectStore

router = APIRouter(prefix="/api/v1", tags=["projects"])


class ProjectAdd(BaseModel):
    path: str


class ProjectPatch(BaseModel):
    starred: bool | None = None
    note: str | None = None
    tags: list[str] | None = None
    name: str | None = None


class OpenPathRequest(BaseModel):
    id: int
    app: str = "explorer"  # explorer / 编辑器 key / CLI 智能体 key


def _openers_state() -> list[dict]:
    return openers_mod.state()


def _store() -> ProjectStore:
    return get_project_store()


def _with_usage(store: ProjectStore, rows: list[dict]) -> list[dict]:
    """给项目行附带已归因的用量（requests / tokens）。"""
    usage = get_usage_store().usage_by_project()
    for r in rows:
        u = usage.get(r["path"], {"requests": 0, "tokens": 0})
        r["requests"] = u["requests"]
        r["tokens"] = u["tokens"]
    return rows


# ---- 列表 / 统计 --------------------------------------------------------
@router.get("/projects", summary="项目列表（含已归因用量）")
def list_projects(
    agent: str | None = None,
    status: str | None = Query(None, pattern="^(active|missing|excluded)$"),
    q: str | None = None,
    starred: bool = False,
    limit: int = Query(200, le=1000),
    offset: int = 0,
):
    store = _store()
    usage = get_usage_store().usage_by_project()
    rows = store.list(
        agent=agent, status=status, q=q,
        starred=starred or None, limit=limit, offset=offset,
    )
    for r in rows:
        u = usage.get(r["path"], {"requests": 0, "tokens": 0})
        r["requests"] = u["requests"]
        r["tokens"] = u["tokens"]
    stats = store.stats()
    stats["attributed_requests"] = sum(u["requests"] for u in usage.values())
    stats["attributed_tokens"] = sum(u["tokens"] for u in usage.values())
    return {"items": rows, "stats": stats}


@router.post("/projects", summary="手动登记项目（目录须存在）")
def add_project(payload: ProjectAdd):
    try:
        rec = _store().add_manual(payload.path)
    except FileNotFoundError as e:
        raise HTTPException(404, str(e))
    except Exception as e:  # noqa: BLE001
        raise HTTPException(400, f"登记失败: {e}")
    if rec is None:
        raise HTTPException(500, "登记失败")
    return _with_usage(_store(), [rec])[0]


# ---- 标注 / 排除 --------------------------------------------------------
@router.patch("/projects/{record_id}", summary="标注：星标/备注/标签/改名")
def patch_project(record_id: int, payload: ProjectPatch):
    rec = _store().patch(
        record_id,
        starred=payload.starred,
        note=payload.note,
        tags=payload.tags,
        name=payload.name,
    )
    if rec is None:
        raise HTTPException(404, "项目不存在")
    return _with_usage(_store(), [rec])[0]


@router.post("/projects/{record_id}/exclude", summary="排除项目（扫描不再复活）")
def exclude_project(record_id: int):
    rec = _store().exclude(record_id)
    if rec is None:
        raise HTTPException(404, "项目不存在")
    return rec


@router.post("/projects/{record_id}/restore", summary="恢复已排除的项目")
def restore_project(record_id: int):
    rec = _store().restore(record_id)
    if rec is None:
        raise HTTPException(404, "项目不存在")
    return rec


# ---- 校验 / 打开 --------------------------------------------------------
@router.post("/projects/validate", summary="校验全部项目路径是否仍存在")
def validate_projects():
    return _store().validate_all()


@router.get("/projects/openers", summary="项目打开方式目录及本机检测结果")
def project_openers():
    return _openers_state()


@router.post("/projects/open", summary="打开项目（资源管理器 / 编辑器 / CLI 智能体）")
def open_project(payload: OpenPathRequest):
    rec = _store().get(payload.id)
    if rec is None:
        raise HTTPException(404, "项目不存在")
    p = Path(rec["path"])
    if not p.is_dir():
        raise HTTPException(410, f"目录不存在: {rec['path']}")

    entry = next((o for o in _openers_state() if o["key"] == payload.app), None)
    if entry is None:
        raise HTTPException(404, f"未知的打开方式: {payload.app}")
    resolved = entry.get("resolved") or ""
    if entry["key"] != "explorer" and not resolved:
        raise HTTPException(410, f"未检测到 {entry['label']}（PATH / 常见安装位置 / 注册表均未找到）")
    try:
        if entry["kind"] == "system":
            os.startfile(str(p))  # type: ignore[attr-defined]
        elif entry["kind"] == "agent":
            # CLI 智能体：在项目目录开一个新终端窗口运行
            arg = f' "{p}"' if entry.get("arg_mode") == "path" else ""
            cmd = f'start "{entry["label"]}" /D "{p}" "{resolved}"{arg}'
            subprocess.Popen(cmd, shell=True)
        else:
            cmd = f'"{resolved}" "{p}"'
            subprocess.Popen(cmd, shell=True)
    except HTTPException:
        raise
    except Exception as e:  # noqa: BLE001
        raise HTTPException(500, f"无法打开: {e}")
    return {"ok": True, "opened": str(p), "app": payload.app}
