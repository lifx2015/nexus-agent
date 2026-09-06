"""技能跨智能体复用 API：目标列表 / 安装状态 / 部署 / 卸载。"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from app.deps import get_discovered_store, get_settings
from app.skills import (
    ShareError,
    deploy,
    deployment_status,
    list_targets,
    resolve_skill_dir,
    undeploy,
)

router = APIRouter(prefix="/api/v1/skills/share", tags=["skills-share"])


@router.get("/targets", summary="可部署的目标智能体及其 skills 目录")
def share_targets():
    return {"items": list_targets()}


@router.get("/status", summary="某技能在各目标智能体的安装状态（实时探测）")
def share_status(
    src: str = Query(..., description="来源: track=扫描发现 / own=自建资产"),
    id: str = Query(..., description="记录 ID"),
):
    try:
        skill_dir = resolve_skill_dir(get_settings(), get_discovered_store(), src, id)
    except ShareError as e:
        raise HTTPException(404, str(e))
    return {
        "skill_name": skill_dir.name,
        "source_dir": str(skill_dir),
        "targets": deployment_status(skill_dir),
    }


class DeployRequest(BaseModel):
    src: str
    id: int | str
    targets: list[str]
    overwrite: bool = False


@router.post("/deploy", summary="部署技能到选中的目标智能体（已存在同名需勾选覆盖）")
def share_deploy(payload: DeployRequest):
    try:
        skill_dir = resolve_skill_dir(get_settings(), get_discovered_store(), payload.src, payload.id)
        results = deploy(get_settings(), skill_dir, payload.targets, payload.overwrite)
    except ShareError as e:
        raise HTTPException(400, str(e))
    return {"results": results}


class RemoveRequest(BaseModel):
    src: str
    id: int | str
    target: str


@router.post("/remove", summary="从目标智能体卸载技能（自动备份到数据目录）")
def share_remove(payload: RemoveRequest):
    try:
        skill_dir = resolve_skill_dir(get_settings(), get_discovered_store(), payload.src, payload.id)
        result = undeploy(get_settings(), skill_dir, payload.target)
    except ShareError as e:
        status = 404 if "未安装" in str(e) else 400
        raise HTTPException(status, str(e))
    return result
