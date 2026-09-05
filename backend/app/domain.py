"""领域模型：资产(Asset)是系统最小管理单元。

一条资产在磁盘上是一个 Markdown 文件：
  ---                                  <- YAML frontmatter(元数据)
  id / kind / name / status / tags
  version / created_at / updated_at
  metadata: { ... 类型专用扩展 }
  ---
  <body>                               <- Markdown 正文(内容，AI 可读)

文件是唯一事实源；SQLite 只是为检索/统计而建的镜像索引，可随时全量重建。
"""
from __future__ import annotations

import enum
import uuid
from dataclasses import dataclass, field
from datetime import datetime


class AssetKind(str, enum.Enum):
    TOOL = "tool"
    MEMORY = "memory"
    RULE = "rule"
    SKILL = "skill"

    @property
    def label(self) -> str:
        return {"tool": "工具", "memory": "记忆", "rule": "规范", "skill": "技能"}[self.value]


class AssetStatus(str, enum.Enum):
    ENABLED = "enabled"
    DISABLED = "disabled"
    ARCHIVED = "archived"

    @property
    def label(self) -> str:
        return {"enabled": "启用", "disabled": "停用", "archived": "归档"}[self.value]


def now_local() -> str:
    """系统时区当前时间，格式 yyyy-MM-dd HH:mm:ss。"""
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def new_id() -> str:
    """12 位十六进制短 id，人类可读。"""
    return uuid.uuid4().hex[:12]


@dataclass
class Asset:
    kind: AssetKind
    id: str
    name: str
    body: str = ""
    status: AssetStatus = AssetStatus.ENABLED
    tags: list[str] = field(default_factory=list)
    metadata: dict = field(default_factory=dict)
    created_at: str = field(default_factory=now_local)
    updated_at: str = field(default_factory=now_local)
    version: int = 1
    # 相对 assets_root 的路径（展示/定位用），由存储层填充
    rel_path: str = ""

    def touch(self) -> None:
        self.updated_at = now_local()
        self.version += 1

    def to_dict(self, include_body: bool = True) -> dict:
        d: dict = {
            "kind": self.kind.value,
            "id": self.id,
            "name": self.name,
            "status": self.status.value,
            "tags": list(self.tags),
            "metadata": dict(self.metadata),
            "version": self.version,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "rel_path": self.rel_path,
        }
        if include_body:
            d["body"] = self.body
        return d

    @classmethod
    def from_dict(cls, d: dict, kind: AssetKind | None = None) -> "Asset":
        return cls(
            kind=AssetKind(d.get("kind")) if kind is None else kind,
            id=d.get("id") or new_id(),
            name=d.get("name", ""),
            body=d.get("body", ""),
            status=AssetStatus(d.get("status", AssetStatus.ENABLED.value)),
            tags=list(d.get("tags", []) or []),
            metadata=dict(d.get("metadata", {}) or {}),
            created_at=d.get("created_at", now_local()),
            updated_at=d.get("updated_at", now_local()),
            version=int(d.get("version", 1)),
            rel_path=d.get("rel_path", ""),
        )
