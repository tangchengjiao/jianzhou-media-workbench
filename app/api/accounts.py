"""账号运营：真实账号绑定、同步、快照与运营周期。"""
from __future__ import annotations

import datetime
import hashlib
import re
import statistics
import urllib.parse

import httpx

from fastapi import APIRouter
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from ..datasource import PLATFORM_LABELS, SUPPORTED_PLATFORMS, get_datasource
from ..db import execute, jdumps, jloads, query, query_one, transaction
from ..errors import not_found, validation_failed
from ..jobs import create_job
from ..ids import new_id
from . import current_user_id, ok

router = APIRouter(tags=["accounts"])

CYCLE = {
    "name": "2026 Q3 · 稳定更新期", "range": None,
    "goal": "稳住周更 4 条，验证「汇报表达」方向",
    "platforms": "抖音 + 小红书", "hours_per_week": 14,
    "complexity": "中等（口播 + 图文）",
    "promotion": "否 · 重点实验：开头钩子 A/B",
    "rhythm": "稳定更新", "completion_rate": 0.87,
}

RHYTHMS = ["高频冲刺", "稳定更新", "低频维护", "内容测试",
           "产品预热", "转化推广", "暂停休整", "自定义"]
RHYTHM_TARGETS = {"高频冲刺": 6, "稳定更新": 4, "低频维护": 2, "内容测试": 3,
                  "产品预热": 5, "转化推广": 5, "暂停休整": 0, "自定义": 0,
                  "未设置": 0}


def _account_out(row: dict) -> dict:
    out = dict(row)
    extra = jloads(out.pop("extra_json", None), {})
    out["extra"] = extra
    out["platform_label"] = PLATFORM_LABELS.get(out["platform"], out["platform"])
    out["display_name"] = extra.get("name") or out["handle"]
    raw_homepage = str(extra.get("link") or "").strip()
    parsed = urllib.parse.urlparse(raw_homepage)
    out["homepage"] = (raw_homepage if parsed.scheme == "https" and parsed.netloc else "")
    out["account_identifier"] = extra.get("uid", "")
    out["can_open_homepage"] = bool(out["homepage"])
    out["avatar"] = extra.get("avatar", "")
    out["bio"] = extra.get("bio", "")
    out["video_count"] = int(extra.get("video_count") or 0)
    out["likes_count"] = int(extra.get("likes") or 0)
    out["followers"] = int(out.get("followers") or 0)
    availability = extra.get("metric_availability") or {}
    out["followers_available"] = bool(
        availability.get("fans", out["platform"] != "channels")
    )
    out["video_count_available"] = bool(
        availability.get("video_count", out["video_count"] > 0)
    )
    return out


def _work_score(work: dict) -> int:
    return (int(work.get("likes") or 0)
            + int(work.get("comments") or 0) * 3
            + int(work.get("collects") or 0) * 2
            + int(work.get("shares") or 0) * 4)


def _account_works_out(aid: str) -> list[dict]:
    account_owner_id = _owned_account(aid)["user_id"]
    works = query(
        "SELECT w.id,w.video_id,w.title,w.cover,w.url,w.create_time,w.plays,w.likes,w.comments,w.collects,"
        "w.shares,w.duration,w.first_seen_at,w.last_seen_at,w.performance_label,w.review_note,"
        "l.source_content_id,s.status source_status,s.assets_json,"
        "(SELECT COUNT(*) FROM analyses x WHERE x.source_content_id=l.source_content_id) analysis_count,"
        "(SELECT COUNT(*) FROM creation_projects p WHERE p.source_content_id=l.source_content_id) project_count,"
        "(SELECT COUNT(*) FROM visual_batches vb JOIN creation_projects p ON p.id=vb.creation_project_id "
        " WHERE p.source_content_id=l.source_content_id AND vb.deleted_at IS NULL) visual_count,"
        "(SELECT COUNT(*) FROM publications pub JOIN creation_projects p ON p.id=pub.project_id "
        " WHERE p.source_content_id=l.source_content_id) publication_count,"
        "(SELECT COUNT(*) FROM retro_reports rr JOIN publications pub ON pub.id=rr.publication_id "
        " JOIN creation_projects p ON p.id=pub.project_id WHERE p.source_content_id=l.source_content_id) retro_count "
        "FROM account_works w LEFT JOIN account_work_source_links l ON l.account_work_id=w.id "
        "LEFT JOIN source_contents s ON s.id=l.source_content_id WHERE w.account_id=?",
        (aid,),
    )
    positive = sorted(score for score in (_work_score(work) for work in works) if score > 0)
    median = float(statistics.median(positive)) if positive else 0
    for work in works:
        from ..asset_center import asset_id
        from ..cover_cache import browser_needs_cover_proxy
        work["cover_display_url"] = (
            f"/api/accounts/{aid}/works/{work['id']}/cover"
            if browser_needs_cover_proxy(str(work.get("cover") or ""))
            else str(work.get("cover") or "")
        )
        score = _work_score(work)
        work["performance_score"] = score
        if not score or not median:
            work["performance_band"] = "unknown"
        elif score >= median * 1.5:
            work["performance_band"] = "high"
        elif score <= median * 0.5:
            work["performance_band"] = "low"
        else:
            work["performance_band"] = "mid"
        assets = jloads(work.pop("assets_json", None), {})
        work["has_text"] = any(
            (assets.get(key) or {}).get("exists") and str((assets.get(key) or {}).get("text") or "").strip()
            for key in ("clean", "caption", "asr")
        )
        work["content_stage"] = (
            "已复盘" if int(work.get("retro_count") or 0) else
            "已发布" if int(work.get("publication_count") or 0) else
            "已有视觉" if int(work.get("visual_count") or 0) else
            "已创作" if int(work.get("project_count") or 0) else
            "已拆解" if int(work.get("analysis_count") or 0) else
            "已提取" if work["has_text"] else "仅元数据"
        )
        work["asset_record_id"] = asset_id(account_owner_id, "account_work", work["id"], "own_work")
    works.sort(key=lambda work: (int(work.get("create_time") or 0),
                                 str(work.get("video_id") or "")), reverse=True)
    return works


def _douyin_work_id(value: str) -> tuple[str, str]:
    raw = value.strip()
    direct = re.search(r"(?:/video/|modal_id=)(\d{10,})", raw)
    if direct:
        video_id = direct.group(1)
        return video_id, f"https://www.douyin.com/video/{video_id}"
    if re.fullmatch(r"\d{10,}", raw):
        return raw, f"https://www.douyin.com/video/{raw}"
    link_match = re.search(r"https://[^\s]+", raw)
    current = link_match.group(0).rstrip("，。；;）)") if link_match else ""
    for _ in range(6):
        parsed = urllib.parse.urlparse(current)
        host = (parsed.hostname or "").lower()
        if parsed.scheme != "https" or not any(
            host == suffix or host.endswith("." + suffix)
            for suffix in ("douyin.com", "iesdouyin.com", "dy.tt")
        ):
            break
        direct = re.search(r"(?:/video/|modal_id=)(\d{10,})", current)
        if direct:
            video_id = direct.group(1)
            return video_id, f"https://www.douyin.com/video/{video_id}"
        try:
            response = httpx.get(current, timeout=12.0, follow_redirects=False,
                                 headers={"User-Agent": "Mozilla/5.0"})
        except httpx.HTTPError:
            break
        location = response.headers.get("location")
        if not (300 <= response.status_code < 400 and location):
            break
        current = urllib.parse.urljoin(current, location)
    raise validation_failed("没有从链接中识别到抖音作品 ID，请粘贴完整作品分享链接")


def _owned_account(aid: str) -> dict:
    row = query_one("SELECT * FROM accounts WHERE id=? AND user_id=?",
                    (aid, current_user_id()))
    if not row:
        raise not_found("账号", aid)
    return row


def _cycle_for_user() -> dict:
    uid = current_user_id()
    row = query_one("SELECT value_json FROM app_settings WHERE key=?",
                    (f"account_cycle:{uid}",))
    stored = jloads(row["value_json"], {}) if row else {}
    from .. import config
    if stored:
        cycle = {**CYCLE, **stored}
    elif not config.DEMO_MODE:
        cycle = {"name": "尚未配置运营周期", "range": None, "goal": "",
                "platforms": "", "hours_per_week": 0, "complexity": "",
                "promotion": "", "rhythm": "未设置"}
    else:
        cycle = dict(CYCLE)
    cutoff = (datetime.date.today() - datetime.timedelta(days=56)).isoformat()
    progress = query_one(
        "SELECT COUNT(*) planned,SUM(CASE WHEN status='PUBLISHED' THEN 1 ELSE 0 END) completed "
        "FROM content_tasks WHERE user_id=? AND date(scheduled_date)>=?", (uid, cutoff)) or {}
    planned = int(progress.get("planned") or 0)
    completed = int(progress.get("completed") or 0)
    completion_rate = round(completed / planned, 3) if planned else 0
    rhythm_target = RHYTHM_TARGETS.get(str(cycle.get("rhythm") or "未设置"), 0)
    hours = float(cycle.get("hours_per_week") or 0)
    capacity_target = max(1, int(hours // 2.5)) if hours > 0 else 0
    weekly_target = min(rhythm_target, capacity_target) if rhythm_target and capacity_target else rhythm_target
    cycle_hash = hashlib.sha256(jdumps({k: cycle.get(k) for k in
        ("goal", "platforms", "hours_per_week", "complexity", "promotion", "rhythm")}).encode("utf-8")).hexdigest()
    return {**cycle, "completion_rate": completion_rate, "completion_samples": planned,
            "weekly_target": weekly_target, "cycle_hash": cycle_hash, "rhythms": RHYTHMS,
            "effects": [
                f"运营建议默认围绕“{cycle.get('goal') or '待设置目标'}”生成",
                f"本周建议上限为 {weekly_target} 条，受“{cycle.get('rhythm') or '未设置'}”与 {hours:g} 小时产能共同约束",
                f"优先适配 {cycle.get('platforms') or '待设置平台'}；制作复杂度按“{cycle.get('complexity') or '待设置'}”过滤",
            ]}


@router.get("/accounts")
def list_accounts():
    rows = query("SELECT * FROM accounts WHERE user_id=? ORDER BY rowid DESC",
                 (current_user_id(),))
    return ok([_account_out(row) for row in rows])


class AccountIn(BaseModel):
    platform: str
    link: str = Field(default="", max_length=2048)
    handle: str = Field(default="", max_length=300)  # 兼容旧客户端
    display_name: str = Field(default="", max_length=200)
    sync_frequency: str = Field(default="每 6 小时", max_length=50)
    source_mode: str = Field(default="provider", pattern="^(provider|manual)$")


@router.post("/accounts")
def add_account(body: AccountIn):
    """绑定自有账号；Provider 模式必须完成真实解析与概况读取后才落库。"""
    uid = current_user_id()
    if body.platform not in SUPPORTED_PLATFORMS:
        raise validation_failed(f"暂不支持平台：{body.platform}")
    source = (body.link or body.handle).strip()
    if not source:
        raise validation_failed("请填写账号主页链接或平台账号 ID")

    if body.source_mode == "manual":
        resolved = {"uid": source, "platform": body.platform, "link": body.link.strip()}
        overview = {
            "name": body.display_name.strip() or source, "avatar": "", "bio": "",
            "fans": 0, "likes": 0, "video_count": 0,
        }
        provider_name = "manual"
        status = "manual"
        note = "已绑定手动导入账号；请到资料中心导入平台后台数据"
    else:
        ds = get_datasource()
        if hasattr(ds, "require_configured"):
            ds.require_configured()
        resolved = ds.resolve_account(source, body.platform)
        overview = ds.account_overview(resolved["uid"], resolved["platform"])
        provider_name = ds.name
        status = "ok"
        note = f"已通过 {provider_name} 解析并读取公开账号资料"

    existing = None
    for row in query("SELECT * FROM accounts WHERE user_id=? AND platform=?",
                     (uid, resolved["platform"])):
        if jloads(row.get("extra_json"), {}).get("uid") == resolved["uid"]:
            existing = row
            break
    aid = existing["id"] if existing else new_id("acc")
    resolved_link = str(resolved.get("link") or "").strip()
    parsed_link = urllib.parse.urlparse(resolved_link)
    safe_link = resolved_link if parsed_link.scheme == "https" and parsed_link.netloc else ""
    extra = {
        "uid": resolved["uid"], "link": safe_link, "input": source,
        "name": overview.get("name") or body.display_name.strip() or source,
        "avatar": overview.get("avatar") or "", "bio": overview.get("bio") or "",
        "likes": int(overview.get("likes") or 0),
        "video_count": int(overview.get("video_count") or 0),
        "metric_availability": overview.get("availability") or {},
        "bound": True,
    }
    now = datetime.datetime.now().isoformat(timespec="seconds")
    last_sync_at = now if body.source_mode == "provider" else None
    handle = "@" + extra["name"].lstrip("@")
    with transaction():
        if existing:
            execute(
                "UPDATE accounts SET handle=?,kind='own',status=?,sync_frequency=?,"
                "last_sync_at=?,followers=?,bound_provider=?,note=?,extra_json=? WHERE id=?",
                (handle, status, body.sync_frequency, last_sync_at,
                 int(overview.get("fans") or 0), provider_name, note, jdumps(extra), aid),
            )
        else:
            execute("INSERT INTO accounts VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                    (aid, uid, resolved["platform"], handle, "own", status,
                     body.sync_frequency, last_sync_at, int(overview.get("fans") or 0),
                     provider_name, note, jdumps(extra)))
        if body.source_mode == "provider":
            date = datetime.date.today().isoformat()
            execute("DELETE FROM account_snapshots WHERE account_id=? AND date=?", (aid, date))
            execute("INSERT INTO account_snapshots VALUES(?,?,?,?,?,?)",
                    (new_id("ss"), aid, date, int(overview.get("fans") or 0),
                     int(overview.get("video_count") or 0),
                     jdumps({"source": provider_name, "likes": extra["likes"]})))
    saved = _account_out(query_one("SELECT * FROM accounts WHERE id=?", (aid,)))
    return ok({"note": ("账号已更新" if existing else "账号已添加") + "；" + note,
               "existing": bool(existing), "account": saved, "id": aid})


@router.get("/accounts/{aid}")
def account_detail(aid: str):
    row = _owned_account(aid)
    snapshots = query("SELECT * FROM account_snapshots WHERE account_id=? ORDER BY date",
                      (aid,))
    for snapshot in snapshots:
        snapshot["metrics"] = jloads(snapshot.pop("metrics_json", None), {})
    works = _account_works_out(aid)[:200]
    return ok({**_account_out(row), "snapshots": snapshots, "works": works,
               "works_cached": len(works)})


class AccountUpdateIn(BaseModel):
    sync_frequency: str = Field(min_length=1, max_length=50)


@router.patch("/accounts/{aid}")
def update_account(aid: str, body: AccountUpdateIn):
    _owned_account(aid)
    execute("UPDATE accounts SET sync_frequency=? WHERE id=?",
            (body.sync_frequency, aid))
    return ok({"note": "同步频率已保存", "account": _account_out(
        query_one("SELECT * FROM accounts WHERE id=?", (aid,)))})


@router.delete("/accounts/{aid}")
def delete_account(aid: str):
    row = _owned_account(aid)
    with transaction():
        execute("DELETE FROM account_snapshots WHERE account_id=?", (aid,))
        execute("DELETE FROM accounts WHERE id=?", (aid,))
    return ok({"note": f"已删除账号 {row['platform']} {row['handle']}（历史快照已清理）"})


@router.post("/accounts/{aid}/sync")
def sync_account(aid: str, count: int = 60):
    row = _owned_account(aid)
    if row["bound_provider"] == "manual":
        return ok({"manual": True,
                   "note": "该账号为手动导入模式，请到资料中心导入平台后台数据"})
    job = create_job(row["user_id"], "account_sync",
                     {"account_id": aid, "title": f"账号同步 · {row['platform']}",
                      "count": max(1, min(int(count), 200)), "sort": "latest"},
                     cost_est=0.0)
    execute("UPDATE accounts SET status='syncing',note='同步任务执行中' WHERE id=?", (aid,))
    return ok({"job_id": job["id"],
               "note": "同步任务已创建；完成后会刷新账号资料、最近作品和快照"})


@router.get("/accounts/{aid}/works")
def account_works(aid: str):
    _owned_account(aid)
    return ok(_account_works_out(aid)[:200])


@router.get("/accounts/{aid}/works/{work_id}/cover")
def account_work_cover(aid: str, work_id: str):
    """Serve a browser-compatible cached JPEG for HEIC/HEIF provider covers."""
    _owned_account(aid)
    work = query_one(
        "SELECT id,cover FROM account_works WHERE id=? AND account_id=?",
        (work_id, aid),
    )
    if not work:
        raise not_found("作品", work_id)
    cover = str(work.get("cover") or "").strip()
    from ..cover_cache import browser_needs_cover_proxy, cached_jpeg_cover
    if not browser_needs_cover_proxy(cover):
        raise validation_failed("该作品封面无需本地格式转换")
    path = cached_jpeg_cover(cover)
    return FileResponse(
        path, media_type="image/jpeg", filename=f"{work_id}.jpg",
        headers={"Cache-Control": "public, max-age=2592000, immutable"},
    )


class AccountWorkReviewIn(BaseModel):
    performance_label: str = Field(pattern="^(unrated|good|average|needs_review)$")
    review_note: str = Field(default="", max_length=500)


@router.patch("/accounts/{aid}/works/{work_id}")
def review_account_work(aid: str, work_id: str, body: AccountWorkReviewIn):
    _owned_account(aid)
    work = query_one("SELECT id FROM account_works WHERE id=? AND account_id=?",
                     (work_id, aid))
    if not work:
        raise not_found("作品", work_id)
    execute("UPDATE account_works SET performance_label=?,review_note=? WHERE id=?",
            (body.performance_label, body.review_note.strip(), work_id))
    return ok({"note": "作品表现标记已保存", "work_id": work_id,
               "performance_label": body.performance_label,
               "review_note": body.review_note.strip()})


class AccountWorkImportIn(BaseModel):
    link: str = Field(min_length=1, max_length=2048)
    title: str = Field(default="", max_length=300)


@router.post("/accounts/{aid}/works/import")
def import_account_work(aid: str, body: AccountWorkImportIn):
    account = _owned_account(aid)
    if account["platform"] != "douyin":
        raise validation_failed("当前补录入口先支持抖音作品链接；其他平台请使用账号同步")
    video_id, url = _douyin_work_id(body.link)
    from ..account_works import persist_account_works
    stats = persist_account_works(aid, [{
        "video_id": video_id, "url": url,
        "title": body.title.strip() or "平台未返回标题",
        "create_time": 0,
    }], datetime.datetime.now().isoformat(timespec="seconds"))
    work = query_one("SELECT id FROM account_works WHERE account_id=? AND video_id=?",
                     (aid, video_id))
    return ok({"work_id": work["id"], "video_id": video_id, **stats,
               "note": "作品已补录并沉淀到本地；可以立即提取文案，后续同步会补齐公开指标。"})


@router.post("/accounts/{aid}/works/{work_id}/share-url")
def account_work_share_url(aid: str, work_id: str):
    account = _owned_account(aid)
    work = query_one("SELECT * FROM account_works WHERE id=? AND account_id=?",
                     (work_id, aid))
    if not work:
        raise not_found("作品", work_id)
    existing = str(work.get("url") or "").strip()
    parsed = urllib.parse.urlparse(existing)
    if parsed.scheme == "https" and parsed.netloc:
        return ok({"url": existing, "cached": True, "cost": 0.0})
    ds = get_datasource()
    resolved = ds.work_share_url(str(work["video_id"]), str(account["platform"]))
    url = str(resolved.get("url") or "").strip()
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme != "https" or not parsed.netloc:
        from ..errors import AppError
        raise AppError("SHARE_URL_NOT_AVAILABLE", "平台没有返回可公开访问的作品链接。",
                       stage="work_share_url", provider=getattr(ds, "name", ""))
    execute("UPDATE account_works SET url=? WHERE id=?", (url, work_id))
    if float(resolved.get("cost") or 0):
        from ..jobs import record_usage
        record_usage(account["user_id"], "生成视频号作品分享链接", getattr(ds, "name", ""),
                     "work_share_url", "", float(resolved["cost"]), "1 次")
    return ok({"url": url, "cached": False, "cost": float(resolved.get("cost") or 0),
               "note": "作品分享链接已生成并缓存在本地。"})


@router.get("/accounts/{aid}/snapshots")
def account_snapshots(aid: str):
    _owned_account(aid)
    return ok(query("SELECT * FROM account_snapshots WHERE account_id=? ORDER BY date",
                    (aid,)))


@router.get("/accounts/cycle/current")
def get_cycle():
    return ok(_cycle_for_user())


class CycleIn(BaseModel):
    rhythm: str | None = None
    hours_per_week: float | None = Field(default=None, ge=0, le=168)
    goal: str | None = Field(default=None, max_length=300)
    platforms: str | None = Field(default=None, max_length=200)
    complexity: str | None = Field(default=None, max_length=200)
    promotion: str | None = Field(default=None, max_length=200)


@router.post("/accounts/cycle/current")
def update_cycle(body: CycleIn):
    updates = body.model_dump(exclude_none=True)
    if "rhythm" in updates and updates["rhythm"] not in RHYTHMS:
        raise validation_failed("更新节奏不在可选范围内")
    current = _cycle_for_user()
    current.pop("rhythms", None)
    current.update(updates)
    uid = current_user_id()
    for derived in ("completion_rate", "completion_samples", "weekly_target", "cycle_hash", "effects"):
        current.pop(derived, None)
    stamp = datetime.datetime.now().isoformat(timespec="seconds")
    with transaction():
        execute("INSERT OR REPLACE INTO app_settings(key,value_json) VALUES(?,?)",
                (f"account_cycle:{uid}", jdumps(current)))
        stale_count = execute(
            "UPDATE operation_plan_drafts SET status='STALE',stale_at=? "
            "WHERE user_id=? AND status='DRAFT'", (stamp, uid))
    refreshed = _cycle_for_user()
    return ok({"note": f"运营周期已保存并立即作用于顾问与排期；{stale_count} 份旧草案已归档为过期",
               "stale_plans": stale_count, "effects": refreshed["effects"], "cycle": refreshed})


@router.get("/accounts/suggestions/list")
def account_suggestions():
    accounts = list_accounts()["data"]
    if not accounts:
        return ok([])
    suggestions = []
    if any(account["status"] == "error" for account in accounts):
        suggestions.append({"tag": "阻塞", "cls": "b-err",
                            "text": "存在账号同步失败；跨平台判断前应先恢复数据源。",
                            "basis": "依据：账号同步状态"})
    if any(account["status"] == "manual" for account in accounts):
        suggestions.append({"tag": "待补数据", "cls": "b-warn",
                            "text": "手动账号尚未导入后台数据，趋势与运营建议不会使用猜测值。",
                            "basis": "依据：账号绑定方式"})
    if any(account["status"] == "ok" for account in accounts):
        suggestions.append({"tag": "已连接", "cls": "b-ok",
                            "text": "已连接账号可按设定频率同步公开概况并沉淀每日快照。",
                            "basis": "依据：真实账号绑定与快照记录"})
    return ok(suggestions)
