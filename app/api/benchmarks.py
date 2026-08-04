"""对标监控 API：账号 CRUD、扫描、作品墙、作者分析。"""
import time

from fastapi import APIRouter
from pydantic import BaseModel

from .. import benchmarks as bm_svc
from ..datasource import PLATFORM_LABELS, detect_platform, get_datasource
from ..db import execute, query, query_one
from ..errors import not_found
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
    link: str
    platform: str | None = None
    scan_range: str = "30"
    note: str = ""


@router.post("")
def add_benchmark(body: BenchmarkIn):
    uid = current_user_id()
    result = bm_svc.add_benchmark(uid, body.link, body.platform,
                                  body.scan_range, body.note)
    return ok({"note": f"已添加并扫描完成：新作品 {result['new']} 条，生成信号 {len(result['signals'])} 个",
               **result})


@router.get("/{bm_id}")
def benchmark_detail(bm_id: str):
    bm = query_one("SELECT * FROM benchmark_accounts WHERE id=?", (bm_id,))
    if not bm:
        raise not_found("对标账号", bm_id)
    works = query("SELECT * FROM benchmark_works WHERE bm_id=?"
                  " ORDER BY create_time DESC", (bm_id,))
    analysis = query_one("SELECT * FROM author_analyses WHERE bm_id=?"
                         " ORDER BY created_at DESC", (bm_id,))
    return ok({**_bm_full(bm), "works": works,
               "analysis": analysis})


@router.post("/{bm_id}/scan")
def scan(bm_id: str):
    result = bm_svc.scan_benchmark(bm_id, emit_signals=True)
    return ok({"note": f"扫描完成：新增 {result['new']} 条，更新 {result['updated']} 条，"
                       f"新信号 {len(result['signals'])} 个", **result})


@router.post("/{bm_id}/read")
def mark_read(bm_id: str):
    execute("UPDATE benchmark_accounts SET has_update=0 WHERE id=?", (bm_id,))
    return ok({})


@router.delete("/{bm_id}")
def delete_benchmark(bm_id: str):
    bm = query_one("SELECT * FROM benchmark_accounts WHERE id=?", (bm_id,))
    if not bm:
        raise not_found("对标账号", bm_id)
    execute("DELETE FROM benchmark_works WHERE bm_id=?", (bm_id,))
    execute("DELETE FROM author_analyses WHERE bm_id=?", (bm_id,))
    execute("DELETE FROM benchmark_accounts WHERE id=?", (bm_id,))
    return ok({"note": f"已删除对标账号「{bm['name'] or bm['uid']}」及其作品缓存（信号与已提取资产保留）"})


@router.post("/{bm_id}/analyze")
def analyze(bm_id: str):
    result = bm_svc.author_analysis(bm_id)
    return ok(result)


@router.get("/{bm_id}/works/{video_id}")
def work_detail(bm_id: str, video_id: str):
    w = query_one("SELECT * FROM benchmark_works WHERE bm_id=? AND video_id=?",
                  (bm_id, video_id))
    if not w:
        raise not_found("作品", video_id)
    return ok(w)


class ScanAllOut(BaseModel):
    pass


@router.post("/scan-all")
def scan_all():
    uid = current_user_id()
    rows = query("SELECT id FROM benchmark_accounts WHERE user_id=?", (uid,))
    job = create_job(uid, "benchmark_patrol",
                     {"title": f"巡检全部对标账号（{len(rows)} 个）",
                      "bm_ids": [r["id"] for r in rows]}, cost_est=0.0)
    return ok({"job_id": job["id"], "note": "巡检任务已排队（公开元数据 · 低成本）"})
