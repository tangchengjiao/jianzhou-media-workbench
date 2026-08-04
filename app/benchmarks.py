"""对标监控核心服务：添加解析、扫描作品、爆款判定、信号生成、作者分析。

综合分与爆款判定与参考项目一致：
  score = likes*1 + comments*3 + collects*4 + shares*5
  爆款阈值 = 中位数*2（中位数为 0 时取均值*1.5）
"""
import datetime
import json
import time

from .datasource import PLATFORM_LABELS, get_datasource
from .db import execute, jdumps, jloads, query, query_one
from .errors import AppError, not_found


def now() -> str:
    return datetime.datetime.now().isoformat(timespec="seconds")


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


def _emit_signal(user_id: str, sig_id: str, source_type: str, platform: str,
                 author: str, title: str, url: str, published_ts: int,
                 metrics: dict, rel_perf: str, relevance: int, timeliness: str,
                 category: str, provider_name: str) -> None:
    if _signal_exists(user_id, sig_id):
        return
    pub = datetime.datetime.fromtimestamp(published_ts).date().isoformat() if published_ts else ""
    execute(
        "INSERT INTO signals VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (sig_id, user_id, source_type, PLATFORM_LABELS.get(platform, platform),
         author, title, url, pub, jdumps(metrics), rel_perf, relevance,
         timeliness, "new", category, provider_name, now(), None))


def scan_benchmark(bm_id: str, emit_signals: bool = True) -> dict:
    """扫描一个对标账号：拉资料+作品 → 写库 → 算分 → 发现新片/爆款生成信号与消息。"""
    bm = query_one("SELECT * FROM benchmark_accounts WHERE id=?", (bm_id,))
    if not bm:
        raise not_found("对标账号", bm_id)
    ds = get_datasource()
    overview = ds.account_overview(bm["uid"], bm["platform"])
    limit = {"30": 30, "60": 60, "all": 200}.get(bm["scan_range"] or "30", 30)
    vids = ds.account_videos(bm["uid"], bm["platform"], limit=limit)
    works = vids.get("works", [])
    if not works:
        raise AppError("PROVIDER_EMPTY_RESULT",
                       f"数据源未返回 {bm['name'] or bm['uid']} 的任何作品。"
                       "可能是接口映射不正确或账号无公开作品。系统不会用假数据填充。",
                       stage="account_videos", provider=ds.name, retryable=True)

    new_works, updated = [], 0
    latest_id = bm["last_video_id"]
    for w in works:
        if not w.get("video_id"):
            continue
        existing = query_one("SELECT id FROM benchmark_works WHERE bm_id=? AND video_id=?",
                             (bm_id, w["video_id"]))
        if existing:
            execute(
                "UPDATE benchmark_works SET plays=?, likes=?, comments=?, collects=?, shares=? WHERE id=?",
                (w["plays"], w["likes"], w["comments"], w["collects"], w["shares"], existing["id"]))
            updated += 1
        else:
            execute(
                "INSERT INTO benchmark_works VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (f"bw-{int(time.time()*1000)}-{w['video_id'][-6:]}", bm_id, w["video_id"],
                 w["title"], w.get("cover", ""), w.get("url", ""), w.get("create_time") or 0,
                 w["plays"], w["likes"], w["comments"], w["collects"], w["shares"],
                 bm_score(w), 0, w.get("duration") or 0, now()))
            new_works.append(w)
    if works:
        latest_id = max((w for w in works if w.get("create_time")),
                        key=lambda x: x["create_time"], default=works[0])["video_id"]

    execute(
        "UPDATE benchmark_accounts SET name=?, avatar=?, bio=?, fans=?, likes_count=?,"
        " video_count=?, last_video_id=?, last_scan_at=? WHERE id=?",
        (overview.get("name") or bm["name"], overview.get("avatar", ""),
         overview.get("bio", ""), overview.get("fans", 0), overview.get("likes", 0),
         overview.get("video_count", 0), latest_id, now(), bm_id))
    stats = compute_account_stats(bm_id)
    execute(
        "UPDATE benchmark_accounts SET median_score=?, avg_score=?, post_gap_days=? WHERE id=?",
        (stats["median_score"], stats["avg_score"], stats["post_gap_days"], bm_id))

    created_signals = []
    if emit_signals:
        uid = bm["user_id"]
        name = overview.get("name") or bm["name"] or bm["uid"]
        for w in new_works[:5]:
            sid = f"SG-BM-{bm_id[-4:]}-{w['video_id'][-6:]}"
            _emit_signal(uid, sid, "对标 · 新作", bm["platform"], "@" + name,
                         w["title"], w.get("url", ""), w.get("create_time") or 0,
                         {"plays": w["plays"], "likes": w["likes"],
                          "comments": w["comments"], "collects": w["collects"]},
                         "", 70, "新发布", "new", ds.name)
            created_signals.append(sid)
        hot_works = query("SELECT * FROM benchmark_works WHERE bm_id=? AND is_hot=1",
                          (bm_id,))
        for w in hot_works[:3]:
            sid = f"SG-HOT-{bm_id[-4:]}-{w['video_id'][-6:]}"
            rel = f"账号均值 {round(w['score'] / max(stats['avg_score'], 1), 1)}×" if stats["avg_score"] else ""
            _emit_signal(uid, sid, "对标 · 高表现", bm["platform"], "@" + name,
                         w["title"], w["url"], w["create_time"],
                         {"plays": w["plays"], "likes": w["likes"],
                          "comments": w["comments"], "collects": w["collects"]},
                         rel, 85, "热度上升中", "hot", ds.name)
            created_signals.append(sid)
        if new_works:
            execute(
                "INSERT INTO inbox VALUES(?,?,?,?,?,?,?,?)",
                (f"in-{int(time.time()*1000)}", uid, "benchmark_new",
                 f"「{name}」发现 {len(new_works)} 条新作品",
                 f"其中爆款候选 {sum(1 for w in new_works if bm_score(w) >= stats['median_score']*2)} 条 · 点击到雷达处理",
                 jdumps({"bm_id": bm_id}), 0, now()))
            execute("UPDATE benchmark_accounts SET has_update=1 WHERE id=?", (bm_id,))
    return {"new": len(new_works), "updated": updated, "signals": created_signals,
            "stats": stats}


def add_benchmark(user_id: str, link: str, platform: str | None,
                  scan_range: str = "30", note: str = "") -> dict:
    """解析链接 → 建档 → 首次扫描。失败显式报错，不创建空壳账号。"""
    ds = get_datasource()
    resolved = ds.resolve_account(link, platform)
    bm_id = f"bm-{int(time.time())}-{resolved['uid'][-6:]}"
    execute(
        "INSERT INTO benchmark_accounts VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (bm_id, user_id, resolved["platform"], resolved["uid"], link.strip(),
         resolved.get("name", ""), "", "", 0, 0, 0, 0, 0, 0.0, None, 0,
         scan_range, note, now(), None))
    try:
        result = scan_benchmark(bm_id, emit_signals=True)
    except Exception:
        # 首次扫描失败：保留账号但明确标记，不伪造同步成功
        execute("UPDATE benchmark_accounts SET note=? WHERE id=?",
                ((note + " " if note else "") + "首次扫描失败，请手动刷新重试", bm_id))
        raise
    return {"id": bm_id, **result}


def author_analysis(bm_id: str) -> dict:
    """作者分析：规则引擎指标 + LLM（demo 或真实）综合解读。明确标注分析引擎。"""
    bm = query_one("SELECT * FROM benchmark_accounts WHERE id=?", (bm_id,))
    if not bm:
        raise not_found("对标账号", bm_id)
    works = query("SELECT * FROM benchmark_works WHERE bm_id=? ORDER BY score DESC",
                  (bm_id,))
    if not works:
        raise AppError("ANALYSIS_NO_DATA", "该账号尚无作品数据，请先扫描。",
                       stage="author_analysis")
    top = works[:5]
    hot = [w for w in works if w["is_hot"]]
    from . import config as _cfg
    from .providers import get_llm_provider
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
        text = llm.chat("analysis_model", "你是内容操盘手，只输出合法 JSON。", prompt)
        reading = _extract_json(text)
        engine = f"规则引擎 + LLM（{llm.name}）"
    result = {"metrics": metrics, "reading": reading, "engine": engine}
    aid = f"aa-{int(time.time())}"
    execute("INSERT INTO author_analyses VALUES(?,?,?,?,?)",
            (aid, bm_id, engine, jdumps(result), now()))
    return result


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
