"""Read-only acceptance checks for the schema-30 channel/visual lineage."""
from __future__ import annotations

import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.db import query, query_one


def main() -> None:
    duplicate_tasks = query(
        "SELECT t.id,t.user_id,t.project_id,t.campaign_id,t.deliverable_id,t.title,t.status,"
        "t.delivery_kind,t.scheduled_date FROM content_tasks t "
        "JOIN (SELECT user_id,project_id FROM content_tasks WHERE project_id IS NOT NULL "
        "AND status IN ('CANDIDATE','SELECTED','OUTLINING','DRAFTING','PRODUCING',"
        "'READY_TO_PUBLISH','PAUSED') "
        "GROUP BY user_id,project_id HAVING COUNT(1)>1) d "
        "ON d.user_id=t.user_id AND d.project_id=t.project_id "
        "ORDER BY t.project_id,t.id"
    )
    result = {
        "schema": query_one("PRAGMA user_version")["user_version"],
        "integrity": query_one("PRAGMA integrity_check")["integrity_check"],
        "fk_errors": len(query("PRAGMA foreign_key_check")),
        "cover_column": any(
            row["name"] == "cover_project_id"
            for row in query("PRAGMA table_info(official_articles)")
        ),
        "duplicate_campaign_projects": query_one(
            "SELECT COUNT(1) n FROM ("
            "SELECT user_id,campaign_id,COUNT(1) c FROM creation_projects "
            "WHERE campaign_id IS NOT NULL GROUP BY user_id,campaign_id HAVING c>1)"
        )["n"],
        "duplicate_active_plan_tasks": query_one(
            "SELECT COUNT(1) n FROM ("
            "SELECT user_id,project_id,COUNT(1) c FROM content_tasks "
            "WHERE project_id IS NOT NULL "
            "AND status IN ('CANDIDATE','SELECTED','OUTLINING','DRAFTING','PRODUCING',"
            "'READY_TO_PUBLISH','PAUSED') "
            "GROUP BY user_id,project_id HAVING c>1)"
        )["n"],
        "duplicate_task_rows": duplicate_tasks,
    }
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
