"""Nexus Agent - FastAPI 应用入口。"""
from __future__ import annotations

from contextlib import asynccontextmanager

from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from app.api.v1.routes import agent as agent_routes
from app.api.v1.routes import assets as assets_routes
from app.api.v1.routes import projects as projects_routes
from app.api.v1.routes import scan as scan_routes
from app.api.v1.routes import skillhub as skillhub_routes
from app.api.v1.routes import system as system_routes
from app.api.v1.routes import usage as usage_routes
from app.core.config import APP_NAME, APP_VERSION, settings
from app.deps import get_service
from app.stores.fs import StoreError
from app.usage.sync import usage_sync_manager


@asynccontextmanager
async def lifespan(app: FastAPI):
    """启动时确保目录布局，并以文件系统为准重建索引。

    全量重建而非增量：本地资产规模(百~千条)每次启动全扫成本极低，
    却能 100% 覆盖「用外部编辑器直接改文件」导致的数据漂移。
    """
    settings.ensure_layout()
    svc = get_service()
    svc.reconcile()
    # 流量统计：首轮 5s 后自动增量同步，之后按间隔循环（间隔<=0 则关闭）
    usage_sync_manager.start_auto(settings.usage_sync_interval)
    yield
    usage_sync_manager.stop_auto()
    svc.close()


app = FastAPI(
    title=APP_NAME,
    version=APP_VERSION,
    description="本地 AI 资产伺服系统：管理工具 / 记忆 / 规范 / Skills",
    lifespan=lifespan,
)

if settings.cors_origins:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )


@app.exception_handler(StoreError)
def handle_store_error(request: Request, exc: StoreError):  # noqa: ARG001
    return JSONResponse(status_code=409, content={"detail": str(exc)})


app.include_router(assets_routes.router)
app.include_router(system_routes.router)
app.include_router(agent_routes.router)
app.include_router(scan_routes.router)
app.include_router(skillhub_routes.router)
app.include_router(projects_routes.router)
app.include_router(usage_routes.router)


@app.get("/health", tags=["system"])
def health() -> dict:
    return {
        "status": "ok",
        "app": APP_NAME,
        "version": APP_VERSION,
        "data_home": str(settings.data_home),
    }


# 前端产物托管（须在 API 路由之后挂载）：桌面壳由 pywebview 直接打开本机地址
STATIC_DIR = Path(__file__).parent / "static"
if STATIC_DIR.exists():
    app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")
