"""内容雷达：信号列表与卡片动作。高成本动作只生成计划，不直接执行。"""
import datetime
import hashlib

from fastapi import APIRouter
from pydantic import BaseModel, Field

from ..db import execute, jdumps, jloads, query, query_one
from ..errors import not_found
from ..jobs import create_job
from .. import radar_sources as source_service
from . import current_user_id, ok

router = APIRouter(prefix="/radar", tags=["radar"])

STATUS_LABELS = {"new": "新发现", "observed": "已观察", "ignored": "已忽略",
                 "processed": "已处理"}


@router.get("/signals")
def list_signals(filter: str = "all"):
    uid = current_user_id()
    rows = query("SELECT * FROM signals WHERE user_id=? ORDER BY fetched_at DESC",
                 (uid,))
    out = []
    for r in rows:
        if filter != "all" and r["category"] != filter:
            continue
        if r["status"] == "ignored" and filter == "all":
            r["ignored"] = True
        r["metrics"] = jloads(r["metrics_json"], {})
        r["status_label"] = STATUS_LABELS.get(r["status"], r["status"])
        out.append(r)
    return ok(out)


def _signal_or_404(sid: str) -> dict:
    row = query_one("SELECT * FROM signals WHERE id=? AND user_id=?",
                    (sid, current_user_id()))
    if not row:
        raise not_found("信号", sid)
    return row


@router.post("/signals/{sid}/observe")
def observe_signal(sid: str):
    _signal_or_404(sid)
    execute("UPDATE signals SET status='observed' WHERE id=?", (sid,))
    return ok({"note": "已加入观察：将持续同步公开数据快照（免费）"})


@router.post("/signals/{sid}/ignore")
def ignore_signal(sid: str):
    _signal_or_404(sid)
    execute("UPDATE signals SET status='ignored' WHERE id=?", (sid,))
    return ok({"note": "已忽略：相似内容将减少推荐（可撤销）"})


@router.post("/signals/{sid}/no-similar")
def no_similar(sid: str):
    _signal_or_404(sid)
    execute("UPDATE signals SET status='ignored' WHERE id=?", (sid,))
    return ok({"note": "已减少相似内容推荐"})


@router.post("/signals/{sid}/to-topic")
def to_topic(sid: str):
    row = _signal_or_404(sid)
    return ok({"note": f"已从信号 #{sid} 创建选题候选", "title": row["title"]})


class RadarSourceIn(BaseModel):
    kind: str
    name: str = Field(min_length=1, max_length=100)
    platform: str | None = None
    frequency_hours: int = Field(default=24, ge=1, le=168)
    config: dict = Field(default_factory=dict)


@router.get("/sources")
def radar_sources():
    return ok(source_service.list_sources(current_user_id()))


@router.post("/sources")
def add_radar_source(body: RadarSourceIn):
    if body.kind not in {"tikhub_hot", "external_feed", "keyword_watch"}:
        from ..errors import validation_failed
        raise validation_failed("来源类型只能是平台热榜、行业连接器或关键词监听")
    if body.kind == "tikhub_hot" and body.platform not in {
        "douyin", "xhs", "bilibili", "kuaishou", "weibo", "zhihu"
    }:
        from ..errors import validation_failed
        raise validation_failed("请选择已支持的平台热点源")
    if body.kind == "external_feed" and not str(body.config.get("url") or "").strip():
        from ..errors import validation_failed
        raise validation_failed("外部数据源必须填写 RSS 或 JSON Feed URL")
    config = dict(body.config)
    if body.kind == "external_feed" and not config.get("connector"):
        probed = source_service.probe_source(str(config.get("url") or ""))
        config.update(probed["config"])
    if body.kind == "keyword_watch":
        if body.platform not in {"douyin", "xhs", "tiktok"}:
            from ..errors import validation_failed
            raise validation_failed("关键词监听当前支持抖音、小红书和 TikTok")
        keywords = [str(value).strip() for value in config.get("keywords", [])
                    if str(value).strip()]
        if not keywords:
            from ..errors import validation_failed
            raise validation_failed("关键词监听至少需要一个关键词")
        config["keywords"] = keywords[:10]
    source = source_service.create_source(
        current_user_id(), body.kind, body.name, body.platform,
        config, body.frequency_hours,
    )
    return ok({**source, "note": "数据源已保存。只有执行同步后才会写入真实信号。"})


class SourceProbeIn(BaseModel):
    url: str = Field(min_length=1, max_length=2048)


@router.post("/sources/probe")
def probe_radar_source(body: SourceProbeIn):
    return ok(source_service.probe_source(body.url))


@router.post("/sources/{source_id}/sync", status_code=202)
def sync_radar_source(source_id: str):
    source = source_service.get_source(source_id, current_user_id())
    job = create_job(current_user_id(), "radar_source_sync", {
        "title": f"同步雷达源 · {source['name']}", "source_id": source_id,
    })
    return ok({"job_id": job["id"], "status": job["status"],
               "reused": bool(job.get("reused")),
               "note": ("该来源已有同步任务在执行，已打开原任务。" if job.get("reused")
                        else "雷达源正在后台同步。")})


class RadarSourceUpdate(BaseModel):
    enabled: bool
    frequency_hours: int = Field(default=24, ge=1, le=168)


@router.put("/sources/{source_id}")
def update_radar_source(source_id: str, body: RadarSourceUpdate):
    source_service.get_source(source_id, current_user_id())
    import datetime
    next_run = ((datetime.datetime.now() + datetime.timedelta(hours=body.frequency_hours))
                .isoformat(timespec="seconds") if body.enabled else None)
    execute("UPDATE radar_sources SET enabled=?,frequency_hours=?,next_run_at=? WHERE id=?",
            (1 if body.enabled else 0, body.frequency_hours, next_run, source_id))
    return ok({"note": "数据源调度已保存。", "next_run_at": next_run})


@router.delete("/sources/{source_id}")
def delete_radar_source(source_id: str):
    source_service.get_source(source_id, current_user_id())
    execute("DELETE FROM radar_sources WHERE id=?", (source_id,))
    return ok({"note": "数据源已删除；已经沉淀的雷达信号和内容资产继续保留。"})


@router.get("/capabilities")
def radar_capabilities():
    return ok([
        {"id": "benchmark", "name": "对标账号", "status": "ready",
         "description": "监控指定作者的新作与高表现内容", "cta": "管理对标账号"},
        {"id": "platform_hot", "name": "平台热榜", "status": "ready",
         "description": "按平台榜单同步公共热点", "cta": "添加平台热榜"},
        {"id": "external", "name": "行业数据源", "status": "ready",
         "description": "连接 AIHot、RSS、Atom 或 JSON Feed", "cta": "添加行业连接器"},
        {"id": "kw", "name": "关键词监听", "status": "ready",
         "description": "在已支持平台搜索指定主题", "cta": "创建关键词规则"},
        {"id": "fb", "name": "用户反馈", "status": "manual",
         "description": "沉淀评论、私域问题和客户反馈", "cta": "录入用户反馈"},
        {"id": "revive", "name": "历史起量", "status": "ready",
         "description": "依据真实发布快照识别旧内容增长", "cta": "扫描历史快照"},
        {"id": "manual", "name": "手动关注", "status": "ready",
         "description": "保存临时发现的链接、备注和选题线索", "cta": "添加手动关注"},
    ])


class ManualSignalIn(BaseModel):
    category: str = Field(pattern="^(manual|fb)$")
    title: str = Field(min_length=1, max_length=500)
    url: str = Field(default="", max_length=2048)
    platform: str = Field(default="手动录入", max_length=50)
    note: str = Field(default="", max_length=2000)


@router.post("/signals/manual")
def create_manual_signal(body: ManualSignalIn):
    uid = current_user_id()
    fingerprint = hashlib.sha256(
        f"{uid}\0{body.category}\0{body.url or body.title}".encode("utf-8")
    ).hexdigest()[:24]
    signal_id = f"SG-MAN-{fingerprint}"
    existing = query_one("SELECT id FROM signals WHERE id=? AND user_id=?", (signal_id, uid))
    if existing:
        return ok({"id": signal_id, "existing": True, "note": "这条内容已经在雷达中。"})
    created_at = datetime.datetime.now().isoformat(timespec="seconds")
    execute(
        "INSERT INTO signals(id,user_id,source_type,platform,author,title,url,published_at,"
        "metrics_json,rel_perf,relevance,timeliness,status,category,source,fetched_at,content_id,radar_source_id) "
        "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (signal_id, uid, "用户反馈" if body.category == "fb" else "手动关注",
         body.platform, "我", body.title.strip(), body.url.strip(), "", jdumps({"note": body.note}),
         "", 100, "刚刚录入", "new", body.category, "manual", created_at, None, None),
    )
    return ok({"id": signal_id, "existing": False, "note": "已保存到雷达并永久沉淀。"})


@router.post("/signals/derive-historical-rise")
def derive_historical_rise():
    uid = current_user_id()
    publications = query(
        "SELECT p.id,p.platform,p.url,p.final_title,p.published_at FROM publications p "
        "WHERE p.user_id=?", (uid,))
    created = 0
    for publication in publications:
        snapshots = query(
            "SELECT plays,taken_at FROM publication_snapshots WHERE publication_id=? "
            "ORDER BY taken_at", (publication["id"],))
        if len(snapshots) < 2:
            continue
        start, end = int(snapshots[0]["plays"] or 0), int(snapshots[-1]["plays"] or 0)
        growth = end - start
        if growth < max(100, int(start * 0.2)):
            continue
        signal_id = "SG-REV-" + hashlib.sha256(
            f"{publication['id']}\0{snapshots[-1]['taken_at']}".encode("utf-8")
        ).hexdigest()[:24]
        if query_one("SELECT id FROM signals WHERE id=?", (signal_id,)):
            continue
        execute(
            "INSERT INTO signals(id,user_id,source_type,platform,author,title,url,published_at,"
            "metrics_json,rel_perf,relevance,timeliness,status,category,source,fetched_at,content_id,radar_source_id) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (signal_id, uid, "历史起量", publication["platform"], "我的账号",
             publication["final_title"] or "历史作品", publication["url"] or "",
             publication["published_at"] or "", jdumps({"start_plays": start, "plays": end,
                                                           "growth": growth}),
             f"新增播放 {growth}", 100, "最新快照", "new", "revive", "local_snapshots",
             datetime.datetime.now().isoformat(timespec="seconds"), None, None),
        )
        created += 1
    return ok({"created": created, "checked": len(publications),
               "note": f"已检查 {len(publications)} 条真实发布记录，新增 {created} 条历史起量信号。"})
