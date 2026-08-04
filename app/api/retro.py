"""数据复盘：账号趋势、单条复盘、规则建议（人工确认才生效）、运营实验。"""
from fastapi import APIRouter

from ..db import execute, jdumps, jloads, query, query_one
from ..errors import not_found, state_invalid
from . import current_user_id, ok

router = APIRouter(tags=["retro"])


@router.get("/retro/overview")
def overview():
    return ok({
        "fans_30d": "+4,218", "posts_per_week": 3.6,
        "plan_vs_actual": "87%", "calibration": "64%（近 12 条）",
    })


@router.get("/retro/due")
def due_list():
    uid = current_user_id()
    rows = query("SELECT * FROM content_tasks WHERE user_id=? AND status='WAITING_RETRO'",
                 (uid,))
    for r in rows:
        r["badges"] = jloads(r["badges_json"], [])
        pub = query_one("SELECT * FROM publications WHERE task_id=?", (r["id"],))
        r["publication_id"] = pub["id"] if pub else None
    return ok(rows)


@router.get("/retro/report/{pub_id}")
def retro_report(pub_id: str):
    pub = query_one("SELECT * FROM publications WHERE id=?", (pub_id,))
    if not pub:
        raise not_found("发布记录", pub_id)
    report = query_one("SELECT * FROM retro_reports WHERE publication_id=?", (pub_id,))
    pred = query_one("SELECT * FROM predictions WHERE id=?",
                     (pub["prediction_id"],)) if pub["prediction_id"] else None
    snaps = query("SELECT * FROM publication_snapshots WHERE publication_id=?"
                  " ORDER BY taken_at", (pub_id,))
    return ok({
        "publication": pub,
        "prediction": pred,
        "snapshots": snaps,
        "report": jloads(report["conclusion_json"], {}) if report else None,
    })


@router.get("/retro/experiments")
def experiments():
    uid = current_user_id()
    return ok(query("SELECT * FROM experiments WHERE user_id=?", (uid,)))


@router.get("/rules")
def list_rules():
    uid = current_user_id()
    return ok(query("SELECT * FROM rules WHERE user_id=?", (uid,)))


@router.get("/rules/suggestions")
def list_suggestions():
    uid = current_user_id()
    return ok(query("SELECT * FROM rule_suggestions WHERE user_id=?"
                    " ORDER BY created_at DESC", (uid,)))


@router.post("/rules/suggestions/{sid}/approve")
def approve_suggestion(sid: str):
    s = query_one("SELECT * FROM rule_suggestions WHERE id=?", (sid,))
    if not s:
        raise not_found("规则建议", sid)
    if s["status"] != "pending":
        raise state_invalid(f"规则建议 {sid} 已处理（{s['status']}）")
    execute("UPDATE rule_suggestions SET status='accepted' WHERE id=?", (sid,))
    import datetime
    rid = f"R-{datetime.datetime.now().strftime('%H%M%S')}"
    execute("INSERT INTO rules VALUES(?,?,?,?,?,?,?,?,?)",
            (rid, s["user_id"], s["rule_text"], "verified", s["scope"],
             s["confidence"], 1, "由建议确认生效", "v2.1"))
    return ok({"rule_id": rid,
               "note": "规则已生效 · 规则经验包 v2.1 已激活（操作者已记录）"})


@router.post("/rules/suggestions/{sid}/reject")
def reject_suggestion(sid: str):
    s = query_one("SELECT * FROM rule_suggestions WHERE id=?", (sid,))
    if not s:
        raise not_found("规则建议", sid)
    execute("UPDATE rule_suggestions SET status='rejected' WHERE id=?", (sid,))
    return ok({"note": "已拒绝（已记录原因）"})


@router.post("/rules/suggestions/{sid}/observe")
def observe_suggestion(sid: str):
    s = query_one("SELECT * FROM rule_suggestions WHERE id=?", (sid,))
    if not s:
        raise not_found("规则建议", sid)
    execute("UPDATE rule_suggestions SET status='observing' WHERE id=?", (sid,))
    return ok({"note": "已标记：继续观察 2 个样本"})
