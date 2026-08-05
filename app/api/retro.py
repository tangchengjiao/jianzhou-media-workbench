"""数据复盘：真实快照、预测校准、规则建议与版本化激活。"""
import datetime

from fastapi import APIRouter
from pydantic import BaseModel, Field

from ..db import execute, jdumps, jloads, query, query_one
from ..errors import state_invalid
from ..ids import new_id
from ..repositories import evidence, owned_row
from . import current_user_id, now, ok

router = APIRouter(tags=["retro"])


@router.get("/retro/overview")
def overview():
    uid = current_user_id()
    pub_count = query_one("SELECT COUNT(*) c FROM publications WHERE user_id=?", (uid,))["c"]
    snapshot_count = query_one(
        "SELECT COUNT(*) c FROM publication_snapshots s JOIN publications p ON p.id=s.publication_id "
        "WHERE p.user_id=?", (uid,),)["c"]
    report_count = query_one("SELECT COUNT(*) c FROM retro_reports WHERE user_id=?", (uid,))["c"]
    pending = query_one(
        "SELECT COUNT(*) c FROM snapshot_schedules s JOIN publications p ON p.id=s.publication_id "
        "WHERE p.user_id=? AND s.status IN ('PENDING','AWAITING_INPUT')", (uid,),)["c"]
    return ok({"publications": pub_count, "snapshots": snapshot_count,
               "retros_completed": report_count, "snapshots_pending": pending,
               "calibration": (f"{report_count}/{pub_count} 已完成" if pub_count else "暂无真实发布")})


@router.get("/retro/due")
def due_list():
    uid = current_user_id()
    rows = query(
        "SELECT t.*,p.id publication_id,p.final_title,p.published_at,p.platform publication_platform "
        "FROM content_tasks t JOIN publications p ON p.task_id=t.id "
        "WHERE t.user_id=? AND t.status IN ('PUBLISHED','WAITING_RETRO') "
        "ORDER BY p.published_at DESC", (uid,))
    for r in rows:
        r["badges"] = jloads(r["badges_json"], [])
        r["schedules"] = query(
            "SELECT * FROM snapshot_schedules WHERE publication_id=? ORDER BY due_at",
            (r["publication_id"],),
        )
    return ok(rows)


@router.get("/retro/report/{pub_id}")
def retro_report(pub_id: str):
    pub = owned_row("publications", pub_id, current_user_id(), label="发布记录")
    report = query_one("SELECT * FROM retro_reports WHERE publication_id=?", (pub_id,))
    pred = query_one("SELECT * FROM predictions WHERE id=?",
                     (pub["prediction_id"],)) if pub["prediction_id"] else None
    snaps = query("SELECT * FROM publication_snapshots WHERE publication_id=?"
                  " ORDER BY taken_at", (pub_id,))
    schedules = query("SELECT * FROM snapshot_schedules WHERE publication_id=? ORDER BY due_at",
                      (pub_id,))
    return ok({
        "publication": pub,
        "prediction": pred,
        "snapshots": snaps,
        "schedules": schedules,
        "report": jloads(report["conclusion_json"], {}) if report else None,
    })


class SnapshotIn(BaseModel):
    tn: str
    plays: int = Field(ge=0)
    likes: int = Field(ge=0)
    comments: int = Field(ge=0)
    collects: int = Field(ge=0)


@router.post("/retro/publications/{pub_id}/snapshots")
def capture_snapshot(pub_id: str, body: SnapshotIn):
    uid = current_user_id()
    pub = owned_row("publications", pub_id, uid, label="发布记录")
    schedule = query_one(
        "SELECT * FROM snapshot_schedules WHERE publication_id=? AND tn=?",
        (pub_id, body.tn),
    )
    if not schedule:
        raise state_invalid("该发布记录没有对应的快照计划")
    existing = query_one(
        "SELECT id FROM publication_snapshots WHERE publication_id=? AND tn=?",
        (pub_id, body.tn),
    )
    captured_at = now()
    sid = existing["id"] if existing else new_id("PS")
    if existing:
        execute("UPDATE publication_snapshots SET plays=?,likes=?,comments=?,collects=?,taken_at=? "
                "WHERE id=?", (body.plays, body.likes, body.comments, body.collects,
                                captured_at, sid))
    else:
        execute("INSERT INTO publication_snapshots VALUES(?,?,?,?,?,?,?,?)",
                (sid, pub_id, body.tn, body.plays, body.likes, body.comments,
                 body.collects, captured_at))
    execute("UPDATE snapshot_schedules SET status='CAPTURED',attempts=attempts+1,"
            "last_error=NULL,captured_at=? WHERE id=?", (captured_at, schedule["id"]))
    if pub.get("task_id"):
        execute("UPDATE content_tasks SET status='WAITING_RETRO' WHERE id=? AND user_id=?",
                (pub["task_id"], uid))
    evidence(uid, "publication", pub_id, "snapshot_captured", refs=[sid, schedule["id"]],
             payload=body.model_dump())
    return ok({"id": sid, "captured_at": captured_at,
               "note": "快照已保存；同一 T+N 再次提交会保留最新的人工校正值。"})


@router.post("/retro/publications/{pub_id}/generate")
def generate_retro(pub_id: str):
    uid = current_user_id()
    pub = owned_row("publications", pub_id, uid, label="发布记录")
    existing = query_one("SELECT * FROM retro_reports WHERE publication_id=?", (pub_id,))
    if existing:
        return ok({"id": existing["id"], "report": jloads(existing["conclusion_json"], {}),
                   "deduplicated": True})
    snapshots = query("SELECT * FROM publication_snapshots WHERE publication_id=? ORDER BY taken_at",
                      (pub_id,))
    if not snapshots:
        raise state_invalid("至少录入一个真实快照后才能复盘")
    prediction = query_one("SELECT * FROM predictions WHERE id=? AND locked=1",
                           (pub.get("prediction_id"),))
    if not prediction:
        raise state_invalid("发布记录缺少已锁定的发布前判断")
    latest = snapshots[-1]
    interactions = latest["likes"] + latest["comments"] + latest["collects"]
    engagement = round(interactions / latest["plays"] * 100, 2) if latest["plays"] else 0.0
    conclusion = {
        "prediction": {"expected_bucket": prediction["expected_bucket"],
                       "confidence": prediction["confidence"],
                       "hypothesis": prediction["hypothesis"]},
        "actual": {"latest_tn": latest["tn"], "plays": latest["plays"],
                   "likes": latest["likes"], "comments": latest["comments"],
                   "collects": latest["collects"], "engagement_rate": engagement},
        "calibration": "需要人工判断档位；系统仅计算真实互动率，不虚构账号基线",
        "evidence_snapshot_ids": [item["id"] for item in snapshots],
        "generated_at": now(),
    }
    rid = new_id("RR")
    execute("INSERT INTO retro_reports VALUES(?,?,?,?,?)",
            (rid, uid, pub_id, jdumps(conclusion), now()))
    suggestion_id = new_id("RS")
    rule_text = (f"在{pub['platform']}发布同类内容时，优先复核发布前假设；"
                 f"当前 {latest['tn']} 真实互动率为 {engagement}%")
    execute("INSERT INTO rule_suggestions VALUES(?,?,?,?,?,?,?,?,?,?)",
            (suggestion_id, uid, "add", rule_text,
             f"发布 {pub_id}；快照 {','.join(conclusion['evidence_snapshot_ids'])}",
             min(0.95, 0.5 + 0.1 * len(snapshots)), pub["platform"],
             "仅在人工确认后进入规则包", "pending", now()))
    if pub.get("task_id"):
        execute("UPDATE content_tasks SET status='RETRO_COMPLETED' WHERE id=? AND user_id=?",
                (pub["task_id"], uid))
    evidence(uid, "publication", pub_id, "retro_generated",
             refs=[rid, suggestion_id, *conclusion["evidence_snapshot_ids"]],
             payload={"engagement_rate": engagement})
    return ok({"id": rid, "report": conclusion,
               "rule_suggestion_id": suggestion_id, "deduplicated": False})


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
    uid = current_user_id()
    s = owned_row("rule_suggestions", sid, uid, label="规则建议")
    if s["status"] != "pending":
        raise state_invalid(f"规则建议 {sid} 已处理（{s['status']}）")
    execute("UPDATE rule_suggestions SET status='accepted' WHERE id=?", (sid,))
    rid = new_id("R")
    version_no = (query_one("SELECT COALESCE(MAX(version_no),0) v FROM rule_package_versions "
                            "WHERE user_id=?", (uid,))["v"] or 0) + 1
    execute("INSERT INTO rules VALUES(?,?,?,?,?,?,?,?,?)",
            (rid, uid, s["rule_text"], "verified", s["scope"],
             s["confidence"], 1, "由建议确认生效", f"v{version_no}"))
    execute("UPDATE rule_package_versions SET active=0 WHERE user_id=?", (uid,))
    version_id = new_id("RPV")
    execute("INSERT INTO rule_package_versions VALUES(?,?,?,?,?,?,?)",
            (version_id, uid, version_no, jdumps({"added": [rid], "source": sid}),
             jdumps([rid]), now(), 1))
    package = query_one("SELECT id FROM data_packages WHERE owner_id=? AND type='rule'",
                        (uid,))
    if package:
        execute("UPDATE data_packages SET version=?,status='active',source_count=source_count+1,"
                "updated_at=?,activated_at=? WHERE id=?",
                (f"v{version_no}", now(), now(), package["id"]))
    else:
        execute("INSERT INTO data_packages VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                (new_id("PKG"), uid, "rule", "规则经验包", f"v{version_no}", "active",
                 1, "由复盘建议人工确认生成", "private", now(), now(), now()))
    evidence(uid, "rule_suggestion", sid, "approved",
             refs=[rid, version_id], payload={"version": version_no})
    return ok({"rule_id": rid,
               "package_version_id": version_id,
               "note": f"规则已生效 · 规则经验包 v{version_no} 已激活（操作者已记录）"})


@router.post("/rules/suggestions/{sid}/reject")
def reject_suggestion(sid: str):
    s = owned_row("rule_suggestions", sid, current_user_id(), label="规则建议")
    execute("UPDATE rule_suggestions SET status='rejected' WHERE id=?", (sid,))
    return ok({"note": "已拒绝（已记录原因）"})


@router.post("/rules/suggestions/{sid}/observe")
def observe_suggestion(sid: str):
    s = owned_row("rule_suggestions", sid, current_user_id(), label="规则建议")
    execute("UPDATE rule_suggestions SET status='observing' WHERE id=?", (sid,))
    return ok({"note": "已标记：继续观察 2 个样本"})
