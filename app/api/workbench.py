"""工作总台：全局状态聚合、行动队列、重要信号、操盘手指令。"""
import datetime

from fastapi import APIRouter
from pydantic import BaseModel

from .. import config
from ..db import jdumps, jloads, query, query_one
from ..providers import get_llm_provider
from . import current_user_id, ok, today

router = APIRouter(tags=["workbench"])


@router.get("/workbench/status")
def workbench_status():
    uid = current_user_id()
    tasks = query("SELECT status, COUNT(*) c FROM content_tasks WHERE user_id=? GROUP BY status", (uid,))
    counts = {r["status"]: r["c"] for r in tasks}
    inventory = sum(counts.get(s, 0) for s in
                    ("SELECTED", "OUTLINING", "DRAFTING", "PRODUCING", "READY_TO_PUBLISH"))
    used_today = query_one(
        "SELECT COALESCE(SUM(cost),0) c FROM usage_ledger WHERE ts LIKE ?",
        (today() + "%",))["c"]
    sync_alerts = query(
        "SELECT id, platform, note FROM accounts WHERE user_id=? AND status='error'", (uid,))
    active_profile = query_one(
        "SELECT version_no FROM profile_versions WHERE user_id=? AND active=1", (uid,))
    packages = query_one(
        "SELECT COUNT(*) c FROM data_packages WHERE owner_id=? AND status='active'", (uid,))["c"]
    return ok({
        "greeting_name": "林小舟" if uid == "u-zhou" else "陈早茶",
        "date": today(),
        "demo_mode": config.DEMO_MODE,
        "cycle": {
            "name": "2026 Q3 · 稳定更新期", "week": "第 3 周 / 共 8 周",
            "goal": "稳住周更 4 条，验证「汇报表达」方向",
            "weekly_target": 4, "weekly_done": 2,
            "hours_total": 14, "hours_left": 6.5,
        },
        "ip_version": active_profile["version_no"] if active_profile else "—",
        "active_packages": packages,
        "inventory": inventory,
        "todo_extract": query_one("SELECT COUNT(*) c FROM signals WHERE user_id=? AND status='new'", (uid,))["c"],
        "todo_analysis": 2, "todo_publish": counts.get("READY_TO_PUBLISH", 0),
        "todo_retro": counts.get("WAITING_RETRO", 0),
        "cost_today": round(used_today, 2),
        "cost_budget": 20,
        "sync_alerts": sync_alerts,
    })


@router.get("/workbench/actions")
def workbench_actions():
    uid = current_user_id()
    actions = []
    # 1. 数据源异常
    err_acc = query_one("SELECT * FROM accounts WHERE user_id=? AND status='error'", (uid,))
    if err_acc:
        actions.append({"priority": "high", "tag": "账号异常", "cls": "b-err",
                        "title": f"{err_acc['platform']}账号同步连续失败 3 次",
                        "meta": "TIKHUB_UNAUTHORIZED · 自动重试已停止，等待你处理",
                        "button": "查看账号", "page": "account"})
    # 2. 最热的未处理对标信号（真实数据）
    hot = query_one(
        "SELECT * FROM signals WHERE user_id=? AND category='hot' AND status='new'"
        " ORDER BY relevance DESC LIMIT 1", (uid,))
    if hot:
        actions.append({"priority": "high", "tag": "对标高表现", "cls": "b-pri",
                        "title": f"{hot['author']}《{hot['title']}》",
                        "meta": f"{hot['rel_perf'] or '高于账号均值'} · 相关度 {hot['relevance']}%",
                        "button": "去提取", "page": "extraction", "primary": True,
                        "signal_id": hot["id"]})
    actions += [
        {"priority": "mid", "tag": "待你决策", "cls": "b-warn",
         "title": "《30 岁前必须懂的 3 个职场真相》已提取完成，等待拆解",
         "meta": "原生字幕 + 清洗文案已就绪 · 拆解将调用深度分析模型（约 ¥0.35）",
         "button": "去拆解", "page": "analysis"},
        {"priority": "mid", "tag": "继续创作", "cls": "b-info",
         "title": "稿件《向上管理不是讨好》停在风格检查",
         "meta": "草稿 v3 · 2 处长句与来源内容重复待处理",
         "button": "继续创作", "page": "creation"},
        {"priority": "low", "tag": "到达复盘", "cls": "b-ok",
         "title": "《我劝你别急着做副业》已到 T+7 复盘时间",
         "meta": "发布前判断已锁定 · 快照数据已回收 3 期",
         "button": "开始复盘", "page": "retro"},
    ]
    return ok(actions[:5])


@router.get("/workbench/signals")
def workbench_signals():
    signals = [
        {"tag": "自有账号", "cls": "b-err",
         "html": "抖音近 3 条平均播放低于 30 天均值 <b>42%</b>",
         "note": "依据：account_snapshots · T+3 数据"},
        {"tag": "对标", "cls": "b-pri",
         "html": "「崔璀」新作品 2 小时破 10 万播放", "note": "来源：TikHub · 12 分钟前"},
        {"tag": "关键词", "cls": "b-info",
         "html": "「向上管理」搜索热度 7 日上升 <b>38%</b>",
         "note": "低成本关键词监控 · 每日 1 次"},
        {"tag": "用户反馈", "cls": "b-warn",
         "html": "「如何选择 offer」在近 5 条作品评论中重复出现 <b>11 次</b>",
         "note": "来自已确认抓取的评论资产"},
        {"tag": "方向变化", "cls": "b-neu",
         "html": "「工具清单」类内容连续 2 条低于均值", "note": "已关联规则：观察中"},
    ]
    return ok(signals)


@router.get("/workbench/tasks")
def workbench_tasks():
    uid = current_user_id()
    rows = query(
        "SELECT id, task_type, status, progress, input_json FROM jobs"
        " WHERE user_id=? AND status IN ('RUNNING','QUEUED','CANCEL_REQUESTED')"
        " ORDER BY created_at DESC LIMIT 8", (uid,))
    done = query(
        "SELECT id, task_type, status, progress, input_json FROM jobs"
        " WHERE user_id=? AND status='SUCCEEDED' ORDER BY finished_at DESC LIMIT 3", (uid,))
    for r in rows + done:
        r["title"] = jloads(r.get("input_json"), {}).get("title", r["task_type"])
    return ok({"running": rows, "recent_done": done})


class CommandIn(BaseModel):
    text: str


@router.post("/workbench/commands/parse")
def parse_command(body: CommandIn):
    llm = get_llm_provider()
    result = llm.parse_command(body.text)
    # 指令中的占位信号替换为当前真实最热信号
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
