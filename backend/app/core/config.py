"""全局配置。

配置优先级: 环境变量(NEXUS_*) > .env 文件 > 代码默认值。

数据根目录默认在用户目录下的隐藏目录 .nexus-agent，
可用环境变量 NEXUS_HOME 覆盖；未来支持在 UI 中切换多数据目录
（多目录的实现方式是：同一份 Settings 用不同 data_home 实例化）。
"""
from __future__ import annotations

from pathlib import Path

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

# 四类资产子目录名（与 frontmatter type 字段一致）
ASSET_TYPES = ("tool", "memory", "rule", "skill")
ASSET_DIR_NAMES = {
    "tool": "tools",
    "memory": "memory",
    "rule": "rules",
    "skill": "skills",
}

DEFAULT_HOME = Path.home() / ".nexus-agent"
APP_NAME = "Nexus Agent"
APP_VERSION = "0.1.0"


class Settings(BaseSettings):
    """应用设置。data_home 为资产根目录（含 assets/ 与 nexus.db）。"""

    model_config = SettingsConfigDict(
        env_prefix="NEXUS_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        # 允许用字段名 data_home 直接实例化（测试/多目录场景），否则会被别名静默吞掉
        populate_by_name=True,
    )

    # 环境变量: NEXUS_DATA_HOME 或 NEXUS_HOME（二者皆可）
    data_home: Path = Field(
        default=DEFAULT_HOME,
        validation_alias=AliasChoices("NEXUS_DATA_HOME", "NEXUS_HOME"),
    )
    host: str = "127.0.0.1"
    port: int = 8721
    # 开发时前端跑 Vite 时需要 CORS；桌面壳加载同源页面则不需要
    cors_origins: list[str] = ["http://localhost:5173", "http://127.0.0.1:5173"]
    # 流量统计自动增量同步间隔（秒）；<=0 关闭自动同步，仅手动触发
    usage_sync_interval: int = 120

    # ---- 派生路径 ----------------------------------------------------
    @property
    def assets_root(self) -> Path:
        return self.data_home / "assets"

    @property
    def db_path(self) -> Path:
        return self.data_home / "nexus.db"

    @property
    def config_file(self) -> Path:
        return self.data_home / "config.toml"

    def asset_dir(self, kind: str) -> Path:
        name = ASSET_DIR_NAMES.get(kind)
        if name is None:
            raise KeyError(f"未知资产类型: {kind}，可用: {', '.join(ASSET_TYPES)}")
        return self.assets_root / name

    def ensure_layout(self) -> None:
        """确保数据目录结构存在（幂等）。"""
        for kind in ASSET_TYPES:
            self.asset_dir(kind).mkdir(parents=True, exist_ok=True)


settings = Settings()
