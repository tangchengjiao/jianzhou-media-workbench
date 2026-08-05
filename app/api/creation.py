"""创作工坊：项目、差异化方向、稿件版本、检查、发布前判断（锁定只读）。"""
import datetime

from fastapi import APIRouter
from pydantic import BaseModel

from ..db import execute, jdumps, jloads, query, query_one
from ..errors import prediction_locked, state_invalid
from ..providers import get_llm_provider
from .. import config
from ..ids import new_id
from ..context_resolver import resolve_context
from ..repositories import evidence, owned_row
from ..jobs import record_usage
from . import current_user_id, now, ok

router = APIRouter(prefix="/creation", tags=["creation"])


def _record_generation(llm, task: str, endpoint: str) -> None:
    usage = getattr(llm, "last_usage", {}) or {}
    units = (f"input={usage.get('prompt_tokens', 0)} tokens · "
             f"output={usage.get('completion_tokens', 0)} tokens") if usage else "1 次"
    record_usage(current_user_id(), task, getattr(llm, "name", "demo"), endpoint,
                 getattr(llm, "last_model", ""), 0.0, units)


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
        "note": "已执行确定性句长与来源连续重复检查；未接入的检查明确标记，不生成分数。",
    }


def _project_full(pid: str) -> dict:
    p = owned_row("creation_projects", pid, current_user_id(), label="创作项目")
    p["source_chain"] = jloads(p["source_chain_json"], [])
    p["outline"] = jloads(p["outline_json"], [])
    p["context"] = jloads(p["context_json"], {})
    p["checks"] = jloads(p["checks_json"], {})
    p["drafts"] = query(
        "SELECT * FROM draft_versions WHERE project_id=? ORDER BY version_no", (pid,))
    p["prediction"] = query_one(
        "SELECT * FROM predictions WHERE project_id=?", (pid,))
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
    title: str
    source_chain: list[str] = []
    goal: str = "收藏率 > 账号均值"
    source_content_id: str | None = None


@router.post("/projects")
def create_project(body: ProjectIn):
    uid = current_user_id()
    pid = new_id("CP")
    if body.source_content_id:
        owned_row("source_contents", body.source_content_id, uid, label="来源内容")
    context_run = resolve_context(uid, "creation", source_content_id=body.source_content_id,
                                  source_chain=body.source_chain)
    ctx = {"context_run_id": context_run["run_id"], "refs": context_run["refs"],
           "tokens": context_run["token_estimate"], "isolation": context_run["isolation"],
           "content": context_run["content"]}
    execute("INSERT INTO creation_projects VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
            (pid, uid, body.title, body.goal, "待确认",
             jdumps(body.source_chain), "", jdumps([]), jdumps(ctx),
            jdumps({}), "CANDIDATE", now()))
    evidence(uid, "creation_project", pid, "project_created",
             refs=[context_run["run_id"], *(body.source_chain or [])],
             payload={"title": body.title, "goal": body.goal})
    return ok({"id": pid})


@router.get("/projects/{pid}/directions")
def directions(pid: str):
    p = _project_full(pid)
    llm = get_llm_provider()
    result = llm.diff_directions(p["title"], p["context"])
    _record_generation(llm, f"差异化方向 · {p['title']}", "creation_directions")
    return ok(result)


class DirectionIn(BaseModel):
    direction: str


@router.post("/projects/{pid}/direction")
def choose_direction(pid: str, body: DirectionIn):
    project = _project_full(pid)
    llm = get_llm_provider()
    outline = llm.generate_outline(project["title"], body.direction, project["context"])
    _record_generation(llm, f"创作提纲 · {project['title']}", "creation_outline")
    execute("UPDATE creation_projects SET diff_strategy=?, outline_json=?,"
            " status='OUTLINING' WHERE id=?",
            (body.direction, jdumps(outline), pid))
    return ok({"outline": outline, "note": "提纲已按新方向生成" + ("（演示）" if config.DEMO_MODE else "")})


@router.post("/projects/{pid}/draft")
def generate_draft(pid: str):
    p = _project_full(pid)
    llm = get_llm_provider()
    content = llm.generate_draft(p["title"], p["diff_strategy"] or "同结构 · 不同案例",
                                 p["context"], "抖音口播")
    _record_generation(llm, f"抖音口播初稿 · {p['title']}", "creation_draft")
    ver = (query_one("SELECT COALESCE(MAX(version_no),0) m FROM draft_versions"
                     " WHERE project_id=?", (pid,))["m"] or 0) + 1
    execute("INSERT INTO draft_versions VALUES(?,?,?,?,?,?,?)",
            (f"dv-{pid}-{ver}", pid, ver, "抖音口播",
             content, "初稿生成（演示）" if config.DEMO_MODE else "初稿生成（真实模型）", now()))
    execute("UPDATE creation_projects SET status='DRAFTING' WHERE id=?", (pid,))
    checks = _draft_checks(p, content)
    execute("UPDATE creation_projects SET checks_json=? WHERE id=?",
            (jdumps(checks), pid))
    return ok({"version": ver, "content": content, "checks": checks})


class MultiDraftIn(BaseModel):
    platforms: list[str] = ["抖音口播", "小红书图文", "B站视频简介", "视频号口播"]


@router.post("/projects/{pid}/drafts/generate")
def generate_multi_platform_drafts(pid: str, body: MultiDraftIn):
    p = _project_full(pid)
    allowed = {"抖音口播", "小红书图文", "B站视频简介", "视频号口播", "微博"}
    platforms = list(dict.fromkeys(body.platforms))
    if not platforms or any(item not in allowed for item in platforms):
        raise state_invalid("请选择受支持的创作平台")
    llm = get_llm_provider()
    start = (query_one("SELECT COALESCE(MAX(version_no),0) m FROM draft_versions "
                       "WHERE project_id=?", (pid,))["m"] or 0)
    drafts = []
    for offset, platform in enumerate(platforms, start=1):
        content = llm.generate_draft(
            p["title"], p["diff_strategy"] or "同结构 · 不同案例",
            p["context"], platform,
        )
        _record_generation(llm, f"{platform}初稿 · {p['title']}", "creation_draft")
        version = start + offset
        execute("INSERT INTO draft_versions VALUES(?,?,?,?,?,?,?)",
                (new_id("DV"), pid, version, platform, content,
                 "多平台真实模型生成" if not config.DEMO_MODE else "多平台演示生成", now()))
        drafts.append({"version": version, "platform": platform, "content": content})
    execute("UPDATE creation_projects SET status='DRAFTING' WHERE id=?", (pid,))
    evidence(current_user_id(), "creation_project", pid, "multi_platform_drafts_generated",
             refs=[p["context"].get("context_run_id")],
             payload={"platforms": platforms, "versions": [d["version"] for d in drafts]})
    return ok({"drafts": drafts})


class DraftIn(BaseModel):
    content: str
    note: str = "手动修改"
    platform: str = "抖音口播"


@router.put("/projects/{pid}/draft")
def save_draft(pid: str, body: DraftIn):
    _project_full(pid)
    ver = (query_one("SELECT COALESCE(MAX(version_no),0) m FROM draft_versions"
                     " WHERE project_id=?", (pid,))["m"] or 0) + 1
    execute("INSERT INTO draft_versions VALUES(?,?,?,?,?,?,?)",
            (f"dv-{pid}-{ver}", pid, ver, body.platform, body.content, body.note, now()))
    return ok({"version": ver, "note": "已保存为新版本（稿件版本只增不改）"})


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
    p = _project_full(pid)
    existing = p["prediction"]
    if existing and existing["locked"]:
        raise prediction_locked()
    if existing:
        execute("DELETE FROM predictions WHERE id=?", (existing["id"],))
    pred_id = f"pred-{pid}"
    execute("INSERT INTO predictions VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (pred_id, pid, body.goal, body.expected_bucket, body.confidence,
             body.strengths, body.risks, body.hypothesis, body.user_judgement,
             0, None))
    return ok({"id": pred_id, "locked": False})


@router.post("/projects/{pid}/prediction/lock")
def lock_prediction(pid: str):
    p = _project_full(pid)
    pred = p["prediction"]
    if not pred:
        raise state_invalid("尚无发布前判断可锁定，请先保存判断")
    if pred["locked"]:
        raise prediction_locked()
    execute("UPDATE predictions SET locked=1, locked_at=? WHERE id=?",
            (now(), pred["id"]))
    return ok({"locked": True, "locked_at": now(),
               "note": "PREDICTION_LOCKED · 之后仅可创建新版本或追加复盘"})
