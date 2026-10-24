"""Canonical asset index and lineage graph over existing business tables.

The index never replaces source tables. It makes their records searchable and
connects them without copying immutable text/media payloads.
"""
from __future__ import annotations

import datetime
import hashlib
from typing import Any

from .db import execute, jdumps, jloads, query, query_one, transaction


ASSET_LABELS = {
    "own_work": "自有作品",
    "signal": "雷达信号",
    "source": "原始内容",
    "transcript": "提取文案",
    "clean_text": "清洗文案",
    "analysis": "内容拆解",
    "pattern": "模式卡",
    "idea": "灵感与随手记",
    "project": "创作项目",
    "decision": "差异化方向",
    "draft": "稿件版本",
    "campaign": "内容母题",
    "channel_deliverable": "渠道成品",
    "moment": "朋友圈内容",
    "official_article": "公众号文章",
    "channel_retro": "渠道复盘",
    "visual_batch": "视觉批次",
    "visual": "视觉成品",
    "visual_page": "图文页面",
    "visual_deliverable": "视觉交付物",
    "publication": "发布记录",
    "retro": "复盘报告",
}


def _now() -> str:
    return datetime.datetime.now().isoformat(timespec="seconds")


def _stable_id(prefix: str, *parts: Any) -> str:
    digest = hashlib.sha256("\0".join(str(part or "") for part in parts).encode("utf-8")).hexdigest()[:24]
    return f"{prefix}-{digest}"


def asset_id(user_id: str, entity_type: str, entity_id: str, asset_type: str) -> str:
    return _stable_id("AR", user_id, entity_type, entity_id, asset_type)


def _upsert(user_id: str, entity_type: str, entity_id: str, asset_type: str, *,
            title: str, platform: str = "", source_kind: str = "",
            status: str = "ready", summary: str = "", search_text: str = "",
            metadata: dict | None = None, created_at: str | None = None,
            updated_at: str | None = None, deleted_at: str | None = None) -> str:
    record_id = asset_id(user_id, entity_type, entity_id, asset_type)
    stamp = updated_at or created_at or _now()
    execute(
        "INSERT INTO asset_records(id,user_id,entity_type,entity_id,asset_type,title,platform,"
        "source_kind,status,summary,search_text,metadata_json,favorite,created_at,updated_at,deleted_at) "
        "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,0,?,?,?) "
        "ON CONFLICT(user_id,entity_type,entity_id,asset_type) DO UPDATE SET "
        "title=excluded.title,platform=excluded.platform,source_kind=excluded.source_kind,"
        "status=excluded.status,summary=excluded.summary,search_text=excluded.search_text,"
        "metadata_json=excluded.metadata_json,updated_at=excluded.updated_at,deleted_at=excluded.deleted_at",
        (record_id, user_id, entity_type, entity_id, asset_type, title or "未命名资产",
         platform or "", source_kind or "", status or "ready", summary or "",
         search_text or "", jdumps(metadata or {}), created_at or stamp, stamp, deleted_at),
    )
    return record_id


def _relate(user_id: str, from_id: str | None, to_id: str | None,
            relation_type: str, metadata: dict | None = None) -> None:
    if not from_id or not to_id or from_id == to_id:
        return
    execute(
        "INSERT INTO asset_relations(id,user_id,from_asset_id,to_asset_id,relation_type,metadata_json,created_at) "
        "VALUES(?,?,?,?,?,?,?) ON CONFLICT(user_id,from_asset_id,to_asset_id,relation_type) "
        "DO UPDATE SET metadata_json=excluded.metadata_json",
        (_stable_id("RL", user_id, from_id, to_id, relation_type), user_id, from_id, to_id,
         relation_type, jdumps(metadata or {}), _now()),
    )


def link_account_work(user_id: str, account_work_id: str, source_content_id: str,
                      match_method: str = "extraction_job", confidence: float = 1.0) -> None:
    stamp = _now()
    execute(
        "INSERT INTO account_work_source_links(account_work_id,user_id,source_content_id,match_method,"
        "confidence,created_at,updated_at) VALUES(?,?,?,?,?,?,?) "
        "ON CONFLICT(account_work_id) DO UPDATE SET source_content_id=excluded.source_content_id,"
        "match_method=excluded.match_method,confidence=excluded.confidence,updated_at=excluded.updated_at",
        (account_work_id, user_id, source_content_id, match_method, confidence, stamp, stamp),
    )


def _source_layer(assets: dict) -> tuple[str, str]:
    for key, label in (("clean", "clean_text"), ("caption", "transcript"), ("asr", "transcript")):
        value = assets.get(key) or {}
        if value.get("exists") and str(value.get("text") or "").strip():
            return label, str(value.get("text") or "")
    return "source", ""


def sync_asset_index(user_id: str | None = None) -> dict:
    """Idempotently index current data and rebuild lineage for one or all users."""
    users = [user_id] if user_id else [row["id"] for row in query("SELECT id FROM users")]
    indexed = relations = 0
    for uid in users:
        before = query_one("SELECT COUNT(*) n FROM asset_records WHERE user_id=?", (uid,))["n"]
        with transaction():
            # v18: analyses are one logical asset per source. Older builds indexed
            # both the business analysis row and its immutable artifact snapshot,
            # which made every拆解 appear twice in the asset center.
            execute(
                "UPDATE asset_records SET deleted_at=?,updated_at=? WHERE user_id=? AND asset_type='analysis' "
                "AND entity_type IN ('analysis','content_artifact') AND deleted_at IS NULL",
                (_now(), _now(), uid),
            )
            legacy_groups: dict[str, list[dict]] = {}
            for cover in query("SELECT * FROM cover_projects WHERE user_id=? AND visual_batch_id IS NULL", (uid,)):
                generation = jloads(cover.get("generation_json"), {})
                batch_id = str(generation.get("batch_id") or _stable_id("VB", uid, cover["id"]))
                legacy_groups.setdefault(batch_id, []).append(cover)
            for batch_id, covers in legacy_groups.items():
                first = covers[0]
                fields = jloads(first.get("fields_json"), {})
                title = str(fields.get("main_topic") or fields.get("title") or "历史视觉批次")
                status = "FAILED" if all(item.get("status") == "FAILED" for item in covers) else "READY"
                execute(
                    "INSERT OR IGNORE INTO visual_batches(id,user_id,title,creation_project_id,draft_version_id,"
                    "template_id,content_type,ratio,generation_mode,quantity,status,request_json,created_at,updated_at,deleted_at) "
                    "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (batch_id, uid, title, first.get("creation_project_id"), first.get("draft_version_id"),
                     first.get("template_id"), first.get("content_type") or "cover", first.get("ratio") or "3:4",
                     first.get("generation_mode") or "local_template", len(covers), status,
                     jdumps({"backfilled": True}), first.get("created_at") or _now(),
                     max(str(item.get("updated_at") or item.get("created_at") or "") for item in covers), None),
                )
                ordered = sorted(covers, key=lambda item: int((jloads(item.get("generation_json"), {}) or {}).get("index") or 1))
                for index, cover in enumerate(ordered, start=1):
                    role = str((jloads(cover.get("generation_json"), {}) or {}).get("page_role")
                               or ("cover" if index == 1 else "content"))
                    execute("UPDATE cover_projects SET visual_batch_id=?,item_index=?,page_role=? WHERE id=?",
                            (batch_id, index, role, cover["id"]))
            # Own works
            for row in query(
                "SELECT w.*,a.platform,a.handle FROM account_works w JOIN accounts a ON a.id=w.account_id "
                "WHERE a.user_id=?", (uid,)
            ):
                _upsert(uid, "account_work", row["id"], "own_work", title=row.get("title") or "无标题作品",
                        platform=row.get("platform") or "", source_kind="own_account",
                        status="metadata", summary=f"赞 {row.get('likes',0)} · 评 {row.get('comments',0)} · 藏 {row.get('collects',0)}",
                        search_text=f"{row.get('title','')} {row.get('handle','')}", metadata={
                            "account_id": row.get("account_id"), "video_id": row.get("video_id"),
                            "url": row.get("url"), "cover": row.get("cover"),
                            "performance_label": row.get("performance_label", "unrated"),
                            "metrics": {k: row.get(k, 0) for k in ("plays", "likes", "comments", "collects", "shares")},
                        }, created_at=row.get("first_seen_at"), updated_at=row.get("last_seen_at"))

            # Radar signals
            for row in query("SELECT * FROM signals WHERE user_id=?", (uid,)):
                _upsert(uid, "signal", row["id"], "signal", title=row.get("title") or "未命名信号",
                        platform=row.get("platform") or "", source_kind=row.get("source_type") or row.get("source") or "radar",
                        status=row.get("status") or "new", summary=f"{row.get('category','')} · 相关度 {row.get('relevance',0)}%",
                        search_text=f"{row.get('title','')} {row.get('author','')} {row.get('category','')}",
                        metadata={"category": row.get("category"), "url": row.get("url"),
                                  "metrics": jloads(row.get("metrics_json"), {})},
                        created_at=row.get("fetched_at"), updated_at=row.get("fetched_at"))

            # Sources and persisted artifacts
            source_assets: dict[str, str] = {}
            for row in query("SELECT * FROM source_contents WHERE user_id=?", (uid,)):
                assets = jloads(row.get("assets_json"), {})
                layer_type, text = _source_layer(assets)
                source_id = _upsert(uid, "source_content", row["id"], "source",
                                    title=row.get("title") or "未命名来源", platform=row.get("platform") or "",
                                    source_kind="source_content", status=row.get("status") or "saved",
                                    summary=(text[:120] if text else "仅保存来源元数据"),
                                    search_text=f"{row.get('title','')} {row.get('author','')} {text[:1000]}",
                                    metadata={"url": row.get("url"), "signal_id": row.get("signal_id"),
                                              "available_layer": layer_type, "has_text": bool(text)},
                                    created_at=row.get("created_at"), updated_at=row.get("created_at"))
                source_assets[row["id"]] = source_id
            analysis_artifacts: dict[str, list[dict]] = {}
            for row in query("SELECT * FROM content_artifact_versions WHERE user_id=?", (uid,)):
                content = jloads(row.get("content_json"), {})
                if row.get("artifact_type") == "analysis":
                    analysis_artifacts.setdefault(str(row.get("source_content_id") or ""), []).append({**row, "content": content})
                    continue
                artifact_type = "analysis" if row.get("artifact_type") == "analysis" else (
                    "clean_text" if row.get("artifact_type") == "clean" else "transcript")
                text = str(content.get("text") or content.get("topic") or "")
                item_id = _upsert(uid, "content_artifact", row["id"], artifact_type,
                                  title=f"{ASSET_LABELS[artifact_type]} · v{row.get('version_no',1)}",
                                  source_kind=row.get("producer") or "pipeline", status="versioned",
                                  summary=text[:160], search_text=f"{text} {jdumps(content)[:1800]}",
                                  metadata={"source_content_id": row.get("source_content_id"),
                                            "version_no": row.get("version_no"),
                                            "artifact_type": row.get("artifact_type")},
                                  created_at=row.get("created_at"), updated_at=row.get("created_at"))
                _relate(uid, source_assets.get(row.get("source_content_id")), item_id, "derived")

            analysis_rows: dict[str, list[dict]] = {}
            for row in query("SELECT * FROM analyses WHERE user_id=? ORDER BY created_at", (uid,)):
                analysis_rows.setdefault(str(row.get("source_content_id") or ""), []).append(row)
            analysis_assets: dict[str, str] = {}
            for source_content_id in sorted(set(analysis_rows) | set(analysis_artifacts)):
                if not source_content_id:
                    continue
                versions = analysis_rows.get(source_content_id, [])
                snapshots = analysis_artifacts.get(source_content_id, [])
                latest = versions[-1] if versions else None
                latest_result = jloads(latest.get("result_json"), {}) if latest else (snapshots[-1].get("content") or {})
                created_candidates = [str(item.get("created_at") or "") for item in [*versions, *snapshots] if item.get("created_at")]
                title = str(latest_result.get("topic") or "内容拆解")
                group_id = _upsert(
                    uid, "analysis_group", source_content_id, "analysis",
                    title=title, source_kind=(latest.get("provider") if latest else snapshots[-1].get("producer")) or "analysis",
                    status=(latest.get("status") if latest else "versioned") or "done",
                    summary=str(latest_result.get("audience") or latest_result.get("structure") or "")[:180],
                    search_text=" ".join(jdumps(jloads(item.get("result_json"), {})) for item in versions[-5:]) + " " +
                                " ".join(jdumps(item.get("content") or {}) for item in snapshots[-5:]),
                    metadata={"source_content_id": source_content_id, "latest_analysis_id": latest.get("id") if latest else None,
                              "analysis_versions": len(versions), "artifact_snapshots": len(snapshots),
                              "version_count": max(len(versions), len(snapshots), 1)},
                    created_at=min(created_candidates) if created_candidates else _now(),
                    updated_at=max(created_candidates) if created_candidates else _now(),
                )
                analysis_assets[source_content_id] = group_id
                _relate(uid, source_assets.get(source_content_id), group_id, "analyzed_as")

            for row in query("SELECT * FROM patterns WHERE user_id=?", (uid,)):
                _upsert(uid, "pattern", row["id"], "pattern", title=row.get("name") or "模式卡",
                        source_kind="analysis", status=row.get("status") or "candidate", summary=row.get("note") or "",
                        search_text=f"{row.get('name','')} {row.get('note','')} {row.get('fit','')}", metadata=row,
                        created_at=row.get("created_at"), updated_at=row.get("created_at"))

            idea_assets: dict[str, str] = {}
            for row in query("SELECT * FROM content_ideas WHERE user_id=?", (uid,)):
                item_id = _upsert(
                    uid, "content_idea", row["id"], "idea",
                    title=row.get("title") or str(row.get("body") or "")[:60] or "未命名灵感",
                    source_kind=row.get("source_kind") or "manual",
                    status=row.get("status") or "ACTIVE",
                    summary=str(row.get("personal_judgement") or row.get("body") or "")[:180],
                    search_text=f"{row.get('title','')} {row.get('body','')} {row.get('personal_judgement','')} {row.get('tags_json','')}",
                    metadata={"item_type": row.get("item_type"), "source_url": row.get("source_url"),
                              "media": [{key: item.get(key) for key in ("id", "name", "content_type", "size")}
                                        for item in jloads(row.get("media_json"), []) if isinstance(item, dict)],
                              "tags": jloads(row.get("tags_json"), []),
                              "privacy_level": row.get("privacy_level")},
                    created_at=row.get("created_at"), updated_at=row.get("updated_at"),
                    deleted_at=row.get("deleted_at"),
                )
                idea_assets[row["id"]] = item_id

            campaign_assets: dict[str, str] = {}
            for row in query("SELECT * FROM content_campaigns WHERE user_id=?", (uid,)):
                item_id = _upsert(
                    uid, "content_campaign", row["id"], "campaign",
                    title=row.get("title") or "内容母题", source_kind=row.get("source_kind") or "manual",
                    status=row.get("status") or "active", summary=row.get("objective") or row.get("core_topic") or "",
                    search_text=f"{row.get('title','')} {row.get('core_topic','')} {row.get('objective','')} {row.get('audience','')}",
                    metadata={"core_topic": row.get("core_topic"), "source_ref_type": row.get("source_ref_type"),
                              "source_ref_id": row.get("source_ref_id"),
                              "source_chain": jloads(row.get("source_chain_json"), [])},
                    created_at=row.get("created_at"), updated_at=row.get("updated_at"),
                    deleted_at=row.get("archived_at"),
                )
                campaign_assets[row["id"]] = item_id
            for link in query(
                "SELECT l.* FROM campaign_idea_links l JOIN content_campaigns c ON c.id=l.campaign_id "
                "WHERE c.user_id=?", (uid,),
            ):
                _relate(uid, idea_assets.get(link.get("idea_id")),
                        campaign_assets.get(link.get("campaign_id")), link.get("role") or "source")

            project_assets: dict[str, str] = {}
            project_titles: dict[str, str] = {}
            for row in query("SELECT * FROM creation_projects WHERE user_id=?", (uid,)):
                project_title = row.get("title") or "未命名创作项目"
                project_titles[row["id"]] = project_title
                project_stats = query_one(
                    "SELECT COUNT(*) draft_count,COALESCE(MAX(version_no),0) latest_version "
                    "FROM draft_versions WHERE project_id=?", (row["id"],)
                ) or {}
                project_id = _upsert(uid, "creation_project", row["id"], "project", title=row.get("title") or "未命名创作项目",
                                     source_kind="creation", status=row.get("status") or "draft",
                                     summary=row.get("goal") or "", search_text=f"{row.get('title','')} {row.get('goal','')} {row.get('audience','')}",
                                     metadata={"project_id": row.get("id"),
                                               "project_title": project_title,
                                               "source_content_id": row.get("source_content_id"),
                                               "analysis_id": row.get("analysis_id"),
                                               "draft_count": int(project_stats.get("draft_count") or 0),
                                               "latest_version": int(project_stats.get("latest_version") or 0)},
                                     created_at=row.get("created_at"), updated_at=row.get("created_at"))
                project_assets[row["id"]] = project_id
                _relate(uid, campaign_assets.get(row.get("campaign_id")), project_id, "has_public_content")
                for source_id in jloads(row.get("source_chain_json"), []):
                    _relate(uid, idea_assets.get(str(source_id)), project_id, "used_for_creation")
                _relate(uid, source_assets.get(row.get("source_content_id")), project_id, "used_for_creation")
                if row.get("analysis_id"):
                    analysis_row = query_one("SELECT source_content_id FROM analyses WHERE id=? AND user_id=?", (row["analysis_id"], uid))
                    _relate(uid, analysis_assets.get((analysis_row or {}).get("source_content_id")), project_id, "used_for_creation")
            for row in query("SELECT * FROM creation_direction_decisions WHERE user_id=?", (uid,)):
                decision_id = _upsert(
                    uid, "creation_direction_decision", row["id"], "decision",
                    title=(row.get("direction_text") or "差异化方向")[:100], source_kind="creation",
                    status="active" if row.get("active") else "history", summary=row.get("reason") or "",
                    search_text=f"{row.get('direction_text','')} {row.get('reason','')} {row.get('candidate_json','')}",
                    metadata={"project_id": row.get("project_id"),
                              "project_title": project_titles.get(row.get("project_id"), "未命名创作项目"),
                              "run_id": row.get("run_id"),
                              "candidate_key": row.get("candidate_key"), "active": bool(row.get("active"))},
                    created_at=row.get("created_at"), updated_at=row.get("created_at"),
                )
                _relate(uid, project_assets.get(row.get("project_id")), decision_id, "selected_direction")
            for row in query(
                "SELECT d.*,p.user_id FROM draft_versions d JOIN creation_projects p ON p.id=d.project_id WHERE p.user_id=?", (uid,)
            ):
                project_title = project_titles.get(row.get("project_id"), "未命名创作项目")
                draft_id = _upsert(uid, "draft_version", row["id"], "draft",
                                   title=f"《{project_title}》 · {row.get('platform') or '稿件'} · v{row.get('version_no',1)}",
                                   platform=row.get("platform") or "", source_kind=row.get("provider") or "creation",
                                   status="versioned", summary=str(row.get("content") or "")[:180],
                                   search_text=str(row.get("content") or ""), metadata={"project_id": row.get("project_id"),
                                       "project_title": project_title,
                                       "version_no": row.get("version_no"), "style_mode": row.get("style_mode"),
                                       "note": row.get("note")},
                                   created_at=row.get("created_at"), updated_at=row.get("created_at"))
                _relate(uid, project_assets.get(row.get("project_id")), draft_id, "has_version")

            batch_assets: dict[str, str] = {}
            cover_assets: dict[str, str] = {}
            for row in query("SELECT * FROM visual_batches WHERE user_id=?", (uid,)):
                batch_id = _upsert(uid, "visual_batch", row["id"], "visual_batch", title=row.get("title") or "视觉批次",
                                   source_kind=row.get("generation_mode") or "visual", status=row.get("status") or "ready",
                                   summary=f"{row.get('content_type')} · {row.get('ratio')} · {row.get('quantity')}项",
                                   search_text=f"{row.get('title','')} {row.get('content_type','')} {row.get('ratio','')}", metadata={
                                       "creation_project_id": row.get("creation_project_id"), "draft_version_id": row.get("draft_version_id"),
                                       "content_type": row.get("content_type"), "ratio": row.get("ratio"), "quantity": row.get("quantity")},
                                   created_at=row.get("created_at"), updated_at=row.get("updated_at"), deleted_at=row.get("deleted_at"))
                batch_assets[row["id"]] = batch_id
                _relate(uid, project_assets.get(row.get("creation_project_id")), batch_id, "has_visual_batch")
                if row.get("draft_version_id"):
                    _relate(uid, asset_id(uid, "draft_version", row["draft_version_id"], "draft"), batch_id, "visualized_as")
            for row in query("SELECT * FROM cover_projects WHERE user_id=?", (uid,)):
                fields = jloads(row.get("fields_json"), {})
                item_id = _upsert(uid, "cover_project", row["id"], "visual",
                                  title=str(fields.get("main_topic") or fields.get("title") or "视觉成品"),
                                  source_kind=row.get("provider") or "visual", status=row.get("status") or "ready",
                                  summary=f"{row.get('content_type')} · {row.get('ratio')}", search_text=jdumps(fields), metadata={
                                      "preview_url": f"/api/visuals/covers/{row['id']}/preview",
                                      "content_type": row.get("content_type"), "ratio": row.get("ratio"),
                                      "visual_batch_id": row.get("visual_batch_id"), "item_index": row.get("item_index")},
                                  created_at=row.get("created_at"), updated_at=row.get("updated_at"), deleted_at=row.get("deleted_at"))
                cover_assets[row["id"]] = item_id
                _relate(uid, batch_assets.get(row.get("visual_batch_id")), item_id, "contains")

            graphic_assets: dict[str, str] = {}
            for row in query("SELECT * FROM graphic_documents WHERE user_id=?", (uid,)):
                pages = query("SELECT id,title,body,role,page_index,selected_revision_id FROM graphic_pages "
                              "WHERE document_id=? AND user_id=? AND deleted_at IS NULL ORDER BY page_index",
                              (row["id"], uid))
                document_asset = _upsert(
                    uid, "graphic_document", row["id"], "visual",
                    title=row.get("title") or "图文文档", platform=row.get("platform") or "",
                    source_kind=row.get("generation_mode") or "visual",
                    status=row.get("status") or "draft",
                    summary=f"{len(pages)} 页 · {row.get('ratio') or ''}",
                    search_text=" ".join(f"{page.get('title','')} {page.get('body','')}" for page in pages),
                    metadata={"creation_project_id": row.get("creation_project_id"),
                              "draft_version_id": row.get("draft_version_id"),
                              "page_count": len(pages), "ratio": row.get("ratio"),
                              "selected_export_id": row.get("selected_export_id")},
                    created_at=row.get("created_at"), updated_at=row.get("updated_at"),
                    deleted_at=row.get("deleted_at"),
                )
                graphic_assets[row["id"]] = document_asset
                _relate(uid, project_assets.get(row.get("creation_project_id")), document_asset, "has_graphic_document")
                if row.get("draft_version_id"):
                    _relate(uid, asset_id(uid, "draft_version", row["draft_version_id"], "draft"),
                            document_asset, "visualized_as")
                for page in pages:
                    page_asset = _upsert(
                        uid, "graphic_page", page["id"], "visual_page",
                        title=page.get("title") or f"图文第 {page.get('page_index')} 页",
                        platform=row.get("platform") or "", source_kind=row.get("generation_mode") or "visual",
                        status="ready" if page.get("selected_revision_id") else "draft",
                        summary=str(page.get("body") or "")[:180],
                        search_text=f"{page.get('title','')} {page.get('body','')} {page.get('role','')}",
                        metadata={"document_id": row["id"], "page_index": page.get("page_index"),
                                  "role": page.get("role"), "preview_url": f"/api/visuals/graphic-pages/{page['id']}/preview"},
                        created_at=row.get("created_at"), updated_at=row.get("updated_at"),
                    )
                    _relate(uid, document_asset, page_asset, "contains")

            for row in query("SELECT * FROM visual_deliverables WHERE user_id=?", (uid,)):
                deliverable_asset = _upsert(
                    uid, "visual_deliverable", row["id"], "visual_deliverable",
                    title="已采用封面" if row.get("kind") == "cover" else "已定稿图文",
                    source_kind=row.get("kind") or "visual", status=row.get("status") or "selected",
                    summary="已同步到创作项目与内容计划" if row.get("selected") else "历史交付版本",
                    search_text=row.get("metadata_json") or "", metadata=jloads(row.get("metadata_json"), {}),
                    created_at=row.get("created_at"), updated_at=row.get("updated_at"),
                )
                source_asset = (asset_id(uid, "cover_project", row.get("entity_id"), "visual")
                                if row.get("kind") == "cover" else graphic_assets.get(row.get("entity_id")))
                _relate(uid, source_asset, deliverable_asset, "selected_as_deliverable")
                _relate(uid, project_assets.get(row.get("creation_project_id")), deliverable_asset, "uses_visual")

            channel_assets: dict[str, str] = {}
            for row in query("SELECT * FROM channel_deliverables WHERE user_id=?", (uid,)):
                channel_asset = _upsert(
                    uid, "channel_deliverable", row["id"], "channel_deliverable",
                    title=row.get("title") or "渠道成品", platform=row.get("channel") or "",
                    source_kind=row.get("deliverable_type") or "channel", status=row.get("status") or "draft",
                    summary=f"{row.get('channel') or ''} · {row.get('deliverable_type') or ''}",
                    search_text=f"{row.get('title','')} {row.get('metadata_json','')}",
                    metadata={"campaign_id": row.get("campaign_id"), "project_id": row.get("project_id"),
                              "draft_version_id": row.get("draft_version_id"),
                              **jloads(row.get("metadata_json"), {})},
                    created_at=row.get("created_at"), updated_at=row.get("updated_at"),
                    deleted_at=row.get("deleted_at"),
                )
                channel_assets[row["id"]] = channel_asset
                _relate(uid, campaign_assets.get(row.get("campaign_id")), channel_asset, "has_channel_deliverable")
                _relate(uid, project_assets.get(row.get("project_id")), channel_asset, "repurposed_as")
            for row in query(
                "SELECT m.*,d.campaign_id,d.project_id FROM moment_posts m "
                "JOIN channel_deliverables d ON d.id=m.deliverable_id "
                "WHERE m.user_id=?",
                (uid,),
            ):
                moment_media = jloads(row.get("media_json"), {})
                if not isinstance(moment_media, dict):
                    moment_media = {}
                post_asset = _upsert(
                    uid, "moment_post", row["id"], "moment",
                    title=f"朋友圈 · {row.get('content_type') or '内容'}", platform="朋友圈",
                    source_kind="moment", status=row.get("status") or "draft",
                    summary=str(row.get("body") or "")[:180], search_text=row.get("body") or "",
                    metadata={"deliverable_id": row.get("deliverable_id"),
                              "campaign_id": row.get("campaign_id"),
                              "project_id": row.get("project_id"),
                              "scheduled_at": row.get("scheduled_at"), "published_at": row.get("published_at"),
                              "origin_mode": row.get("origin_mode") or "ai_campaign",
                              "idea_id": row.get("idea_id"),
                              "source_refs": jloads(row.get("source_refs_json"), []),
                              "visual_suggestion": moment_media.get("suggestion") or {},
                              "cover_project_ids": moment_media.get("cover_project_ids") or [],
                              "metrics": jloads(row.get("metrics_json"), {})},
                    created_at=row.get("created_at"), updated_at=row.get("updated_at"),
                    deleted_at=row.get("deleted_at"),
                )
                _relate(uid, channel_assets.get(row.get("deliverable_id")), post_asset, "contains")
                _relate(uid, idea_assets.get(row.get("idea_id")), post_asset, "expressed_as_moment")
                for cover_id in moment_media.get("cover_project_ids") or []:
                    _relate(uid, cover_assets.get(cover_id), post_asset, "used_as_moment_visual")
            for row in query(
                "SELECT a.*,d.campaign_id,d.project_id FROM official_articles a "
                "JOIN channel_deliverables d ON d.id=a.deliverable_id "
                "WHERE a.user_id=?",
                (uid,),
            ):
                version = query_one("SELECT * FROM official_article_versions WHERE id=? AND user_id=?",
                                    (row.get("current_version_id"), uid)) or {}
                article_asset = _upsert(
                    uid, "official_article", row["id"], "official_article",
                    title=row.get("title") or "公众号文章", platform="公众号",
                    source_kind="official_article", status=row.get("status") or "draft",
                    summary=row.get("digest") or str(version.get("markdown_content") or "")[:180],
                    search_text=f"{row.get('title','')} {row.get('digest','')} {version.get('markdown_content','')}",
                    metadata={"deliverable_id": row.get("deliverable_id"),
                              "campaign_id": row.get("campaign_id"),
                              "project_id": row.get("project_id"),
                              "current_version_id": row.get("current_version_id"),
                              "review_status": row.get("review_status"), "published_url": row.get("published_url")},
                    created_at=row.get("created_at"), updated_at=row.get("updated_at"),
                    deleted_at=row.get("deleted_at"),
                )
                _relate(uid, channel_assets.get(row.get("deliverable_id")), article_asset, "contains")
                _relate(uid, cover_assets.get(row.get("cover_project_id")), article_asset,
                        "used_as_official_cover")
            for row in query(
                "SELECT r.*,d.campaign_id,d.project_id,d.channel FROM channel_retro_reports r "
                "JOIN channel_deliverables d ON d.id=r.deliverable_id "
                "WHERE r.user_id=?",
                (uid,),
            ):
                report = jloads(row.get("report_json"), {})
                item_id = _upsert(
                    uid, "channel_retro_report", row["id"], "channel_retro",
                    title=f"渠道复盘 · v{row.get('version_no') or 1}",
                    source_kind="channel_retro", status="completed",
                    summary=str(report.get("summary") or "")[:180], search_text=jdumps(report),
                    metadata={"deliverable_id": row.get("deliverable_id"),
                              "campaign_id": row.get("campaign_id"),
                              "project_id": row.get("project_id"),
                              "channel": row.get("channel"),
                              "version_no": row.get("version_no"),
                              "snapshot_hash": row.get("snapshot_hash")},
                    created_at=row.get("created_at"), updated_at=row.get("created_at"),
                )
                _relate(uid, channel_assets.get(row.get("deliverable_id")), item_id, "reviewed_as")

            publication_assets: dict[str, str] = {}
            for row in query("SELECT * FROM publications WHERE user_id=?", (uid,)):
                item_id = _upsert(uid, "publication", row["id"], "publication", title=row.get("final_title") or "发布记录",
                                  platform=row.get("platform") or "", source_kind="publication", status="published",
                                  summary=row.get("url") or "", search_text=f"{row.get('final_title','')} {row.get('note','')}",
                                  metadata={"project_id": row.get("project_id"), "task_id": row.get("task_id"),
                                            "campaign_id": row.get("campaign_id"), "deliverable_id": row.get("deliverable_id"),
                                            "url": row.get("url"), "published_at": row.get("published_at")},
                                  created_at=row.get("published_at"), updated_at=row.get("published_at"))
                publication_assets[row["id"]] = item_id
                _relate(uid, project_assets.get(row.get("project_id")), item_id, "published_as")
                if row.get("visual_deliverable_id"):
                    _relate(uid, asset_id(uid, "visual_deliverable", row["visual_deliverable_id"], "visual_deliverable"),
                            item_id, "published_with")
            for row in query("SELECT * FROM retro_reports WHERE user_id=?", (uid,)):
                conclusion = jloads(row.get("conclusion_json"), {})
                item_id = _upsert(uid, "retro_report", row["id"], "retro", title="内容复盘",
                                  source_kind="retro", status="completed", summary=str(conclusion.get("summary") or conclusion.get("conclusion") or "")[:180],
                                  search_text=jdumps(conclusion), metadata={"publication_id": row.get("publication_id")},
                                  created_at=row.get("created_at"), updated_at=row.get("created_at"))
                _relate(uid, publication_assets.get(row.get("publication_id")), item_id, "reviewed_as")

            # Stable own-work links: explicit first, then conservative URL backfill.
            for job in query(
                "SELECT input_json,output_json FROM jobs WHERE user_id=? AND task_type='extraction' "
                "AND status='SUCCEEDED' ORDER BY finished_at", (uid,)
            ):
                job_input = jloads(job.get("input_json"), {})
                job_output = jloads(job.get("output_json"), {})
                work_id = job_input.get("account_work_id")
                source_id = job_output.get("source_content_id") or job_input.get("source_content_id")
                if (work_id and source_id and query_one(
                    "SELECT id FROM source_contents WHERE id=? AND user_id=?", (source_id, uid)
                )):
                    link_account_work(uid, work_id, source_id, "job_history_backfill", 1.0)
            for link in query("SELECT * FROM account_work_source_links WHERE user_id=?", (uid,)):
                _relate(uid, asset_id(uid, "account_work", link["account_work_id"], "own_work"),
                        source_assets.get(link["source_content_id"]), "extracted_as",
                        {"match_method": link["match_method"], "confidence": link["confidence"]})
            for row in query(
                "SELECT w.id work_id,s.id source_id FROM account_works w JOIN accounts a ON a.id=w.account_id "
                "JOIN source_contents s ON s.user_id=a.user_id AND s.url=w.url "
                "WHERE a.user_id=? AND COALESCE(w.url,'')<>''", (uid,)
            ):
                link_account_work(uid, row["work_id"], row["source_id"], "url_backfill", 0.95)
                _relate(uid, asset_id(uid, "account_work", row["work_id"], "own_work"),
                        source_assets.get(row["source_id"]), "extracted_as", {"match_method": "url_backfill"})

        after = query_one("SELECT COUNT(*) n FROM asset_records WHERE user_id=?", (uid,))["n"]
        indexed += max(0, after - before)
        relations += query_one("SELECT COUNT(*) n FROM asset_relations WHERE user_id=?", (uid,))["n"]
    return {"users": len(users), "indexed": indexed, "relations": relations}


def asset_content(record: dict) -> dict:
    """Resolve a human-readable detail payload from the canonical source table."""
    entity_type, entity_id = record["entity_type"], record["entity_id"]
    if entity_type == "source_content":
        row = query_one("SELECT * FROM source_contents WHERE id=? AND user_id=?", (entity_id, record["user_id"])) or {}
        assets = jloads(row.get("assets_json"), {})
        text = next((str((assets.get(key) or {}).get("text") or "") for key in ("clean", "caption", "asr")
                     if (assets.get(key) or {}).get("exists") and (assets.get(key) or {}).get("text")), "")
        return {"text": text, "raw": {k: v for k, v in row.items() if k != "assets_json"}, "layers": assets}
    if entity_type == "content_artifact":
        row = query_one("SELECT * FROM content_artifact_versions WHERE id=? AND user_id=?", (entity_id, record["user_id"])) or {}
        return {"structured": jloads(row.get("content_json"), {})}
    if entity_type == "analysis":
        row = query_one("SELECT * FROM analyses WHERE id=? AND user_id=?", (entity_id, record["user_id"])) or {}
        return {"structured": jloads(row.get("result_json"), {})}
    if entity_type == "analysis_group":
        analyses = query(
            "SELECT id,status,result_json,model,cost,created_at FROM analyses "
            "WHERE source_content_id=? AND user_id=? ORDER BY created_at DESC",
            (entity_id, record["user_id"]),
        )
        snapshots = query(
            "SELECT id,version_no,producer,sha256,content_json,created_at FROM content_artifact_versions "
            "WHERE source_content_id=? AND user_id=? AND artifact_type='analysis' ORDER BY version_no DESC",
            (entity_id, record["user_id"]),
        )
        latest = jloads(analyses[0].get("result_json"), {}) if analyses else (
            jloads(snapshots[0].get("content_json"), {}) if snapshots else {})
        return {"structured": latest, "source_content_id": entity_id, "versions": [
            {"id": row["id"], "kind": "业务拆解", "version_no": len(analyses) - index,
             "status": row.get("status"), "model": row.get("model"), "provider": row.get("provider") or "llm",
             "created_at": row.get("created_at"), "content": jloads(row.get("result_json"), {})}
            for index, row in enumerate(analyses)
        ], "snapshots": [
            {"id": row["id"], "kind": "不可变快照", "version_no": row.get("version_no"),
             "provider": row.get("producer"), "created_at": row.get("created_at"),
             "content_hash": row.get("sha256")}
            for row in snapshots
        ]}
    if entity_type == "creation_direction_decision":
        row = query_one("SELECT * FROM creation_direction_decisions WHERE id=? AND user_id=?", (entity_id, record["user_id"])) or {}
        return {"text": row.get("direction_text") or "", "structured": jloads(row.get("candidate_json"), {}),
                "raw": {k: v for k, v in row.items() if k != "candidate_json"}}
    if entity_type == "content_idea":
        row = query_one("SELECT * FROM content_ideas WHERE id=? AND user_id=?",
                        (entity_id, record["user_id"])) or {}
        return {"text": row.get("body") or "",
                "structured": {"personal_judgement": row.get("personal_judgement") or "",
                               "source_url": row.get("source_url") or "",
                               "tags": jloads(row.get("tags_json"), []),
                               "media": [{key: item.get(key) for key in ("id", "name", "content_type", "size")}
                                         for item in jloads(row.get("media_json"), []) if isinstance(item, dict)]},
                "raw": {k: v for k, v in row.items()
                        if k not in {"body", "personal_judgement", "tags_json", "media_json"}}}
    if entity_type == "draft_version":
        row = query_one("SELECT d.* FROM draft_versions d JOIN creation_projects p ON p.id=d.project_id WHERE d.id=? AND p.user_id=?",
                        (entity_id, record["user_id"])) or {}
        return {"text": row.get("content") or "", "raw": {k: v for k, v in row.items() if k != "content"}}
    if entity_type == "content_campaign":
        row = query_one("SELECT * FROM content_campaigns WHERE id=? AND user_id=?",
                        (entity_id, record["user_id"])) or {}
        return {"text": row.get("core_topic") or "",
                "structured": {"objective": row.get("objective"), "audience": row.get("audience"),
                               "source_chain": jloads(row.get("source_chain_json"), [])},
                "raw": {k: v for k, v in row.items() if k != "source_chain_json"}}
    if entity_type == "channel_deliverable":
        row = query_one("SELECT * FROM channel_deliverables WHERE id=? AND user_id=?",
                        (entity_id, record["user_id"])) or {}
        return {"structured": jloads(row.get("metadata_json"), {}),
                "raw": {k: v for k, v in row.items() if k != "metadata_json"}}
    if entity_type == "moment_post":
        row = query_one("SELECT * FROM moment_posts WHERE id=? AND user_id=?",
                        (entity_id, record["user_id"])) or {}
        return {"text": row.get("body") or "", "raw": {k: v for k, v in row.items() if k != "body"}}
    if entity_type == "official_article":
        row = query_one("SELECT * FROM official_articles WHERE id=? AND user_id=?",
                        (entity_id, record["user_id"])) or {}
        version = query_one("SELECT * FROM official_article_versions WHERE id=? AND user_id=?",
                            (row.get("current_version_id"), record["user_id"])) or {}
        versions = query("SELECT id,version_no,title,digest,change_note,created_at FROM official_article_versions "
                         "WHERE article_id=? AND user_id=? ORDER BY version_no DESC",
                         (entity_id, record["user_id"]))
        return {"text": version.get("markdown_content") or "", "html": version.get("html_content") or "",
                "versions": versions, "raw": row}
    if entity_type == "channel_retro_report":
        row = query_one("SELECT * FROM channel_retro_reports WHERE id=? AND user_id=?",
                        (entity_id, record["user_id"])) or {}
        return {"structured": jloads(row.get("report_json"), {}),
                "raw": {k: v for k, v in row.items() if k != "report_json"}}
    return {"structured": jloads(record.get("metadata_json"), {})}
