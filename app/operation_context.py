"""Evidence-backed operating context assembled from the user's real workspace."""
from __future__ import annotations

import datetime
import hashlib

from .db import execute, jdumps, jloads, query, query_one
from .ids import new_id


def _text_from_assets(value: str | None) -> str:
    assets = jloads(value, {})
    for key in ("clean", "caption", "asr"):
        text = str((assets.get(key) or {}).get("text") or "").strip()
        if text:
            return text
    return ""


def build_operation_context(user_id: str, days: int = 90) -> dict:
    cutoff = int((datetime.datetime.now() - datetime.timedelta(days=days)).timestamp())
    works = query(
        "SELECT w.*,a.platform,a.handle,l.source_content_id,s.assets_json,"
        "(SELECT result_json FROM analyses x WHERE x.source_content_id=l.source_content_id "
        " ORDER BY x.created_at DESC LIMIT 1) analysis_json "
        "FROM account_works w JOIN accounts a ON a.id=w.account_id "
        "LEFT JOIN account_work_source_links l ON l.account_work_id=w.id "
        "LEFT JOIN source_contents s ON s.id=l.source_content_id "
        "WHERE a.user_id=? AND COALESCE(w.create_time,0)>=? ORDER BY w.create_time DESC",
        (user_id, cutoff),
    )
    own_samples = []
    extracted = analyzed = 0
    for row in works[:60]:
        text = _text_from_assets(row.get("assets_json"))
        analysis = jloads(row.get("analysis_json"), {})
        extracted += bool(text)
        analyzed += bool(analysis)
        score = (int(row.get("plays") or 0) + int(row.get("likes") or 0) * 3
                 + int(row.get("comments") or 0) * 5 + int(row.get("collects") or 0) * 6
                 + int(row.get("shares") or 0) * 8)
        own_samples.append({
            "id": row["id"], "title": row.get("title") or "无标题作品",
            "platform": row.get("platform") or "", "published_at": row.get("create_time"),
            "metrics": {k: int(row.get(k) or 0) for k in ("plays", "likes", "comments", "collects", "shares")},
            "performance_score": score, "user_label": row.get("performance_label") or "unrated",
            "content_state": "analyzed" if analysis else "extracted" if text else "metadata_only",
            "text_excerpt": text[:500], "analysis_excerpt": {
                key: analysis.get(key) for key in ("topic", "audience", "structure", "hook", "emotion") if analysis.get(key)
            },
        })
    strongest = sorted(own_samples, key=lambda item: item["performance_score"], reverse=True)[:8]
    weakest = sorted([item for item in own_samples if item["performance_score"] > 0],
                     key=lambda item: item["performance_score"])[:5]

    signals = query(
        "SELECT id,category,source_type,platform,author,title,url,relevance,timeliness,metrics_json,fetched_at "
        "FROM signals WHERE user_id=? AND status<>'ignored' ORDER BY relevance DESC,fetched_at DESC LIMIT 80",
        (user_id,),
    )
    grouped: dict[str, list] = {}
    for row in signals:
        category = row.get("category") or "other"
        grouped.setdefault(category, [])
        if len(grouped[category]) < 8:
            grouped[category].append({
                "id": row["id"], "title": row.get("title") or "", "platform": row.get("platform") or "",
                "author": row.get("author") or "", "source_type": row.get("source_type") or "",
                "relevance": row.get("relevance"), "timeliness": row.get("timeliness"),
                "metrics": jloads(row.get("metrics_json"), {}), "fetched_at": row.get("fetched_at"),
            })

    retros = []
    for row in query(
        "SELECT r.id,r.conclusion_json,r.created_at,p.final_title,p.platform,p.url "
        "FROM retro_reports r JOIN publications p ON p.id=r.publication_id "
        "WHERE r.user_id=? ORDER BY r.created_at DESC LIMIT 12", (user_id,)
    ):
        retros.append({"id": row["id"], "title": row.get("final_title") or "历史复盘",
                       "platform": row.get("platform") or "", "conclusion": jloads(row.get("conclusion_json"), {}),
                       "created_at": row.get("created_at")})

    total = len(works)
    coverage = {
        "own_works": total, "with_text": extracted, "with_analysis": analyzed,
        "text_coverage": round(extracted / total * 100, 1) if total else 0,
        "analysis_coverage": round(analyzed / total * 100, 1) if total else 0,
        "market_signals": len(signals), "signal_categories": {key: len(value) for key, value in grouped.items()},
        "retros": len(retros),
        "gaps": (["自有作品尚未同步"] if not total else [])
                + (["自有作品正文提取覆盖不足50%"] if total and extracted / total < .5 else [])
                + (["尚无真实发布复盘样本"] if not retros else []),
    }
    context = {"own_content": {"latest": own_samples[:12], "strongest": strongest, "weakest": weakest},
               "market_signals": grouped, "retros": retros, "coverage": coverage}
    serialized = jdumps(context)
    source_hash = hashlib.sha256(serialized.encode("utf-8")).hexdigest()
    existing = query_one(
        "SELECT id FROM operation_context_snapshots WHERE user_id=? AND window_days=? AND source_hash=? "
        "ORDER BY created_at DESC LIMIT 1", (user_id, days, source_hash)
    )
    snapshot_id = existing["id"] if existing else new_id("OCS")
    if not existing:
        execute("INSERT INTO operation_context_snapshots VALUES(?,?,?,?,?,?,?)",
                (snapshot_id, user_id, days, source_hash, serialized, jdumps(coverage),
                 datetime.datetime.now().isoformat(timespec="seconds")))
    refs = ([item["id"] for item in own_samples[:20]]
            + [item["id"] for values in grouped.values() for item in values[:4]]
            + [item["id"] for item in retros[:8]])
    return {"id": snapshot_id, "context": context, "coverage": coverage,
            "source_hash": source_hash, "refs": refs}
