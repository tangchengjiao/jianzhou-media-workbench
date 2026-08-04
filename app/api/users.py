"""创作者切换：关闭当前包组合 → 激活新用户包 → 清空会话上下文。"""
from fastapi import APIRouter

from ..db import execute, query, query_one
from ..errors import not_found
from . import current_user_id, ok, set_current_user

router = APIRouter(prefix="/users", tags=["users"])


@router.get("")
def list_users():
    active = current_user_id()
    users = query("SELECT * FROM users")
    for u in users:
        u["active"] = u["id"] == active
        u["package_count"] = query_one(
            "SELECT COUNT(*) c FROM data_packages WHERE owner_id=?", (u["id"],))["c"]
    return ok(users)


@router.get("/{uid}/switch-preview")
def switch_preview(uid: str):
    user = query_one("SELECT * FROM users WHERE id=?", (uid,))
    if not user:
        raise not_found("创作者", uid)
    current = current_user_id()
    closing = query("SELECT name, type FROM data_packages WHERE owner_id=? AND status='active'",
                    (current,))
    activating = query("SELECT name, type FROM data_packages WHERE owner_id=?",
                       (uid,))
    return ok({
        "target": user,
        "will_close": closing,
        "will_activate": activating,
        "note": "切换将：① 关闭当前数据包组合 ② 激活新用户数据包 ③ 清空会话级临时上下文 "
                "④ 保留系统级模型与数据源配置 ⑤ 禁止跨用户检索。",
    })


@router.post("/{uid}/activate")
def activate_user(uid: str):
    user = query_one("SELECT * FROM users WHERE id=?", (uid,))
    if not user:
        raise not_found("创作者", uid)
    set_current_user(uid)
    return ok({"active_user": uid,
               "note": f"已切换到「{user['name']}」：会话上下文已清空，系统级配置保留。"})
