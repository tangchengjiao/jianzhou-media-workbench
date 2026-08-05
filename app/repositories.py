"""Ownership-aware repository helpers and append-only evidence events.

The application is local-first, but creator switching still requires strict
object ownership. API modules use these helpers instead of id-only lookups.
"""
from __future__ import annotations

import datetime

from .db import execute, jdumps, query, query_one
from .errors import not_found
from .ids import new_id


OWNER_COLUMNS = {
    "accounts": "user_id",
    "signals": "user_id",
    "source_contents": "user_id",
    "jobs": "user_id",
    "analyses": "user_id",
    "patterns": "user_id",
    "creation_projects": "user_id",
    "content_tasks": "user_id",
    "publications": "user_id",
    "retro_reports": "user_id",
    "rule_suggestions": "user_id",
    "rules": "user_id",
    "corpus_items": "user_id",
    "import_batches": "user_id",
    "benchmark_accounts": "user_id",
    "media_assets": "user_id",
    "context_runs": "user_id",
    "profile_field_candidates": "user_id",
    "interview_sessions": "user_id",
    "data_packages": "owner_id",
}


def owned_row(table: str, rid: str, user_id: str, *, id_column: str = "id",
              label: str = "记录") -> dict:
    owner = OWNER_COLUMNS.get(table)
    if not owner:
        raise ValueError(f"No ownership mapping for table {table}")
    row = query_one(
        f'SELECT * FROM "{table}" WHERE "{id_column}"=? AND "{owner}"=?',
        (rid, user_id),
    )
    if not row:
        # Deliberately return the same 404 for missing and cross-user objects.
        raise not_found(label, rid)
    return row


def owned_children(table: str, user_id: str, *, order_by: str = "") -> list[dict]:
    owner = OWNER_COLUMNS.get(table)
    if not owner:
        raise ValueError(f"No ownership mapping for table {table}")
    suffix = f" ORDER BY {order_by}" if order_by else ""
    return query(f'SELECT * FROM "{table}" WHERE "{owner}"=?{suffix}', (user_id,))


def evidence(user_id: str, entity_type: str, entity_id: str, event_type: str,
             *, refs: list | dict | None = None,
             payload: dict | None = None) -> str:
    event_id = new_id("EV")
    execute(
        "INSERT INTO evidence_events VALUES(?,?,?,?,?,?,?,?)",
        (event_id, user_id, entity_type, entity_id, event_type,
         jdumps(refs or []), jdumps(payload or {}),
         datetime.datetime.now().isoformat(timespec="seconds")),
    )
    return event_id


def evidence_chain(user_id: str, entity_type: str, entity_id: str) -> list[dict]:
    return query(
        "SELECT * FROM evidence_events WHERE user_id=? AND entity_type=? AND entity_id=?"
        " ORDER BY created_at, id",
        (user_id, entity_type, entity_id),
    )
