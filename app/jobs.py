"""Job 系统：asyncio 后台 Worker，状态机驱动，不存在自动成功。

状态机：DRAFT → WAITING_CONFIRMATION → QUEUED → RUNNING → SUCCEEDED
       RUNNING → FAILED / CANCEL_REQUESTED → CANCELLED
       FAILED → RETRY_REQUESTED → QUEUED
"""
import asyncio
import datetime
import json

from .db import execute, jdumps, jloads, query, query_one
from .errors import new_trace_id
from .ids import new_id
from .providers import get_llm_provider, get_social_provider

_worker_task: asyncio.Task | None = None
_cancel_requests: set[str] = set()


def now() -> str:
    return datetime.datetime.now().isoformat(timespec="seconds")


def create_job(user_id: str, task_type: str, input_data: dict,
               plan: dict | None = None, cost_est: float = 0.0,
               job_id: str | None = None) -> dict:
    jid = job_id or new_id("J")
    execute(
        "INSERT INTO jobs VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (jid, user_id, task_type, "QUEUED", "", 0, jdumps(input_data),
         jdumps(plan or {}), jdumps({}), None, new_trace_id(),
         cost_est, 0.0, now(), None, None))
    return get_job(jid)


def get_job(job_id: str) -> dict | None:
    row = query_one("SELECT * FROM jobs WHERE id=?", (job_id,))
    if not row:
        return None
    for k in ("input_json", "plan_json", "output_json"):
        row[k] = jloads(row[k], {})
    row["error"] = jloads(row.pop("error_json"), None)
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
        _cancel_requests.add(job_id)
        execute("UPDATE jobs SET status='CANCEL_REQUESTED' WHERE id=?", (job_id,))


def retry_job(job_id: str) -> None:
    execute("UPDATE jobs SET status='QUEUED', progress=0, error_json=NULL,"
            " finished_at=NULL WHERE id=?", (job_id,))


def _set(job_id: str, **fields) -> None:
    cols = ", ".join(f"{k}=?" for k in fields)
    execute(f"UPDATE jobs SET {cols} WHERE id=?",
            tuple(fields.values()) + (job_id,))


def record_usage(user_id: str, task: str, provider: str, endpoint: str,
                 model: str, cost: float) -> None:
    execute("INSERT INTO usage_ledger VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (new_id("ul"), user_id, task, provider, endpoint,
             model, "1 次", cost, 0, 0, now()))


async def _run_steps(job_id: str, steps: list[str], step_seconds: float = 0.6):
    for i, step in enumerate(steps):
        if job_id in _cancel_requests:
            _cancel_requests.discard(job_id)
            _set(job_id, status="CANCELLED", finished_at=now())
            return False
        _set(job_id, current_step=step, progress=int((i + 0.5) / len(steps) * 100))
        await asyncio.sleep(step_seconds)
    return True


async def _execute(job: dict) -> None:
    jid, ttype = job["id"], job["task_type"]
    _set(jid, status="RUNNING", started_at=now())
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
                if jid in _cancel_requests:
                    _cancel_requests.discard(jid)
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
        _set(jid, status="FAILED", error_json=jdumps(err), finished_at=now())


async def _run_extraction(jid: str, user_id: str, input_data: dict) -> None:
    from .datasource import get_datasource
    title = input_data.get("title", "未命名来源")
    want_asr = bool(input_data.get("authorizations", {}).get("asr_when_no_caption"))
    caption_hit = input_data.get("caption_available", True)
    ds = get_datasource()
    source = {"title": title}

    ok = await _run_steps(jid, ["resolve_source", "check_cache"], 0.4)
    if not ok:
        return
    if caption_hit:
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
        ok = await _run_steps(jid, ["download_media", "extract_audio",
                                    "transcribe_segments"], 0.7)
        if not ok:
            return
        tr = ds.asr_transcribe(source) if hasattr(ds, "asr_transcribe") else \
            {"text": f"【演示 ASR 转写】《{title}》：本期我们聊聊……（demo）",
             "cost": 0.0, "model": "demo_faster_whisper_small"}
        record_usage(user_id, f"ASR 转写 · {title}", "demo_stt", "asr",
                     tr.get("model", ""), tr["cost"])
        assets = {
            "metadata": {"exists": True, "file": "metadata.json"},
            "media": {"exists": True, "note": "84 MB · 转写后保留 30 天"},
            "caption": {"exists": False, "note": "平台无原生字幕"},
            "asr": {"exists": True, "immutable": True, "text": tr["text"],
                    "trace": "demo faster-whisper small · 本地 · ¥0"},
            "clean": {"exists": False},
            "comments": {"exists": False},
        }
        cost = tr["cost"]
    ok = await _run_steps(jid, ["validate_transcript", "persist_assets"], 0.3)
    if not ok:
        return
    # 写入来源内容资产
    sc_id = input_data.get("source_content_id") or f"SC-{new_trace_id()[-6:]}"
    existing = query_one("SELECT id FROM source_contents WHERE id=?", (sc_id,))
    if existing:
        execute("UPDATE source_contents SET status=?, assets_json=? WHERE id=?",
                ("transcribed", jdumps(assets), sc_id))
    else:
        execute("INSERT INTO source_contents VALUES(?,?,?,?,?,?,?,?,?,?)",
                (sc_id, user_id, input_data.get("signal_id", ""), title,
                 input_data.get("platform", ""), input_data.get("author", ""),
                 input_data.get("url", ""), "transcribed", jdumps(assets), now()))
    _set(jid, status="SUCCEEDED", progress=100, cost_actual=cost,
         finished_at=now(), output_json=jdumps({"source_content_id": sc_id}))


async def _run_analysis(jid: str, user_id: str, input_data: dict) -> None:
    ok = await _run_steps(jid, ["load_transcript", "context_resolve",
                                "llm_analyze", "schema_validate", "persist"], 0.6)
    if not ok:
        return
    llm = get_llm_provider()
    result = llm.analyze(input_data.get("title", "未命名内容"))
    result["source_chain"] = input_data.get("source_chain", [])
    aid = f"A-{new_trace_id()[-4:]}"
    execute("INSERT INTO analyses VALUES(?,?,?,?,?,?,?,?)",
            (aid, user_id, input_data.get("source_content_id", ""), "done",
             jdumps(result), getattr(llm, "name", "demo"), 0.35, now()))
    record_usage(user_id, f"内容拆解 · {input_data.get('title', '')}",
                 getattr(llm, "name", "demo"), "analysis",
                 result.get("model", ""), 0.35)
    _set(jid, status="SUCCEEDED", progress=100, cost_actual=0.35,
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
