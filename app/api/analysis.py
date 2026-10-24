"""内容拆解：拆解任务、拆解结果、内容模式卡。拆解 ≠ 仿写。"""
import hashlib
from fastapi import APIRouter
from pydantic import BaseModel, Field

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
        sc = query_one("SELECT title,signal_id,platform,status,assets_json FROM source_contents WHERE id=?",
                       (a["source_content_id"],))
        a["source_title"] = sc["title"] if sc else a["source_content_id"]
        a["signal_id"] = sc["signal_id"] if sc else None
        a["source_platform"] = sc["platform"] if sc else ""
        a["source_status"] = sc["status"] if sc else "missing"
        if sc:
            from .extraction import _resolved_text
            source_text, source_text_kind = _resolved_text(jloads(sc.get("assets_json"), {}))
            a["original_text_kind"] = source_text_kind
            a["original_text_chars"] = len(source_text)
        else:
            a["original_text_kind"] = "来源不存在"
            a["original_text_chars"] = 0
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
    source = owned_row("source_contents", row["source_content_id"], current_user_id(),
                       label="来源内容")
    source_assets = jloads(source.get("assets_json"), {})
    from .extraction import _resolved_text
    source_text, source_text_kind = _resolved_text(source_assets)
    row["source"] = {
        "id": source["id"], "title": source["title"],
        "platform": source["platform"], "author": source["author"],
        "url": source["url"], "status": source["status"],
        "text": source_text, "text_kind": source_text_kind,
        "text_chars": len(source_text),
    }
    return ok(row)


class AnalysisJobIn(BaseModel):
    source_content_id: str
    title: str
    source_chain: list[str] = []
    force_reanalyze: bool = False


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
    assets = jloads(source.get("assets_json"), {})
    transcript = next((str((assets.get(key) or {}).get("text") or "")
                       for key in ("clean", "asr", "caption")
                       if (assets.get(key) or {}).get("exists")
                       and (assets.get(key) or {}).get("text")), "")
    transcript_hash = hashlib.sha256(transcript.encode("utf-8")).hexdigest()[:16]
    operation_key = f"analysis:{source['id']}:{transcript_hash}"
    if body.force_reanalyze:
        operation_key = f"{operation_key}:manual:{new_id('run')}"
    job = create_job(uid, "analysis", payload, cost_est=cost_est,
                     idempotency_key=operation_key, reuse_succeeded=not body.force_reanalyze)
    note = ("拆解任务已排队 · 演示成本 ¥0.35" if config.DEMO_MODE
            else "拆解任务已排队 · Token 用量将写入台账，金额以 Provider 账单为准")
    if job.get("reused"):
        note = "相同正文已经拆解并沉淀，本次直接复用原任务；如需新版本请使用“重新拆解”。"
    return ok({"job_id": job["id"], "reused": bool(job.get("reused")), "note": note})


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


def _comparison_key() -> str:
    return f"analysis_comparison:{current_user_id()}"


@router.get("/comparison-items")
def comparison_items():
    row = query_one("SELECT value_json FROM app_settings WHERE key=?", (_comparison_key(),))
    saved = jloads(row["value_json"], []) if row else []
    output = []
    for item in saved:
        analysis = query_one("SELECT id,source_content_id,result_json,created_at FROM analyses"
                             " WHERE id=? AND user_id=?", (item.get("analysis_id"), current_user_id()))
        if not analysis:
            continue
        source = query_one("SELECT title FROM source_contents WHERE id=?", (analysis["source_content_id"],))
        result = jloads(analysis["result_json"], {})
        output.append({**item, "title": source["title"] if source else analysis["id"],
                       "topic": result.get("topic", ""),
                       "hook": (result.get("hook") or {}).get("text", ""),
                       "created_at": analysis["created_at"]})
    return ok(output)


class ComparisonItemIn(BaseModel):
    analysis_id: str
    performance_bucket: str = Field(pattern="^(high|medium|low)$")
    note: str = Field(default="", max_length=300)


@router.post("/comparison-items")
def add_comparison_item(body: ComparisonItemIn):
    owned_row("analyses", body.analysis_id, current_user_id(), label="拆解")
    row = query_one("SELECT value_json FROM app_settings WHERE key=?", (_comparison_key(),))
    saved = jloads(row["value_json"], []) if row else []
    saved = [item for item in saved if item.get("analysis_id") != body.analysis_id]
    saved.append(body.model_dump())
    execute("INSERT OR REPLACE INTO app_settings(key,value_json) VALUES(?,?)",
            (_comparison_key(), jdumps(saved)))
    return ok({"note": "已加入对比组；可继续补齐高/中/低表现样本", "count": len(saved)})
