"""系统级 API：信息、统计、索引重建、数据目录切换。"""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.core.config import APP_NAME, APP_VERSION, ASSET_DIR_NAMES, Settings
from app.deps import get_service, get_settings, rebind
from app.schemas.asset import OkOut, StatsOut, SystemInfoOut

router = APIRouter(prefix="/api/v1", tags=["system"])


class DataHomeUpdate(BaseModel):
    path: str


@router.get("/stats", response_model=StatsOut, summary="资产统计")
def stats():
    return get_service().stats()


@router.get("/system/info", response_model=SystemInfoOut, summary="系统与数据目录信息")
def system_info():
    s: Settings = get_settings()
    return {
        "app": APP_NAME,
        "version": APP_VERSION,
        "data_home": str(s.data_home),
        "assets_root": str(s.assets_root),
        "index_db": str(s.db_path),
        "asset_dirs": {k: str(s.asset_dir(k)) for k in ASSET_DIR_NAMES},
        "total": get_service().count(),
    }


@router.post("/system/reconcile", response_model=OkOut, summary="按文件系统重建索引")
def reconcile():
    res = get_service().reconcile()
    return {"ok": True, "detail": f"已重建索引，共 {res['indexed']} 条资产"}


@router.post("/system/data-home", response_model=SystemInfoOut, summary="切换数据目录")
def switch_data_home(payload: DataHomeUpdate):
    target = Path(payload.path).expanduser()
    try:
        rebind(target)
    except Exception as e:  # 目录不可写等
        raise HTTPException(400, f"切换数据目录失败: {e}")
    s = get_settings()
    return {
        "app": APP_NAME,
        "version": APP_VERSION,
        "data_home": str(s.data_home),
        "assets_root": str(s.assets_root),
        "index_db": str(s.db_path),
        "asset_dirs": {k: str(s.asset_dir(k)) for k in ASSET_DIR_NAMES},
        "total": get_service().count(),
    }
