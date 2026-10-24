"""Cross-channel operations: content campaigns, Moments and Official Account.

The campaign is the parent business object. Public content, private-domain posts,
Official Account articles, visual assets and metrics remain separate deliverables
so that progress in one channel never overwrites another channel's evidence.
"""
from __future__ import annotations

import hashlib
import html
import json
import re
import datetime
from pathlib import Path

from fastapi import APIRouter, UploadFile, File
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from .. import config
from ..db import execute, jdumps, jloads, query, query_one, transaction
from ..errors import not_found, state_invalid, validation_failed
from ..ids import new_id
from ..idea_store import create_idea, get_owned as get_owned_idea, link_campaign, public_idea
from ..operation_context import build_operation_context
from ..providers import get_llm_provider, get_setting
from ..repositories import evidence
from ..wechat_official import WeChatOfficialClient, connector_settings
from . import current_user_id, now, ok

router = APIRouter(tags=["channels"])


def _owned(table: str, rid: str, label: str) -> dict:
    row = query_one(f'SELECT * FROM "{table}" WHERE id=? AND user_id=?',
                    (rid, current_user_id()))
    if not row:
        raise not_found(label, rid)
    return row


def _hash(value: str) -> str:
    return hashlib.sha256(str(value or "").encode("utf-8")).hexdigest()


def _ensure_campaign(project_id: str) -> dict:
    uid = current_user_id()
    project = query_one("SELECT * FROM creation_projects WHERE id=? AND user_id=?",
                        (project_id, uid))
    if not project:
        raise not_found("创作项目", project_id)
    if project.get("campaign_id"):
        row = query_one("SELECT * FROM content_campaigns WHERE id=? AND user_id=?",
                        (project["campaign_id"], uid))
        if row:
            return row
    campaign_id = new_id("CMP")
    stamp = now()
    execute(
        "INSERT INTO content_campaigns(id,user_id,title,core_topic,source_kind,source_ref_type,"
        "source_ref_id,source_chain_json,objective,audience,status,created_at,updated_at,archived_at) "
        "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,NULL)",
        (campaign_id, uid, project["title"], project["title"], "creation_project",
         "creation_project", project_id, project.get("source_chain_json") or "[]",
         project.get("goal") or "", project.get("audience") or "", "ACTIVE", stamp, stamp),
    )
    execute("UPDATE creation_projects SET campaign_id=? WHERE id=? AND user_id=?",
            (campaign_id, project_id, uid))
    execute("UPDATE content_tasks SET campaign_id=? WHERE project_id=? AND user_id=?",
            (campaign_id, project_id, uid))
    return query_one("SELECT * FROM content_campaigns WHERE id=?", (campaign_id,))


def _campaign(campaign_id: str | None, project_id: str | None) -> dict:
    if campaign_id:
        return _owned("content_campaigns", campaign_id, "内容母题")
    if project_id:
        return _ensure_campaign(project_id)
    raise validation_failed("请选择内容母题或已保存的创作项目。")


def _source_context(campaign: dict, project_id: str | None = None,
                    draft_version_id: str | None = None) -> dict:
    uid = current_user_id()
    project = None
    if project_id:
        project = query_one("SELECT * FROM creation_projects WHERE id=? AND user_id=?",
                            (project_id, uid))
    elif campaign.get("source_ref_type") == "creation_project":
        project = query_one("SELECT * FROM creation_projects WHERE id=? AND user_id=?",
                            (campaign.get("source_ref_id"), uid))
    if not project:
        project = query_one("SELECT * FROM creation_projects WHERE campaign_id=? AND user_id=? "
                            "ORDER BY created_at DESC LIMIT 1", (campaign["id"], uid))
    draft = None
    if draft_version_id and project:
        draft = query_one("SELECT * FROM draft_versions WHERE id=? AND project_id=?",
                          (draft_version_id, project["id"]))
    if not draft and project:
        draft = query_one("SELECT * FROM draft_versions WHERE project_id=? "
                          "ORDER BY version_no DESC,created_at DESC LIMIT 1", (project["id"],))
    return {
        "campaign_id": campaign["id"], "title": campaign["title"],
        "core_topic": campaign.get("core_topic") or campaign["title"],
        "objective": campaign.get("objective") or "", "audience": campaign.get("audience") or "",
        "source_chain": jloads(campaign.get("source_chain_json"), []),
        "project_id": (project or {}).get("id"), "draft_version_id": (draft or {}).get("id"),
        "source_content": (draft or {}).get("content") or "",
        "platform": (draft or {}).get("platform") or "",
        "project_context": jloads((project or {}).get("context_json"), {}),
    }


def _deliverable(campaign: dict, channel: str, kind: str, title: str,
                 context: dict, metadata: dict | None = None) -> str:
    did = new_id("CD")
    stamp = now()
    execute(
        "INSERT INTO channel_deliverables(id,user_id,campaign_id,project_id,draft_version_id,"
        "channel,deliverable_type,title,status,planned_at,published_at,source_hash,metadata_json,"
        "created_at,updated_at,deleted_at) VALUES(?,?,?,?,?,?,?,?,?,NULL,NULL,?,?,?,?,NULL)",
        (did, current_user_id(), campaign["id"], context.get("project_id"),
         context.get("draft_version_id"), channel, kind, title, "DRAFT",
         _hash(context.get("source_content") or campaign["title"]), jdumps(metadata or {}), stamp, stamp),
    )
    return did


def _parse_deliverable(row: dict) -> dict:
    row["metadata"] = jloads(row.get("metadata_json"), {})
    return row


def _campaign_progress(uid: str, campaign_id: str) -> dict:
    """Return user-visible progress without inventing completion states."""
    project = query_one(
        "SELECT id,status FROM creation_projects WHERE user_id=? AND campaign_id=? "
        "ORDER BY created_at,id LIMIT 1", (uid, campaign_id),
    ) or {}
    task = query_one(
        "SELECT id,status FROM content_tasks WHERE user_id=? AND campaign_id=? "
        "AND delivery_kind='public_content' AND project_id IS NOT NULL "
        "ORDER BY id LIMIT 1", (uid, campaign_id),
    ) or {}
    counts = query_one(
        "SELECT "
        "(SELECT COUNT(*) FROM visual_batches b JOIN creation_projects p "
        " ON p.id=b.creation_project_id WHERE p.user_id=? AND p.campaign_id=? "
        " AND b.deleted_at IS NULL) + "
        "(SELECT COUNT(*) FROM graphic_documents g JOIN creation_projects p "
        " ON p.id=g.creation_project_id WHERE p.user_id=? AND p.campaign_id=? "
        " AND g.deleted_at IS NULL) visual_count,"
        "(SELECT COUNT(*) FROM channel_deliverables d WHERE d.user_id=? AND d.campaign_id=? "
        " AND d.deleted_at IS NULL) channel_count,"
        "(SELECT COUNT(*) FROM channel_retro_reports r JOIN channel_deliverables d "
        " ON d.id=r.deliverable_id WHERE r.user_id=? AND d.campaign_id=?) retro_count",
        (uid, campaign_id, uid, campaign_id, uid, campaign_id, uid, campaign_id),
    ) or {}
    latest_retro = query_one(
        "SELECT r.id,r.version_no,r.report_json,r.created_at FROM channel_retro_reports r "
        "JOIN channel_deliverables d ON d.id=r.deliverable_id "
        "WHERE r.user_id=? AND d.campaign_id=? ORDER BY r.created_at DESC,r.version_no DESC LIMIT 1",
        (uid, campaign_id),
    ) or {}
    report = jloads(latest_retro.get("report_json"), {})
    return {
        "project_id": project.get("id"), "project_status": project.get("status"),
        "plan_task_id": task.get("id"), "plan_task_status": task.get("status"),
        "visual_count": int(counts.get("visual_count") or 0),
        "channel_count": int(counts.get("channel_count") or 0),
        "retro_count": int(counts.get("retro_count") or 0),
        "latest_retro_id": latest_retro.get("id"),
        "latest_retro_summary": str(report.get("summary") or "")[:240],
    }


def _sync_channel_task(deliverable_id: str, status: str, *, platform: str,
                       delivery_kind: str, title: str = "",
                       scheduled_at: str = "") -> str:
    """Mirror a channel deliverable into the planning board without duplicating it.

    Channel tasks intentionally have no project_id: the existing board enforces one
    active public-content task per creation project.  The campaign/deliverable keys
    are the stable cross-channel relationship instead.
    """
    uid = current_user_id()
    deliverable = _owned("channel_deliverables", deliverable_id, "渠道成品")
    task = query_one(
        "SELECT * FROM content_tasks WHERE user_id=? AND deliverable_id=? "
        "AND delivery_kind=? ORDER BY id LIMIT 1",
        (uid, deliverable_id, delivery_kind),
    )
    scheduled_date = (scheduled_at or deliverable.get("planned_at") or now())[:10]
    task_title = (title or deliverable.get("title") or "渠道内容").strip()
    badges = [{"t": "渠道运营", "c": "b-pri"},
              {"t": platform, "c": "b-info"}]
    source = {"kind": delivery_kind, "campaign_id": deliverable["campaign_id"],
              "deliverable_id": deliverable_id}
    if task:
        execute(
            "UPDATE content_tasks SET title=?,status=?,platform=?,scheduled_date=?,"
            "badges_json=?,source_json=?,campaign_id=? WHERE id=? AND user_id=?",
            (task_title, status, platform, scheduled_date, jdumps(badges),
             jdumps(source), deliverable["campaign_id"], task["id"], uid),
        )
        return task["id"]
    task_id = new_id("CT")
    execute(
        "INSERT INTO content_tasks(id,user_id,project_id,title,status,platform,scheduled_date,"
        "estimate_h,priority,badges_json,source_json,campaign_id,deliverable_id,delivery_kind) "
        "VALUES(?,?,NULL,?,?,?,?,?,?,?, ?,?,?,?)",
        (task_id, uid, task_title, status, platform, scheduled_date, 0.5, "mid",
         jdumps(badges), jdumps(source), deliverable["campaign_id"], deliverable_id,
         delivery_kind),
    )
    return task_id


class CampaignIn(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    core_topic: str = Field(default="", max_length=500)
    objective: str = Field(default="", max_length=1000)
    audience: str = Field(default="", max_length=500)
    source_kind: str = Field(default="manual", max_length=50)
    source_ref_type: str = Field(default="", max_length=50)
    source_ref_id: str | None = Field(default=None, max_length=200)
    source_chain: list = Field(default_factory=list)


@router.post("/channels/campaigns")
def create_campaign(body: CampaignIn):
    uid = current_user_id()
    campaign_id = new_id("CMP")
    stamp = now()
    execute(
        "INSERT INTO content_campaigns(id,user_id,title,core_topic,source_kind,source_ref_type,"
        "source_ref_id,source_chain_json,objective,audience,status,created_at,updated_at,archived_at) "
        "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,NULL)",
        (campaign_id, uid, body.title.strip(), (body.core_topic or body.title).strip(),
         body.source_kind, body.source_ref_type, body.source_ref_id, jdumps(body.source_chain),
         body.objective.strip(), body.audience.strip(), "ACTIVE", stamp, stamp),
    )
    evidence(uid, "content_campaign", campaign_id, "campaign_created",
             refs=body.source_chain, payload={"source_kind": body.source_kind})
    return ok({"id": campaign_id, "note": "内容母题已建立；各渠道将作为独立成品留档。"})


@router.post("/channels/campaigns/from-project/{project_id}")
def campaign_from_project(project_id: str):
    row = _ensure_campaign(project_id)
    return ok({**row, "note": "创作项目已关联内容母题；原稿和历史版本未被复制或覆盖。"})


@router.get("/channels/campaigns")
def list_campaigns(status: str = "ACTIVE"):
    uid = current_user_id()
    rows = query(
        "SELECT c.*,(SELECT COUNT(*) FROM channel_deliverables d WHERE d.campaign_id=c.id "
        "AND d.deleted_at IS NULL) deliverable_count FROM content_campaigns c "
        "WHERE c.user_id=? AND (?='all' OR c.status=?) ORDER BY c.updated_at DESC",
        (uid, status, status),
    )
    for row in rows:
        row["progress"] = _campaign_progress(uid, row["id"])
        row["source_chain"] = jloads(row.get("source_chain_json"), [])
    return ok(rows)


@router.get("/channels/campaigns/{campaign_id}")
def campaign_detail(campaign_id: str):
    campaign = _owned("content_campaigns", campaign_id, "内容母题")
    deliverables = [_parse_deliverable(row) for row in query(
        "SELECT * FROM channel_deliverables WHERE user_id=? AND campaign_id=? "
        "AND deleted_at IS NULL ORDER BY updated_at DESC", (current_user_id(), campaign_id))]
    return ok({**campaign, "source_chain": jloads(campaign.get("source_chain_json"), []),
               "progress": _campaign_progress(current_user_id(), campaign_id),
               "deliverables": deliverables})


def _latest_wechat_probe(settings: dict | None = None) -> dict | None:
    settings = settings or connector_settings()
    account_key = _hash(settings.get("app_id") or "unconfigured")[:24]
    probe = query_one(
        "SELECT * FROM wechat_permission_probes WHERE user_id=? AND account_key=? "
        "ORDER BY checked_at DESC LIMIT 1",
        (current_user_id(), account_key),
    )
    if not probe:
        return None
    return {
        "status": probe.get("status"), "reachable": bool(probe.get("reachable")),
        "capabilities": jloads(probe.get("capabilities_json"), {}),
        "errors": jloads(probe.get("errors_json"), []),
        "egress_ips": jloads(probe.get("egress_ips_json"), []),
        "next_actions": jloads(probe.get("next_actions_json"), []),
        "checked_at": probe.get("checked_at"),
    }


@router.get("/channels/overview")
def channels_overview():
    uid = current_user_id()
    counts = {row["channel"]: row["n"] for row in query(
        "SELECT channel,COUNT(*) n FROM channel_deliverables WHERE user_id=? AND deleted_at IS NULL GROUP BY channel",
        (uid,))}
    pending = query(
        "SELECT d.*,c.title campaign_title FROM channel_deliverables d "
        "JOIN content_campaigns c ON c.id=d.campaign_id WHERE d.user_id=? AND d.deleted_at IS NULL "
        "AND d.status IN ('DRAFT','REVIEW_REQUIRED','READY','SCHEDULED','IN_DRAFT_BOX') "
        "ORDER BY d.updated_at DESC LIMIT 20", (uid,))
    connector = connector_settings()
    connector.pop("app_secret", None)
    connector["app_secret_configured"] = bool(get_setting("wechat_official", "app_secret", ""))
    connector["last_probe"] = _latest_wechat_probe(connector)
    if connector["last_probe"] and connector["last_probe"].get("egress_ips"):
        connector["ip_whitelist_note"] = (
            "待加入当前公网出口 IP：" + ", ".join(connector["last_probe"]["egress_ips"])
        )
    return ok({"campaigns": query_one("SELECT COUNT(*) n FROM content_campaigns WHERE user_id=? AND status='ACTIVE'", (uid,))["n"],
               "counts": counts, "pending": [_parse_deliverable(row) for row in pending],
               "wechat": connector,
               "moments_boundary": "个人微信朋友圈无官方自动发布接口：工作台负责生成、编辑、排期、复制和手动发布登记。"})


class GenerateIn(BaseModel):
    campaign_id: str | None = None
    project_id: str | None = None
    draft_version_id: str | None = None
    instruction: str = Field(default="", max_length=4000)


class ManualMomentIn(BaseModel):
    body: str = Field(min_length=1, max_length=5000)
    title: str = Field(default="", max_length=200)
    content_type: str = Field(default="human", max_length=40)
    personal_judgement: str = Field(default="", max_length=5000)
    source_url: str = Field(default="", max_length=2048)
    scheduled_at: str = Field(default="", max_length=40)
    tags: list[str] = Field(default_factory=list)


DEFAULT_MOMENT_RATIOS = {
    "value": 30, "process": 25, "human": 25, "trust": 10, "interaction": 10,
}


class MomentMixPreferenceIn(BaseModel):
    window_days: int = Field(default=7, ge=1, le=90)
    profile_key: str = Field(default="balanced", max_length=40)
    ratios: dict = Field(default_factory=lambda: dict(DEFAULT_MOMENT_RATIOS))


class MomentMixIn(MomentMixPreferenceIn):
    instruction: str = Field(default="", max_length=4000)
    idea_ids: list[str] = Field(default_factory=list)
    max_posts: int = Field(default=5, ge=1, le=7)


def _normalized_moment_ratios(value: dict | None) -> dict:
    allowed = tuple(DEFAULT_MOMENT_RATIOS)
    raw = {key: max(0.0, float((value or {}).get(key, 0) or 0)) for key in allowed}
    total = sum(raw.values())
    if total <= 0:
        return dict(DEFAULT_MOMENT_RATIOS)
    normalized = {key: round(amount / total * 100, 1) for key, amount in raw.items()}
    normalized[allowed[0]] = round(normalized[allowed[0]] + (100 - sum(normalized.values())), 1)
    return normalized


_MOMENT_VISUAL_FORMATS = {
    "real_photo", "screenshot", "knowledge_card", "minimal_card",
    "process_collage", "no_image",
}


def _moment_visual_suggestion(item: dict) -> dict:
    suggestion = item.get("visual_suggestion") if isinstance(item.get("visual_suggestion"), dict) else {}
    content_type = str(item.get("content_type") or "value")
    defaults = {
        "value": ("knowledge_card", "把唯一核心判断做成留白充足的知识卡，不复制整段正文。", "1:1", "强化观点记忆"),
        "process": ("screenshot", "选择本轮真实工作截图并裁掉隐私；没有真实素材时保持纯文字。", "4:3", "补充真实过程证据"),
        "human": ("real_photo", "选择当日真实生活或工作照片，不使用虚构现场图。", "4:3", "增强真实感"),
        "trust": ("screenshot", "选择可公开的真实结果或来源截图，并隐去敏感信息。", "4:3", "提供可核验依据"),
        "interaction": ("minimal_card", "把互动问题做成一个问题加两个短选项的极简卡片。", "1:1", "降低互动门槛"),
    }
    fmt, brief, ratio, reason = defaults.get(content_type, defaults["value"])
    requested = str(suggestion.get("format") or fmt).strip()
    if requested not in _MOMENT_VISUAL_FORMATS:
        requested = fmt
    return {
        "format": requested,
        "brief": str(suggestion.get("brief") or brief).strip()[:600],
        "ratio": str(suggestion.get("ratio") or ratio).strip()[:20],
        "reason": str(suggestion.get("reason") or reason).strip()[:300],
        "source_hint": str(suggestion.get("source_hint") or "优先复用同一内容母题的真实视觉资产；不得伪造现场或反馈。").strip()[:400],
    }


def _moment_media(raw: object, item: dict | None = None) -> dict:
    value = raw
    if isinstance(raw, str):
        value = jloads(raw, {})
    if isinstance(value, dict):
        suggestion = value.get("suggestion") if isinstance(value.get("suggestion"), dict) else {}
        cover_ids = value.get("cover_project_ids") if isinstance(value.get("cover_project_ids"), list) else []
    else:
        suggestion, cover_ids = {}, []
        if isinstance(value, list):
            cover_ids = [entry.get("cover_project_id") for entry in value
                         if isinstance(entry, dict) and entry.get("cover_project_id")]
    source = dict(item or {})
    if suggestion:
        source["visual_suggestion"] = suggestion
    return {
        "suggestion": _moment_visual_suggestion(source),
        "cover_project_ids": list(dict.fromkeys(str(cid) for cid in cover_ids if cid))[:9],
    }


def _campaign_visual_covers(campaign_id: str, bound_ids: list[str] | None = None) -> list[dict]:
    bound = set(bound_ids or [])
    if not campaign_id:
        return []
    covers = query(
        "SELECT cp.*,MAX(COALESCE(vd.selected,0)) deliverable_selected FROM cover_projects cp "
        "JOIN creation_projects p ON p.id=cp.creation_project_id AND p.user_id=cp.user_id "
        "LEFT JOIN visual_deliverables vd ON vd.user_id=cp.user_id AND vd.kind='cover' AND vd.entity_id=cp.id "
        "WHERE cp.user_id=? AND cp.deleted_at IS NULL AND p.campaign_id=? "
        "AND cp.content_type='cover' AND COALESCE(cp.preview_path,'')<>'' "
        "GROUP BY cp.id ORDER BY CASE WHEN cp.id IN ({}) THEN 0 "
        "WHEN MAX(COALESCE(vd.selected,0))=1 THEN 1 ELSE 2 END,cp.created_at DESC LIMIT 40".format(
            ",".join("?" for _ in bound) or "''"
        ),
        (current_user_id(), campaign_id, *bound),
    )
    result = []
    for cover in covers:
        path = Path(str(cover.get("preview_path") or ""))
        if not path.exists() or not path.is_file():
            continue
        fields = jloads(cover.get("fields_json"), {})
        result.append({
            "id": cover["id"], "creation_project_id": cover.get("creation_project_id"),
            "title": str(fields.get("main_topic") or fields.get("title") or "视觉封面"),
            "ratio": cover.get("ratio") or "", "generation_mode": cover.get("generation_mode") or "",
            "selected": bool(cover.get("deliverable_selected")), "bound": cover["id"] in bound,
            "preview_url": f"/api/visuals/covers/{cover['id']}/preview",
        })
    return result


@router.get("/channels/moments/preferences")
def moment_preferences():
    row = query_one("SELECT * FROM moment_mix_preferences WHERE user_id=?",
                    (current_user_id(),))
    return ok({
        "window_days": int((row or {}).get("window_days") or 7),
        "profile_key": (row or {}).get("profile_key") or "balanced",
        "ratios": _normalized_moment_ratios(jloads((row or {}).get("ratios_json"), DEFAULT_MOMENT_RATIOS)),
        "updated_at": (row or {}).get("updated_at"),
    })


@router.put("/channels/moments/preferences")
def save_moment_preferences(body: MomentMixPreferenceIn):
    ratios = _normalized_moment_ratios(body.ratios)
    execute(
        "INSERT INTO moment_mix_preferences(user_id,window_days,profile_key,ratios_json,updated_at) "
        "VALUES(?,?,?,?,?) ON CONFLICT(user_id) DO UPDATE SET window_days=excluded.window_days,"
        "profile_key=excluded.profile_key,ratios_json=excluded.ratios_json,updated_at=excluded.updated_at",
        (current_user_id(), body.window_days, body.profile_key, jdumps(ratios), now()),
    )
    return ok({"window_days": body.window_days, "profile_key": body.profile_key,
               "ratios": ratios, "note": "朋友圈滚动编排比例已保存；它只影响下一次生成。"})


@router.post("/channels/moments/manual")
def create_manual_moment(body: ManualMomentIn):
    uid = current_user_id()
    content_type = body.content_type if body.content_type in DEFAULT_MOMENT_RATIOS else "human"
    idea_type = {"human": "life_note", "process": "work_process", "trust": "trust_evidence",
                 "value": "self_idea", "interaction": "self_idea"}.get(content_type, "self_idea")
    try:
        idea = create_idea(
            uid, body=body.body, title=body.title, item_type=idea_type,
            personal_judgement=body.personal_judgement, source_url=body.source_url,
            tags=body.tags, source_kind="manual_moment",
        )
    except ValueError as exc:
        raise validation_failed(str(exc)) from exc
    stamp = now()
    campaign_id = new_id("CMP")
    campaign_title = body.title.strip() or body.body.strip().splitlines()[0][:80]
    campaign = {"id": campaign_id, "title": campaign_title}
    context = {"project_id": None, "draft_version_id": None, "source_content": body.body}
    target_status = "SCHEDULED" if body.scheduled_at.strip() else "DRAFT"
    with transaction():
        execute(
            "INSERT INTO content_campaigns(id,user_id,title,core_topic,source_kind,source_ref_type,"
            "source_ref_id,source_chain_json,objective,audience,status,created_at,updated_at,archived_at) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,NULL)",
            (campaign_id, uid, campaign_title, campaign_title, "manual_reflection", "content_idea",
             idea["id"], jdumps([idea["id"]]), "本人原始感悟，不经 AI 改写", "朋友圈受众",
             "ACTIVE", stamp, stamp),
        )
        link_campaign(uid, campaign_id, idea["id"], "manual_moment")
        did = _deliverable(campaign, "moments", "manual_moment", campaign_title, context,
                           {"origin_mode": "manual", "idea_id": idea["id"]})
        post_id = new_id("MOM")
        media = _moment_media({}, {"content_type": content_type,
                                  "visual_suggestion": {"format": "no_image",
                                                        "brief": "可保持纯文字，或由你选择与这条真实记录一致的照片。",
                                                        "ratio": "4:3",
                                                        "reason": "本人手写内容优先保留真实感",
                                                        "source_hint": "只使用本人真实素材"}})
        execute(
            "INSERT INTO moment_posts(id,user_id,deliverable_id,sequence_no,content_type,body,media_json,"
            "status,scheduled_at,published_at,publish_note,metrics_json,version_no,created_at,updated_at,"
            "deleted_at,idea_id,source_refs_json,origin_mode) "
            "VALUES(?,?,?,?,?,?,?, ?,?,NULL,'','{}',1,?,?,NULL,?,?,?)",
            (post_id, uid, did, 1, content_type, body.body.strip(), jdumps(media), target_status,
             body.scheduled_at.strip() or None, stamp, stamp, idea["id"], jdumps([idea["id"]]), "manual"),
        )
        _sync_channel_task(did, "READY_TO_PUBLISH" if target_status == "SCHEDULED" else "PRODUCING",
                           platform="朋友圈", delivery_kind="manual_moment",
                           title=f"朋友圈 · {campaign_title}", scheduled_at=body.scheduled_at)
        evidence(uid, "moment_post", post_id, "manual_moment_recorded", refs=[idea["id"], campaign_id],
                 payload={"content_type": content_type, "scheduled_at": body.scheduled_at})
    return ok({"id": post_id, "idea_id": idea["id"], "deliverable_id": did,
               "status": target_status,
               "note": "你的原始感悟已直接保存为朋友圈草稿，并同步进入资料与内容计划；没有调用 AI 改写。"})


@router.post("/channels/moments/compose")
def compose_moment_mix(body: MomentMixIn):
    uid = current_user_id()
    ratios = _normalized_moment_ratios(body.ratios)
    cutoff = (datetime.datetime.now() - datetime.timedelta(days=body.window_days)).isoformat(timespec="seconds")
    requested_ids = [str(item) for item in body.idea_ids if str(item).strip()]
    if requested_ids:
        placeholders = ",".join("?" for _ in requested_ids)
        ideas = query(
            f"SELECT * FROM content_ideas WHERE user_id=? AND deleted_at IS NULL AND id IN ({placeholders}) "
            f"ORDER BY updated_at DESC", (uid, *requested_ids),
        )
    else:
        ideas = query(
            "SELECT * FROM content_ideas WHERE user_id=? AND deleted_at IS NULL AND updated_at>=? "
            "ORDER BY updated_at DESC LIMIT 60", (uid, cutoff),
        )
    operation = build_operation_context(uid, max(30, body.window_days))
    idea_payload = [public_idea(item) for item in ideas]
    idea_refs = {item["id"] for item in idea_payload}
    allowed_refs = set(operation.get("refs") or []) | idea_refs
    gaps = []
    if not any(item.get("item_type") == "life_note" for item in idea_payload):
        gaps.append("近期开启的记录中没有真实生活感悟；本轮不会编造生活经历。")
    if not any(item.get("item_type") == "work_process" for item in idea_payload):
        gaps.append("近期开启的记录中没有工作过程素材；过程型内容会减少或省略。")
    payload = {
        "generation_mode": "rolling_mix", "max_posts": body.max_posts,
        "ratios": ratios, "window_days": body.window_days,
        "instruction": body.instruction, "ideas": idea_payload,
        "operation_context": operation.get("context") or {},
        "allowed_source_refs": sorted(allowed_refs),
        "evidence_policy": "真人感和过程记录必须引用本人灵感ID；素材不足时省略，绝不编造。",
    }
    result = get_llm_provider().generate_channel_moments(payload)
    raw_posts = result.get("posts") or []
    posts = []
    for item in raw_posts[:body.max_posts]:
        content = str(item.get("body") or "").strip()
        content_type = str(item.get("content_type") or "value")
        refs = [str(ref) for ref in (item.get("source_refs") or []) if str(ref) in allowed_refs]
        if not content:
            continue
        if content_type in {"human", "process", "trust"} and not any(ref in idea_refs for ref in refs):
            gaps.append(f"一条{content_type}草稿没有引用本人记录，已阻止保存。")
            continue
        posts.append({**item, "content_type": content_type if content_type in DEFAULT_MOMENT_RATIOS else "value",
                      "body": content, "source_refs": refs})
    if not posts:
        raise state_invalid("当前真实素材不足，模型没有生成可追溯的朋友圈草稿；请先写一条感悟或工作记录。")
    stamp = now()
    campaign_id = new_id("CMP")
    title = f"朋友圈滚动编排 · {stamp[:10]}"
    campaign = {"id": campaign_id, "title": title}
    context = {"project_id": None, "draft_version_id": None,
               "source_content": "\n".join(item.get("body") or "" for item in idea_payload)}
    with transaction():
        execute(
            "INSERT INTO content_campaigns(id,user_id,title,core_topic,source_kind,source_ref_type,"
            "source_ref_id,source_chain_json,objective,audience,status,created_at,updated_at,archived_at) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,NULL)",
            (campaign_id, uid, title, "本周朋友圈内容结构", "moment_mix", "operation_context",
             operation.get("id"), jdumps(sorted(allowed_refs)[:80]), "保持专业价值与真实生活的合理比例",
             "私域关系用户", "ACTIVE", stamp, stamp),
        )
        for idea in idea_payload:
            link_campaign(uid, campaign_id, idea["id"], "moment_mix_source")
        did = _deliverable(campaign, "moments", "moment_mix", title, context,
                           {"origin_mode": "ai_mix", "strategy": result.get("strategy"),
                            "ratios": ratios, "window_days": body.window_days,
                            "provider": result.get("provider"), "model": result.get("model"),
                            "gaps": gaps})
        ids = []
        for index, item in enumerate(posts, 1):
            refs = item["source_refs"]
            idea_id = next((ref for ref in refs if ref.startswith("IDEA")), None)
            post_id = new_id("MOM")
            execute(
                "INSERT INTO moment_posts(id,user_id,deliverable_id,sequence_no,content_type,body,media_json,"
                "status,scheduled_at,published_at,publish_note,metrics_json,version_no,created_at,updated_at,"
                "deleted_at,idea_id,source_refs_json,origin_mode) "
                "VALUES(?,?,?,?,?,?,?,'DRAFT',NULL,NULL,'','{}',1,?,?,NULL,?,?,?)",
                (post_id, uid, did, index, item["content_type"], item["body"],
                 jdumps(_moment_media({}, item)), stamp, stamp, idea_id, jdumps(refs), "ai_mix"),
            )
            ids.append(post_id)
        _sync_channel_task(did, "PRODUCING", platform="朋友圈", delivery_kind="moment_mix",
                           title=title)
        evidence(uid, "channel_deliverable", did, "moment_mix_composed",
                 refs=[operation.get("id"), *ids], payload={"ratios": ratios, "gaps": gaps})
    save_moment_preferences(MomentMixPreferenceIn(window_days=body.window_days,
                                                   profile_key=body.profile_key,
                                                   ratios=ratios))
    return ok({"deliverable_id": did, "post_ids": ids, "count": len(ids),
               "gaps": list(dict.fromkeys(gaps)), "ratios": ratios,
               "note": "滚动朋友圈草稿已按真实资料编排并逐条存档；缺少的生活素材没有被伪造。"})


@router.post("/channels/moments/generate")
def generate_moments(body: GenerateIn):
    campaign = _campaign(body.campaign_id, body.project_id)
    context = _source_context(campaign, body.project_id, body.draft_version_id)
    result = get_llm_provider().generate_channel_moments({**context, "instruction": body.instruction,
                                                          "generation_mode": "single_campaign",
                                                          "max_posts": 2})
    posts = result.get("posts") or []
    if len(posts) > 2:
        posts = posts[:2]
    with transaction():
        did = _deliverable(campaign, "moments", "moment_sequence", campaign["title"], context,
                           {"strategy": result.get("strategy"), "provider": result.get("provider"),
                            "model": result.get("model")})
        stamp = now()
        ids = []
        for index, item in enumerate(posts, 1):
            body_text = str(item.get("body") or "").strip()
            if not body_text:
                continue
            post_id = new_id("MOM")
            media = _moment_media({}, item)
            source_refs = [str(ref) for ref in (item.get("source_refs") or []) if str(ref).strip()]
            execute(
                "INSERT INTO moment_posts(id,user_id,deliverable_id,sequence_no,content_type,body,"
                "media_json,status,scheduled_at,published_at,publish_note,metrics_json,version_no,"
                "created_at,updated_at,deleted_at,idea_id,source_refs_json,origin_mode) "
                "VALUES(?,?,?,?,?,?,?,'DRAFT',NULL,NULL,'','{}',1,?,?,NULL,NULL,?,'ai_campaign')",
                (post_id, current_user_id(), did, index, str(item.get("content_type") or "value"),
                 body_text, jdumps(media), stamp, stamp, jdumps(source_refs)),
            )
            ids.append(post_id)
        execute("UPDATE content_campaigns SET updated_at=? WHERE id=?", (stamp, campaign["id"]))
        evidence(current_user_id(), "channel_deliverable", did, "moments_generated",
                 refs=[context.get("project_id"), context.get("draft_version_id")],
                 payload={"post_ids": ids, "provider": result.get("provider")})
        _sync_channel_task(did, "PRODUCING", platform="朋友圈",
                           delivery_kind="moment_sequence",
                           title=f"朋友圈组 · {campaign['title']}")
    return ok({"deliverable_id": did, "post_ids": ids, "count": len(ids),
               "note": "朋友圈草稿已分条存档；不会自动发布到个人微信。"})


@router.get("/channels/moments")
def list_moments(status: str = "all", limit: int = 80):
    limit = max(1, min(limit, 200))
    rows = query(
        "SELECT m.*,d.campaign_id,d.project_id,d.title deliverable_title,c.title campaign_title "
        "FROM moment_posts m JOIN channel_deliverables d ON d.id=m.deliverable_id "
        "JOIN content_campaigns c ON c.id=d.campaign_id WHERE m.user_id=? AND m.deleted_at IS NULL "
        "AND d.deleted_at IS NULL AND (?='all' OR m.status=?) ORDER BY COALESCE(m.scheduled_at,m.updated_at) DESC LIMIT ?",
        (current_user_id(), status, status, limit))
    for row in rows:
        media = _moment_media(row.get("media_json"), row)
        options = _campaign_visual_covers(str(row.get("campaign_id") or ""), media["cover_project_ids"])
        row["media"] = media
        row["visual_suggestion"] = media["suggestion"]
        row["visual_options"] = options
        row["bound_visuals"] = [item for item in options if item["bound"]]
        row["metrics"] = jloads(row.get("metrics_json"), {})
        row["source_refs"] = jloads(row.get("source_refs_json"), [])
        row["idea"] = public_idea(get_owned_idea(current_user_id(), row.get("idea_id"))) if row.get("idea_id") else None
    return ok(rows)


@router.post("/channels/moments/{post_id}/visual-cover/{cover_id}")
def bind_moment_visual_cover(post_id: str, cover_id: str):
    post = _owned("moment_posts", post_id, "朋友圈草稿")
    deliverable = _owned("channel_deliverables", post["deliverable_id"], "渠道成品")
    options = _campaign_visual_covers(deliverable.get("campaign_id") or "", [cover_id])
    cover = next((item for item in options if item["id"] == cover_id), None)
    if not cover:
        raise validation_failed("只能选择同一内容母题下仍然存在的视觉成品。")
    media = _moment_media(post.get("media_json"), post)
    media["cover_project_ids"] = list(dict.fromkeys([*media["cover_project_ids"], cover_id]))[:9]
    with transaction():
        execute("UPDATE moment_posts SET media_json=?,updated_at=? WHERE id=? AND user_id=?",
                (jdumps(media), now(), post_id, current_user_id()))
        evidence(current_user_id(), "moment_post", post_id, "moment_visual_bound",
                 refs=[cover_id, deliverable.get("campaign_id")], payload={"cover_project_id": cover_id})
    return ok({"id": post_id, "cover_project_id": cover_id,
               "note": "已把同母题视觉成品绑定到这条朋友圈；发布仍由你在微信中手动完成。"})


@router.delete("/channels/moments/{post_id}/visual-cover/{cover_id}")
def unbind_moment_visual_cover(post_id: str, cover_id: str):
    post = _owned("moment_posts", post_id, "朋友圈草稿")
    media = _moment_media(post.get("media_json"), post)
    media["cover_project_ids"] = [item for item in media["cover_project_ids"] if item != cover_id]
    execute("UPDATE moment_posts SET media_json=?,updated_at=? WHERE id=? AND user_id=?",
            (jdumps(media), now(), post_id, current_user_id()))
    evidence(current_user_id(), "moment_post", post_id, "moment_visual_unbound",
             refs=[cover_id], payload={})
    return ok({"id": post_id, "note": "已解除这条朋友圈的配图绑定；视觉工厂中的成品未被删除。"})


class MomentPatch(BaseModel):
    body: str | None = Field(default=None, max_length=5000)
    content_type: str | None = Field(default=None, max_length=40)
    scheduled_at: str | None = Field(default=None, max_length=40)
    status: str | None = Field(default=None, max_length=30)


@router.put("/channels/moments/{post_id}")
def update_moment(post_id: str, body: MomentPatch):
    row = _owned("moment_posts", post_id, "朋友圈草稿")
    content = row["body"] if body.body is None else body.body.strip()
    if not content:
        raise validation_failed("朋友圈正文不能为空。")
    target_status = body.status or row["status"]
    if target_status not in {"DRAFT", "READY", "SCHEDULED", "PUBLISHED", "ARCHIVED"}:
        raise validation_failed("朋友圈状态无效。")
    if body.scheduled_at and target_status == "DRAFT":
        target_status = "SCHEDULED"
    execute("UPDATE moment_posts SET body=?,content_type=?,scheduled_at=?,status=?,version_no=version_no+1,updated_at=? WHERE id=?",
            (content, body.content_type or row["content_type"],
             row.get("scheduled_at") if body.scheduled_at is None else body.scheduled_at,
             target_status, now(), post_id))
    if target_status in {"READY", "SCHEDULED"}:
        _sync_channel_task(row["deliverable_id"], "READY_TO_PUBLISH", platform="朋友圈",
                           delivery_kind="moment_sequence",
                           scheduled_at=(row.get("scheduled_at") if body.scheduled_at is None else body.scheduled_at) or "")
    return ok({"id": post_id, "status": target_status, "note": "朋友圈草稿已保存为新版本。"})


class ManualPublishIn(BaseModel):
    published_at: str = ""
    url: str = Field(default="", max_length=2048)
    note: str = Field(default="", max_length=1000)


@router.post("/channels/moments/{post_id}/publish-manual")
def publish_moment_manual(post_id: str, body: ManualPublishIn):
    row = _owned("moment_posts", post_id, "朋友圈草稿")
    stamp = body.published_at or now()
    deliverable = _owned("channel_deliverables", row["deliverable_id"], "渠道成品")
    record_id = new_id("CPR")
    with transaction():
        execute("UPDATE moment_posts SET status='PUBLISHED',published_at=?,publish_note=?,updated_at=? WHERE id=?",
                (stamp, body.note, now(), post_id))
        remaining = query_one("SELECT COUNT(*) n FROM moment_posts WHERE deliverable_id=? "
                              "AND deleted_at IS NULL AND status<>'PUBLISHED'", (deliverable["id"],))["n"]
        deliverable_status = "PUBLISHED" if remaining == 0 else "READY"
        execute("UPDATE channel_deliverables SET status=?,published_at=?,updated_at=? WHERE id=?",
                (deliverable_status, stamp if remaining == 0 else None, now(), deliverable["id"]))
        execute("INSERT INTO channel_publish_records(id,user_id,deliverable_id,channel,mode,status,remote_id,url,"
                "request_json,response_json,error_json,published_at,created_at,updated_at,subject_type,subject_id) "
                "VALUES(?,?,?,?,?,'PUBLISHED',NULL,?,?, '{}','{}',?,?,?,?,?)",
                (record_id, current_user_id(), deliverable["id"], "moments", "manual", body.url,
                 jdumps({"post_id": post_id, "note": body.note}), stamp, now(), now(),
                 "moment_post", post_id))
        _sync_channel_task(deliverable["id"],
                           "PUBLISHED" if remaining == 0 else "READY_TO_PUBLISH",
                           platform="朋友圈", delivery_kind="moment_sequence")
    return ok({"id": record_id, "published_at": stamp,
               "note": "已登记为手动发布；系统未调用个人微信自动发布。"})


class MetricsIn(BaseModel):
    tn: str = Field(default="manual", max_length=20)
    metrics: dict = Field(default_factory=dict)
    subject_type: str = Field(default="deliverable", max_length=40)
    subject_id: str = Field(default="", max_length=120)


def _metric_subject(deliverable: dict, subject_type: str, subject_id: str) -> tuple[str, str]:
    kind = str(subject_type or "deliverable").strip()
    sid = str(subject_id or "").strip()
    if kind == "deliverable":
        published = deliverable.get("status") == "PUBLISHED" or query_one(
            "SELECT id FROM channel_publish_records WHERE user_id=? AND deliverable_id=? "
            "AND status='PUBLISHED' ORDER BY created_at DESC LIMIT 1",
            (current_user_id(), deliverable["id"]),
        )
        if not published:
            raise state_invalid("渠道成品尚未真实发布，不能录入发布后数据。")
        return kind, ""
    tables = {"moment_post": "moment_posts", "official_article": "official_articles"}
    table = tables.get(kind)
    if not table or not sid:
        raise validation_failed("指标对象必须是整个成品、具体朋友圈或公众号文章。")
    row = _owned(table, sid, "渠道内容")
    if row.get("deliverable_id") != deliverable["id"]:
        raise validation_failed("指标对象不属于当前渠道成品。")
    if row.get("status") != "PUBLISHED":
        raise state_invalid("这条内容尚未真实发布，不能录入发布后数据。")
    return kind, sid


@router.post("/channels/deliverables/{deliverable_id}/metrics")
def save_channel_metrics(deliverable_id: str, body: MetricsIn):
    row = _owned("channel_deliverables", deliverable_id, "渠道成品")
    if not body.metrics:
        raise validation_failed("请至少填写一项真实渠道指标。")
    if any(isinstance(value, (dict, list)) for value in body.metrics.values()):
        raise validation_failed("渠道指标只接受数字或简短文本，不能嵌套对象。")
    kind, sid = _metric_subject(row, body.subject_type, body.subject_id)
    uid = current_user_id()
    existing = query_one(
        "SELECT id FROM channel_metric_snapshots WHERE user_id=? AND deliverable_id=? "
        "AND subject_type=? AND subject_id=? AND tn=? AND source='manual'",
        (uid, deliverable_id, kind, sid, body.tn.strip() or "manual"),
    )
    snapshot_id = (existing or {}).get("id") or new_id("CMS")
    stamp = now()
    with transaction():
        if existing:
            execute("UPDATE channel_metric_snapshots SET metrics_json=?,taken_at=? WHERE id=? AND user_id=?",
                    (jdumps(body.metrics), stamp, snapshot_id, uid))
        else:
            execute("INSERT INTO channel_metric_snapshots(id,user_id,deliverable_id,channel,subject_type,subject_id,tn,metrics_json,source,taken_at) "
                    "VALUES(?,?,?,?,?,?,?,?,?,?)",
                    (snapshot_id, uid, deliverable_id, row["channel"], kind, sid,
                     body.tn.strip() or "manual", jdumps(body.metrics), "manual", stamp))
        evidence(uid, "channel_deliverable", deliverable_id, "channel_metrics_saved",
                 refs=[snapshot_id, sid] if sid else [snapshot_id],
                 payload={"subject_type": kind, "tn": body.tn, "updated": bool(existing)})
    return ok({"id": snapshot_id, "updated": bool(existing),
               "note": "真实渠道数据已写入复盘层，不会改写发布前稿件。"})


def _performance_payload(deliverable: dict) -> dict:
    uid = current_user_id()
    snapshots = query(
        "SELECT * FROM channel_metric_snapshots WHERE user_id=? AND deliverable_id=? "
        "ORDER BY taken_at,id", (uid, deliverable["id"]),
    )
    for item in snapshots:
        item["metrics"] = jloads(item.pop("metrics_json", None), {})
    records = query(
        "SELECT * FROM channel_publish_records WHERE user_id=? AND deliverable_id=? "
        "ORDER BY created_at,id", (uid, deliverable["id"]),
    )
    for item in records:
        item["request"] = jloads(item.pop("request_json", None), {})
        item["response"] = jloads(item.pop("response_json", None), {})
        item["error"] = jloads(item.pop("error_json", None), {})
    reports = query(
        "SELECT * FROM channel_retro_reports WHERE user_id=? AND deliverable_id=? "
        "ORDER BY version_no DESC", (uid, deliverable["id"]),
    )
    for item in reports:
        item["report"] = jloads(item.pop("report_json", None), {})
    return {"deliverable": _parse_deliverable(deliverable), "publish_records": records,
            "snapshots": snapshots, "retro_reports": reports}


@router.get("/channels/deliverables/{deliverable_id}/performance")
def channel_performance(deliverable_id: str):
    return ok(_performance_payload(_owned("channel_deliverables", deliverable_id, "渠道成品")))


@router.post("/channels/deliverables/{deliverable_id}/retro")
def generate_channel_retro(deliverable_id: str):
    deliverable = _owned("channel_deliverables", deliverable_id, "渠道成品")
    payload = _performance_payload(deliverable)
    published_records = [item for item in payload["publish_records"] if item.get("status") == "PUBLISHED"]
    if not published_records:
        raise state_invalid("渠道内容尚未登记发布，不能生成发布后复盘。")
    if not payload["snapshots"]:
        raise state_invalid("请先录入至少一个真实数据快照，再生成复盘。")
    canonical = json.dumps({"publish_records": published_records,
                            "snapshots": payload["snapshots"]},
                           ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    snapshot_hash = _hash(canonical)
    uid = current_user_id()
    existing = query_one(
        "SELECT * FROM channel_retro_reports WHERE user_id=? AND deliverable_id=? AND snapshot_hash=?",
        (uid, deliverable_id, snapshot_hash),
    )
    if existing:
        existing["report"] = jloads(existing.pop("report_json", None), {})
        return ok({**existing, "reused": True,
                   "note": "数据快照未变化，已复用既有复盘，未重复计费。"})
    result = get_llm_provider().review_channel_performance({
        "deliverable": payload["deliverable"], "publish_records": payload["publish_records"],
        "snapshots": payload["snapshots"],
    })
    latest = query_one("SELECT MAX(version_no) n FROM channel_retro_reports WHERE deliverable_id=?",
                       (deliverable_id,))
    version_no = int((latest or {}).get("n") or 0) + 1
    report_id = new_id("CRR")
    with transaction():
        execute("INSERT INTO channel_retro_reports(id,user_id,deliverable_id,version_no,snapshot_hash,report_json,provider,model,created_at) "
                "VALUES(?,?,?,?,?,?,?,?,?)",
                (report_id, uid, deliverable_id, version_no, snapshot_hash, jdumps(result),
                 result.get("provider") or "", result.get("model") or "", now()))
        evidence(uid, "channel_deliverable", deliverable_id, "channel_retro_generated",
                 refs=[report_id] + [item["id"] for item in payload["snapshots"]],
                 payload={"version_no": version_no, "snapshot_hash": snapshot_hash})
    return ok({"id": report_id, "version_no": version_no, "report": result,
               "reused": False, "note": "复盘已按当前真实快照存档；新数据会生成下一版本。"})


@router.delete("/channels/moments/{post_id}")
def delete_moment(post_id: str):
    row = _owned("moment_posts", post_id, "朋友圈草稿")
    execute("UPDATE moment_posts SET deleted_at=?,updated_at=? WHERE id=?", (now(), now(), post_id))
    remaining = query_one("SELECT COUNT(*) n FROM moment_posts WHERE deliverable_id=? AND deleted_at IS NULL",
                          (row["deliverable_id"],))["n"]
    if remaining == 0:
        execute("UPDATE channel_deliverables SET deleted_at=?,updated_at=? WHERE id=?",
                (now(), now(), row["deliverable_id"]))
        execute("UPDATE content_tasks SET status='CANCELLED' WHERE user_id=? AND deliverable_id=?",
                (current_user_id(), row["deliverable_id"]))
    return ok({"id": post_id, "note": "朋友圈记录已移入回收状态。"})


def _inline(text: str) -> str:
    value = html.escape(text)
    value = re.sub(r"\*\*(.+?)\*\*", r"<strong style=\"color:#111827\">\1</strong>", value)
    return value


def render_official_html(markdown: str) -> str:
    """Render a safe, portable WeChat article fragment with inline styles."""
    lines = str(markdown or "").replace("\r\n", "\n").split("\n")
    out = ['<section style="font-family:-apple-system,BlinkMacSystemFont,Segoe UI,PingFang SC,Microsoft YaHei,sans-serif;color:#262626;font-size:16px;line-height:1.85;letter-spacing:.02em;">']
    list_mode = None
    for raw in lines:
        line = raw.strip()
        if not line:
            if list_mode:
                out.append(f"</{list_mode}>")
                list_mode = None
            continue
        if line.startswith("# "):
            if list_mode:
                out.append(f"</{list_mode}>"); list_mode = None
            out.append(f'<h1 style="margin:18px 0 24px;font-size:28px;line-height:1.35;color:#111827;font-weight:800;">{_inline(line[2:])}</h1>')
        elif line.startswith("## "):
            if list_mode:
                out.append(f"</{list_mode}>"); list_mode = None
            out.append(f'<h2 style="margin:34px 0 14px;padding-left:12px;border-left:4px solid #ef5a29;font-size:21px;line-height:1.5;color:#111827;font-weight:750;">{_inline(line[3:])}</h2>')
        elif line.startswith("> "):
            if list_mode:
                out.append(f"</{list_mode}>"); list_mode = None
            out.append(f'<blockquote style="margin:20px 0;padding:16px 18px;background:#f7f7f5;border-radius:8px;color:#555;">{_inline(line[2:])}</blockquote>')
        elif re.match(r"^\d+\.\s+", line):
            if list_mode != "ol":
                if list_mode: out.append(f"</{list_mode}>")
                out.append('<ol style="margin:14px 0;padding-left:26px;">'); list_mode = "ol"
            out.append(f'<li style="margin:8px 0;">{_inline(re.sub(r"^\d+\.\s+", "", line))}</li>')
        elif line.startswith("- "):
            if list_mode != "ul":
                if list_mode: out.append(f"</{list_mode}>")
                out.append('<ul style="margin:14px 0;padding-left:24px;">'); list_mode = "ul"
            out.append(f'<li style="margin:8px 0;">{_inline(line[2:])}</li>')
        else:
            if list_mode:
                out.append(f"</{list_mode}>"); list_mode = None
            out.append(f'<p style="margin:12px 0;text-align:justify;">{_inline(line)}</p>')
    if list_mode:
        out.append(f"</{list_mode}>")
    out.append('</section>')
    return "".join(out)


@router.post("/channels/official/generate")
def generate_official(body: GenerateIn):
    campaign = _campaign(body.campaign_id, body.project_id)
    context = _source_context(campaign, body.project_id, body.draft_version_id)
    result = get_llm_provider().generate_official_article({**context, "instruction": body.instruction})
    markdown = str(result.get("markdown") or "").strip()
    if not markdown:
        raise validation_failed("公众号正文为空。")
    stamp = now()
    with transaction():
        did = _deliverable(campaign, "wechat_official", "official_article",
                           str(result.get("title") or campaign["title"]), context,
                           {"provider": result.get("provider"), "model": result.get("model")})
        article_id = new_id("OA")
        version_id = new_id("OAV")
        title = str(result.get("title") or campaign["title"]).strip()
        digest = str(result.get("digest") or "").strip()
        execute("INSERT INTO official_articles(id,user_id,deliverable_id,current_version_id,title,digest,author,status,"
                "review_status,layout_key,cover_media_id,content_source_url,remote_media_id,remote_publish_id,published_url,"
                "created_at,updated_at,deleted_at) VALUES(?,?,?,?,?,?,?,'DRAFT','NOT_REVIEWED','jianzhou_clean',NULL,'',NULL,NULL,NULL,?,?,NULL)",
                (article_id, current_user_id(), did, version_id, title, digest,
                 get_setting("wechat_official", "default_author", "见舟AI"), stamp, stamp))
        execute("INSERT INTO official_article_versions(id,user_id,article_id,version_no,title,digest,markdown_content,"
                "html_content,outline_json,change_note,source_hash,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                (version_id, current_user_id(), article_id, 1, title, digest, markdown,
                 render_official_html(markdown), jdumps(result.get("outline") or []),
                 "由内容母题生成初稿", _hash(markdown), stamp))
        evidence(current_user_id(), "official_article", article_id, "article_generated",
                 refs=[campaign["id"], context.get("draft_version_id")],
                 payload={"version_id": version_id, "provider": result.get("provider")})
        _sync_channel_task(did, "PRODUCING", platform="公众号",
                           delivery_kind="official_article", title=title)
    return ok({"id": article_id, "deliverable_id": did, "version_id": version_id,
               "note": "公众号长文已作为独立成品和版本存档；尚未上传草稿箱。"})


def _article_campaign_id(row: dict) -> str:
    deliverable = query_one(
        "SELECT campaign_id FROM channel_deliverables WHERE id=? AND user_id=? AND deleted_at IS NULL",
        (row.get("deliverable_id"), current_user_id()),
    )
    return str((deliverable or {}).get("campaign_id") or "")


def _official_visual_covers(row: dict) -> list[dict]:
    """Return usable covers from the same content campaign as the article."""
    campaign_id = _article_campaign_id(row)
    return _campaign_visual_covers(campaign_id, [row.get("cover_project_id")] if row.get("cover_project_id") else [])


def _bound_official_cover(row: dict) -> dict | None:
    cover_id = str(row.get("cover_project_id") or "")
    if not cover_id:
        return None
    campaign_id = _article_campaign_id(row)
    cover = query_one(
        "SELECT cp.* FROM cover_projects cp "
        "JOIN creation_projects p ON p.id=cp.creation_project_id AND p.user_id=cp.user_id "
        "WHERE cp.id=? AND cp.user_id=? AND cp.deleted_at IS NULL AND p.campaign_id=? "
        "AND cp.content_type='cover'",
        (cover_id, current_user_id(), campaign_id),
    )
    if not cover:
        raise state_invalid("当前文章绑定的视觉封面已不存在，或已不属于同一内容母题。请重新选择封面。")
    path = Path(str(cover.get("preview_path") or ""))
    if not path.exists() or not path.is_file():
        raise state_invalid("当前文章绑定的视觉封面文件不存在。请在视觉工厂重新生成或重新选择。")
    cover["path"] = path
    return cover


def _article_payload(row: dict) -> dict:
    version = query_one("SELECT * FROM official_article_versions WHERE id=? AND user_id=?",
                        (row.get("current_version_id"), current_user_id())) or {}
    review = query_one("SELECT * FROM official_article_reviews WHERE article_id=? AND user_id=? "
                       "ORDER BY created_at DESC LIMIT 1", (row["id"], current_user_id()))
    if review:
        review["issues"] = jloads(review.get("issues_json"), [])
        review["checks"] = jloads(review.get("checks_json"), {})
    covers = _official_visual_covers(row)
    bound = next((item for item in covers if item["bound"]), None)
    return {**row, "version": {**version, "outline": jloads(version.get("outline_json"), [])},
            "review": review, "visual_covers": covers,
            "cover_preview_url": (bound or {}).get("preview_url") or ""}


@router.get("/channels/official")
def list_official(status: str = "all", limit: int = 80):
    rows = query("SELECT a.*,d.campaign_id,c.title campaign_title FROM official_articles a "
                 "JOIN channel_deliverables d ON d.id=a.deliverable_id "
                 "JOIN content_campaigns c ON c.id=d.campaign_id WHERE a.user_id=? AND a.deleted_at IS NULL "
                 "AND d.deleted_at IS NULL AND (?='all' OR a.status=?) ORDER BY a.updated_at DESC LIMIT ?",
                 (current_user_id(), status, status, max(1, min(limit, 200))))
    return ok(rows)


@router.get("/channels/official/{article_id}")
def official_detail(article_id: str):
    return ok(_article_payload(_owned("official_articles", article_id, "公众号文章")))


@router.post("/channels/official/{article_id}/visual-cover/{cover_id}")
def bind_official_visual_cover(article_id: str, cover_id: str):
    article = _owned("official_articles", article_id, "公众号文章")
    campaign_id = _article_campaign_id(article)
    cover = query_one(
        "SELECT cp.* FROM cover_projects cp "
        "JOIN creation_projects p ON p.id=cp.creation_project_id AND p.user_id=cp.user_id "
        "WHERE cp.id=? AND cp.user_id=? AND cp.deleted_at IS NULL AND p.campaign_id=? "
        "AND cp.content_type='cover'",
        (cover_id, current_user_id(), campaign_id),
    )
    if not cover:
        raise validation_failed("只能选择当前文章同一内容母题下、仍然存在的封面成品。")
    path = Path(str(cover.get("preview_path") or ""))
    if not path.exists() or not path.is_file():
        raise state_invalid("该封面的本地成品文件不存在，请在视觉工厂重新生成。")
    stamp = now()
    with transaction():
        execute(
            "UPDATE official_articles SET cover_project_id=?,cover_media_id=NULL,updated_at=? "
            "WHERE id=? AND user_id=?",
            (cover_id, stamp, article_id, current_user_id()),
        )
        evidence(
            current_user_id(), "official_article", article_id, "official_visual_cover_bound",
            refs=[cover_id, cover.get("creation_project_id"), campaign_id],
            payload={"cover_project_id": cover_id, "ratio": cover.get("ratio")},
        )
    return ok({"id": article_id, "cover_project_id": cover_id,
               "cover_preview_url": f"/api/visuals/covers/{cover_id}/preview",
               "note": "已绑定本篇公众号封面；只有上传草稿箱时才会调用微信素材接口。"})


@router.delete("/channels/official/{article_id}/visual-cover")
def unbind_official_visual_cover(article_id: str):
    article = _owned("official_articles", article_id, "公众号文章")
    old_cover_id = str(article.get("cover_project_id") or "")
    with transaction():
        execute(
            "UPDATE official_articles SET cover_project_id=NULL,cover_media_id=NULL,updated_at=? "
            "WHERE id=? AND user_id=?",
            (now(), article_id, current_user_id()),
        )
        evidence(
            current_user_id(), "official_article", article_id, "official_visual_cover_unbound",
            refs=[old_cover_id] if old_cover_id else [], payload={},
        )
    return ok({"id": article_id, "note": "已解除本篇封面绑定；历史视觉成品没有被删除。"})


class ArticlePatch(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    digest: str = Field(default="", max_length=500)
    markdown: str = Field(min_length=1, max_length=200000)
    change_note: str = Field(default="人工编辑", max_length=500)
    author: str = Field(default="", max_length=100)
    content_source_url: str = Field(default="", max_length=2048)


@router.put("/channels/official/{article_id}")
def update_official(article_id: str, body: ArticlePatch):
    article = _owned("official_articles", article_id, "公众号文章")
    latest = query_one("SELECT MAX(version_no) n FROM official_article_versions WHERE article_id=?",
                       (article_id,))
    version_no = int((latest or {}).get("n") or 0) + 1
    version_id = new_id("OAV")
    stamp = now()
    execute("INSERT INTO official_article_versions(id,user_id,article_id,version_no,title,digest,markdown_content,"
            "html_content,outline_json,change_note,source_hash,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
            (version_id, current_user_id(), article_id, version_no, body.title.strip(), body.digest.strip(),
             body.markdown.strip(), render_official_html(body.markdown), "[]", body.change_note,
             _hash(body.markdown), stamp))
    execute("UPDATE official_articles SET current_version_id=?,title=?,digest=?,author=?,content_source_url=?,"
            "status='DRAFT',review_status='NOT_REVIEWED',updated_at=? WHERE id=?",
            (version_id, body.title.strip(), body.digest.strip(), body.author or article.get("author") or "",
             body.content_source_url, stamp, article_id))
    execute("UPDATE channel_deliverables SET status='DRAFT',title=?,updated_at=? WHERE id=?",
            (body.title.strip(), stamp, article["deliverable_id"]))
    _sync_channel_task(article["deliverable_id"], "PRODUCING", platform="公众号",
                       delivery_kind="official_article", title=body.title.strip())
    return ok({"id": article_id, "version_id": version_id, "version_no": version_no,
               "note": "已保存为新版本，旧版本仍可追溯。"})


@router.post("/channels/official/{article_id}/review")
def review_official(article_id: str):
    article = _article_payload(_owned("official_articles", article_id, "公众号文章"))
    version = article["version"]
    result = get_llm_provider().review_official_article({
        "title": version.get("title"), "digest": version.get("digest"),
        "markdown": version.get("markdown_content"), "html": version.get("html_content"),
    })
    review_id = new_id("OAR")
    status = "PASS" if str(result.get("status")) == "PASS" else "NEEDS_REVISION"
    execute("INSERT INTO official_article_reviews(id,user_id,article_id,version_id,status,score,issues_json,checks_json,"
            "provider,model,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (review_id, current_user_id(), article_id, version["id"], status,
             float(result.get("score") or 0), jdumps(result.get("issues") or []),
             jdumps(result.get("checks") or {}), result.get("provider") or "",
             result.get("model") or "", now()))
    article_status = "READY" if status == "PASS" else "REVIEW_REQUIRED"
    execute("UPDATE official_articles SET review_status=?,status=?,updated_at=? WHERE id=?",
            (status, article_status, now(), article_id))
    execute("UPDATE channel_deliverables SET status=?,updated_at=? WHERE id=?",
            (article_status, now(), article["deliverable_id"]))
    _sync_channel_task(article["deliverable_id"],
                       "READY_TO_PUBLISH" if status == "PASS" else "PRODUCING",
                       platform="公众号", delivery_kind="official_article",
                       title=article["title"])
    return ok({"id": review_id, "status": status, "score": result.get("score"),
               "issues": result.get("issues") or [], "checks": result.get("checks") or {}})


@router.get("/channels/official/{article_id}/preview", response_class=HTMLResponse)
def preview_official(article_id: str):
    article = _article_payload(_owned("official_articles", article_id, "公众号文章"))
    title = html.escape(article["title"])
    return HTMLResponse(f'<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{title}</title><body style="margin:0;background:#f5f5f3"><main style="max-width:720px;margin:0 auto;padding:36px 24px;background:white;min-height:100vh">{article["version"].get("html_content") or ""}</main></body></html>')


@router.get("/channels/official/{article_id}/export")
def export_official(article_id: str):
    article = _article_payload(_owned("official_articles", article_id, "公众号文章"))
    return ok({"id": article_id, "title": article["title"],
               "html": article["version"].get("html_content") or "",
               "markdown": article["version"].get("markdown_content") or "",
               "note": "可复制 HTML 到公众号编辑器；这是无接口权限时的真实兜底，不会伪装上传成功。"})


@router.get("/channels/wechat/status")
def wechat_status():
    settings = connector_settings()
    last_probe = _latest_wechat_probe(settings)
    return ok({"configured": bool(settings["app_id"] and settings["app_secret"]),
               "enabled": settings["enabled"], "account_name": settings["account_name"],
               "original_id": settings.get("original_id") or "",
               "last_probe": last_probe,
               "requirements": [
                   {"field": "公众号原始ID", "where": "微信公众平台 → 设置与开发 → 公众号设置 → 原始ID（gh_ 开头）"},
                   {"field": "AppID", "where": "微信公众平台 → 设置与开发 → 基本配置 → 开发者ID"},
                   {"field": "AppSecret", "where": "同一页面 → 开发者密码；请直接填入本应用系统设置，不要发到聊天中"},
                   {"field": "IP白名单", "where": "同一页面 → IP白名单；填写运行本应用机器的公网出口IP"},
                   {"field": "账号类型与认证状态", "where": "微信公众平台右上角账号信息/认证详情"},
                   {"field": "默认封面素材media_id", "where": "可在工作台上传封面自动换取，或填已有永久素材media_id"},
               ],
               "boundary": "默认只上传草稿箱；正式发布需要单独启用并显式确认。个人朋友圈始终采用手动发布登记。"})


@router.post("/channels/wechat/test")
def test_wechat():
    result = WeChatOfficialClient().probe()
    settings = connector_settings()
    execute(
        "INSERT INTO wechat_permission_probes(id,user_id,account_key,status,reachable,capabilities_json,"
        "errors_json,egress_ips_json,next_actions_json,checked_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
        (new_id("WPP"), current_user_id(), _hash(settings.get("app_id") or "unconfigured")[:24],
         result.get("status") or "UNKNOWN", 1 if result.get("reachable") else 0,
         jdumps({"booleans": result.get("capabilities") or {},
                 "states": result.get("capability_states") or {}}),
         jdumps(result.get("errors") or []), jdumps(result.get("egress_ips") or []),
         jdumps(result.get("next_actions") or []), now()),
    )
    return ok(result)


@router.post("/channels/wechat/upload-thumb")
async def upload_wechat_thumb(file: UploadFile = File(...)):
    payload = await file.read(10 * 1024 * 1024 + 1)
    if not payload or len(payload) > 10 * 1024 * 1024:
        raise validation_failed("公众号封面必须是 10 MB 以内的 JPG 或 PNG。")
    suffix = ".png" if (file.content_type or "").lower() == "image/png" else ".jpg"
    target_dir = config.UPLOAD_DIR / "wechat" / current_user_id()
    target_dir.mkdir(parents=True, exist_ok=True)
    path = target_dir / f"{new_id('thumb')}{suffix}"
    path.write_bytes(payload)
    result = WeChatOfficialClient().upload_permanent_image(path)
    return ok({"media_id": result.get("media_id"), "url": result.get("url"),
               "note": "封面已上传为公众号永久素材；可设为默认或用于当前文章。"})


class DraftPushIn(BaseModel):
    thumb_media_id: str = Field(default="", max_length=300)


@router.post("/channels/official/{article_id}/push-draft")
def push_official_draft(article_id: str, body: DraftPushIn):
    article = _article_payload(_owned("official_articles", article_id, "公众号文章"))
    if article["review_status"] != "PASS":
        raise state_invalid("公众号文章必须先通过发布前审核，才能上传草稿箱。")
    client = WeChatOfficialClient()
    thumb_media_id = str(body.thumb_media_id or article.get("cover_media_id") or "")
    uploaded_cover = None
    if not thumb_media_id and article.get("cover_project_id"):
        uploaded_cover = _bound_official_cover(article)
        material = client.upload_permanent_image(uploaded_cover["path"])
        thumb_media_id = str(material.get("media_id") or "")
        if not thumb_media_id:
            raise state_invalid("微信已响应封面素材上传，但没有返回 media_id；本次没有继续创建草稿。")
        execute(
            "UPDATE official_articles SET cover_media_id=?,updated_at=? WHERE id=? AND user_id=?",
            (thumb_media_id, now(), article_id, current_user_id()),
        )
        evidence(
            current_user_id(), "official_article", article_id, "official_cover_material_uploaded",
            refs=[uploaded_cover["id"]],
            payload={"cover_project_id": uploaded_cover["id"], "media_id": thumb_media_id},
        )
    result = client.add_draft({
        "title": article["title"], "author": article.get("author"), "digest": article.get("digest"),
        "content": article["version"].get("html_content"),
        "content_source_url": article.get("content_source_url"),
        "thumb_media_id": thumb_media_id,
    })
    media_id = str(result.get("media_id") or "")
    record_id = new_id("CPR")
    stamp = now()
    with transaction():
        execute("UPDATE official_articles SET status='IN_DRAFT_BOX',remote_media_id=?,cover_media_id=COALESCE(NULLIF(?,''),cover_media_id),updated_at=? WHERE id=?",
                (media_id, thumb_media_id, stamp, article_id))
        execute("UPDATE channel_deliverables SET status='IN_DRAFT_BOX',updated_at=? WHERE id=?",
                (stamp, article["deliverable_id"]))
        execute("INSERT INTO channel_publish_records(id,user_id,deliverable_id,channel,mode,status,remote_id,url,request_json,response_json,error_json,published_at,created_at,updated_at,subject_type,subject_id) "
                "VALUES(?,?,?,?,?,'IN_DRAFT_BOX',?,NULL,?,?, '{}',NULL,?,?,?,?)",
                (record_id, current_user_id(), article["deliverable_id"], "wechat_official", "api_draft",
                 media_id, jdumps({"article_id": article_id,
                                   "cover_project_id": article.get("cover_project_id"),
                                   "thumb_media_id": thumb_media_id,
                                   "cover_uploaded_now": bool(uploaded_cover)}),
                 jdumps(result), stamp, stamp,
                 "official_article", article_id))
    return ok({"media_id": media_id, "record_id": record_id,
               "note": "文章已进入公众号草稿箱，尚未正式发布。请在公众号后台预览确认。"})


class OfficialPublishIn(BaseModel):
    confirm: bool = False


@router.post("/channels/official/{article_id}/publish")
def publish_official(article_id: str, body: OfficialPublishIn):
    if not body.confirm:
        raise validation_failed("正式发布需要显式确认。")
    article = _article_payload(_owned("official_articles", article_id, "公众号文章"))
    if article["status"] != "IN_DRAFT_BOX" or not article.get("remote_media_id"):
        raise state_invalid("文章尚未进入公众号草稿箱。")
    result = WeChatOfficialClient().publish(article["remote_media_id"])
    publish_id = str(result.get("publish_id") or "")
    if not publish_id:
        raise state_invalid("微信已响应，但没有返回 publish_id；本次没有伪造发布成功记录。")
    record_id = new_id("CPR")
    stamp = now()
    with transaction():
        execute("UPDATE official_articles SET status='PUBLISHING',remote_publish_id=?,updated_at=? WHERE id=?",
                (publish_id, stamp, article_id))
        execute("UPDATE channel_deliverables SET status='PUBLISHING',updated_at=? WHERE id=?",
                (stamp, article["deliverable_id"]))
        execute("INSERT INTO channel_publish_records(id,user_id,deliverable_id,channel,mode,status,remote_id,url,"
                "request_json,response_json,error_json,published_at,created_at,updated_at,subject_type,subject_id) "
                "VALUES(?,?,?,?,?,'PUBLISHING',?,NULL,?,?, '{}',NULL,?,?,?,?)",
                (record_id, current_user_id(), article["deliverable_id"], "wechat_official", "api_publish",
                 publish_id, jdumps({"article_id": article_id, "media_id": article["remote_media_id"]}),
                 jdumps(result), stamp, stamp, "official_article", article_id))
    return ok({"publish_id": publish_id, "record_id": record_id,
               "note": "微信已接收发布任务；仍需同步最终状态与文章链接。"})


@router.post("/channels/official/{article_id}/sync-status")
def sync_official_status(article_id: str):
    article = _article_payload(_owned("official_articles", article_id, "公众号文章"))
    if not article.get("remote_publish_id"):
        raise state_invalid("当前文章没有微信发布任务。")
    result = WeChatOfficialClient().publish_status(article["remote_publish_id"])
    status_code = int(result.get("publish_status") or 0)
    articles = result.get("article_detail", {}).get("item") or []
    url = str((articles[0] if articles else {}).get("article_url") or "")
    status_map = {0: "PUBLISHED", 1: "PUBLISHING", 2: "FAILED", 3: "FAILED", 4: "FAILED",
                  5: "REMOVED", 6: "BLOCKED"}
    record_status = status_map.get(status_code, "PUBLISHING")
    article_status = {"FAILED": "PUBLISH_FAILED", "REMOVED": "REMOVED", "BLOCKED": "BLOCKED"}.get(
        record_status, record_status)
    if status_code == 0 and not url:
        record_status = article_status = "PUBLISHING"
    stamp = now()
    record = query_one(
        "SELECT * FROM channel_publish_records WHERE user_id=? AND subject_type='official_article' "
        "AND subject_id=? AND mode='api_publish' AND remote_id=? ORDER BY created_at DESC LIMIT 1",
        (current_user_id(), article_id, article["remote_publish_id"]),
    )
    with transaction():
        if record:
            execute("UPDATE channel_publish_records SET status=?,url=?,response_json=?,error_json=?,"
                    "published_at=?,updated_at=? WHERE id=?",
                    (record_status, url or None, jdumps(result),
                     jdumps(result if record_status == "FAILED" else {}),
                     stamp if record_status == "PUBLISHED" else None, stamp, record["id"]))
        if record_status == "PUBLISHED":
            execute("UPDATE official_articles SET status='PUBLISHED',published_url=?,updated_at=? WHERE id=?",
                    (url, stamp, article_id))
            execute("UPDATE channel_deliverables SET status='PUBLISHED',published_at=?,updated_at=? WHERE id=?",
                    (stamp, stamp, article["deliverable_id"]))
            _sync_channel_task(article["deliverable_id"], "PUBLISHED", platform="公众号",
                               delivery_kind="official_article", title=article["title"])
        elif article_status != "PUBLISHING":
            execute("UPDATE official_articles SET status=?,updated_at=? WHERE id=?",
                    (article_status, stamp, article_id))
            execute("UPDATE channel_deliverables SET status=?,updated_at=? WHERE id=?",
                    (article_status, stamp, article["deliverable_id"]))
    notes = {"PUBLISHED": "正式发布成功，文章链接和发布证据已存档。",
             "PUBLISHING": "微信仍在处理发布任务。",
             "FAILED": "微信返回发布失败，已保留原始错误证据。",
             "REMOVED": "文章曾发布成功，但随后被用户删除。",
             "BLOCKED": "文章曾发布成功，但随后被平台封禁。"}
    return ok({"status_code": status_code, "status": record_status, "url": url,
               "raw": result, "note": notes.get(record_status, "已同步微信发布状态。")})


@router.post("/channels/official/{article_id}/publish-manual")
def publish_official_manual(article_id: str, body: ManualPublishIn):
    article = _article_payload(_owned("official_articles", article_id, "公众号文章"))
    if not body.url.strip():
        raise validation_failed("手动发布登记需要填写公众号文章链接。")
    stamp = body.published_at or now()
    record_id = new_id("CPR")
    with transaction():
        execute("UPDATE official_articles SET status='PUBLISHED',published_url=?,updated_at=? WHERE id=?",
                (body.url.strip(), now(), article_id))
        execute("UPDATE channel_deliverables SET status='PUBLISHED',published_at=?,updated_at=? WHERE id=?",
                (stamp, now(), article["deliverable_id"]))
        execute("INSERT INTO channel_publish_records(id,user_id,deliverable_id,channel,mode,status,remote_id,url,request_json,response_json,error_json,published_at,created_at,updated_at,subject_type,subject_id) "
                "VALUES(?,?,?,?,?,'PUBLISHED',NULL,?,'{}','{}','{}',?,?,?,?,?)",
                (record_id, current_user_id(), article["deliverable_id"], "wechat_official", "manual",
                 body.url.strip(), stamp, now(), now(), "official_article", article_id))
        _sync_channel_task(article["deliverable_id"], "PUBLISHED", platform="公众号",
                           delivery_kind="official_article", title=article["title"])
    return ok({"record_id": record_id, "note": "公众号文章已按真实链接登记发布，可进入数据回收与复盘。"})


@router.delete("/channels/official/{article_id}")
def delete_official(article_id: str):
    article = _owned("official_articles", article_id, "公众号文章")
    stamp = now()
    execute("UPDATE official_articles SET deleted_at=?,updated_at=? WHERE id=?", (stamp, stamp, article_id))
    execute("UPDATE channel_deliverables SET deleted_at=?,updated_at=? WHERE id=?",
            (stamp, stamp, article["deliverable_id"]))
    execute("UPDATE content_tasks SET status='CANCELLED' WHERE user_id=? AND deliverable_id=?",
            (current_user_id(), article["deliverable_id"]))
    return ok({"id": article_id, "note": "公众号文档已移入回收状态，历史版本未被物理覆盖。"})
