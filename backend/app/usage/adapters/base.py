"""适配器基类与注册表。

新增一家智能体：继承 BaseUsageAdapter，实现 roots()/collect()，
加入 ADAPTERS 列表即可，同步管理器与 API 层零改动（数据驱动，
与扫描中心 SPECS 的扩展方式一致）。
"""
from __future__ import annotations

from pathlib import Path

from app.usage.common import SyncStats, expand_path
from app.usage.store import UsageStore

# 单轮同步来源文件数上限（防御海量碎文件拖慢同步；超出下轮继续）
MAX_FILES_PER_SYNC = 20000


class BaseUsageAdapter:
    source: str = ""       # 游标命名空间，如 claude/codex
    agent: str = ""        # 归属智能体 key（对齐 scanners.SPECS）
    name: str = ""         # 展示名

    def roots(self) -> list[Path]:
        """候选数据目录（未展开占位）。子类覆写。"""
        return []

    def detected_roots(self) -> list[Path]:
        return [p for p in (expand_path(r) for r in self.roots()) if p.exists()]

    def detect(self) -> bool:
        return bool(self.detected_roots())

    def collect(self, store: UsageStore) -> SyncStats:  # pragma: no cover - 接口
        raise NotImplementedError


ADAPTERS: list[BaseUsageAdapter] = []


def register(cls: type[BaseUsageAdapter]) -> type[BaseUsageAdapter]:
    ADAPTERS.append(cls())
    return cls
