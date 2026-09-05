"""API 层数据契约（Pydantic v2）。"""
from __future__ import annotations

from pydantic import BaseModel, Field

from app.domain import AssetKind, AssetStatus


class AssetCreate(BaseModel):
    kind: AssetKind
    name: str = Field(min_length=1, max_length=200)
    body: str = ""
    status: AssetStatus = AssetStatus.ENABLED
    tags: list[str] = Field(default_factory=list)
    metadata: dict = Field(default_factory=dict)
    id: str | None = None


class AssetUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    body: str | None = None
    status: AssetStatus | None = None
    tags: list[str] | None = None
    metadata: dict | None = None


class StatusUpdate(BaseModel):
    status: AssetStatus


class AssetOut(BaseModel):
    kind: str
    id: str
    name: str
    status: str
    tags: list[str] = Field(default_factory=list)
    metadata: dict = Field(default_factory=dict)
    body: str = ""
    version: int = 1
    created_at: str
    updated_at: str
    rel_path: str = ""


class StatsOut(BaseModel):
    by_kind: dict[str, dict[str, int]]
    totals: dict[str, int]
    total: int


class SystemInfoOut(BaseModel):
    app: str
    version: str
    data_home: str
    assets_root: str
    index_db: str
    asset_dirs: dict[str, str]
    total: int


class OkOut(BaseModel):
    ok: bool
    detail: str | None = None
