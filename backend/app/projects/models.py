"""项目管理数据模型。

核心约束（与扫描中心一致）：只记录路径与元数据快照做跟踪管理，
绝不复制 / 移动 / 修改任何项目文件。
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class ProjectRef:
    """从某来源解析出的一条「项目」线索（去重 / 聚合前）。

    path        规范化后的项目绝对路径（显示与唯一键来源）
    agent       归属 Agent 的 key（如 claude-code）
    source      线索来源: log(会话日志回溯) / marker(标记文件) / manual(手动)
    session_id  关联的会话 id（可空，仅日志来源有）
    ts          该线索对应的时间戳（unix 秒），用于 last_activity 与排序
    """

    path: str
    agent: str
    source: str = "log"
    session_id: str | None = None
    ts: int = 0
    extra: dict = field(default_factory=dict)

    def fingerprint(self) -> str:
        return f"{self.path}|{self.source}"
