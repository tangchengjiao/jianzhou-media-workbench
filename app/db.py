"""SQLite 持久层：标准库实现，启动自动建表（幂等），JSON 列存储复杂结构。"""
import json
import sqlite3
import threading
from pathlib import Path

from .config import DATA_DIR, DB_PATH

_local = threading.local()

SCHEMA = """
CREATE TABLE IF NOT EXISTS users(
  id TEXT PRIMARY KEY, name TEXT, avatar TEXT, created_at TEXT);
CREATE TABLE IF NOT EXISTS data_packages(
  id TEXT PRIMARY KEY, owner_id TEXT, type TEXT, name TEXT, version TEXT,
  status TEXT, source_count INTEGER, summary TEXT, visibility TEXT,
  created_at TEXT, updated_at TEXT, activated_at TEXT);
CREATE TABLE IF NOT EXISTS profile_versions(
  id TEXT PRIMARY KEY, user_id TEXT, version_no TEXT, content_json TEXT,
  reason TEXT, source TEXT, created_at TEXT, active INTEGER);
CREATE TABLE IF NOT EXISTS profile_field_candidates(
  id TEXT PRIMARY KEY, user_id TEXT, session_id TEXT, field_path TEXT,
  value TEXT, inference_type TEXT, confidence REAL, evidence TEXT,
  status TEXT, created_at TEXT);
CREATE TABLE IF NOT EXISTS interview_sessions(
  id TEXT PRIMARY KEY, user_id TEXT, topic TEXT, mode TEXT, status TEXT,
  current_q INTEGER, total_q INTEGER, created_at TEXT, updated_at TEXT);
CREATE TABLE IF NOT EXISTS interview_turns(
  id TEXT PRIMARY KEY, session_id TEXT, idx INTEGER, role TEXT, text TEXT,
  audio_ref TEXT, ts TEXT);
CREATE TABLE IF NOT EXISTS accounts(
  id TEXT PRIMARY KEY, user_id TEXT, platform TEXT, handle TEXT, kind TEXT,
  status TEXT, sync_frequency TEXT, last_sync_at TEXT, followers INTEGER,
  bound_provider TEXT, note TEXT, extra_json TEXT);
CREATE TABLE IF NOT EXISTS account_snapshots(
  id TEXT PRIMARY KEY, account_id TEXT, date TEXT, followers INTEGER,
  posts INTEGER, metrics_json TEXT);
CREATE TABLE IF NOT EXISTS signals(
  id TEXT PRIMARY KEY, user_id TEXT, source_type TEXT, platform TEXT,
  author TEXT, title TEXT, url TEXT, published_at TEXT, metrics_json TEXT,
  rel_perf TEXT, relevance INTEGER, timeliness TEXT, status TEXT,
  category TEXT, source TEXT, fetched_at TEXT, content_id TEXT);
CREATE TABLE IF NOT EXISTS source_contents(
  id TEXT PRIMARY KEY, user_id TEXT, signal_id TEXT, title TEXT,
  platform TEXT, author TEXT, url TEXT, status TEXT, assets_json TEXT,
  created_at TEXT);
CREATE TABLE IF NOT EXISTS jobs(
  id TEXT PRIMARY KEY, user_id TEXT, task_type TEXT, status TEXT,
  current_step TEXT, progress INTEGER, input_json TEXT, plan_json TEXT,
  output_json TEXT, error_json TEXT, trace_id TEXT, cost_est REAL,
  cost_actual REAL, created_at TEXT, started_at TEXT, finished_at TEXT);
CREATE TABLE IF NOT EXISTS analyses(
  id TEXT PRIMARY KEY, user_id TEXT, source_content_id TEXT, status TEXT,
  result_json TEXT, model TEXT, cost REAL, created_at TEXT);
CREATE TABLE IF NOT EXISTS patterns(
  id TEXT PRIMARY KEY, user_id TEXT, name TEXT, status TEXT, samples INTEGER,
  confidence TEXT, fit TEXT, note TEXT, created_at TEXT);
CREATE TABLE IF NOT EXISTS creation_projects(
  id TEXT PRIMARY KEY, user_id TEXT, title TEXT, goal TEXT, audience TEXT,
  source_chain_json TEXT, diff_strategy TEXT, outline_json TEXT,
  context_json TEXT, checks_json TEXT, status TEXT, created_at TEXT);
CREATE TABLE IF NOT EXISTS draft_versions(
  id TEXT PRIMARY KEY, project_id TEXT, version_no INTEGER, platform TEXT,
  content TEXT, note TEXT, created_at TEXT);
CREATE TABLE IF NOT EXISTS predictions(
  id TEXT PRIMARY KEY, project_id TEXT, goal TEXT, expected_bucket TEXT,
  confidence REAL, strengths TEXT, risks TEXT, hypothesis TEXT,
  user_judgement TEXT, locked INTEGER, locked_at TEXT);
CREATE TABLE IF NOT EXISTS content_tasks(
  id TEXT PRIMARY KEY, user_id TEXT, project_id TEXT, title TEXT,
  status TEXT, platform TEXT, scheduled_date TEXT, estimate_h REAL,
  priority TEXT, badges_json TEXT);
CREATE TABLE IF NOT EXISTS publications(
  id TEXT PRIMARY KEY, user_id TEXT, project_id TEXT, task_id TEXT,
  platform TEXT, url TEXT, published_at TEXT, final_title TEXT, note TEXT,
  prediction_id TEXT);
CREATE TABLE IF NOT EXISTS publication_snapshots(
  id TEXT PRIMARY KEY, publication_id TEXT, tn TEXT, plays INTEGER,
  likes INTEGER, comments INTEGER, collects INTEGER, taken_at TEXT);
CREATE TABLE IF NOT EXISTS retro_reports(
  id TEXT PRIMARY KEY, user_id TEXT, publication_id TEXT,
  conclusion_json TEXT, created_at TEXT);
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
  charged_on_fail INTEGER, ts TEXT);
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
  scan_range TEXT, note TEXT, created_at TEXT, last_scan_at TEXT);
CREATE TABLE IF NOT EXISTS benchmark_works(
  id TEXT PRIMARY KEY, bm_id TEXT, video_id TEXT, title TEXT, cover TEXT,
  url TEXT, create_time INTEGER, plays INTEGER, likes INTEGER,
  comments INTEGER, collects INTEGER, shares INTEGER, score INTEGER,
  is_hot INTEGER, duration INTEGER, first_seen_at TEXT,
  UNIQUE(bm_id, video_id));
CREATE TABLE IF NOT EXISTS inbox(
  id TEXT PRIMARY KEY, user_id TEXT, kind TEXT, title TEXT, body TEXT,
  ref_json TEXT, read INTEGER, ts TEXT);
CREATE TABLE IF NOT EXISTS author_analyses(
  id TEXT PRIMARY KEY, bm_id TEXT, engine TEXT, result_json TEXT,
  created_at TEXT);
"""


def connect() -> sqlite3.Connection:
    if not hasattr(_local, "conn") or _local.conn is None:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(DB_PATH), check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        _local.conn = conn
    return _local.conn


def init_db() -> None:
    conn = connect()
    conn.executescript(SCHEMA)
    conn.commit()


def query(sql: str, params: tuple = ()) -> list[dict]:
    cur = connect().execute(sql, params)
    return [dict(r) for r in cur.fetchall()]


def query_one(sql: str, params: tuple = ()) -> dict | None:
    rows = query(sql, params)
    return rows[0] if rows else None


def execute(sql: str, params: tuple = ()) -> int:
    conn = connect()
    cur = conn.execute(sql, params)
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
