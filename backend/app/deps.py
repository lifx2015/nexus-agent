"""依赖注入：全局单例服务。"""
from __future__ import annotations

import threading

from app.core.config import Settings
from app.projects.store import ProjectStore
from app.skillhub.store import SkillHubStore
from app.stores.discovered import DiscoveredStore
from app.stores.service import AssetService
from app.usage.store import UsageStore

_settings = Settings()
_service = AssetService(_settings)
_lock = threading.Lock()

# discovered / projects / usage 存储按数据目录缓存（切换目录后各自独立）
_discovered_stores: dict[str, DiscoveredStore] = {}
_project_stores: dict[str, ProjectStore] = {}
_usage_stores: dict[str, UsageStore] = {}
_skillhub_stores: dict[str, SkillHubStore] = {}


def get_settings() -> Settings:
    return _settings


def get_service() -> AssetService:
    return _service


def get_discovered_store() -> DiscoveredStore:
    key = str(_settings.data_home)
    store = _discovered_stores.get(key)
    if store is None:
        store = DiscoveredStore(_settings)
        _discovered_stores[key] = store
    return store


def get_usage_store() -> UsageStore:
    key = str(_settings.data_home)
    store = _usage_stores.get(key)
    if store is None:
        store = UsageStore(_settings)
        _usage_stores[key] = store
    return store


def get_skillhub_store() -> SkillHubStore:
    key = str(_settings.data_home)
    store = _skillhub_stores.get(key)
    if store is None:
        store = SkillHubStore(_settings)
        _skillhub_stores[key] = store
    return store


def get_project_store() -> ProjectStore:
    key = str(_settings.data_home)
    store = _project_stores.get(key)
    if store is None:
        store = ProjectStore(_settings)
        _project_stores[key] = store
    return store


def rebind(data_home) -> AssetService:
    """切换数据目录：重建配置与服务的全局单例（各按目录缓存的存储一并失效）。"""
    global _settings, _service
    with _lock:
        _service.close()
        for store in _usage_stores.values():
            store.close()
        _usage_stores.clear()
        for store in _project_stores.values():
            store.close()
        _project_stores.clear()
        for store in _discovered_stores.values():
            store.close()
        _discovered_stores.clear()
        for store in _skillhub_stores.values():
            store.close()
        _skillhub_stores.clear()
        _settings = Settings(data_home=data_home)
        _service = AssetService(_settings)
        _service.reconcile()
        return _service
