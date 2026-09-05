"""开发期直接运行: python run.py（uvicorn 热重载）。"""
from __future__ import annotations

import uvicorn

from app.core.config import settings

if __name__ == "__main__":
    uvicorn.run(
        "app.main:app",
        host=settings.host,
        port=settings.port,
        reload=True,
    )
