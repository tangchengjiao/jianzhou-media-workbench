"""内容拆解：拆解任务、拆解结果、内容模式卡。拆解 ≠ 仿写。"""
from fastapi import APIRouter
from pydantic import BaseModel

from ..db import execute, jdumps, jloads, query, query_one
from ..jobs import create_job
from ..ids import new_id
from .. import config
from ..providers import get_setting
from ..repositories import owned_row
from . import current_user_id, now, ok

router = APIRouter(prefix="/analysis", tags=["analysis"])


@router.get("/list")
def list_analysis():
    uid = current_user_id()
    analyses = query("SELECT * FROM analyses WHERE user_id=? ORDER BY created_at DESC",
                     (uid,))
    for a in analyses:
        a["result"] = jloads(a["result_json"], {})
        sc = query_one("SELECT title FROM source_contents WHERE id=?",
                       (a["source_content_id"],))
        a["source_title"] = sc["title"] if sc else a["source_content_id"]
    pending = query(
        "SELECT id, title, status FROM source_contents"
        " WHERE user_id=? AND status='transcribed'"
        " AND id NOT IN (SELECT source_content_id FROM analyses WHERE user_id=?)",
        (uid, uid))
    return ok({"analyses": analyses, "pending_sources": pending})


@router.get("/detail/{aid}")
def analysis_detail(aid: str):
    row = owned_row("analyses", aid, current_user_id(), label="拆解")
    row["result"] = jloads(row["result_json"], {})
    return ok(row)


class AnalysisJobIn(BaseModel):
    source_content_id: str
    title: str
    source_chain: list[str] = []


@router.post("/plans")
def analysis_plan(body: AnalysisJobIn):
    source = owned_row("source_contents", body.source_content_id, current_user_id(),
                       label="来源内容")
    model = "demo_analysis_model" if config.DEMO_MODE else get_setting("llm", "analysis_model")
    return ok({
        "task_type": "analysis",
        "cost_est": 0.35 if config.DEMO_MODE else 0.0,
        "model": model,
        "steps": ["加载转写资产", "Context Resolver 显式加载上下文",
                  "深度模型拆解", "Schema 校验", "写库"],
        "requires_confirmation": True,
        "source": {"id": source["id"], "title": source["title"],
                   "status": source["status"]},
    })


@router.post("/jobs")
def analysis_job(body: AnalysisJobIn):
    uid = current_user_id()
    source = owned_row("source_contents", body.source_content_id, uid, label="来源内容")
    payload = body.model_dump()
    payload["title"] = source["title"]
    cost_est = 0.35 if config.DEMO_MODE else 0.0
    job = create_job(uid, "analysis", payload, cost_est=cost_est,
                     idempotency_key=f"analysis:{source['id']}")
    note = ("拆解任务已排队 · 演示成本 ¥0.35" if config.DEMO_MODE
            else "拆解任务已排队 · Token 用量将写入台账，金额以 Provider 账单为准")
    return ok({"job_id": job["id"], "note": note})


@router.get("/patterns")
def list_patterns():
    uid = current_user_id()
    return ok(query("SELECT * FROM patterns WHERE user_id=?", (uid,)))


class PatternIn(BaseModel):
    name: str
    note: str = ""


@router.post("/patterns")
def save_pattern(body: PatternIn):
    uid = current_user_id()
    pid = new_id("pt")
    execute("INSERT INTO patterns VALUES(?,?,?,?,?,?,?,?,?)",
            (pid, uid, body.name, "draft", 1, "低", "待补充证据",
             body.note or "从拆解保存的草稿模式卡", now()))
    return ok({"id": pid, "note": "已保存为模式卡草稿，积累证据后可提升置信度"})
