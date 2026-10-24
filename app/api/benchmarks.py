"""对标监控 API：账号 CRUD、扫描、作品墙、作者分析。"""
from fastapi import APIRouter, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from .. import benchmarks as bm_svc
from ..datasource import PLATFORM_LABELS, SUPPORTED_PLATFORMS
from ..db import execute, jloads, query, query_one, transaction
from ..errors import not_found, validation_failed
from ..jobs import create_job, list_jobs
from . import current_user_id, ok

router = APIRouter(prefix="/benchmarks", tags=["benchmarks"])


def _bm_full(bm: dict) -> dict:
    works = query("SELECT * FROM benchmark_works WHERE bm_id=?"
                  " ORDER BY create_time DESC", (bm["id"],))
    hot = sum(1 for w in works if w["is_hot"])
    return {**bm, "platform_label": PLATFORM_LABELS.get(bm["platform"], bm["platform"]),
            "works_count": len(works), "total_cached": len(works), "hot_count": hot}


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


@router.post("", status_code=202)
def add_benchmark(body: BenchmarkIn):
    uid = current_user_id()
    if body.platform and body.platform not in SUPPORTED_PLATFORMS:
        raise validation_failed(f"暂不支持平台：{body.platform}")
    count = bm_svc.normalize_scan_count(body.scan_range, body.scan_count)
    job = create_job(uid, "benchmark_onboard", {
        "title": f"添加对标账号 · {body.platform or '自动识别'}",
        "link": body.link.strip(), "platform": body.platform,
        "scan_count": count, "note": body.note,
    })
    return ok({"job_id": job["id"], "status": job["status"],
               "note": "已进入后台同步。你可以继续使用其他功能，完成后会通知并提供查看入口。"})


@router.get("/jobs")
def benchmark_jobs():
    return ok(list_jobs(current_user_id(), [
        "benchmark_onboard", "benchmark_refresh", "benchmark_backfill",
        "benchmark_patrol", "radar_source_sync",
    ])[:30])


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
    from ..cover_cache import browser_needs_cover_proxy
    for work in works:
        cover = str(work.get("cover") or "").strip()
        work["cover_display_url"] = (
            f"/api/benchmarks/{bm_id}/works/{work['id']}/cover"
            if browser_needs_cover_proxy(cover)
            else cover
        )
    analysis = query_one("SELECT * FROM author_analyses WHERE bm_id=?"
                         " ORDER BY created_at DESC", (bm_id,))
    if analysis:
        analysis = {**analysis, "result": jloads(analysis["result_json"], {})}
        analysis.pop("result_json", None)
    runs = query("SELECT * FROM benchmark_sync_runs WHERE bm_id=? ORDER BY started_at DESC LIMIT 10",
                 (bm_id,))
    for run in runs:
        run["error"] = jloads(run.pop("error_json"), None)
    return ok({**_bm_full(bm), "works": works, "analysis": analysis,
               "sync_runs": runs})


@router.post("/{bm_id}/scan", status_code=202)
def scan(bm_id: str, count: int = Query(default=20, ge=1, le=100)):
    _owned_benchmark(bm_id)
    job = create_job(current_user_id(), "benchmark_refresh",
                     {"title": "刷新对标最新作品", "bm_id": bm_id, "count": count})
    return ok({"job_id": job["id"], "status": job["status"],
               "note": "刷新任务已在后台运行。"})


class BackfillIn(BaseModel):
    count: int = Field(default=20, ge=1, le=200)


@router.post("/{bm_id}/backfill", status_code=202)
def backfill(bm_id: str, body: BackfillIn):
    bm = _owned_benchmark(bm_id)
    if not bm.get("has_more", 1):
        return ok({"job_id": None, "status": "SUCCEEDED",
                   "note": "该账号已抓取到平台返回的全部公开作品。"})
    job = create_job(current_user_id(), "benchmark_backfill",
                     {"title": f"继续抓取 {body.count} 条历史作品",
                      "bm_id": bm_id, "count": body.count})
    return ok({"job_id": job["id"], "status": job["status"],
               "note": f"已在后台继续抓取最多 {body.count} 条历史作品。"})


class MonitorIn(BaseModel):
    enabled: bool = True
    frequency_hours: int = Field(default=24, ge=1, le=168)


@router.put("/{bm_id}/monitor")
def update_monitor(bm_id: str, body: MonitorIn):
    _owned_benchmark(bm_id)
    import datetime
    next_run = ((datetime.datetime.now() + datetime.timedelta(hours=body.frequency_hours))
                .isoformat(timespec="seconds") if body.enabled else None)
    execute("UPDATE benchmark_accounts SET monitor_enabled=?,monitor_frequency_hours=?,"
            "next_run_at=? WHERE id=?",
            (1 if body.enabled else 0, body.frequency_hours, next_run, bm_id))
    return ok({"enabled": body.enabled, "frequency_hours": body.frequency_hours,
               "next_run_at": next_run, "note": "监控频率已保存。"})


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
def analyze(bm_id: str, force: bool = False):
    _owned_benchmark(bm_id)
    result = bm_svc.author_analysis(bm_id, force=force)
    return ok(result)


@router.get("/{bm_id}/works/{video_id}")
def work_detail(bm_id: str, video_id: str):
    _owned_benchmark(bm_id)
    w = query_one("SELECT * FROM benchmark_works WHERE bm_id=? AND video_id=?",
                  (bm_id, video_id))
    if not w:
        raise not_found("作品", video_id)
    return ok(w)


@router.get("/{bm_id}/works/{work_id}/cover")
def benchmark_work_cover(bm_id: str, work_id: str):
    """Serve a browser-compatible cached JPEG for HEIC/HEIF benchmark covers."""
    _owned_benchmark(bm_id)
    work = query_one(
        "SELECT id,cover FROM benchmark_works WHERE id=? AND bm_id=?",
        (work_id, bm_id),
    )
    if not work:
        raise not_found("作品", work_id)
    cover = str(work.get("cover") or "").strip()
    from ..cover_cache import browser_needs_cover_proxy, cached_jpeg_cover
    if not browser_needs_cover_proxy(cover):
        raise validation_failed("该作品封面无需本地格式转换")
    path = cached_jpeg_cover(cover)
    return FileResponse(
        path, media_type="image/jpeg", filename=f"{work_id}.jpg",
        headers={"Cache-Control": "public, max-age=2592000, immutable"},
    )


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
