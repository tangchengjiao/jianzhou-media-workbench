"""对标监控 API：账号 CRUD、扫描、作品墙、作者分析。"""
from fastapi import APIRouter
from pydantic import BaseModel, Field

from .. import benchmarks as bm_svc
from ..datasource import PLATFORM_LABELS, SUPPORTED_PLATFORMS
from ..db import execute, jloads, query, query_one, transaction
from ..errors import not_found, validation_failed
from ..jobs import create_job
from . import current_user_id, ok

router = APIRouter(prefix="/benchmarks", tags=["benchmarks"])


def _bm_full(bm: dict) -> dict:
    works = query("SELECT * FROM benchmark_works WHERE bm_id=?"
                  " ORDER BY create_time DESC", (bm["id"],))
    hot = sum(1 for w in works if w["is_hot"])
    return {**bm, "platform_label": PLATFORM_LABELS.get(bm["platform"], bm["platform"]),
            "works_count": len(works), "hot_count": hot}


@router.get("")
def list_benchmarks():
    uid = current_user_id()
    rows = query("SELECT * FROM benchmark_accounts WHERE user_id=?"
                 " ORDER BY created_at DESC", (uid,))
    return ok([_bm_full(r) for r in rows])


class BenchmarkIn(BaseModel):
    link: str = Field(min_length=1, max_length=2048)
    platform: str | None = None
    scan_count: int | None = Field(default=None, ge=1, le=500)
    scan_range: str | None = None  # 兼容 Phase 2 旧客户端
    note: str = Field(default="", max_length=300)


@router.post("")
def add_benchmark(body: BenchmarkIn):
    uid = current_user_id()
    if body.platform and body.platform not in SUPPORTED_PLATFORMS:
        raise validation_failed(f"暂不支持平台：{body.platform}")
    result = bm_svc.add_benchmark(uid, body.link, body.platform,
                                  body.scan_range or "30", body.note,
                                  scan_count=body.scan_count)
    if result.get("sync_status") == "error":
        return ok({"note": "账号入口已保存，但首次真实同步失败；没有生成替代数据。可在账号卡片上查看原因并重试。",
                   **result})
    note_prefix = "账号已存在并完成刷新" if result.get("existing") else "已添加并扫描完成"
    return ok({"note": f"{note_prefix}：新作品 {result['new']} 条，生成信号 {len(result['signals'])} 个",
               **result})


def _owned_benchmark(bm_id: str) -> dict:
    bm = query_one(
        "SELECT * FROM benchmark_accounts WHERE id=? AND user_id=?",
        (bm_id, current_user_id()),
    )
    if not bm:
        raise not_found("对标账号", bm_id)
    return bm


@router.get("/{bm_id}")
def benchmark_detail(bm_id: str):
    bm = _owned_benchmark(bm_id)
    works = query("SELECT * FROM benchmark_works WHERE bm_id=?"
                  " ORDER BY create_time DESC", (bm_id,))
    analysis = query_one("SELECT * FROM author_analyses WHERE bm_id=?"
                         " ORDER BY created_at DESC", (bm_id,))
    if analysis:
        analysis = {**analysis, "result": jloads(analysis["result_json"], {})}
        analysis.pop("result_json", None)
    return ok({**_bm_full(bm), "works": works, "analysis": analysis})


@router.post("/{bm_id}/scan")
def scan(bm_id: str):
    _owned_benchmark(bm_id)
    result = bm_svc.scan_benchmark(bm_id, emit_signals=True)
    return ok({"note": f"扫描完成：新增 {result['new']} 条，更新 {result['updated']} 条，"
                       f"新信号 {len(result['signals'])} 个", **result})


@router.post("/{bm_id}/read")
def mark_read(bm_id: str):
    _owned_benchmark(bm_id)
    execute("UPDATE benchmark_accounts SET has_update=0 WHERE id=?", (bm_id,))
    return ok({})


@router.delete("/{bm_id}")
def delete_benchmark(bm_id: str):
    bm = _owned_benchmark(bm_id)
    with transaction():
        execute("DELETE FROM benchmark_accounts WHERE id=?", (bm_id,))
    return ok({"note": f"已删除对标账号「{bm['name'] or bm['uid']}」及其作品缓存（信号与已提取资产保留）"})


@router.post("/{bm_id}/analyze")
def analyze(bm_id: str):
    _owned_benchmark(bm_id)
    result = bm_svc.author_analysis(bm_id)
    return ok(result)


@router.get("/{bm_id}/works/{video_id}")
def work_detail(bm_id: str, video_id: str):
    _owned_benchmark(bm_id)
    w = query_one("SELECT * FROM benchmark_works WHERE bm_id=? AND video_id=?",
                  (bm_id, video_id))
    if not w:
        raise not_found("作品", video_id)
    return ok(w)


@router.post("/{bm_id}/works/{video_id}/to-signal")
def work_to_signal(bm_id: str, video_id: str):
    _owned_benchmark(bm_id)
    return ok(bm_svc.work_to_signal(bm_id, video_id))


class ScanAllOut(BaseModel):
    pass


@router.post("/scan-all")
def scan_all():
    uid = current_user_id()
    rows = query("SELECT id FROM benchmark_accounts WHERE user_id=?", (uid,))
    if not rows:
        return ok({"job_id": None, "note": "还没有对标账号，请先添加账号。"})
    job = create_job(uid, "benchmark_patrol",
                     {"title": f"巡检全部对标账号（{len(rows)} 个）",
                      "bm_ids": [r["id"] for r in rows]}, cost_est=0.0)
    return ok({"job_id": job["id"], "note": "巡检任务已排队（公开元数据 · 低成本）"})
