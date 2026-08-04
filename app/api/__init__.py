"""API 路由聚合 + 公共助手。"""
import datetime
import json

from fastapi import APIRouter

from ..db import jdumps, jloads, query, query_one, execute


def current_user_id() -> str:
    row = query_one("SELECT value_json FROM app_settings WHERE key='runtime'")
    if row:
        try:
            return json.loads(row["value_json"]).get("active_user", "u-zhou")
        except Exception:
            pass
    return "u-zhou"


def set_current_user(uid: str) -> None:
    execute("INSERT OR REPLACE INTO app_settings VALUES('runtime',?)",
            (jdumps({"active_user": uid}),))


def ok(data) -> dict:
    return {"success": True, "data": data}


def now() -> str:
    return datetime.datetime.now().isoformat(timespec="seconds")


def today() -> str:
    return datetime.date.today().isoformat()


from . import (accounts, analysis, benchmarks, creation, extraction, growth,
               packages, planning, profile, radar, retro, settings, users,
               workbench)

api_router = APIRouter(prefix="/api")
for m in (workbench, profile, accounts, benchmarks, radar, extraction,
          analysis, creation, planning, retro, growth, packages, settings,
          users):
    api_router.include_router(m.router)
