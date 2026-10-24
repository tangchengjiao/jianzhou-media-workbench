"""数据复盘：真实快照、预测校准、规则建议与版本化激活。"""
import datetime
import hashlib
import json

from fastapi import APIRouter
from pydantic import BaseModel, Field

from ..db import execute, jdumps, jloads, query, query_one, transaction
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
    report_count = query_one(
        "SELECT COUNT(DISTINCT publication_id) c FROM retro_reports WHERE user_id=?",
        (uid,),
    )["c"]
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
        "WHERE t.user_id=? AND t.status IN "
        "('PUBLISHED','WAITING_RETRO','RETRO_COMPLETED','ARCHIVED') "
        "ORDER BY p.published_at DESC", (uid,))
    for r in rows:
        r["badges"] = jloads(r["badges_json"], [])
        r["schedules"] = query(
            "SELECT * FROM snapshot_schedules WHERE publication_id=? ORDER BY due_at",
            (r["publication_id"],),
        )
        latest_report = query_one(
            "SELECT version_no,stage,created_at FROM retro_reports "
            "WHERE publication_id=? ORDER BY version_no DESC,created_at DESC LIMIT 1",
            (r["publication_id"],),
        )
        r["latest_report"] = latest_report
    return ok(rows)


@router.get("/retro/report/{pub_id}")
def retro_report(pub_id: str):
    pub = owned_row("publications", pub_id, current_user_id(), label="发布记录")
    report = query_one(
        "SELECT * FROM retro_reports WHERE publication_id=? "
        "ORDER BY version_no DESC,created_at DESC LIMIT 1",
        (pub_id,),
    )
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
        "report_meta": ({"id": report["id"], "version_no": report.get("version_no") or 1,
                         "stage": report.get("stage") or "INTERIM",
                         "created_at": report.get("created_at")}
                        if report else None),
    })


class SnapshotIn(BaseModel):
    tn: str
    plays: int = Field(ge=0)
    likes: int = Field(ge=0)
    comments: int = Field(ge=0)
    collects: int = Field(ge=0)
    shares: int = Field(default=0, ge=0)
    views: int = Field(default=0, ge=0)
    follows: int = Field(default=0, ge=0)
    avg_watch_seconds: float | None = Field(default=None, ge=0)
    completion_rate: float | None = Field(default=None, ge=0, le=100)
    extra_metrics: dict = {}


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
    captured_at = now()
    sid = new_id("PS")
    # 快照只追加不覆盖：同一 T+N 的人工校正也保留为下一条证据。
    with transaction():
        execute("INSERT INTO publication_snapshots"
                "(id,publication_id,tn,plays,likes,comments,collects,taken_at,shares,views,"
                "follows,avg_watch_seconds,completion_rate,metrics_json)"
                " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (sid, pub_id, body.tn, body.plays, body.likes, body.comments,
                 body.collects, captured_at, body.shares, body.views, body.follows,
                 body.avg_watch_seconds, body.completion_rate, jdumps(body.extra_metrics)))
        execute("UPDATE snapshot_schedules SET status='CAPTURED',attempts=attempts+1,"
                "last_error=NULL,captured_at=? WHERE id=?", (captured_at, schedule["id"]))
        if pub.get("task_id"):
            execute("UPDATE content_tasks SET status='WAITING_RETRO' WHERE id=? AND user_id=?",
                    (pub["task_id"], uid))
        if pub.get("project_id"):
            execute("UPDATE creation_projects SET status='WAITING_RETRO' "
                    "WHERE id=? AND user_id=?", (pub["project_id"], uid))
    evidence(uid, "publication", pub_id, "snapshot_captured", refs=[sid, schedule["id"]],
             payload=body.model_dump())
    return ok({"id": sid, "captured_at": captured_at,
               "note": "快照已追加保存；同一 T+N 的再次提交会作为校正证据保留，不覆盖历史。"})


@router.post("/retro/publications/{pub_id}/generate")
def generate_retro(pub_id: str):
    uid = current_user_id()
    pub = owned_row("publications", pub_id, uid, label="发布记录")
    snapshots = query("SELECT * FROM publication_snapshots WHERE publication_id=? ORDER BY taken_at",
                      (pub_id,))
    if not snapshots:
        raise state_invalid("至少录入一个真实快照后才能复盘")
    snapshot_fingerprint = [{key: item.get(key) for key in (
        "id", "tn", "plays", "likes", "comments", "collects", "shares", "views",
        "follows", "avg_watch_seconds", "completion_rate", "taken_at", "metrics_json")}
        for item in snapshots]
    snapshot_hash = hashlib.sha256(
        json.dumps(snapshot_fingerprint, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()
    existing = query_one(
        "SELECT * FROM retro_reports WHERE publication_id=? AND snapshot_hash=?",
        (pub_id, snapshot_hash),
    )
    if existing:
        return ok({"id": existing["id"], "report": jloads(existing["conclusion_json"], {}),
                   "stage": existing.get("stage") or "INTERIM",
                   "version_no": existing.get("version_no") or 1,
                   "rule_suggestion_id": None, "deduplicated": True,
                   "note": "当前快照集合已经复盘过，本次没有重复生成"})
    captured_tns = {str(item.get("tn") or "") for item in snapshots}
    stage = "FINAL" if "T+7" in captured_tns else "INTERIM"
    version_no = int((query_one(
        "SELECT COALESCE(MAX(version_no),0) n FROM retro_reports WHERE publication_id=?",
        (pub_id,),
    ) or {}).get("n") or 0) + 1
    prediction = (query_one("SELECT * FROM predictions WHERE id=? AND locked=1",
                            (pub.get("prediction_id"),))
                  if pub.get("prediction_id") else None)
    latest = snapshots[-1]
    interactions = (latest["likes"] + latest["comments"] + latest["collects"]
                    + int(latest.get("shares") or 0))
    engagement = round(interactions / latest["plays"] * 100, 2) if latest["plays"] else 0.0
    details = jloads(prediction.get("details_json"), {}) if prediction else {}
    center = details.get("center") or {}
    probabilities = details.get("bucket_probabilities") or {}
    actual_bucket = "待人工确认账号基线" if prediction else "仅实绩记录"
    center_value = center.get("value")
    if isinstance(center_value, (int, float)) and center.get("metric") == "播放量":
        ratio = latest["plays"] / center_value if center_value else 0
        actual_bucket = ("明显起量" if ratio >= 2 else "高于账号基线" if ratio >= 1.2
                         else "接近账号基线" if ratio >= 0.8 else "低于账号基线")
    hypothesis_result = {
        "status": "待人工判定",
        "reason": "系统保留真实指标与原预测，不擅自把相关性解释为因果。",
    }
    prediction_summary = ({
        "expected_bucket": prediction["expected_bucket"],
        "confidence": prediction["confidence"],
        "hypothesis": prediction["hypothesis"],
        "bucket_probabilities": probabilities,
        "prediction_version": prediction.get("version_no"),
        "integrity_hash": prediction.get("integrity_hash"),
    } if prediction else None)
    conclusion = {
        "mode": "prediction_calibration" if prediction else "actual_only",
        "prediction": prediction_summary,
        "actual": {"latest_tn": latest["tn"], "plays": latest["plays"],
                   "likes": latest["likes"], "comments": latest["comments"],
                   "collects": latest["collects"], "shares": latest.get("shares") or 0,
                   "follows": latest.get("follows") or 0,
                   "completion_rate": latest.get("completion_rate"),
                   "engagement_rate": engagement, "actual_bucket": actual_bucket},
        "comparison": {"predicted_bucket": prediction["expected_bucket"] if prediction else None,
                       "actual_bucket": actual_bucket,
                       "hypothesis_result": hypothesis_result if prediction else {
                           "status": "不适用",
                           "reason": "该内容在工作台外完成，发布前没有盲预测，因此只复盘真实表现，不补造预测。",
                       }},
        "calibration": (
            "已对齐原始不可变预测与真实快照；缺少账号基线时明确等待人工判断"
            if prediction else
            "仅实绩复盘：保留真实快照，但不计入发布前预测准确率校准"
        ),
        "evidence_snapshot_ids": [item["id"] for item in snapshots],
        "stage": stage,
        "version_no": version_no,
        "generated_at": now(),
    }
    rid = new_id("RR")
    observation_id = None
    suggestion_id = None
    lifecycle_status = "RETRO_COMPLETED" if stage == "FINAL" else "WAITING_RETRO"
    with transaction():
        execute(
            "INSERT INTO retro_reports(id,user_id,publication_id,conclusion_json,created_at,"
            "version_no,stage,snapshot_hash) VALUES(?,?,?,?,?,?,?,?)",
            (rid, uid, pub_id, jdumps(conclusion), now(), version_no, stage, snapshot_hash),
        )
        if prediction:
            observation_id = new_id("PO")
            execute("INSERT INTO prediction_observations VALUES(?,?,?,?,?,?,?,?)",
                    (observation_id, uid, prediction["id"], pub_id, latest["id"],
                     "retro_comparison", jdumps(conclusion["comparison"]), now()))
        if stage == "FINAL":
            suggestion_id = new_id("RS")
            rule_text = ((f"在{pub['platform']}发布同类内容时，优先复核发布前假设；"
                          f"当前 {latest['tn']} 真实互动率为 {engagement}%")
                         if prediction else
                         (f"在{pub['platform']}发布同类内容时，参考本次仅实绩样本；"
                          f"当前 {latest['tn']} 真实互动率为 {engagement}%"))
            execute("INSERT INTO rule_suggestions VALUES(?,?,?,?,?,?,?,?,?,?)",
                    (suggestion_id, uid, "add", rule_text,
                     f"发布 {pub_id}；快照 {','.join(conclusion['evidence_snapshot_ids'])}",
                     min(0.95, 0.5 + 0.1 * len(snapshots)), pub["platform"],
                     "仅在人工确认后进入规则包", "pending", now()))
        if pub.get("task_id"):
            execute("UPDATE content_tasks SET status=? WHERE id=? AND user_id=?",
                    (lifecycle_status, pub["task_id"], uid))
        if pub.get("project_id"):
            execute("UPDATE creation_projects SET status=? WHERE id=? AND user_id=?",
                    (lifecycle_status, pub["project_id"], uid))
    refs = [rid, *conclusion["evidence_snapshot_ids"]]
    if suggestion_id:
        refs.append(suggestion_id)
    if observation_id:
        refs.append(observation_id)
    evidence(uid, "publication", pub_id, "retro_generated", refs=refs,
             payload={"engagement_rate": engagement, "mode": conclusion["mode"],
                      "stage": stage, "version_no": version_no})
    return ok({"id": rid, "report": conclusion,
               "stage": stage, "version_no": version_no,
               "rule_suggestion_id": suggestion_id, "deduplicated": False,
               "note": ("最终复盘已生成，规则建议等待人工确认" if stage == "FINAL" else
                        "阶段复盘已保存；录入后续 T+3/T+7 快照后可继续生成新版复盘")})


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
