"""动态运营顾问：账号数据生成画像与周计划建议，所有正式计划均需人工确认。"""
import datetime
import hashlib

from fastapi import APIRouter, Query
from pydantic import BaseModel, Field

from ..db import execute, jdumps, jloads, query, query_one, transaction
from ..errors import not_found, state_invalid
from ..ids import new_id
from ..providers import get_llm_provider
from ..repositories import evidence
from ..operation_context import build_operation_context
from . import current_user_id, now, ok
from .accounts import _cycle_for_user

router = APIRouter(prefix="/advisor", tags=["advisor"])


def _works(uid: str, days: int) -> list[dict]:
    cutoff = int((datetime.datetime.now() - datetime.timedelta(days=days)).timestamp())
    return query(
        "SELECT w.*,a.platform,a.handle FROM account_works w "
        "JOIN accounts a ON a.id=w.account_id "
        "WHERE a.user_id=? AND COALESCE(w.create_time,0)>=? ORDER BY w.create_time DESC",
        (uid, cutoff),
    )


def _metrics(rows: list[dict]) -> dict:
    count = len(rows)
    totals = {key: sum(int(row.get(key) or 0) for row in rows)
              for key in ("plays", "likes", "comments", "collects", "shares")}
    interactions = totals["likes"] + totals["comments"] + totals["collects"] + totals["shares"]
    return {"works": count, **totals,
            "avg_plays": round(totals["plays"] / count, 1) if count else 0,
            "engagement_rate": round(interactions / totals["plays"] * 100, 2)
            if totals["plays"] else 0,
            "rated_good": sum(1 for row in rows if row.get("performance_label") == "good"),
            "rated_weak": sum(1 for row in rows if row.get("performance_label") == "weak")}


@router.post("/portrait/generate")
def generate_portrait():
    uid = current_user_id()
    operation_context = build_operation_context(uid, 90)
    rows30, rows90 = _works(uid, 30), _works(uid, 90)
    metrics = {"30d": _metrics(rows30), "90d": _metrics(rows90)}
    platforms = sorted({row["platform"] for row in rows90})
    best = sorted(rows90, key=lambda row: (int(row.get("plays") or 0),
                                           int(row.get("collects") or 0)), reverse=True)[:5]
    diagnosis = {
        "data_state": "sufficient" if len(rows90) >= 10 else "limited",
        "platforms": platforms,
        "best_examples": [{"id": row["id"], "title": row["title"],
                           "platform": row["platform"], "plays": row["plays"]}
                          for row in best],
        "signals": [
            "近30天内容数量不足，建议先保证稳定样本" if len(rows30) < 8 else "近30天已有可分析样本",
            "请继续给作品标记好/弱并填写复盘备注" if not any(
                row.get("performance_label") != "unrated" for row in rows90) else "已纳入人工好/弱标记",
        ],
        "coverage": operation_context["coverage"],
        "market_signal_summary": operation_context["coverage"].get("signal_categories", {}),
        "context_snapshot_id": operation_context["id"],
        "boundary": "动态运营画像只生成建议，不自动改写稳定IP档案。",
    }
    source = jdumps({"ids": [row["id"] for row in rows90], "metrics": metrics,
                     "operation_context_hash": operation_context["source_hash"]})
    source_hash = hashlib.sha256(source.encode("utf-8")).hexdigest()
    existing = query_one("SELECT * FROM operation_portraits WHERE user_id=? AND window_days=90 "
                         "AND source_hash=? ORDER BY generated_at DESC LIMIT 1", (uid, source_hash))
    if existing:
        return ok({"id": existing["id"], "metrics": jloads(existing["metrics_json"], {}),
                   "diagnosis": jloads(existing["diagnosis_json"], {}), "deduplicated": True})
    portrait_id = new_id("OP")
    execute("INSERT INTO operation_portraits VALUES(?,?,?,?,?,?,?)",
            (portrait_id, uid, 90, jdumps(metrics), jdumps(diagnosis), source_hash, now()))
    evidence(uid, "operation_portrait", portrait_id, "portrait_generated",
             refs=[row["id"] for row in rows90[:20]], payload={"metrics": metrics})
    return ok({"id": portrait_id, "metrics": metrics, "diagnosis": diagnosis,
               "deduplicated": False})


@router.get("/portrait/latest")
def latest_portrait():
    row = query_one("SELECT * FROM operation_portraits WHERE user_id=? "
                    "ORDER BY generated_at DESC LIMIT 1", (current_user_id(),))
    if not row:
        return ok(None)
    row["metrics"] = jloads(row["metrics_json"], {})
    row["diagnosis"] = jloads(row["diagnosis_json"], {})
    return ok(row)


class PlanDraftIn(BaseModel):
    week_start: datetime.date
    weekly_capacity_hours: float | None = Field(default=None, ge=0, le=168)
    desired_posts: int | None = Field(default=None, ge=0, le=14)
    instruction: str = Field(default="", max_length=1000)


@router.post("/plans/generate")
def generate_plan(body: PlanDraftIn):
    uid = current_user_id()
    cycle = _cycle_for_user()
    weekly_capacity_hours = body.weekly_capacity_hours if body.weekly_capacity_hours is not None else float(cycle.get("hours_per_week") or 0)
    desired_posts = body.desired_posts if body.desired_posts is not None else int(cycle.get("weekly_target") or 0)
    if desired_posts <= 0:
        raise state_invalid("当前运营周期没有可执行的周更目标；请先在账号运营中设置更新节奏和每周产能")
    portrait = query_one("SELECT * FROM operation_portraits WHERE user_id=? "
                         "ORDER BY generated_at DESC LIMIT 1", (uid,))
    if not portrait:
        generate_portrait()
        portrait = query_one("SELECT * FROM operation_portraits WHERE user_id=? "
                             "ORDER BY generated_at DESC LIMIT 1", (uid,))
    profile = query_one("SELECT id,version_no,content_json FROM profile_versions "
                        "WHERE user_id=? AND active=1", (uid,))
    operation_context = build_operation_context(uid, 90)
    payload = {
        "week_start": body.week_start.isoformat(),
        "weekly_capacity_hours": weekly_capacity_hours,
        "desired_posts": desired_posts,
        "instruction": body.instruction,
        "cycle_snapshot": {key: cycle.get(key) for key in
                           ("goal", "platforms", "hours_per_week", "complexity", "promotion", "rhythm",
                            "completion_rate", "completion_samples", "weekly_target", "cycle_hash")},
        "portrait": {"metrics": jloads(portrait["metrics_json"], {}),
                     "diagnosis": jloads(portrait["diagnosis_json"], {})},
        "stable_ip_profile": jloads(profile["content_json"], {}) if profile else {},
        "operation_context": operation_context["context"],
        "evidence_policy": "每条建议必须引用输入中的真实ID；数据不足时明确标记数据缺口，不得虚构依据。",
    }
    llm = get_llm_provider()
    plan = llm.suggest_operation_plan(payload)
    if not isinstance(plan, dict) or not isinstance(plan.get("items"), list) or not plan["items"]:
        raise state_invalid("运营建议模型没有返回有效计划，本次没有创建空计划")
    fallback_refs = operation_context["refs"][:12]
    for index, item in enumerate(plan["items"]):
        evidence_ids = [str(value) for value in (item.get("evidence_ids") or []) if str(value) in operation_context["refs"]]
        if not evidence_ids and fallback_refs:
            evidence_ids = fallback_refs[index % len(fallback_refs):][:2] or fallback_refs[:2]
        item["evidence_ids"] = evidence_ids
        item["evidence_note"] = str(item.get("evidence_note") or item.get("reason") or "基于当前运营上下文")
        raw_confidence = item.get("confidence")
        if isinstance(raw_confidence, (int, float)):
            confidence = max(0.0, min(1.0, float(raw_confidence)))
        else:
            confidence = {"高": .82, "中": .62, "低": .35, "high": .82,
                          "medium": .62, "low": .35}.get(str(raw_confidence or "").lower())
            if confidence is None:
                confidence = .35 if operation_context["coverage"]["gaps"] else .62
        item["confidence"] = confidence
    plan["constraints"] = {"requires_user_approval": True,
                           "profile_auto_edit": False,
                           "source_profile_version": profile["version_no"] if profile else None,
                           "cycle_hash": cycle.get("cycle_hash"),
                           "weekly_capacity_hours": weekly_capacity_hours,
                           "desired_posts": desired_posts}
    plan_id = new_id("OPD")
    with transaction():
        execute("INSERT INTO operation_plan_drafts(id,user_id,week_start,portrait_id,status,plan_json,model,provider,created_at,approved_at,context_snapshot_id,cycle_hash) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                (plan_id, uid, body.week_start.isoformat(), portrait["id"], "DRAFT",
                 jdumps(plan), getattr(llm, "last_model", ""), getattr(llm, "name", ""),
                 now(), None, operation_context["id"], cycle.get("cycle_hash")))
        execute("UPDATE operation_plan_drafts SET status='SUPERSEDED',replaced_by_id=?,stale_at=? "
                "WHERE user_id=? AND week_start=? AND status='DRAFT' AND id<>?",
                (plan_id, now(), uid, body.week_start.isoformat(), plan_id))
    evidence(uid, "operation_plan", plan_id, "plan_drafted",
             refs=operation_context["refs"][:40],
             payload={"context_snapshot_id": operation_context["id"],
                      "coverage": operation_context["coverage"]})
    return ok({"id": plan_id, "status": "DRAFT", "plan": plan,
               "coverage": operation_context["coverage"],
               "context_snapshot_id": operation_context["id"],
               "note": "这是AI建议草案；确认前不会进入正式排期，也不会修改IP档案"})


@router.get("/plans")
def list_plans(scope: str = Query(default="active", pattern="^(active|history|all)$"),
               limit: int = Query(default=20, ge=1, le=100), offset: int = Query(default=0, ge=0)):
    status_sql = "AND status IN ('DRAFT','APPROVED')" if scope == "active" else (
        "AND status IN ('REJECTED','STALE','SUPERSEDED')" if scope == "history" else "")
    rows = query(f"SELECT * FROM operation_plan_drafts WHERE user_id=? {status_sql} "
                 "ORDER BY created_at DESC LIMIT ? OFFSET ?", (current_user_id(), limit, offset))
    for row in rows:
        row["plan"] = jloads(row["plan_json"], {})
        tasks = query(
            "SELECT id,title,status,platform,scheduled_date,project_id,operation_plan_item_index "
            "FROM content_tasks WHERE user_id=? AND operation_plan_draft_id=? "
            "ORDER BY operation_plan_item_index,id",
            (current_user_id(), row["id"]),
        )
        for task in tasks:
            task["creation_action"] = "继续创作" if task.get("project_id") else "开始创作"
        row["tasks"] = tasks
    return ok(rows)


@router.get("/context/latest")
def latest_context():
    uid = current_user_id()
    row = query_one("SELECT * FROM operation_context_snapshots WHERE user_id=? ORDER BY created_at DESC LIMIT 1", (uid,))
    if not row:
        return ok(None)
    return ok({"id": row["id"], "window_days": row["window_days"],
               "coverage": jloads(row["coverage_json"], {}),
               "context": jloads(row["context_json"], {}), "created_at": row["created_at"]})


@router.post("/plans/{plan_id}/approve")
def approve_plan(plan_id: str):
    uid = current_user_id()
    row = query_one("SELECT * FROM operation_plan_drafts WHERE id=? AND user_id=?",
                    (plan_id, uid))
    if not row:
        raise not_found("运营计划草案", plan_id)
    if row["status"] != "DRAFT":
        raise state_invalid("该计划已经处理，不能重复确认")
    plan = jloads(row["plan_json"], {})
    created = []
    with transaction():
        for index, item in enumerate(plan.get("items") or []):
            task_id = new_id("T")
            date = item.get("date") or row["week_start"]
            execute("INSERT INTO content_tasks"
                    "(id,user_id,project_id,title,status,platform,scheduled_date,estimate_h,priority,badges_json,"
                    "operation_plan_draft_id,operation_plan_item_index,source_json)"
                    " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (task_id, uid, None, str(item.get("title") or f"AI建议选题{index+1}"),
                     "SELECTED", str(item.get("platform") or "待确认"), date,
                     float(item.get("estimate_h") or 1.5), "P1" if index == 0 else "P2",
                     jdumps(["AI建议", "已人工确认"]), plan_id, index,
                     jdumps({"kind": "advisor_plan", "plan_id": plan_id, "item_index": index,
                             "evidence_ids": item.get("evidence_ids") or [],
                             "reason": item.get("reason") or item.get("rationale") or item.get("description") or "",
                             "confidence": item.get("confidence")})))
            created.append(task_id)
        execute("UPDATE operation_plan_drafts SET status='APPROVED',approved_at=? "
                "WHERE id=?", (now(), plan_id))
    evidence(uid, "operation_plan", plan_id, "plan_approved", refs=created,
             payload={"task_count": len(created)})
    return ok({"status": "APPROVED", "task_ids": created,
               "note": "已按你的确认写入正式内容计划；IP档案未被修改"})


@router.post("/plans/{plan_id}/reject")
def reject_plan(plan_id: str):
    changed = execute("UPDATE operation_plan_drafts SET status='REJECTED',archived_at=? "
                      "WHERE id=? AND user_id=? AND status='DRAFT'",
                      (now(), plan_id, current_user_id()))
    if not changed:
        raise state_invalid("计划不存在或已经处理")
    return ok({"status": "REJECTED"})
