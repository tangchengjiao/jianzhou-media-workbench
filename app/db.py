"""SQLite 持久层：标准库实现，启动自动建表（幂等），JSON 列存储复杂结构。"""
import json
import os
import sqlite3
import threading
import hashlib
import datetime
from contextlib import contextmanager
from pathlib import Path

from .config import BACKUP_DIR, DATA_DIR, DB_PATH

_local = threading.local()

SCHEMA = """
CREATE TABLE IF NOT EXISTS users(
  id TEXT PRIMARY KEY, name TEXT, avatar TEXT, created_at TEXT);
CREATE TABLE IF NOT EXISTS data_packages(
  id TEXT PRIMARY KEY, owner_id TEXT, type TEXT, name TEXT, version TEXT,
  status TEXT, source_count INTEGER, summary TEXT, visibility TEXT,
  created_at TEXT, updated_at TEXT, activated_at TEXT,
  FOREIGN KEY(owner_id) REFERENCES users(id) ON DELETE CASCADE);
CREATE TABLE IF NOT EXISTS profile_versions(
  id TEXT PRIMARY KEY, user_id TEXT, version_no TEXT, content_json TEXT,
  reason TEXT, source TEXT, created_at TEXT, active INTEGER,
  FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE);
CREATE TABLE IF NOT EXISTS profile_field_candidates(
  id TEXT PRIMARY KEY, user_id TEXT, session_id TEXT, field_path TEXT,
  value TEXT, inference_type TEXT, confidence REAL, evidence TEXT,
  status TEXT, created_at TEXT,
  FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE,
  FOREIGN KEY(session_id) REFERENCES interview_sessions(id) ON DELETE CASCADE);
CREATE TABLE IF NOT EXISTS interview_sessions(
  id TEXT PRIMARY KEY, user_id TEXT, topic TEXT, mode TEXT, status TEXT,
  current_q INTEGER, total_q INTEGER, created_at TEXT, updated_at TEXT,
  FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE);
CREATE TABLE IF NOT EXISTS interview_turns(
  id TEXT PRIMARY KEY, session_id TEXT, idx INTEGER, role TEXT, text TEXT,
  audio_ref TEXT, ts TEXT,
  FOREIGN KEY(session_id) REFERENCES interview_sessions(id) ON DELETE CASCADE);
CREATE TABLE IF NOT EXISTS accounts(
  id TEXT PRIMARY KEY, user_id TEXT, platform TEXT, handle TEXT, kind TEXT,
  status TEXT, sync_frequency TEXT, last_sync_at TEXT, followers INTEGER,
  bound_provider TEXT, note TEXT, extra_json TEXT,
  FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE);
CREATE TABLE IF NOT EXISTS account_snapshots(
  id TEXT PRIMARY KEY, account_id TEXT, date TEXT, followers INTEGER,
  posts INTEGER, metrics_json TEXT,
  FOREIGN KEY(account_id) REFERENCES accounts(id) ON DELETE CASCADE);
CREATE TABLE IF NOT EXISTS signals(
  id TEXT PRIMARY KEY, user_id TEXT, source_type TEXT, platform TEXT,
  author TEXT, title TEXT, url TEXT, published_at TEXT, metrics_json TEXT,
  rel_perf TEXT, relevance INTEGER, timeliness TEXT, status TEXT,
  category TEXT, source TEXT, fetched_at TEXT, content_id TEXT,
  FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE);
CREATE TABLE IF NOT EXISTS source_contents(
  id TEXT PRIMARY KEY, user_id TEXT, signal_id TEXT, title TEXT,
  platform TEXT, author TEXT, url TEXT, status TEXT, assets_json TEXT,
  created_at TEXT,
  FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE,
  FOREIGN KEY(signal_id) REFERENCES signals(id) ON DELETE SET NULL);
CREATE TABLE IF NOT EXISTS jobs(
  id TEXT PRIMARY KEY, user_id TEXT, task_type TEXT, status TEXT,
  current_step TEXT, progress INTEGER, input_json TEXT, plan_json TEXT,
  output_json TEXT, error_json TEXT, trace_id TEXT, cost_est REAL,
  cost_actual REAL, created_at TEXT, started_at TEXT, finished_at TEXT,
  FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE);
CREATE TABLE IF NOT EXISTS analyses(
  id TEXT PRIMARY KEY, user_id TEXT, source_content_id TEXT, status TEXT,
  result_json TEXT, model TEXT, cost REAL, created_at TEXT,
  FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE,
  FOREIGN KEY(source_content_id) REFERENCES source_contents(id) ON DELETE CASCADE);
CREATE TABLE IF NOT EXISTS patterns(
  id TEXT PRIMARY KEY, user_id TEXT, name TEXT, status TEXT, samples INTEGER,
  confidence TEXT, fit TEXT, note TEXT, created_at TEXT,
  FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE);
CREATE TABLE IF NOT EXISTS creation_projects(
  id TEXT PRIMARY KEY, user_id TEXT, title TEXT, goal TEXT, audience TEXT,
  source_chain_json TEXT, diff_strategy TEXT, outline_json TEXT,
  context_json TEXT, checks_json TEXT, status TEXT, created_at TEXT,
  FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE);
CREATE TABLE IF NOT EXISTS draft_versions(
  id TEXT PRIMARY KEY, project_id TEXT, version_no INTEGER, platform TEXT,
  content TEXT, note TEXT, created_at TEXT,
  FOREIGN KEY(project_id) REFERENCES creation_projects(id) ON DELETE CASCADE);
CREATE TABLE IF NOT EXISTS predictions(
  id TEXT PRIMARY KEY, project_id TEXT, goal TEXT, expected_bucket TEXT,
  confidence REAL, strengths TEXT, risks TEXT, hypothesis TEXT,
  user_judgement TEXT, locked INTEGER, locked_at TEXT,
  FOREIGN KEY(project_id) REFERENCES creation_projects(id) ON DELETE CASCADE);
CREATE TABLE IF NOT EXISTS content_tasks(
  id TEXT PRIMARY KEY, user_id TEXT, project_id TEXT, title TEXT,
  status TEXT, platform TEXT, scheduled_date TEXT, estimate_h REAL,
  priority TEXT, badges_json TEXT,
  FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE,
  FOREIGN KEY(project_id) REFERENCES creation_projects(id) ON DELETE SET NULL);
CREATE TABLE IF NOT EXISTS publications(
  id TEXT PRIMARY KEY, user_id TEXT, project_id TEXT, task_id TEXT,
  platform TEXT, url TEXT, published_at TEXT, final_title TEXT, note TEXT,
  prediction_id TEXT,
  FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE,
  FOREIGN KEY(project_id) REFERENCES creation_projects(id) ON DELETE SET NULL,
  FOREIGN KEY(task_id) REFERENCES content_tasks(id) ON DELETE SET NULL,
  FOREIGN KEY(prediction_id) REFERENCES predictions(id) ON DELETE SET NULL);
CREATE TABLE IF NOT EXISTS publication_snapshots(
  id TEXT PRIMARY KEY, publication_id TEXT, tn TEXT, plays INTEGER,
  likes INTEGER, comments INTEGER, collects INTEGER, taken_at TEXT,
  FOREIGN KEY(publication_id) REFERENCES publications(id) ON DELETE CASCADE);
CREATE TABLE IF NOT EXISTS retro_reports(
  id TEXT PRIMARY KEY, user_id TEXT, publication_id TEXT,
  conclusion_json TEXT, created_at TEXT,
  FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE,
  FOREIGN KEY(publication_id) REFERENCES publications(id) ON DELETE CASCADE);
CREATE TABLE IF NOT EXISTS rule_suggestions(
  id TEXT PRIMARY KEY, user_id TEXT, action TEXT, rule_text TEXT,
  evidence TEXT, confidence REAL, scope TEXT, impact TEXT, status TEXT,
  created_at TEXT);
CREATE TABLE IF NOT EXISTS rules(
  id TEXT PRIMARY KEY, user_id TEXT, text TEXT, status TEXT, scope TEXT,
  confidence REAL, evidence_count INTEGER, note TEXT, version TEXT);
CREATE TABLE IF NOT EXISTS growth_events(
  id TEXT PRIMARY KEY, user_id TEXT, date TEXT, title TEXT, desc TEXT,
  kind TEXT);
CREATE TABLE IF NOT EXISTS experiments(
  id TEXT PRIMARY KEY, user_id TEXT, name TEXT, change TEXT, period TEXT,
  metric TEXT, samples TEXT, status TEXT, result TEXT);
CREATE TABLE IF NOT EXISTS corpus_items(
  id TEXT PRIMARY KEY, user_id TEXT, name TEXT, type TEXT, count INTEGER,
  perms_json TEXT, valid_until TEXT, source TEXT, analyzed INTEGER);
CREATE TABLE IF NOT EXISTS import_batches(
  id TEXT PRIMARY KEY, user_id TEXT, filename TEXT, target_package TEXT,
  status TEXT, total INTEGER, success INTEGER, failed INTEGER,
  errors_json TEXT, mapping_json TEXT, created_at TEXT);
CREATE TABLE IF NOT EXISTS usage_ledger(
  id TEXT PRIMARY KEY, user_id TEXT, task TEXT, provider TEXT,
  endpoint TEXT, model TEXT, units TEXT, cost REAL, cached INTEGER,
  charged_on_fail INTEGER, ts TEXT,
  FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE);
CREATE TABLE IF NOT EXISTS app_settings(
  key TEXT PRIMARY KEY, value_json TEXT);
CREATE TABLE IF NOT EXISTS error_logs(
  id INTEGER PRIMARY KEY AUTOINCREMENT, trace_id TEXT, code TEXT,
  stage TEXT, provider TEXT, message TEXT, ts TEXT);
CREATE TABLE IF NOT EXISTS benchmark_accounts(
  id TEXT PRIMARY KEY, user_id TEXT, platform TEXT, uid TEXT, link TEXT,
  name TEXT, avatar TEXT, bio TEXT, fans INTEGER, likes_count INTEGER,
  video_count INTEGER, median_score INTEGER, avg_score INTEGER,
  post_gap_days REAL, last_video_id TEXT, has_update INTEGER,
  scan_range TEXT, note TEXT, created_at TEXT, last_scan_at TEXT,
  FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE);
CREATE TABLE IF NOT EXISTS benchmark_works(
  id TEXT PRIMARY KEY, bm_id TEXT, video_id TEXT, title TEXT, cover TEXT,
  url TEXT, create_time INTEGER, plays INTEGER, likes INTEGER,
  comments INTEGER, collects INTEGER, shares INTEGER, score INTEGER,
  is_hot INTEGER, duration INTEGER, first_seen_at TEXT,
  UNIQUE(bm_id, video_id),
  FOREIGN KEY(bm_id) REFERENCES benchmark_accounts(id) ON DELETE CASCADE);
CREATE TABLE IF NOT EXISTS inbox(
  id TEXT PRIMARY KEY, user_id TEXT, kind TEXT, title TEXT, body TEXT,
  ref_json TEXT, read INTEGER, ts TEXT,
  FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE);
CREATE TABLE IF NOT EXISTS author_analyses(
  id TEXT PRIMARY KEY, bm_id TEXT, engine TEXT, result_json TEXT,
  created_at TEXT,
  FOREIGN KEY(bm_id) REFERENCES benchmark_accounts(id) ON DELETE CASCADE);
CREATE TABLE IF NOT EXISTS schema_migrations(
  version INTEGER PRIMARY KEY, name TEXT NOT NULL, checksum TEXT NOT NULL,
  applied_at TEXT NOT NULL);
"""

INDEX_MIGRATION = """
CREATE INDEX IF NOT EXISTS idx_packages_owner_type_status ON data_packages(owner_id, type, status);
CREATE INDEX IF NOT EXISTS idx_profiles_user_active ON profile_versions(user_id, active);
CREATE INDEX IF NOT EXISTS idx_accounts_user_platform ON accounts(user_id, platform);
CREATE INDEX IF NOT EXISTS idx_signals_user_status_published ON signals(user_id, status, published_at);
CREATE INDEX IF NOT EXISTS idx_source_contents_user_created ON source_contents(user_id, created_at);
CREATE INDEX IF NOT EXISTS idx_jobs_user_status_created ON jobs(user_id, status, created_at);
CREATE INDEX IF NOT EXISTS idx_jobs_status_created ON jobs(status, created_at);
CREATE INDEX IF NOT EXISTS idx_analyses_user_source ON analyses(user_id, source_content_id);
CREATE INDEX IF NOT EXISTS idx_projects_user_created ON creation_projects(user_id, created_at);
CREATE INDEX IF NOT EXISTS idx_drafts_project_platform_version ON draft_versions(project_id, platform, version_no);
CREATE INDEX IF NOT EXISTS idx_tasks_user_status_date ON content_tasks(user_id, status, scheduled_date);
CREATE INDEX IF NOT EXISTS idx_publications_user_published ON publications(user_id, published_at);
CREATE INDEX IF NOT EXISTS idx_snapshots_publication_tn ON publication_snapshots(publication_id, tn);
CREATE INDEX IF NOT EXISTS idx_usage_user_ts ON usage_ledger(user_id, ts);
CREATE INDEX IF NOT EXISTS idx_benchmarks_user_created ON benchmark_accounts(user_id, created_at);
CREATE INDEX IF NOT EXISTS idx_works_benchmark_time ON benchmark_works(bm_id, create_time DESC);
CREATE INDEX IF NOT EXISTS idx_inbox_user_read_ts ON inbox(user_id, read, ts DESC);
"""

BENCHMARK_SYNC_STATE_MIGRATION = """
ALTER TABLE benchmark_accounts ADD COLUMN sync_status TEXT NOT NULL DEFAULT 'idle';
ALTER TABLE benchmark_accounts ADD COLUMN last_error_code TEXT;
ALTER TABLE benchmark_accounts ADD COLUMN last_error_message TEXT;
CREATE INDEX IF NOT EXISTS idx_benchmarks_user_sync_status
ON benchmark_accounts(user_id, sync_status, created_at DESC);
CREATE UNIQUE INDEX IF NOT EXISTS idx_benchmarks_user_platform_uid
ON benchmark_accounts(user_id, platform, uid);
"""

MIGRATIONS = [
    (1, "critical_indexes", INDEX_MIGRATION),
    (2, "constrained_schema_marker",
     "CREATE INDEX IF NOT EXISTS idx_schema_migrations_applied "
     "ON schema_migrations(applied_at);"),
    (3, "benchmark_sync_state", BENCHMARK_SYNC_STATE_MIGRATION),
]


def connect() -> sqlite3.Connection:
    if not hasattr(_local, "conn") or _local.conn is None:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(DB_PATH), check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA busy_timeout=5000")
        conn.execute("PRAGMA synchronous=NORMAL")
        _local.conn = conn
    return _local.conn


def init_db() -> None:
    existed_before = DB_PATH.exists()
    conn = connect()
    conn.executescript(SCHEMA)
    conn.commit()
    if existed_before:
        _protect_legacy_secrets(conn)
    rebuilt = False
    if _needs_constraint_rebuild(conn):
        backup_database("pre_constraint_rebuild")
        _rebuild_with_constraints(conn)
        conn = connect()
        rebuilt = True
    _apply_migrations(conn, create_backup=existed_before and not rebuilt)


def _protect_legacy_secrets(conn: sqlite3.Connection) -> None:
    """Encrypt and scrub legacy plaintext keys before any migration backup."""
    from .secret_store import get_secret, set_secret

    changed = False
    for section, field, secret_name in (
        ("source", "tikhub_api_key", "tikhub_api_key"),
        ("llm", "api_key", "llm_api_key"),
    ):
        row = conn.execute("SELECT value_json FROM app_settings WHERE key=?", (section,)).fetchone()
        values = jloads(row["value_json"], {}) if row else {}
        legacy = values.pop(field, "")
        if legacy:
            if not get_secret(secret_name):
                set_secret(secret_name, legacy)
            conn.execute("UPDATE app_settings SET value_json=? WHERE key=?",
                         (jdumps(values), section))
            changed = True
    row = conn.execute("SELECT value_json FROM app_settings WHERE key='datasource'").fetchone()
    values = jloads(row["value_json"], {}) if row else {}
    auth = (((values.get("providers") or {}).get("tikhub") or {}).get("auth") or {})
    legacy = auth.pop("value", "")
    if legacy:
        if not get_secret("tikhub_api_key"):
            set_secret("tikhub_api_key", legacy)
        conn.execute("UPDATE app_settings SET value_json=? WHERE key='datasource'",
                     (jdumps(values),))
        changed = True
    if changed:
        conn.commit()


def _needs_constraint_rebuild(conn: sqlite3.Connection) -> bool:
    tables = {r["name"] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    )}
    if "data_packages" not in tables:
        return False
    return not conn.execute("PRAGMA foreign_key_list(data_packages)").fetchall()


_NULLABLE_FOREIGN_KEYS = {
    ("source_contents", "signal_id"),
    ("content_tasks", "project_id"),
    ("publications", "project_id"),
    ("publications", "task_id"),
    ("publications", "prediction_id"),
    ("predictions", "project_id"),
}


def _rebuild_with_constraints(source: sqlite3.Connection) -> None:
    """Copy a legacy database into the canonical constrained schema atomically."""
    shadow = DB_PATH.with_name(DB_PATH.name + ".migrating")
    if shadow.exists():
        shadow.unlink()
    target = sqlite3.connect(str(shadow))
    target.row_factory = sqlite3.Row
    try:
        target.execute("PRAGMA foreign_keys=OFF")
        target.executescript(SCHEMA)
        target_tables = [r["name"] for r in target.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name NOT LIKE 'sqlite_%' ORDER BY name"
        )]
        source_tables = {r["name"] for r in source.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )}
        for table in target_tables:
            if table not in source_tables:
                continue
            target_columns = [r["name"] for r in target.execute(f'PRAGMA table_info("{table}")')]
            source_columns = {r["name"] for r in source.execute(f'PRAGMA table_info("{table}")')}
            columns = [c for c in target_columns if c in source_columns]
            if not columns:
                continue
            quoted = ",".join(f'"{c}"' for c in columns)
            rows = []
            for row in source.execute(f'SELECT {quoted} FROM "{table}"'):
                values = []
                for column, value in zip(columns, row):
                    if (table, column) in _NULLABLE_FOREIGN_KEYS and value == "":
                        value = None
                    values.append(value)
                rows.append(tuple(values))
            if rows:
                placeholders = ",".join("?" for _ in columns)
                target.executemany(
                    f'INSERT INTO "{table}"({quoted}) VALUES({placeholders})', rows
                )
        target.commit()
        target.execute("PRAGMA foreign_keys=ON")
        violations = target.execute("PRAGMA foreign_key_check").fetchall()
        integrity = target.execute("PRAGMA integrity_check").fetchone()[0]
        if violations or integrity != "ok":
            sample = [tuple(r) for r in violations[:10]]
            raise RuntimeError(f"约束迁移校验失败：integrity={integrity}, foreign_keys={sample}")
    except Exception:
        target.close()
        failed = shadow.with_name(shadow.name + ".failed")
        if failed.exists():
            failed.unlink()
        shadow.replace(failed)
        raise
    else:
        target.close()
    close_connection()
    os.replace(shadow, DB_PATH)
    for suffix in ("-wal", "-shm"):
        stale = Path(str(DB_PATH) + suffix)
        if stale.exists():
            stale.unlink()


def _apply_migrations(conn: sqlite3.Connection, create_backup: bool = True) -> None:
    applied = {r["version"]: r for r in conn.execute(
        "SELECT version, name, checksum FROM schema_migrations"
    ).fetchall()}
    pending = [(v, n, sql) for v, n, sql in MIGRATIONS if v not in applied]
    if create_backup and pending and DB_PATH.exists() and DB_PATH.stat().st_size:
        backup_database("pre_migration")
    for version, name, sql in pending:
        checksum = hashlib.sha256(sql.encode("utf-8")).hexdigest()
        with transaction():
            for statement in (part.strip() for part in sql.split(";")):
                if statement:
                    conn.execute(statement)
            conn.execute(
                "INSERT INTO schema_migrations(version,name,checksum,applied_at) VALUES(?,?,?,?)",
                (version, name, checksum,
                 datetime.datetime.now().isoformat(timespec="seconds")),
            )
            conn.execute(f"PRAGMA user_version={version}")
    for version, name, sql in MIGRATIONS:
        if version in applied:
            expected = hashlib.sha256(sql.encode("utf-8")).hexdigest()
            if applied[version]["checksum"] != expected:
                raise RuntimeError(
                    f"数据库迁移 {version} ({name}) 已被修改；请新增迁移，不要改写历史迁移"
                )


def backup_database(reason: str = "manual") -> Path:
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    safe_reason = "".join(c for c in reason if c.isalnum() or c in "-_")[:32] or "backup"
    path = BACKUP_DIR / f"app-{stamp}-{safe_reason}.db"
    source = connect()
    with sqlite3.connect(str(path)) as target:
        source.backup(target)
    return path


def close_connection() -> None:
    conn = getattr(_local, "conn", None)
    if conn is not None:
        conn.close()
        _local.conn = None


@contextmanager
def transaction():
    """Atomic unit of work with nested savepoints on the current thread."""
    conn = connect()
    depth = getattr(_local, "transaction_depth", 0)
    savepoint = f"sp_{depth}"
    if depth == 0:
        conn.execute("BEGIN IMMEDIATE")
    else:
        conn.execute(f"SAVEPOINT {savepoint}")
    _local.transaction_depth = depth + 1
    try:
        yield conn
        if depth == 0:
            conn.commit()
        else:
            conn.execute(f"RELEASE SAVEPOINT {savepoint}")
    except Exception:
        if depth == 0:
            conn.rollback()
        else:
            conn.execute(f"ROLLBACK TO SAVEPOINT {savepoint}")
            conn.execute(f"RELEASE SAVEPOINT {savepoint}")
        raise
    finally:
        _local.transaction_depth = depth


def query(sql: str, params: tuple = ()) -> list[dict]:
    cur = connect().execute(sql, params)
    return [dict(r) for r in cur.fetchall()]


def query_one(sql: str, params: tuple = ()) -> dict | None:
    rows = query(sql, params)
    return rows[0] if rows else None


def execute(sql: str, params: tuple = ()) -> int:
    conn = connect()
    cur = conn.execute(sql, params)
    if getattr(_local, "transaction_depth", 0) == 0:
        conn.commit()
    return cur.rowcount


def insert(sql: str, params: tuple = ()) -> None:
    execute(sql, params)


def jloads(value, default=None):
    if value is None:
        return default
    try:
        return json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return default


def jdumps(value) -> str:
    return json.dumps(value, ensure_ascii=False)


def table_empty(name: str) -> bool:
    row = query_one(f"SELECT COUNT(*) AS c FROM {name}")
    return row["c"] == 0
