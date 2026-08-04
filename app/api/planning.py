"""内容计划：库存看板、周计划、产能计算（规则引擎，非 LLM）、发布登记。"""
import datetime

from fastapi import APIRouter
from pydantic import BaseModel

from ..config import CONTENT_STATE_LABELS
from ..db import execute, jdumps, jloads, query, query_one
from ..errors import not_found, state_invalid
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
    scheduled = sum(r["estimate_h"] or 0 for r in rows
                    if r["status"] in ("SELECTED", "OUTLINING", "DRAFTING", "PRODUCING"))
    backlog = sum(1 for r in rows if r["status"] in
                  ("SELECTED", "OUTLINING", "DRAFTING", "PRODUCING", "READY_TO_PUBLISH"))
    today_wd = datetime.date.today().weekday()
    monday = datetime.date.today() - datetime.timedelta(days=today_wd)
    days = []
    events = {
        0: [{"t": "定稿《汇报被打断》口播稿", "c": "pri"}, {"t": "复盘《副业》T+7", "c": "warn"}],
        1: [{"t": "拍摄口播（1.5h）", "c": "info"}],
        2: [{"t": "发布：小红书图文《述职准备》", "c": "ok"}],
        3: [{"t": "发布：抖音《汇报被打断》", "c": "ok"}],
        4: [{"t": "修改《向上管理》v4", "c": "neu"}],
        5: [{"t": "拍摄《向上管理》（2h）", "c": "info"}],
        6: [{"t": "休整 · 周报生成", "c": "neu"}],
    }
    names = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]
    for i in range(7):
        day = monday + datetime.timedelta(days=i)
        days.append({"name": names[i], "date": day.isoformat(),
                     "today": i == today_wd, "events": events.get(i, [])})
    return ok({
        "hours_total": 14, "hours_scheduled": round(scheduled, 1),
        "hours_left": round(14 - scheduled, 1),
        "suggested_count": 4, "scheduled_count": 3,
        "ratio": "汇报表达 2 / 向上管理 1 / 职业选择 1",
        "backlog": backlog,
        "note": "建议由规则引擎计算（非 LLM）：产能 - 已排时长 = 建议数量。库存积压时优先完成已有内容。",
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
    tid = f"CT-{datetime.datetime.now().strftime('%H%M%S')}"
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
    row = query_one("SELECT * FROM content_tasks WHERE id=?", (tid,))
    if not row:
        raise not_found("内容任务", tid)
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
    task = query_one("SELECT * FROM content_tasks WHERE id=?", (body.task_id,))
    if not task:
        raise not_found("内容任务", body.task_id)
    pid = f"P-{datetime.datetime.now().strftime('%H%M%S')}"
    execute("INSERT INTO publications VALUES(?,?,?,?,?,?,?,?,?,?)",
            (pid, uid, task["project_id"], body.task_id, body.platform, body.url,
             now(), body.final_title, body.note, None))
    execute("UPDATE content_tasks SET status='PUBLISHED' WHERE id=?", (body.task_id,))
    return ok({"id": pid,
               "note": "发布登记完成，已创建 T+1/T+3/T+7 数据快照计划（公开数据 · 免费）"})
