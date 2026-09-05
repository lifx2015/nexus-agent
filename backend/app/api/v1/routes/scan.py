"""扫描与跟踪 API：Agent 检测、触发扫描、发现项列表/标注/校验/打开路径。"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from app.deps import get_discovered_store
from app.scanners.manager import scan_manager

router = APIRouter(prefix="/api/v1", tags=["scan"])


class ScanRunRequest(BaseModel):
    agents: list[str] | None = None
    project_roots: list[str] | None = None


class DiscoveredPatch(BaseModel):
    starred: bool | None = None
    note: str | None = None
    tags: list[str] | None = None
    kind: str | None = None
    agent: str | None = None


class OpenPathRequest(BaseModel):
    id: int
    mode: str = "file"  # file: 打开文件 / dir: 打开所在目录


def _store():
    return get_discovered_store()


# ---- Agent 检测与扫描 --------------------------------------------------
@router.get("/scan/agents", summary="受支持 Agent 及本机检测状态")
def scan_agents():
    return scan_manager.engine.agent_status()


@router.post("/scan/run", summary="触发扫描（后台执行）")
def run_scan(payload: ScanRunRequest | None = None):
    payload = payload or ScanRunRequest()
    job = scan_manager.start(payload.agents, payload.project_roots)
    return {"job": job}


@router.get("/scan/status", summary="当前扫描任务状态/进度")
def scan_status():
    job = scan_manager.status()
    if job is None:
        return {"state": "idle", "done": True, "progress": {}, "total": 0}
    return job


# ---- 发现项跟踪 ---------------------------------------------------------
@router.get("/discovered", summary="发现资产列表")
def list_discovered(
    agent: str | None = None,
    kind: str | None = None,
    status: str | None = None,
    q: str | None = None,
    starred: bool = False,
    limit: int = Query(200, le=1000),
    offset: int = 0,
):
    store = _store()
    return {
        "items": store.list(
            agent=agent, kind=kind, status=status, q=q,
            starred=starred or None, limit=limit, offset=offset,
        ),
        "stats": store.stats(),
    }


@router.get("/discovered/stats", summary="发现资产统计")
def discovered_stats():
    return _store().stats()


_PREVIEW_MAX_FILES = 80
_PREVIEW_MAX_BYTES = 512 * 1024


@router.get("/discovered/{record_id}/files", summary="只读预览：读取记录对应文件/目录下的文本文件内容")
def discovered_files(record_id: int):
    rec = _store().get(record_id)
    if rec is None:
        raise HTTPException(404, "记录不存在")
    p = Path(rec["path"])
    if not p.exists():
        raise HTTPException(410, f"路径不存在: {p}")

    files: dict[str, str] = {}
    try:
        if p.is_file() and p.name != "SKILL.md":
            # 普通单文件记录（JSON/MD/YAML 等）：只读该文件本身
            candidates = [(p.name, p)]
        else:
            base = p.parent if p.is_file() else p
            if not base.exists():
                raise HTTPException(410, f"目录不存在: {base}")
            candidates = [(f.name, f) for f in sorted(base.iterdir())]
    except OSError as e:
        raise HTTPException(500, f"无法读取路径: {e}")

    for name, f in candidates:
        if len(files) >= _PREVIEW_MAX_FILES:
            break
        try:
            if not f.is_file() or f.stat().st_size > _PREVIEW_MAX_BYTES:
                continue
            files[name] = f.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue  # 二进制或不可读文件跳过
    return {"name": rec["name"], "path": rec["path"], "files": files}


@router.patch("/discovered/{record_id}", summary="标注：星标/备注/标签/归类")
def patch_discovered(record_id: int, payload: DiscoveredPatch):
    rec = _store().patch(
        record_id,
        starred=payload.starred,
        note=payload.note,
        tags=payload.tags,
        kind=payload.kind,
        agent=payload.agent,
    )
    if rec is None:
        raise HTTPException(404, "记录不存在")
    return rec


@router.post("/discovered/validate", summary="校验全部记录对应文件是否仍存在")
def validate_discovered():
    return _store().validate_all()


@router.post("/discovered/open", summary="打开记录对应的文件或所在目录")
def open_path(payload: OpenPathRequest):
    rec = _store().get(payload.id)
    if rec is None:
        raise HTTPException(404, "记录不存在")
    p = Path(rec["path"])
    if not p.exists():
        raise HTTPException(410, f"文件已不存在: {rec['path']}")

    target = p.parent if payload.mode == "dir" else p
    if not target.exists():
        raise HTTPException(410, f"目录不存在: {target}")
    try:
        if os.name == "nt":
            os.startfile(str(target))  # type: ignore[attr-defined]
        elif sys.platform == "darwin":
            subprocess.Popen(["open", str(target)])
        else:
            subprocess.Popen(["xdg-open", str(target)])
    except Exception as e:  # noqa: BLE001
        raise HTTPException(500, f"无法打开路径: {e}")
    return {"ok": True, "opened": str(target)}
