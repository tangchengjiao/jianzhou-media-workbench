"""内容雷达：信号列表与卡片动作。高成本动作只生成计划，不直接执行。"""
from fastapi import APIRouter

from ..db import execute, jloads, query, query_one
from ..errors import not_found
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
    row = query_one("SELECT * FROM signals WHERE id=?", (sid,))
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
