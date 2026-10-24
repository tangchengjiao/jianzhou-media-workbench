"""Persistence helpers for public works belonging to the user's own accounts."""
from __future__ import annotations

import hashlib
import urllib.parse

from .db import execute, query_one, transaction
from .text_quality import repair_text_tree


def account_work_id(account_id: str, video_id: str) -> str:
    digest = hashlib.sha256(f"{account_id}\0{video_id}".encode("utf-8")).hexdigest()[:24]
    return f"OW-{digest}"


def canonical_work_url(platform: str, video_id: str, supplied: str = "") -> str:
    parsed = urllib.parse.urlparse(supplied.strip()) if supplied else None
    if parsed and parsed.scheme == "https" and parsed.netloc:
        return supplied
    builders = {
        "douyin": lambda: f"https://www.douyin.com/video/{video_id}",
        "xiaohongshu": lambda: f"https://www.xiaohongshu.com/explore/{video_id}",
        "bilibili": lambda: f"https://www.bilibili.com/video/{video_id}",
        "kuaishou": lambda: f"https://www.kuaishou.com/short-video/{video_id}",
    }
    build = builders.get(platform)
    return build() if build else ""


def persist_account_works(account_id: str, works: list[dict], seen_at: str) -> dict:
    created = 0
    updated = 0
    account = query_one("SELECT platform FROM accounts WHERE id=?", (account_id,)) or {}
    platform = str(account.get("platform") or "")
    with transaction():
        for raw in works:
            work = repair_text_tree(raw)
            video_id = str(work.get("video_id") or "").strip()
            if not video_id:
                continue
            wid = account_work_id(account_id, video_id)
            exists = query_one("SELECT * FROM account_works WHERE id=?", (wid,))
            title = str(work.get("title") or "").strip()
            cover = str(work.get("cover") or "").strip()
            if platform == "channels" and cover.startswith("http://wxapp.tc.qq.com/"):
                cover = "https://" + cover.removeprefix("http://")
            url = canonical_work_url(platform, video_id, str(work.get("url") or ""))
            if exists:
                title = title or str(exists.get("title") or "")
                cover = cover or str(exists.get("cover") or "")
                url = url or str(exists.get("url") or "")
            values = (
                title or "平台未返回标题", cover, url,
                int(work.get("create_time") or 0),
                int(work.get("plays") or 0), int(work.get("likes") or 0),
                int(work.get("comments") or 0), int(work.get("collects") or 0),
                int(work.get("shares") or 0), int(work.get("duration") or 0),
            )
            if exists:
                execute(
                    "UPDATE account_works SET title=?,cover=?,url=?,create_time=?,plays=?,likes=?,"
                    "comments=?,collects=?,shares=?,duration=?,last_seen_at=? WHERE id=?",
                    (*values, seen_at, wid),
                )
                updated += 1
            else:
                execute(
                    "INSERT INTO account_works(id,account_id,video_id,title,cover,url,create_time,"
                    "plays,likes,comments,collects,shares,duration,first_seen_at,last_seen_at) "
                    "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (wid, account_id, video_id, *values, seen_at, seen_at),
                )
                created += 1
    return {"created": created, "updated": updated}
