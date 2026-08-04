"""系统设置、诊断、用量台账。"""
import json
import shutil

from fastapi import APIRouter
from pydantic import BaseModel

from .. import config
from ..db import execute, jdumps, jloads, query, query_one
from . import ok, today

router = APIRouter(tags=["settings"])

SECRET_KEYS = {"tikhub_api_key", "api_key"}


def _mask(section: str, values: dict) -> dict:
    masked = dict(values)
    for k in list(masked):
        if k in SECRET_KEYS and masked[k]:
            masked[k] = masked[k][:4] + "••••••••" + masked[k][-4:]
    return masked


@router.get("/settings")
def get_settings():
    rows = query("SELECT * FROM app_settings")
    out = {}
    for r in rows:
        if r["key"] == "runtime":
            continue
        out[r["key"]] = _mask(r["key"], jloads(r["value_json"], {}))
    return ok(out)


class SettingsIn(BaseModel):
    section: str
    values: dict


@router.put("/settings")
def put_settings(body: SettingsIn):
    old_row = query_one("SELECT value_json FROM app_settings WHERE key=?",
                        (body.section,))
    old = jloads(old_row["value_json"], {}) if old_row else {}
    merged = dict(old)
    for k, v in body.values.items():
        if k in SECRET_KEYS and isinstance(v, str) and "••••" in v:
            continue  # 脱敏占位符不覆盖真实密钥
        merged[k] = v
    execute("INSERT OR REPLACE INTO app_settings VALUES(?,?)",
            (body.section, jdumps(merged)))
    return ok({"note": "设置已保存 · 密钥仅存储于本机，日志中已脱敏"})


@router.get("/settings/runtime")
def runtime():
    return ok({
        "demo_mode": config.DEMO_MODE,
        "app_name": config.APP_NAME,
        "version": config.APP_VERSION,
        "db_path": str(config.DB_PATH),
        "data_dir": str(config.DATA_DIR),
    })


@router.get("/diagnostics")
def diagnostics():
    ffmpeg = shutil.which("ffmpeg")
    data_dir = config.DATA_DIR
    writable = False
    try:
        data_dir.mkdir(parents=True, exist_ok=True)
        probe = data_dir / ".write_probe"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        writable = True
    except OSError:
        writable = False
    usage = shutil.disk_usage(str(data_dir.anchor or "."))
    tikhub_key = query_one("SELECT value_json FROM app_settings WHERE key='source'")
    has_key = bool(tikhub_key and json.loads(tikhub_key["value_json"]).get("tikhub_api_key"))
    return ok({
        "checks": [
            {"name": "数据源（演示模式）" if config.DEMO_MODE else "TikHub API 连通性",
             "status": "ok" if (config.DEMO_MODE or has_key) else "err",
             "detail": "demo_tikhub · 演示数据" if config.DEMO_MODE
                       else ("已配置 Key" if has_key else "CONFIG_MISSING_API_KEY")},
            {"name": "LLM（演示模式）" if config.DEMO_MODE else "LLM 连通性",
             "status": "ok" if config.DEMO_MODE else "err",
             "detail": "demo_llm · 演示数据" if config.DEMO_MODE else "未配置真实模型"},
            {"name": "本地 Whisper（演示）", "status": "ok",
             "detail": "demo_faster_whisper_small" if config.DEMO_MODE else "未安装"},
            {"name": "FFmpeg", "status": "ok" if ffmpeg else "warn",
             "detail": ffmpeg or "未在 PATH 中找到（媒体处理功能受限，不影响其他功能）"},
            {"name": "本地存储权限", "status": "ok" if writable else "err",
             "detail": f"{'可写' if writable else '不可写'} · 剩余 {usage.free // (1024**3)} GB · {data_dir}"},
        ],
    })


@router.get("/diagnostics/errors")
def recent_errors():
    rows = query("SELECT * FROM error_logs ORDER BY id DESC LIMIT 20")
    return ok(rows)


@router.get("/diagnostics/trace/{trace_id}")
def trace_query(trace_id: str):
    rows = query("SELECT * FROM error_logs WHERE trace_id=?", (trace_id,))
    jobs = query("SELECT id, task_type, status, current_step, trace_id FROM jobs"
                 " WHERE trace_id=?", (trace_id,))
    return ok({"errors": rows, "jobs": jobs})


@router.get("/usage/summary")
def usage_summary():
    today_cost = query_one(
        "SELECT COALESCE(SUM(cost),0) c FROM usage_ledger WHERE ts LIKE ?",
        (today() + "%",))["c"]
    month_cost = query_one(
        "SELECT COALESCE(SUM(cost),0) c FROM usage_ledger WHERE ts LIKE ?",
        (today()[:7] + "%",))["c"]
    cost_settings = jloads(
        query_one("SELECT value_json FROM app_settings WHERE key='cost'")["value_json"], {})
    return ok({
        "today": round(today_cost, 2), "month": round(month_cost, 2),
        "daily_budget": cost_settings.get("daily_budget", 20),
        "monthly_budget": cost_settings.get("monthly_budget", 300),
        "today_requests": query_one(
            "SELECT COUNT(*) c FROM usage_ledger WHERE ts LIKE ?", (today() + "%",))["c"],
    })


@router.get("/usage/ledger")
def usage_ledger():
    return ok(query("SELECT * FROM usage_ledger ORDER BY ts DESC LIMIT 50"))


# ================= 数据源与调度 =================

@router.get("/datasource/status")
def ds_status():
    from ..datasource import datasource_status
    return ok(datasource_status())


@router.post("/datasource/check")
def ds_check():
    """连通性检查：真实调用一次账号资料接口，不通过即显式报错。"""
    from ..datasource import get_datasource
    ds = get_datasource()
    ov = ds.account_overview("check_probe", "douyin")
    return ok({"provider": ds.name, "reachable": True,
               "note": f"数据源连通正常（{ds.name}）"})


@router.get("/scheduler")
def scheduler_status():
    from .. import scheduler
    return ok(scheduler.status())


class SchedulerIn(BaseModel):
    enabled: bool | None = None
    benchmark_patrol_time: str | None = None
    snapshot_time: str | None = None


@router.put("/scheduler")
def scheduler_update(body: SchedulerIn):
    from .. import scheduler
    cfg = scheduler.get_config()
    for k, v in body.model_dump(exclude_none=True).items():
        cfg[k] = v
    scheduler.save_config(cfg)
    return ok({"note": "调度配置已保存", "config": cfg})


@router.post("/scheduler/run/{task_name}")
async def scheduler_run(task_name: str):
    from .. import scheduler
    result = await scheduler.run_task(task_name)
    return ok(result)
