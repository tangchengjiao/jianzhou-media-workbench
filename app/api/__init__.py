"""API 路由聚合 + 公共助手。"""
import datetime
import json

from fastapi import APIRouter

from ..db import jdumps, jloads, query, query_one, execute


def current_user_id() -> str:
    from .. import config
    row = query_one("SELECT value_json FROM app_settings WHERE key='runtime'")
    scope_key = "active_demo_user" if config.DEMO_MODE else "active_real_user"
    fallback = "u-zhou" if config.DEMO_MODE else "u-local"
    if row:
        try:
            values = json.loads(row["value_json"])
            return values.get(scope_key) or values.get("active_user") or fallback
        except Exception:
            pass
    return fallback


def set_current_user(uid: str) -> None:
    from .. import config
    row = query_one("SELECT value_json FROM app_settings WHERE key='runtime'")
    values = jloads(row["value_json"], {}) if row else {}
    scope_key = "active_demo_user" if config.DEMO_MODE else "active_real_user"
    values[scope_key] = uid
    values["active_user"] = uid
    execute("INSERT OR REPLACE INTO app_settings VALUES('runtime',?)", (jdumps(values),))


def ok(data) -> dict:
    return {"success": True, "data": data}


def now() -> str:
    return datetime.datetime.now().isoformat(timespec="seconds")


def today() -> str:
    return datetime.date.today().isoformat()


from . import (accounts, advisor, analysis, assets, benchmarks, channels, creation, extraction, growth, ideas,
               job_status, packages, planning, profile, radar, retro, settings,
               users, visuals, workbench)

api_router = APIRouter(prefix="/api")
for m in (workbench, profile, accounts, benchmarks, radar, extraction,
          analysis, creation, planning, retro, growth, packages, settings, job_status,
          users, advisor, visuals, assets, channels, ideas):
    api_router.include_router(m.router)
