"""Text quality guards and one-time repair for reversible UTF-8 mojibake.

The most common failure handled here is UTF-8 bytes decoded as Latin-1, for
example ``第7集`` becoming ``ç¬¬7é\x9b\x86``.  Repair is deliberately narrow:
plain ASCII, valid Chinese, and ordinary Latin text are left unchanged.
"""
from __future__ import annotations

import datetime
import json
import re
import sqlite3
from typing import Any


def repair_mojibake(value: str) -> str:
    if not isinstance(value, str) or not value:
        return value
    current = value
    for _ in range(2):
        try:
            candidate = current.encode("latin-1").decode("utf-8")
        except (UnicodeEncodeError, UnicodeDecodeError):
            break
        if candidate == current:
            break
        # A real repair normally removes C1 controls or common UTF-8-as-Latin-1
        # marker characters.  Requiring that improvement avoids changing valid
        # Western-European text that merely happens to be Latin-1 encodable.
        before = _suspicion_score(current)
        after = _suspicion_score(candidate)
        if before <= after or before == 0:
            break
        current = candidate
    return current


def repair_text_tree(value: Any) -> Any:
    if isinstance(value, str):
        return repair_mojibake(value)
    if isinstance(value, list):
        return [repair_text_tree(item) for item in value]
    if isinstance(value, dict):
        return {key: repair_text_tree(item) for key, item in value.items()}
    return value


def _suspicion_score(value: str) -> int:
    c1 = sum(1 for ch in value if "\u0080" <= ch <= "\u009f")
    markers = sum(value.count(mark) for mark in (
        "Ã", "Â", "â€", "â€™", "â€œ", "â€\u009d", "ðŸ", "ï¼", "ã€",
        "å", "æ", "ç", "é", "è", "ä", "ç", "ð",
    ))
    replacement = value.count("\ufffd")
    return c1 * 5 + markers * 2 + replacement * 8


def _repair_json(raw: str | None) -> str | None:
    if not raw:
        return raw
    try:
        parsed = json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return repair_mojibake(raw)
    repaired = repair_text_tree(parsed)
    if repaired == parsed:
        return raw
    return json.dumps(repaired, ensure_ascii=False, separators=(",", ":"))


def repair_legacy_database_text(conn: sqlite3.Connection) -> dict[str, int]:
    """Repair known persisted presentation fields once after migration v10."""
    marker = "utf8_latin1_mojibake_v1"
    conn.execute(
        "CREATE TABLE IF NOT EXISTS data_repairs("
        "name TEXT PRIMARY KEY, details_json TEXT NOT NULL, applied_at TEXT NOT NULL)"
    )
    if conn.execute("SELECT 1 FROM data_repairs WHERE name=?", (marker,)).fetchone():
        return {"rows": 0, "fields": 0}

    text_columns = {
        "source_contents": ("title", "author"),
        "creation_projects": ("title", "goal", "audience", "diff_strategy"),
        "content_tasks": ("title",),
        "publications": ("final_title", "note"),
        "usage_ledger": ("task",),
        "patterns": ("name", "fit", "note"),
    }
    json_columns = {
        "jobs": ("input_json", "plan_json", "output_json", "error_json"),
        "source_contents": ("assets_json",),
        "analyses": ("result_json",),
        "content_artifact_versions": ("content_json", "source_refs_json"),
        "creation_projects": ("source_chain_json", "outline_json", "context_json", "checks_json"),
    }
    tables = {row[0] for row in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    ).fetchall()}
    stats = {"rows": 0, "fields": 0}
    touched: set[tuple[str, int]] = set()

    for table, columns in text_columns.items():
        if table not in tables:
            continue
        available = {row[1] for row in conn.execute(f'PRAGMA table_info("{table}")')}
        cols = [column for column in columns if column in available]
        if not cols:
            continue
        select_cols = ",".join(f'"{column}"' for column in cols)
        for row in conn.execute(f'SELECT rowid,{select_cols} FROM "{table}"').fetchall():
            updates = {}
            for index, column in enumerate(cols, start=1):
                old = row[index]
                new = repair_mojibake(old) if isinstance(old, str) else old
                if new != old:
                    updates[column] = new
            if updates:
                clause = ",".join(f'"{key}"=?' for key in updates)
                conn.execute(f'UPDATE "{table}" SET {clause} WHERE rowid=?',
                             (*updates.values(), row[0]))
                stats["fields"] += len(updates)
                touched.add((table, row[0]))

    for table, columns in json_columns.items():
        if table not in tables:
            continue
        available = {row[1] for row in conn.execute(f'PRAGMA table_info("{table}")')}
        cols = [column for column in columns if column in available]
        if not cols:
            continue
        select_cols = ",".join(f'"{column}"' for column in cols)
        for row in conn.execute(f'SELECT rowid,{select_cols} FROM "{table}"').fetchall():
            updates = {}
            for index, column in enumerate(cols, start=1):
                old = row[index]
                new = _repair_json(old)
                if new != old:
                    updates[column] = new
            if updates:
                clause = ",".join(f'"{key}"=?' for key in updates)
                conn.execute(f'UPDATE "{table}" SET {clause} WHERE rowid=?',
                             (*updates.values(), row[0]))
                stats["fields"] += len(updates)
                touched.add((table, row[0]))

    stats["rows"] = len(touched)
    conn.execute(
        "INSERT INTO data_repairs(name,details_json,applied_at) VALUES(?,?,?)",
        (marker, json.dumps(stats, ensure_ascii=False),
         datetime.datetime.now().isoformat(timespec="seconds")),
    )
    conn.commit()
    return stats


def repair_mislabeled_description_assets(conn: sqlite3.Connection) -> dict[str, int]:
    """Preserve, but stop treating platform descriptions as verbatim captions."""
    marker = "description_mislabeled_as_caption_v1"
    conn.execute(
        "CREATE TABLE IF NOT EXISTS data_repairs("
        "name TEXT PRIMARY KEY, details_json TEXT NOT NULL, applied_at TEXT NOT NULL)"
    )
    if conn.execute("SELECT 1 FROM data_repairs WHERE name=?", (marker,)).fetchone():
        return {"sources": 0, "analyses": 0}
    stats = {"sources": 0, "analyses": 0}
    rows = conn.execute(
        "SELECT id,title,assets_json FROM source_contents"
    ).fetchall()
    for row in rows:
        assets = json.loads(row[2] or "{}")
        caption = assets.get("caption") or {}
        asr = assets.get("asr") or {}
        text = str(caption.get("text") or "").strip()
        trace = str(caption.get("trace") or "")
        title = str(row[1] or "").strip()
        if not caption.get("exists") or asr.get("exists"):
            continue
        is_legacy_description = "字幕接口" in trace and (
            text == title or (text and title and (text in title or title in text))
        )
        if not is_legacy_description:
            continue
        metadata = assets.setdefault("metadata", {"exists": True})
        metadata["exists"] = True
        metadata["description"] = text
        metadata["description_note"] = "平台作品描述（不是逐字字幕）"
        assets["caption"] = {
            "exists": False,
            "note": "历史版本曾将作品描述误标为字幕；已保留到元数据，需授权 ASR 后再拆解",
        }
        conn.execute(
            "UPDATE source_contents SET status='metadata_only',assets_json=? WHERE id=?",
            (json.dumps(assets, ensure_ascii=False, separators=(",", ":")), row[0]),
        )
        stats["sources"] += 1
        for analysis in conn.execute(
            "SELECT id,result_json FROM analyses WHERE source_content_id=?", (row[0],)
        ).fetchall():
            result = json.loads(analysis[1] or "{}")
            result["quality_status"] = "insufficient_source"
            result["quality_note"] = "该拆解基于作品描述而非逐字转写，需完成 ASR 后重新拆解"
            conn.execute(
                "UPDATE analyses SET status='stale',result_json=? WHERE id=?",
                (json.dumps(result, ensure_ascii=False, separators=(",", ":")), analysis[0]),
            )
            stats["analyses"] += 1
    conn.execute(
        "INSERT INTO data_repairs(name,details_json,applied_at) VALUES(?,?,?)",
        (marker, json.dumps(stats, ensure_ascii=False),
         datetime.datetime.now().isoformat(timespec="seconds")),
    )
    conn.commit()
    return stats


def repair_placeholder_draft_platforms(conn: sqlite3.Connection) -> dict[str, int]:
    """Repair platform labels that were persisted as question-mark placeholders."""
    tables = {row[0] for row in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    ).fetchall()}
    if "draft_versions" not in tables:
        return {"rows": 0}
    repaired = 0
    for row in conn.execute(
        "SELECT id,project_id,platform FROM draft_versions"
    ).fetchall():
        platform = str(row[2] or "").strip()
        if platform and not re.fullmatch(r"[?？\s]+", platform):
            continue
        siblings = conn.execute(
            "SELECT platform FROM draft_versions WHERE project_id=? AND id<>? "
            "AND platform IS NOT NULL AND TRIM(platform)<>'' ORDER BY version_no DESC",
            (row[1], row[0]),
        ).fetchall()
        inferred = next((str(item[0]).strip() for item in siblings
                         if not re.fullmatch(r"[?？\s]+", str(item[0] or "").strip())),
                        "抖音口播")
        conn.execute("UPDATE draft_versions SET platform=? WHERE id=?", (inferred, row[0]))
        repaired += 1
    if repaired:
        conn.commit()
    return {"rows": repaired}
