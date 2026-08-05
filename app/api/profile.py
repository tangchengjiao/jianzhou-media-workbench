"""IP 档案：总览、版本、字段候选、语料、表达画像、访谈会话。"""
import datetime

from fastapi import APIRouter, File, UploadFile
from pydantic import BaseModel, Field

from ..db import execute, jdumps, jloads, query, query_one
from ..errors import not_found, state_invalid, validation_failed
from ..config import MAX_AUDIO_UPLOAD_BYTES
from ..providers import get_llm_provider, get_stt_provider
from ..ids import new_id
from . import current_user_id, now, ok

router = APIRouter(prefix="/profile", tags=["profile"])

STYLE_PROFILE = {
    "句长": {"value": "偏短（平均 14 字）", "src": "语料统计"},
    "口语化程度": {"value": "高", "src": "语料统计"},
    "开头习惯": {"value": "先给结论", "src": "访谈 #5"},
    "论证结构": {"value": "观点 → 案例 → 清单", "src": "语料统计"},
    "常用词": {"value": "说白了 / 本质上 / 可迁移", "src": "语料统计"},
    "禁用词": {"value": "家人们 / 宝子们（待确认）", "src": "访谈 #8 · 待确认"},
    "情绪强度": {"value": "中", "src": "语料统计"},
    "案例使用方式": {"value": "匿名真实案例 + 数字", "src": "语料统计"},
    "自我称呼": {"value": "我 / 小舟（推断 · 待确认）", "src": "语料统计"},
    "用户称呼": {"value": "你 / 咱们", "src": "语料统计"},
    "结尾方式": {"value": "行动清单", "src": "语料统计"},
    "常见口头语": {"value": "“你品一下”", "src": "访谈 #5"},
}

INTERVIEW_TOPICS = ["完整建档", "经历与专业能力", "用户与问题", "内容定位",
                    "观点与价值观", "商业与产品", "表达风格", "案例补充", "档案纠偏"]


@router.get("/active")
def get_active_profile():
    uid = current_user_id()
    row = query_one(
        "SELECT * FROM profile_versions WHERE user_id=? AND active=1", (uid,))
    if not row:
        raise not_found("激活的档案", uid)
    row["content"] = jloads(row["content_json"], {})
    return ok(row)


@router.get("/versions")
def list_versions():
    uid = current_user_id()
    rows = query(
        "SELECT id, version_no, reason, source, created_at, active"
        " FROM profile_versions WHERE user_id=? ORDER BY created_at DESC", (uid,))
    return ok(rows)


class ProfileVersionIn(BaseModel):
    content: dict
    reason: str = Field(min_length=2, max_length=300)
    activate: bool = True


@router.post("/versions")
def create_version(body: ProfileVersionIn):
    """显式创建不可变档案版本；旧版本保留，只切换 active 标记。"""
    uid = current_user_id()
    active = query_one("SELECT content_json FROM profile_versions WHERE user_id=? AND active=1",
                       (uid,))
    if not active:
        raise not_found("激活的档案", uid)
    previous = jloads(active["content_json"], {})
    allowed = set(previous)
    unknown = set(body.content) - allowed
    if unknown:
        raise validation_failed("档案包含未知字段：" + "、".join(sorted(unknown)))
    content = {**previous, **body.content}
    required = ("ip_name", "positioning", "audience", "value", "key_opinion")
    if any(not str(content.get(key) or "").strip() for key in required):
        raise validation_failed("IP 名称、定位、目标用户、核心价值和代表观点不能为空")
    for key in ("pillars", "banned_words"):
        if not isinstance(content.get(key), list):
            raise validation_failed(f"{key} 必须是列表")
    versions = query("SELECT version_no FROM profile_versions WHERE user_id=?", (uid,))
    nums = []
    for row in versions:
        try:
            nums.append(float(str(row["version_no"]).lstrip("v")))
        except ValueError:
            pass
    version_no = f"v{(max(nums) + 0.1 if nums else 1.0):.1f}"
    vid = new_id("pv")
    if body.activate:
        execute("UPDATE profile_versions SET active=0 WHERE user_id=?", (uid,))
    execute("INSERT INTO profile_versions VALUES(?,?,?,?,?,?,?,?)",
            (vid, uid, version_no, jdumps(content), body.reason,
             "手动编辑", now(), 1 if body.activate else 0))
    return ok({"id": vid, "version_no": version_no, "active": body.activate,
               "note": f"档案版本 {version_no} 已创建" + ("并激活" if body.activate else "，等待激活")})


@router.post("/versions/{vid}/activate")
def activate_version(vid: str):
    uid = current_user_id()
    row = query_one("SELECT id FROM profile_versions WHERE id=? AND user_id=?",
                    (vid, uid))
    if not row:
        raise not_found("档案版本", vid)
    execute("UPDATE profile_versions SET active=0 WHERE user_id=?", (uid,))
    execute("UPDATE profile_versions SET active=1 WHERE id=?", (vid,))
    return ok({"activated": vid})


@router.get("/candidates")
def list_candidates():
    uid = current_user_id()
    return ok(query(
        "SELECT * FROM profile_field_candidates WHERE user_id=?"
        " ORDER BY created_at DESC", (uid,)))


@router.post("/candidates/{cid}/confirm")
def confirm_candidate(cid: str):
    row = query_one("SELECT * FROM profile_field_candidates WHERE id=?", (cid,))
    if not row:
        raise not_found("字段候选", cid)
    if row["status"] != "pending":
        raise state_invalid(f"字段候选 {cid} 已处理（{row['status']}），不能重复确认")
    execute("UPDATE profile_field_candidates SET status='confirmed' WHERE id=?", (cid,))
    return ok({"id": cid, "status": "confirmed",
               "note": "将随档案版本 v3.3 草稿一并激活"})


@router.post("/candidates/{cid}/reject")
def reject_candidate(cid: str):
    row = query_one("SELECT * FROM profile_field_candidates WHERE id=?", (cid,))
    if not row:
        raise not_found("字段候选", cid)
    execute("UPDATE profile_field_candidates SET status='rejected' WHERE id=?", (cid,))
    return ok({"id": cid, "status": "rejected",
               "note": "已拒绝：不会进入档案，拒绝记录已保留"})


@router.get("/corpus")
def list_corpus():
    uid = current_user_id()
    rows = query("SELECT * FROM corpus_items WHERE user_id=?", (uid,))
    for r in rows:
        r["perms"] = jloads(r["perms_json"], {})
    return ok(rows)


@router.get("/style")
def get_style():
    return ok(STYLE_PROFILE)


# ================= 访谈 =================

@router.get("/interviews/topics")
def interview_topics():
    return ok(INTERVIEW_TOPICS)


@router.get("/interviews")
def list_interviews():
    uid = current_user_id()
    return ok(query("SELECT * FROM interview_sessions WHERE user_id=?"
                    " ORDER BY updated_at DESC", (uid,)))


@router.get("/interviews/{sid}")
def get_interview(sid: str):
    s = query_one("SELECT * FROM interview_sessions WHERE id=?", (sid,))
    if not s:
        raise not_found("访谈会话", sid)
    s["turns"] = query(
        "SELECT * FROM interview_turns WHERE session_id=? ORDER BY idx", (sid,))
    return ok(s)


class InterviewCreate(BaseModel):
    topic: str
    mode: str = "text"  # text | voice


@router.post("/interviews")
def create_interview(body: InterviewCreate):
    uid = current_user_id()
    llm = get_llm_provider()
    sid = new_id("iv")
    total = len(llm.INTERVIEW_SCRIPTS.get(body.topic,
                                          llm.INTERVIEW_SCRIPTS["完整建档"]))
    execute("INSERT INTO interview_sessions VALUES(?,?,?,?,?,?,?,?,?)",
            (sid, uid, body.topic, body.mode, "IN_PROGRESS", 1, total,
             now(), now()))
    opening = llm.interview_opening(body.topic)
    execute("INSERT INTO interview_turns VALUES(?,?,?,?,?,?,?)",
            (f"it-{sid}-1", sid, 1, "assistant", opening, None, now()))
    return get_interview(sid)


class TurnIn(BaseModel):
    text: str


@router.post("/interviews/{sid}/turns")
def add_turn(sid: str, body: TurnIn):
    s = query_one("SELECT * FROM interview_sessions WHERE id=?", (sid,))
    if not s:
        raise not_found("访谈会话", sid)
    if s["status"] not in ("IN_PROGRESS", "PAUSED"):
        raise state_invalid(f"会话状态 {s['status']} 不能继续作答")
    llm = get_llm_provider()
    max_idx = query_one(
        "SELECT COALESCE(MAX(idx),0) m FROM interview_turns WHERE session_id=?",
        (sid,))["m"]
    execute("INSERT INTO interview_turns VALUES(?,?,?,?,?,?,?)",
            (f"it-{sid}-{max_idx+1}", sid, max_idx + 1, "user", body.text, None, now()))
    nxt = llm.interview_next(s["topic"], s["current_q"], body.text)
    if nxt is None:
        execute("UPDATE interview_sessions SET status='READY_TO_FINISH',"
                " updated_at=? WHERE id=?", (now(), sid))
    else:
        execute("INSERT INTO interview_turns VALUES(?,?,?,?,?,?,?)",
                (f"it-{sid}-{max_idx+2}", sid, max_idx + 2, "assistant", nxt, None, now()))
        execute("UPDATE interview_sessions SET current_q=?, status='IN_PROGRESS',"
                " updated_at=? WHERE id=?", (s["current_q"] + 1, now(), sid))
    return get_interview(sid)


@router.post("/interviews/{sid}/turns/audio")
async def add_audio_turn(sid: str, file: UploadFile = File(...)):
    """语音作答：上传音频分片 → STT → 走与文字相同的轮次逻辑。模式来源已记录。"""
    s = query_one("SELECT * FROM interview_sessions WHERE id=?", (sid,))
    if not s:
        raise not_found("访谈会话", sid)
    audio = await file.read(MAX_AUDIO_UPLOAD_BYTES + 1)
    if len(audio) > MAX_AUDIO_UPLOAD_BYTES:
        raise validation_failed(
            f"音频文件超过 {MAX_AUDIO_UPLOAD_BYTES // (1024 * 1024)} MB 限制"
        )
    stt = get_stt_provider()
    result = stt.transcribe(audio, context=s["topic"])
    resp = add_turn(sid, TurnIn(text=result["text"]))
    resp["stt"] = {"provider": stt.name, "demo": result.get("demo", False),
                   "model": result.get("model", "")}
    return resp


@router.post("/interviews/{sid}/pause")
def pause_interview(sid: str):
    execute("UPDATE interview_sessions SET status='PAUSED', updated_at=?"
            " WHERE id=?", (now(), sid))
    return ok({"id": sid, "status": "PAUSED",
               "note": "已保存当前进度，中断不会生成「建档成功」的假结果"})


@router.post("/interviews/{sid}/finish")
def finish_interview(sid: str):
    s = query_one("SELECT * FROM interview_sessions WHERE id=?", (sid,))
    if not s:
        raise not_found("访谈会话", sid)
    turns = query("SELECT * FROM interview_turns WHERE session_id=? ORDER BY idx",
                  (sid,))
    llm = get_llm_provider()
    cands = llm.interview_extract(s["topic"], turns)
    uid = current_user_id()
    ids = []
    for c in cands:
        cid = new_id("fc")
        execute("INSERT INTO profile_field_candidates VALUES(?,?,?,?,?,?,?,?,?,?)",
                (cid, uid, sid, c["field_path"], c["value"], c["inference_type"],
                 c["confidence"], c["evidence"], "pending", now()))
        ids.append(cid)
    execute("UPDATE interview_sessions SET status='REVIEW_REQUIRED',"
            " updated_at=? WHERE id=?", (now(), sid))
    return ok({"candidates": ids,
               "note": "字段候选已生成，逐项确认后才会写入档案版本草稿；"
                       "推断（inferred）字段不会自动进入正式档案"})
