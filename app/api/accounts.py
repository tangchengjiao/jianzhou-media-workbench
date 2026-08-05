"""账号运营：真实账号绑定、同步、快照与运营周期。"""
from __future__ import annotations

import datetime

from fastapi import APIRouter
from pydantic import BaseModel, Field

from ..datasource import PLATFORM_LABELS, SUPPORTED_PLATFORMS, get_datasource
from ..db import execute, jdumps, jloads, query, query_one, transaction
from ..errors import not_found, validation_failed
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


def _account_out(row: dict) -> dict:
    out = dict(row)
    extra = jloads(out.pop("extra_json", None), {})
    out["extra"] = extra
    out["platform_label"] = PLATFORM_LABELS.get(out["platform"], out["platform"])
    out["display_name"] = extra.get("name") or out["handle"]
    out["homepage"] = extra.get("link", "")
    out["avatar"] = extra.get("avatar", "")
    out["bio"] = extra.get("bio", "")
    out["video_count"] = int(extra.get("video_count") or 0)
    out["likes_count"] = int(extra.get("likes") or 0)
    out["followers"] = int(out.get("followers") or 0)
    return out


def _owned_account(aid: str) -> dict:
    row = query_one("SELECT * FROM accounts WHERE id=? AND user_id=?",
                    (aid, current_user_id()))
    if not row:
        raise not_found("账号", aid)
    return row


def _cycle_for_user() -> dict:
    row = query_one("SELECT value_json FROM app_settings WHERE key=?",
                    (f"account_cycle:{current_user_id()}",))
    stored = jloads(row["value_json"], {}) if row else {}
    return {**CYCLE, **stored, "rhythms": RHYTHMS}


@router.get("/accounts")
def list_accounts():
    rows = query("SELECT * FROM accounts WHERE user_id=? ORDER BY rowid DESC",
                 (current_user_id(),))
    return ok([_account_out(row) for row in rows])


class AccountIn(BaseModel):
    platform: str
    link: str = Field(default="", max_length=2048)
    handle: str = Field(default="", max_length=300)  # 兼容旧客户端
    display_name: str = Field(default="", max_length=200)
    sync_frequency: str = Field(default="每 6 小时", max_length=50)
    source_mode: str = Field(default="provider", pattern="^(provider|manual)$")


@router.post("/accounts")
def add_account(body: AccountIn):
    """绑定自有账号；Provider 模式必须完成真实解析与概况读取后才落库。"""
    uid = current_user_id()
    if body.platform not in SUPPORTED_PLATFORMS:
        raise validation_failed(f"暂不支持平台：{body.platform}")
    source = (body.link or body.handle).strip()
    if not source:
        raise validation_failed("请填写账号主页链接或平台账号 ID")

    if body.source_mode == "manual":
        resolved = {"uid": source, "platform": body.platform, "link": body.link.strip()}
        overview = {
            "name": body.display_name.strip() or source, "avatar": "", "bio": "",
            "fans": 0, "likes": 0, "video_count": 0,
        }
        provider_name = "manual"
        status = "manual"
        note = "已绑定手动导入账号；请到资料中心导入平台后台数据"
    else:
        ds = get_datasource()
        if hasattr(ds, "require_configured"):
            ds.require_configured()
        resolved = ds.resolve_account(source, body.platform)
        overview = ds.account_overview(resolved["uid"], resolved["platform"])
        provider_name = ds.name
        status = "ok"
        note = f"已通过 {provider_name} 解析并读取公开账号资料"

    existing = None
    for row in query("SELECT * FROM accounts WHERE user_id=? AND platform=?",
                     (uid, resolved["platform"])):
        if jloads(row.get("extra_json"), {}).get("uid") == resolved["uid"]:
            existing = row
            break
    aid = existing["id"] if existing else new_id("acc")
    extra = {
        "uid": resolved["uid"], "link": resolved.get("link") or source,
        "name": overview.get("name") or body.display_name.strip() or source,
        "avatar": overview.get("avatar") or "", "bio": overview.get("bio") or "",
        "likes": int(overview.get("likes") or 0),
        "video_count": int(overview.get("video_count") or 0),
        "bound": True,
    }
    now = datetime.datetime.now().isoformat(timespec="seconds")
    last_sync_at = now if body.source_mode == "provider" else None
    handle = "@" + extra["name"].lstrip("@")
    with transaction():
        if existing:
            execute(
                "UPDATE accounts SET handle=?,kind='own',status=?,sync_frequency=?,"
                "last_sync_at=?,followers=?,bound_provider=?,note=?,extra_json=? WHERE id=?",
                (handle, status, body.sync_frequency, last_sync_at,
                 int(overview.get("fans") or 0), provider_name, note, jdumps(extra), aid),
            )
        else:
            execute("INSERT INTO accounts VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                    (aid, uid, resolved["platform"], handle, "own", status,
                     body.sync_frequency, last_sync_at, int(overview.get("fans") or 0),
                     provider_name, note, jdumps(extra)))
        if body.source_mode == "provider":
            date = datetime.date.today().isoformat()
            execute("DELETE FROM account_snapshots WHERE account_id=? AND date=?", (aid, date))
            execute("INSERT INTO account_snapshots VALUES(?,?,?,?,?,?)",
                    (new_id("ss"), aid, date, int(overview.get("fans") or 0),
                     int(overview.get("video_count") or 0),
                     jdumps({"source": provider_name, "likes": extra["likes"]})))
    saved = _account_out(query_one("SELECT * FROM accounts WHERE id=?", (aid,)))
    return ok({"note": ("账号已更新" if existing else "账号已添加") + "；" + note,
               "existing": bool(existing), "account": saved, "id": aid})


@router.get("/accounts/{aid}")
def account_detail(aid: str):
    row = _owned_account(aid)
    snapshots = query("SELECT * FROM account_snapshots WHERE account_id=? ORDER BY date",
                      (aid,))
    for snapshot in snapshots:
        snapshot["metrics"] = jloads(snapshot.pop("metrics_json", None), {})
    return ok({**_account_out(row), "snapshots": snapshots})


class AccountUpdateIn(BaseModel):
    sync_frequency: str = Field(min_length=1, max_length=50)


@router.patch("/accounts/{aid}")
def update_account(aid: str, body: AccountUpdateIn):
    _owned_account(aid)
    execute("UPDATE accounts SET sync_frequency=? WHERE id=?",
            (body.sync_frequency, aid))
    return ok({"note": "同步频率已保存", "account": _account_out(
        query_one("SELECT * FROM accounts WHERE id=?", (aid,)))})


@router.delete("/accounts/{aid}")
def delete_account(aid: str):
    row = _owned_account(aid)
    with transaction():
        execute("DELETE FROM account_snapshots WHERE account_id=?", (aid,))
        execute("DELETE FROM accounts WHERE id=?", (aid,))
    return ok({"note": f"已删除账号 {row['platform']} {row['handle']}（历史快照已清理）"})


@router.post("/accounts/{aid}/sync")
def sync_account(aid: str):
    row = _owned_account(aid)
    if row["bound_provider"] == "manual":
        return ok({"manual": True,
                   "note": "该账号为手动导入模式，请到资料中心导入平台后台数据"})
    job = create_job(row["user_id"], "account_sync",
                     {"account_id": aid, "title": f"账号同步 · {row['platform']}"},
                     cost_est=0.0)
    execute("UPDATE accounts SET status='syncing',note='同步任务执行中' WHERE id=?", (aid,))
    return ok({"job_id": job["id"], "note": "同步任务已创建；完成后会刷新账号资料和快照"})


@router.get("/accounts/{aid}/snapshots")
def account_snapshots(aid: str):
    _owned_account(aid)
    return ok(query("SELECT * FROM account_snapshots WHERE account_id=? ORDER BY date",
                    (aid,)))


@router.get("/accounts/cycle/current")
def get_cycle():
    return ok(_cycle_for_user())


class CycleIn(BaseModel):
    rhythm: str | None = None
    hours_per_week: float | None = Field(default=None, ge=0, le=168)
    goal: str | None = Field(default=None, max_length=300)
    platforms: str | None = Field(default=None, max_length=200)
    complexity: str | None = Field(default=None, max_length=200)
    promotion: str | None = Field(default=None, max_length=200)


@router.post("/accounts/cycle/current")
def update_cycle(body: CycleIn):
    updates = body.model_dump(exclude_none=True)
    if "rhythm" in updates and updates["rhythm"] not in RHYTHMS:
        raise validation_failed("更新节奏不在可选范围内")
    current = _cycle_for_user()
    current.pop("rhythms", None)
    current.update(updates)
    execute("INSERT OR REPLACE INTO app_settings(key,value_json) VALUES(?,?)",
            (f"account_cycle:{current_user_id()}", jdumps(current)))
    return ok({"note": "运营周期已保存，重新启动后仍会保留",
               "cycle": {**current, "rhythms": RHYTHMS}})


@router.get("/accounts/suggestions/list")
def account_suggestions():
    accounts = list_accounts()["data"]
    if not accounts:
        return ok([])
    suggestions = []
    if any(account["status"] == "error" for account in accounts):
        suggestions.append({"tag": "阻塞", "cls": "b-err",
                            "text": "存在账号同步失败；跨平台判断前应先恢复数据源。",
                            "basis": "依据：账号同步状态"})
    if any(account["status"] == "manual" for account in accounts):
        suggestions.append({"tag": "待补数据", "cls": "b-warn",
                            "text": "手动账号尚未导入后台数据，趋势与运营建议不会使用猜测值。",
                            "basis": "依据：账号绑定方式"})
    if any(account["status"] == "ok" for account in accounts):
        suggestions.append({"tag": "已连接", "cls": "b-ok",
                            "text": "已连接账号可按设定频率同步公开概况并沉淀每日快照。",
                            "basis": "依据：真实账号绑定与快照记录"})
    return ok(suggestions)
