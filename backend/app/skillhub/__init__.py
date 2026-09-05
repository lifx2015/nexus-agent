"""SkillHub 技能仓库：关联本机技能、版本对比、一键更新。"""
from __future__ import annotations

import threading

from app.skillhub.client import SkillHubClient, SkillHubError
from app.skillhub.match import (
    AHEAD,
    NOT_FOUND,
    UP_AVAILABLE,
    UP_TO_DATE,
    UNKNOWN_LOCAL,
    compare_local_skills,
    compare_versions,
    normalize_slug,
)
from app.skillhub.updater import UpdateError, update_skill
from app.skillhub.store import SkillHubStore, recount_states

__all__ = [
    "SkillHubClient",
    "SkillHubError",
    "UpdateError",
    "update_skill",
    "compare_local_skills",
    "compare_versions",
    "normalize_slug",
    "get_hub_client",
    "SkillHubStore",
    "recount_states",
    "UP_AVAILABLE",
    "UP_TO_DATE",
    "AHEAD",
    "UNKNOWN_LOCAL",
    "NOT_FOUND",
]

_client: SkillHubClient | None = None
_client_lock = threading.Lock()


def get_hub_client() -> SkillHubClient:
    """全局 SkillHub 客户端（惰性单例）。"""
    global _client
    with _client_lock:
        if _client is None:
            _client = SkillHubClient()
        return _client
