"""创作工坊：项目、差异化方向、稿件版本、检查、发布前判断（锁定只读）。"""
import datetime

from fastapi import APIRouter
from pydantic import BaseModel

from ..db import execute, jdumps, jloads, query, query_one
from ..errors import not_found, prediction_locked, state_invalid
from ..providers import get_llm_provider
from .. import config
from ..ids import new_id
from . import current_user_id, now, ok

router = APIRouter(prefix="/creation", tags=["creation"])


def _project_full(pid: str) -> dict:
    p = query_one("SELECT * FROM creation_projects WHERE id=?", (pid,))
    if not p:
        raise not_found("创作项目", pid)
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


@router.post("/projects")
def create_project(body: ProjectIn):
    uid = current_user_id()
    pid = new_id("CP")
    ctx = {
        "packages": ["IP资料包 v3.2", "表达画像 v1.4", "规则经验包 v2.0", "运营周期包 Q3"],
        "sources": ["来源内容 ×1", "本人案例 ×2（咨询记录 #C-31 #C-42）"],
        "tokens": 6800, "isolation": "禁止跨用户检索 · 引用 ID 已记录",
    }
    execute("INSERT INTO creation_projects VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
            (pid, uid, body.title, body.goal, "待确认",
             jdumps(body.source_chain), "", jdumps([]), jdumps(ctx),
             jdumps({}), "CANDIDATE", now()))
    return ok({"id": pid})


@router.get("/projects/{pid}/directions")
def directions(pid: str):
    p = query_one("SELECT title FROM creation_projects WHERE id=?", (pid,))
    if not p:
        raise not_found("创作项目", pid)
    return ok(get_llm_provider().diff_directions(p["title"]))


class DirectionIn(BaseModel):
    direction: str


@router.post("/projects/{pid}/direction")
def choose_direction(pid: str, body: DirectionIn):
    project = query_one("SELECT id,title FROM creation_projects WHERE id=?", (pid,)) \
        or (_ for _ in ()).throw(not_found("创作项目", pid))
    llm = get_llm_provider()
    outline = llm.generate_outline(project["title"], body.direction)
    execute("UPDATE creation_projects SET diff_strategy=?, outline_json=?,"
            " status='OUTLINING' WHERE id=?",
            (body.direction, jdumps(outline), pid))
    return ok({"outline": outline, "note": "提纲已按新方向生成" + ("（演示）" if config.DEMO_MODE else "")})


@router.post("/projects/{pid}/draft")
def generate_draft(pid: str):
    p = _project_full(pid)
    llm = get_llm_provider()
    content = llm.generate_draft(p["title"], p["diff_strategy"] or "同结构 · 不同案例")
    ver = (query_one("SELECT COALESCE(MAX(version_no),0) m FROM draft_versions"
                     " WHERE project_id=?", (pid,))["m"] or 0) + 1
    execute("INSERT INTO draft_versions VALUES(?,?,?,?,?,?,?)",
            (f"dv-{pid}-{ver}", pid, ver, "抖音口播",
             content, "初稿生成（演示）" if config.DEMO_MODE else "初稿生成（真实模型）", now()))
    execute("UPDATE creation_projects SET status='DRAFTING' WHERE id=?", (pid,))
    checks = {
        "style_match": {"label": "风格适配 · 表达画像 v1.4", "status": "ok", "value": "匹配 92%"},
        "style_rules": {"label": "句长 / 禁用词 / 开头习惯", "status": "ok", "value": "✓ ✓ ✓"},
        "source_dup": {"label": "与来源内容长句重复", "status": "warn", "value": "2 处待处理"},
        "history_dup": {"label": "与本人历史内容重复", "status": "ok", "value": "0 处"},
        "fact_density": {"label": "本人事实与观点密度", "status": "ok", "value": "充分"},
        "note": "原创性检查仅提示风险，不做法律结论。",
    }
    execute("UPDATE creation_projects SET checks_json=? WHERE id=?",
            (jdumps(checks), pid))
    return ok({"version": ver, "content": content, "checks": checks})


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
