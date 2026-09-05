"""资产 REST API（统一 kind 参数，覆盖 tool/memory/rule/skill 四类）。"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException

from app.deps import get_service
from app.domain import AssetKind, AssetStatus
from app.schemas.asset import AssetCreate, AssetOut, AssetUpdate, StatusUpdate
from app.stores.fs import StoreError

router = APIRouter(prefix="/api/v1", tags=["assets"])


def parse_kind(kind: str) -> AssetKind:
    try:
        return AssetKind(kind)
    except ValueError:
        raise HTTPException(404, f"未知资产类型: {kind}（可用: tool/memory/rule/skill）")


def parse_status(status: str | None) -> AssetStatus | None:
    if not status:
        return None
    try:
        return AssetStatus(status)
    except ValueError:
        raise HTTPException(400, f"未知状态: {status}（可用: enabled/disabled/archived）")


@router.get("/assets", response_model=list[AssetOut], summary="资产列表（可筛选/搜索）")
def list_assets(
    kind: str | None = None,
    status: str | None = None,
    tag: str | None = None,
    q: str | None = None,
    limit: int = 200,
    offset: int = 0,
):
    return get_service().list(
        kind=parse_kind(kind) if kind else None,
        status=parse_status(status),
        tag=tag,
        q=q,
        limit=limit,
        offset=offset,
    )


@router.post("/assets", response_model=AssetOut, status_code=201, summary="新建资产")
def create_asset(payload: AssetCreate):
    try:
        asset = get_service().create(
            kind=payload.kind,
            name=payload.name,
            body=payload.body,
            status=payload.status,
            tags=payload.tags,
            metadata=payload.metadata,
            asset_id=payload.id,
        )
    except StoreError as e:
        raise HTTPException(409, str(e))
    return asset.to_dict()


@router.get("/assets/{kind}/{asset_id}", response_model=AssetOut, summary="读取资产详情")
def get_asset(kind: str, asset_id: str):
    asset = get_service().get(parse_kind(kind), asset_id)
    if asset is None:
        raise HTTPException(404, "资产不存在")
    return asset.to_dict()


@router.patch("/assets/{kind}/{asset_id}", response_model=AssetOut, summary="更新资产")
def update_asset(kind: str, asset_id: str, payload: AssetUpdate):
    patch = payload.model_dump(exclude_unset=True)
    try:
        asset = get_service().update(parse_kind(kind), asset_id, patch)
    except KeyError:
        raise HTTPException(404, "资产不存在")
    except StoreError as e:
        raise HTTPException(409, str(e))
    return asset.to_dict()


@router.delete("/assets/{kind}/{asset_id}", summary="删除资产")
def delete_asset(kind: str, asset_id: str):
    ok = get_service().delete(parse_kind(kind), asset_id)
    if not ok:
        raise HTTPException(404, "资产不存在")
    return {"ok": True}


@router.post(
    "/assets/{kind}/{asset_id}/status", response_model=AssetOut, summary="启停/归档"
)
def set_status(kind: str, asset_id: str, payload: StatusUpdate):
    try:
        asset = get_service().set_status(parse_kind(kind), asset_id, payload.status)
    except KeyError:
        raise HTTPException(404, "资产不存在")
    return asset.to_dict()


@router.get("/skills/{asset_id}/files", summary="读取 Skill 目录下所有文件")
def skill_files(asset_id: str):
    svc = get_service()
    asset = svc.get(AssetKind.SKILL, asset_id)
    if asset is None:
        raise HTTPException(404, "Skill 不存在")
    return svc.fs.read_skill_files(asset.rel_path)
