"""内容计划：库存看板、周计划、产能计算（规则引擎，非 LLM）、发布登记。"""
import datetime
import urllib.parse

from fastapi import APIRouter
from pydantic import BaseModel

from ..config import CONTENT_STATE_LABELS
from ..db import execute, jdumps, jloads, query, query_one, transaction
from ..errors import state_invalid, validation_failed
from ..ids import new_id
from ..repositories import evidence, owned_row
from . import current_user_id, now, ok, today

router = APIRouter(tags=["planning"])

BOARD_COLUMNS = ["CANDIDATE", "SELECTED", "OUTLINING", "DRAFTING", "PRODUCING",
                 "READY_TO_PUBLISH", "PUBLISHED", "WAITING_RETRO"]

ALLOWED_TRANSITIONS = {
    "CANDIDATE": ["SELECTED", "CANCELLED"],
    "SELECTED": ["OUTLINING", "DRAFTING", "CANDIDATE", "PAUSED"],
    "OUTLINING": ["DRAFTING", "SELECTED", "PAUSED"],
    "DRAFTING": ["PRODUCING", "OUTLINING", "PAUSED"],
    "PRODUCING": ["READY_TO_PUBLISH", "DRAFTING", "PAUSED"],
    "READY_TO_PUBLISH": ["PUBLISHED", "PRODUCING", "PAUSED"],
    "PUBLISHED": ["WAITING_RETRO"],
    "WAITING_RETRO": ["RETRO_COMPLETED"],
    "RETRO_COMPLETED": ["ARCHIVED"],
    "PAUSED": ["SELECTED", "OUTLINING", "DRAFTING", "PRODUCING"],
}


def _channel_target(task: dict) -> str | None:
    """Resolve the concrete channel item behind a plan task for exact UI routing."""
    deliverable_id = task.get("deliverable_id")
    if not deliverable_id:
        return None
    if task.get("delivery_kind") == "official_article":
        row = query_one(
            "SELECT id FROM official_articles WHERE deliverable_id=? AND user_id=? AND deleted_at IS NULL "
            "ORDER BY updated_at DESC LIMIT 1",
            (deliverable_id, task.get("user_id")),
        )
    else:
        row = query_one(
            "SELECT id FROM moment_posts WHERE deliverable_id=? AND user_id=? AND deleted_at IS NULL "
            "ORDER BY updated_at DESC,sequence_no LIMIT 1",
            (deliverable_id, task.get("user_id")),
        )
    return (row or {}).get("id")


@router.get("/planning/board")
def board():
    uid = current_user_id()
    rows = query(
        "SELECT t.*,(SELECT p.id FROM publications p WHERE p.task_id=t.id "
        "ORDER BY p.published_at DESC LIMIT 1) publication_id "
        "FROM content_tasks t WHERE t.user_id=?",
        (uid,),
    )
    cols = []
    for st in BOARD_COLUMNS:
        cards = []
        for r in rows:
            if r["status"] == st:
                r["badges"] = [item if isinstance(item, dict) else {
                    "t": str(item), "c": "b-pri" if item == "AI建议" else "b-ok"
                } for item in jloads(r["badges_json"], [])]
                if r.get("visual_status") == "READY":
                    r["badges"].append({"t": "视觉已就绪", "c": "b-ok"})
                elif r.get("visual_status") == "NOT_STARTED" and st in {"PRODUCING", "READY_TO_PUBLISH"}:
                    r["badges"].append({"t": "视觉待制作", "c": "b-warn"})
                r["source"] = jloads(r.get("source_json"), {})
                r["channel_target_id"] = _channel_target(r)
                r["creation_action"] = ("继续渠道制作" if r.get("delivery_kind") not in
                                        (None, "", "public_content") else
                                        ("继续创作" if r.get("project_id") else "开始创作"))
                r["status_label"] = CONTENT_STATE_LABELS.get(st, st)
                cards.append(r)
        cols.append({"state": st, "label": CONTENT_STATE_LABELS[st],
                     "cards": cards, "count": len(cards)})
    done = [r for r in rows if r["status"] in ("RETRO_COMPLETED", "ARCHIVED")]
    for r in done:
        r["badges"] = jloads(r["badges_json"], [])
    return ok({"columns": cols, "done": done})


@router.get("/planning/week")
def week():
    uid = current_user_id()
    rows = query(
        "SELECT t.*,(SELECT p.id FROM publications p WHERE p.task_id=t.id "
        "ORDER BY p.published_at DESC LIMIT 1) publication_id "
        "FROM content_tasks t WHERE t.user_id=?",
        (uid,),
    )
    scheduled_hours = sum(r["estimate_h"] or 0 for r in rows
                          if r["status"] in ("SELECTED", "OUTLINING", "DRAFTING", "PRODUCING"))
    backlog = sum(1 for r in rows if r["status"] in
                  ("SELECTED", "OUTLINING", "DRAFTING", "PRODUCING", "READY_TO_PUBLISH"))
    today_wd = datetime.date.today().weekday()
    monday = datetime.date.today() - datetime.timedelta(days=today_wd)
    days = []
    events = {i: [] for i in range(7)}
    state_colors = {"READY_TO_PUBLISH": "ok", "PUBLISHED": "info",
                    "WAITING_RETRO": "warn", "RETRO_COMPLETED": "ok",
                    "PRODUCING": "info", "DRAFTING": "pri"}
    for row in rows:
        try:
            scheduled_date = datetime.date.fromisoformat(row.get("scheduled_date") or "")
        except ValueError:
            continue
        offset = (scheduled_date - monday).days
        if 0 <= offset < 7:
            channel_target_id = _channel_target(row)
            retro_status = row["status"] in {
                "PUBLISHED", "WAITING_RETRO", "RETRO_COMPLETED", "ARCHIVED"
            }
            events[offset].append({"t": f"{row['title']}（{row['estimate_h'] or 0:g}h）",
                                    "c": state_colors.get(row["status"], "neu"),
                                    "task_id": row["id"], "status": row["status"],
                                    "project_id": row.get("project_id"),
                                    "publication_id": row.get("publication_id"),
                                    "delivery_kind": row.get("delivery_kind"),
                                    "campaign_id": row.get("campaign_id"),
                                    "deliverable_id": row.get("deliverable_id"),
                                    "channel_target_id": channel_target_id,
                                    "action": ("渠道数据 / 复盘" if retro_status and row.get("delivery_kind") not in (None, "", "public_content") else
                                               "继续渠道制作" if row.get("delivery_kind") not in (None, "", "public_content") else
                                               "查看复盘" if retro_status else
                                               "继续创作" if row.get("project_id") else
                                               "开始创作")})
    names = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]
    for i in range(7):
        day = monday + datetime.timedelta(days=i)
        days.append({"name": names[i], "date": day.isoformat(),
                     "today": i == today_wd, "events": events.get(i, [])})
    from .accounts import _cycle_for_user
    cycle = _cycle_for_user()
    capacity = max(0.0, float(cycle.get("hours_per_week") or 0))
    active = [r for r in rows if r["status"] in
              ("SELECTED", "OUTLINING", "DRAFTING", "PRODUCING", "READY_TO_PUBLISH")]
    average = (sum(float(r["estimate_h"] or 0) for r in active) / len(active)) if active else 1.5
    platform_counts = {}
    for row in active:
        key = row.get("platform") or "未指定"
        platform_counts[key] = platform_counts.get(key, 0) + 1
    return ok({
        "hours_total": capacity, "hours_scheduled": round(scheduled_hours, 1),
        "hours_left": round(max(0, capacity - scheduled_hours), 1),
        "suggested_count": max(0, min(
            int(cycle.get("weekly_target") or 0) - len(active),
            int(max(0, capacity - scheduled_hours) // max(average, 0.5)))),
        "weekly_target": int(cycle.get("weekly_target") or 0),
        "completion_rate": float(cycle.get("completion_rate") or 0),
        "completion_samples": int(cycle.get("completion_samples") or 0),
        "scheduled_count": len(active),
        "ratio": " / ".join(f"{name} {count}" for name, count in platform_counts.items()) or "暂无排期",
        "backlog": backlog,
        "note": "由当前运营周期、近8周真实完成率和已排任务共同计算；确认后的AI建议可直接进入创作工坊。",
        "days": days,
    })


@router.post("/planning/tasks/{tid}/start-creation")
def start_task_creation(tid: str):
    """Idempotently connect a scheduled task to a real creation project."""
    uid = current_user_id()
    task = owned_row("content_tasks", tid, uid, label="内容任务")
    if task.get("project_id"):
        existing = query_one("SELECT id FROM creation_projects WHERE id=? AND user_id=?",
                             (task["project_id"], uid))
        if existing:
            return ok({"task_id": tid, "project_id": existing["id"], "existing": True,
                       "note": "已打开该计划绑定的创作项目"})
    source = jloads(task.get("source_json"), {})
    plan_id = task.get("operation_plan_draft_id") or source.get("plan_id")
    item_index = task.get("operation_plan_item_index")
    item = {}
    if plan_id:
        plan_row = query_one("SELECT plan_json FROM operation_plan_drafts WHERE id=? AND user_id=?",
                             (plan_id, uid))
        items = (jloads((plan_row or {}).get("plan_json"), {}).get("items") or [])
        if item_index is not None and 0 <= int(item_index) < len(items):
            item = items[int(item_index)]
    reason = str(item.get("reason") or item.get("rationale") or item.get("description")
                 or source.get("reason") or "按本周运营计划开始创作")
    chain = [f"内容计划 #{tid}"]
    if plan_id:
        chain.insert(0, f"运营建议 #{plan_id}")
    chain.extend(str(ref) for ref in (item.get("evidence_ids") or source.get("evidence_ids") or [])[:8])
    from .creation import ProjectIn, create_project
    with transaction():
        result = create_project(ProjectIn(title=task["title"], source_chain=chain,
                                           goal=reason[:300], plan_task_id=tid))
        project_id = result["data"]["id"]
        stamp = now()
        target_status = ("OUTLINING" if task["status"] in ("CANDIDATE", "SELECTED")
                         else task["status"])
        execute("UPDATE content_tasks SET project_id=?,creation_started_at=?,status=? "
                "WHERE id=? AND user_id=?",
                (project_id, stamp, target_status, tid, uid))
        execute("UPDATE creation_projects SET status=? WHERE id=? AND user_id=?",
                (target_status, project_id, uid))
        evidence(uid, "content_task", tid, "creation_started", refs=[project_id, *(chain[:10])],
                 payload={"operation_plan_draft_id": plan_id,
                          "operation_plan_item_index": item_index})
    return ok({"task_id": tid, "project_id": project_id, "existing": False,
               "note": "创作项目已建立，并保留运营建议、计划任务与证据来源"})


class TaskIn(BaseModel):
    title: str
    platform: str = ""
    scheduled_date: str = ""
    estimate_h: float = 1.5
    project_id: str | None = None


@router.post("/planning/tasks")
def create_task(body: TaskIn):
    uid = current_user_id()
    project = None
    if body.project_id:
        project = owned_row("creation_projects", body.project_id, uid, label="创作项目")
        existing = query_one(
            "SELECT * FROM content_tasks WHERE user_id=? AND project_id=? "
            "AND status<>'CANCELLED' ORDER BY id LIMIT 1",
            (uid, body.project_id),
        )
        if existing:
            return ok({"id": existing["id"], "existing": True,
                       "status": existing["status"],
                       "note": "该创作项目已经在内容计划中，本次没有重复创建"})
    tid = new_id("CT")
    execute("INSERT INTO content_tasks(id,user_id,project_id,title,status,platform,scheduled_date,estimate_h,priority,badges_json,campaign_id,delivery_kind) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
            (tid, uid, body.project_id, body.title, "CANDIDATE", body.platform,
             body.scheduled_date or today(), body.estimate_h, "mid", jdumps([]),
             (project or {}).get("campaign_id"), "public_content"))
    return ok({"id": tid, "existing": False, "status": "CANDIDATE"})


class TaskPatch(BaseModel):
    status: str | None = None
    scheduled_date: str | None = None
    priority: str | None = None


@router.patch("/planning/tasks/{tid}")
def patch_task(tid: str, body: TaskPatch):
    row = owned_row("content_tasks", tid, current_user_id(), label="内容任务")
    if body.status and body.status != row["status"]:
        if row.get("delivery_kind") not in (None, "", "public_content"):
            raise state_invalid("朋友圈和公众号任务请在渠道运营中推进；计划看板会自动同步真实状态。")
        allowed = ALLOWED_TRANSITIONS.get(row["status"], [])
        if body.status not in allowed:
            raise state_invalid(
                f"状态转换不允许：{CONTENT_STATE_LABELS.get(row['status'], row['status'])}"
                f" → {CONTENT_STATE_LABELS.get(body.status, body.status)}。"
                f"状态转换必须由明确事件驱动。")
        execute("UPDATE content_tasks SET status=? WHERE id=?", (body.status, tid))
    if body.scheduled_date:
        execute("UPDATE content_tasks SET scheduled_date=? WHERE id=?",
                (body.scheduled_date, tid))
    if body.priority:
        execute("UPDATE content_tasks SET priority=? WHERE id=?", (body.priority, tid))
    return ok({"id": tid})


class PublicationIn(BaseModel):
    task_id: str
    platform: str
    url: str
    final_title: str
    note: str = ""


class ExternalPublicationIn(BaseModel):
    platform: str
    url: str
    final_title: str
    published_at: str | None = None
    note: str = ""


def _valid_public_url(value: str) -> str:
    url = value.strip()
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise validation_failed("请填写有效的公开发布链接")
    return url


def _published_time(value: str | None) -> datetime.datetime:
    if not value:
        return datetime.datetime.now()
    try:
        parsed = datetime.datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        if parsed.tzinfo:
            parsed = parsed.astimezone().replace(tzinfo=None)
    except (TypeError, ValueError):
        raise validation_failed("发布时间格式无效，请重新选择日期和时间")
    if parsed > datetime.datetime.now() + datetime.timedelta(minutes=10):
        raise validation_failed("已发布内容的发布时间不能晚于当前时间")
    return parsed


def _create_snapshot_schedules(publication_id: str,
                               published_at: datetime.datetime) -> list[dict]:
    schedules = []
    for days, tn in ((1, "T+1"), (3, "T+3"), (7, "T+7")):
        sid = new_id("SS")
        due_at = (published_at + datetime.timedelta(days=days)).isoformat(timespec="seconds")
        execute(
            "INSERT INTO snapshot_schedules(id,publication_id,tn,due_at,status,attempts,last_error,captured_at) "
            "VALUES(?,?,?,?,?,?,?,?)",
            (sid, publication_id, tn, due_at, "PENDING", 0, None, None),
        )
        schedules.append({"id": sid, "tn": tn, "due_at": due_at, "status": "PENDING"})
    return schedules


@router.post("/publications")
def register_publication(body: PublicationIn):
    uid = current_user_id()
    task = owned_row("content_tasks", body.task_id, uid, label="内容任务")
    if task["status"] != "READY_TO_PUBLISH":
        raise state_invalid("只有“待发布”的内容任务可以登记发布")
    url = _valid_public_url(body.url)
    prediction = None
    if task.get("project_id"):
        prediction = query_one(
            "SELECT * FROM predictions WHERE project_id=? AND locked=1 "
            "ORDER BY version_no DESC,locked_at DESC LIMIT 1",
            (task["project_id"],),
        )
    if not prediction:
        raise state_invalid("发布前必须先保存并锁定判断，才能形成可校准的证据链")
    if not task.get("final_draft_version_id") and prediction.get("draft_version_id"):
        latest = query_one(
            "SELECT id FROM draft_versions WHERE project_id=? ORDER BY version_no DESC LIMIT 1",
            (task.get("project_id"),),
        )
        if latest and latest["id"] == prediction["draft_version_id"]:
            # Compatibility for READY tasks created before the final-draft binding column existed.
            execute(
                "UPDATE content_tasks SET final_draft_version_id=?,finalized_at=COALESCE(finalized_at,?) "
                "WHERE id=? AND user_id=?",
                (latest["id"], prediction.get("locked_at") or now(), task["id"], uid),
            )
            task["final_draft_version_id"] = latest["id"]
    if prediction.get("draft_version_id") != task.get("final_draft_version_id"):
        raise state_invalid("待发布任务与锁定预测对应的稿件版本不一致，请回到创作工坊重新确认定稿")
    existing = query_one("SELECT id FROM publications WHERE user_id=? AND url=?", (uid, url))
    if existing:
        raise state_invalid("该发布链接已经登记过，请直接进入数据复盘查看")
    pid = new_id("P")
    published_at = datetime.datetime.now()
    with transaction():
        execute(
            "INSERT INTO publications(id,user_id,project_id,task_id,platform,url,published_at,"
            "final_title,note,prediction_id,entry_mode,source_json,visual_deliverable_id,campaign_id,deliverable_id) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (pid, uid, task["project_id"], body.task_id, body.platform, url,
             published_at.isoformat(timespec="seconds"), body.final_title, body.note,
             prediction["id"], "planned", jdumps({"kind": "planned_publication",
                                                    "visual_deliverable_id": task.get("visual_deliverable_id")}),
             task.get("visual_deliverable_id"), task.get("campaign_id"), task.get("deliverable_id")),
        )
        execute("UPDATE content_tasks SET status='PUBLISHED' WHERE id=? AND user_id=?",
                (body.task_id, uid))
        if task.get("project_id"):
            execute("UPDATE creation_projects SET status='PUBLISHED' WHERE id=? AND user_id=?",
                    (task["project_id"], uid))
        schedules = _create_snapshot_schedules(pid, published_at)
    evidence(uid, "publication", pid, "publication_registered",
             refs=[value for value in (task["id"], prediction["id"], task.get("visual_deliverable_id")) if value],
             payload={"platform": body.platform, "url": body.url,
                      "snapshot_schedule_ids": [s["id"] for s in schedules]})
    return ok({"id": pid,
               "schedules": schedules,
               "note": "发布登记完成，已创建 T+1/T+3/T+7 快照计划；无授权采集能力时会等待手动录入，不会伪造数据。"})


@router.post("/publications/import")
def import_external_publication(body: ExternalPublicationIn):
    """Record content published outside the creation workflow without inventing a prediction."""
    uid = current_user_id()
    title = body.final_title.strip()
    platform = body.platform.strip()
    if not title:
        raise validation_failed("请填写已发布内容的标题")
    if not platform:
        raise validation_failed("请填写发布平台")
    url = _valid_public_url(body.url)
    published_at = _published_time(body.published_at)
    existing = query_one("SELECT id,task_id FROM publications WHERE user_id=? AND url=?", (uid, url))
    if existing:
        return ok({"id": existing["id"], "task_id": existing.get("task_id"),
                   "deduplicated": True,
                   "note": "该发布链接已经记录，本次没有重复创建；可直接进入数据复盘。"})
    task_id = new_id("CT")
    publication_id = new_id("P")
    campaign_id = new_id("CMP")
    source = {"kind": "external_publication", "prediction_available": False,
              "note": "内容在工作台外完成并发布"}
    with transaction():
        stamp = now()
        execute(
            "INSERT INTO content_campaigns(id,user_id,title,core_topic,source_kind,source_ref_type,source_ref_id,"
            "source_chain_json,objective,audience,status,created_at,updated_at,archived_at) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,NULL)",
            (campaign_id, uid, title, title, "external_publication", "publication", publication_id,
             jdumps([url]), "记录并复盘随手发布内容", "", "ACTIVE", stamp, stamp),
        )
        execute(
            "INSERT INTO content_tasks(id,user_id,project_id,title,status,platform,scheduled_date,"
            "estimate_h,priority,badges_json,source_json,finalized_at,campaign_id,delivery_kind) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (task_id, uid, None, title, "PUBLISHED", platform, published_at.date().isoformat(),
             0, "mid", jdumps(["随手发布", "无发布前预测"]), jdumps(source),
             published_at.isoformat(timespec="seconds"), campaign_id, "external_public_content"),
        )
        execute(
            "INSERT INTO publications(id,user_id,project_id,task_id,platform,url,published_at,"
            "final_title,note,prediction_id,entry_mode,source_json,campaign_id) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (publication_id, uid, None, task_id, platform, url,
             published_at.isoformat(timespec="seconds"), title, body.note, None,
             "external", jdumps(source), campaign_id),
        )
        schedules = _create_snapshot_schedules(publication_id, published_at)
        evidence(uid, "publication", publication_id, "external_publication_imported",
                 refs=[task_id], payload={**source, "platform": platform, "url": url,
                                          "published_at": published_at.isoformat(timespec="seconds")})
    return ok({"id": publication_id, "task_id": task_id, "deduplicated": False,
               "schedules": schedules,
               "note": "已补录这条随手发布内容并创建 T+1/T+3/T+7 快照计划；因发布前没有盲预测，复盘会明确标记为“仅实绩复盘”。"})
