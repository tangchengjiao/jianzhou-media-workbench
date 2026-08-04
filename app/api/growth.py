"""成长记录：时间线、经验库、成长报告导出。"""
import datetime

from fastapi import APIRouter
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel

from ..db import execute, query
from . import current_user_id, ok

router = APIRouter(prefix="/growth", tags=["growth"])


@router.get("/timeline")
def timeline():
    uid = current_user_id()
    return ok(query("SELECT * FROM growth_events WHERE user_id=?"
                    " ORDER BY date DESC", (uid,)))


class EventIn(BaseModel):
    title: str
    desc: str


@router.post("/events")
def add_event(body: EventIn):
    uid = current_user_id()
    eid = f"ge-{datetime.datetime.now().strftime('%H%M%S')}"
    execute("INSERT INTO growth_events VALUES(?,?,?,?,?,?)",
            (eid, uid, datetime.date.today().isoformat(), body.title,
             body.desc, "normal"))
    return ok({"id": eid})


@router.get("/export", response_class=PlainTextResponse)
def export_report():
    uid = current_user_id()
    events = query("SELECT * FROM growth_events WHERE user_id=? ORDER BY date DESC",
                   (uid,))
    rules = query("SELECT * FROM rules WHERE user_id=?", (uid,))
    lines = ["# 成长报告（导出 · 不含密钥）", "",
             f"导出时间：{datetime.datetime.now().isoformat(timespec='seconds')}", "",
             "## 时间线"]
    for e in events:
        lines.append(f"- {e['date']} · {e['title']}：{e['desc']}")
    lines += ["", "## 经验库（规则经验包）"]
    for r in rules:
        lines.append(f"- [{r['status']}] {r['text']}（置信度 {r['confidence']} · {r['note']}）")
    return "\n".join(lines)
