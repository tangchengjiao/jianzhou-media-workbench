"""系统设置、诊断、用量台账。"""
import json
import shutil
import copy

from fastapi import APIRouter
from pydantic import BaseModel

from .. import config
from ..db import execute, jdumps, jloads, query, query_one
from ..errors import validation_failed
from ..secret_store import get_secret, has_secret, set_secret
from . import current_user_id, ok, today

router = APIRouter(tags=["settings"])

PUBLIC_SECTIONS = set(config.DEFAULT_SETTINGS)
SECRET_FIELDS = {
    ("source", "tikhub_api_key"): "tikhub_api_key",
    ("llm", "api_key"): "llm_api_key",
}


def _public_values(section: str, values: dict) -> dict:
    public = copy.deepcopy(values)
    for (secret_section, field), secret_name in SECRET_FIELDS.items():
        if section == secret_section:
            public.pop(field, None)
            public[f"{field}_configured"] = has_secret(secret_name)
    if section == "datasource":
        for provider_name, provider in (public.get("providers") or {}).items():
            auth = provider.get("auth") or {}
            auth.pop("value", None)
            auth["configured"] = has_secret("tikhub_api_key") if provider_name == "tikhub" else False
    return public


def _validate_shape(value, template, path: str) -> None:
    if not isinstance(value, dict):
        return
    if not isinstance(template, dict):
        raise validation_failed(f"设置字段 {path} 不接受对象值")
    for key, child in value.items():
        if key not in template:
            raise validation_failed(f"未知设置字段：{path}.{key}")
        _validate_shape(child, template[key], f"{path}.{key}")


def _deep_merge(base: dict, update: dict) -> dict:
    result = copy.deepcopy(base)
    for key, value in update.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def migrate_legacy_secrets() -> None:
    """Move plaintext secrets from legacy SQLite settings into DPAPI storage."""
    for section, field, secret_name in (
        ("source", "tikhub_api_key", "tikhub_api_key"),
        ("llm", "api_key", "llm_api_key"),
    ):
        row = query_one("SELECT value_json FROM app_settings WHERE key=?", (section,))
        values = jloads(row["value_json"], {}) if row else {}
        legacy = values.pop(field, "")
        if legacy and not get_secret(secret_name):
            set_secret(secret_name, legacy)
        if row:
            execute("UPDATE app_settings SET value_json=? WHERE key=?", (jdumps(values), section))
    row = query_one("SELECT value_json FROM app_settings WHERE key='datasource'")
    values = jloads(row["value_json"], {}) if row else {}
    auth = (((values.get("providers") or {}).get("tikhub") or {}).get("auth") or {})
    legacy = auth.pop("value", "")
    if legacy and not get_secret("tikhub_api_key"):
        set_secret("tikhub_api_key", legacy)
    if row:
        execute("UPDATE app_settings SET value_json=? WHERE key='datasource'", (jdumps(values),))


@router.get("/settings")
def get_settings():
    rows = query("SELECT * FROM app_settings")
    out = {}
    for r in rows:
        if r["key"] == "runtime":
            continue
        if r["key"] in PUBLIC_SECTIONS:
            out[r["key"]] = _public_values(r["key"], jloads(r["value_json"], {}))
    return ok(out)


class SettingsIn(BaseModel):
    section: str
    values: dict


@router.put("/settings")
def put_settings(body: SettingsIn):
    if body.section not in PUBLIC_SECTIONS:
        raise validation_failed(f"设置分区不允许修改：{body.section}")
    template = config.DEFAULT_SETTINGS[body.section]
    secret_updates: list[tuple[str, str]] = []
    incoming = copy.deepcopy(body.values)
    incoming.pop("tikhub_api_key_configured", None)
    incoming.pop("api_key_configured", None)
    for (secret_section, field), secret_name in SECRET_FIELDS.items():
        if body.section == secret_section and field in incoming:
            secret_value = incoming.pop(field)
            if not isinstance(secret_value, str):
                raise validation_failed(f"{body.section}.{field} 必须是字符串")
            secret_updates.append((secret_name, secret_value))
    if body.section == "datasource":
        provider = ((incoming.get("providers") or {}).get("tikhub") or {})
        auth = provider.get("auth") or {}
        auth.pop("configured", None)
        if "value" in auth:
            value = auth.pop("value")
            if not isinstance(value, str):
                raise validation_failed("datasource.providers.tikhub.auth.value 必须是字符串")
            secret_updates.append(("tikhub_api_key", value))
    _validate_shape(incoming, template, body.section)
    old_row = query_one("SELECT value_json FROM app_settings WHERE key=?",
                        (body.section,))
    old = jloads(old_row["value_json"], {}) if old_row else {}
    merged = _deep_merge(old, incoming)
    for secret_name, secret_value in secret_updates:
        set_secret(secret_name, secret_value)
    execute("INSERT OR REPLACE INTO app_settings VALUES(?,?)",
            (body.section, jdumps(merged)))
    return ok({"note": "设置已保存；密钥由当前 Windows 用户的 DPAPI 加密，且不会通过 API 回传"})


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
    has_key = has_secret("tikhub_api_key")
    foreign_keys = query_one("PRAGMA foreign_keys")["foreign_keys"] == 1
    fk_errors = query("PRAGMA foreign_key_check")
    integrity = query_one("PRAGMA quick_check")["quick_check"]
    return ok({
        "checks": [
            {"name": "数据源（演示模式）" if config.DEMO_MODE else "TikHub API 连通性",
             "status": "ok" if (config.DEMO_MODE or has_key) else "err",
             "detail": "demo_tikhub · 演示数据" if config.DEMO_MODE
                       else ("已配置 Key" if has_key else "CONFIG_MISSING_API_KEY")},
            {"name": "LLM（演示模式）" if config.DEMO_MODE else "LLM 连通性",
             "status": "ok" if config.DEMO_MODE else "err",
             "detail": "demo_llm · 演示数据" if config.DEMO_MODE else "未配置真实模型"},
            {"name": "本地 Whisper（演示）" if config.DEMO_MODE else "本地 Whisper",
             "status": "ok" if config.DEMO_MODE else "err",
             "detail": "demo_faster_whisper_small" if config.DEMO_MODE else "未安装或尚未验证"},
            {"name": "FFmpeg", "status": "ok" if ffmpeg else "warn",
             "detail": ffmpeg or "未在 PATH 中找到（媒体处理功能受限，不影响其他功能）"},
            {"name": "本地存储权限", "status": "ok" if writable else "err",
             "detail": f"{'可写' if writable else '不可写'} · 剩余 {usage.free // (1024**3)} GB · {data_dir}"},
            {"name": "SQLite 完整性与外键",
             "status": "ok" if foreign_keys and not fk_errors and integrity == "ok" else "err",
             "detail": ("quick_check=ok · foreign_keys=on" if foreign_keys and not fk_errors and integrity == "ok"
                        else f"quick_check={integrity} · foreign_key_errors={len(fk_errors)}")},
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
    uid = current_user_id()
    today_cost = query_one(
        "SELECT COALESCE(SUM(cost),0) c FROM usage_ledger WHERE user_id=? AND ts LIKE ?",
        (uid, today() + "%",))["c"]
    month_cost = query_one(
        "SELECT COALESCE(SUM(cost),0) c FROM usage_ledger WHERE user_id=? AND ts LIKE ?",
        (uid, today()[:7] + "%",))["c"]
    cost_settings = jloads(
        query_one("SELECT value_json FROM app_settings WHERE key='cost'")["value_json"], {})
    return ok({
        "today": round(today_cost, 2), "month": round(month_cost, 2),
        "daily_budget": cost_settings.get("daily_budget", 20),
        "monthly_budget": cost_settings.get("monthly_budget", 300),
        "today_requests": query_one(
            "SELECT COUNT(*) c FROM usage_ledger WHERE user_id=? AND ts LIKE ?",
            (uid, today() + "%",))["c"],
    })


@router.get("/usage/ledger")
def usage_ledger():
    return ok(query("SELECT * FROM usage_ledger WHERE user_id=? ORDER BY ts DESC LIMIT 50",
                    (current_user_id(),)))


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
