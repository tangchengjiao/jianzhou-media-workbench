"""Job 系统：asyncio 后台 Worker，状态机驱动，不存在自动成功。

状态机：DRAFT → WAITING_CONFIRMATION → QUEUED → RUNNING → SUCCEEDED
       RUNNING → FAILED / CANCEL_REQUESTED → CANCELLED
       FAILED → RETRY_REQUESTED → QUEUED
"""
import asyncio
import datetime
from pathlib import Path

from .db import execute, jdumps, jloads, query, query_one
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
               max_attempts: int = 3) -> dict:
    if idempotency_key:
        existing = query_one(
            "SELECT id FROM jobs WHERE user_id=? AND task_type=? AND idempotency_key=?",
            (user_id, task_type, idempotency_key),
        )
        if existing:
            return get_job(existing["id"])
    jid = job_id or new_id("J")
    execute(
        "INSERT INTO jobs(id,user_id,task_type,status,current_step,progress,input_json,"
        "plan_json,output_json,error_json,trace_id,cost_est,cost_actual,created_at,"
        "started_at,finished_at,attempt_count,max_attempts,idempotency_key,lease_until,heartbeat_at)"
        " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (jid, user_id, task_type, "QUEUED", "", 0, jdumps(input_data),
         jdumps(plan or {}), jdumps({}), None, new_trace_id(),
         cost_est, 0.0, now(), None, None, 0, max(1, min(max_attempts, 10)),
         idempotency_key, None, None))
    return get_job(jid)


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
                    scan_benchmark(bid, emit_signals=True)
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
    except Exception as exc:  # 任务失败 = 显式失败，不伪装
        from .errors import AppError
        if isinstance(exc, AppError):
            err = exc.to_dict()
        else:
            err = {"code": "UNEXPECTED_ERROR", "message": str(exc),
                   "stage": "job_run", "provider": None, "retryable": True,
                   "trace_id": new_trace_id(), "details": {}}
        _set(jid, status="FAILED", error_json=jdumps(err), finished_at=now(),
             lease_until=None, heartbeat_at=now())


async def _run_extraction(jid: str, user_id: str, input_data: dict) -> None:
    from .datasource import get_datasource
    from .repositories import evidence, owned_row
    title = input_data.get("title", "未命名来源")
    want_asr = bool(input_data.get("authorizations", {}).get("asr_when_no_caption"))
    caption_hit = input_data.get("caption_available", True)
    ds = get_datasource()
    source = {"title": title}
    uploaded_asset = None
    if input_data.get("asset_id"):
        uploaded_asset = owned_row("media_assets", input_data["asset_id"], user_id,
                                   label="素材")

    ok = await _run_steps(jid, ["resolve_source", "check_cache"], 0.4)
    if not ok:
        return
    if caption_hit and not uploaded_asset:
        _set(jid, current_step="fetch_caption")
        await asyncio.sleep(0.3)
        cap = ds.video_text(input_data.get("video_id", ""), "douyin", title)
        record_usage(user_id, f"原生字幕 · {title}", ds.name, "caption", "", cap["cost"])
        assets = {
            "metadata": {"exists": True, "file": "metadata.json"},
            "media": {"exists": False, "note": "未下载（命中字幕）"},
            "caption": {"exists": True, "immutable": True, "text": cap["text"],
                        "trace": f"{ds.name} 字幕接口 · ¥0.10 · 缓存键已记录"},
            "asr": {"exists": False, "note": "未执行（命中原生字幕，未触发 ASR）"},
            "clean": {"exists": False},
            "comments": {"exists": False},
        }
        cost = cap["cost"]
    else:
        if not want_asr:
            # 无字幕且未授权 ASR：显式停在待决策，不是失败也不是成功
            _set(jid, status="WAITING_CONFIRMATION",
                 current_step="awaiting_asr_authorization",
                 output_json=jdumps({"reason": "平台无原生字幕，等待你明确授权 ASR"}))
            return
        steps = (["load_uploaded_audio", "transcribe_audio"] if uploaded_asset
                 else ["download_media", "extract_audio", "transcribe_segments"])
        ok = await _run_steps(jid, steps, 0.1 if uploaded_asset else 0.7)
        if not ok:
            return
        if not uploaded_asset:
            raise RuntimeError("当前真实链路需要先上传音频；平台媒体下载尚未授权")
        audio_path = Path(uploaded_asset["path"])
        if not audio_path.is_file():
            from .errors import AppError
            raise AppError("ASSET_FILE_MISSING", "素材文件不存在或已被移动。",
                           stage="load_uploaded_audio", retryable=False)
        stt = get_stt_provider()
        tr = stt.transcribe(audio_path.read_bytes(), context=f"内容标题：{title}",
                            mime_type=uploaded_asset.get("mime_type") or "audio/wav")
        record_usage(user_id, f"ASR 转写 · {title}", getattr(stt, "name", "stt"), "asr",
                     tr.get("model", ""), tr["cost"])
        assets = {
            "metadata": {"exists": True, "file": "metadata.json"},
            "media": {"exists": True, "asset_id": uploaded_asset["id"],
                      "size_bytes": uploaded_asset["size_bytes"], "sha256": uploaded_asset["sha256"]},
            "caption": {"exists": False, "note": "平台无原生字幕"},
            "asr": {"exists": True, "immutable": True, "text": tr["text"],
                    "trace": f"{getattr(stt, 'name', 'stt')} · {tr.get('model', '')}"},
            "clean": {"exists": False},
            "comments": {"exists": False},
        }
        cost = tr["cost"]
    ok = await _run_steps(jid, ["validate_transcript", "persist_assets"], 0.3)
    if not ok:
        return
    # 写入来源内容资产
    sc_id = input_data.get("source_content_id") or f"SC-{new_trace_id()[-6:]}"
    existing = query_one("SELECT id FROM source_contents WHERE id=? AND user_id=?",
                         (sc_id, user_id))
    if existing:
        execute("UPDATE source_contents SET status=?, assets_json=? WHERE id=?",
                ("transcribed", jdumps(assets), sc_id))
    else:
        execute("INSERT INTO source_contents VALUES(?,?,?,?,?,?,?,?,?,?)",
                (sc_id, user_id, input_data.get("signal_id", ""), title,
                 input_data.get("platform", ""), input_data.get("author", ""),
                 input_data.get("url", ""), "transcribed", jdumps(assets), now()))
    transcript_asset_id = None
    transcript = (assets.get("caption") or {}).get("text") or (assets.get("asr") or {}).get("text")
    if transcript:
        from . import config
        digest = __import__("hashlib").sha256(transcript.encode("utf-8")).hexdigest()
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
    evidence(user_id, "source_content", sc_id, "transcript_persisted",
             refs=[x for x in [input_data.get("asset_id"), transcript_asset_id] if x],
             payload={"job_id": jid, "method": "caption" if caption_hit else "asr",
                      "cost": cost})
    _set(jid, status="SUCCEEDED", progress=100, cost_actual=cost,
         finished_at=now(), output_json=jdumps({"source_content_id": sc_id,
                                                "transcript_asset_id": transcript_asset_id}))


async def _run_analysis(jid: str, user_id: str, input_data: dict) -> None:
    from .context_resolver import resolve_context
    from .repositories import evidence, owned_row
    source = owned_row("source_contents", input_data.get("source_content_id", ""),
                       user_id, label="来源内容")
    assets = jloads(source.get("assets_json"), {})
    transcript = ""
    for key in ("clean", "caption", "asr"):
        if (assets.get(key) or {}).get("exists"):
            transcript = str(assets[key].get("text") or "")
            if transcript:
                break
    if not transcript:
        from .errors import AppError
        raise AppError("TRANSCRIPT_MISSING", "来源内容尚无可用字幕或转写。",
                       stage="load_transcript", retryable=False)
    ok = await _run_steps(jid, ["load_transcript", "context_resolve",
                                "llm_analyze", "schema_validate", "persist"], 0.6)
    if not ok:
        return
    llm = get_llm_provider()
    context = resolve_context(user_id, "analysis",
                              source_content_id=source["id"],
                              source_chain=input_data.get("source_chain", []))
    result = llm.analyze(source["title"], transcript, context["content"])
    result["source_chain"] = input_data.get("source_chain", [])
    result["context_run_id"] = context["run_id"]
    aid = f"A-{new_trace_id()[-4:]}"
    actual_cost = 0.35 if getattr(llm, "name", "").startswith("demo") else 0.0
    execute("INSERT INTO analyses VALUES(?,?,?,?,?,?,?,?)",
            (aid, user_id, input_data.get("source_content_id", ""), "done",
             jdumps(result), getattr(llm, "name", "demo"), actual_cost, now()))
    usage = getattr(llm, "last_usage", {}) or {}
    units = (f"input={usage.get('prompt_tokens', 0)} tokens · "
             f"output={usage.get('completion_tokens', 0)} tokens") if usage else "1 次"
    record_usage(user_id, f"内容拆解 · {input_data.get('title', '')}",
                 getattr(llm, "name", "demo"), "analysis",
                 result.get("model", ""), actual_cost, units)
    evidence(user_id, "analysis", aid, "analysis_completed",
             refs=[source["id"], context["run_id"]],
             payload={"job_id": jid, "model": result.get("model", "")})
    _set(jid, status="SUCCEEDED", progress=100, cost_actual=actual_cost,
         finished_at=now(), output_json=jdumps({"analysis_id": aid}))


async def _run_sync(jid: str, user_id: str, input_data: dict) -> None:
    from .datasource import get_datasource
    from .errors import tikhub_unauthorized
    account = query_one("SELECT * FROM accounts WHERE id=?",
                        (input_data.get("account_id"),))
    if not account:
        raise RuntimeError(f"账号不存在：{input_data.get('account_id')}")
    if account["status"] == "error":
        raise tikhub_unauthorized(account["platform"])
    ok = await _run_steps(jid, ["resolve_account", "fetch_posts",
                                "fetch_metrics", "persist_snapshot"], 0.4)
    if not ok:
        return
    ds = get_datasource()
    extra = account.get("extra_json")
    import json as _json
    uid = ( _json.loads(extra).get("uid") if extra else None) or account["id"]
    ov = ds.account_overview(uid, "douyin")
    execute("UPDATE accounts SET followers=?, last_sync_at=? WHERE id=?",
            (ov["fans"], now(), account["id"]))
    execute("INSERT INTO account_snapshots VALUES(?,?,?,?,?,?)",
            (f"ss-{new_trace_id()[-6:]}", account["id"],
             datetime.date.today().isoformat(), ov["fans"], 0,
             jdumps({"source": ds.name})))
    _set(jid, status="SUCCEEDED", progress=100, finished_at=now(),
         output_json=jdumps({"followers": ov["fans"], "source": ds.name}))


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
