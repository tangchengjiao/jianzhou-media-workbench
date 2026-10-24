"""工作总台：只聚合当前用户的真实状态、行动与信号。"""
from __future__ import annotations

import datetime
import html

from fastapi import APIRouter
from pydantic import BaseModel

from .. import config
from ..db import jloads, query, query_one
from ..providers import get_llm_provider
from . import current_user_id, ok, today
from .accounts import _cycle_for_user

router = APIRouter(tags=["workbench"])


def _week_bounds() -> tuple[str, str]:
    current = datetime.date.today()
    start = current - datetime.timedelta(days=current.weekday())
    return start.isoformat(), (start + datetime.timedelta(days=6)).isoformat()


@router.get("/workbench/status")
def workbench_status():
    uid = current_user_id()
    tasks = query("SELECT status, COUNT(*) c FROM content_tasks WHERE user_id=? GROUP BY status", (uid,))
    counts = {r["status"]: r["c"] for r in tasks}
    inventory = sum(counts.get(s, 0) for s in
                    ("SELECTED", "OUTLINING", "DRAFTING", "PRODUCING", "READY_TO_PUBLISH"))
    used_today = query_one(
        "SELECT COALESCE(SUM(cost),0) c FROM usage_ledger WHERE user_id=? AND ts LIKE ?",
        (uid, today() + "%"))["c"]
    sync_alerts = query(
        "SELECT id, platform, note FROM accounts WHERE user_id=? AND status='error'", (uid,))
    active_profile = query_one(
        "SELECT version_no FROM profile_versions WHERE user_id=? AND active=1", (uid,))
    packages = query_one(
        "SELECT COUNT(*) c FROM data_packages WHERE owner_id=? AND status='active'", (uid,))["c"]
    todo_analysis = query_one(
        "SELECT COUNT(*) c FROM source_contents s WHERE s.user_id=? AND s.status='transcribed' "
        "AND NOT EXISTS (SELECT 1 FROM analyses a WHERE a.user_id=? AND a.source_content_id=s.id)",
        (uid, uid))["c"]
    week_start, week_end = _week_bounds()
    weekly_done = query_one(
        "SELECT COUNT(*) c FROM publications WHERE user_id=? AND date(published_at) BETWEEN ? AND ?",
        (uid, week_start, week_end))["c"]
    scheduled_hours = query_one(
        "SELECT COALESCE(SUM(estimate_h),0) c FROM content_tasks WHERE user_id=? "
        "AND date(scheduled_date) BETWEEN ? AND ? AND status NOT IN ('PUBLISHED','CANCELLED')",
        (uid, week_start, week_end))["c"]
    cycle_saved = _cycle_for_user()
    hours_total = float(cycle_saved.get("hours_per_week") or 0)
    cycle = {
        **cycle_saved,
        "week": f"{week_start} 至 {week_end}",
        "weekly_target": int(cycle_saved.get("weekly_target") or 0),
        "weekly_done": weekly_done,
        "hours_total": hours_total,
        "hours_left": round(max(0.0, hours_total - float(scheduled_hours or 0)), 1),
    }
    return ok({
        "greeting_name": "见舟",
        "date": today(),
        "demo_mode": config.DEMO_MODE,
        "cycle": cycle,
        "ip_version": active_profile["version_no"] if active_profile else "—",
        "active_packages": packages,
        "inventory": inventory,
        "todo_extract": query_one("SELECT COUNT(*) c FROM signals WHERE user_id=? AND status='new'", (uid,))["c"],
        "todo_analysis": todo_analysis,
        "todo_publish": counts.get("READY_TO_PUBLISH", 0),
        "todo_retro": counts.get("WAITING_RETRO", 0),
        "cost_today": round(used_today, 2),
        "cost_budget": 20,
        "sync_alerts": sync_alerts,
    })


@router.get("/workbench/actions")
def workbench_actions():
    uid = current_user_id()
    actions = []
    err_acc = query_one("SELECT * FROM accounts WHERE user_id=? AND status='error' ORDER BY rowid DESC", (uid,))
    if err_acc:
        actions.append({"priority": "high", "tag": "账号异常", "cls": "b-err",
                        "title": f"{err_acc['platform']} 账号同步失败",
                        "meta": err_acc.get("note") or "打开账号详情查看失败原因与 Trace ID",
                        "button": "查看账号", "page": "account"})
    hot = query_one(
        "SELECT * FROM signals WHERE user_id=? AND category='hot' AND status='new'"
        " ORDER BY relevance DESC LIMIT 1", (uid,))
    if hot:
        actions.append({"priority": "high", "tag": "对标高表现", "cls": "b-pri",
                        "title": f"{hot['author']}《{hot['title']}》",
                        "meta": f"{hot['rel_perf'] or '尚未建立表现基线'} · 相关度 {hot['relevance']}%",
                        "button": "去提取", "page": "extraction", "primary": True,
                        "signal_id": hot["id"]})
    pending = query_one(
        "SELECT s.id,s.title,s.platform,s.created_at FROM source_contents s WHERE s.user_id=? "
        "AND s.status='transcribed' AND NOT EXISTS (SELECT 1 FROM analyses a WHERE a.user_id=? "
        "AND a.source_content_id=s.id) ORDER BY s.created_at DESC LIMIT 1", (uid, uid))
    if pending:
        actions.append({"priority": "mid", "tag": "待你决策", "cls": "b-warn",
                        "title": f"《{pending['title']}》已完成提取，等待拆解",
                        "meta": f"来源 {pending['platform']} · 提取资产已落库",
                        "button": "去拆解", "page": "analysis"})
    project = query_one(
        "SELECT id,title,status,created_at FROM creation_projects WHERE user_id=? "
        "AND status NOT IN ('completed','published','archived') ORDER BY created_at DESC LIMIT 1", (uid,))
    if project:
        actions.append({"priority": "mid", "tag": "继续创作", "cls": "b-info",
                        "title": f"《{project['title']}》仍在创作中",
                        "meta": f"真实项目状态：{project['status']}",
                        "button": "继续创作", "page": "creation"})
    due = query_one(
        "SELECT p.final_title,p.platform,s.tn,s.due_at FROM snapshot_schedules s "
        "JOIN publications p ON p.id=s.publication_id WHERE p.user_id=? "
        "AND s.status IN ('PENDING','AWAITING_INPUT') AND datetime(s.due_at)<=datetime('now','localtime') "
        "ORDER BY s.due_at LIMIT 1", (uid,))
    if due:
        actions.append({"priority": "low", "tag": "到达复盘", "cls": "b-ok",
                        "title": f"《{due['final_title']}》已到 {due['tn']} 数据回收时间",
                        "meta": f"{due['platform']} · 到期 {due['due_at']}",
                        "button": "开始复盘", "page": "retro"})
    if not actions:
        has_account = query_one("SELECT id FROM accounts WHERE user_id=? LIMIT 1", (uid,))
        actions.append({"priority": "mid", "tag": "开始连接", "cls": "b-info",
                        "title": "尚无需要处理的真实行动",
                        "meta": "先连接自有账号，再从内容雷达添加对标账号并同步作品。",
                        "button": "连接账号" if not has_account else "打开内容雷达",
                        "page": "account" if not has_account else "radar"})
    return ok(actions[:5])


@router.get("/workbench/signals")
def workbench_signals():
    uid = current_user_id()
    output = []
    accounts = query("SELECT id,platform,handle FROM accounts WHERE user_id=? AND status='active' ORDER BY rowid DESC", (uid,))
    for account in accounts[:2]:
        snapshots = query("SELECT date,followers,posts FROM account_snapshots WHERE account_id=? ORDER BY date DESC LIMIT 2",
                          (account["id"],))
        if len(snapshots) >= 2:
            delta = int(snapshots[0]["followers"] or 0) - int(snapshots[1]["followers"] or 0)
            direction = "增加" if delta >= 0 else "减少"
            output.append({"tag": "自有账号", "cls": "b-ok" if delta >= 0 else "b-warn",
                           "html": f"{html.escape(account['platform'])} · {html.escape(account['handle'])} 粉丝较上次快照{direction} <b>{abs(delta)}</b>",
                           "note": f"依据 account_snapshots · {snapshots[0]['date']}"})
        elif snapshots:
            output.append({"tag": "自有账号", "cls": "b-info",
                           "html": f"{html.escape(account['platform'])} · {html.escape(account['handle'])} 已建立首个真实快照",
                           "note": f"粉丝 {int(snapshots[0]['followers'] or 0)} · 作品 {int(snapshots[0]['posts'] or 0)}；至少两次同步后显示趋势"})
    category_labels = {"hot": ("对标", "b-pri"), "keyword": ("关键词", "b-info"),
                       "feedback": ("用户反馈", "b-warn")}
    for category, (label, css) in category_labels.items():
        signal = query_one("SELECT * FROM signals WHERE user_id=? AND category=? ORDER BY fetched_at DESC,relevance DESC LIMIT 1",
                           (uid, category))
        if signal:
            output.append({"tag": label, "cls": css,
                           "html": f"{html.escape(signal['author'] or signal['platform'])}《{html.escape(signal['title'])}》",
                           "note": f"来源 {signal['source'] or '已入库信号'} · 相关度 {int(signal['relevance'] or 0)}% · {signal['fetched_at'] or '时间未知'}"})
    return ok(output[:5])


@router.get("/workbench/tasks")
def workbench_tasks():
    uid = current_user_id()
    all_rows = query(
        "SELECT id,task_type,status,progress,input_json,idempotency_key,created_at,finished_at FROM jobs "
        "WHERE user_id=? ORDER BY created_at DESC LIMIT 120", (uid,))
    for r in all_rows:
        r["title"] = jloads(r.get("input_json"), {}).get("title", r["task_type"])
    groups: dict[str, list[dict]] = {}
    for item in all_rows:
        raw_key = item.get("idempotency_key") or f"{item['task_type']}:{item['title']}"
        logical_key = raw_key.split(":manual:", 1)[0]
        groups.setdefault(logical_key, []).append(item)
    active_statuses = {"RUNNING", "QUEUED", "CANCEL_REQUESTED", "WAITING_CONFIRMATION"}
    running, recent_done = [], []
    for logical_key, history in groups.items():
        current = next((item for item in history if item["status"] in active_statuses), history[0])
        current = {**current, "logical_key": logical_key, "history_count": len(history)}
        if current["status"] in active_statuses:
            running.append(current)
        elif current["status"] == "SUCCEEDED":
            recent_done.append(current)
    running.sort(key=lambda item: item.get("created_at") or "", reverse=True)
    recent_done.sort(key=lambda item: item.get("finished_at") or item.get("created_at") or "", reverse=True)
    return ok({"running": running[:8], "recent_done": recent_done[:3]})


class CommandIn(BaseModel):
    text: str


@router.post("/workbench/commands/parse")
def parse_command(body: CommandIn):
    llm = get_llm_provider()
    result = llm.parse_command(body.text)
    uid = current_user_id()
    hot = query_one(
        "SELECT id FROM signals WHERE user_id=? AND category='hot' AND status='new'"
        " ORDER BY relevance DESC LIMIT 1", (uid,))
    if hot:
        result["signal_id"] = hot["id"]
        result["tools"] = [t.replace("#SG-2031", "#" + hot["id"]) for t in result.get("tools", [])]
    return ok(result)


@router.get("/workbench/inbox")
def inbox():
    uid = current_user_id()
    rows = query("SELECT * FROM inbox WHERE user_id=? ORDER BY ts DESC LIMIT 20", (uid,))
    for r in rows:
        r["ref"] = jloads(r["ref_json"], {})
    return ok(rows)
