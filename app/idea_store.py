"""Persistent first-party inspiration and reflection records.

Ideas are evidence, not generated copy.  They may be reused by creation,
Moments and campaigns without copying or silently rewriting the original text.
"""
from __future__ import annotations

import datetime

from .db import execute, jdumps, jloads, query_one
from .ids import new_id
from .repositories import evidence


IDEA_TYPES = {
    "self_idea", "life_note", "work_process", "hot_opinion",
    "feedback", "trust_evidence",
}


def _now() -> str:
    return datetime.datetime.now().isoformat(timespec="seconds")


def normalize_type(value: str | None) -> str:
    item_type = str(value or "self_idea").strip()
    return item_type if item_type in IDEA_TYPES else "self_idea"


def public_idea(row: dict | None) -> dict | None:
    if not row:
        return None
    media = []
    for item in jloads(row.get("media_json"), []):
        if not isinstance(item, dict):
            continue
        # Never expose the local absolute path to the browser.  The authenticated
        # attachment endpoint remains the only way to read a first-party asset.
        media.append({key: value for key, value in item.items() if key != "path"} | {
            "url": f"/api/ideas/{row['id']}/attachments/{item.get('id')}",
        })
    return {
        "id": row["id"], "item_type": row.get("item_type") or "self_idea",
        "title": row.get("title") or "", "body": row.get("body") or "",
        "personal_judgement": row.get("personal_judgement") or "",
        "source_kind": row.get("source_kind") or "manual",
        "source_url": row.get("source_url") or "",
        "media": media,
        "tags": jloads(row.get("tags_json"), []),
        "privacy_level": row.get("privacy_level") or "private",
        "status": row.get("status") or "ACTIVE",
        "created_at": row.get("created_at"), "updated_at": row.get("updated_at"),
    }


def get_owned(user_id: str, idea_id: str) -> dict | None:
    return query_one(
        "SELECT * FROM content_ideas WHERE id=? AND user_id=? AND deleted_at IS NULL",
        (idea_id, user_id),
    )


def create_idea(user_id: str, *, body: str, title: str = "",
                item_type: str = "self_idea", personal_judgement: str = "",
                source_kind: str = "manual", source_url: str = "",
                tags: list | None = None, media: list | None = None,
                privacy_level: str = "private") -> dict:
    text = str(body or "").strip()
    if not text:
        raise ValueError("灵感或感悟正文不能为空")
    idea_id = new_id("IDEA")
    stamp = _now()
    execute(
        "INSERT INTO content_ideas(id,user_id,item_type,title,body,personal_judgement,"
        "source_kind,source_url,media_json,tags_json,privacy_level,status,created_at,updated_at,deleted_at) "
        "VALUES(?,?,?,?,?,?,?,?,?,?,?,'ACTIVE',?,?,NULL)",
        (idea_id, user_id, normalize_type(item_type), str(title or "").strip()[:200],
         text, str(personal_judgement or "").strip(), str(source_kind or "manual")[:50],
         str(source_url or "").strip()[:2048], jdumps(media or []), jdumps(tags or []),
         "private" if privacy_level not in {"private", "shareable"} else privacy_level,
         stamp, stamp),
    )
    evidence(user_id, "content_idea", idea_id, "idea_recorded", refs=[],
             payload={"item_type": normalize_type(item_type), "source_kind": source_kind})
    return get_owned(user_id, idea_id)


def link_campaign(user_id: str, campaign_id: str, idea_id: str,
                  role: str = "source") -> None:
    idea = get_owned(user_id, idea_id)
    campaign = query_one("SELECT id FROM content_campaigns WHERE id=? AND user_id=?",
                         (campaign_id, user_id))
    if not idea or not campaign:
        return
    execute(
        "INSERT OR IGNORE INTO campaign_idea_links(campaign_id,idea_id,role,created_at) "
        "VALUES(?,?,?,?)", (campaign_id, idea_id, str(role or "source")[:40], _now()),
    )
