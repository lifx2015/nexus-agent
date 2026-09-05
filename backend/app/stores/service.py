"""资产服务层：文件存储(事实源) + SQLite 索引 的写穿(read-through/write-through)门面。

所有变更: 先落文件 → 再同步索引。索引只读操作直接查库，读详情实时读文件。
"""
from __future__ import annotations

import sqlite3

from app.core.config import Settings
from app.domain import Asset, AssetKind, AssetStatus, new_id
from app.stores.fs import FileStore, StoreError, rel_path_for
from app.stores.index import IndexStore

# 可变字段白名单
_UPDATABLE = {"name", "body", "status", "tags", "metadata"}


class AssetService:
    def __init__(self, settings: Settings | None = None):
        self.settings = settings or Settings()
        self.fs = FileStore(self.settings)
        self.index = IndexStore(self.settings)

    def close(self) -> None:
        self.index.close()

    # ---- 查询 ----------------------------------------------------------
    def list(
        self,
        kind: AssetKind | None = None,
        status: AssetStatus | None = None,
        tag: str | None = None,
        q: str | None = None,
        limit: int = 200,
        offset: int = 0,
        include_body: bool = False,
    ) -> list[dict]:
        return self.index.list_assets(
            kind=kind.value if kind else None,
            status=status.value if status else None,
            tag=tag,
            q=q,
            limit=limit,
            offset=offset,
            include_body=include_body,
        )

    def get(self, kind: AssetKind, asset_id: str) -> Asset | None:
        """实时读文件保证最新内容（含外部编辑器改动）。"""
        return self.fs.read_asset(kind, asset_id)

    def search(self, q: str, kind: AssetKind | None = None) -> list[dict]:
        """检索命中正文的场景很多，默认返回正文。"""
        return self.index.list_assets(
            kind=kind.value if kind else None, q=q, limit=100, include_body=True
        )

    def stats(self) -> dict:
        return self.index.stats()

    def count(self) -> int:
        return self.index.count_assets()

    # ---- 变更 ----------------------------------------------------------
    def create(
        self,
        kind: AssetKind,
        name: str,
        body: str = "",
        status: AssetStatus = AssetStatus.ENABLED,
        tags: list[str] | None = None,
        metadata: dict | None = None,
        asset_id: str | None = None,
    ) -> Asset:
        name = (name or "").strip()
        if not name:
            raise StoreError("资产名称不能为空")

        asset = Asset(
            kind=kind,
            id=asset_id or new_id(),
            name=name,
            body=body,
            status=status,
            tags=list(tags or []),
            metadata=dict(metadata or {}),
        )
        # id 全局唯一（含用户自定义 id）
        for _ in range(3):
            if self.index.get_asset(asset.id) is None and self.fs.read_asset(kind, asset.id) is None:
                break
            asset.id = new_id()
        else:
            raise StoreError("无法生成唯一 id，请重试")

        # 同名冲突防护
        p = self.fs.path_of(rel_path_for(kind, name))
        if p.exists():
            raise StoreError(f"同名资产已存在: {name}")

        self.fs.write_asset(asset)
        self.index.upsert_asset(asset)
        return asset

    def update(self, kind: AssetKind, asset_id: str, patch: dict) -> Asset:
        asset = self.fs.read_asset(kind, asset_id)
        if asset is None:
            raise KeyError(f"{kind.label}资产不存在: {asset_id}")

        rename_needed = False
        new_name = asset.name
        for key, value in patch.items():
            if key not in _UPDATABLE:
                continue
            if key == "name":
                value = (value or "").strip()
                if not value:
                    raise StoreError("资产名称不能为空")
                if value != asset.name:
                    rename_needed = True
                    new_name = value
            elif key == "status":
                value = AssetStatus(value)
            elif key == "tags":
                value = [str(t) for t in (value or [])]
            elif key == "metadata":
                value = dict(value or {})
            setattr(asset, key, value)

        if rename_needed:
            # 目标重名检测（FileStore.rename_asset 内部处理）
            asset.rel_path = self.fs.rename_asset(kind, asset.rel_path, new_name)

        asset.touch()
        self.fs.write_asset(asset)
        self.index.upsert_asset(asset)
        return asset

    def set_status(self, kind: AssetKind, asset_id: str, status: AssetStatus) -> Asset:
        return self.update(kind, asset_id, {"status": status.value})

    def delete(self, kind: AssetKind, asset_id: str) -> bool:
        asset = self.fs.read_asset(kind, asset_id)
        if asset is None:
            return False
        self.fs.delete_asset(kind, asset.rel_path)
        self.index.delete_asset(asset.id)
        return True

    # ---- 一致性维护 ----------------------------------------------------
    def reconcile(self) -> dict:
        """以文件系统为准全量重建索引。返回统计。"""
        self.index.clear()
        total = 0
        for kind in AssetKind:
            for asset in self.fs.list_assets(kind):
                self.index.upsert_asset(asset)
                total += 1
        return {"indexed": total}
