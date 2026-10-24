"""User-authored inspiration, reflections and first-party evidence."""
from __future__ import annotations

import mimetypes
from pathlib import Path

from fastapi import APIRouter, File, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from .. import config
from ..db import execute, jdumps, jloads, query
from ..errors import not_found, validation_failed
from ..idea_store import create_idea, get_owned, normalize_type, public_idea
from ..ids import new_id
from ..repositories import evidence
from . import current_user_id, now, ok

router = APIRouter(prefix="/ideas", tags=["ideas"])

ALLOWED_MEDIA = {
    "image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp",
    "audio/mpeg": ".mp3", "audio/wav": ".wav", "audio/x-m4a": ".m4a",
    "audio/mp4": ".m4a", "video/mp4": ".mp4",
}
MAX_IDEA_MEDIA_BYTES = 20 * 1024 * 1024


class IdeaIn(BaseModel):
    body: str = Field(min_length=1, max_length=20000)
    title: str = Field(default="", max_length=200)
    item_type: str = Field(default="self_idea", max_length=40)
    personal_judgement: str = Field(default="", max_length=10000)
    source_url: str = Field(default="", max_length=2048)
    tags: list[str] = Field(default_factory=list)
    privacy_level: str = Field(default="private", max_length=20)


class IdeaPatch(BaseModel):
    body: str | None = Field(default=None, max_length=20000)
    title: str | None = Field(default=None, max_length=200)
    item_type: str | None = Field(default=None, max_length=40)
    personal_judgement: str | None = Field(default=None, max_length=10000)
    source_url: str | None = Field(default=None, max_length=2048)
    tags: list[str] | None = None
    privacy_level: str | None = Field(default=None, max_length=20)


@router.get("")
def list_ideas(item_type: str = "all", status: str = "ACTIVE", limit: int = 100):
    limit = max(1, min(limit, 300))
    rows = query(
        "SELECT * FROM content_ideas WHERE user_id=? AND deleted_at IS NULL "
        "AND (?='all' OR item_type=?) AND (?='all' OR status=?) "
        "ORDER BY updated_at DESC,id DESC LIMIT ?",
        (current_user_id(), item_type, item_type, status, status, limit),
    )
    return ok([public_idea(row) for row in rows])


@router.post("")
def add_idea(body: IdeaIn):
    try:
        row = create_idea(
            current_user_id(), body=body.body, title=body.title,
            item_type=body.item_type, personal_judgement=body.personal_judgement,
            source_url=body.source_url, tags=body.tags,
            privacy_level=body.privacy_level,
        )
    except ValueError as exc:
        raise validation_failed(str(exc)) from exc
    return ok({**public_idea(row), "note": "已存入灵感与随手记；原文不会被 AI 静默改写。"})


@router.get("/{idea_id}")
def idea_detail(idea_id: str):
    row = get_owned(current_user_id(), idea_id)
    if not row:
        raise not_found("灵感记录", idea_id)
    return ok(public_idea(row))


@router.put("/{idea_id}")
def edit_idea(idea_id: str, body: IdeaPatch):
    uid = current_user_id()
    row = get_owned(uid, idea_id)
    if not row:
        raise not_found("灵感记录", idea_id)
    values = {
        "body": row["body"] if body.body is None else body.body.strip(),
        "title": (row.get("title") or "") if body.title is None else body.title.strip(),
        "item_type": row.get("item_type") if body.item_type is None else normalize_type(body.item_type),
        "personal_judgement": ((row.get("personal_judgement") or "") if body.personal_judgement is None
                               else body.personal_judgement.strip()),
        "source_url": ((row.get("source_url") or "") if body.source_url is None
                       else body.source_url.strip()),
        "tags_json": (row.get("tags_json") or "[]") if body.tags is None else jdumps(body.tags),
        "privacy_level": ((row.get("privacy_level") or "private") if body.privacy_level is None
                          else body.privacy_level),
    }
    if not values["body"]:
        raise validation_failed("灵感或感悟正文不能为空。")
    if values["privacy_level"] not in {"private", "shareable"}:
        raise validation_failed("隐私级别无效。")
    execute(
        "UPDATE content_ideas SET title=?,body=?,item_type=?,personal_judgement=?,source_url=?,"
        "tags_json=?,privacy_level=?,updated_at=? WHERE id=? AND user_id=?",
        (values["title"], values["body"], values["item_type"],
         values["personal_judgement"], values["source_url"], values["tags_json"],
         values["privacy_level"], now(), idea_id, uid),
    )
    evidence(uid, "content_idea", idea_id, "idea_edited", refs=[], payload={})
    return ok({**public_idea(get_owned(uid, idea_id)), "note": "修改已保存；历史引用仍指向同一条本人记录。"})


@router.delete("/{idea_id}")
def remove_idea(idea_id: str):
    uid = current_user_id()
    if not get_owned(uid, idea_id):
        raise not_found("灵感记录", idea_id)
    execute("UPDATE content_ideas SET status='ARCHIVED',deleted_at=?,updated_at=? WHERE id=? AND user_id=?",
            (now(), now(), idea_id, uid))
    evidence(uid, "content_idea", idea_id, "idea_archived", refs=[], payload={})
    return ok({"id": idea_id, "note": "已移入回收状态；已生成内容的证据链不会被破坏。"})


@router.post("/{idea_id}/attachments")
async def upload_attachment(idea_id: str, file: UploadFile = File(...)):
    uid = current_user_id()
    row = get_owned(uid, idea_id)
    if not row:
        raise not_found("灵感记录", idea_id)
    content_type = str(file.content_type or "").lower()
    suffix = ALLOWED_MEDIA.get(content_type)
    if not suffix:
        guessed = mimetypes.guess_type(file.filename or "")[0] or ""
        suffix = ALLOWED_MEDIA.get(guessed)
        content_type = guessed
    if not suffix:
        raise validation_failed("仅支持 JPG、PNG、WEBP、MP3、WAV、M4A 或 MP4。")
    payload = await file.read(MAX_IDEA_MEDIA_BYTES + 1)
    if not payload or len(payload) > MAX_IDEA_MEDIA_BYTES:
        raise validation_failed("附件不能为空且不能超过 20 MB。")
    folder = config.UPLOAD_DIR / "ideas" / uid
    folder.mkdir(parents=True, exist_ok=True)
    file_id = new_id("MEDIA")
    target = folder / f"{file_id}{suffix}"
    target.write_bytes(payload)
    media = jloads(row.get("media_json"), [])
    media.append({"id": file_id, "name": Path(file.filename or f"attachment{suffix}").name[:240],
                  "content_type": content_type, "size": len(payload), "path": str(target)})
    execute("UPDATE content_ideas SET media_json=?,updated_at=? WHERE id=? AND user_id=?",
            (jdumps(media), now(), idea_id, uid))
    evidence(uid, "content_idea", idea_id, "idea_attachment_added", refs=[file_id],
             payload={"content_type": content_type, "size": len(payload)})
    return ok({"id": file_id, "media": public_idea(get_owned(uid, idea_id))["media"],
               "note": "真实素材已绑定到这条灵感记录。"})


@router.get("/{idea_id}/attachments/{file_id}")
def read_attachment(idea_id: str, file_id: str):
    row = get_owned(current_user_id(), idea_id)
    if not row:
        raise not_found("灵感记录", idea_id)
    item = next((entry for entry in jloads(row.get("media_json"), [])
                 if str(entry.get("id")) == file_id), None)
    path = Path(str((item or {}).get("path") or ""))
    root = (config.UPLOAD_DIR / "ideas" / current_user_id()).resolve()
    resolved = path.resolve()
    if (not item or not resolved.is_relative_to(root)
            or not resolved.exists() or not resolved.is_file()):
        raise not_found("灵感附件", file_id)
    return FileResponse(resolved, media_type=item.get("content_type"), filename=item.get("name"))


@router.delete("/{idea_id}/attachments/{file_id}")
def delete_attachment(idea_id: str, file_id: str):
    uid = current_user_id()
    row = get_owned(uid, idea_id)
    if not row:
        raise not_found("灵感记录", idea_id)
    media = jloads(row.get("media_json"), [])
    item = next((entry for entry in media if str(entry.get("id")) == file_id), None)
    if not item:
        raise not_found("灵感附件", file_id)
    path = Path(str(item.get("path") or ""))
    root = (config.UPLOAD_DIR / "ideas" / uid).resolve()
    resolved = path.resolve()
    if resolved.is_relative_to(root) and resolved.exists() and resolved.is_file():
        resolved.unlink()
    media = [entry for entry in media if str(entry.get("id")) != file_id]
    execute("UPDATE content_ideas SET media_json=?,updated_at=? WHERE id=? AND user_id=?",
            (jdumps(media), now(), idea_id, uid))
    return ok({"id": file_id, "note": "附件已删除，文字记录仍保留。"})
