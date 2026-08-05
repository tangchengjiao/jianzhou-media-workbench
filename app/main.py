"""FastAPI 应用装配：API 路由、静态前端、错误体系、Job Worker。"""
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from . import config
from .api import api_router
from .db import init_db
from .errors import register_error_handlers
from .jobs import recover_interrupted_jobs, start_worker, stop_worker
from .seed import seed_if_empty


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    _ensure_settings()
    _apply_execution_mode()
    from .api.settings import migrate_legacy_secrets
    from .db import transaction
    migrate_legacy_secrets()
    recover_interrupted_jobs()
    with transaction():
        seed_if_empty()
    start_worker()
    from . import scheduler
    scheduler.start()
    yield
    stop_worker()
    scheduler.stop()


def _ensure_settings() -> None:
    """老库升级：补齐设置；仅在映射版本升级时替换内置 Provider 端点。"""
    import copy
    from .db import execute, jdumps, jloads, query_one
    def merge_missing(current, defaults):
        if not isinstance(current, dict) or not isinstance(defaults, dict):
            return current
        merged = copy.deepcopy(current)
        for key, default_value in defaults.items():
            if key not in merged:
                merged[key] = copy.deepcopy(default_value)
            elif isinstance(merged[key], dict) and isinstance(default_value, dict):
                merged[key] = merge_missing(merged[key], default_value)
        return merged

    for section, values in config.DEFAULT_SETTINGS.items():
        if section == "llm":
            row = query_one("SELECT value_json FROM app_settings WHERE key='llm'")
            current = jloads(row["value_json"], {}) if row else {}
            if row and int(current.get("configVersion") or 0) < int(values["configVersion"]):
                had_custom_base = bool(str(current.get("api_base") or "").strip())
                current["configVersion"] = values["configVersion"]
                current.setdefault("protocol", values["protocol"])
                current.setdefault("auth_mode", values["auth_mode"])
                current.setdefault("allow_private_network", values["allow_private_network"])
                if not had_custom_base:
                    current["api_base"] = values["api_base"]
                    for key in ("analysis_model", "writing_model"):
                        if current.get(key) == "claude-sonnet-4":
                            current[key] = values[key]
                execute("UPDATE app_settings SET value_json=? WHERE key='llm'",
                        (jdumps(current),))
        if section == "datasource":
            row = query_one("SELECT value_json FROM app_settings WHERE key='datasource'")
            current = jloads(row["value_json"], {}) if row else {}
            current_version = int(current.get("mappingVersion") or 0)
            default_version = int(values.get("mappingVersion") or 0)
            if row and current_version < default_version:
                merged = copy.deepcopy(current)
                merged["mappingVersion"] = default_version
                merged["_note"] = values.get("_note", "")
                providers = merged.setdefault("providers", {})
                for name, default_provider in (values.get("providers") or {}).items():
                    provider = providers.setdefault(name, {})
                    provider["endpoints"] = copy.deepcopy(default_provider.get("endpoints") or {})
                    for key in ("baseUrl", "auth", "rateLimit"):
                        provider.setdefault(key, copy.deepcopy(default_provider.get(key)))
                execute("UPDATE app_settings SET value_json=? WHERE key='datasource'",
                        (jdumps(merged),))
                current = merged
        row = query_one("SELECT value_json FROM app_settings WHERE key=?", (section,))
        if not row:
            execute("INSERT INTO app_settings VALUES(?,?)", (section, jdumps(values)))
            continue
        current = jloads(row["value_json"], {})
        merged = merge_missing(current, values)
        if merged != current:
            execute("UPDATE app_settings SET value_json=? WHERE key=?",
                    (jdumps(merged), section))


def _apply_execution_mode() -> None:
    from .db import jloads, query_one
    row = query_one("SELECT value_json FROM app_settings WHERE key='execution'")
    values = jloads(row["value_json"], {}) if row else {}
    config.set_demo_mode(values.get("mode", "demo") != "real")


def create_app() -> FastAPI:
    app = FastAPI(title=config.APP_NAME, version=config.APP_VERSION,
                  lifespan=lifespan)
    register_error_handlers(app)
    app.include_router(api_router)

    @app.middleware("http")
    async def security_headers(request, call_next):
        origin = request.headers.get("origin", "")
        if origin and origin not in {
            "http://127.0.0.1:8741", "http://localhost:8741",
            f"http://127.0.0.1:{request.url.port or 80}",
            f"http://localhost:{request.url.port or 80}",
        }:
            from fastapi.responses import JSONResponse
            return JSONResponse(status_code=403, content={
                "success": False,
                "error": {"code": "ORIGIN_NOT_ALLOWED", "message": "请求来源不受信任"},
            })
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "same-origin"
        response.headers["Content-Security-Policy"] = (
            # The no-build Vue distribution compiles in-page templates at runtime.
            # Remove unsafe-eval after Phase 5 moves templates to a build-time compiler.
            "default-src 'self'; script-src 'self' 'unsafe-eval'; style-src 'self' 'unsafe-inline'; "
            "img-src 'self' data: https:; connect-src 'self'; object-src 'none'; "
            "base-uri 'self'; frame-ancestors 'none'"
        )
        return response

    @app.get("/healthz")
    def healthz():
        from .db import query_one
        version = query_one("PRAGMA user_version")
        return {"ok": True, "demo_mode": config.DEMO_MODE,
                "schema_version": next(iter(version.values())) if version else 0}

    # 前端静态资源（免构建 Vue3）
    app.mount("/assets", StaticFiles(directory=config.FRONTEND_DIR / "assets"),
              name="assets")

    @app.get("/{full_path:path}")
    def index(full_path: str = ""):
        return FileResponse(config.FRONTEND_DIR / "index.html")

    return app


app = create_app()
