"""调度器（借鉴参考项目 scheduler.js）：定时巡检对标账号与数据快照。

- 每 60s 检查一次是否到达配置时间（HH:MM，本地时区）；
- 日期戳判重：同一天同一任务只自动跑一次，失败下一轮补跑；
- 互斥锁：定时触发与手动触发不并发；
- 演示模式下用 demo 数据源跑同样逻辑；真实模式未配置则空转并明示。
"""
import asyncio
import datetime

from .db import execute, jdumps, jloads, query, query_one

_lock = asyncio.Lock()
_task: asyncio.Task | None = None


def _today() -> str:
    return datetime.date.today().isoformat()


def _schedule_state() -> dict:
    row = query_one("SELECT value_json FROM app_settings WHERE key='schedule'")
    return jloads(row["value_json"], {}) if row else {}


def _save_state(st: dict) -> None:
    execute("INSERT OR REPLACE INTO app_settings VALUES('schedule',?)",
            (jdumps(st),))


def get_config() -> dict:
    row = query_one("SELECT value_json FROM app_settings WHERE key='scheduler'")
    cfg = jloads(row["value_json"], {}) if row else {}
    return {"enabled": cfg.get("enabled", True),
            "benchmark_patrol_time": cfg.get("benchmark_patrol_time", "09:00"),
            "snapshot_time": cfg.get("snapshot_time", "21:00")}


def save_config(cfg: dict) -> None:
    execute("INSERT OR REPLACE INTO app_settings VALUES('scheduler',?)",
            (jdumps(cfg),))


async def run_benchmark_patrol() -> dict:
    from . import config
    from .jobs import create_job
    scope = "demo" if config.DEMO_MODE else "real"
    now = datetime.datetime.now().isoformat(timespec="seconds")
    rows = query(
        "SELECT b.id,b.user_id,b.name,b.monitor_frequency_hours FROM benchmark_accounts b "
        "JOIN workspace_users u ON u.user_id=b.user_id WHERE u.data_scope=? AND b.monitor_enabled=1 "
        "AND (b.next_run_at IS NULL OR b.next_run_at<=?)", (scope, now))
    results, errors = [], []
    for r in rows:
        try:
            job = create_job(r["user_id"], "benchmark_refresh", {
                "title": f"定时刷新 · {r.get('name') or r['id']}",
                "bm_id": r["id"], "count": 20, "trigger": "schedule",
            })
            results.append({"bm_id": r["id"], "job_id": job["id"]})
        except Exception as e:
            errors.append({"bm_id": r["id"], "error": str(e)})
    return {"enqueued": len(results), "failed": len(errors),
            "results": results, "errors": errors}


async def run_snapshot_due() -> dict:
    """Never invent metrics: due rows wait for a real collector or manual input."""
    now = datetime.datetime.now().isoformat(timespec="seconds")
    rows = query(
        "SELECT s.id,s.publication_id,s.tn,p.platform,p.url FROM snapshot_schedules s "
        "JOIN publications p ON p.id=s.publication_id "
        "WHERE s.status='PENDING' AND s.due_at<=? ORDER BY s.due_at", (now,),
    )
    for row in rows:
        execute(
            "UPDATE snapshot_schedules SET status='AWAITING_INPUT',attempts=attempts+1,"
            "last_error=? WHERE id=?",
            ("当前平台未配置获授权的数据采集 Provider；请人工录入真实公开指标", row["id"]),
        )
    return {"due": len(rows), "captured": 0, "awaiting_input": len(rows),
            "note": "没有可验证的指标采集能力时，调度器不会生成演示快照。"}


async def run_radar_sources_due() -> dict:
    from . import config
    from .jobs import create_job
    scope = "demo" if config.DEMO_MODE else "real"
    current = datetime.datetime.now().isoformat(timespec="seconds")
    rows = query(
        "SELECT r.id,r.user_id,r.name FROM radar_sources r "
        "JOIN workspace_users u ON u.user_id=r.user_id WHERE u.data_scope=? AND r.enabled=1 "
        "AND (r.next_run_at IS NULL OR r.next_run_at<=?)", (scope, current))
    jobs = []
    for row in rows:
        job = create_job(row["user_id"], "radar_source_sync", {
            "title": f"定时同步雷达源 · {row['name']}", "source_id": row["id"],
            "trigger": "schedule",
        })
        jobs.append(job["id"])
        # Prevent a one-minute enqueue storm while the worker is still running.
        execute("UPDATE radar_sources SET next_run_at=? WHERE id=?",
                ((datetime.datetime.now() + datetime.timedelta(minutes=10))
                 .isoformat(timespec="seconds"), row["id"]))
    return {"enqueued": len(jobs), "job_ids": jobs}


async def run_task(task_name: str) -> dict:
    """手动或定时触发同一入口；互斥防并发。"""
    if _lock.locked():
        return {"skipped": True, "reason": "任务正在执行中（互斥锁）"}
    async with _lock:
        if task_name == "benchmark_patrol":
            result = await run_benchmark_patrol()
        elif task_name == "radar_sources":
            result = await run_radar_sources_due()
        elif task_name == "snapshot":
            result = await run_snapshot_due()
        else:
            return {"error": f"未知任务：{task_name}"}
        st = _schedule_state()
        st[f"{task_name}_last"] = _today()
        st[f"{task_name}_result"] = {"ok": True, "ts": datetime.datetime.now().isoformat(timespec="seconds")}
        _save_state(st)
        return result


async def _tick() -> None:
    cfg = get_config()
    if not cfg["enabled"]:
        return
    now_hm = datetime.datetime.now().strftime("%H:%M")
    st = _schedule_state()
    if now_hm >= cfg["benchmark_patrol_time"] and st.get("benchmark_patrol_last") != _today():
        await run_task("benchmark_patrol")
    await run_radar_sources_due()
    if now_hm >= cfg["snapshot_time"] and st.get("snapshot_last") != _today():
        await run_task("snapshot")


async def _loop() -> None:
    await asyncio.sleep(3)  # 启动后先补跑一次检查
    while True:
        try:
            await _tick()
        except Exception:
            pass  # 调度失败下一轮补跑，错误已写入任务结果
        await asyncio.sleep(60)


def start() -> None:
    global _task
    if _task is None:
        _task = asyncio.create_task(_loop())


def stop() -> None:
    global _task
    if _task:
        _task.cancel()
        _task = None


def status() -> dict:
    st = _schedule_state()
    return {"config": get_config(),
            "benchmark_patrol_last": st.get("benchmark_patrol_last"),
            "snapshot_last": st.get("snapshot_last"),
            "locked": _lock.locked()}
