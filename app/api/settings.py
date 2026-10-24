"""系统设置、诊断、用量台账。"""
import json
import shutil
import copy
import urllib.parse

from fastapi import APIRouter, File, UploadFile
from pydantic import BaseModel, Field

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
    ("voice", "api_key"): "asr_api_key",
    ("image", "api_key"): "image_api_key",
    ("wechat_official", "app_secret"): "wechat_app_secret",
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
    """Move plaintext secrets from legacy SQLite into the OS credential store."""
    for section, field, secret_name in (
        ("source", "tikhub_api_key", "tikhub_api_key"),
        ("llm", "api_key", "llm_api_key"),
        ("voice", "api_key", "asr_api_key"),
        ("image", "api_key", "image_api_key"),
        ("wechat_official", "app_secret", "wechat_app_secret"),
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
    # Add newly introduced non-secret fields without overwriting user choices.
    for section, defaults in config.DEFAULT_SETTINGS.items():
        row = query_one("SELECT value_json FROM app_settings WHERE key=?", (section,))
        stored = jloads(row["value_json"], {}) if row else {}
        if section == "voice" and int(stored.get("configVersion") or 0) < 2:
            stored = {**stored, **{
                key: value for key, value in defaults.items()
                if key in {"configVersion", "stt_provider", "protocol", "auth_mode",
                           "api_base", "asr_model"}
            }}
        normalized = _deep_merge(defaults, stored)
        for (secret_section, field), _secret_name in SECRET_FIELDS.items():
            if section == secret_section:
                normalized.pop(field, None)
        execute("INSERT OR REPLACE INTO app_settings(key,value_json) VALUES(?,?)",
                (section, jdumps(normalized)))


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
    if body.section == "execution":
        if incoming.get("mode") not in {"demo", "real"}:
            raise validation_failed("运行模式只能是 demo 或 real")
    _validate_shape(incoming, template, body.section)
    old_row = query_one("SELECT value_json FROM app_settings WHERE key=?",
                        (body.section,))
    old = jloads(old_row["value_json"], {}) if old_row else {}
    merged = _deep_merge(old, incoming)
    if body.section == "llm":
        _validate_llm_values(merged)
    if body.section == "voice":
        _validate_voice_values(merged)
    if body.section == "image":
        _validate_image_values(merged)
    for secret_name, secret_value in secret_updates:
        set_secret(secret_name, secret_value)
    execute("INSERT OR REPLACE INTO app_settings VALUES(?,?)",
            (body.section, jdumps(merged)))
    if body.section == "execution":
        config.set_demo_mode(merged.get("mode") != "real")
    return ok({"note": "设置已保存；密钥由当前操作系统的安全凭据库保护，且不会通过 API 回传",
               "demo_mode": config.DEMO_MODE})


def _validate_llm_values(values: dict) -> None:
    from ..providers import _validate_llm_url
    protocol = values.get("protocol", "openai_compatible")
    if protocol != "openai_compatible":
        raise validation_failed("当前只支持 OpenAI Compatible 协议")
    auth_mode = values.get("auth_mode", "bearer")
    if auth_mode not in {"bearer", "none"}:
        raise validation_failed("模型鉴权方式只能是 bearer 或 none")
    base_url = str(values.get("api_base") or "").strip().rstrip("/")
    if not base_url:
        raise validation_failed("请填写模型 API Base URL")
    parsed = urllib.parse.urlparse(base_url)
    if not parsed.path or parsed.path == "/":
        base_url += "/v1"
    values["api_base"] = base_url
    try:
        _validate_llm_url(base_url, allow_private=bool(values.get("allow_private_network")))
    except Exception as exc:
        if getattr(exc, "code", None):
            raise validation_failed(exc.message)
        raise
    for key in ("fast_model", "analysis_model", "writing_model", "structured_model"):
        model = str(values.get(key) or "").strip()
        if not model or len(model) > 200:
            raise validation_failed(f"{key} 必须填写有效模型名称")
        values[key] = model


def _validate_voice_values(values: dict) -> None:
    from ..providers import _validate_llm_url
    if values.get("stt_provider") != "aliyun_bailian":
        raise validation_failed("当前真实 ASR 只支持 aliyun_bailian")
    if values.get("protocol") != "openai_compatible_audio":
        raise validation_failed("ASR 协议必须是 openai_compatible_audio")
    if values.get("auth_mode") != "bearer":
        raise validation_failed("ASR 鉴权方式必须是 bearer")
    base_url = str(values.get("api_base") or "").strip().rstrip("/")
    if not base_url:
        raise validation_failed("请填写 ASR API Base URL")
    try:
        _validate_llm_url(base_url)
    except Exception as exc:
        if getattr(exc, "code", None):
            raise validation_failed(exc.message)
        raise
    values["api_base"] = base_url
    model = str(values.get("asr_model") or "").strip()
    if not model or len(model) > 200:
        raise validation_failed("请填写有效的 ASR 模型名称")
    values["asr_model"] = model


def _validate_image_values(values: dict) -> None:
    from ..providers import _validate_llm_url
    from ..image_provider import normalize_image_endpoint
    if values.get("protocol") != "openai_compatible_images":
        raise validation_failed("图片模型协议必须是 OpenAI Compatible Images")
    if values.get("auth_mode") not in {"bearer", "none"}:
        raise validation_failed("图片模型鉴权方式只能是 bearer 或 none")
    base_url, generation_path = normalize_image_endpoint(
        str(values.get("api_base") or ""),
        str(values.get("generation_path") or "images/generations"),
    )
    if not base_url:
        raise validation_failed("请填写图片模型 API Base URL")
    parsed = urllib.parse.urlparse(base_url)
    if not parsed.path or parsed.path == "/":
        base_url += "/v1"
    try:
        _validate_llm_url(base_url, allow_private=bool(values.get("allow_private_network")))
    except Exception as exc:
        if getattr(exc, "code", None):
            raise validation_failed(exc.message)
        raise
    model = str(values.get("model") or "").strip()
    if not model or len(model) > 200:
        raise validation_failed("请填写有效的图片模型名称")
    if not generation_path or ".." in generation_path or "://" in generation_path:
        raise validation_failed("图片生成路径必须是相对 API 路径")
    if values.get("default_ratio") not in {"3:4", "9:16", "16:9", "1:1"}:
        raise validation_failed("默认图片比例不受支持")
    variants = int(values.get("default_variants") or 1)
    if variants < 1 or variants > 4:
        raise validation_failed("默认候选数量必须是 1–4")
    values.update({"api_base": base_url, "model": model,
                   "generation_path": generation_path,
                   "default_variants": variants})


@router.get("/settings/runtime")
def runtime():
    version = query_one("PRAGMA user_version")
    schema_version = next(iter(version.values())) if version else 0
    return ok({
        "demo_mode": config.DEMO_MODE,
        "app_name": config.APP_NAME,
        "version": config.APP_VERSION,
        "build_profile": config.BUILD_PROFILE,
        "build_id": config.BUILD_ID,
        "schema_version": schema_version,
        "db_path": str(config.DB_PATH),
        "data_dir": str(config.DATA_DIR),
        "resource_dir": str(config.RESOURCE_DIR),
        "capabilities": {
            "visual_factory": True,
            "unified_assets": True,
            "content_lifecycle": True,
            "advisor": True,
        },
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
    has_key = has_secret("tikhub_api_key")
    llm_row = query_one("SELECT value_json FROM app_settings WHERE key='llm'")
    llm_values = jloads(llm_row["value_json"], {}) if llm_row else {}
    llm_ready = bool(llm_values.get("api_base") and llm_values.get("fast_model")
                     and (llm_values.get("auth_mode") == "none" or has_secret("llm_api_key")))
    voice_row = query_one("SELECT value_json FROM app_settings WHERE key='voice'")
    voice_values = jloads(voice_row["value_json"], {}) if voice_row else {}
    asr_ready = bool(voice_values.get("api_base") and voice_values.get("asr_model")
                     and has_secret("asr_api_key"))
    image_row = query_one("SELECT value_json FROM app_settings WHERE key='image'")
    image_values = jloads(image_row["value_json"], {}) if image_row else {}
    image_ready = bool(image_values.get("api_base") and image_values.get("model")
                       and (image_values.get("auth_mode") == "none"
                            or has_secret("image_api_key")))
    foreign_keys = query_one("PRAGMA foreign_keys")["foreign_keys"] == 1
    fk_errors = query("PRAGMA foreign_key_check")
    integrity = query_one("PRAGMA quick_check")["quick_check"]
    version = query_one("PRAGMA user_version")
    schema_version = next(iter(version.values())) if version else 0
    return ok({
        "runtime": {
            "build_profile": config.BUILD_PROFILE,
            "build_id": config.BUILD_ID,
            "schema_version": schema_version,
            "data_dir": str(config.DATA_DIR),
            "resource_dir": str(config.RESOURCE_DIR),
            "capabilities": {
                "visual_factory": True,
                "unified_assets": True,
                "content_lifecycle": True,
                "advisor": True,
            },
        },
        "checks": [
            {"name": "当前运行版本",
             "status": "ok" if config.BUILD_PROFILE == "source" else "warn",
             "detail": (f"{config.BUILD_PROFILE} · {config.BUILD_ID} · Schema {schema_version}")},
            {"name": "核心能力清单", "status": "ok",
             "detail": "视觉工厂 · 统一资料中心 · 创作生命周期 · 运营顾问"},
            {"name": "数据源（演示模式）" if config.DEMO_MODE else "TikHub API 连通性",
             "status": "ok" if (config.DEMO_MODE or has_key) else "err",
             "detail": "demo_tikhub · 演示数据" if config.DEMO_MODE
                       else ("已配置 Key" if has_key else "CONFIG_MISSING_API_KEY")},
            {"name": "LLM（演示模式）" if config.DEMO_MODE else "LLM 配置",
             "status": "ok" if (config.DEMO_MODE or llm_ready) else "err",
             "detail": "demo_llm · 演示数据" if config.DEMO_MODE
                       else ("配置完整 · 请手动测试连接" if llm_ready else "配置不完整")},
            {"name": "ASR（演示模式）" if config.DEMO_MODE else "阿里云百炼 ASR",
             "status": "ok" if (config.DEMO_MODE or asr_ready) else "err",
             "detail": "demo_stt · 演示数据" if config.DEMO_MODE
                       else (f"{voice_values.get('asr_model')} · 请手动测试音频"
                             if asr_ready else "配置不完整")},
            {"name": "图片模型（演示模式）" if config.DEMO_MODE else "图片模型配置",
             "status": "ok" if (config.DEMO_MODE or image_ready) else "warn",
             "detail": "demo_image · 演示背景" if config.DEMO_MODE
                       else (f"{image_values.get('model')} · 请手动生成测试"
                             if image_ready else "未配置 · 视觉工厂仅允许显式本地模板模式")},
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


class DatasourceCheckIn(BaseModel):
    link: str = Field(min_length=1, max_length=2048)
    platform: str = "douyin"


@router.post("/datasource/check")
def ds_check(body: DatasourceCheckIn):
    """Use the user's public account as an explicit, potentially billable probe."""
    from ..datasource import EndpointExecutor, SUPPORTED_PLATFORMS
    if body.platform not in SUPPORTED_PLATFORMS:
        raise validation_failed(f"暂不支持平台：{body.platform}")
    ds = EndpointExecutor()
    account = ds.resolve_account(body.link, body.platform)
    overview = ds.account_overview(account["uid"], account["platform"])
    return ok({"provider": ds.name, "reachable": True,
               "platform": account["platform"], "uid": account["uid"],
               "link": body.link.strip(), "account_name": overview.get("name"),
               "avatar": overview.get("avatar") or "", "bio": overview.get("bio") or "",
               "fans": int(overview.get("fans") or 0),
               "likes": int(overview.get("likes") or 0),
               "video_count": int(overview.get("video_count") or 0),
               "note": f"真实数据源连通正常（{ds.name}）"})


class LLMCheckIn(BaseModel):
    model: str | None = Field(default=None, max_length=200)


@router.get("/llm/models")
def llm_models():
    from ..providers import RealLLMProvider
    models = RealLLMProvider().list_models()
    return ok({"models": models, "count": len(models),
               "note": f"已读取 {len(models)} 个模型；你仍可手动填写未列出的模型名称。"})


@router.post("/llm/check")
def llm_check(body: LLMCheckIn):
    from ..providers import RealLLMProvider
    result = RealLLMProvider().check_connection(body.model)
    return ok({**result, "note": f"模型 {result['model']} 连接与最小生成测试通过。"})


@router.post("/image/check")
def image_check():
    """显式、可能计费的图片模型最小生成测试。"""
    from ..image_provider import get_image_provider
    from ..jobs import record_usage
    provider = get_image_provider()
    if config.DEMO_MODE:
        result = provider.generate("演示图片背景", "1:1")
        payload = {"provider": result["provider"], "model": result["model"],
                   "reachable": True, "image_bytes": len(result["bytes"]),
                   "request_id": result["request_id"]}
    else:
        payload = provider.check_connection()
    record_usage(current_user_id(), "图片模型连通测试", payload["provider"],
                 "images/generations", payload["model"], 0.0, "1 张")
    return ok({**payload, "note": f"图片模型 {payload['model']} 已返回有效图片。"})


@router.post("/stt/check")
async def stt_check(file: UploadFile = File(...)):
    """Explicit, potentially billable ASR probe with a user-supplied audio file."""
    from ..providers import RealSTTProvider
    payload = await file.read(config.MAX_AUDIO_UPLOAD_BYTES + 1)
    if len(payload) > config.MAX_AUDIO_UPLOAD_BYTES:
        raise validation_failed("测试音频不能超过 10 MB")
    result = RealSTTProvider().transcribe(
        payload, context="这是见舟新媒体工作台的语音识别连通测试。",
        mime_type=file.content_type or "audio/wav",
    )
    return ok({"provider": RealSTTProvider.name, "model": result["model"],
               "reachable": True, "text": result["text"],
               "note": "ASR 连接与真实音频转写通过。"})


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
