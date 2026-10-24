"""Job 系统：asyncio 后台 Worker，状态机驱动，不存在自动成功。

状态机：DRAFT → WAITING_CONFIRMATION → QUEUED → RUNNING → SUCCEEDED
       RUNNING → FAILED / CANCEL_REQUESTED → CANCELLED
       FAILED → RETRY_REQUESTED → QUEUED
"""
import asyncio
import datetime
import hashlib
import sqlite3
import shutil
import subprocess
import tempfile
from pathlib import Path

from .db import execute, jdumps, jloads, query, query_one, transaction
from .errors import new_trace_id
from .ids import new_id
from .providers import get_llm_provider, get_stt_provider

_worker_task: asyncio.Task | None = None


def now() -> str:
    return datetime.datetime.now().isoformat(timespec="seconds")


def create_job(user_id: str, task_type: str, input_data: dict,
               plan: dict | None = None, cost_est: float = 0.0,
               job_id: str | None = None,
               idempotency_key: str | None = None,
               max_attempts: int = 3,
               reuse_succeeded: bool = False) -> dict:
    idempotency_key = idempotency_key or _default_operation_key(task_type, input_data)
    if idempotency_key:
        statuses = "'QUEUED','RUNNING','CANCEL_REQUESTED','WAITING_CONFIRMATION'"
        if reuse_succeeded:
            statuses += ",'SUCCEEDED'"
        existing = query_one(
            f"SELECT id FROM jobs WHERE user_id=? AND task_type=? AND idempotency_key=? "
            f"AND status IN ({statuses}) ORDER BY created_at DESC LIMIT 1",
            (user_id, task_type, idempotency_key),
        )
        if existing:
            job = get_job(existing["id"])
            job["reused"] = True
            return job
    from .text_quality import repair_text_tree
    input_data = repair_text_tree(input_data)
    plan = repair_text_tree(plan or {})
    jid = job_id or new_id("J")
    try:
        execute(
            "INSERT INTO jobs(id,user_id,task_type,status,current_step,progress,input_json,"
            "plan_json,output_json,error_json,trace_id,cost_est,cost_actual,created_at,"
            "started_at,finished_at,attempt_count,max_attempts,idempotency_key,lease_until,heartbeat_at)"
            " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (jid, user_id, task_type, "QUEUED", "", 0, jdumps(input_data),
             jdumps(plan), jdumps({}), None, new_trace_id(),
             cost_est, 0.0, now(), None, None, 0, max(1, min(max_attempts, 10)),
             idempotency_key, None, None))
    except sqlite3.IntegrityError:
        # A concurrent request won the active-operation uniqueness race.
        existing = query_one(
            "SELECT id FROM jobs WHERE user_id=? AND task_type=? AND idempotency_key=? "
            "AND status IN ('QUEUED','RUNNING','CANCEL_REQUESTED','WAITING_CONFIRMATION') "
            "ORDER BY created_at DESC LIMIT 1",
            (user_id, task_type, idempotency_key),
        )
        if not existing:
            raise
        job = get_job(existing["id"])
        job["reused"] = True
        return job
    return get_job(jid)


def _default_operation_key(task_type: str, input_data: dict) -> str | None:
    """Return a stable business-operation key for operations that must not overlap."""
    fields_by_type = {
        "account_sync": ("account_id",),
        "radar_source_sync": ("source_id",),
        "benchmark_refresh": ("bm_id",),
        "benchmark_backfill": ("bm_id",),
        "benchmark_onboard": ("platform", "link"),
        "benchmark_patrol": ("bm_ids",),
        "extraction": ("source_content_id", "account_work_id", "signal_id",
                       "asset_id", "platform", "video_id", "url"),
        "comment_fetch": ("source_content_id", "after"),
    }
    fields = fields_by_type.get(task_type)
    if not fields:
        return None
    payload = {field: input_data.get(field) for field in fields if input_data.get(field) not in (None, "", [])}
    if not payload:
        return None
    digest = hashlib.sha256(jdumps(payload).encode("utf-8")).hexdigest()[:32]
    return f"{task_type}:{digest}"


def get_job(job_id: str) -> dict | None:
    row = query_one("SELECT * FROM jobs WHERE id=?", (job_id,))
    if not row:
        return None
    for k in ("input_json", "plan_json", "output_json"):
        row[k] = jloads(row[k], {})
    row["error"] = jloads(row.pop("error_json"), None)
    row["steps"] = query(
        "SELECT step,status,attempt,started_at,finished_at,error_json"
        " FROM job_steps WHERE job_id=? ORDER BY attempt,started_at,id", (job_id,))
    for step in row["steps"]:
        step["error"] = jloads(step.pop("error_json"), None)
    return row


def list_jobs(user_id: str, task_types: list[str] | None = None) -> list[dict]:
    rows = query("SELECT * FROM jobs WHERE user_id=? ORDER BY created_at DESC",
                 (user_id,))
    out = []
    for r in rows:
        j = get_job(r["id"])
        if task_types and j["task_type"] not in task_types:
            continue
        out.append(j)
    return out


TERMINAL_JOB_STATUSES = {"SUCCEEDED", "FAILED", "CANCELLED"}


def _job_logical_source_key(job: dict) -> str:
    """Group retries/cache hits for the same business source, not just request IDs."""
    payload = job.get("input_json") or {}
    for field in ("account_work_id", "signal_id", "asset_id"):
        if payload.get(field):
            return f"{job['task_type']}:{field}:{payload[field]}"
    if payload.get("platform") and payload.get("video_id"):
        return f"{job['task_type']}:work:{payload['platform']}:{payload['video_id']}"
    source_content_id = (payload.get("source_content_id")
                         or (job.get("output_json") or {}).get("source_content_id"))
    if source_content_id:
        return f"{job['task_type']}:source:{source_content_id}"
    title = "".join(str(payload.get("title") or "").lower().split())
    return f"{job['task_type']}:title:{title}" if title else f"job:{job['id']}"


def duplicate_job_summary(user_id: str, task_types: list[str] | None = None) -> dict:
    groups: dict[str, list[dict]] = {}
    for job in list_jobs(user_id, task_types):
        groups.setdefault(_job_logical_source_key(job), []).append(job)
    duplicates = [items for items in groups.values() if len(items) > 1]
    return {
        "duplicate_count": sum(len(items) - 1 for items in duplicates),
        "group_count": len(duplicates),
    }


def delete_job_record(user_id: str, job_id: str) -> bool:
    row = query_one("SELECT status FROM jobs WHERE id=? AND user_id=?", (job_id, user_id))
    if not row:
        return False
    if row["status"] not in TERMINAL_JOB_STATUSES:
        from .errors import AppError
        raise AppError("JOB_NOT_TERMINAL", "执行中的任务不能删除，请先取消并等待任务停止。",
                       stage="job_delete", retryable=False)
    with transaction():
        execute("DELETE FROM job_steps WHERE job_id=?", (job_id,))
        execute("DELETE FROM jobs WHERE id=? AND user_id=?", (job_id, user_id))
    return True


def cleanup_duplicate_jobs(user_id: str, task_types: list[str] | None = None) -> dict:
    """Delete redundant terminal run records while preserving the best current record."""
    groups: dict[str, list[dict]] = {}
    for job in list_jobs(user_id, task_types):
        groups.setdefault(_job_logical_source_key(job), []).append(job)

    active = {"RUNNING", "QUEUED", "WAITING_CONFIRMATION", "CANCEL_REQUESTED"}

    def rank(job: dict) -> tuple[int, str]:
        priority = 4 if job["status"] in active else 3 if job["status"] == "SUCCEEDED" else 2
        return priority, job.get("created_at") or ""

    deleted: list[str] = []
    with transaction():
        for items in groups.values():
            if len(items) < 2:
                continue
            keeper = max(items, key=rank)
            for job in items:
                if job["id"] == keeper["id"] or job["status"] not in TERMINAL_JOB_STATUSES:
                    continue
                execute("DELETE FROM job_steps WHERE job_id=?", (job["id"],))
                execute("DELETE FROM jobs WHERE id=? AND user_id=?", (job["id"], user_id))
                deleted.append(job["id"])
    return {"deleted_count": len(deleted), "deleted_ids": deleted}


def request_cancel(job_id: str) -> None:
    row = query_one("SELECT status FROM jobs WHERE id=?", (job_id,))
    if row and row["status"] in ("QUEUED", "RUNNING"):
        execute("UPDATE jobs SET status='CANCEL_REQUESTED' WHERE id=?", (job_id,))


def retry_job(job_id: str) -> None:
    row = query_one("SELECT status,attempt_count,max_attempts FROM jobs WHERE id=?", (job_id,))
    if not row or row["status"] not in ("FAILED", "CANCELLED", "WAITING_CONFIRMATION"):
        return
    if row["attempt_count"] >= row["max_attempts"]:
        from .errors import AppError
        raise AppError("JOB_RETRY_EXHAUSTED", "任务已达到最大重试次数。",
                       stage="job_retry", retryable=False)
    execute("UPDATE jobs SET status='QUEUED', progress=0, current_step='', error_json=NULL,"
            " finished_at=NULL, lease_until=NULL WHERE id=?", (job_id,))


def _set(job_id: str, **fields) -> None:
    if fields.get("status") in {"SUCCEEDED", "FAILED", "CANCELLED", "WAITING_CONFIRMATION"}:
        fields.setdefault("lease_until", None)
        fields.setdefault("heartbeat_at", now())
    cols = ", ".join(f"{k}=?" for k in fields)
    execute(f"UPDATE jobs SET {cols} WHERE id=?",
            tuple(fields.values()) + (job_id,))


def _lease_deadline(minutes: int = 10) -> str:
    return (datetime.datetime.now() + datetime.timedelta(minutes=minutes)).isoformat(
        timespec="seconds")


def _step_start(job_id: str, step: str, attempt: int) -> str:
    sid = new_id("JS")
    execute(
        "INSERT OR REPLACE INTO job_steps"
        "(id,job_id,step,status,attempt,started_at,finished_at,error_json)"
        " VALUES(?,?,?,?,?,?,?,?)",
        (sid, job_id, step, "RUNNING", attempt, now(), None, None),
    )
    return sid


def _step_finish(step_id: str, status: str, error: dict | None = None) -> None:
    execute("UPDATE job_steps SET status=?,finished_at=?,error_json=? WHERE id=?",
            (status, now(), jdumps(error) if error else None, step_id))


def recover_interrupted_jobs() -> dict:
    """Recover persisted state after a process crash or normal restart."""
    running = query("SELECT id FROM jobs WHERE status='RUNNING'")
    cancelled = query("SELECT id FROM jobs WHERE status='CANCEL_REQUESTED'")
    for row in running:
        execute(
            "UPDATE jobs SET status='QUEUED',current_step='recovered_after_restart',"
            "lease_until=NULL,heartbeat_at=NULL WHERE id=?", (row["id"],))
    for row in cancelled:
        execute("UPDATE jobs SET status='CANCELLED',finished_at=?,lease_until=NULL WHERE id=?",
                (now(), row["id"]))
    return {"requeued": len(running), "cancelled": len(cancelled)}


def _cancel_requested(job_id: str) -> bool:
    row = query_one("SELECT status FROM jobs WHERE id=?", (job_id,))
    return bool(row and row["status"] == "CANCEL_REQUESTED")


def record_usage(user_id: str, task: str, provider: str, endpoint: str,
                 model: str, cost: float, units: str = "1 次") -> None:
    execute("INSERT INTO usage_ledger VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (new_id("ul"), user_id, task, provider, endpoint,
             model, units, cost, 0, 0, now()))


async def _run_steps(job_id: str, steps: list[str], step_seconds: float = 0.6):
    job = query_one("SELECT attempt_count FROM jobs WHERE id=?", (job_id,)) or {}
    attempt = int(job.get("attempt_count") or 1)
    for i, step in enumerate(steps):
        if _cancel_requested(job_id):
            _set(job_id, status="CANCELLED", finished_at=now())
            return False
        step_id = _step_start(job_id, step, attempt)
        _set(job_id, current_step=step, progress=int((i + 0.5) / len(steps) * 100),
             heartbeat_at=now(), lease_until=_lease_deadline())
        try:
            await asyncio.sleep(step_seconds)
        except Exception as exc:
            _step_finish(step_id, "FAILED", {"message": str(exc)})
            raise
        _step_finish(step_id, "SUCCEEDED")
    return True


async def _run_step(job_id: str, step: str, progress: int, operation):
    """Run one real operation and persist its truthful lifecycle."""
    if _cancel_requested(job_id):
        _set(job_id, status="CANCELLED", finished_at=now())
        raise asyncio.CancelledError()
    job = query_one("SELECT attempt_count FROM jobs WHERE id=?", (job_id,)) or {}
    attempt = int(job.get("attempt_count") or 1)
    step_id = _step_start(job_id, step, attempt)
    _set(job_id, current_step=step, progress=max(0, min(int(progress), 99)),
         heartbeat_at=now(), lease_until=_lease_deadline())
    try:
        result = operation()
        if asyncio.iscoroutine(result):
            result = await result
    except Exception as exc:
        payload = exc.to_dict() if hasattr(exc, "to_dict") else {
            "code": "UNEXPECTED_ERROR", "message": str(exc), "stage": step,
        }
        _step_finish(step_id, "FAILED", payload)
        raise
    _step_finish(step_id, "SUCCEEDED")
    return result


def _transcribe_segmented_audio(job_id: str, audio_path: Path, stt, title: str,
                                work_dir: Path) -> dict:
    """Normalize and split audio below Qwen ASR's five-minute request limit."""
    from .errors import AppError
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise AppError(
            "ASR_SEGMENTER_MISSING",
            "本机缺少 FFmpeg，无法把长音频切分为 ASR 可接受的片段。",
            stage="prepare_asr_segments", provider="ffmpeg", retryable=False,
        )
    work_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=f"{job_id}-asr-", dir=str(work_dir)) as temp:
        pattern = str(Path(temp) / "segment-%03d.mp3")
        completed = subprocess.run(
            [ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-i", str(audio_path),
             "-map", "0:a:0", "-vn", "-ac", "1", "-ar", "16000", "-b:a", "48k",
             "-f", "segment", "-segment_time", "240", "-reset_timestamps", "1", pattern],
            capture_output=True, timeout=300, check=False,
        )
        segments = sorted(Path(temp).glob("segment-*.mp3"))
        if completed.returncode or not segments:
            raise AppError(
                "ASR_SEGMENTATION_FAILED",
                "音频切分失败，原始媒体仍保留，可查看错误后重试。",
                stage="prepare_asr_segments", provider="ffmpeg", retryable=True,
                details={"return_code": completed.returncode},
            )
        texts: list[str] = []
        total_cost = 0.0
        model = ""
        request_ids: list[str] = []
        for index, segment in enumerate(segments, start=1):
            _set(
                job_id, current_step="transcribe_audio_segment",
                progress=64 + int(index / len(segments) * 14),
                heartbeat_at=now(), lease_until=_lease_deadline(),
                output_json=jdumps({"asr_progress": {"segment": index,
                                                       "total": len(segments)}}),
            )
            result = stt.transcribe(
                segment.read_bytes(),
                context=f"内容标题：{title}；当前为第 {index}/{len(segments)} 段。",
                mime_type="audio/mpeg",
            )
            text = str(result.get("text") or "").strip()
            if not text:
                raise AppError(
                    "STT_OUTPUT_EMPTY", f"第 {index}/{len(segments)} 段没有返回文本。",
                    stage="transcribe_segment", provider=getattr(stt, "name", "stt"),
                    retryable=True, details={"segment": index, "total": len(segments)},
                )
            texts.append(text)
            total_cost += float(result.get("cost") or 0)
            model = str(result.get("model") or model)
            if result.get("provider_request_id"):
                request_ids.append(str(result["provider_request_id"]))
        return {
            "text": "\n\n".join(texts), "model": model,
            "cost": total_cost, "demo": False, "segment_count": len(segments),
            "provider_request_ids": request_ids,
        }


async def _execute(job: dict) -> None:
    jid, ttype = job["id"], job["task_type"]
    attempt = int(job.get("attempt_count") or 0) + 1
    _set(jid, status="RUNNING", started_at=now(), finished_at=None,
         attempt_count=attempt, lease_until=_lease_deadline(), heartbeat_at=now())
    input_data = job["input_json"]
    user_id = job["user_id"]
    try:
        if ttype == "extraction":
            await _run_extraction(jid, user_id, input_data)
        elif ttype == "analysis":
            await _run_analysis(jid, user_id, input_data)
        elif ttype == "account_sync":
            await _run_sync(jid, user_id, input_data)
        elif ttype == "benchmark_onboard":
            from .benchmarks import add_benchmark
            ok = await _run_steps(jid, ["resolve_account", "create_monitor"], 0.05)
            if not ok:
                return
            _set(jid, current_step="fetch_and_persist_works", progress=45,
                 heartbeat_at=now(), lease_until=_lease_deadline())
            result = await asyncio.to_thread(
                add_benchmark, user_id, input_data["link"], input_data.get("platform"),
                str(input_data.get("scan_count") or 30), input_data.get("note", ""),
                scan_count=int(input_data.get("scan_count") or 30),
            )
            if result.get("sync_status") == "error":
                raise RuntimeError((result.get("error") or {}).get("message") or
                                   "对标账号首次同步失败")
            _set(jid, status="SUCCEEDED", progress=100, current_step="completed",
                 finished_at=now(), output_json=jdumps({**result,
                     "note": f"首次同步完成：新增 {result.get('new', 0)} 条作品"}))
            execute("INSERT INTO inbox VALUES(?,?,?,?,?,?,?,?)",
                    (new_id("in"), user_id, "job_completed", "对标账号首次同步完成",
                     f"已沉淀 {result.get('new', 0)} 条作品，点击查看监控墙。",
                     jdumps({"job_id": jid, "bm_id": result.get("id"), "page": "radar"}),
                     0, now()))
        elif ttype in {"benchmark_refresh", "benchmark_backfill"}:
            from .benchmarks import scan_benchmark
            bm_id = input_data["bm_id"]
            kind = "backfill" if ttype == "benchmark_backfill" else "refresh"
            ok = await _run_steps(jid, ["load_monitor", "fetch_pages"], 0.05)
            if not ok:
                return
            result = await asyncio.to_thread(
                scan_benchmark, bm_id, emit_signals=True, kind=kind,
                requested_count=int(input_data.get("count") or 20),
            )
            note = (f"继续抓取完成：新增 {result['new']} 条历史作品"
                    if kind == "backfill" else
                    f"最新作品刷新完成：新增 {result['new']} 条")
            _set(jid, status="SUCCEEDED", progress=100, current_step="completed",
                 finished_at=now(), output_json=jdumps({**result, "bm_id": bm_id,
                                                        "kind": kind, "note": note}))
            execute("INSERT INTO inbox VALUES(?,?,?,?,?,?,?,?)",
                    (new_id("in"), user_id, "job_completed", note,
                     "作品缓存、指标快照和同步游标均已保存。",
                     jdumps({"job_id": jid, "bm_id": bm_id, "page": "radar"}), 0, now()))
        elif ttype == "radar_source_sync":
            from .radar_sources import sync_source
            ok = await _run_steps(jid, ["fetch_source", "normalize_and_deduplicate"], 0.05)
            if not ok:
                return
            result = await asyncio.to_thread(sync_source, input_data["source_id"], user_id)
            _set(jid, status="SUCCEEDED", progress=100, current_step="completed",
                 finished_at=now(), output_json=jdumps({**result,
                     "note": f"数据源同步完成：新增 {result['created']} 条雷达信号"}))
            execute("INSERT INTO inbox VALUES(?,?,?,?,?,?,?,?)",
                    (new_id("in"), user_id, "job_completed", "雷达数据源同步完成",
                     f"新增 {result['created']} 条真实信号，重复 {result['deduplicated']} 条。",
                     jdumps({"job_id": jid, "source_id": result["source_id"], "page": "radar"}),
                     0, now()))
        elif ttype == "benchmark_patrol":
            from .benchmarks import scan_benchmark
            bm_ids = input_data.get("bm_ids") or []
            total = max(len(bm_ids), 1)
            done_cnt, failed = 0, 0
            for i, bid in enumerate(bm_ids):
                if _cancel_requested(jid):
                    _set(jid, status="CANCELLED", finished_at=now())
                    return
                _set(jid, current_step=f"scan_{bid}", progress=int((i + 0.5) / total * 100))
                try:
                    await asyncio.to_thread(
                        scan_benchmark, bid, emit_signals=True, kind="refresh",
                        requested_count=int(input_data.get("count") or 20),
                    )
                    done_cnt += 1
                except Exception:
                    failed += 1
                await asyncio.sleep(0.2)
            _set(jid, status="SUCCEEDED" if done_cnt else "FAILED", progress=100,
                 finished_at=now(),
                 output_json=jdumps({"scanned": done_cnt, "failed": failed}))
        elif ttype == "comment_fetch":
            ok = await _run_steps(jid, ["fetch_pages", "validate", "persist"], 0.5)
            if ok:
                record_usage(user_id, input_data.get("title", "评论获取"),
                             "demo_tikhub", "comments", "", 0.08)
                _set(jid, status="SUCCEEDED", progress=100, cost_actual=0.08,
                     finished_at=now(),
                     output_json=jdumps({"comments": 200, "pages": 4}))
        elif ttype == "comment_cluster":
            ok = await _run_steps(jid, ["load_comments", "embed", "cluster", "summarize"], 0.8)
            if ok:
                _set(jid, status="SUCCEEDED", progress=100, finished_at=now(),
                     output_json=jdumps({"clusters": 3,
                                         "top": "“如何选择 offer”类提问聚类 11 次"}))
        elif ttype == "snapshot":
            ok = await _run_steps(jid, ["fetch_metrics", "persist_snapshot"], 0.4)
            if ok:
                _set(jid, status="SUCCEEDED", progress=100, finished_at=now())
        else:
            ok = await _run_steps(jid, ["run"], 0.5)
            if ok:
                _set(jid, status="SUCCEEDED", progress=100, finished_at=now())
    except asyncio.CancelledError:
        if _cancel_requested(jid):
            _set(jid, status="CANCELLED", finished_at=now())
        else:
            raise
    except Exception as exc:  # 任务失败 = 显式失败，不伪装
        from .errors import AppError
        if isinstance(exc, AppError):
            err = exc.to_dict()
        else:
            err = {"code": "UNEXPECTED_ERROR", "message": str(exc),
                   "stage": "job_run", "provider": None, "retryable": True,
                   "trace_id": new_trace_id(), "details": {}}
        provider_trace = err.get("trace_id")
        if provider_trace and provider_trace != job.get("trace_id"):
            err.setdefault("details", {})["provider_trace_id"] = provider_trace
        err["trace_id"] = job.get("trace_id") or provider_trace or new_trace_id()
        running_step = query_one(
            "SELECT id FROM job_steps WHERE job_id=? AND status='RUNNING' "
            "ORDER BY started_at DESC LIMIT 1", (jid,),
        )
        if running_step:
            _step_finish(running_step["id"], "FAILED", err)
        execute(
            "INSERT INTO error_logs(trace_id,code,stage,provider,message,ts) VALUES(?,?,?,?,?,?)",
            (err["trace_id"], err.get("code", "UNEXPECTED_ERROR"),
             err.get("stage", "job_run"), err.get("provider") or "",
             err.get("message", str(exc)), now()),
        )
        _set(jid, status="FAILED", error_json=jdumps(err), finished_at=now(),
             lease_until=None, heartbeat_at=now())


async def _run_extraction(jid: str, user_id: str, input_data: dict) -> None:
    from . import config
    from .datasource import get_datasource
    from .repositories import evidence, owned_row, persist_artifact_version
    from .text_quality import repair_mojibake
    title = repair_mojibake(input_data.get("title", "未命名来源"))
    want_asr = bool(input_data.get("authorizations", {}).get("asr_when_no_caption"))
    caption_hit = input_data.get("caption_available", True)
    ds = get_datasource()
    source = {"title": title}
    uploaded_asset = None
    downloaded_media = None
    derived_audio = None
    provider_cost = 0.0
    def validate_source_identity():
        if input_data.get("asset_id") or input_data.get("source_content_id"):
            return True
        if not input_data.get("platform") or not input_data.get("video_id"):
            from .errors import AppError
            raise AppError(
                "SOURCE_IDENTITY_INCOMPLETE",
                "提取任务缺少作品平台或作品 ID，请从原作品重新生成提取计划。",
                stage="resolve_source", retryable=False,
                details={"signal_id": input_data.get("signal_id"),
                         "account_work_id": input_data.get("account_work_id")},
            )
        return True
    if not config.DEMO_MODE:
        await _run_step(jid, "resolve_source", 5, validate_source_identity)
    if input_data.get("source_content_id"):
        cached_source = owned_row(
            "source_contents", input_data["source_content_id"], user_id,
            label="来源内容",
        )
        cached_assets = jloads(cached_source.get("assets_json"), {})
        cached_text = next((
            str((cached_assets.get(key) or {}).get("text") or "")
            for key in ("clean", "caption", "asr")
            if (cached_assets.get(key) or {}).get("exists")
            and (cached_assets.get(key) or {}).get("text")
        ), "")
        if cached_text:
            latest_asset = query_one(
                "SELECT id FROM media_assets WHERE user_id=? AND source_content_id=? "
                "AND kind='transcript' ORDER BY created_at DESC LIMIT 1",
                (user_id, cached_source["id"]),
            )
            evidence(
                user_id, "source_content", cached_source["id"],
                "transcript_cache_hit", refs=[latest_asset["id"]] if latest_asset else [],
                payload={"job_id": jid, "cost": 0},
            )
            await _run_step(jid, "check_cache", 80, lambda: True)
            _set(
                jid, status="SUCCEEDED", progress=100,
                current_step="cache_hit", cost_actual=0, finished_at=now(),
                output_json=jdumps({
                    "source_content_id": cached_source["id"],
                    "transcript_asset_id": latest_asset["id"] if latest_asset else None,
                    "cache_hit": True,
                }),
            )
            if input_data.get("account_work_id"):
                from .asset_center import link_account_work, sync_asset_index
                link_account_work(user_id, input_data["account_work_id"], cached_source["id"],
                                  "extraction_cache_hit", 1.0)
                sync_asset_index(user_id)
            return
    if input_data.get("asset_id"):
        uploaded_asset = owned_row("media_assets", input_data["asset_id"], user_id,
                                   label="素材")

    await _run_step(jid, "check_cache", 12, lambda: True)
    if caption_hit and not uploaded_asset:
        cap = await _run_step(
            jid, "fetch_caption", 38,
            lambda: asyncio.to_thread(
                ds.video_text, input_data.get("video_id", ""),
                input_data.get("platform", "douyin"), title,
            ),
        )
        record_usage(user_id, f"原生字幕 · {title}", ds.name, "caption", "", cap["cost"])
        provider_cost += float(cap.get("cost") or 0)
        assets = {
            "metadata": {"exists": True, "file": "metadata.json"},
            "media": {"exists": False, "note": "未下载（命中字幕）"},
            "caption": {"exists": True, "immutable": True, "text": cap["text"],
                        "trace": f"{ds.name} 字幕接口 · ¥0.10 · 缓存键已记录"},
            "asr": {"exists": False, "note": "未执行（命中原生字幕，未触发 ASR）"},
            "clean": {"exists": False},
            "comments": {"exists": False},
        }
        cost = provider_cost
    else:
        if not want_asr:
            # 无字幕且未授权 ASR：显式停在待决策，不是失败也不是成功
            _set(jid, status="WAITING_CONFIRMATION",
                 current_step="awaiting_asr_authorization",
                 output_json=jdumps({"reason": "平台无原生字幕，等待你明确授权 ASR"}))
            return
        if uploaded_asset:
            def load_uploaded_audio():
                path = Path(uploaded_asset["path"])
                if not path.is_file():
                    from .errors import AppError
                    raise AppError("ASSET_FILE_MISSING", "素材文件不存在或已被移动。",
                                   stage="load_uploaded_audio", retryable=False)
                return path
            audio_path = await _run_step(jid, "load_uploaded_audio", 32,
                                         load_uploaded_audio)
            audio_mime = uploaded_asset.get("mime_type") or "audio/wav"
        else:
            from .errors import AppError
            media = await _run_step(
                jid, "download_media", 34,
                lambda: asyncio.to_thread(
                    ds.video_media, input_data.get("video_id", ""),
                    input_data.get("platform", "douyin"),
                ),
            )
            record_usage(user_id, f"公开媒体解析 · {title}", ds.name, "media", "",
                         media.get("cost", 0.0))
            provider_cost += float(media.get("cost") or 0)
            folder = config.UPLOAD_DIR / user_id
            folder.mkdir(parents=True, exist_ok=True)
            media_id = new_id("MA")
            media_path = folder / f"{media_id}.mp4"
            media_path.write_bytes(media["bytes"])
            downloaded_media = {
                "id": media_id, "path": str(media_path),
                "mime_type": media.get("mime_type") or "video/mp4",
                "size_bytes": len(media["bytes"]),
                "sha256": hashlib.sha256(media["bytes"]).hexdigest(),
                "metadata": {"source_url": media.get("url", ""), "provider": ds.name},
            }
            ffmpeg = shutil.which("ffmpeg")
            if not ffmpeg:
                raise AppError(
                    "AUDIO_EXTRACTOR_MISSING",
                    "已获得媒体，但本机缺少 FFmpeg，无法提取音轨。请安装 FFmpeg 后重试。",
                    stage="extract_audio", provider="ffmpeg", retryable=False,
                )
            audio_id = new_id("MA")
            audio_path = folder / f"{audio_id}.mp3"
            completed = await _run_step(
                jid, "extract_audio", 58,
                lambda: asyncio.to_thread(
                    subprocess.run,
                    [ffmpeg, "-y", "-i", str(media_path), "-vn", "-ac", "1",
                     "-ar", "16000", "-b:a", "48k", str(audio_path)],
                    capture_output=True, timeout=180, check=False,
                ),
            )
            if completed.returncode != 0 or not audio_path.is_file():
                raise AppError(
                    "AUDIO_EXTRACTION_FAILED", "媒体音轨提取失败，原始媒体已保留。",
                    stage="extract_audio", provider="ffmpeg", retryable=False,
                    details={"return_code": completed.returncode},
                )
            audio_bytes = audio_path.read_bytes()
            derived_audio = {
                "id": audio_id, "path": str(audio_path), "mime_type": "audio/mpeg",
                "size_bytes": len(audio_bytes),
                "sha256": hashlib.sha256(audio_bytes).hexdigest(),
                "metadata": {"derived_from": media_id, "sample_rate": 16000,
                             "channels": 1, "bitrate": "48k"},
            }
            audio_mime = "audio/mpeg"
        stt = get_stt_provider()
        if config.DEMO_MODE:
            tr = await _run_step(
                jid, "transcribe_audio", 74,
                lambda: asyncio.to_thread(
                    stt.transcribe, audio_path.read_bytes(), context=f"内容标题：{title}",
                    mime_type=audio_mime,
                ),
            )
            tr.setdefault("segment_count", 1)
        else:
            tr = await _run_step(
                jid, "transcribe_audio", 64,
                lambda: asyncio.to_thread(
                    _transcribe_segmented_audio, jid, audio_path, stt, title,
                    config.UPLOAD_DIR / user_id,
                ),
            )
        record_usage(user_id, f"ASR 转写 · {title}", getattr(stt, "name", "stt"), "asr",
                     tr.get("model", ""), tr["cost"])
        provider_cost += float(tr.get("cost") or 0)
        assets = {
            "metadata": {"exists": True, "file": "metadata.json"},
            "media": {"exists": True,
                      "asset_id": uploaded_asset["id"] if uploaded_asset else downloaded_media["id"],
                      "audio_asset_id": None if uploaded_asset else derived_audio["id"],
                      "size_bytes": uploaded_asset["size_bytes"] if uploaded_asset else downloaded_media["size_bytes"],
                      "sha256": uploaded_asset["sha256"] if uploaded_asset else downloaded_media["sha256"],
                      "source": "user_upload" if uploaded_asset else "provider_public_media"},
            "caption": {"exists": False, "note": "平台无原生字幕"},
            "asr": {"exists": True, "immutable": True, "text": tr["text"],
                    "segments": int(tr.get("segment_count") or 1),
                    "trace": (f"{getattr(stt, 'name', 'stt')} · {tr.get('model', '')} · "
                              f"{int(tr.get('segment_count') or 1)} 段")},
            "clean": {"exists": False},
            "comments": {"exists": False},
        }
        cost = provider_cost
    if input_data.get("account_work_id"):
        assets["account_work_id"] = input_data["account_work_id"]
    def validate_transcript():
        transcript_text = ((assets.get("caption") or {}).get("text")
                           or (assets.get("asr") or {}).get("text") or "").strip()
        if not transcript_text:
            from .errors import AppError
            raise AppError("TRANSCRIPT_EMPTY", "转写结果为空，未写入完整文案资产。",
                           stage="validate_transcript", retryable=True)
        return transcript_text
    await _run_step(jid, "validate_transcript", 82, validate_transcript)
    job_row = query_one("SELECT attempt_count FROM jobs WHERE id=?", (jid,)) or {}
    persist_step_id = _step_start(
        jid, "persist_assets", int(job_row.get("attempt_count") or 1)
    )
    _set(jid, current_step="persist_assets", progress=88,
         heartbeat_at=now(), lease_until=_lease_deadline())
    # 写入来源内容资产
    sc_id = input_data.get("source_content_id")
    if not sc_id and input_data.get("signal_id"):
        prior = query_one(
            "SELECT id FROM source_contents WHERE user_id=? AND signal_id=? "
            "ORDER BY created_at DESC LIMIT 1",
            (user_id, input_data["signal_id"]),
        )
        sc_id = prior["id"] if prior else None
    sc_id = sc_id or f"SC-{new_trace_id()[-6:]}"
    existing = query_one("SELECT id FROM source_contents WHERE id=? AND user_id=?",
                         (sc_id, user_id))
    if existing:
        execute("UPDATE source_contents SET title=?,status=?,assets_json=? WHERE id=?",
                (title, "transcribed", jdumps(assets), sc_id))
    else:
        execute("INSERT INTO source_contents VALUES(?,?,?,?,?,?,?,?,?,?)",
                (sc_id, user_id, input_data.get("signal_id", ""), title,
                 input_data.get("platform", ""), input_data.get("author", ""),
                 input_data.get("url", ""), "transcribed", jdumps(assets), now()))
    for media_asset, kind in ((downloaded_media, "original_media"),
                              (derived_audio, "audio_derivative")):
        if not media_asset:
            continue
        execute(
            "INSERT INTO media_assets(id,user_id,source_content_id,kind,path,mime_type,size_bytes,sha256,"
            "immutable,metadata_json,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (media_asset["id"], user_id, sc_id, kind, media_asset["path"],
             media_asset["mime_type"], media_asset["size_bytes"], media_asset["sha256"],
             1, jdumps(media_asset["metadata"]), now()),
        )
    if input_data.get("signal_id"):
        execute(
            "UPDATE signals SET content_id=? WHERE id=? AND user_id=?",
            (sc_id, input_data["signal_id"], user_id),
        )
    if input_data.get("account_work_id"):
        from .asset_center import link_account_work
        link_account_work(user_id, input_data["account_work_id"], sc_id,
                          "extraction_job", 1.0)
    transcript_asset_id = None
    transcript = (assets.get("caption") or {}).get("text") or (assets.get("asr") or {}).get("text")
    if transcript:
        from . import config
        digest = hashlib.sha256(transcript.encode("utf-8")).hexdigest()
        transcript_asset_id = new_id("MA")
        folder = config.UPLOAD_DIR / user_id
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / f"{transcript_asset_id}.txt"
        path.write_text(transcript, encoding="utf-8")
        execute(
            "INSERT INTO media_assets(id,user_id,source_content_id,kind,path,mime_type,size_bytes,sha256,"
            "immutable,metadata_json,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (transcript_asset_id, user_id, sc_id, "transcript", str(path), "text/plain",
             len(transcript.encode("utf-8")), digest, 1,
             jdumps({"provider": assets.get("asr", {}).get("trace") or
                                      assets.get("caption", {}).get("trace")}), now()),
        )
        transcript_version = persist_artifact_version(
            user_id, sc_id, "transcript", {"text": transcript},
            producer=(assets.get("asr", {}).get("trace") or
                      assets.get("caption", {}).get("trace") or ds.name),
            source_refs=[x for x in [input_data.get("signal_id"), input_data.get("asset_id"),
                                     (downloaded_media or {}).get("id"),
                                     (derived_audio or {}).get("id")]
                         if x],
        )
    else:
        transcript_version = None
    evidence(user_id, "source_content", sc_id, "transcript_persisted",
             refs=[x for x in [input_data.get("asset_id"),
                                (downloaded_media or {}).get("id"),
                                (derived_audio or {}).get("id"), transcript_asset_id,
                                (transcript_version or {}).get("id")] if x],
             payload={"job_id": jid,
                      "method": "caption" if assets.get("caption", {}).get("exists") else "asr",
                      "cost": cost, "artifact_version":
                      (transcript_version or {}).get("version_no")})
    _step_finish(persist_step_id, "SUCCEEDED")
    _set(jid, status="SUCCEEDED", progress=100, cost_actual=cost,
         current_step="complete", finished_at=now(), output_json=jdumps({"source_content_id": sc_id,
                                                "transcript_asset_id": transcript_asset_id}))
    from .asset_center import sync_asset_index
    sync_asset_index(user_id)


async def _run_analysis(jid: str, user_id: str, input_data: dict) -> None:
    from .context_resolver import resolve_context
    from .repositories import evidence, owned_row, persist_artifact_version
    source = owned_row("source_contents", input_data.get("source_content_id", ""),
                       user_id, label="来源内容")
    assets = jloads(source.get("assets_json"), {})
    transcript = ""
    for key in ("clean", "caption", "asr"):
        if (assets.get(key) or {}).get("exists"):
            transcript = str(assets[key].get("text") or "")
            if transcript:
                break
    def load_transcript():
        if not transcript:
            from .errors import AppError
            raise AppError("TRANSCRIPT_MISSING", "来源内容尚无可用字幕或转写。",
                           stage="load_transcript", retryable=False)
        return transcript
    await _run_step(jid, "load_transcript", 12, load_transcript)
    llm = get_llm_provider()
    context = await _run_step(
        jid, "context_resolve", 28,
        lambda: resolve_context(user_id, "analysis",
                                source_content_id=source["id"],
                                source_chain=input_data.get("source_chain", [])),
    )
    result = await _run_step(
        jid, "llm_analyze", 58,
        lambda: asyncio.to_thread(
            llm.analyze, source["title"], transcript, context["content"]
        ),
    )
    def validate_analysis():
        if not isinstance(result, dict) or not result.get("topic"):
            from .errors import AppError
            raise AppError("ANALYSIS_SCHEMA_INVALID", "模型返回的拆解缺少核心选题，未写入结果。",
                           stage="schema_validate", retryable=True,
                           provider=getattr(llm, "name", ""))
        return True
    await _run_step(jid, "schema_validate", 76, validate_analysis)
    result["source_chain"] = input_data.get("source_chain", [])
    result["context_run_id"] = context["run_id"]
    job_row = query_one("SELECT attempt_count FROM jobs WHERE id=?", (jid,)) or {}
    persist_step_id = _step_start(jid, "persist", int(job_row.get("attempt_count") or 1))
    _set(jid, current_step="persist", progress=84,
         heartbeat_at=now(), lease_until=_lease_deadline())
    aid = f"A-{new_trace_id()[-4:]}"
    actual_cost = 0.35 if getattr(llm, "name", "").startswith("demo") else 0.0
    execute("INSERT INTO analyses VALUES(?,?,?,?,?,?,?,?)",
            (aid, user_id, input_data.get("source_content_id", ""), "done",
             jdumps(result), getattr(llm, "name", "demo"), actual_cost, now()))
    artifact = persist_artifact_version(
        user_id, source["id"], "analysis", result,
        producer=f"{getattr(llm, 'name', 'demo')}:{result.get('model', '')}",
        source_refs=[source["id"], context["run_id"], aid],
    )
    usage = getattr(llm, "last_usage", {}) or {}
    units = (f"input={usage.get('prompt_tokens', 0)} tokens · "
             f"output={usage.get('completion_tokens', 0)} tokens") if usage else "1 次"
    record_usage(user_id, f"内容拆解 · {input_data.get('title', '')}",
                 getattr(llm, "name", "demo"), "analysis",
                 result.get("model", ""), actual_cost, units)
    evidence(user_id, "analysis", aid, "analysis_completed",
             refs=[source["id"], context["run_id"], artifact["id"]],
             payload={"job_id": jid, "model": result.get("model", ""),
                      "artifact_version": artifact["version_no"]})
    _step_finish(persist_step_id, "SUCCEEDED")
    _set(jid, status="SUCCEEDED", progress=100, cost_actual=actual_cost,
         current_step="complete", finished_at=now(),
         output_json=jdumps({"analysis_id": aid}))
    from .asset_center import sync_asset_index
    sync_asset_index(user_id)


async def _run_sync(jid: str, user_id: str, input_data: dict) -> None:
    from .datasource import get_datasource
    account = query_one("SELECT * FROM accounts WHERE id=?",
                        (input_data.get("account_id"),))
    if not account:
        raise RuntimeError(f"账号不存在：{input_data.get('account_id')}")
    ds = get_datasource()
    extra = account.get("extra_json")
    import json as _json
    meta = _json.loads(extra) if extra else {}
    uid = meta.get("uid") or account["id"]
    try:
        ov = await _run_step(
            jid, "fetch_profile", 15,
            lambda: asyncio.to_thread(ds.account_overview, uid, account["platform"]),
        )
        videos = await _run_step(
            jid, "fetch_latest_posts", 45,
            lambda: asyncio.to_thread(
                ds.account_videos, uid, account["platform"],
                limit=max(1, min(int(input_data.get("count") or 60), 200)),
            ),
        )
    except Exception as exc:
        code = getattr(exc, "code", "ACCOUNT_SYNC_FAILED")
        trace_id = getattr(exc, "trace_id", "")
        meta.update({"error_code": code, "trace_id": trace_id})
        execute("UPDATE accounts SET status='error',note=?,extra_json=? WHERE id=?",
                (getattr(exc, "message", str(exc)), jdumps(meta), account["id"]))
        raise
    meta.update({"name": ov.get("name") or meta.get("name", ""),
                 "avatar": ov.get("avatar") or meta.get("avatar", ""),
                 "bio": ov.get("bio") or meta.get("bio", ""),
                 "likes": int(ov.get("likes") or 0),
                 "video_count": int(ov.get("video_count") or 0),
                 "metric_availability": ov.get("availability") or {},
                 "latest_work_at": int(videos.get("newest_create_time") or 0),
                 "sync_pages": int(videos.get("pages") or 0)})
    from .account_works import persist_account_works
    work_stats = await _run_step(
        jid, "persist_works", 75,
        lambda: asyncio.to_thread(
            persist_account_works, account["id"], videos.get("works") or [], now()
        ),
    )
    execute("UPDATE accounts SET handle=?,followers=?,last_sync_at=?,status='ok',"
            "note=?,bound_provider=?,extra_json=? WHERE id=?",
            ("@" + (meta.get("name") or account["handle"]).lstrip("@"),
             int(ov.get("fans") or 0), now(), "最近一次同步成功", ds.name,
             jdumps(meta), account["id"]))
    def persist_snapshot():
        execute("DELETE FROM account_snapshots WHERE account_id=? AND date=?",
                (account["id"], datetime.date.today().isoformat()))
        execute("INSERT INTO account_snapshots VALUES(?,?,?,?,?,?)",
                (f"ss-{new_trace_id()[-6:]}", account["id"],
                 datetime.date.today().isoformat(), int(ov.get("fans") or 0),
                 int(ov.get("video_count") or 0),
                 jdumps({"source": ds.name, "likes": int(ov.get("likes") or 0),
                         "availability": ov.get("availability") or {}})))
    await _run_step(jid, "persist_snapshot", 92, persist_snapshot)
    _set(jid, status="SUCCEEDED", progress=100, finished_at=now(),
         output_json=jdumps({"followers": ov["fans"], "source": ds.name,
                             "works": len(videos.get("works") or []),
                             "pages": videos.get("pages", 0),
                             "newest_create_time": videos.get("newest_create_time", 0),
                             **work_stats}))


async def _worker_loop() -> None:
    while True:
        rows = query("SELECT id FROM jobs WHERE status='QUEUED' ORDER BY created_at LIMIT 3")
        for r in rows:
            job = get_job(r["id"])
            if job:
                await _execute(job)
        await asyncio.sleep(0.5)


def start_worker() -> None:
    global _worker_task
    if _worker_task is None:
        _worker_task = asyncio.create_task(_worker_loop())


def stop_worker() -> None:
    global _worker_task
    if _worker_task:
        _worker_task.cancel()
        _worker_task = None
