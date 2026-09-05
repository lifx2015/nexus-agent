"""扫描层数据模型。

核心约束：**只记录路径做跟踪管理，绝不复制/导入被扫描方的数据**。
因此 DiscoveredItem 只保存位置、体积、时间、摘要等信息，不保存正文。
"""
from __future__ import annotations

import enum
from dataclasses import dataclass, field
from pathlib import Path


class ItemKind(str, enum.Enum):
    """发现的资产类型（沿用四类资产 + 会话/配置/其他）。"""

    RULE = "rule"
    MEMORY = "memory"
    SKILL = "skill"
    TOOL = "tool"
    SESSION = "session"
    CONFIG = "config"
    OTHER = "other"

    @property
    def label(self) -> str:
        return {
            "rule": "规范",
            "memory": "记忆",
            "skill": "技能",
            "tool": "工具",
            "session": "会话",
            "config": "配置",
            "other": "其他",
        }[self.value]


@dataclass
class DiscoveredItem:
    """一条被发现的外部资产（仅索引，不搬运内容）。"""

    agent: str            # 归属 Agent 的 key，如 claude-code
    kind: ItemKind
    name: str
    path: Path            # 绝对路径（跟踪目标）
    size: int = 0
    mtime: float = 0.0
    summary: str = ""     # 文本摘要（前若干字符），仅用于预览与检索
    tags: list[str] = field(default_factory=list)
    extra: dict = field(default_factory=dict)

    def fingerprint(self) -> str:
        """变更指纹：路径 + 体积 + 修改时间，用于判断原文件是否被改动。"""
        return f"{self.path}|{self.size}|{int(self.mtime)}"

    def to_dict(self) -> dict:
        return {
            "agent": self.agent,
            "kind": self.kind.value,
            "name": self.name,
            "path": str(self.path),
            "size": self.size,
            "mtime": self.mtime,
            "summary": self.summary,
            "tags": list(self.tags),
            "extra": dict(self.extra),
        }


@dataclass
class AgentSpec:
    """一个受支持 Agent 的扫描配置（数据驱动，新增 Agent 只需加一条）。"""

    key: str
    name: str
    vendor: str = ""
    # 全局数据目录（支持 ~ 与 %APPDATA% 风格占位，运行时展开）
    global_dirs: list[str] = field(default_factory=list)
    # 项目级目录名（在用户指定的项目根下查找）
    project_dirs: list[str] = field(default_factory=list)
    # 项目级文件名
    project_files: list[str] = field(default_factory=list)
    # 需要跳过的子目录（体积大、无价值）
    exclude: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.exclude = list(self.exclude) + [
            "node_modules",
            ".git",
            ".svn",
            ".venv",
            "venv",
            "__pycache__",
            "dist",
            "build",
            ".next",
            ".cache",
        ]
