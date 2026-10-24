"""对标监控核心服务：添加解析、扫描作品、爆款判定、信号生成、作者分析。

综合分与爆款判定与参考项目一致：
  score = likes*1 + comments*3 + collects*4 + shares*5
  爆款阈值 = 中位数*2（中位数为 0 时取均值*1.5）
"""
import datetime
import hashlib
import json

from .datasource import PLATFORM_LABELS, get_datasource
from .account_works import canonical_work_url
from .db import execute, jdumps, query, query_one, transaction
from .errors import AppError, not_found
from .ids import new_id


def now() -> str:
    return datetime.datetime.now().isoformat(timespec="seconds")


def normalize_scan_count(scan_range: str | None, scan_count: int | None = None) -> int:
    if scan_count is not None:
        value = int(scan_count)
    elif scan_range == "all":
        value = 200
    else:
        try:
            value = int(scan_range or 30)
        except (TypeError, ValueError):
            value = 30
    return min(max(value, 1), 500)


def bm_score(w: dict) -> int:
    return int((w.get("likes") or 0) * 1 + (w.get("comments") or 0) * 3
               + (w.get("collects") or 0) * 4 + (w.get("shares") or 0) * 5)


def compute_account_stats(bm_id: str) -> dict:
    works = query("SELECT * FROM benchmark_works WHERE bm_id=?", (bm_id,))
    for w in works:
        score = bm_score(w)
        if score != w["score"]:
            execute("UPDATE benchmark_works SET score=? WHERE id=?", (score, w["id"]))
        w["score"] = score
    scores = sorted((w["score"] for w in works if w["score"] > 0))
    n = len(scores)
    median = (scores[(n - 1) // 2] if n % 2 else (scores[n // 2 - 1] + scores[n // 2]) / 2) if n else 0
    avg = sum(scores) / n if n else 0
    th = median * 2 if median > 0 else (avg * 1.5 if avg > 0 else float("inf"))
    for w in works:
        hot = 1 if w["score"] > 0 and w["score"] >= th else 0
        if hot != w["is_hot"]:
            execute("UPDATE benchmark_works SET is_hot=? WHERE id=?", (hot, w["id"]))
    times = sorted((w["create_time"] for w in works if w["create_time"]), reverse=True)
    gap = (max(0.1, (times[0] - times[-1]) / 86400 / (len(times) - 1))
           if len(times) >= 2 else 0)
    return {"median_score": round(median), "avg_score": round(avg),
            "post_gap_days": round(gap, 1),
            "hot_count": sum(1 for w in works if w["score"] >= th and w["score"] > 0),
            "works_count": len(works)}


def _signal_exists(user_id: str, ref: str) -> bool:
    return query_one("SELECT id FROM signals WHERE user_id=? AND id=?", (user_id, ref)) is not None


def _signal_id(kind: str, user_id: str, bm_id: str, video_id: str) -> str:
    digest = hashlib.sha256(
        f"{kind}\0{user_id}\0{bm_id}\0{video_id}".encode("utf-8")
    ).hexdigest()[:20]
    return f"SG-{kind}-{digest}"


def _emit_signal(user_id: str, sig_id: str, source_type: str, platform: str,
                 author: str, title: str, url: str, published_ts: int,
                 metrics: dict, rel_perf: str, relevance: int, timeliness: str,
                 category: str, provider_name: str,
                 benchmark_work_id: str | None = None) -> None:
    if _signal_exists(user_id, sig_id):
        if benchmark_work_id:
            execute(
                "UPDATE signals SET benchmark_work_id=COALESCE(benchmark_work_id,?) "
                "WHERE id=? AND user_id=?",
                (benchmark_work_id, sig_id, user_id),
            )
        return
    pub = datetime.datetime.fromtimestamp(published_ts).date().isoformat() if published_ts else ""
    execute(
        "INSERT INTO signals(id,user_id,source_type,platform,author,title,url,published_at,"
        "metrics_json,rel_perf,relevance,timeliness,status,category,source,fetched_at,content_id,"
        "benchmark_work_id) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (sig_id, user_id, source_type, PLATFORM_LABELS.get(platform, platform),
         author, title, url, pub, jdumps(metrics), rel_perf, relevance,
         timeliness, "new", category, provider_name, now(), None, benchmark_work_id))


def scan_benchmark(bm_id: str, emit_signals: bool = True, *,
                   kind: str = "refresh", requested_count: int | None = None) -> dict:
    """扫描一个对标账号，并持久化真实同步状态与可恢复错误。"""
    bm = query_one("SELECT * FROM benchmark_accounts WHERE id=?", (bm_id,))
    if not bm:
        raise not_found("对标账号", bm_id)
    count = normalize_scan_count(bm.get("scan_range"), requested_count or bm.get("scan_count"))
    cursor_in = bm.get("next_cursor") if kind == "backfill" else None
    run_id = new_id("bsr")
    ds = get_datasource()
    execute(
        "INSERT INTO benchmark_sync_runs(id,user_id,bm_id,kind,status,requested_count,"
        "cursor_in,cursor_out,has_more,new_count,updated_count,provider,error_json,started_at,finished_at) "
        "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (run_id, bm["user_id"], bm_id, kind, "RUNNING", count, cursor_in, None,
         None, 0, 0, ds.name, None, now(), None),
    )
    execute(
        "UPDATE benchmark_accounts SET sync_status='syncing',"
        " last_error_code=NULL,last_error_message=NULL,last_sync_kind=? WHERE id=?",
        (kind, bm_id),
    )
    try:
        result = _scan_benchmark(bm, emit_signals, kind=kind,
                                 requested_count=count, run_id=run_id)
    except AppError as exc:
        execute(
            "UPDATE benchmark_accounts SET sync_status='error',"
            " last_error_code=?,last_error_message=?,last_failure_at=? WHERE id=?",
            (exc.code, exc.message, now(), bm_id),
        )
        execute("UPDATE benchmark_sync_runs SET status='FAILED',error_json=?,finished_at=? WHERE id=?",
                (jdumps(exc.to_dict()), now(), run_id))
        raise
    except Exception as exc:
        execute(
            "UPDATE benchmark_accounts SET sync_status='error',"
            " last_error_code='UNEXPECTED_ERROR',"
            " last_error_message='同步发生未预期错误，请查看本地日志',last_failure_at=? WHERE id=?",
            (now(), bm_id),
        )
        execute("UPDATE benchmark_sync_runs SET status='FAILED',error_json=?,finished_at=? WHERE id=?",
                (jdumps({"code": "UNEXPECTED_ERROR", "message": str(exc)}), now(), run_id))
        raise
    next_run = (datetime.datetime.now() + datetime.timedelta(
        hours=max(1, int(bm.get("monitor_frequency_hours") or 24))
    )).isoformat(timespec="seconds")
    execute(
        "UPDATE benchmark_accounts SET sync_status='ready',"
        " last_error_code=NULL,last_error_message=NULL,last_success_at=?,next_run_at=? WHERE id=?",
        (now(), next_run, bm_id),
    )
    execute(
        "UPDATE benchmark_sync_runs SET status='SUCCEEDED',cursor_out=?,has_more=?,"
        "new_count=?,updated_count=?,finished_at=? WHERE id=?",
        (result.get("next_cursor"), 1 if result.get("has_more") else 0,
         result["new"], result["updated"], now(), run_id),
    )
    result["run_id"] = run_id
    return result


def _scan_benchmark(bm: dict, emit_signals: bool, *, kind: str,
                    requested_count: int, run_id: str) -> dict:
    """拉取资料与作品，原子写入一次扫描结果。"""
    bm_id = bm["id"]
    ds = get_datasource()
    if kind == "backfill" and not bool(bm.get("has_more", 1)):
        return {"new": 0, "updated": 0, "signals": [],
                "stats": compute_account_stats(bm_id), "has_more": False,
                "next_cursor": bm.get("next_cursor"), "exhausted": True}
    overview = ds.account_overview(bm["uid"], bm["platform"])
    limit = normalize_scan_count(bm.get("scan_range"), requested_count)
    cursor_in = bm.get("next_cursor") if kind == "backfill" else None
    vids = ds.account_videos(bm["uid"], bm["platform"], limit=limit,
                             cursor=cursor_in)
    works = vids.get("works", [])
    if not works:
        if kind == "backfill":
            execute("UPDATE benchmark_accounts SET has_more=0 WHERE id=?", (bm_id,))
            return {"new": 0, "updated": 0, "signals": [],
                    "stats": compute_account_stats(bm_id), "has_more": False,
                    "next_cursor": cursor_in, "exhausted": True}
        raise AppError("PROVIDER_EMPTY_RESULT",
                       f"数据源未返回 {bm['name'] or bm['uid']} 的任何作品。"
                       "可能是接口映射不正确或账号无公开作品。系统不会用假数据填充。",
                       stage="account_videos", provider=ds.name, retryable=True)

    new_works, updated = [], 0
    latest_id = bm["last_video_id"]
    captured_at = now()
    with transaction():
        for w in works:
            if not w.get("video_id"):
                continue
            w["url"] = canonical_work_url(
                bm["platform"], str(w["video_id"]), str(w.get("url") or "")
            )
            existing = query_one(
                "SELECT id FROM benchmark_works WHERE bm_id=? AND video_id=?",
                (bm_id, w["video_id"]),
            )
            if existing:
                execute(
                    "UPDATE benchmark_works SET title=?,cover=?,url=?,create_time=?,"
                    " plays=?,likes=?,comments=?,collects=?,shares=?,duration=? WHERE id=?",
                    (w["title"], w.get("cover", ""), w["url"],
                     w.get("create_time") or 0, w["plays"], w["likes"],
                     w["comments"], w["collects"], w["shares"],
                     w.get("duration") or 0, existing["id"]),
                )
                updated += 1
                work_id = existing["id"]
            else:
                work_id = new_id("bw")
                execute(
                    "INSERT INTO benchmark_works("
                    "id,bm_id,video_id,title,cover,url,create_time,plays,likes,comments,"
                    "collects,shares,score,is_hot,duration,first_seen_at) "
                    "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (work_id, bm_id, w["video_id"],
                     w["title"], w.get("cover", ""), w["url"],
                     w.get("create_time") or 0, w["plays"], w["likes"],
                     w["comments"], w["collects"], w["shares"], bm_score(w),
                     0, w.get("duration") or 0, now()),
                )
                new_works.append(w)
            w["_work_id"] = work_id
            execute(
                "INSERT OR IGNORE INTO benchmark_work_snapshots"
                "(id,bm_id,work_id,metrics_json,captured_at) VALUES(?,?,?,?,?)",
                (new_id("bws"), bm_id, work_id,
                 jdumps({"plays": w["plays"], "likes": w["likes"],
                         "comments": w["comments"], "collects": w["collects"],
                         "shares": w["shares"], "sync_run_id": run_id}), captured_at),
            )
        if works:
            latest_id = max(
                (w for w in works if w.get("create_time")),
                key=lambda x: x["create_time"], default=works[0],
            )["video_id"]
        oldest_ts = min((w.get("create_time") or 0 for w in works if w.get("create_time")),
                        default=0)
        fetched_oldest = (datetime.datetime.fromtimestamp(oldest_ts).isoformat(
            timespec="seconds") if oldest_ts else None)
        oldest_candidates = [value for value in
                             (bm.get("oldest_fetched_at"), fetched_oldest) if value]
        oldest = min(oldest_candidates) if oldest_candidates else None
        cursor_out = vids.get("cursor")
        has_more = bool(vids.get("has_more"))
        # Old databases have cached works but no continuation cursor.  The first
        # refresh after migration initializes it; later refreshes preserve the
        # historical backfill position so "continue fetching" never jumps back.
        cursor_sql = (cursor_out if kind in {"onboard", "backfill"}
                      or not bm.get("next_cursor") else bm.get("next_cursor"))
        more_sql = has_more if kind in {"onboard", "backfill"} else bool(bm.get("has_more", 1))
        execute(
            "UPDATE benchmark_accounts SET name=?,avatar=?,bio=?,fans=?,likes_count=?,"
            " video_count=?,last_video_id=?,last_scan_at=?,next_cursor=?,has_more=?,"
            " oldest_fetched_at=? WHERE id=?",
            (overview.get("name") or bm["name"], overview.get("avatar", ""),
             overview.get("bio", ""), overview.get("fans", 0),
             overview.get("likes", 0), overview.get("video_count", 0),
             latest_id, now(), cursor_sql, 1 if more_sql else 0, oldest, bm_id),
        )
    stats = compute_account_stats(bm_id)
    execute(
        "UPDATE benchmark_accounts SET median_score=?, avg_score=?, post_gap_days=? WHERE id=?",
        (stats["median_score"], stats["avg_score"], stats["post_gap_days"], bm_id))

    created_signals = []
    if emit_signals:
        uid = bm["user_id"]
        name = overview.get("name") or bm["name"] or bm["uid"]
        for w in new_works[:5]:
            sid = _signal_id("BM", uid, bm_id, w["video_id"])
            _emit_signal(uid, sid, "对标 · 新作", bm["platform"], "@" + name,
                         w["title"], w.get("url", ""), w.get("create_time") or 0,
                         {"plays": w["plays"], "likes": w["likes"],
                          "comments": w["comments"], "collects": w["collects"]},
                         "", 70, "新发布", "new", ds.name, w.get("_work_id"))
            created_signals.append(sid)
        hot_works = query("SELECT * FROM benchmark_works WHERE bm_id=? AND is_hot=1",
                          (bm_id,))
        for w in hot_works[:3]:
            sid = _signal_id("HOT", uid, bm_id, w["video_id"])
            rel = f"账号均值 {round(w['score'] / max(stats['avg_score'], 1), 1)}×" if stats["avg_score"] else ""
            _emit_signal(uid, sid, "对标 · 高表现", bm["platform"], "@" + name,
                         w["title"], w["url"], w["create_time"],
                         {"plays": w["plays"], "likes": w["likes"],
                          "comments": w["comments"], "collects": w["collects"]},
                         rel, 85, "热度上升中", "hot", ds.name, w.get("id"))
            created_signals.append(sid)
        if new_works:
            execute(
                "INSERT INTO inbox VALUES(?,?,?,?,?,?,?,?)",
                (new_id("in"), uid, "benchmark_new",
                 f"「{name}」发现 {len(new_works)} 条新作品",
                 f"其中爆款候选 {sum(1 for w in new_works if bm_score(w) >= stats['median_score']*2)} 条 · 点击到雷达处理",
                 jdumps({"bm_id": bm_id}), 0, now()))
            execute("UPDATE benchmark_accounts SET has_update=1 WHERE id=?", (bm_id,))
    from .repositories import evidence
    evidence(bm["user_id"], "benchmark", bm_id, f"benchmark_{kind}_completed",
             refs=[run_id], payload={"new": len(new_works), "updated": updated,
                                     "cursor_in": cursor_in,
                                     "cursor_out": vids.get("cursor"),
                                     "has_more": bool(vids.get("has_more"))})
    return {"new": len(new_works), "updated": updated, "signals": created_signals,
            "stats": stats, "has_more": bool(vids.get("has_more")),
            "next_cursor": vids.get("cursor"), "pages": vids.get("pages", 1)}


def add_benchmark(user_id: str, link: str, platform: str | None,
                  scan_range: str = "30", note: str = "",
                  scan_count: int | None = None) -> dict:
    """解析链接 → 建档 → 首次扫描；配置缺失时绝不创建假账号。"""
    ds = get_datasource()
    if hasattr(ds, "require_configured"):
        ds.require_configured()
    resolved = ds.resolve_account(link, platform)
    count = normalize_scan_count(scan_range, scan_count)
    existing = query_one(
        "SELECT id FROM benchmark_accounts WHERE user_id=? AND platform=? AND uid=?",
        (user_id, resolved["platform"], resolved["uid"]),
    )
    if existing:
        execute(
            "UPDATE benchmark_accounts SET link=?,scan_range=?,scan_count=?,note=? WHERE id=?",
            (link.strip(), str(count), count, note, existing["id"]),
        )
        try:
            result = scan_benchmark(existing["id"], emit_signals=True, kind="refresh",
                                    requested_count=count)
        except AppError as exc:
            return {"id": existing["id"], "new": 0, "updated": 0,
                    "signals": [], "existing": True, "sync_status": "error",
                    "error": exc.to_dict()}
        return {"id": existing["id"], "existing": True, **result}
    bm_id = new_id("bm")
    execute(
        "INSERT INTO benchmark_accounts("
        "id,user_id,platform,uid,link,name,avatar,bio,fans,likes_count,video_count,"
        "median_score,avg_score,post_gap_days,last_video_id,has_update,scan_range,note,"
        "created_at,last_scan_at,sync_status,last_error_code,last_error_message,scan_count) "
        "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (bm_id, user_id, resolved["platform"], resolved["uid"], link.strip(),
         resolved.get("name", ""), "", "", 0, 0, 0, 0, 0, 0.0, None, 0,
         str(count), note, now(), None, "idle", None, None, count))
    try:
        result = scan_benchmark(bm_id, emit_signals=True, kind="onboard",
                                requested_count=count)
    except AppError as exc:
        # 账号入口保留，用户可看见失败原因并重试；绝不写入替代作品。
        return {"id": bm_id, "new": 0, "updated": 0, "signals": [],
                "sync_status": "error", "error": exc.to_dict()}
    return {"id": bm_id, **result}


def author_analysis(bm_id: str, *, force: bool = False) -> dict:
    """作者分析：规则引擎指标 + LLM（demo 或真实）综合解读。明确标注分析引擎。"""
    bm = query_one("SELECT * FROM benchmark_accounts WHERE id=?", (bm_id,))
    if not bm:
        raise not_found("对标账号", bm_id)
    works = query("SELECT * FROM benchmark_works WHERE bm_id=? ORDER BY score DESC",
                  (bm_id,))
    if not works:
        raise AppError("ANALYSIS_NO_DATA", "该账号尚无作品数据，请先扫描。",
                       stage="author_analysis")
    input_checksum = hashlib.sha256(jdumps([
        {"video_id": w["video_id"], "title": w["title"], "plays": w["plays"],
         "likes": w["likes"], "comments": w["comments"], "collects": w["collects"],
         "shares": w["shares"]} for w in works
    ]).encode("utf-8")).hexdigest()
    latest = query_one("SELECT * FROM author_analyses WHERE bm_id=? ORDER BY created_at DESC LIMIT 1",
                       (bm_id,))
    if latest and not force:
        cached = json.loads(latest["result_json"])
        if cached.get("input_checksum") == input_checksum:
            return {**cached, "cached": True, "analysis_id": latest["id"]}
    top = works[:5]
    hot = [w for w in works if w["is_hot"]]
    from . import config as _cfg
    from .providers import get_llm_provider, get_setting
    metrics = {
        "works_analyzed": len(works),
        "hot_ratio": round(len(hot) / len(works) * 100, 1) if works else 0,
        "median_score": bm["median_score"], "avg_score": bm["avg_score"],
        "post_gap_days": bm["post_gap_days"],
        "top_titles": [w["title"] for w in top],
        "avg_likes": round(sum(w["likes"] for w in works) / len(works)),
        "collect_rate": round(sum(w["collects"] for w in works)
                              / max(sum(w["likes"] for w in works), 1) * 100, 1),
    }
    if _cfg.DEMO_MODE:
        engine = "规则引擎 + demo_llm（演示数据）"
        reading = {
            "positioning": "职场方法论赛道 · 以「向上管理/汇报表达」为核心支柱",
            "content_formula": "反常识钩子开头 → 痛点共鸣 → 方法步骤 → 模板 CTA，结构高度稳定",
            "strengths": ["开头 8 秒钩子命中率高", "清单体密度稳定（收藏率驱动）",
                          "评论区模板钩子拉互动"],
            "weaknesses": ["选题同质化风险", "案例多口述、缺数据引用"],
            "learn_from": ["归因翻转结构", "模板型 CTA", "痛点具象化写法"],
            "avoid": ["逐字照搬框架（重复度风险）", "对方的个人语气词"],
        }
    else:
        llm = get_llm_provider()
        prompt = (
            "你是内容操盘手。基于以下对标账号数据，输出 JSON："
            '{"positioning":"","content_formula":"","strengths":[],"weaknesses":[],'
            '"learn_from":[],"avoid":[]}。只输出 JSON。\n'
            f"账号：{bm['name']}（{bm['platform']}）· 粉丝 {bm['fans']} · 简介：{bm['bio']}\n"
            f"近 {len(works)} 条作品：中位综合分 {bm['median_score']}，爆款率 {metrics['hot_ratio']}%，"
            f"发布间隔 {bm['post_gap_days']} 天\n"
            f"高分作品标题：{'、'.join(metrics['top_titles'])}"
        )
        text = llm.chat(get_setting("llm", "analysis_model"),
                        "你是内容操盘手，只输出合法 JSON。", prompt)
        reading = _extract_json(text)
        engine = f"规则引擎 + LLM（{llm.name}）"
    result = {"metrics": metrics, "reading": reading, "engine": engine,
              "input_checksum": input_checksum}
    aid = new_id("aa")
    execute("INSERT INTO author_analyses VALUES(?,?,?,?,?)",
            (aid, bm_id, engine, jdumps(result), now()))
    from .repositories import evidence
    evidence(bm["user_id"], "benchmark", bm_id, "author_analysis_persisted",
             refs=[aid], payload={"input_checksum": input_checksum, "engine": engine})
    return {**result, "cached": False, "analysis_id": aid}


def work_to_signal(bm_id: str, video_id: str) -> dict:
    """把任意对标作品接入雷达/提取链路，而不是只处理自动判定的爆款。"""
    bm = query_one("SELECT * FROM benchmark_accounts WHERE id=?", (bm_id,))
    if not bm:
        raise not_found("对标账号", bm_id)
    work = query_one(
        "SELECT * FROM benchmark_works WHERE bm_id=? AND video_id=?",
        (bm_id, video_id),
    )
    if not work:
        raise not_found("作品", video_id)
    signal_id = _signal_id("WORK", bm["user_id"], bm_id, video_id)
    ds = get_datasource()
    _emit_signal(
        bm["user_id"], signal_id, "对标 · 手动关注", bm["platform"],
        "@" + (bm["name"] or bm["uid"]), work["title"], work["url"],
        work["create_time"],
        {"plays": work["plays"], "likes": work["likes"],
         "comments": work["comments"], "collects": work["collects"]},
        (f"账号均值 {round(work['score'] / max(bm['avg_score'], 1), 1)}×"
         if bm["avg_score"] else ""),
        80, "手动加入", "manual", ds.name,
    )
    return {"signal_id": signal_id, "title": work["title"]}


def _extract_json(text: str) -> dict:
    try:
        return json.loads(text)
    except (ValueError, TypeError):
        import re
        m = re.search(r"\{[\s\S]*\}", text or "")
        if m:
            try:
                return json.loads(m.group(0))
            except ValueError:
                pass
    raise AppError("LLM_OUTPUT_INVALID", "作者分析的模型输出不是合法 JSON，任务失败。"
                   "系统不会构造缺失字段。", stage="schema_validate", retryable=True)
