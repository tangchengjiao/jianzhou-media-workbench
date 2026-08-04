"""内容提取：执行计划（先计划后执行）、任务管理、资产分层。"""
from fastapi import APIRouter
from pydantic import BaseModel

from ..db import execute, jdumps, jloads, query, query_one
from ..errors import not_found
from ..jobs import create_job, get_job, list_jobs, request_cancel, retry_job
from . import current_user_id, ok

router = APIRouter(prefix="/extraction", tags=["extraction"])

PRIORITY = ["有效的本地缓存", "用户导入的正文", "平台原生正文",
            "平台原生字幕", "用户主动选择的 ASR"]


class PlanIn(BaseModel):
    signal_id: str | None = None
    title: str | None = None
    platform: str = ""
    url: str = ""


@router.post("/plans")
def create_plan(body: PlanIn):
    """生成执行计划，不立即执行。无字幕继续 ASR 默认不授权。"""
    video_id = ""
    if body.signal_id:
        sig = query_one("SELECT * FROM signals WHERE id=?", (body.signal_id,))
        if not sig:
            raise not_found("信号", body.signal_id)
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
    caption_available = "偷懒" not in (title or "")  # 演示规则：该条无原生字幕
    from ..datasource import datasource_status
    ds_st = datasource_status()
    plan = {
        "task_type": "extraction",
        "source": {"signal_id": body.signal_id, "title": title,
                   "platform": platform, "url": url, "video_id": video_id},
        "steps": ["解析来源（RESOLVE_SOURCE）", "检查本地缓存（无命中）",
                  "调用字幕接口获取原生字幕",
                  "若无字幕且你已勾选 → 下载媒体 + 本地 ASR",
                  "校验并分层保存资产"],
        "caption_available": caption_available,
        "cost": {"数据源": f"字幕接口（{ds_st['provider']}）",
                 "需下载媒体": "仅无字幕时 · 约 84 MB",
                 "音频时长": "3 分 12 秒",
                 "ASR 模型": "本地 faster-whisper small · ¥0",
                 "预计费用": "¥0.10（字幕接口）", "缓存": "无命中",
                 "产物": "metadata / 原生字幕 / 原始转写 / (可选)清洗文案",
                 "媒体保留": "转写完成后保留 30 天"},
        "cost_est": 0.10,
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
        "caption_available": body.plan.get("caption_available", True),
        "authorizations": {
            "asr_when_no_caption": bool(
                body.authorizations.get("asr_when_no_caption"))
        },
    }, plan=body.plan, cost_est=body.plan.get("cost_est", 0.0))
    return ok({"job_id": job["id"], "status": job["status"],
               "note": "任务已创建并排入队列。原始字幕与原始 ASR 不可覆盖。"})


@router.get("/jobs")
def jobs():
    uid = current_user_id()
    return ok(list_jobs(uid, ["extraction", "comment_fetch", "comment_cluster"]))


@router.get("/jobs/{jid}")
def job_detail(jid: str):
    job = get_job(jid)
    if not job:
        raise not_found("任务", jid)
    return ok(job)


@router.post("/jobs/{jid}/cancel")
def cancel(jid: str):
    request_cancel(jid)
    return ok({"note": "已请求取消：将在当前步骤完成后停止，已产生产物保留"})


@router.post("/jobs/{jid}/retry")
def retry(jid: str):
    job = get_job(jid)
    if not job:
        raise not_found("任务", jid)
    retry_job(jid)
    return ok({"note": "已从失败点重试（同一 Provider，重试次数已记录）"})


@router.get("/assets/{sc_id}")
def assets(sc_id: str):
    row = query_one("SELECT * FROM source_contents WHERE id=?", (sc_id,))
    if not row:
        raise not_found("来源内容", sc_id)
    row["assets"] = jloads(row["assets_json"], {})
    return ok(row)


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
