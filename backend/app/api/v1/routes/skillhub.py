"""SkillHub 技能仓库 API：连通性 / 浏览 / 本机对比 / 一键更新 / 版本历史。"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from app.deps import get_discovered_store, get_settings, get_skillhub_store
from app.skillhub import (
    SkillHubError,
    UpdateError,
    compare_local_skills,
    get_hub_client,
    recount_states,
    update_skill,
)
from pydantic import BaseModel

router = APIRouter(prefix="/api/v1/skillhub", tags=["skillhub"])


def _hub():
    return get_hub_client()


def _store():
    return get_discovered_store()


# ---- 连通性与浏览 --------------------------------------------------------
@router.get("/status", summary="SkillHub 连通性与仓库规模")
def hub_status():
    try:
        info = _hub().ping()
    except SkillHubError as e:
        return {"online": False, "total": 0, "error": str(e)}
    return info


@router.get("/categories", summary="仓库分类")
def hub_categories():
    try:
        return {"items": _hub().categories()}
    except SkillHubError as e:
        raise HTTPException(502, str(e))


@router.get("/skills", summary="浏览/搜索仓库技能")
def hub_skills(
    q: str = "",
    category: str = "",
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    sort_by: str = "downloads",
    order: str = "desc",
):
    try:
        return _hub().list_skills(q=q, category=category, page=page,
                                  page_size=page_size, sort_by=sort_by, order=order)
    except SkillHubError as e:
        raise HTTPException(502, str(e))


class CompareRequest(BaseModel):
    ids: list[int] | None = None


# ---- 本机对比（结果持久化，页面加载直接读快照，无需访问网络）--------------
@router.get("/compare", summary="读取上次对比快照（不访问网络）")
def hub_compare_cached():
    snap = get_skillhub_store().load_snapshot()
    if snap is None:
        return {"items": [], "summary": recount_states([]), "elapsed": 0, "created_at": None}
    # 过滤已失效的发现项（记录被移除 / 技能文件丢失）
    ids = {r["id"] for r in _store().list(kind="skill", status="active", limit=5000)}
    items = [i for i in snap["items"] if i.get("record_id") in ids]
    return {
        "items": items,
        "summary": recount_states(items),
        "elapsed": snap["elapsed"],
        "created_at": snap["created_at"],
    }


@router.post("/compare", summary="对比本机技能与仓库版本（结果存为快照）")
def hub_compare(payload: CompareRequest | None = None):
    """items 每项含 state: update-available / up-to-date / ahead / unknown-local / not-found"""
    only_ids = payload.ids if payload else None
    try:
        result = compare_local_skills(_store(), _hub(), only_ids=only_ids)
    except SkillHubError as e:
        raise HTTPException(502, str(e))
    result["created_at"] = get_skillhub_store().save_snapshot(
        result["items"], result["summary"], result["elapsed"]
    )
    return result


# ---- 版本历史 --------------------------------------------------------------
@router.get("/versions", summary="技能在仓库的版本历史")
def hub_versions(slug: str, namespace: str = ""):
    try:
        return {"slug": slug, "namespace": namespace, "versions": _hub().versions(slug, namespace)}
    except SkillHubError as e:
        raise HTTPException(502, str(e))


# ---- 更新 ------------------------------------------------------------------
class UpdateRequest(BaseModel):
    record_id: int
    version: str = ""
    backup: bool = True


@router.post("/update", summary="从仓库更新本地技能（自动备份旧版本）")
def hub_update(payload: UpdateRequest):
    try:
        result = update_skill(
            get_settings(), _store(), _hub(),
            record_id=payload.record_id, version=payload.version, backup=payload.backup,
        )
    except UpdateError as e:
        status = 404 if ("不存在" in str(e) or "未收录" in str(e)) else 500
        detail = f"{e}（旧版本已备份: {e.backup_dir}）" if e.backup_dir else str(e)
        raise HTTPException(status, detail)
    except SkillHubError as e:
        raise HTTPException(502, str(e))
    # 同步修正持久化快照，避免页面重载后仍显示旧状态
    get_skillhub_store().patch_snapshot_item(
        payload.record_id,
        local_version=result["to_version"],
        hub_version=result["to_version"],
        state="up-to-date",
    )
    return result
