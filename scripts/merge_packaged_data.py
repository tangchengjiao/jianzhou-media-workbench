"""将历史打包数据库无损合并到当前主库。

规则：当前主库优先；相同主键不覆盖；命中业务唯一键时建立 ID 映射；
所有外键随映射更新；完整性检查失败则整笔回滚。默认只演练，传 --apply 才提交。
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from collections import defaultdict
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.config import DB_PATH

EXCLUDED = {"app_settings", "schema_migrations", "data_repairs", "error_logs"}


def quote(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def table_info(conn: sqlite3.Connection, table: str):
    return conn.execute(f"PRAGMA table_info({quote(table)})").fetchall()


def table_order(conn: sqlite3.Connection, tables: set[str]) -> list[str]:
    parents = {}
    for table in tables:
        parents[table] = {
            row[2] for row in conn.execute(f"PRAGMA foreign_key_list({quote(table)})")
            if row[2] in tables and row[2] != table
        }
    ordered: list[str] = []
    remaining = set(tables)
    while remaining:
        ready = sorted(t for t in remaining if parents[t].issubset(set(ordered)))
        if not ready:
            ready = [sorted(remaining)[0]]
        ordered.extend(ready)
        remaining.difference_update(ready)
    return ordered


def unique_indexes(conn: sqlite3.Connection, table: str) -> list[list[str]]:
    result = []
    for row in conn.execute(f"PRAGMA index_list({quote(table)})"):
        if not row[2] or row[4]:
            continue
        cols = [item[2] for item in conn.execute(f"PRAGMA index_info({quote(row[1])})")]
        if cols:
            result.append(cols)
    return result


def find_unique_match(conn: sqlite3.Connection, table: str, values: dict,
                      pk: str | None) -> str | None:
    if not pk:
        return None
    indexes = unique_indexes(conn, table)
    if table == "content_tasks" and values.get("status") not in {"CANCELLED", "ARCHIVED"}:
        indexes.insert(0, ["user_id", "project_id"])
    for cols in indexes:
        if pk in cols or any(values.get(col) is None for col in cols):
            continue
        where = " AND ".join(f"{quote(col)}=?" for col in cols)
        row = conn.execute(
            f"SELECT {quote(pk)} FROM {quote(table)} WHERE {where} LIMIT 1",
            [values[col] for col in cols],
        ).fetchone()
        if row:
            return row[0]
    return None


def merge(source_path: Path, target_path: Path, apply: bool) -> dict:
    source = sqlite3.connect(str(source_path))
    target = sqlite3.connect(str(target_path))
    source.row_factory = sqlite3.Row
    target.row_factory = sqlite3.Row
    source.execute("PRAGMA query_only=ON")
    target.execute("PRAGMA foreign_keys=ON")

    source_tables = {row[0] for row in source.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
    )}
    target_tables = {row[0] for row in target.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
    )}
    tables = (source_tables & target_tables) - EXCLUDED
    order = table_order(target, tables)
    mappings: dict[str, dict[object, object]] = defaultdict(dict)
    report = {"mode": "apply" if apply else "dry-run", "source": str(source_path),
              "target": str(target_path), "tables": {}, "foreign_key_errors": []}

    try:
        target.execute("BEGIN IMMEDIATE")
        target.execute("PRAGMA defer_foreign_keys=ON")
        for table in order:
            src_columns = [row[1] for row in table_info(source, table)]
            dst_columns = {row[1] for row in table_info(target, table)}
            columns = [col for col in src_columns if col in dst_columns]
            pk_cols = [row[1] for row in table_info(target, table) if row[5]]
            pk = pk_cols[0] if len(pk_cols) == 1 else None
            foreign_keys = list(target.execute(f"PRAGMA foreign_key_list({quote(table)})"))
            stats = {"source": 0, "inserted": 0, "existing": 0, "mapped": 0}
            for row in source.execute(f"SELECT {','.join(quote(c) for c in columns)} FROM {quote(table)}"):
                stats["source"] += 1
                values = {col: row[col] for col in columns}
                for fk in foreign_keys:
                    parent_table, from_col = fk[2], fk[3]
                    old_value = values.get(from_col)
                    if old_value in mappings.get(parent_table, {}):
                        values[from_col] = mappings[parent_table][old_value]

                old_pk = values.get(pk) if pk else None
                if pk and target.execute(
                    f"SELECT 1 FROM {quote(table)} WHERE {quote(pk)}=?", (old_pk,)
                ).fetchone():
                    mappings[table][old_pk] = old_pk
                    stats["existing"] += 1
                    continue

                placeholders = ",".join("?" for _ in columns)
                cursor = target.execute(
                    f"INSERT OR IGNORE INTO {quote(table)} "
                    f"({','.join(quote(c) for c in columns)}) VALUES({placeholders})",
                    [values[col] for col in columns],
                )
                if cursor.rowcount == 1:
                    if pk:
                        mappings[table][old_pk] = values[pk]
                    stats["inserted"] += 1
                    continue

                match = find_unique_match(target, table, values, pk)
                if pk and match is not None:
                    mappings[table][old_pk] = match
                    stats["mapped"] += 1
                else:
                    stats["existing"] += 1
            report["tables"][table] = stats

        fk_errors = [tuple(row) for row in target.execute("PRAGMA foreign_key_check")]
        report["foreign_key_errors"] = fk_errors[:50]
        if fk_errors:
            raise RuntimeError(f"合并后发现 {len(fk_errors)} 条外键错误")
        if target.execute("PRAGMA quick_check").fetchone()[0] != "ok":
            raise RuntimeError("合并后 quick_check 未通过")
        if apply:
            target.commit()
        else:
            target.rollback()
    except Exception:
        target.rollback()
        raise
    finally:
        source.close()
        target.close()

    report["inserted_total"] = sum(v["inserted"] for v in report["tables"].values())
    report["mapped_total"] = sum(v["mapped"] for v in report["tables"].values())
    return report


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path,
                        default=Path.home() / "AppData/Local/JianzhouMediaWorkbench/app.db")
    parser.add_argument("--target", type=Path, default=DB_PATH)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    if not args.source.exists():
        raise SystemExit(f"历史数据库不存在：{args.source}")
    if not args.target.exists():
        raise SystemExit(f"主数据库不存在：{args.target}")
    print(json.dumps(merge(args.source.resolve(), args.target.resolve(), args.apply),
                     ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
