"""FastAPI 应用装配：API 路由、静态前端、错误体系、Job Worker。"""
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from . import config
from .api import api_router
from .db import init_db
from .errors import register_error_handlers
from .jobs import start_worker, stop_worker
from .seed import seed_if_empty


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    _ensure_settings()
    seed_if_empty()
    start_worker()
    from . import scheduler
    scheduler.start()
    yield
    stop_worker()
    scheduler.stop()


def _ensure_settings() -> None:
    """老库升级：补齐新增设置分区（不改已有值）。"""
    from .db import execute, jdumps
    for section, values in config.DEFAULT_SETTINGS.items():
        execute("INSERT OR IGNORE INTO app_settings VALUES(?,?)",
                (section, jdumps(values)))


def create_app() -> FastAPI:
    app = FastAPI(title=config.APP_NAME, version=config.APP_VERSION,
                  lifespan=lifespan)
    register_error_handlers(app)
    app.include_router(api_router)

    @app.get("/healthz")
    def healthz():
        return {"ok": True, "demo_mode": config.DEMO_MODE}

    # 前端静态资源（免构建 Vue3）
    app.mount("/assets", StaticFiles(directory=config.FRONTEND_DIR / "assets"),
              name="assets")

    @app.get("/{full_path:path}")
    def index(full_path: str = ""):
        return FileResponse(config.FRONTEND_DIR / "index.html")

    return app


app = create_app()
