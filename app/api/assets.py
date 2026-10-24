"""Unified asset center: search, lineage, collections and reusable actions."""
from fastapi import APIRouter, Query
from pydantic import BaseModel, Field

from ..asset_center import ASSET_LABELS, asset_content, sync_asset_index
from ..db import execute, jdumps, jloads, query, query_one
from ..errors import not_found
from ..ids import new_id
from . import current_user_id, now, ok

router = APIRouter(prefix="/assets", tags=["assets"])


def _row(record: dict) -> dict:
    record["metadata"] = jloads(record.pop("metadata_json", None), {})
    record["type_label"] = ASSET_LABELS.get(record["asset_type"], record["asset_type"])
    return record


@router.post("/sync")
def sync_assets():
    result = sync_asset_index(current_user_id())
    return ok({**result, "note": "资产索引和来源关系已刷新；原始数据未被复制或覆盖。"})


@router.get("/summary")
def summary():
    uid = current_user_id()
    sync_asset_index(uid)
    counts = {row["asset_type"]: row["n"] for row in query(
        "SELECT asset_type,COUNT(*) n FROM asset_records WHERE user_id=? AND deleted_at IS NULL "
        "GROUP BY asset_type", (uid,)
    )}
    total = sum(counts.values())
    sources = query_one("SELECT COUNT(*) n FROM asset_records WHERE user_id=? AND deleted_at IS NULL "
                        "AND asset_type IN ('own_work','signal','source')", (uid,))["n"]
    reusable = query_one("SELECT COUNT(*) n FROM asset_records WHERE user_id=? AND deleted_at IS NULL "
                         "AND asset_type IN ('idea','transcript','clean_text','analysis','pattern','decision','draft',"
                         "'campaign','channel_deliverable','moment','official_article','channel_retro','visual_batch',"
                         "'visual','visual_page','visual_deliverable')", (uid,))["n"]
    unlinked_works = query_one(
        "SELECT COUNT(*) n FROM account_works w JOIN accounts a ON a.id=w.account_id "
        "LEFT JOIN account_work_source_links l ON l.account_work_id=w.id "
        "WHERE a.user_id=? AND l.account_work_id IS NULL", (uid,)
    )["n"]
    return ok({"total": total, "sources": sources, "reusable": reusable,
               "unlinked_works": unlinked_works, "counts": counts})


@router.get("")
def list_assets(q: str = "", asset_type: str = "all", platform: str = "all",
                source_kind: str = "all", favorite: bool = False,
                sort: str = "updated", limit: int = Query(default=80, ge=1, le=200),
                offset: int = Query(default=0, ge=0)):
    uid = current_user_id()
    where = ["user_id=?", "deleted_at IS NULL"]
    params: list = [uid]
    if q.strip():
        where.append("(title LIKE ? OR summary LIKE ? OR search_text LIKE ?)")
        needle = f"%{q.strip()}%"
        params.extend([needle, needle, needle])
    if asset_type != "all":
        types = [item.strip() for item in asset_type.split(",") if item.strip()]
        if types:
            where.append(f"asset_type IN ({','.join('?' for _ in types)})")
            params.extend(types)
    if platform != "all":
        where.append("platform=?")
        params.append(platform)
    if source_kind != "all":
        where.append("source_kind=?")
        params.append(source_kind)
    if favorite:
        where.append("favorite=1")
    order = {
        "created": "created_at DESC", "title": "title COLLATE NOCASE",
        "favorite": "favorite DESC,updated_at DESC", "updated": "updated_at DESC",
    }.get(sort, "updated_at DESC")
    sql_where = " AND ".join(where)
    rows = query(f"SELECT * FROM asset_records WHERE {sql_where} ORDER BY {order} LIMIT ? OFFSET ?",
                 tuple([*params, limit, offset]))
    total = query_one(f"SELECT COUNT(*) n FROM asset_records WHERE {sql_where}", tuple(params))["n"]
    return ok({"items": [_row(row) for row in rows], "total": total, "limit": limit, "offset": offset})


@router.get("/facets")
def facets():
    uid = current_user_id()
    platforms = [row["platform"] for row in query(
        "SELECT DISTINCT platform FROM asset_records WHERE user_id=? AND deleted_at IS NULL "
        "AND platform<>'' ORDER BY platform", (uid,)
    )]
    sources = [row["source_kind"] for row in query(
        "SELECT DISTINCT source_kind FROM asset_records WHERE user_id=? AND deleted_at IS NULL "
        "AND source_kind<>'' ORDER BY source_kind", (uid,)
    )]
    return ok({"types": [{"id": key, "label": value} for key, value in ASSET_LABELS.items()],
               "platforms": platforms, "sources": sources})


@router.get("/{record_id}")
def detail(record_id: str):
    uid = current_user_id()
    record = query_one("SELECT * FROM asset_records WHERE id=? AND user_id=? AND deleted_at IS NULL",
                       (record_id, uid))
    if not record:
        raise not_found("资产", record_id)
    relations = query(
        "SELECT r.relation_type,r.from_asset_id,r.to_asset_id,a.id,a.asset_type,a.title,a.platform,a.summary "
        "FROM asset_relations r JOIN asset_records a ON a.id=CASE WHEN r.from_asset_id=? "
        "THEN r.to_asset_id ELSE r.from_asset_id END WHERE r.user_id=? "
        "AND (r.from_asset_id=? OR r.to_asset_id=?) AND a.deleted_at IS NULL ORDER BY r.created_at",
        (record_id, uid, record_id, record_id),
    )
    collections = query(
        "SELECT c.id,c.name FROM asset_collection_items i JOIN asset_collections c ON c.id=i.collection_id "
        "WHERE i.asset_id=? AND c.user_id=? ORDER BY c.name", (record_id, uid)
    )
    return ok({"asset": _row(record), "content": asset_content(record),
               "relations": relations, "collections": collections})


@router.post("/{record_id}/favorite")
def toggle_favorite(record_id: str):
    uid = current_user_id()
    row = query_one("SELECT favorite FROM asset_records WHERE id=? AND user_id=?", (record_id, uid))
    if not row:
        raise not_found("资产", record_id)
    value = 0 if row["favorite"] else 1
    execute("UPDATE asset_records SET favorite=?,updated_at=? WHERE id=? AND user_id=?",
            (value, now(), record_id, uid))
    return ok({"favorite": bool(value)})


class CollectionIn(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    description: str = Field(default="", max_length=500)


@router.get("/collections/list")
def list_collections():
    uid = current_user_id()
    rows = query(
        "SELECT c.*,COUNT(i.asset_id) item_count FROM asset_collections c "
        "LEFT JOIN asset_collection_items i ON i.collection_id=c.id WHERE c.user_id=? "
        "GROUP BY c.id ORDER BY c.updated_at DESC", (uid,)
    )
    return ok(rows)


@router.post("/collections")
def create_collection(body: CollectionIn):
    uid = current_user_id()
    existing = query_one("SELECT id FROM asset_collections WHERE user_id=? AND name=?", (uid, body.name.strip()))
    if existing:
        return ok({"id": existing["id"], "existing": True})
    collection_id = new_id("AC")
    execute("INSERT INTO asset_collections VALUES(?,?,?,?,?,?)",
            (collection_id, uid, body.name.strip(), body.description.strip(), now(), now()))
    return ok({"id": collection_id, "existing": False})


class AddCollectionItemIn(BaseModel):
    asset_id: str


@router.post("/collections/{collection_id}/items")
def add_collection_item(collection_id: str, body: AddCollectionItemIn):
    uid = current_user_id()
    if not query_one("SELECT id FROM asset_collections WHERE id=? AND user_id=?", (collection_id, uid)):
        raise not_found("资产合集", collection_id)
    if not query_one("SELECT id FROM asset_records WHERE id=? AND user_id=?", (body.asset_id, uid)):
        raise not_found("资产", body.asset_id)
    position = query_one("SELECT COALESCE(MAX(position),0)+1 n FROM asset_collection_items WHERE collection_id=?",
                         (collection_id,))["n"]
    execute("INSERT OR IGNORE INTO asset_collection_items VALUES(?,?,?,?)",
            (collection_id, body.asset_id, position, now()))
    execute("UPDATE asset_collections SET updated_at=? WHERE id=?", (now(), collection_id))
    return ok({"note": "已加入资产合集"})
