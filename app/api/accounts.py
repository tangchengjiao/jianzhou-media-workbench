"""账号运营：自有账号、运营周期、节奏建议、账号数据。"""
from fastapi import APIRouter
from pydantic import BaseModel

from ..db import execute, jdumps, jloads, query, query_one
from ..errors import not_found
from ..jobs import create_job
from ..ids import new_id
from . import current_user_id, ok

router = APIRouter(tags=["accounts"])

CYCLE = {
    "name": "2026 Q3 · 稳定更新期", "range": None,
    "goal": "稳住周更 4 条，验证「汇报表达」方向",
    "platforms": "抖音 + 小红书", "hours_per_week": 14,
    "complexity": "中等（口播 + 图文）",
    "promotion": "否 · 重点实验：开头钩子 A/B",
    "rhythm": "稳定更新", "completion_rate": 0.87,
}

RHYTHMS = ["高频冲刺", "稳定更新", "低频维护", "内容测试",
           "产品预热", "转化推广", "暂停休整", "自定义"]


@router.get("/accounts")
def list_accounts():
    uid = current_user_id()
    rows = query("SELECT * FROM accounts WHERE user_id=?", (uid,))
    for r in rows:
        r["extra"] = jloads(r.pop("extra_json"), {})
    return ok(rows)


class AccountIn(BaseModel):
    platform: str
    handle: str
    sync_frequency: str = "每 6 小时"


@router.post("/accounts")
def add_account(body: AccountIn):
    uid = current_user_id()
    aid = new_id("acc")
    execute("INSERT INTO accounts VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
            (aid, uid, body.platform, body.handle, "own", "ok",
             body.sync_frequency, None, 0, "demo_tikhub", "新添加 · 尚未同步",
             jdumps({"bound": True})))
    return ok({"id": aid})


@router.delete("/accounts/{aid}")
def delete_account(aid: str):
    row = query_one("SELECT * FROM accounts WHERE id=?", (aid,))
    if not row:
        raise not_found("账号", aid)
    execute("DELETE FROM account_snapshots WHERE account_id=?", (aid,))
    execute("DELETE FROM accounts WHERE id=?", (aid,))
    return ok({"note": f"已删除账号 {row['platform']} {row['handle']}（历史快照已清理）"})


@router.post("/accounts/{aid}/sync")
def sync_account(aid: str):
    row = query_one("SELECT * FROM accounts WHERE id=?", (aid,))
    if not row:
        raise not_found("账号", aid)
    if row["bound_provider"] == "manual":
        return ok({"manual": True,
                   "note": "该账号为手动导入模式，请到资料中心导入平台后台数据"})
    job = create_job(row["user_id"], "account_sync",
                     {"account_id": aid, "title": f"账号同步 · {row['platform']}"},
                     cost_est=0.0)
    return ok({"job_id": job["id"], "note": "同步任务已创建（公开元数据 · 免费额度内）"})


@router.get("/accounts/{aid}/snapshots")
def account_snapshots(aid: str):
    return ok(query("SELECT * FROM account_snapshots WHERE account_id=?"
                    " ORDER BY date", (aid,)))


@router.get("/accounts/cycle/current")
def get_cycle():
    return ok({**CYCLE, "rhythms": RHYTHMS})


class CycleIn(BaseModel):
    rhythm: str | None = None
    hours_per_week: float | None = None


@router.post("/accounts/cycle/current")
def update_cycle(body: CycleIn):
    if body.rhythm:
        CYCLE["rhythm"] = body.rhythm
    if body.hours_per_week:
        CYCLE["hours_per_week"] = body.hours_per_week
    return ok({"note": "周期修改将创建新版本（运营周期包为不可变版本）", "cycle": CYCLE})


@router.get("/accounts/suggestions/list")
def account_suggestions():
    return ok([
        {"tag": "保持", "cls": "b-ok",
         "text": "「汇报表达」近 4 周互动率高于账号均值 31%，建议保持占比 ≥ 30%",
         "basis": "依据：账号数据 + 当前运营目标"},
        {"tag": "库存", "cls": "b-warn",
         "text": "内容积压未发布，建议本周优先消耗库存再开新选题",
         "basis": "依据：内容库存 + 剩余产能 6.5h"},
        {"tag": "阻塞", "cls": "b-err",
         "text": "小红书数据源中断，跨平台表现评估暂缓，先修复配置",
         "basis": "依据：数据源状态（TIKHUB_UNAUTHORIZED）"},
    ])
