"""内容提取：素材上传、执行计划、可靠任务与证据资产。"""
import hashlib
from pathlib import Path

from fastapi import APIRouter, File, Form, UploadFile
from pydantic import BaseModel

from .. import config
from ..db import execute, jdumps, jloads, query, query_one
from ..errors import state_invalid, validation_failed
from ..ids import new_id
from ..jobs import (cleanup_duplicate_jobs, create_job, delete_job_record,
                    duplicate_job_summary, get_job, list_jobs, request_cancel,
                    retry_job)
from ..repositories import evidence, evidence_chain, owned_row
from ..text_quality import repair_mojibake, repair_text_tree
from . import current_user_id, now, ok

router = APIRouter(prefix="/extraction", tags=["extraction"])

PRIORITY = ["有效的本地缓存", "用户导入的正文", "平台原生正文",
            "平台原生字幕", "用户主动选择的 ASR"]


def _resolved_text(assets: dict) -> tuple[str, str]:
    """Return the best persisted text and its provenance without inventing content."""
    for key, label in (("clean", "清洗文案"), ("asr", "原始 ASR 转写"),
                       ("caption", "平台原生字幕")):
        layer = assets.get(key) or {}
        text = str(layer.get("text") or "").strip()
        if layer.get("exists") and text:
            return text, label
    return "", "暂无完整原文"


class PlanIn(BaseModel):
    signal_id: str | None = None
    account_work_id: str | None = None
    title: str | None = None
    platform: str = ""
    url: str = ""
    asset_id: str | None = None


@router.post("/plans")
def create_plan(body: PlanIn):
    """生成执行计划，不立即执行。无字幕继续 ASR 默认不授权。"""
    uid = current_user_id()
    video_id = ""
    asset = None
    source_content_id = None
    if body.asset_id:
        asset = owned_row("media_assets", body.asset_id, uid, label="素材")
        if asset.get("source_content_id"):
            source = owned_row("source_contents", asset["source_content_id"], uid,
                               label="来源内容")
            title, platform, url = source["title"], source["platform"], source["url"]
        else:
            title, platform, url = body.title or "本地音频", body.platform, body.url
        source_content_id = asset.get("source_content_id")
    elif body.signal_id:
        sig = owned_row("signals", body.signal_id, uid, label="信号")
        title, platform = sig["title"], sig["platform"]
        url = sig["url"]
        source_content_id = sig.get("content_id")
        # 对标信号显式关联缓存作品，不能从哈希信号 ID 猜平台作品 ID。
        # 旧库由迁移按所有权与标题补链；仍无关联时明确保留为空并由
        # Provider 返回可理解的失败，绝不生成演示文案。
        if sig.get("benchmark_work_id"):
            w = query_one(
                "SELECT w.video_id,w.url,b.platform FROM benchmark_works w "
                "JOIN benchmark_accounts b ON b.id=w.bm_id "
                "WHERE w.id=? AND b.user_id=?",
                (sig["benchmark_work_id"], uid),
            )
            if w:
                video_id = w["video_id"] or ""
                url = url or w["url"] or ""
                platform = w["platform"] or platform
    elif body.account_work_id:
        work = query_one(
            "SELECT w.*,a.platform,a.user_id FROM account_works w "
            "JOIN accounts a ON a.id=w.account_id WHERE w.id=? AND a.user_id=?",
            (body.account_work_id, uid),
        )
        if not work:
            raise validation_failed("未找到这个自有账号作品，可能已被删除或不属于当前工作区")
        title = repair_mojibake(work["title"] or "无标题作品")
        platform, url, video_id = work["platform"], work["url"], work["video_id"]
        cached = query_one(
            "SELECT id FROM source_contents WHERE user_id=? AND url=? "
            "ORDER BY created_at DESC LIMIT 1",
            (uid, url),
        ) if url else None
        source_content_id = cached["id"] if cached else None
    else:
        title = body.title or "手动粘贴来源"
        platform, url = body.platform, body.url
    cache_hit = False
    if source_content_id:
        cached_source = query_one(
            "SELECT assets_json FROM source_contents WHERE id=? AND user_id=?",
            (source_content_id, uid),
        )
        cached_assets = jloads(cached_source["assets_json"], {}) if cached_source else {}
        cache_hit = any(
            (cached_assets.get(key) or {}).get("exists")
            and (cached_assets.get(key) or {}).get("text")
            for key in ("clean", "caption", "asr")
        )
    # TikHub 的公开作品详情返回的是发布描述，不是逐字字幕。真实模式下
    # 必须把它当元数据；只有用户明确授权后才下载公开媒体并执行 ASR。
    caption_available = bool(config.DEMO_MODE and not asset)
    from ..datasource import datasource_status
    ds_st = datasource_status()
    plan = {
        "task_type": "extraction",
        "source": {"signal_id": body.signal_id, "account_work_id": body.account_work_id,
                   "title": repair_mojibake(title),
                   "platform": platform, "url": url, "video_id": video_id,
                   "asset_id": body.asset_id,
                   "source_content_id": source_content_id},
        "steps": (["读取本地缓存（命中）", "返回已沉淀的文案资产"] if cache_hit else
                  ["解析来源（RESOLVE_SOURCE）", "检查本地缓存（无命中）",
                   "读取作品描述（仅作元数据，不冒充逐字字幕）",
                   "你已勾选时 → 下载公开媒体 / 读取上传音频 + 百炼 ASR",
                   "校验并分层保存资产"]),
        "cache_hit": cache_hit,
        "caption_available": caption_available,
        "cost": {"数据源": f"作品详情与公开媒体（{ds_st['provider']}）",
                 "需下载媒体": "本地上传无需下载" if asset else "仅在勾选 ASR 授权后",
                 "音频大小": f"{asset.get('size_bytes', 0) / 1024:.1f} KB" if asset else "待解析",
                 "ASR 模型": "阿里云百炼 qwen3-asr-flash" if asset else "按运行模式决定",
                 "预计费用": "由 Provider 实际用量记录", "缓存": "按 SHA-256 去重",
                 "产物": "metadata / 原生字幕 / 原始转写 / (可选)清洗文案",
                 "媒体保留": "转写完成后保留 30 天"},
        "cost_est": 0.0 if cache_hit or asset else 0.10,
        "requires_confirmation": True,
        "authorizations": {"asr_when_no_caption": False},
    }
    return ok(plan)


class JobIn(BaseModel):
    plan: dict
    authorizations: dict = {"asr_when_no_caption": False}


@router.post("/jobs")
def submit_job(body: JobIn):
    uid = current_user_id()
    clean_plan = repair_text_tree(body.plan)
    src = clean_plan.get("source", {})
    wants_asr = bool(body.authorizations.get("asr_when_no_caption"))
    if wants_asr and not src.get("asset_id") and not (
        src.get("platform") and src.get("video_id")
    ):
        raise validation_failed(
            "授权 ASR 前必须保留作品平台和作品 ID；请从原作品重新生成提取计划。"
        )
    job = create_job(uid, "extraction", {
        "title": src.get("title", "未命名来源"),
        "signal_id": src.get("signal_id"),
        "account_work_id": src.get("account_work_id"),
        "platform": src.get("platform", ""),
        "url": src.get("url", ""),
        "video_id": src.get("video_id", ""),
        "asset_id": src.get("asset_id"),
        "source_content_id": src.get("source_content_id"),
        "cache_hit": bool(clean_plan.get("cache_hit")),
        "caption_available": clean_plan.get("caption_available", True),
        "authorizations": {
            "asr_when_no_caption": bool(
                body.authorizations.get("asr_when_no_caption"))
        },
    }, plan=clean_plan, cost_est=clean_plan.get("cost_est", 0.0),
       idempotency_key=clean_plan.get("idempotency_key"), reuse_succeeded=True)
    return ok({"job_id": job["id"], "status": job["status"],
               "note": "任务已创建并排入队列。原始字幕与原始 ASR 不可覆盖。"})


@router.get("/jobs")
def jobs():
    uid = current_user_id()
    return ok(list_jobs(uid, ["extraction", "comment_fetch", "comment_cluster"]))


@router.get("/jobs/duplicates/summary")
def job_duplicates_summary():
    return ok(duplicate_job_summary(current_user_id(), ["extraction"]))


@router.delete("/jobs/{jid}")
def delete_job(jid: str):
    owned_row("jobs", jid, current_user_id(), label="任务")
    delete_job_record(current_user_id(), jid)
    return ok({"deleted": jid, "note": "任务运行记录已删除；已沉淀的原文、媒体和拆解资产仍然保留。"})


@router.post("/jobs/duplicates/cleanup")
def cleanup_job_duplicates():
    result = cleanup_duplicate_jobs(current_user_id(), ["extraction"])
    return ok({**result, "note": f"已清理 {result['deleted_count']} 条重复任务记录；内容资产未删除。"})


@router.get("/jobs/{jid}")
def job_detail(jid: str):
    owned_row("jobs", jid, current_user_id(), label="任务")
    job = get_job(jid)
    return ok(job)


@router.post("/jobs/{jid}/cancel")
def cancel(jid: str):
    owned_row("jobs", jid, current_user_id(), label="任务")
    request_cancel(jid)
    return ok({"note": "已请求取消：将在当前步骤完成后停止，已产生产物保留"})


@router.post("/jobs/{jid}/retry")
def retry(jid: str):
    owned_row("jobs", jid, current_user_id(), label="任务")
    job = get_job(jid)
    if job and job["task_type"] == "extraction":
        hydrated = _hydrate_extraction_input(job["input_json"])
        execute("UPDATE jobs SET input_json=? WHERE id=?", (jdumps(hydrated), jid))
    retry_job(jid)
    return ok({"note": "已从失败点重试（同一 Provider，重试次数已记录）"})


def _hydrate_extraction_input(input_data: dict) -> dict:
    """Repair legacy task inputs from their owned source instead of guessing IDs."""
    hydrated = dict(input_data or {})
    if hydrated.get("asset_id") or (hydrated.get("platform") and hydrated.get("video_id")):
        return hydrated
    plan = None
    if hydrated.get("account_work_id"):
        plan = create_plan(PlanIn(account_work_id=hydrated["account_work_id"]))["data"]
    elif hydrated.get("signal_id"):
        plan = create_plan(PlanIn(signal_id=hydrated["signal_id"]))["data"]
    if plan:
        source = plan.get("source") or {}
        for key in ("title", "signal_id", "account_work_id", "platform", "url",
                    "video_id", "asset_id", "source_content_id"):
            if source.get(key) not in (None, ""):
                hydrated[key] = source[key]
    return hydrated


@router.post("/jobs/{jid}/authorize-asr")
def authorize_asr(jid: str):
    owned_row("jobs", jid, current_user_id(), label="任务")
    job = get_job(jid)
    if not job or job["task_type"] != "extraction":
        raise state_invalid("只有提取任务可以授权 ASR")
    if job["status"] != "WAITING_CONFIRMATION":
        raise state_invalid("任务当前不在等待 ASR 授权状态")
    input_data = _hydrate_extraction_input(job["input_json"])
    if not input_data.get("asset_id") and not (
        input_data.get("platform") and input_data.get("video_id")
    ):
        raise validation_failed(
            "该任务缺少可恢复的作品平台或作品 ID，请从原作品重新创建提取计划。"
        )
    input_data["authorizations"] = {**(input_data.get("authorizations") or {}),
                                    "asr_when_no_caption": True}
    execute(
        "UPDATE jobs SET input_json=?,status='QUEUED',progress=0,"
        "current_step='asr_authorized',output_json=?,error_json=NULL,finished_at=NULL "
        "WHERE id=?",
        (jdumps(input_data), jdumps({"authorization": "asr_when_no_caption"}), jid),
    )
    return ok({"job_id": jid, "note": "ASR 授权已写入原任务，完整来源参数已保留并重新排队。"})


@router.get("/assets/{sc_id}")
def assets(sc_id: str):
    row = owned_row("source_contents", sc_id, current_user_id(), label="来源内容")
    row["assets"] = jloads(row["assets_json"], {})
    text, text_kind = _resolved_text(row["assets"])
    row["transcript"] = {
        "text": text, "kind": text_kind, "chars": len(text),
        "available": bool(text),
    }
    row["files"] = query(
        "SELECT id,kind,mime_type,size_bytes,sha256,immutable,metadata_json,created_at "
        "FROM media_assets WHERE user_id=? AND source_content_id=? ORDER BY created_at",
        (current_user_id(), sc_id),
    )
    row["versions"] = query(
        "SELECT id,artifact_type,version_no,sha256,producer,source_refs_json,created_at "
        "FROM content_artifact_versions WHERE user_id=? AND source_content_id=? "
        "ORDER BY artifact_type,version_no DESC",
        (current_user_id(), sc_id),
    )
    for version in row["versions"]:
        version["source_refs"] = jloads(version.pop("source_refs_json"), [])
    row["analyses"] = query(
        "SELECT id,status,model,cost,created_at FROM analyses "
        "WHERE user_id=? AND source_content_id=? ORDER BY created_at DESC",
        (current_user_id(), sc_id),
    )
    row["evidence"] = evidence_chain(current_user_id(), "source_content", sc_id)
    return ok(row)


ALLOWED_AUDIO_TYPES = {
    "audio/wav", "audio/x-wav", "audio/mpeg", "audio/mp4", "audio/x-m4a",
    "audio/aac", "audio/ogg", "audio/webm", "video/mp4",
}


@router.post("/assets/upload")
async def upload_asset(file: UploadFile = File(...),
                       title: str = Form("本地上传音频"),
                       platform: str = Form("local")):
    uid = current_user_id()
    mime = (file.content_type or "application/octet-stream").lower()
    if mime not in ALLOWED_AUDIO_TYPES:
        raise validation_failed(f"不支持的音频类型：{mime}")
    payload = await file.read(config.MAX_AUDIO_UPLOAD_BYTES + 1)
    if not payload:
        raise validation_failed("上传文件为空")
    if len(payload) > config.MAX_AUDIO_UPLOAD_BYTES:
        raise validation_failed("音频不能超过 10 MB")
    digest = hashlib.sha256(payload).hexdigest()
    existing = query_one(
        "SELECT * FROM media_assets WHERE user_id=? AND sha256=? ORDER BY created_at DESC LIMIT 1",
        (uid, digest),
    )
    if existing:
        return ok({"asset_id": existing["id"],
                   "source_content_id": existing["source_content_id"],
                   "deduplicated": True, "sha256": digest})
    source_id = new_id("SC")
    asset_id = new_id("MA")
    suffix = Path(file.filename or "audio.wav").suffix.lower()
    if not suffix or len(suffix) > 8:
        suffix = ".bin"
    target_dir = config.UPLOAD_DIR / uid
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / f"{asset_id}{suffix}"
    target.write_bytes(payload)
    execute(
        "INSERT INTO source_contents(id,user_id,signal_id,title,platform,author,url,status,assets_json,created_at) "
        "VALUES(?,?,?,?,?,?,?,?,?,?)",
        (source_id, uid, None, repair_mojibake(title.strip()) or "本地上传音频", platform,
         "", "", "uploaded", jdumps({"media": {"exists": True, "asset_id": asset_id}}), now()),
    )
    execute(
        "INSERT INTO media_assets(id,user_id,source_content_id,kind,path,mime_type,size_bytes,sha256,"
        "immutable,metadata_json,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
        (asset_id, uid, source_id, "original_audio", str(target), mime, len(payload),
         digest, 1, jdumps({"original_name": Path(file.filename or "audio").name}), now()),
    )
    evidence(uid, "source_content", source_id, "audio_uploaded",
             refs=[asset_id], payload={"mime_type": mime, "size_bytes": len(payload),
                                       "sha256": digest})
    return ok({"asset_id": asset_id, "source_content_id": source_id,
               "deduplicated": False, "sha256": digest, "size_bytes": len(payload)})


@router.get("/assets")
def list_assets_endpoint():
    rows = query(
        "SELECT s.id AS source_content_id,s.title,s.platform,s.author,s.url,s.status,s.created_at,"
        "s.assets_json,(SELECT COUNT(*) FROM media_assets m WHERE m.user_id=s.user_id "
        "AND m.source_content_id=s.id) AS file_count,"
        "(SELECT COUNT(*) FROM analyses a WHERE a.user_id=s.user_id "
        "AND a.source_content_id=s.id) AS analysis_count,"
        "(SELECT a.id FROM analyses a WHERE a.user_id=s.user_id AND a.source_content_id=s.id "
        "ORDER BY a.created_at DESC LIMIT 1) AS latest_analysis_id "
        "FROM source_contents s WHERE s.user_id=? ORDER BY s.created_at DESC",
        (current_user_id(),),
    )
    for row in rows:
        layers = jloads(row.pop("assets_json", None), {})
        text, text_kind = _resolved_text(layers)
        row["text_available"] = bool(text)
        row["text_kind"] = text_kind
        row["text_chars"] = len(text)
        row["text_preview"] = text[:180]
        row["media_available"] = bool((layers.get("media") or {}).get("exists"))
    return ok(rows)


@router.get("/priority")
def priority():
    return ok(PRIORITY)


class CommentPlanIn(BaseModel):
    source_content_id: str
    title: str
    range: str = "热门 200 条"
    after: str = "保存后聚类"


@router.post("/comment-jobs")
def comment_job(body: CommentPlanIn):
    uid = current_user_id()
    job = create_job(uid, "comment_fetch",
                     {"title": f"评论获取 · {body.title}（{body.range}）",
                      "source_content_id": body.source_content_id,
                      "after": body.after},
                     cost_est=0.08)
    return ok({"job_id": job["id"], "note": "评论获取任务已排队，费用 ¥0.08（演示）"})
