"""IP 档案：总览、版本、字段候选、语料、表达画像、访谈会话。"""
import datetime

from fastapi import APIRouter, File, UploadFile
from pydantic import BaseModel, Field

from ..db import execute, jdumps, jloads, query, query_one, transaction
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

PROFILE_DEFAULTS = {
    "ip_name": "", "ip_type": "", "positioning": "", "audience": "",
    "not_for": [], "value": "", "pillars": [], "key_opinion": "",
    "monetization": "", "offer_ladder": [], "trust_assets": [],
    "expression_style": [], "expression_boundaries": [], "banned_words": [],
    "spoken_style_rules": [], "voice_samples": [],
    "validation_status": "", "source_refs": [], "field_sources": {},
}
PROFILE_LIST_FIELDS = {
    "not_for", "pillars", "offer_ladder", "trust_assets", "expression_style",
    "expression_boundaries", "banned_words", "source_refs",
    "spoken_style_rules", "voice_samples",
}

FIELD_PATH_MAP = {
    "IP名称": "ip_name", "名称": "ip_name", "IP类型": "ip_type",
    "定位": "positioning", "IP定位": "positioning", "内容定位": "positioning",
    "目标用户": "audience", "用户": "audience", "不服务人群": "not_for",
    "核心价值": "value", "价值": "value", "内容支柱": "pillars",
    "代表观点": "key_opinion", "核心观点": "key_opinion",
    "商业化": "monetization", "产品阶梯": "offer_ladder",
    "信任资产": "trust_assets", "表达风格": "expression_style",
    "表达边界": "expression_boundaries", "禁用词": "banned_words",
    "口语规则": "spoken_style_rules", "原话样本": "voice_samples",
}


def _owned_session(sid: str) -> dict:
    row = query_one("SELECT * FROM interview_sessions WHERE id=? AND user_id=?",
                    (sid, current_user_id()))
    if not row:
        raise not_found("访谈会话", sid)
    return row


def _next_profile_version(uid: str) -> str:
    values = []
    for row in query("SELECT version_no FROM profile_versions WHERE user_id=?", (uid,)):
        try:
            values.append(float(str(row["version_no"]).lstrip("v")))
        except ValueError:
            continue
    return f"v{(max(values) + 0.1 if values else 1.0):.1f}"


def _validate_profile_content(content: dict) -> None:
    unknown = set(content) - set(PROFILE_DEFAULTS)
    if unknown:
        raise validation_failed("档案包含未知字段：" + "、".join(sorted(unknown)))
    required = ("ip_name", "positioning", "audience", "value", "key_opinion")
    if any(not str(content.get(key) or "").strip() for key in required):
        raise validation_failed("IP 名称、定位、目标用户、核心价值和代表观点不能为空")
    for key in PROFILE_LIST_FIELDS:
        if not isinstance(content.get(key), list):
            raise validation_failed(f"{key} 必须是列表")
    if not isinstance(content.get("field_sources"), dict):
        raise validation_failed("field_sources 必须是对象")


def _insert_profile_version(uid: str, content: dict, reason: str, source: str,
                            activate: bool = True) -> tuple[str, str]:
    _validate_profile_content(content)
    version_no = _next_profile_version(uid)
    vid = new_id("pv")
    with transaction():
        if activate:
            execute("UPDATE profile_versions SET active=0 WHERE user_id=?", (uid,))
        execute("INSERT INTO profile_versions VALUES(?,?,?,?,?,?,?,?)",
                (vid, uid, version_no, jdumps(content), reason, source, now(),
                 1 if activate else 0))
    return vid, version_no


@router.get("/active")
def get_active_profile():
    uid = current_user_id()
    row = query_one(
        "SELECT * FROM profile_versions WHERE user_id=? AND active=1", (uid,))
    if not row:
        return ok(None)
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
    source: str = Field(default="手动编辑", min_length=2, max_length=500)


@router.post("/versions")
def create_version(body: ProfileVersionIn):
    """显式创建不可变档案版本；旧版本保留，只切换 active 标记。"""
    uid = current_user_id()
    active = query_one("SELECT content_json FROM profile_versions WHERE user_id=? AND active=1",
                       (uid,))
    previous = {**PROFILE_DEFAULTS,
                **(jloads(active["content_json"], {}) if active else {})}
    content = {**previous, **body.content}
    vid, version_no = _insert_profile_version(
        uid, content, body.reason, body.source.strip(), body.activate)
    return ok({"id": vid, "version_no": version_no, "active": body.activate,
               "note": f"档案版本 {version_no} 已创建" + ("并激活" if body.activate else "，等待激活")})


@router.post("/versions/{vid}/activate")
def activate_version(vid: str):
    uid = current_user_id()
    row = query_one("SELECT id FROM profile_versions WHERE id=? AND user_id=?",
                    (vid, uid))
    if not row:
        raise not_found("档案版本", vid)
    with transaction():
        execute("UPDATE profile_versions SET active=0 WHERE user_id=?", (uid,))
        execute("UPDATE profile_versions SET active=1 WHERE id=? AND user_id=?", (vid, uid))
    return ok({"activated": vid})


@router.get("/candidates")
def list_candidates():
    uid = current_user_id()
    return ok(query(
        "SELECT * FROM profile_field_candidates WHERE user_id=?"
        " ORDER BY created_at DESC", (uid,)))


@router.post("/candidates/{cid}/confirm")
def confirm_candidate(cid: str):
    row = query_one("SELECT * FROM profile_field_candidates WHERE id=? AND user_id=?",
                    (cid, current_user_id()))
    if not row:
        raise not_found("字段候选", cid)
    if row["status"] != "pending":
        raise state_invalid(f"字段候选 {cid} 已处理（{row['status']}），不能重复确认")
    execute("UPDATE profile_field_candidates SET status='confirmed', decided_at=? WHERE id=?",
            (now(), cid))
    return ok({"id": cid, "status": "confirmed",
               "note": "已进入待应用变更；只有确认变更预览后才会生成新档案版本"})


@router.post("/candidates/{cid}/reject")
def reject_candidate(cid: str):
    row = query_one("SELECT * FROM profile_field_candidates WHERE id=? AND user_id=?",
                    (cid, current_user_id()))
    if not row:
        raise not_found("字段候选", cid)
    execute("UPDATE profile_field_candidates SET status='rejected', decided_at=? WHERE id=?",
            (now(), cid))
    return ok({"id": cid, "status": "rejected",
               "note": "已拒绝：不会进入档案，拒绝记录已保留"})


class CandidateEditIn(BaseModel):
    value: str = Field(min_length=1, max_length=5000)
    decision_note: str = Field(default="", max_length=500)


@router.patch("/candidates/{cid}")
def edit_candidate(cid: str, body: CandidateEditIn):
    row = query_one("SELECT * FROM profile_field_candidates WHERE id=? AND user_id=?",
                    (cid, current_user_id()))
    if not row:
        raise not_found("字段候选", cid)
    if row["status"] != "pending":
        raise state_invalid("已处理的候选不能修改；可在正式档案编辑器中创建新版本")
    execute("UPDATE profile_field_candidates SET edited_value=?,decision_note=? WHERE id=?",
            (body.value.strip(), body.decision_note.strip(), cid))
    return ok({"id": cid, "value": body.value.strip(), "saved": True})


def _candidate_changes(uid: str, sid: str | None = None) -> tuple[list[dict], dict]:
    sql = ("SELECT * FROM profile_field_candidates WHERE user_id=? AND status='confirmed'")
    params: tuple = (uid,)
    if sid:
        sql += " AND session_id=?"
        params = (uid, sid)
    rows = query(sql + " ORDER BY created_at", params)
    changes: dict = {}
    accepted = []
    for row in rows:
        raw_path = str(row["field_path"] or "").strip()
        key = raw_path if raw_path in PROFILE_DEFAULTS else FIELD_PATH_MAP.get(raw_path)
        if not key:
            continue
        value = row.get("edited_value") or row["value"]
        if key in PROFILE_LIST_FIELDS:
            parsed = jloads(value, None)
            if not isinstance(parsed, list):
                parsed = [part.strip() for part in str(value).replace("；", "\n").splitlines()
                          if part.strip()]
            value = parsed
        changes[key] = value
        accepted.append({"candidate_id": row["id"], "field": key, "value": value,
                         "evidence": row["evidence"]})
    return accepted, changes


class ChangeSetIn(BaseModel):
    session_id: str | None = None


@router.post("/change-sets/preview")
def preview_change_set(body: ChangeSetIn):
    uid = current_user_id()
    if body.session_id:
        _owned_session(body.session_id)
    active = query_one("SELECT * FROM profile_versions WHERE user_id=? AND active=1", (uid,))
    base = {**PROFILE_DEFAULTS, **(jloads(active["content_json"], {}) if active else {})}
    accepted, changes = _candidate_changes(uid, body.session_id)
    if not changes:
        raise state_invalid("没有可应用的已确认候选；请先编辑并确认至少一个字段")
    merged = {**base, **changes}
    merged["field_sources"] = {**(base.get("field_sources") or {}),
                               **{item["field"]: item["evidence"] for item in accepted}}
    cid = new_id("PCS")
    preview = {"before": {k: base.get(k) for k in changes},
               "after": {k: merged.get(k) for k in changes},
               "candidate_count": len(accepted), "candidates": accepted}
    execute("INSERT INTO profile_change_sets VALUES(?,?,?,?,?,?,?,?,?)",
            (cid, uid, body.session_id, active["id"] if active else None, "PREVIEW",
             jdumps(changes), jdumps(preview), now(), None))
    return ok({"id": cid, "preview": preview})


@router.post("/change-sets/{change_set_id}/confirm")
def confirm_change_set(change_set_id: str):
    uid = current_user_id()
    row = query_one("SELECT * FROM profile_change_sets WHERE id=? AND user_id=?",
                    (change_set_id, uid))
    if not row:
        raise not_found("档案变更集", change_set_id)
    if row["status"] != "PREVIEW":
        raise state_invalid("该变更集已经处理")
    active = query_one("SELECT * FROM profile_versions WHERE user_id=? AND active=1", (uid,))
    base = {**PROFILE_DEFAULTS, **(jloads(active["content_json"], {}) if active else {})}
    changes = jloads(row["changes_json"], {})
    merged = {**base, **changes}
    merged["field_sources"] = {**(base.get("field_sources") or {}),
                               **{key: f"访谈变更集 {change_set_id}" for key in changes}}
    with transaction():
        vid, version_no = _insert_profile_version(
            uid, merged, "确认访谈候选变更", f"访谈变更集 {change_set_id}", True)
        execute("UPDATE profile_change_sets SET status='CONFIRMED',confirmed_at=? WHERE id=?",
                (now(), change_set_id))
        candidate_ids = [item.get("candidate_id") for item in
                         jloads(row["preview_json"], {}).get("candidates", [])]
        for candidate_id in candidate_ids:
            if candidate_id:
                execute("UPDATE profile_field_candidates SET status='applied' WHERE id=? AND user_id=?",
                        (candidate_id, uid))
    return ok({"profile_version_id": vid, "version_no": version_no, "active": True})


@router.get("/corpus")
def list_corpus():
    uid = current_user_id()
    rows = query("SELECT * FROM corpus_items WHERE user_id=?", (uid,))
    for r in rows:
        r["perms"] = jloads(r["perms_json"], {})
    return ok(rows)


@router.get("/style")
def get_style():
    from .. import config
    if config.DEMO_MODE:
        return ok(STYLE_PROFILE)
    row = query_one("SELECT content_json FROM profile_versions WHERE user_id=? AND active=1",
                    (current_user_id(),))
    if not row:
        return ok({})
    profile = jloads(row["content_json"], {})
    styles = profile.get("expression_style") or []
    rules = profile.get("spoken_style_rules") or []
    samples = profile.get("voice_samples") or []
    boundaries = profile.get("expression_boundaries") or []
    return ok({
        "整体气质": {"value": styles[0] if styles else "待补充", "src": "当前激活 IP 档案"},
        "句式节奏": {"value": rules[0] if rules else (styles[1] if len(styles) > 1 else "待补充"),
                     "src": "对谈记录 + 口吻指纹"},
        "逻辑承接": {"value": rules[1] if len(rules) > 1 else "先给判断，再用真实经历或流程解释",
                     "src": "本人朗读修正"},
        "原话样本": {"value": samples[0] if samples else "待补充", "src": "原话样本池"},
        "表达边界": {"value": boundaries[0] if boundaries else "不得虚构经历或承诺结果",
                     "src": "当前激活 IP 档案"},
    })


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
    s = _owned_session(sid)
    s["turns"] = query(
        "SELECT * FROM interview_turns WHERE session_id=? ORDER BY idx", (sid,))
    s["archives"] = query(
        "SELECT * FROM interview_archive_revisions WHERE session_id=? AND user_id=?"
        " ORDER BY revision_no DESC", (sid, current_user_id()))
    for item in s["archives"]:
        item["tags"] = jloads(item["tags_json"], [])
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
    s = _owned_session(sid)
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
    s = _owned_session(sid)
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
    _owned_session(sid)
    execute("UPDATE interview_sessions SET status='PAUSED', updated_at=?"
            " WHERE id=?", (now(), sid))
    return ok({"id": sid, "status": "PAUSED",
               "note": "已保存当前进度，中断不会生成「建档成功」的假结果"})


@router.post("/interviews/{sid}/finish")
def finish_interview(sid: str):
    s = _owned_session(sid)
    turns = query("SELECT * FROM interview_turns WHERE session_id=? ORDER BY idx",
                  (sid,))
    llm = get_llm_provider()
    cands = llm.interview_extract(s["topic"], turns)
    uid = current_user_id()
    ids = []
    for c in cands:
        cid = new_id("fc")
        execute("INSERT INTO profile_field_candidates"
                "(id,user_id,session_id,field_path,value,inference_type,confidence,evidence,status,created_at)"
                " VALUES(?,?,?,?,?,?,?,?,?,?)",
                (cid, uid, sid, c["field_path"], c["value"], c["inference_type"],
                 c["confidence"], c["evidence"], "pending", now()))
        ids.append(cid)
    user_turns = [str(item["text"]).strip() for item in turns if item["role"] == "user"]
    transcript = "\n\n".join(
        f"{('访谈助手' if item['role'] == 'assistant' else '我')}：{item['text']}"
        for item in turns)
    summary = "；".join(user_turns[:5])[:1500]
    existing_archive = query_one(
        "SELECT MAX(revision_no) AS n FROM interview_archive_revisions WHERE session_id=?", (sid,))
    revision = int((existing_archive or {}).get("n") or 0) + 1
    execute("INSERT INTO interview_archive_revisions VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (new_id("IAR"), sid, uid, revision, f"{s['topic']} · 访谈存档",
             summary, transcript, jdumps([s["topic"]]), "系统根据原始轮次生成，可人工修订",
             "system", now()))
    execute("UPDATE interview_sessions SET status='REVIEW_REQUIRED',"
            " updated_at=? WHERE id=?", (now(), sid))
    return ok({"candidates": ids,
               "note": "字段候选已生成，逐项确认后才会写入档案版本草稿；"
                       "推断（inferred）字段不会自动进入正式档案"})


class ArchiveRevisionIn(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    summary: str = Field(default="", max_length=10000)
    corrected_transcript: str = Field(min_length=1, max_length=100000)
    tags: list[str] = []
    notes: str = Field(default="", max_length=5000)


@router.post("/interviews/{sid}/archive-revisions")
def revise_interview_archive(sid: str, body: ArchiveRevisionIn):
    _owned_session(sid)
    latest = query_one(
        "SELECT COALESCE(MAX(revision_no),0) AS n FROM interview_archive_revisions"
        " WHERE session_id=? AND user_id=?", (sid, current_user_id()))
    revision = int(latest["n"] or 0) + 1
    archive_id = new_id("IAR")
    execute("INSERT INTO interview_archive_revisions VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (archive_id, sid, current_user_id(), revision, body.title.strip(),
             body.summary.strip(), body.corrected_transcript.strip(),
             jdumps([item.strip() for item in body.tags if item.strip()]),
             body.notes.strip(), "user", now()))
    return ok({"id": archive_id, "revision_no": revision,
               "note": "已保存为新的人工修订版本；原始访谈轮次未被覆盖"})
