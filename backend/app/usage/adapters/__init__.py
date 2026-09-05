"""适配器注册：导入各模块触发 @register，填充 ADAPTERS。"""
from app.usage.adapters.base import ADAPTERS, BaseUsageAdapter
from app.usage.adapters.claude_code import ClaudeCodeAdapter  # noqa: F401
from app.usage.adapters.codex import CodexAdapter  # noqa: F401
from app.usage.adapters.gemini import GeminiAdapter  # noqa: F401
from app.usage.adapters.opencode import OpenCodeAdapter  # noqa: F401
from app.usage.adapters.workbuddy import WorkBuddyAdapter  # noqa: F401
from app.usage.adapters.zcode import ZCodeAdapter  # noqa: F401

__all__ = ["ADAPTERS", "BaseUsageAdapter"]
