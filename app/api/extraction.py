"""内容提取：素材上传、执行计划、可靠任务与证据资产。"""
import hashlib
from pathlib import Path

from fastapi import APIRouter, File, Form, UploadFile
from pydantic import BaseModel

from .. import config
from ..db import execute, jdumps, jloads, query, query_one
from ..errors import validation_failed
from ..ids import new_id
from ..jobs import create_job, get_job, list_jobs, request_cancel, retry_job
from ..repositories import evidence, evidence_chain, owned_row
from . import current_user_id, now, ok

router = APIRouter(prefix="/extraction", tags=["extraction"])

PRIORITY = ["有效的本地缓存", "用户导入的正文", "平台原生正文",
            "平台原生字幕", "用户主动选择的 ASR"]


class PlanIn(BaseModel):
    signal_id: str | None = None
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
    if body.asset_id:
        asset = owned_row("media_assets", body.asset_id, uid, label="素材")
        if asset.get("source_content_id"):
            source = owned_row("source_contents", asset["source_content_id"], uid,
                               label="来源内容")
            title, platform, url = source["title"], source["platform"], source["url"]
        else:
            title, platform, url = body.title or "本地音频", body.platform, body.url
    elif body.signal_id:
        sig = owned_row("signals", body.signal_id, uid, label="信号")
        title, platform = sig["title"], sig["platform"]
        url = sig["url"]
        # 对标扫描产生的信号：解析出真实 video_id（供字幕接口调用）
        if body.signal_id.startswith(("SG-HOT-", "SG-BM-")):
            suffix = body.signal_id.rsplit("-", 1)[-1]
            w = query_one("SELECT video_id FROM benchmark_works WHERE video_id LIKE ?",
                          (f"%{suffix}",))
            video_id = w["video_id"] if w else ""
    else:
        title = body.title or "手动粘贴来源"
        platform, url = body.platform, body.url
    caption_available = False if asset else "偷懒" not in (title or "")
    from ..datasource import datasource_status
    ds_st = datasource_status()
    plan = {
        "task_type": "extraction",
        "source": {"signal_id": body.signal_id, "title": title,
                   "platform": platform, "url": url, "video_id": video_id,
                   "asset_id": body.asset_id,
                   "source_content_id": asset.get("source_content_id") if asset else None},
        "steps": ["解析来源（RESOLVE_SOURCE）", "检查本地缓存（无命中）",
                  "调用字幕接口获取原生字幕",
                  "若无字幕且你已勾选 → 读取上传音频 + 百炼 ASR",
                  "校验并分层保存资产"],
        "caption_available": caption_available,
        "cost": {"数据源": f"字幕接口（{ds_st['provider']}）",
                 "需下载媒体": "本地上传无需下载" if asset else "仅无字幕时",
                 "音频大小": f"{asset.get('size_bytes', 0) / 1024:.1f} KB" if asset else "待解析",
                 "ASR 模型": "阿里云百炼 qwen3-asr-flash" if asset else "按运行模式决定",
                 "预计费用": "由 Provider 实际用量记录", "缓存": "按 SHA-256 去重",
                 "产物": "metadata / 原生字幕 / 原始转写 / (可选)清洗文案",
                 "媒体保留": "转写完成后保留 30 天"},
        "cost_est": 0.0 if asset else 0.10,
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
    src = body.plan.get("source", {})
    job = create_job(uid, "extraction", {
        "title": src.get("title", "未命名来源"),
        "signal_id": src.get("signal_id"),
        "platform": src.get("platform", ""),
        "url": src.get("url", ""),
        "video_id": src.get("video_id", ""),
        "asset_id": src.get("asset_id"),
        "source_content_id": src.get("source_content_id"),
        "caption_available": body.plan.get("caption_available", True),
        "authorizations": {
            "asr_when_no_caption": bool(
                body.authorizations.get("asr_when_no_caption"))
        },
    }, plan=body.plan, cost_est=body.plan.get("cost_est", 0.0),
       idempotency_key=body.plan.get("idempotency_key"))
    return ok({"job_id": job["id"], "status": job["status"],
               "note": "任务已创建并排入队列。原始字幕与原始 ASR 不可覆盖。"})


@router.get("/jobs")
def jobs():
    uid = current_user_id()
    return ok(list_jobs(uid, ["extraction", "comment_fetch", "comment_cluster"]))


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
    retry_job(jid)
    return ok({"note": "已从失败点重试（同一 Provider，重试次数已记录）"})


@router.get("/assets/{sc_id}")
def assets(sc_id: str):
    row = owned_row("source_contents", sc_id, current_user_id(), label="来源内容")
    row["assets"] = jloads(row["assets_json"], {})
    row["files"] = query(
        "SELECT id,kind,mime_type,size_bytes,sha256,immutable,metadata_json,created_at "
        "FROM media_assets WHERE user_id=? AND source_content_id=? ORDER BY created_at",
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
        (source_id, uid, None, title.strip() or "本地上传音频", platform,
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
        "SELECT m.id,m.source_content_id,m.kind,m.mime_type,m.size_bytes,m.sha256,m.created_at,"
        "s.title,s.status FROM media_assets m LEFT JOIN source_contents s ON s.id=m.source_content_id "
        "WHERE m.user_id=? ORDER BY m.created_at DESC",
        (current_user_id(),),
    )
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
