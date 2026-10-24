"""创作工坊：项目、差异化方向、稿件版本、检查、发布前判断（锁定只读）。"""
import datetime
import difflib
import hashlib
import json
import re

from fastapi import APIRouter
from pydantic import BaseModel, Field

from ..db import execute, jdumps, jloads, query, query_one, transaction
from ..errors import prediction_locked, state_invalid, validation_failed
from ..providers import get_llm_provider
from .. import config
from ..ids import new_id
from ..context_resolver import resolve_context
from ..idea_store import create_idea, get_owned as get_owned_idea, link_campaign
from ..repositories import evidence, owned_row
from ..jobs import record_usage
from . import current_user_id, now, ok, today

router = APIRouter(prefix="/creation", tags=["creation"])


def _validated_platform(value: str) -> str:
    platform = str(value or "").strip()
    if not platform or re.fullmatch(r"[?？\s]+", platform):
        raise validation_failed("平台名称为空或编码异常，请重新选择发布平台")
    if len(platform) > 80:
        raise validation_failed("平台名称过长")
    return platform


def _record_generation(llm, task: str, endpoint: str) -> None:
    usage = getattr(llm, "last_usage", {}) or {}
    units = (f"input={usage.get('prompt_tokens', 0)} tokens · "
             f"output={usage.get('completion_tokens', 0)} tokens") if usage else "1 次"
    record_usage(current_user_id(), task, getattr(llm, "name", "demo"), endpoint,
                 getattr(llm, "last_model", ""), 0.0, units)


def _draft_hash(content: str) -> str:
    return hashlib.sha256(str(content or "").encode("utf-8")).hexdigest()


def _invalidate_quality_gate(pid: str) -> None:
    """Any new draft invalidates candidate-final and keeps planning in writing."""
    execute(
        "UPDATE creation_projects SET candidate_draft_version_id=NULL,candidate_confirmed_at=NULL,"
        "quality_gate_status='NEEDS_ASSESSMENT' WHERE id=?",
        (pid,),
    )


def _draft_diff(before: str, after: str) -> dict:
    before_lines = str(before or "").splitlines()
    after_lines = str(after or "").splitlines()
    ratio = difflib.SequenceMatcher(None, str(before or ""), str(after or "")).ratio()
    changes = list(difflib.unified_diff(before_lines, after_lines, lineterm=""))
    return {
        "similarity": round(ratio, 4),
        "changed_lines": sum(1 for line in changes if line.startswith(("+", "-"))
                             and not line.startswith(("+++", "---"))),
        "before_chars": len(str(before or "")),
        "after_chars": len(str(after or "")),
        "preview": changes[:80],
    }


def _normalized_draft_text(value: str) -> str:
    """Ignore formatting-only whitespace when deciding whether a version changed."""
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _validated_instruction_trace(result: dict, content: str, instructions: str) -> list[dict]:
    """Return only instruction evidence that can be found in the saved draft."""
    if not instructions.strip():
        return []
    compact_content = re.sub(r"\s+", "", content)
    traces = result.get("instruction_trace") if isinstance(result.get("instruction_trace"), list) else []
    validated = []
    for item in traces:
        if not isinstance(item, dict):
            continue
        evidence_text = str(item.get("evidence") or "").strip()
        applied = bool(item.get("applied"))
        if applied and evidence_text and re.sub(r"\s+", "", evidence_text) in compact_content:
            validated.append({"requirement": str(item.get("requirement") or instructions)[:1000],
                              "applied": True, "evidence": evidence_text[:2000],
                              "note": str(item.get("note") or "")[:1000]})
    return validated


def _draft_checks(project: dict, content: str) -> dict:
    source = (((project.get("context") or {}).get("content") or {}).get("source") or {})
    transcript = str(source.get("transcript") or "")
    compact_source = "".join(transcript.split())
    compact_draft = "".join(content.split())
    overlaps = []
    if compact_source and compact_draft:
        for index in range(0, max(0, len(compact_source) - 15), 8):
            phrase = compact_source[index:index + 16]
            if len(phrase) == 16 and phrase in compact_draft and phrase not in overlaps:
                overlaps.append(phrase)
                if len(overlaps) == 5:
                    break
    sentences = [part.strip() for part in content.replace("！", "。").replace("？", "。").split("。")
                 if part.strip()]
    longest = max((len(item) for item in sentences), default=0)
    filler_words = ("然后", "其实", "我觉得", "相当于", "就是说")
    filler_hits = sum(content.count(item) for item in filler_words)
    generic_opening = any(content.lstrip().startswith(item) for item in
                          ("大家好", "今天我们来聊聊", "随着", "在当今"))
    return {
        "style_match": {"label": "风格画像量化匹配", "status": "warn",
                        "value": "尚未接入评分器，不生成虚假百分比"},
        "style_rules": {"label": "句长检查", "status": "ok" if longest <= 80 else "warn",
                        "value": f"最长句 {longest} 字"},
        "source_dup": {"label": "与来源内容 16 字连续重复", "status": "warn" if overlaps else "ok",
                       "value": f"发现 {len(overlaps)} 处", "samples": overlaps},
        "history_dup": {"label": "与本人历史内容重复", "status": "warn",
                        "value": "历史语料比对器尚未接入"},
        "fact_density": {"label": "本人事实与观点密度", "status": "warn",
                         "value": "需要人工复核事实来源"},
        "opening": {"label": "开头有效性", "status": "warn" if generic_opening else "ok",
                    "value": "发现泛化开场" if generic_opening else "未发现常见泛化开场"},
        "filler": {"label": "口头禅密度", "status": "warn" if filler_hits > 10 else "ok",
                   "value": f"常见口头禅 {filler_hits} 次"},
        "note": "已执行确定性句长、泛化开头、口头禅与来源连续重复检查；未接入的语义检查明确标记。",
    }


def _project_full(pid: str) -> dict:
    p = owned_row("creation_projects", pid, current_user_id(), label="创作项目")
    p["source_chain"] = jloads(p["source_chain_json"], [])
    p["outline"] = jloads(p["outline_json"], [])
    p["context"] = jloads(p["context_json"], {})
    p["checks"] = jloads(p["checks_json"], {})
    p["drafts"] = query(
        "SELECT * FROM draft_versions WHERE project_id=? ORDER BY version_no", (pid,))
    p["assessments"] = query(
        "SELECT * FROM draft_assessments WHERE project_id=? AND user_id=? "
        "ORDER BY created_at DESC,version_no DESC", (pid, current_user_id()))
    for assessment in p["assessments"]:
        assessment["dimensions"] = jloads(assessment.get("dimensions_json"), [])
        assessment["issues"] = jloads(assessment.get("issues_json"), [])
        assessment["strengths"] = jloads(assessment.get("strengths_json"), [])
    latest_draft = p["drafts"][-1] if p["drafts"] else None
    p["assessment"] = next(
        (item for item in p["assessments"]
         if latest_draft and item.get("draft_version_id") == latest_draft.get("id")), None)
    p["revision_runs"] = query(
        "SELECT * FROM draft_revision_runs WHERE project_id=? AND user_id=? ORDER BY created_at DESC",
        (pid, current_user_id()))
    for revision in p["revision_runs"]:
        revision["selected_issue_ids"] = jloads(revision.get("selected_issue_ids_json"), [])
        revision["change_log"] = jloads(revision.get("change_log_json"), [])
        revision["diff"] = jloads(revision.get("diff_json"), {})
    candidate_id = p.get("candidate_draft_version_id")
    p["candidate_draft"] = next((item for item in p["drafts"] if item["id"] == candidate_id), None)
    p["predictions"] = query(
        "SELECT * FROM predictions WHERE project_id=? ORDER BY version_no DESC,created_at DESC",
        (pid,))
    for prediction in p["predictions"]:
        prediction["details"] = jloads(prediction.get("details_json"), {})
        prediction["override"] = jloads(prediction.get("override_json"), {})
    p["prediction"] = p["predictions"][0] if p["predictions"] else None
    p["direction_runs"] = query(
        "SELECT * FROM creation_direction_runs WHERE project_id=? ORDER BY version_no DESC", (pid,))
    for run in p["direction_runs"]:
        run["candidates"] = jloads(run.get("candidates_json"), [])
    p["direction_decisions"] = query(
        "SELECT * FROM creation_direction_decisions WHERE project_id=? ORDER BY created_at DESC", (pid,))
    for decision in p["direction_decisions"]:
        decision["candidate"] = jloads(decision.get("candidate_json"), {})
    p["direction_decision"] = next(
        (item for item in p["direction_decisions"] if item.get("active")), None)
    p["linked_tasks"] = query(
        "SELECT * FROM content_tasks WHERE project_id=? AND user_id=? ORDER BY id",
        (pid, current_user_id()),
    )
    prediction = p["prediction"]
    prediction_matches = bool(
        latest_draft and prediction and prediction.get("locked")
        and prediction.get("draft_version_id") == latest_draft.get("id")
        and p.get("quality_gate_status") == "FINAL_CANDIDATE"
        and p.get("candidate_draft_version_id") == latest_draft.get("id")
    )
    ready_tasks = [task for task in p["linked_tasks"] if task.get("status") == "READY_TO_PUBLISH"]
    if not latest_draft:
        blocking_reason = "还没有可确认的稿件版本"
    elif (p.get("quality_gate_status") != "FINAL_CANDIDATE"
          or p.get("candidate_draft_version_id") != latest_draft.get("id")):
        blocking_reason = "请先完成稿件体检并确认当前版本为候选终稿"
    elif not prediction:
        blocking_reason = "请先运行 AI 发布前预测"
    elif not prediction.get("locked"):
        blocking_reason = "请先复核并锁定当前预测"
    elif not prediction_matches:
        blocking_reason = "锁定预测对应的是旧稿；当前稿件变化后必须重新预测并确认"
    elif not ready_tasks:
        blocking_reason = "当前稿件与预测已锁定，等待确认进入待发布"
    else:
        blocking_reason = ""
    p["finalization"] = {
        "latest_draft_id": latest_draft.get("id") if latest_draft else None,
        "prediction_id": prediction.get("id") if prediction else None,
        "prediction_draft_id": prediction.get("draft_version_id") if prediction else None,
        "prediction_locked": bool(prediction and prediction.get("locked")),
        "prediction_matches_latest": prediction_matches,
        "ready": bool(prediction_matches and ready_tasks),
        "task_ids": [task["id"] for task in p["linked_tasks"]],
        "ready_task_ids": [task["id"] for task in ready_tasks],
        "blocking_reason": blocking_reason,
    }
    p["quality_gate"] = {
        "status": p.get("quality_gate_status") or "NOT_ASSESSED",
        "latest_draft_id": latest_draft.get("id") if latest_draft else None,
        "latest_assessment_id": p["assessment"].get("id") if p["assessment"] else None,
        "candidate_draft_id": candidate_id,
        "candidate_confirmed_at": p.get("candidate_confirmed_at"),
        "candidate_is_latest": bool(latest_draft and candidate_id == latest_draft.get("id")),
        "ready_for_prediction": bool(latest_draft and candidate_id == latest_draft.get("id")
                                     and p.get("quality_gate_status") == "FINAL_CANDIDATE"),
    }
    return p


@router.get("/projects")
def list_projects():
    uid = current_user_id()
    rows = query("SELECT id, title, status, goal, created_at"
                 " FROM creation_projects WHERE user_id=? ORDER BY created_at DESC",
                 (uid,))
    return ok(rows)


@router.get("/projects/{pid}")
def get_project(pid: str):
    return ok(_project_full(pid))


class ProjectIn(BaseModel):
    title: str = ""
    source_chain: list[str] = Field(default_factory=list)
    goal: str = "待确认"
    audience: str = ""
    source_content_id: str | None = None
    analysis_id: str | None = None
    campaign_id: str | None = None
    plan_task_id: str | None = None
    idea_id: str | None = None
    idea_body: str = Field(default="", max_length=20000)
    personal_judgement: str = Field(default="", max_length=10000)
    idea_type: str = Field(default="self_idea", max_length=40)
    source_url: str = Field(default="", max_length=2048)
    tags: list[str] = Field(default_factory=list)


def _ensure_public_plan_task(uid: str, project_id: str, campaign_id: str,
                             title: str) -> dict:
    """Make a campaign-backed creation project immediately executable in planning.

    Older projects may predate the campaign workflow, so this helper is called for
    both newly-created and reused projects.  The database's partial unique index on
    active project tasks makes the INSERT OR IGNORE safe under duplicate clicks.
    """
    existing = query_one(
        "SELECT id,status FROM content_tasks WHERE user_id=? AND project_id=? "
        "AND status<>'CANCELLED' ORDER BY id LIMIT 1",
        (uid, project_id),
    )
    if existing:
        return {**existing, "created": False}
    task_id = new_id("CT")
    execute(
        "INSERT OR IGNORE INTO content_tasks(id,user_id,project_id,title,status,platform,"
        "scheduled_date,estimate_h,priority,badges_json,source_json,campaign_id,delivery_kind) "
        "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (task_id, uid, project_id, title, "CANDIDATE", "", today(), 1.5, "mid",
         jdumps([{"t": "内容母题", "c": "b-pri"},
                 {"t": "公域创作", "c": "b-info"}]),
         jdumps({"kind": "public_content", "campaign_id": campaign_id,
                 "project_id": project_id}), campaign_id, "public_content"),
    )
    saved = query_one(
        "SELECT id,status FROM content_tasks WHERE user_id=? AND project_id=? "
        "AND status<>'CANCELLED' ORDER BY id LIMIT 1",
        (uid, project_id),
    )
    if not saved:
        raise state_invalid("创作项目已建立，但内容计划任务未能同步，请重试。")
    created = saved["id"] == task_id
    if created:
        evidence(uid, "content_task", task_id, "campaign_creation_scheduled",
                 refs=[campaign_id, project_id],
                 payload={"title": title, "status": "CANDIDATE"})
    return {**saved, "created": created}


@router.post("/projects")
def create_project(body: ProjectIn):
    uid = current_user_id()
    plan_task = None
    if body.plan_task_id:
        plan_task = owned_row("content_tasks", body.plan_task_id, uid, label="内容计划任务")
        if plan_task.get("project_id"):
            existing_project = owned_row(
                "creation_projects", plan_task["project_id"], uid, label="创作项目"
            )
            return ok({"id": existing_project["id"],
                       "campaign_id": existing_project.get("campaign_id"),
                       "plan_task_id": plan_task["id"],
                       "plan_task_status": plan_task["status"],
                       "reused": True,
                       "note": "该计划任务已经绑定创作项目，本次没有重复创建。"})
    campaign = None
    campaign_id = body.campaign_id
    idea = None
    idea_id = body.idea_id
    if idea_id:
        idea = get_owned_idea(uid, idea_id)
        if not idea:
            raise validation_failed("选择的灵感记录不存在或不属于当前用户。")
    if campaign_id:
        campaign = owned_row("content_campaigns", campaign_id, uid, label="内容母题")
        existing = query_one(
            "SELECT id,campaign_id,title FROM creation_projects WHERE user_id=? AND campaign_id=? "
            "ORDER BY created_at,id LIMIT 1",
            (uid, campaign_id),
        )
        if existing:
            task = _ensure_public_plan_task(uid, existing["id"], campaign_id,
                                            existing.get("title") or campaign.get("title") or "公域创作")
            return ok({"id": existing["id"], "campaign_id": campaign_id,
                       "plan_task_id": task["id"], "plan_task_status": task["status"],
                       "reused": True,
                       "note": "这个内容母题已有公域项目和计划任务，已打开既有项目，没有重复创建。"})
    # Only create a new first-party record after the reuse check above.  This
    # prevents duplicate clicks on an existing campaign from leaving orphan ideas.
    if not idea_id and body.idea_body.strip():
        try:
            idea = create_idea(
                uid, body=body.idea_body, title=body.title,
                item_type=body.idea_type, personal_judgement=body.personal_judgement,
                source_url=body.source_url, tags=body.tags,
            )
        except ValueError as exc:
            raise validation_failed(str(exc)) from exc
        idea_id = idea["id"]
    pid = new_id("CP")
    campaign_id = campaign_id or new_id("CMP")
    if body.source_content_id:
        owned_row("source_contents", body.source_content_id, uid, label="来源内容")
    if body.analysis_id:
        analysis = owned_row("analyses", body.analysis_id, uid, label="拆解")
        if body.source_content_id and analysis["source_content_id"] != body.source_content_id:
            raise state_invalid("拆解结果与来源内容不属于同一条证据链")
    source_chain = list(body.source_chain or [])
    if campaign and not source_chain:
        source_chain = jloads(campaign.get("source_chain_json"), [])
    if idea_id and idea_id not in source_chain:
        source_chain.append(idea_id)
    idea_title = str((idea or {}).get("title") or (idea or {}).get("body") or "").strip()
    title = body.title.strip() or (campaign or {}).get("title") or idea_title[:80] or "未命名内容母题"
    goal = body.goal.strip() or (campaign or {}).get("objective") or (campaign or {}).get("core_topic") or "待确认"
    audience = body.audience.strip() or (campaign or {}).get("audience") or "待确认"
    context_run = resolve_context(uid, "creation", source_content_id=body.source_content_id,
                                  source_chain=source_chain)
    ctx = {"context_run_id": context_run["run_id"], "refs": context_run["refs"],
           "tokens": context_run["token_estimate"], "isolation": context_run["isolation"],
           "content": context_run["content"]}
    stamp = now()
    with transaction():
        if not campaign:
            execute(
                "INSERT INTO content_campaigns(id,user_id,title,core_topic,source_kind,source_ref_type,"
                "source_ref_id,source_chain_json,objective,audience,status,created_at,updated_at,archived_at) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,NULL)",
                (campaign_id, uid, title, title, "inspiration" if idea_id else "creation_project",
                 "content_idea" if idea_id else "creation_project",
                 idea_id or pid, jdumps(source_chain), goal, audience, "ACTIVE", stamp, stamp),
            )
        else:
            execute("UPDATE content_campaigns SET updated_at=? WHERE id=? AND user_id=?",
                    (stamp, campaign_id, uid))
        inserted = execute(
            ("INSERT OR IGNORE INTO " if campaign else "INSERT INTO ") +
            "creation_projects(id,user_id,title,goal,audience,source_chain_json,"
            "diff_strategy,outline_json,context_json,checks_json,status,created_at,"
            "source_content_id,analysis_id,campaign_id) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (pid, uid, title, goal, audience, jdumps(source_chain), "",
             jdumps([]), jdumps(ctx), jdumps({}), "CANDIDATE", stamp,
             body.source_content_id, body.analysis_id, campaign_id),
        )
        if campaign and inserted == 0:
            existing = query_one(
                "SELECT id,title FROM creation_projects WHERE user_id=? AND campaign_id=?",
                (uid, campaign_id),
            )
            task = _ensure_public_plan_task(uid, existing["id"], campaign_id,
                                            existing.get("title") or title)
            return ok({"id": existing["id"], "campaign_id": campaign_id,
                       "plan_task_id": task["id"], "plan_task_status": task["status"],
                       "reused": True,
                       "note": "这个内容母题已有公域项目和计划任务，已打开既有项目，没有重复创建。"})
        if idea_id:
            link_campaign(uid, campaign_id, idea_id, "creation_source")
        if plan_task:
            execute(
                "UPDATE content_tasks SET project_id=?,campaign_id=?,delivery_kind='public_content' "
                "WHERE id=? AND user_id=?",
                (pid, campaign_id, plan_task["id"], uid),
            )
            task = {"id": plan_task["id"], "status": plan_task["status"], "created": False}
        else:
            task = _ensure_public_plan_task(uid, pid, campaign_id, title)
        evidence(uid, "creation_project", pid, "project_created",
                 refs=[context_run["run_id"], campaign_id, *source_chain],
                 payload={"title": title, "goal": goal,
                          "source_content_id": body.source_content_id,
                          "analysis_id": body.analysis_id,
                          "campaign_id": campaign_id,
                          "idea_id": idea_id})
    return ok({"id": pid, "campaign_id": campaign_id,
               "idea_id": idea_id,
               "plan_task_id": task["id"],
               "plan_task_status": task["status"],
               "reused": False,
               "note": "已建立公域创作项目并同步到内容计划；稿件与视觉资产会沿用同一条证据链。"})


def _direction_candidates(run: dict | None) -> list[dict]:
    if not run:
        return []
    return [
        {**item, "run_id": run["id"], "run_version": run["version_no"],
         "candidate_index": index}
        for index, item in enumerate(jloads(run.get("candidates_json"), []))
    ]


@router.get("/projects/{pid}/directions")
def directions(pid: str):
    _project_full(pid)
    run = query_one(
        "SELECT * FROM creation_direction_runs WHERE project_id=? ORDER BY version_no DESC LIMIT 1",
        (pid,))
    return ok(_direction_candidates(run))


@router.post("/projects/{pid}/directions/generate")
def generate_directions(pid: str):
    p = _project_full(pid)
    llm = get_llm_provider()
    result = llm.diff_directions(p["title"], p["context"])
    if not isinstance(result, list) or not result or any(not isinstance(item, dict) for item in result):
        raise state_invalid("模型没有返回可用的差异化方向，本次没有写入空记录")
    version = int((query_one(
        "SELECT COALESCE(MAX(version_no),0) n FROM creation_direction_runs WHERE project_id=?",
        (pid,)) or {}).get("n") or 0) + 1
    context_hash = hashlib.sha256(jdumps({"title": p["title"], "context": p["context"]}).encode("utf-8")).hexdigest()
    run_id = new_id("CDR")
    execute(
        "INSERT INTO creation_direction_runs(id,user_id,project_id,version_no,context_hash,candidates_json,model,provider,created_at) "
        "VALUES(?,?,?,?,?,?,?,?,?)",
        (run_id, current_user_id(), pid, version, context_hash, jdumps(result),
         getattr(llm, "last_model", ""), getattr(llm, "name", ""), now()),
    )
    _record_generation(llm, f"差异化方向 · {p['title']}", "creation_directions")
    evidence(current_user_id(), "creation_direction_run", run_id, "directions_generated",
             refs=[p["context"].get("context_run_id")],
             payload={"project_id": pid, "version_no": version, "candidate_count": len(result)})
    return ok(_direction_candidates({"id": run_id, "version_no": version,
                                     "candidates_json": jdumps(result)}))


class DirectionIn(BaseModel):
    direction: str
    run_id: str | None = None
    candidate_index: int | None = None
    reason: str = ""


@router.post("/projects/{pid}/direction")
def choose_direction(pid: str, body: DirectionIn):
    project = _project_full(pid)
    candidate = {}
    if body.run_id:
        run = query_one(
            "SELECT * FROM creation_direction_runs WHERE id=? AND project_id=? AND user_id=?",
            (body.run_id, pid, current_user_id()))
        if not run:
            raise state_invalid("差异化方向批次不存在或不属于当前项目")
        candidates = jloads(run.get("candidates_json"), [])
        if body.candidate_index is not None and 0 <= body.candidate_index < len(candidates):
            candidate = candidates[body.candidate_index]
    direction_text = body.direction.strip()
    if not direction_text:
        raise validation_failed("请选择一个非空的差异化方向")
    llm = get_llm_provider()
    outline = llm.generate_outline(project["title"], direction_text, project["context"])
    _record_generation(llm, f"创作提纲 · {project['title']}", "creation_outline")
    decision_id = new_id("CDD")
    with transaction():
        execute("UPDATE creation_direction_decisions SET active=0 WHERE project_id=? AND active=1", (pid,))
        execute(
            "INSERT INTO creation_direction_decisions(id,user_id,project_id,run_id,candidate_key,direction_text,candidate_json,reason,active,created_at) "
            "VALUES(?,?,?,?,?,?,?,?,?,?)",
            (decision_id, current_user_id(), pid, body.run_id,
             str(body.candidate_index if body.candidate_index is not None else "manual"),
             direction_text, jdumps(candidate), body.reason.strip(), 1, now()),
        )
        execute("UPDATE creation_projects SET diff_strategy=?, outline_json=?,status='OUTLINING',"
                "selected_direction_decision_id=? WHERE id=?",
                (direction_text, jdumps(outline), decision_id, pid))
        _sync_linked_task_status(
            pid, "OUTLINING",
            ("CANDIDATE", "SELECTED", "DRAFTING", "PRODUCING", "READY_TO_PUBLISH"),
        )
    evidence(current_user_id(), "creation_direction_decision", decision_id, "direction_selected",
             refs=[body.run_id] if body.run_id else [],
             payload={"project_id": pid, "direction": direction_text, "reason": body.reason})
    return ok({"outline": outline, "decision_id": decision_id,
               "note": "差异化方向与提纲已存档" + ("（演示）" if config.DEMO_MODE else "")})


class DraftGenerateIn(BaseModel):
    style_mode: str = "light"
    platform: str = "抖音口播"


def _validate_style_mode(style_mode: str) -> str:
    if style_mode not in {"structure", "light", "deep"}:
        raise state_invalid("稿件风格模式必须是 structure、light 或 deep")
    return style_mode


def _generation_context(project: dict) -> dict:
    return {**project["context"], "_outline": project["outline"],
            "_goal": project.get("goal") or ""}


def _sync_linked_task_status(pid: str, target: str, from_states: tuple[str, ...]) -> None:
    """Keep the planning card in step with explicit creation actions."""
    if not from_states:
        return
    marks = ",".join("?" for _ in from_states)
    execute(
        f"UPDATE content_tasks SET status=? WHERE project_id=? AND status IN ({marks})",
        (target, pid, *from_states),
    )


def _insert_generated_draft(pid: str, version: int, platform: str, content: str,
                            style_mode: str, llm, checks: dict, note: str) -> str:
    draft_id = new_id("DV")
    project = query_one("SELECT selected_direction_decision_id FROM creation_projects WHERE id=?", (pid,)) or {}
    execute(
        "INSERT INTO draft_versions(id,project_id,version_no,platform,content,note,created_at,"
        "style_mode,model,provider,quality_json,direction_decision_id) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
        (draft_id, pid, version, platform, content, note, now(), style_mode,
         getattr(llm, "last_model", ""), getattr(llm, "name", "demo"), jdumps(checks),
         project.get("selected_direction_decision_id")),
    )
    _invalidate_quality_gate(pid)
    _sync_linked_task_status(
        pid, "DRAFTING", ("CANDIDATE", "SELECTED", "OUTLINING", "PRODUCING", "READY_TO_PUBLISH"))
    return draft_id


@router.post("/projects/{pid}/draft")
def generate_draft(pid: str, body: DraftGenerateIn | None = None):
    body = body or DraftGenerateIn()
    style_mode = _validate_style_mode(body.style_mode)
    platform = _validated_platform(body.platform)
    p = _project_full(pid)
    llm = get_llm_provider()
    content = llm.generate_draft(p["title"], p["diff_strategy"] or "同结构 · 不同案例",
                                 _generation_context(p), platform, style_mode)
    if not str(content).strip():
        raise state_invalid("模型没有返回可用正文，本次不会创建空白稿件版本")
    _record_generation(llm, f"{platform}初稿 · {p['title']} · {style_mode}", "creation_draft")
    ver = (query_one("SELECT COALESCE(MAX(version_no),0) m FROM draft_versions"
                     " WHERE project_id=?", (pid,))["m"] or 0) + 1
    checks = _draft_checks(p, content)
    labels = {"structure": "结构优先", "light": "轻量口吻", "deep": "深度口吻"}
    draft_id = _insert_generated_draft(
        pid, ver, platform, content, style_mode, llm, checks,
        f"{labels[style_mode]}生成（{'演示' if config.DEMO_MODE else '真实模型'}）",
    )
    execute("UPDATE creation_projects SET status='DRAFTING' WHERE id=?", (pid,))
    execute("UPDATE creation_projects SET checks_json=? WHERE id=?",
            (jdumps(checks), pid))
    return ok({"id": draft_id, "version": ver, "content": content, "checks": checks,
               "style_mode": style_mode, "platform": platform,
               "autosaved": True, "note": f"已自动保存为 v{ver}"})


class DraftCompareIn(BaseModel):
    platform: str = "抖音口播"


@router.post("/projects/{pid}/drafts/compare")
def generate_draft_comparison(pid: str, body: DraftCompareIn):
    """显式生成结构基准版与轻量口吻版，供用户真实比较后选择。"""
    platform = _validated_platform(body.platform)
    p = _project_full(pid)
    llm = get_llm_provider()
    start = (query_one("SELECT COALESCE(MAX(version_no),0) m FROM draft_versions "
                       "WHERE project_id=?", (pid,))["m"] or 0)
    generated = []
    for offset, mode in enumerate(("structure", "light"), start=1):
        content = llm.generate_draft(
            p["title"], p["diff_strategy"] or "同结构 · 不同案例",
            _generation_context(p), platform, mode,
        )
        if not str(content).strip():
            raise state_invalid("模型没有返回完整的 A/B 稿件，本次不会写入半套版本")
        _record_generation(llm, f"稿件A/B · {p['title']} · {mode}", "creation_draft_compare")
        generated.append({"version": start + offset, "content": content, "mode": mode,
                          "checks": _draft_checks(p, content),
                          "model": getattr(llm, "last_model", ""),
                          "provider": getattr(llm, "name", "demo")})
    with transaction():
        for item in generated:
            _insert_generated_draft(
                pid, item["version"], platform, item["content"], item["mode"], llm,
                item["checks"], "A/B结构基准版" if item["mode"] == "structure" else "A/B轻量口吻版",
            )
        execute("UPDATE creation_projects SET status='DRAFTING' WHERE id=?", (pid,))
    return ok({"drafts": generated, "note": "结构基准版与轻量口吻版已分别保存，旧版本未覆盖"})


class MultiDraftIn(BaseModel):
    platforms: list[str] = ["抖音口播", "小红书图文", "B站视频简介", "视频号口播"]
    style_mode: str = "light"


@router.post("/projects/{pid}/drafts/generate")
def generate_multi_platform_drafts(pid: str, body: MultiDraftIn):
    p = _project_full(pid)
    allowed = {"抖音口播", "小红书图文", "B站视频简介", "视频号口播", "微博"}
    platforms = [_validated_platform(item) for item in dict.fromkeys(body.platforms)]
    if not platforms or any(item not in allowed for item in platforms):
        raise state_invalid("请选择受支持的创作平台")
    llm = get_llm_provider()
    style_mode = _validate_style_mode(body.style_mode)
    start = (query_one("SELECT COALESCE(MAX(version_no),0) m FROM draft_versions "
                       "WHERE project_id=?", (pid,))["m"] or 0)
    drafts = []
    for offset, platform in enumerate(platforms, start=1):
        content = llm.generate_draft(
            p["title"], p["diff_strategy"] or "同结构 · 不同案例",
            _generation_context(p), platform, style_mode,
        )
        if not str(content).strip():
            raise state_invalid(f"{platform} 没有返回可用正文，本次多平台生成未保存任何版本")
        _record_generation(llm, f"{platform}初稿 · {p['title']}", "creation_draft")
        version = start + offset
        drafts.append({"id": new_id("DV"), "version": version,
                       "platform": platform, "content": content,
                       "checks": _draft_checks(p, content),
                       "model": getattr(llm, "last_model", ""),
                       "provider": getattr(llm, "name", "demo")})
    # 所有模型调用成功后再一次性写库。任一平台超时/失败时，不留下半套版本。
    with transaction():
        for draft in drafts:
            decision = query_one("SELECT selected_direction_decision_id FROM creation_projects WHERE id=?", (pid,)) or {}
            execute(
                "INSERT INTO draft_versions(id,project_id,version_no,platform,content,note,created_at,"
                "style_mode,model,provider,quality_json,direction_decision_id) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                (draft["id"], pid, draft["version"], draft["platform"],
                 draft["content"],
                 "多平台真实模型生成" if not config.DEMO_MODE else "多平台演示生成",
                  now(), style_mode, draft["model"], draft["provider"], jdumps(draft["checks"]),
                 decision.get("selected_direction_decision_id")),
            )
        _invalidate_quality_gate(pid)
        _sync_linked_task_status(
            pid, "DRAFTING", ("CANDIDATE", "SELECTED", "OUTLINING", "PRODUCING", "READY_TO_PUBLISH"))
        execute("UPDATE creation_projects SET status='DRAFTING' WHERE id=?", (pid,))
        evidence(current_user_id(), "creation_project", pid,
                 "multi_platform_drafts_generated",
                 refs=[p["context"].get("context_run_id")],
                 payload={"platforms": platforms,
                          "versions": [d["version"] for d in drafts]})
    for draft in drafts:
        draft.pop("id", None)
    return ok({"drafts": drafts, "autosaved": True,
               "note": f"{len(drafts)} 个平台版本已全部自动保存"})


class DraftIn(BaseModel):
    content: str
    note: str = "手动修改"
    platform: str = "抖音口播"


@router.put("/projects/{pid}/draft")
def save_draft(pid: str, body: DraftIn):
    project = _project_full(pid)
    platform = _validated_platform(body.platform)
    if not body.content.strip():
        raise state_invalid("稿件正文不能为空；空白内容不会保存为版本")
    latest = project["drafts"][-1] if project["drafts"] else None
    if (latest and platform == latest.get("platform")
            and _normalized_draft_text(body.content) == _normalized_draft_text(latest.get("content"))):
        raise state_invalid("正文和当前版本完全相同，没有创建重复版本")
    ver = (query_one("SELECT COALESCE(MAX(version_no),0) m FROM draft_versions"
                     " WHERE project_id=?", (pid,))["m"] or 0) + 1
    checks = _draft_checks(_project_full(pid), body.content)
    execute(
        "INSERT INTO draft_versions(id,project_id,version_no,platform,content,note,created_at,"
        "style_mode,model,provider,quality_json,direction_decision_id) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
        (f"dv-{pid}-{ver}", pid, ver, platform, body.content, body.note, now(),
         "manual", "", "user", jdumps(checks), project.get("selected_direction_decision_id")),
    )
    execute("UPDATE creation_projects SET status='DRAFTING',checks_json=? WHERE id=?",
            (jdumps(checks), pid))
    _invalidate_quality_gate(pid)
    _sync_linked_task_status(
        pid, "DRAFTING", ("CANDIDATE", "SELECTED", "OUTLINING", "PRODUCING", "READY_TO_PUBLISH"))
    return ok({"version": ver, "note": "已保存为新版本（稿件版本只增不改）"})


class DraftAssessmentIn(BaseModel):
    draft_version_id: str | None = None


def _normalize_assessment(result: dict) -> dict:
    try:
        composite = max(0.0, min(100.0, float(result.get("composite_score", 0))))
    except (TypeError, ValueError):
        raise state_invalid("稿件体检没有返回有效综合分")
    dimensions = result.get("dimensions") if isinstance(result.get("dimensions"), list) else []
    issues = result.get("issues") if isinstance(result.get("issues"), list) else []
    strengths = result.get("strengths") if isinstance(result.get("strengths"), list) else []
    if not dimensions:
        raise state_invalid("稿件体检没有返回可解释评分维度，本次不会保存空结果")
    normalized_issues = []
    used_ids = set()
    for index, item in enumerate(issues):
        if not isinstance(item, dict):
            continue
        issue_id = re.sub(r"[^a-zA-Z0-9_-]+", "_", str(item.get("id") or f"issue_{index + 1}"))[:80]
        if issue_id in used_ids:
            issue_id = f"{issue_id}_{index + 1}"
        used_ids.add(issue_id)
        normalized_issues.append({
            "id": issue_id,
            "dimension": str(item.get("dimension") or "general")[:80],
            "severity": str(item.get("severity") or "medium").lower()
            if str(item.get("severity") or "medium").lower() in {"high", "medium", "low"}
            else "medium",
            "quote": str(item.get("quote") or "")[:500],
            "problem": str(item.get("problem") or "")[:1000],
            "action": str(item.get("action") or "")[:1000],
            "example": str(item.get("example") or "")[:1000],
            "expected_impact": str(item.get("expected_impact") or "")[:500],
            "side_effect": str(item.get("side_effect") or "")[:500],
            "confidence": str(item.get("confidence") or "medium")[:40],
        })
    return {
        "composite_score": round(composite, 1),
        "dimensions": dimensions,
        "issues": normalized_issues,
        "strengths": [str(item)[:500] for item in strengths],
        "summary": str(result.get("summary") or "")[:2000],
        "rubric_version": str(result.get("rubric_version") or "workspace-quality-v1")[:80],
    }


@router.post("/projects/{pid}/assessments/generate")
def generate_draft_assessment(pid: str, body: DraftAssessmentIn | None = None):
    body = body or DraftAssessmentIn()
    project = _project_full(pid)
    draft_id = body.draft_version_id or (project["drafts"][-1]["id"] if project["drafts"] else None)
    draft = next((item for item in project["drafts"] if item["id"] == draft_id), None)
    if not draft or not str(draft.get("content") or "").strip():
        raise state_invalid("请选择一个已保存的非空稿件版本再运行体检")
    llm = get_llm_provider()
    normalized = _normalize_assessment(
        llm.assess_draft(project["title"], draft["content"], project["context"]))
    version_no = int((query_one(
        "SELECT COALESCE(MAX(version_no),0) n FROM draft_assessments "
        "WHERE project_id=? AND draft_version_id=?", (pid, draft["id"])) or {}).get("n") or 0) + 1
    assessment_id = new_id("DQA")
    created_at = now()
    with transaction():
        execute(
            "INSERT INTO draft_assessments(id,user_id,project_id,draft_version_id,version_no,"
            "rubric_version,composite_score,dimensions_json,issues_json,strengths_json,summary,"
            "source_hash,provider,model,status,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (assessment_id, current_user_id(), pid, draft["id"], version_no,
             normalized["rubric_version"], normalized["composite_score"],
             jdumps(normalized["dimensions"]), jdumps(normalized["issues"]),
             jdumps(normalized["strengths"]), normalized["summary"], _draft_hash(draft["content"]),
             getattr(llm, "name", ""), getattr(llm, "last_model", ""), "ACTIVE", created_at),
        )
        latest_id = project["drafts"][-1]["id"] if project["drafts"] else None
        if draft["id"] == latest_id and project.get("candidate_draft_version_id") != draft["id"]:
            execute("UPDATE creation_projects SET quality_gate_status='ASSESSED' WHERE id=?", (pid,))
        evidence(current_user_id(), "draft_assessment", assessment_id, "draft_quality_assessed",
                 refs=[draft["id"]], payload={"project_id": pid, "version_no": version_no,
                                             "score": normalized["composite_score"],
                                             "issue_count": len(normalized["issues"])})
    _record_generation(llm, f"稿件体检 · {project['title']} · v{draft['version_no']}",
                       "creation_draft_assessment")
    return ok({"id": assessment_id, "draft_version_id": draft["id"],
               "version_no": version_no, "created_at": created_at, **normalized,
               "note": f"稿件 v{draft['version_no']} 体检完成；这不是正式发布预测"})


class DraftOptimizeIn(BaseModel):
    assessment_id: str
    selected_issue_ids: list[str] = []
    instructions: str = ""


@router.post("/projects/{pid}/drafts/optimize")
def optimize_draft(pid: str, body: DraftOptimizeIn):
    project = _project_full(pid)
    assessment = query_one(
        "SELECT * FROM draft_assessments WHERE id=? AND project_id=? AND user_id=?",
        (body.assessment_id, pid, current_user_id()))
    if not assessment:
        raise state_invalid("稿件体检记录不存在或不属于当前项目")
    source_draft = next((item for item in project["drafts"]
                         if item["id"] == assessment["draft_version_id"]), None)
    if not source_draft:
        raise state_invalid("体检对应的来源稿件已经不存在")
    all_issues = jloads(assessment.get("issues_json"), [])
    issue_map = {str(item.get("id")): item for item in all_issues if isinstance(item, dict)}
    selected_ids = list(dict.fromkeys(str(item) for item in body.selected_issue_ids))
    missing = [item for item in selected_ids if item not in issue_map]
    if missing:
        raise validation_failed("所选优化问题不属于当前体检结果：" + "、".join(missing))
    selected = [issue_map[item] for item in selected_ids]
    if not selected and not body.instructions.strip():
        raise validation_failed("请至少选择一个体检问题，或填写本轮补充要求")
    llm = get_llm_provider()
    revised = llm.revise_draft(project["title"], source_draft["content"], selected,
                               body.instructions.strip(), project["context"])
    content = str(revised.get("content") or "").strip()
    if not content:
        raise state_invalid("AI没有返回可用的新稿件，本次不会创建空版本")
    if _normalized_draft_text(content) == _normalized_draft_text(source_draft["content"]):
        raise state_invalid("AI没有产生正文变化；请调整所选问题或补充要求后重试")
    instruction_trace = _validated_instruction_trace(revised, content, body.instructions)
    if body.instructions.strip() and not instruction_trace:
        raise state_invalid("AI没有提供可在新正文中验证的补充要求落实证据；本次不会创建新版本")
    version = int((query_one("SELECT COALESCE(MAX(version_no),0) n FROM draft_versions "
                             "WHERE project_id=?", (pid,)) or {}).get("n") or 0) + 1
    checks = _draft_checks(project, content)
    diff = _draft_diff(source_draft["content"], content)
    if not diff["changed_lines"] or float(diff["similarity"]) >= 0.998:
        raise state_invalid("优化稿与来源版本几乎相同；请扩大修改范围后重试，本次未创建重复版本")
    change_log = revised.get("change_log") if isinstance(revised.get("change_log"), list) else []
    if instruction_trace and not any(
        isinstance(item, dict) and item.get("issue_id") == "user_instruction" for item in change_log
    ):
        change_log.append({"issue_id": "user_instruction", "change": "已落实本轮补充要求",
                           "reason": body.instructions.strip()[:1000],
                           "evidence": instruction_trace[0]["evidence"]})
    revision_id = new_id("DQR")
    with transaction():
        target_id = _insert_generated_draft(
            pid, version, source_draft.get("platform") or "抖音口播", content,
            "quality_revision", llm, checks,
            f"AI优化自 v{source_draft['version_no']} · 体检 v{assessment['version_no']}",
        )
        execute(
            "INSERT INTO draft_revision_runs(id,user_id,project_id,assessment_id,"
            "source_draft_version_id,target_draft_version_id,selected_issue_ids_json,instructions,"
            "change_log_json,provider,model,created_at,diff_json) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (revision_id, current_user_id(), pid, assessment["id"], source_draft["id"], target_id,
             jdumps(selected_ids), body.instructions.strip(), jdumps(change_log),
             getattr(llm, "name", ""), getattr(llm, "last_model", ""), now(), jdumps(diff)),
        )
        execute("UPDATE creation_projects SET status='DRAFTING',checks_json=?,"
                "quality_gate_status='NEEDS_ASSESSMENT' WHERE id=?", (jdumps(checks), pid))
        evidence(current_user_id(), "draft_revision", revision_id, "draft_optimized_from_assessment",
                 refs=[assessment["id"], source_draft["id"], target_id],
                 payload={"selected_issue_ids": selected_ids, "diff": diff})
    _record_generation(llm, f"稿件优化 · {project['title']} · v{source_draft['version_no']}→v{version}",
                       "creation_draft_optimize")
    return ok({"id": target_id, "version": version, "version_no": version, "content": content,
               "source_draft_version_id": source_draft["id"], "assessment_id": assessment["id"],
               "revision_run_id": revision_id, "change_log": change_log,
               "instruction_trace": instruction_trace,
               "user_instruction_applied": bool(instruction_trace),
               "unresolved": revised.get("unresolved") or [], "diff": diff,
               "note": f"已按所选问题生成并保存 v{version}；来源 v{source_draft['version_no']} 未覆盖"})


class CandidateDraftIn(BaseModel):
    draft_version_id: str


@router.post("/projects/{pid}/candidate-final")
def confirm_candidate_draft(pid: str, body: CandidateDraftIn):
    project = _project_full(pid)
    latest = project["drafts"][-1] if project["drafts"] else None
    if not latest or latest["id"] != body.draft_version_id:
        raise state_invalid("只能确认当前最新稿件；如需使用旧版本，请先恢复为新的当前版本")
    assessment = next((item for item in project["assessments"]
                       if item["draft_version_id"] == latest["id"]), None)
    if not assessment:
        raise state_invalid("当前稿件尚未完成AI稿件体检，请先体检再确认候选终稿")
    confirmed_at = now()
    with transaction():
        execute("UPDATE creation_projects SET candidate_draft_version_id=?,candidate_confirmed_at=?,"
                "quality_gate_status='FINAL_CANDIDATE',status='DRAFTING' WHERE id=? AND user_id=?",
                (latest["id"], confirmed_at, pid, current_user_id()))
        _sync_linked_task_status(
            pid, "DRAFTING", ("CANDIDATE", "SELECTED", "OUTLINING", "PRODUCING", "READY_TO_PUBLISH"))
        evidence(current_user_id(), "creation_project", pid, "draft_confirmed_as_prediction_candidate",
                 refs=[latest["id"], assessment["id"]],
                 payload={"confirmed_at": confirmed_at, "draft_version": latest["version_no"]})
    return ok({"draft_version_id": latest["id"], "confirmed_at": confirmed_at,
               "quality_gate_status": "FINAL_CANDIDATE",
               "note": f"稿件 v{latest['version_no']} 已确认为候选终稿，可以生成正式盲预测"})


class PredictionIn(BaseModel):
    goal: str
    expected_bucket: str
    confidence: float
    strengths: str
    risks: str
    hypothesis: str
    user_judgement: str = ""


@router.post("/projects/{pid}/prediction")
def save_prediction(pid: str, body: PredictionIn):
    """Record the human review separately; never rewrite the AI prediction."""
    p = _project_full(pid)
    existing = p["prediction"]
    if not existing:
        raise state_invalid("请先运行 AI 发布前预测，再填写人工复核意见")
    if existing["locked"]:
        raise prediction_locked()
    override = {
        "review_note": body.user_judgement,
        "reviewed_at": now(),
        "ai_original": {key: existing.get(key) for key in
                        ("goal", "expected_bucket", "confidence", "strengths", "risks", "hypothesis")},
        "user_view": {"goal": body.goal, "expected_bucket": body.expected_bucket,
                      "confidence": body.confidence, "strengths": body.strengths,
                      "risks": body.risks, "hypothesis": body.hypothesis},
    }
    execute("UPDATE predictions SET user_judgement=?,override_json=? WHERE id=? AND locked=0",
            (body.user_judgement, jdumps(override), existing["id"]))
    return ok({"id": existing["id"], "locked": False,
               "note": "人工复核已单独记录；AI 原始预测没有被覆盖"})


def _calibration_confidence(uid: str) -> tuple[float, str, int]:
    count = int((query_one(
        "SELECT COUNT(DISTINCT p.id) AS n FROM publications p"
        " JOIN retro_reports r ON r.publication_id=p.id WHERE p.user_id=?", (uid,)) or {}).get("n") or 0)
    if count == 0:
        return 0.20, "极低（暂无完成复盘样本）", count
    if count <= 2:
        return 0.35, "低（仅1-2个复盘样本）", count
    if count <= 5:
        return 0.48, "偏低（3-5个复盘样本）", count
    if count <= 10:
        return 0.62, "中等（6-10个复盘样本）", count
    if count <= 20:
        return 0.76, "较高（11-20个复盘样本）", count
    return 0.86, "高（21个以上复盘样本）", count


@router.post("/projects/{pid}/prediction/generate")
def generate_prediction(pid: str):
    p = _project_full(pid)
    if query_one("SELECT id FROM publications WHERE project_id=? AND user_id=? LIMIT 1",
                 (pid, current_user_id())):
        raise state_invalid("该项目已经登记发布，不能再创建盲预测；请进入复盘查看真实数据")
    draft = p.get("candidate_draft")
    latest = p["drafts"][-1] if p["drafts"] else None
    if not draft or p.get("quality_gate_status") != "FINAL_CANDIDATE":
        raise state_invalid("请先完成稿件体检，并由你确认当前版本为候选终稿")
    if not latest or draft["id"] != latest["id"]:
        raise state_invalid("候选终稿已被后续稿件替代，请重新体检并确认最新版本")
    if not str(draft.get("content") or "").strip():
        raise state_invalid("候选终稿为空，不能生成正式发布预测")
    draft_hash = _draft_hash(draft["content"])
    previous = p["prediction"]
    if previous and previous["locked"] and previous.get("draft_hash") == draft_hash:
        return ok({**previous, "details": jloads(previous.get("details_json"), {}),
                   "note": "当前候选终稿已有锁定预测，未重复创建"})
    if previous and not previous["locked"] and previous.get("draft_hash") == draft_hash:
        return ok({**previous, "details": jloads(previous.get("details_json"), {}),
                   "note": "当前稿件已有未锁定预测，未重复调用模型"})
    llm = get_llm_provider()
    details = llm.predict_content(p["title"], draft["content"], p["context"])
    dimensions = details.get("dimensions") or []
    if not dimensions or any(not isinstance(item, dict) for item in dimensions):
        raise state_invalid("预测模型没有返回可解释评分维度，本次结果未保存")
    confidence, confidence_label, sample_count = _calibration_confidence(current_user_id())
    details["calibration"] = {"completed_retro_samples": sample_count,
                              "confidence_label": confidence_label,
                              "note": "置信度由已完成复盘样本数约束，不由模型自行夸大"}
    details["input_snapshot"] = {"draft_version_id": draft["id"],
                                 "draft_version": draft["version_no"],
                                 "draft_hash": draft_hash,
                                 "context_run_id": p["context"].get("context_run_id"),
                                 "assessment_id": p["assessment"].get("id") if p.get("assessment") else None,
                                 "quality_gate_status": p.get("quality_gate_status")}
    probabilities = details.get("bucket_probabilities") or {}
    expected_bucket = max(probabilities, key=probabilities.get) if probabilities else "待校准"
    version = int((query_one("SELECT COALESCE(MAX(version_no),0) AS n FROM predictions"
                             " WHERE project_id=?", (pid,)) or {}).get("n") or 0) + 1
    pred_id = new_id("PRED")
    strengths = "；".join(str(x) for x in (details.get("strengths") or []))
    risks = "；".join(str(x) for x in (details.get("risks") or []))
    hypothesis = str(details.get("hypothesis") or "")
    created_at = now()
    provider = getattr(llm, "name", "")
    model = getattr(llm, "last_model", "")
    with transaction():
        execute(
            "INSERT INTO predictions(id,project_id,goal,expected_bucket,confidence,strengths,risks,"
            "hypothesis,user_judgement,locked,locked_at,version_no,draft_version_id,draft_hash,"
            "rubric_version,model,provider,details_json,integrity_hash,blind_status,override_json,"
            "created_at,supersedes_id) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (pred_id, pid, p["goal"], expected_bucket, confidence, strengths, risks,
             hypothesis, "", 0, None, version, draft["id"], draft_hash,
             "workspace-v1", model, provider, jdumps(details), "", "blind", jdumps({}),
             created_at, previous["id"] if previous else None),
        )
        evidence(current_user_id(), "prediction", pred_id, "ai_prediction_generated",
                 refs=[draft["id"], p["context"].get("context_run_id")],
                 payload={"version": version, "draft_hash": draft_hash,
                          "model": model, "provider": provider})
    _record_generation(llm, f"发布前预测 · {p['title']}", "creation_prediction")
    return ok({"id": pred_id, "version_no": version, "locked": False,
               "confidence": confidence, "confidence_label": confidence_label,
               "expected_bucket": expected_bucket, "details": details})


def _finalize_project(pid: str, prediction: dict | None = None) -> dict:
    """Confirm the assessed candidate draft and synchronize every linked plan task."""
    uid = current_user_id()
    project = _project_full(pid)
    latest_draft = project["drafts"][-1] if project["drafts"] else None
    if not latest_draft or not str(latest_draft.get("content") or "").strip():
        raise state_invalid("当前项目没有可确认的非空稿件版本")
    if (project.get("quality_gate_status") != "FINAL_CANDIDATE"
            or project.get("candidate_draft_version_id") != latest_draft["id"]):
        raise state_invalid("请先完成稿件体检并确认当前版本为候选终稿")
    prediction = prediction or query_one(
        "SELECT * FROM predictions WHERE project_id=? AND locked=1 "
        "ORDER BY version_no DESC,locked_at DESC LIMIT 1",
        (pid,),
    )
    if not prediction or not prediction.get("locked"):
        raise state_invalid("请先运行、复核并锁定当前稿件的 AI 发布前预测")
    if prediction.get("draft_version_id") != latest_draft["id"]:
        raise state_invalid(
            "当前稿件在预测后发生了变化。请重新运行 AI 发布前预测并锁定，旧预测不会冒充当前稿件的判断。")
    terminal = {"PUBLISHED", "WAITING_RETRO", "RETRO_COMPLETED", "ARCHIVED", "CANCELLED"}
    tasks = query(
        "SELECT * FROM content_tasks WHERE project_id=? AND user_id=? ORDER BY id",
        (pid, uid),
    )
    active_tasks = [task for task in tasks if task.get("status") not in terminal]
    if tasks and not active_tasks:
        raise state_invalid("该创作项目已经发布或归档，不能再次进入待发布；如需二次创作请新建项目版本。")
    finalized_at = now()
    campaign_id = project.get("campaign_id")
    with transaction():
        if not active_tasks:
            task_id = new_id("CT")
            execute(
                "INSERT INTO content_tasks(id,user_id,project_id,title,status,platform,scheduled_date,"
                "estimate_h,priority,badges_json,source_json,creation_started_at,finalized_at,"
                "final_draft_version_id,campaign_id,delivery_kind) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (task_id, uid, pid, project["title"], "READY_TO_PUBLISH",
                 latest_draft.get("platform") or "待确认", today(), 1.5, "high",
                 jdumps(["创作工坊", "已确认定稿"]),
                 jdumps({"kind": "creation_finalization", "project_id": pid}),
                 project.get("created_at"), finalized_at, latest_draft["id"], campaign_id,
                 "public_content"),
            )
            active_tasks = [{"id": task_id}]
        else:
            execute(
                "UPDATE content_tasks SET status='READY_TO_PUBLISH',finalized_at=?,"
                "final_draft_version_id=?,campaign_id=COALESCE(campaign_id,?) WHERE project_id=? AND user_id=? "
                "AND status NOT IN ('PUBLISHED','WAITING_RETRO','RETRO_COMPLETED','ARCHIVED','CANCELLED')",
                (finalized_at, latest_draft["id"], campaign_id, pid, uid),
            )
        execute("UPDATE creation_projects SET status='READY_TO_PUBLISH' WHERE id=? AND user_id=?",
                (pid, uid))
        evidence(
            uid, "creation_project", pid, "final_draft_confirmed",
            refs=[latest_draft["id"], prediction["id"], *[task["id"] for task in active_tasks]],
            payload={"finalized_at": finalized_at,
                     "draft_version_id": latest_draft["id"],
                     "prediction_id": prediction["id"]},
        )
    return {
        "project_id": pid,
        "task_ids": [task["id"] for task in active_tasks],
        "status": "READY_TO_PUBLISH",
        "finalized_at": finalized_at,
        "draft_version_id": latest_draft["id"],
        "prediction_id": prediction["id"],
        "note": "当前稿件已确认定稿，内容计划已同步进入“待发布”；发布后登记链接即可进入快照与复盘。",
    }


@router.post("/projects/{pid}/finalize")
def finalize_project(pid: str):
    return ok(_finalize_project(pid))


@router.post("/projects/{pid}/prediction/lock")
def lock_prediction(pid: str):
    p = _project_full(pid)
    pred = p["prediction"]
    if not pred:
        raise state_invalid("尚无发布前判断可锁定，请先保存判断")
    if pred["locked"]:
        return ok(_finalize_project(pid, pred))
    locked_at = now()
    immutable = {key: pred.get(key) for key in (
        "id", "project_id", "goal", "expected_bucket", "confidence", "strengths",
        "risks", "hypothesis", "version_no", "draft_version_id", "draft_hash",
        "rubric_version", "model", "provider", "details_json", "override_json", "created_at")}
    integrity_hash = hashlib.sha256(
        json.dumps(immutable, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()
    with transaction():
        execute("UPDATE predictions SET locked=1, locked_at=?,integrity_hash=? WHERE id=? AND locked=0",
                (locked_at, integrity_hash, pred["id"]))
        pred = {**pred, "locked": 1, "locked_at": locked_at, "integrity_hash": integrity_hash}
        finalized = _finalize_project(pid, pred)
    return ok({**finalized, "locked": True, "locked_at": locked_at,
               "integrity_hash": integrity_hash})
