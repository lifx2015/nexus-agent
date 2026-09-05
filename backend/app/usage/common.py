"""适配器公共工具：路径展开、时间解析、同步统计。"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path


def expand_path(raw: str) -> Path:
    """展开 `~` 与 %VAR% / $VAR 环境变量（Windows 与 POSIX 通吃）。"""
    return Path(os.path.expanduser(os.path.expandvars(raw)))


def parse_ts(value: object) -> int | None:
    """解析 ISO8601/RFC3339 字符串为 unix 秒；失败返回 None。"""
    if not isinstance(value, str) or not value:
        return None
    text = value.strip()
    if text.endswith(("Z", "z")):
        text = text[:-1] + "+00:00"
    try:
        return int(datetime.fromisoformat(text).timestamp())
    except ValueError:
        return None


@dataclass
class SyncStats:
    """单个适配器一轮同步的结果。"""

    source: str
    files_scanned: int = 0
    imported: int = 0
    skipped: int = 0
    errors: list[str] = field(default_factory=list)

    def merge(self, other: "SyncStats") -> None:
        self.files_scanned += other.files_scanned
        self.imported += other.imported
        self.skipped += other.skipped
        self.errors.extend(other.errors)

    def to_dict(self) -> dict:
        return {
            "source": self.source,
            "files_scanned": self.files_scanned,
            "imported": self.imported,
            "skipped": self.skipped,
            "errors": self.errors[:20],  # 上限，防止一次坏盘刷屏
        }
