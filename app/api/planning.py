"""内容计划：库存看板、周计划、产能计算（规则引擎，非 LLM）、发布登记。"""
import datetime
import urllib.parse

from fastapi import APIRouter
from pydantic import BaseModel

from ..config import CONTENT_STATE_LABELS
from ..db import execute, jdumps, jloads, query, query_one
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


@router.get("/planning/board")
def board():
    uid = current_user_id()
    rows = query("SELECT * FROM content_tasks WHERE user_id=?", (uid,))
    cols = []
    for st in BOARD_COLUMNS:
        cards = []
        for r in rows:
            if r["status"] == st:
                r["badges"] = jloads(r["badges_json"], [])
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
    rows = query("SELECT * FROM content_tasks WHERE user_id=?", (uid,))
    scheduled_hours = sum(r["estimate_h"] or 0 for r in rows
                          if r["status"] in ("SELECTED", "OUTLINING", "DRAFTING", "PRODUCING"))
    backlog = sum(1 for r in rows if r["status"] in
                  ("SELECTED", "OUTLINING", "DRAFTING", "PRODUCING", "READY_TO_PUBLISH"))
    today_wd = datetime.date.today().weekday()
    monday = datetime.date.today() - datetime.timedelta(days=today_wd)
    days = []
    events = {i: [] for i in range(7)}
    state_colors = {"READY_TO_PUBLISH": "ok", "WAITING_RETRO": "warn",
                    "PRODUCING": "info", "DRAFTING": "pri"}
    for row in rows:
        try:
            scheduled_date = datetime.date.fromisoformat(row.get("scheduled_date") or "")
        except ValueError:
            continue
        offset = (scheduled_date - monday).days
        if 0 <= offset < 7:
            events[offset].append({"t": f"{row['title']}（{row['estimate_h'] or 0:g}h）",
                                   "c": state_colors.get(row["status"], "neu"),
                                   "task_id": row["id"], "status": row["status"]})
    names = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]
    for i in range(7):
        day = monday + datetime.timedelta(days=i)
        days.append({"name": names[i], "date": day.isoformat(),
                     "today": i == today_wd, "events": events.get(i, [])})
    cost_row = query_one("SELECT value_json FROM app_settings WHERE key='cost'")
    cost_values = jloads(cost_row["value_json"], {}) if cost_row else {}
    capacity = max(0.0, float(cost_values.get("weekly_capacity_hours", 14)))
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
        "suggested_count": max(0, int(max(0, capacity - scheduled_hours) // max(average, 0.5))),
        "scheduled_count": len(active),
        "ratio": " / ".join(f"{name} {count}" for name, count in platform_counts.items()) or "暂无排期",
        "backlog": backlog,
        "note": "由真实任务与系统设置中的每周产能计算（非 LLM）：剩余产能 ÷ 当前任务平均工时。",
        "days": days,
    })


class TaskIn(BaseModel):
    title: str
    platform: str = ""
    scheduled_date: str = ""
    estimate_h: float = 1.5
    project_id: str | None = None


@router.post("/planning/tasks")
def create_task(body: TaskIn):
    uid = current_user_id()
    if body.project_id:
        owned_row("creation_projects", body.project_id, uid, label="创作项目")
    tid = new_id("CT")
    execute("INSERT INTO content_tasks VALUES(?,?,?,?,?,?,?,?,?,?)",
            (tid, uid, body.project_id, body.title, "CANDIDATE", body.platform,
             body.scheduled_date or today(), body.estimate_h, "mid", jdumps([])))
    return ok({"id": tid})


class TaskPatch(BaseModel):
    status: str | None = None
    scheduled_date: str | None = None
    priority: str | None = None


@router.patch("/planning/tasks/{tid}")
def patch_task(tid: str, body: TaskPatch):
    row = owned_row("content_tasks", tid, current_user_id(), label="内容任务")
    if body.status and body.status != row["status"]:
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


@router.post("/publications")
def register_publication(body: PublicationIn):
    uid = current_user_id()
    task = owned_row("content_tasks", body.task_id, uid, label="内容任务")
    if task["status"] != "READY_TO_PUBLISH":
        raise state_invalid("只有“待发布”的内容任务可以登记发布")
    parsed = urllib.parse.urlparse(body.url.strip())
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise validation_failed("请填写有效的公开发布链接")
    prediction = None
    if task.get("project_id"):
        prediction = query_one(
            "SELECT * FROM predictions WHERE project_id=? AND locked=1",
            (task["project_id"],),
        )
    if not prediction:
        raise state_invalid("发布前必须先保存并锁定判断，才能形成可校准的证据链")
    pid = new_id("P")
    execute("INSERT INTO publications VALUES(?,?,?,?,?,?,?,?,?,?)",
            (pid, uid, task["project_id"], body.task_id, body.platform, body.url,
             now(), body.final_title, body.note, prediction["id"]))
    execute("UPDATE content_tasks SET status='PUBLISHED' WHERE id=?", (body.task_id,))
    published_at = datetime.datetime.now()
    schedules = []
    for days, tn in ((1, "T+1"), (3, "T+3"), (7, "T+7")):
        sid = new_id("SS")
        due_at = (published_at + datetime.timedelta(days=days)).isoformat(timespec="seconds")
        execute(
            "INSERT INTO snapshot_schedules(id,publication_id,tn,due_at,status,attempts,last_error,captured_at) "
            "VALUES(?,?,?,?,?,?,?,?)",
            (sid, pid, tn, due_at, "PENDING", 0, None, None),
        )
        schedules.append({"id": sid, "tn": tn, "due_at": due_at, "status": "PENDING"})
    evidence(uid, "publication", pid, "publication_registered",
             refs=[task["id"], prediction["id"]],
             payload={"platform": body.platform, "url": body.url,
                      "snapshot_schedule_ids": [s["id"] for s in schedules]})
    return ok({"id": pid,
               "schedules": schedules,
               "note": "发布登记完成，已创建 T+1/T+3/T+7 快照计划；无授权采集能力时会等待手动录入，不会伪造数据。"})
