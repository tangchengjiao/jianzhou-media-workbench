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
CREATE TABLE IF NOT EXISTS account_works(
  id TEXT PRIMARY KEY, account_id TEXT NOT NULL, video_id TEXT NOT NULL,
  title TEXT NOT NULL, cover TEXT, url TEXT, create_time INTEGER,
  plays INTEGER NOT NULL DEFAULT 0, likes INTEGER NOT NULL DEFAULT 0,
  comments INTEGER NOT NULL DEFAULT 0, collects INTEGER NOT NULL DEFAULT 0,
  shares INTEGER NOT NULL DEFAULT 0, duration INTEGER NOT NULL DEFAULT 0,
  first_seen_at TEXT NOT NULL, last_seen_at TEXT NOT NULL,
  UNIQUE(account_id,video_id),
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
CREATE TABLE IF NOT EXISTS data_repairs(
  name TEXT PRIMARY KEY, details_json TEXT NOT NULL, applied_at TEXT NOT NULL);
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

BENCHMARK_SCAN_COUNT_MIGRATION = """
ALTER TABLE benchmark_accounts ADD COLUMN scan_count INTEGER NOT NULL DEFAULT 30;
UPDATE benchmark_accounts
SET scan_count = CASE
  WHEN scan_range = 'all' THEN 200
  WHEN CAST(scan_range AS INTEGER) BETWEEN 1 AND 500 THEN CAST(scan_range AS INTEGER)
  ELSE 30
END;
CREATE INDEX IF NOT EXISTS idx_benchmarks_user_platform_scan
ON benchmark_accounts(user_id, platform, scan_count);
"""

PHASE_TWO_FOUNDATION_MIGRATION = """
ALTER TABLE jobs ADD COLUMN attempt_count INTEGER NOT NULL DEFAULT 0;
ALTER TABLE jobs ADD COLUMN max_attempts INTEGER NOT NULL DEFAULT 3;
ALTER TABLE jobs ADD COLUMN idempotency_key TEXT;
ALTER TABLE jobs ADD COLUMN lease_until TEXT;
ALTER TABLE jobs ADD COLUMN heartbeat_at TEXT;
CREATE UNIQUE INDEX IF NOT EXISTS idx_jobs_user_task_idempotency
ON jobs(user_id, task_type, idempotency_key)
WHERE idempotency_key IS NOT NULL;
CREATE TABLE IF NOT EXISTS job_steps(
  id TEXT PRIMARY KEY, job_id TEXT NOT NULL, step TEXT NOT NULL,
  status TEXT NOT NULL, attempt INTEGER NOT NULL, started_at TEXT,
  finished_at TEXT, error_json TEXT,
  UNIQUE(job_id, step, attempt),
  FOREIGN KEY(job_id) REFERENCES jobs(id) ON DELETE CASCADE);
CREATE INDEX IF NOT EXISTS idx_job_steps_job_attempt
ON job_steps(job_id, attempt, started_at);
CREATE TABLE IF NOT EXISTS media_assets(
  id TEXT PRIMARY KEY, user_id TEXT NOT NULL, source_content_id TEXT,
  kind TEXT NOT NULL, path TEXT NOT NULL, mime_type TEXT, size_bytes INTEGER,
  sha256 TEXT NOT NULL, immutable INTEGER NOT NULL DEFAULT 1,
  metadata_json TEXT, created_at TEXT NOT NULL,
  FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE,
  FOREIGN KEY(source_content_id) REFERENCES source_contents(id) ON DELETE SET NULL);
CREATE INDEX IF NOT EXISTS idx_media_assets_user_source
ON media_assets(user_id, source_content_id, created_at);
CREATE TABLE IF NOT EXISTS context_runs(
  id TEXT PRIMARY KEY, user_id TEXT NOT NULL, task_type TEXT NOT NULL,
  refs_json TEXT NOT NULL, content_json TEXT NOT NULL,
  token_estimate INTEGER NOT NULL, checksum TEXT NOT NULL, created_at TEXT NOT NULL,
  FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE);
CREATE INDEX IF NOT EXISTS idx_context_runs_user_created
ON context_runs(user_id, created_at DESC);
CREATE TABLE IF NOT EXISTS snapshot_schedules(
  id TEXT PRIMARY KEY, publication_id TEXT NOT NULL, tn TEXT NOT NULL,
  due_at TEXT NOT NULL, status TEXT NOT NULL, attempts INTEGER NOT NULL DEFAULT 0,
  last_error TEXT, captured_at TEXT,
  UNIQUE(publication_id, tn),
  FOREIGN KEY(publication_id) REFERENCES publications(id) ON DELETE CASCADE);
CREATE INDEX IF NOT EXISTS idx_snapshot_schedules_due
ON snapshot_schedules(status, due_at);
CREATE TABLE IF NOT EXISTS import_rows(
  id TEXT PRIMARY KEY, batch_id TEXT NOT NULL, row_no INTEGER NOT NULL,
  payload_json TEXT NOT NULL, status TEXT NOT NULL, target_id TEXT, error TEXT,
  UNIQUE(batch_id, row_no),
  FOREIGN KEY(batch_id) REFERENCES import_batches(id) ON DELETE CASCADE);
CREATE TABLE IF NOT EXISTS rule_package_versions(
  id TEXT PRIMARY KEY, user_id TEXT NOT NULL, version_no INTEGER NOT NULL,
  diff_json TEXT NOT NULL, rule_ids_json TEXT NOT NULL, created_at TEXT NOT NULL,
  active INTEGER NOT NULL DEFAULT 0,
  UNIQUE(user_id, version_no),
  FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE);
CREATE TABLE IF NOT EXISTS evidence_events(
  id TEXT PRIMARY KEY, user_id TEXT NOT NULL, entity_type TEXT NOT NULL,
  entity_id TEXT NOT NULL, event_type TEXT NOT NULL, refs_json TEXT NOT NULL,
  payload_json TEXT NOT NULL, created_at TEXT NOT NULL,
  FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE);
CREATE INDEX IF NOT EXISTS idx_evidence_entity
ON evidence_events(user_id, entity_type, entity_id, created_at);
"""

LEGACY_PUBLICATION_SCHEDULE_MIGRATION = """
INSERT OR IGNORE INTO snapshot_schedules
(id,publication_id,tn,due_at,status,attempts,last_error,captured_at)
SELECT 'SS-MIG-' || id || '-T1',id,'T+1',datetime(published_at,'+1 day'),
CASE WHEN EXISTS(SELECT 1 FROM publication_snapshots s WHERE s.publication_id=publications.id AND s.tn='T+1')
THEN 'CAPTURED' ELSE 'PENDING' END,0,NULL,
(SELECT MAX(taken_at) FROM publication_snapshots s WHERE s.publication_id=publications.id AND s.tn='T+1')
FROM publications;
INSERT OR IGNORE INTO snapshot_schedules
(id,publication_id,tn,due_at,status,attempts,last_error,captured_at)
SELECT 'SS-MIG-' || id || '-T3',id,'T+3',datetime(published_at,'+3 day'),
CASE WHEN EXISTS(SELECT 1 FROM publication_snapshots s WHERE s.publication_id=publications.id AND s.tn='T+3')
THEN 'CAPTURED' ELSE 'PENDING' END,0,NULL,
(SELECT MAX(taken_at) FROM publication_snapshots s WHERE s.publication_id=publications.id AND s.tn='T+3')
FROM publications;
INSERT OR IGNORE INTO snapshot_schedules
(id,publication_id,tn,due_at,status,attempts,last_error,captured_at)
SELECT 'SS-MIG-' || id || '-T7',id,'T+7',datetime(published_at,'+7 day'),
CASE WHEN EXISTS(SELECT 1 FROM publication_snapshots s WHERE s.publication_id=publications.id AND s.tn='T+7')
THEN 'CAPTURED' ELSE 'PENDING' END,0,NULL,
(SELECT MAX(taken_at) FROM publication_snapshots s WHERE s.publication_id=publications.id AND s.tn='T+7')
FROM publications;
"""

WORKSPACE_RADAR_AND_INCREMENTAL_MIGRATION = """
CREATE TABLE IF NOT EXISTS workspace_users(
  user_id TEXT PRIMARY KEY, data_scope TEXT NOT NULL,
  FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE);
CREATE INDEX IF NOT EXISTS idx_workspace_users_scope ON workspace_users(data_scope,user_id);

ALTER TABLE benchmark_accounts ADD COLUMN next_cursor TEXT;
ALTER TABLE benchmark_accounts ADD COLUMN has_more INTEGER NOT NULL DEFAULT 1;
ALTER TABLE benchmark_accounts ADD COLUMN oldest_fetched_at TEXT;
ALTER TABLE benchmark_accounts ADD COLUMN last_success_at TEXT;
ALTER TABLE benchmark_accounts ADD COLUMN last_failure_at TEXT;
ALTER TABLE benchmark_accounts ADD COLUMN monitor_enabled INTEGER NOT NULL DEFAULT 1;
ALTER TABLE benchmark_accounts ADD COLUMN monitor_frequency_hours INTEGER NOT NULL DEFAULT 24;
ALTER TABLE benchmark_accounts ADD COLUMN next_run_at TEXT;
ALTER TABLE benchmark_accounts ADD COLUMN last_sync_kind TEXT;
CREATE INDEX IF NOT EXISTS idx_benchmarks_monitor_due
ON benchmark_accounts(user_id,monitor_enabled,next_run_at);

CREATE TABLE IF NOT EXISTS benchmark_sync_runs(
  id TEXT PRIMARY KEY, user_id TEXT NOT NULL, bm_id TEXT,
  kind TEXT NOT NULL, status TEXT NOT NULL, requested_count INTEGER NOT NULL,
  cursor_in TEXT, cursor_out TEXT, has_more INTEGER,
  new_count INTEGER NOT NULL DEFAULT 0, updated_count INTEGER NOT NULL DEFAULT 0,
  provider TEXT, error_json TEXT, started_at TEXT NOT NULL, finished_at TEXT,
  FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE,
  FOREIGN KEY(bm_id) REFERENCES benchmark_accounts(id) ON DELETE SET NULL);
CREATE INDEX IF NOT EXISTS idx_benchmark_runs_bm_started
ON benchmark_sync_runs(bm_id,started_at DESC);

CREATE TABLE IF NOT EXISTS benchmark_work_snapshots(
  id TEXT PRIMARY KEY, bm_id TEXT NOT NULL, work_id TEXT NOT NULL,
  metrics_json TEXT NOT NULL, captured_at TEXT NOT NULL,
  UNIQUE(work_id,captured_at),
  FOREIGN KEY(bm_id) REFERENCES benchmark_accounts(id) ON DELETE CASCADE,
  FOREIGN KEY(work_id) REFERENCES benchmark_works(id) ON DELETE CASCADE);
CREATE INDEX IF NOT EXISTS idx_benchmark_work_snapshots_work
ON benchmark_work_snapshots(work_id,captured_at DESC);

CREATE TABLE IF NOT EXISTS content_artifact_versions(
  id TEXT PRIMARY KEY, user_id TEXT NOT NULL, source_content_id TEXT NOT NULL,
  artifact_type TEXT NOT NULL, version_no INTEGER NOT NULL,
  content_json TEXT NOT NULL, sha256 TEXT NOT NULL, producer TEXT,
  source_refs_json TEXT NOT NULL, created_at TEXT NOT NULL,
  UNIQUE(source_content_id,artifact_type,version_no),
  FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE,
  FOREIGN KEY(source_content_id) REFERENCES source_contents(id) ON DELETE CASCADE);
CREATE INDEX IF NOT EXISTS idx_artifact_versions_source_type
ON content_artifact_versions(source_content_id,artifact_type,version_no DESC);

CREATE TABLE IF NOT EXISTS radar_sources(
  id TEXT PRIMARY KEY, user_id TEXT NOT NULL, kind TEXT NOT NULL,
  name TEXT NOT NULL, platform TEXT, config_json TEXT NOT NULL,
  enabled INTEGER NOT NULL DEFAULT 1, status TEXT NOT NULL DEFAULT 'idle',
  frequency_hours INTEGER NOT NULL DEFAULT 24, cursor TEXT,
  last_run_at TEXT, next_run_at TEXT, last_error TEXT, created_at TEXT NOT NULL,
  FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE);
CREATE INDEX IF NOT EXISTS idx_radar_sources_user_enabled
ON radar_sources(user_id,enabled,kind);

CREATE TABLE IF NOT EXISTS radar_fetch_runs(
  id TEXT PRIMARY KEY, user_id TEXT NOT NULL, source_id TEXT NOT NULL,
  status TEXT NOT NULL, fetched INTEGER NOT NULL DEFAULT 0,
  created INTEGER NOT NULL DEFAULT 0, deduplicated INTEGER NOT NULL DEFAULT 0,
  cursor_in TEXT, cursor_out TEXT, error_json TEXT,
  started_at TEXT NOT NULL, finished_at TEXT,
  FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE,
  FOREIGN KEY(source_id) REFERENCES radar_sources(id) ON DELETE CASCADE);
CREATE INDEX IF NOT EXISTS idx_radar_runs_source_started
ON radar_fetch_runs(source_id,started_at DESC);

ALTER TABLE signals ADD COLUMN radar_source_id TEXT;
"""

BENCHMARK_SIGNAL_LINK_MIGRATION = """
ALTER TABLE signals ADD COLUMN benchmark_work_id TEXT REFERENCES benchmark_works(id) ON DELETE SET NULL;
CREATE INDEX IF NOT EXISTS idx_signals_benchmark_work ON signals(benchmark_work_id);
UPDATE signals
SET benchmark_work_id=(
  SELECT w.id FROM benchmark_works w
  JOIN benchmark_accounts b ON b.id=w.bm_id
  WHERE b.user_id=signals.user_id AND w.title=signals.title
  ORDER BY w.first_seen_at DESC LIMIT 1
)
WHERE benchmark_work_id IS NULL
AND (id LIKE 'SG-BM-%' OR id LIKE 'SG-HOT-%');
"""

SIGNAL_CONTENT_LINK_MIGRATION = """
CREATE INDEX IF NOT EXISTS idx_signals_content_id ON signals(content_id);
CREATE INDEX IF NOT EXISTS idx_source_contents_user_signal
ON source_contents(user_id,signal_id,created_at DESC);
UPDATE signals
SET content_id=(
  SELECT sc.id FROM source_contents sc
  WHERE sc.user_id=signals.user_id AND sc.signal_id=signals.id
  ORDER BY sc.created_at DESC LIMIT 1
)
WHERE content_id IS NULL
AND EXISTS(
  SELECT 1 FROM source_contents sc
  WHERE sc.user_id=signals.user_id AND sc.signal_id=signals.id
);
"""

TEXT_QUALITY_AND_OWN_WORKS_MIGRATION = """
CREATE TABLE IF NOT EXISTS account_works(
  id TEXT PRIMARY KEY, account_id TEXT NOT NULL, video_id TEXT NOT NULL,
  title TEXT NOT NULL, cover TEXT, url TEXT, create_time INTEGER,
  plays INTEGER NOT NULL DEFAULT 0, likes INTEGER NOT NULL DEFAULT 0,
  comments INTEGER NOT NULL DEFAULT 0, collects INTEGER NOT NULL DEFAULT 0,
  shares INTEGER NOT NULL DEFAULT 0, duration INTEGER NOT NULL DEFAULT 0,
  first_seen_at TEXT NOT NULL, last_seen_at TEXT NOT NULL,
  UNIQUE(account_id,video_id),
  FOREIGN KEY(account_id) REFERENCES accounts(id) ON DELETE CASCADE);
CREATE INDEX IF NOT EXISTS idx_account_works_account_time
ON account_works(account_id,create_time DESC);
CREATE TABLE IF NOT EXISTS data_repairs(
  name TEXT PRIMARY KEY, details_json TEXT NOT NULL, applied_at TEXT NOT NULL);
"""

RADAR_CONNECTORS_AND_JOB_OPERATIONS_MIGRATION = """
DROP INDEX IF EXISTS idx_jobs_user_task_idempotency;
CREATE UNIQUE INDEX IF NOT EXISTS idx_jobs_active_operation
ON jobs(user_id,task_type,idempotency_key)
WHERE idempotency_key IS NOT NULL
AND status IN ('QUEUED','RUNNING','CANCEL_REQUESTED','WAITING_CONFIRMATION');
CREATE INDEX IF NOT EXISTS idx_jobs_user_operation_history
ON jobs(user_id,task_type,idempotency_key,created_at DESC);

ALTER TABLE radar_sources ADD COLUMN failure_count INTEGER NOT NULL DEFAULT 0;
ALTER TABLE radar_sources ADD COLUMN last_success_at TEXT;

CREATE TABLE IF NOT EXISTS radar_rules(
  id TEXT PRIMARY KEY, user_id TEXT NOT NULL, kind TEXT NOT NULL,
  name TEXT NOT NULL, config_json TEXT NOT NULL,
  enabled INTEGER NOT NULL DEFAULT 1, status TEXT NOT NULL DEFAULT 'idle',
  frequency_hours INTEGER NOT NULL DEFAULT 24,
  last_run_at TEXT, next_run_at TEXT, last_error TEXT, created_at TEXT NOT NULL,
  FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE);
CREATE INDEX IF NOT EXISTS idx_radar_rules_user_kind
ON radar_rules(user_id,kind,enabled);
"""

OWN_WORK_REVIEW_AND_CREATION_LINK_MIGRATION = """
ALTER TABLE account_works ADD COLUMN performance_label TEXT NOT NULL DEFAULT 'unrated';
ALTER TABLE account_works ADD COLUMN review_note TEXT NOT NULL DEFAULT '';
ALTER TABLE creation_projects ADD COLUMN source_content_id TEXT;
ALTER TABLE creation_projects ADD COLUMN analysis_id TEXT;
CREATE INDEX IF NOT EXISTS idx_account_works_account_label_time
ON account_works(account_id,performance_label,create_time DESC);
CREATE INDEX IF NOT EXISTS idx_projects_source_content
ON creation_projects(user_id,source_content_id,created_at DESC);
"""

TARGET_MODE_LIFECYCLE_MIGRATION = """
UPDATE profile_versions AS current
SET active=0
WHERE current.active=1
AND EXISTS(
  SELECT 1 FROM profile_versions AS newer
  WHERE newer.user_id=current.user_id AND newer.active=1
  AND (COALESCE(newer.created_at,'') > COALESCE(current.created_at,'')
       OR (COALESCE(newer.created_at,'') = COALESCE(current.created_at,'') AND newer.id > current.id))
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_profile_versions_one_active
ON profile_versions(user_id) WHERE active=1;

ALTER TABLE profile_field_candidates ADD COLUMN edited_value TEXT;
ALTER TABLE profile_field_candidates ADD COLUMN decision_note TEXT NOT NULL DEFAULT '';
ALTER TABLE profile_field_candidates ADD COLUMN decided_at TEXT;

CREATE TABLE IF NOT EXISTS interview_archive_revisions(
  id TEXT PRIMARY KEY, session_id TEXT NOT NULL, user_id TEXT NOT NULL,
  revision_no INTEGER NOT NULL, title TEXT NOT NULL, summary TEXT NOT NULL,
  corrected_transcript TEXT NOT NULL, tags_json TEXT NOT NULL, notes TEXT NOT NULL,
  created_by TEXT NOT NULL, created_at TEXT NOT NULL,
  UNIQUE(session_id,revision_no),
  FOREIGN KEY(session_id) REFERENCES interview_sessions(id) ON DELETE CASCADE,
  FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_interview_archives_user_created
ON interview_archive_revisions(user_id,created_at DESC);
CREATE TABLE IF NOT EXISTS profile_change_sets(
  id TEXT PRIMARY KEY, user_id TEXT NOT NULL, session_id TEXT,
  base_profile_version_id TEXT, status TEXT NOT NULL, changes_json TEXT NOT NULL,
  preview_json TEXT NOT NULL, created_at TEXT NOT NULL, confirmed_at TEXT,
  FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE,
  FOREIGN KEY(session_id) REFERENCES interview_sessions(id) ON DELETE SET NULL,
  FOREIGN KEY(base_profile_version_id) REFERENCES profile_versions(id) ON DELETE SET NULL
);

ALTER TABLE predictions ADD COLUMN version_no INTEGER NOT NULL DEFAULT 1;
ALTER TABLE predictions ADD COLUMN draft_version_id TEXT;
ALTER TABLE predictions ADD COLUMN draft_hash TEXT NOT NULL DEFAULT '';
ALTER TABLE predictions ADD COLUMN rubric_version TEXT NOT NULL DEFAULT 'workspace-v1';
ALTER TABLE predictions ADD COLUMN model TEXT NOT NULL DEFAULT '';
ALTER TABLE predictions ADD COLUMN provider TEXT NOT NULL DEFAULT '';
ALTER TABLE predictions ADD COLUMN details_json TEXT NOT NULL DEFAULT '{}';
ALTER TABLE predictions ADD COLUMN integrity_hash TEXT NOT NULL DEFAULT '';
ALTER TABLE predictions ADD COLUMN blind_status TEXT NOT NULL DEFAULT 'blind';
ALTER TABLE predictions ADD COLUMN override_json TEXT NOT NULL DEFAULT '{}';
ALTER TABLE predictions ADD COLUMN created_at TEXT;
ALTER TABLE predictions ADD COLUMN supersedes_id TEXT;
UPDATE predictions SET created_at=COALESCE(created_at,locked_at,datetime('now'));
CREATE UNIQUE INDEX IF NOT EXISTS idx_predictions_project_version
ON predictions(project_id,version_no);
CREATE INDEX IF NOT EXISTS idx_predictions_project_created
ON predictions(project_id,created_at DESC);

ALTER TABLE publication_snapshots ADD COLUMN shares INTEGER NOT NULL DEFAULT 0;
ALTER TABLE publication_snapshots ADD COLUMN views INTEGER NOT NULL DEFAULT 0;
ALTER TABLE publication_snapshots ADD COLUMN follows INTEGER NOT NULL DEFAULT 0;
ALTER TABLE publication_snapshots ADD COLUMN avg_watch_seconds REAL;
ALTER TABLE publication_snapshots ADD COLUMN completion_rate REAL;
ALTER TABLE publication_snapshots ADD COLUMN metrics_json TEXT NOT NULL DEFAULT '{}';
CREATE TABLE IF NOT EXISTS prediction_observations(
  id TEXT PRIMARY KEY, user_id TEXT NOT NULL, prediction_id TEXT NOT NULL,
  publication_id TEXT NOT NULL, snapshot_id TEXT, observation_type TEXT NOT NULL,
  content_json TEXT NOT NULL, created_at TEXT NOT NULL,
  FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE,
  FOREIGN KEY(prediction_id) REFERENCES predictions(id) ON DELETE CASCADE,
  FOREIGN KEY(publication_id) REFERENCES publications(id) ON DELETE CASCADE,
  FOREIGN KEY(snapshot_id) REFERENCES publication_snapshots(id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS operation_portraits(
  id TEXT PRIMARY KEY, user_id TEXT NOT NULL, window_days INTEGER NOT NULL,
  metrics_json TEXT NOT NULL, diagnosis_json TEXT NOT NULL, source_hash TEXT NOT NULL,
  generated_at TEXT NOT NULL,
  FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_operation_portraits_user_window
ON operation_portraits(user_id,window_days,generated_at DESC);
CREATE TABLE IF NOT EXISTS operation_plan_drafts(
  id TEXT PRIMARY KEY, user_id TEXT NOT NULL, week_start TEXT NOT NULL,
  portrait_id TEXT, status TEXT NOT NULL, plan_json TEXT NOT NULL,
  model TEXT NOT NULL DEFAULT '', provider TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL, approved_at TEXT,
  FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE,
  FOREIGN KEY(portrait_id) REFERENCES operation_portraits(id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS visual_profiles(
  id TEXT PRIMARY KEY, user_id TEXT NOT NULL UNIQUE, person_image_path TEXT,
  logo_path TEXT, brand_json TEXT NOT NULL, default_template_id TEXT,
  updated_at TEXT NOT NULL,
  FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS cover_templates(
  id TEXT PRIMARY KEY, user_id TEXT NOT NULL, name TEXT NOT NULL,
  ratio TEXT NOT NULL, template_json TEXT NOT NULL, prompt_template TEXT NOT NULL,
  builtin INTEGER NOT NULL DEFAULT 0, active INTEGER NOT NULL DEFAULT 1,
  created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
  FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_cover_templates_user_active
ON cover_templates(user_id,active,created_at DESC);
CREATE TABLE IF NOT EXISTS cover_projects(
  id TEXT PRIMARY KEY, user_id TEXT NOT NULL, creation_project_id TEXT,
  draft_version_id TEXT, template_id TEXT, fields_json TEXT NOT NULL,
  prompt TEXT NOT NULL, preview_path TEXT, status TEXT NOT NULL,
  created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
  FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE,
  FOREIGN KEY(creation_project_id) REFERENCES creation_projects(id) ON DELETE SET NULL,
  FOREIGN KEY(draft_version_id) REFERENCES draft_versions(id) ON DELETE SET NULL,
  FOREIGN KEY(template_id) REFERENCES cover_templates(id) ON DELETE SET NULL
);
"""

VISUAL_FACTORY_AND_WRITING_QUALITY_MIGRATION = """
ALTER TABLE draft_versions ADD COLUMN style_mode TEXT NOT NULL DEFAULT 'legacy';
ALTER TABLE draft_versions ADD COLUMN model TEXT NOT NULL DEFAULT '';
ALTER TABLE draft_versions ADD COLUMN provider TEXT NOT NULL DEFAULT '';
ALTER TABLE draft_versions ADD COLUMN quality_json TEXT NOT NULL DEFAULT '{}';

ALTER TABLE cover_projects ADD COLUMN content_type TEXT NOT NULL DEFAULT 'cover';
ALTER TABLE cover_projects ADD COLUMN ratio TEXT NOT NULL DEFAULT '3:4';
ALTER TABLE cover_projects ADD COLUMN provider TEXT NOT NULL DEFAULT 'local_compositor';
ALTER TABLE cover_projects ADD COLUMN model TEXT NOT NULL DEFAULT '';
ALTER TABLE cover_projects ADD COLUMN generation_mode TEXT NOT NULL DEFAULT 'local_template';
ALTER TABLE cover_projects ADD COLUMN cost REAL NOT NULL DEFAULT 0;
ALTER TABLE cover_projects ADD COLUMN source_image_path TEXT;
ALTER TABLE cover_projects ADD COLUMN generation_json TEXT NOT NULL DEFAULT '{}';
ALTER TABLE cover_projects ADD COLUMN error_message TEXT NOT NULL DEFAULT '';

CREATE TABLE IF NOT EXISTS visual_assets(
  id TEXT PRIMARY KEY, user_id TEXT NOT NULL, kind TEXT NOT NULL,
  name TEXT NOT NULL, path TEXT NOT NULL, metadata_json TEXT NOT NULL DEFAULT '{}',
  active INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
  FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_visual_assets_user_kind
ON visual_assets(user_id,kind,active,created_at DESC);
CREATE INDEX IF NOT EXISTS idx_cover_projects_user_created
ON cover_projects(user_id,created_at DESC);
"""

VISUAL_HISTORY_LIFECYCLE_MIGRATION = """
ALTER TABLE cover_projects ADD COLUMN deleted_at TEXT;
CREATE INDEX IF NOT EXISTS idx_cover_projects_user_visible
ON cover_projects(user_id,deleted_at,created_at DESC);
"""

UNIFIED_ASSET_OPERATIONS_VISUAL_MIGRATION = """
CREATE TABLE IF NOT EXISTS asset_records(
  id TEXT PRIMARY KEY, user_id TEXT NOT NULL,
  entity_type TEXT NOT NULL, entity_id TEXT NOT NULL,
  asset_type TEXT NOT NULL, title TEXT NOT NULL,
  platform TEXT NOT NULL DEFAULT '', source_kind TEXT NOT NULL DEFAULT '',
  status TEXT NOT NULL DEFAULT 'ready', summary TEXT NOT NULL DEFAULT '',
  search_text TEXT NOT NULL DEFAULT '', metadata_json TEXT NOT NULL DEFAULT '{}',
  favorite INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL, deleted_at TEXT,
  UNIQUE(user_id,entity_type,entity_id,asset_type),
  FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_asset_records_user_type_updated
ON asset_records(user_id,asset_type,deleted_at,updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_asset_records_user_platform
ON asset_records(user_id,platform,deleted_at);

CREATE TABLE IF NOT EXISTS asset_relations(
  id TEXT PRIMARY KEY, user_id TEXT NOT NULL,
  from_asset_id TEXT NOT NULL, to_asset_id TEXT NOT NULL,
  relation_type TEXT NOT NULL, metadata_json TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL,
  UNIQUE(user_id,from_asset_id,to_asset_id,relation_type),
  FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE,
  FOREIGN KEY(from_asset_id) REFERENCES asset_records(id) ON DELETE CASCADE,
  FOREIGN KEY(to_asset_id) REFERENCES asset_records(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_asset_relations_from ON asset_relations(user_id,from_asset_id);
CREATE INDEX IF NOT EXISTS idx_asset_relations_to ON asset_relations(user_id,to_asset_id);

CREATE TABLE IF NOT EXISTS asset_collections(
  id TEXT PRIMARY KEY, user_id TEXT NOT NULL, name TEXT NOT NULL,
  description TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
  UNIQUE(user_id,name),
  FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS asset_collection_items(
  collection_id TEXT NOT NULL, asset_id TEXT NOT NULL,
  position INTEGER NOT NULL DEFAULT 0, added_at TEXT NOT NULL,
  PRIMARY KEY(collection_id,asset_id),
  FOREIGN KEY(collection_id) REFERENCES asset_collections(id) ON DELETE CASCADE,
  FOREIGN KEY(asset_id) REFERENCES asset_records(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS account_work_source_links(
  account_work_id TEXT PRIMARY KEY, user_id TEXT NOT NULL,
  source_content_id TEXT NOT NULL, match_method TEXT NOT NULL,
  confidence REAL NOT NULL DEFAULT 1, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
  FOREIGN KEY(account_work_id) REFERENCES account_works(id) ON DELETE CASCADE,
  FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE,
  FOREIGN KEY(source_content_id) REFERENCES source_contents(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_account_work_source_user
ON account_work_source_links(user_id,source_content_id);

CREATE TABLE IF NOT EXISTS visual_batches(
  id TEXT PRIMARY KEY, user_id TEXT NOT NULL, title TEXT NOT NULL,
  creation_project_id TEXT, draft_version_id TEXT, template_id TEXT,
  content_type TEXT NOT NULL, ratio TEXT NOT NULL,
  generation_mode TEXT NOT NULL, quantity INTEGER NOT NULL,
  status TEXT NOT NULL, request_json TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL, updated_at TEXT NOT NULL, deleted_at TEXT,
  FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE,
  FOREIGN KEY(creation_project_id) REFERENCES creation_projects(id) ON DELETE SET NULL,
  FOREIGN KEY(draft_version_id) REFERENCES draft_versions(id) ON DELETE SET NULL,
  FOREIGN KEY(template_id) REFERENCES cover_templates(id) ON DELETE SET NULL
);
CREATE INDEX IF NOT EXISTS idx_visual_batches_user_visible
ON visual_batches(user_id,deleted_at,created_at DESC);
ALTER TABLE cover_projects ADD COLUMN visual_batch_id TEXT REFERENCES visual_batches(id) ON DELETE SET NULL;
ALTER TABLE cover_projects ADD COLUMN item_index INTEGER NOT NULL DEFAULT 1;
ALTER TABLE cover_projects ADD COLUMN page_role TEXT NOT NULL DEFAULT 'cover';
CREATE INDEX IF NOT EXISTS idx_cover_projects_batch_item
ON cover_projects(visual_batch_id,item_index);

ALTER TABLE cover_templates ADD COLUMN template_type TEXT NOT NULL DEFAULT 'cover';
ALTER TABLE cover_templates ADD COLUMN layout_version INTEGER NOT NULL DEFAULT 2;

CREATE TABLE IF NOT EXISTS operation_context_snapshots(
  id TEXT PRIMARY KEY, user_id TEXT NOT NULL, window_days INTEGER NOT NULL,
  source_hash TEXT NOT NULL, context_json TEXT NOT NULL,
  coverage_json TEXT NOT NULL, created_at TEXT NOT NULL,
  FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_operation_context_user_created
ON operation_context_snapshots(user_id,created_at DESC);
ALTER TABLE operation_plan_drafts ADD COLUMN context_snapshot_id TEXT REFERENCES operation_context_snapshots(id) ON DELETE SET NULL;
"""

VISUAL_SELECTION_AND_REVISION_MIGRATION = """
ALTER TABLE visual_batches ADD COLUMN selected_cover_id TEXT REFERENCES cover_projects(id) ON DELETE SET NULL;
ALTER TABLE cover_projects ADD COLUMN revision_no INTEGER NOT NULL DEFAULT 1;
ALTER TABLE cover_projects ADD COLUMN replaces_cover_id TEXT REFERENCES cover_projects(id) ON DELETE SET NULL;
CREATE INDEX IF NOT EXISTS idx_cover_projects_replaces ON cover_projects(replaces_cover_id);
"""

ASSET_ADVISOR_DIRECTION_LIFECYCLE_MIGRATION = """
CREATE TABLE IF NOT EXISTS creation_direction_runs(
  id TEXT PRIMARY KEY, user_id TEXT NOT NULL, project_id TEXT NOT NULL,
  version_no INTEGER NOT NULL, context_hash TEXT NOT NULL,
  candidates_json TEXT NOT NULL, model TEXT NOT NULL DEFAULT '',
  provider TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL,
  UNIQUE(project_id,version_no),
  FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE,
  FOREIGN KEY(project_id) REFERENCES creation_projects(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_direction_runs_project_version
ON creation_direction_runs(project_id,version_no DESC);

CREATE TABLE IF NOT EXISTS creation_direction_decisions(
  id TEXT PRIMARY KEY, user_id TEXT NOT NULL, project_id TEXT NOT NULL,
  run_id TEXT, candidate_key TEXT NOT NULL DEFAULT '',
  direction_text TEXT NOT NULL, candidate_json TEXT NOT NULL DEFAULT '{}',
  reason TEXT NOT NULL DEFAULT '', active INTEGER NOT NULL DEFAULT 1,
  created_at TEXT NOT NULL,
  FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE,
  FOREIGN KEY(project_id) REFERENCES creation_projects(id) ON DELETE CASCADE,
  FOREIGN KEY(run_id) REFERENCES creation_direction_runs(id) ON DELETE SET NULL
);
CREATE INDEX IF NOT EXISTS idx_direction_decisions_project_active
ON creation_direction_decisions(project_id,active,created_at DESC);

ALTER TABLE creation_projects ADD COLUMN selected_direction_decision_id TEXT
REFERENCES creation_direction_decisions(id) ON DELETE SET NULL;
ALTER TABLE draft_versions ADD COLUMN direction_decision_id TEXT
REFERENCES creation_direction_decisions(id) ON DELETE SET NULL;

ALTER TABLE operation_plan_drafts ADD COLUMN cycle_hash TEXT NOT NULL DEFAULT '';
ALTER TABLE operation_plan_drafts ADD COLUMN replaced_by_id TEXT
REFERENCES operation_plan_drafts(id) ON DELETE SET NULL;
ALTER TABLE operation_plan_drafts ADD COLUMN stale_at TEXT;
ALTER TABLE operation_plan_drafts ADD COLUMN archived_at TEXT;
CREATE INDEX IF NOT EXISTS idx_operation_plans_user_status_week
ON operation_plan_drafts(user_id,status,week_start DESC,created_at DESC);
"""

ADVISOR_PLAN_TO_CREATION_MIGRATION = """
ALTER TABLE content_tasks ADD COLUMN operation_plan_draft_id TEXT
REFERENCES operation_plan_drafts(id) ON DELETE SET NULL;
ALTER TABLE content_tasks ADD COLUMN operation_plan_item_index INTEGER;
ALTER TABLE content_tasks ADD COLUMN source_json TEXT NOT NULL DEFAULT '{}';
ALTER TABLE content_tasks ADD COLUMN creation_started_at TEXT;
UPDATE content_tasks SET status='SELECTED' WHERE status='TODO';
CREATE INDEX IF NOT EXISTS idx_content_tasks_plan_item
ON content_tasks(user_id,operation_plan_draft_id,operation_plan_item_index);
"""

ADVISOR_PLAN_LEGACY_LINK_MIGRATION = """
UPDATE content_tasks AS task
SET operation_plan_draft_id = (
      SELECT plan.id
      FROM operation_plan_drafts AS plan,
           json_each(plan.plan_json, '$.items') AS item
      WHERE plan.user_id=task.user_id
        AND plan.status='APPROVED'
        AND task.title=COALESCE(NULLIF(json_extract(item.value,'$.title'),''),
                                'AI建议选题' || (CAST(item.key AS INTEGER)+1))
        AND task.scheduled_date=COALESCE(NULLIF(json_extract(item.value,'$.date'),''),plan.week_start)
      ORDER BY COALESCE(plan.approved_at,plan.created_at) DESC
      LIMIT 1
    ),
    operation_plan_item_index = (
      SELECT CAST(item.key AS INTEGER)
      FROM operation_plan_drafts AS plan,
           json_each(plan.plan_json, '$.items') AS item
      WHERE plan.user_id=task.user_id
        AND plan.status='APPROVED'
        AND task.title=COALESCE(NULLIF(json_extract(item.value,'$.title'),''),
                                'AI建议选题' || (CAST(item.key AS INTEGER)+1))
        AND task.scheduled_date=COALESCE(NULLIF(json_extract(item.value,'$.date'),''),plan.week_start)
      ORDER BY COALESCE(plan.approved_at,plan.created_at) DESC
      LIMIT 1
    )
WHERE task.operation_plan_draft_id IS NULL
  AND EXISTS (
      SELECT 1
      FROM operation_plan_drafts AS plan,
           json_each(plan.plan_json, '$.items') AS item
      WHERE plan.user_id=task.user_id
        AND plan.status='APPROVED'
        AND task.title=COALESCE(NULLIF(json_extract(item.value,'$.title'),''),
                                'AI建议选题' || (CAST(item.key AS INTEGER)+1))
        AND task.scheduled_date=COALESCE(NULLIF(json_extract(item.value,'$.date'),''),plan.week_start)
  );
"""

CREATION_FINALIZATION_AND_EXTERNAL_PUBLICATION_MIGRATION = """
ALTER TABLE content_tasks ADD COLUMN finalized_at TEXT;
ALTER TABLE content_tasks ADD COLUMN final_draft_version_id TEXT
REFERENCES draft_versions(id) ON DELETE SET NULL;
ALTER TABLE publications ADD COLUMN entry_mode TEXT NOT NULL DEFAULT 'planned';
ALTER TABLE publications ADD COLUMN source_json TEXT NOT NULL DEFAULT '{}';

UPDATE content_tasks AS task
SET status='READY_TO_PUBLISH',
    finalized_at=COALESCE(finalized_at,(
      SELECT prediction.locked_at FROM predictions AS prediction
      WHERE prediction.project_id=task.project_id AND prediction.locked=1
      ORDER BY prediction.version_no DESC LIMIT 1
    )),
    final_draft_version_id=(
      SELECT prediction.draft_version_id FROM predictions AS prediction
      WHERE prediction.project_id=task.project_id AND prediction.locked=1
      ORDER BY prediction.version_no DESC LIMIT 1
    )
WHERE task.status IN ('SELECTED','OUTLINING','DRAFTING','PRODUCING')
  AND EXISTS (
    SELECT 1 FROM predictions AS prediction
    WHERE prediction.project_id=task.project_id AND prediction.locked=1
      AND prediction.draft_version_id=(
        SELECT draft.id FROM draft_versions AS draft
        WHERE draft.project_id=task.project_id
        ORDER BY draft.version_no DESC LIMIT 1
      )
  );

UPDATE creation_projects AS project
SET status='READY_TO_PUBLISH'
WHERE EXISTS (
  SELECT 1 FROM predictions AS prediction
  WHERE prediction.project_id=project.id AND prediction.locked=1
    AND prediction.draft_version_id=(
      SELECT draft.id FROM draft_versions AS draft
      WHERE draft.project_id=project.id
      ORDER BY draft.version_no DESC LIMIT 1
    )
);
CREATE INDEX IF NOT EXISTS idx_publications_user_entry_mode
ON publications(user_id,entry_mode,published_at DESC);
"""

CONTENT_LIFECYCLE_CONTINUITY_MIGRATION = """
ALTER TABLE retro_reports ADD COLUMN version_no INTEGER NOT NULL DEFAULT 1;
ALTER TABLE retro_reports ADD COLUMN stage TEXT NOT NULL DEFAULT 'INTERIM';
ALTER TABLE retro_reports ADD COLUMN snapshot_hash TEXT NOT NULL DEFAULT '';

-- A creation project owns one active planning card.  Older builds could create
-- duplicates when “加入计划” was clicked repeatedly. Preserve those rows as
-- cancelled history instead of deleting them.
WITH ranked AS (
  SELECT id,
         ROW_NUMBER() OVER (
           PARTITION BY user_id,project_id
           ORDER BY
             CASE status
               WHEN 'READY_TO_PUBLISH' THEN 7
               WHEN 'PRODUCING' THEN 6
               WHEN 'DRAFTING' THEN 5
               WHEN 'OUTLINING' THEN 4
               WHEN 'SELECTED' THEN 3
               WHEN 'CANDIDATE' THEN 2
               WHEN 'PAUSED' THEN 1
               ELSE 0
             END DESC,
             rowid ASC
         ) AS rn
  FROM content_tasks
  WHERE project_id IS NOT NULL
    AND status IN ('CANDIDATE','SELECTED','OUTLINING','DRAFTING','PRODUCING',
                   'READY_TO_PUBLISH','PAUSED')
)
UPDATE content_tasks
SET status='CANCELLED',
    source_json=json_set(COALESCE(NULLIF(source_json,''),'{}'),
                         '$.lifecycle_repair','duplicate_project_task')
WHERE id IN (SELECT id FROM ranked WHERE rn>1);

-- Reconcile pre-v22 project/task pairs to the furthest explicit creation step.
UPDATE creation_projects
SET status='OUTLINING'
WHERE status='CANDIDATE' AND EXISTS (
  SELECT 1 FROM content_tasks t WHERE t.project_id=creation_projects.id
    AND t.status='OUTLINING'
);
UPDATE content_tasks
SET status='DRAFTING'
WHERE status IN ('CANDIDATE','SELECTED','OUTLINING') AND EXISTS (
  SELECT 1 FROM creation_projects p WHERE p.id=content_tasks.project_id
    AND p.status='DRAFTING'
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_content_tasks_one_active_project
ON content_tasks(user_id,project_id)
WHERE project_id IS NOT NULL
  AND status IN ('CANDIDATE','SELECTED','OUTLINING','DRAFTING','PRODUCING',
                 'READY_TO_PUBLISH','PAUSED');
CREATE UNIQUE INDEX IF NOT EXISTS idx_retro_reports_publication_snapshot
ON retro_reports(publication_id,snapshot_hash)
WHERE snapshot_hash<>'';
CREATE INDEX IF NOT EXISTS idx_retro_reports_publication_version
ON retro_reports(publication_id,version_no DESC,created_at DESC);
"""

FINALIZED_PROJECT_TASK_REPAIR_MIGRATION = """
INSERT INTO content_tasks(
  id,user_id,project_id,title,status,platform,scheduled_date,estimate_h,priority,
  badges_json,source_json,creation_started_at,finalized_at,final_draft_version_id
)
SELECT 'CT-MIG-' || project.id,
       project.user_id,
       project.id,
       project.title,
       'READY_TO_PUBLISH',
       COALESCE(NULLIF(draft.platform,''),'待确认'),
       date('now'),
       1.5,
       'high',
       json_array('创作工坊','已确认定稿'),
       json_object('kind','finalization_repair','project_id',project.id),
       project.created_at,
       prediction.locked_at,
       draft.id
FROM creation_projects project
JOIN predictions prediction ON prediction.id=(
  SELECT p2.id FROM predictions p2
  WHERE p2.project_id=project.id AND p2.locked=1
  ORDER BY p2.version_no DESC,p2.locked_at DESC LIMIT 1
)
JOIN draft_versions draft ON draft.id=prediction.draft_version_id
WHERE project.status='READY_TO_PUBLISH'
  AND draft.id=(
    SELECT d2.id FROM draft_versions d2 WHERE d2.project_id=project.id
    ORDER BY d2.version_no DESC LIMIT 1
  )
  AND NOT EXISTS (
    SELECT 1 FROM content_tasks task
    WHERE task.user_id=project.user_id AND task.project_id=project.id
      AND task.status<>'CANCELLED'
  );
"""

VISUAL_DOCUMENT_WORKFLOW_MIGRATION = """
CREATE TABLE IF NOT EXISTS visual_prompt_presets(
  id TEXT PRIMARY KEY, user_id TEXT NOT NULL,
  name TEXT NOT NULL, scope TEXT NOT NULL,
  template_text TEXT NOT NULL, negative_prompt TEXT NOT NULL DEFAULT '',
  builtin INTEGER NOT NULL DEFAULT 0, active INTEGER NOT NULL DEFAULT 1,
  created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
  FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_visual_prompt_presets_user_scope
ON visual_prompt_presets(user_id,scope,active,updated_at DESC);

CREATE TABLE IF NOT EXISTS visual_prompt_runs(
  id TEXT PRIMARY KEY, user_id TEXT NOT NULL,
  creation_project_id TEXT NOT NULL, draft_version_id TEXT NOT NULL,
  output_kind TEXT NOT NULL, prompt_mode TEXT NOT NULL,
  preset_id TEXT, source_text_hash TEXT NOT NULL,
  raw_user_prompt TEXT NOT NULL DEFAULT '',
  optimized_prompt TEXT NOT NULL DEFAULT '',
  compiled_prompt TEXT NOT NULL,
  negative_prompt TEXT NOT NULL DEFAULT '',
  source_snapshot_json TEXT NOT NULL DEFAULT '{}',
  provider TEXT NOT NULL DEFAULT '', model TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL,
  FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE,
  FOREIGN KEY(creation_project_id) REFERENCES creation_projects(id) ON DELETE CASCADE,
  FOREIGN KEY(draft_version_id) REFERENCES draft_versions(id) ON DELETE CASCADE,
  FOREIGN KEY(preset_id) REFERENCES visual_prompt_presets(id) ON DELETE SET NULL
);
CREATE INDEX IF NOT EXISTS idx_visual_prompt_runs_project_created
ON visual_prompt_runs(user_id,creation_project_id,created_at DESC);

CREATE TABLE IF NOT EXISTS graphic_documents(
  id TEXT PRIMARY KEY, user_id TEXT NOT NULL,
  creation_project_id TEXT NOT NULL, draft_version_id TEXT NOT NULL,
  title TEXT NOT NULL, platform TEXT NOT NULL DEFAULT '',
  ratio TEXT NOT NULL DEFAULT '3:4', theme_key TEXT NOT NULL DEFAULT 'editorial',
  generation_mode TEXT NOT NULL DEFAULT 'ai_hybrid',
  prompt_run_id TEXT, status TEXT NOT NULL DEFAULT 'DRAFT',
  version_no INTEGER NOT NULL DEFAULT 1, source_hash TEXT NOT NULL,
  selected_export_id TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
  deleted_at TEXT,
  FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE,
  FOREIGN KEY(creation_project_id) REFERENCES creation_projects(id) ON DELETE CASCADE,
  FOREIGN KEY(draft_version_id) REFERENCES draft_versions(id) ON DELETE CASCADE,
  FOREIGN KEY(prompt_run_id) REFERENCES visual_prompt_runs(id) ON DELETE SET NULL
);
CREATE INDEX IF NOT EXISTS idx_graphic_documents_user_updated
ON graphic_documents(user_id,deleted_at,updated_at DESC);

CREATE TABLE IF NOT EXISTS graphic_pages(
  id TEXT PRIMARY KEY, user_id TEXT NOT NULL, document_id TEXT NOT NULL,
  page_index INTEGER NOT NULL, role TEXT NOT NULL,
  title TEXT NOT NULL, body TEXT NOT NULL DEFAULT '',
  bullets_json TEXT NOT NULL DEFAULT '[]', layout_key TEXT NOT NULL DEFAULT 'editorial',
  visual_prompt TEXT NOT NULL DEFAULT '', locked INTEGER NOT NULL DEFAULT 0,
  status TEXT NOT NULL DEFAULT 'DRAFT', selected_revision_id TEXT,
  created_at TEXT NOT NULL, updated_at TEXT NOT NULL, deleted_at TEXT,
  UNIQUE(document_id,page_index),
  FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE,
  FOREIGN KEY(document_id) REFERENCES graphic_documents(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_graphic_pages_document_index
ON graphic_pages(document_id,deleted_at,page_index);

CREATE TABLE IF NOT EXISTS graphic_page_revisions(
  id TEXT PRIMARY KEY, user_id TEXT NOT NULL, page_id TEXT NOT NULL,
  revision_no INTEGER NOT NULL, image_path TEXT,
  prompt TEXT NOT NULL DEFAULT '', provider TEXT NOT NULL DEFAULT '',
  model TEXT NOT NULL DEFAULT '', generation_mode TEXT NOT NULL,
  cost REAL NOT NULL DEFAULT 0, request_json TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL, deleted_at TEXT,
  UNIQUE(page_id,revision_no),
  FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE,
  FOREIGN KEY(page_id) REFERENCES graphic_pages(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_graphic_revisions_page_version
ON graphic_page_revisions(page_id,deleted_at,revision_no DESC);

CREATE TABLE IF NOT EXISTS visual_exports(
  id TEXT PRIMARY KEY, user_id TEXT NOT NULL, document_id TEXT NOT NULL,
  export_path TEXT, manifest_json TEXT NOT NULL DEFAULT '{}',
  status TEXT NOT NULL DEFAULT 'READY', created_at TEXT NOT NULL, deleted_at TEXT,
  FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE,
  FOREIGN KEY(document_id) REFERENCES graphic_documents(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS visual_deliverables(
  id TEXT PRIMARY KEY, user_id TEXT NOT NULL,
  creation_project_id TEXT NOT NULL, draft_version_id TEXT NOT NULL,
  kind TEXT NOT NULL, entity_id TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'SELECTED', selected INTEGER NOT NULL DEFAULT 1,
  metadata_json TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
  UNIQUE(user_id,kind,entity_id),
  FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE,
  FOREIGN KEY(creation_project_id) REFERENCES creation_projects(id) ON DELETE CASCADE,
  FOREIGN KEY(draft_version_id) REFERENCES draft_versions(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_visual_deliverables_project
ON visual_deliverables(user_id,creation_project_id,selected,updated_at DESC);

ALTER TABLE creation_projects ADD COLUMN visual_status TEXT NOT NULL DEFAULT 'NOT_STARTED';
ALTER TABLE creation_projects ADD COLUMN visual_deliverable_id TEXT;
ALTER TABLE content_tasks ADD COLUMN visual_status TEXT NOT NULL DEFAULT 'NOT_STARTED';
ALTER TABLE content_tasks ADD COLUMN visual_deliverable_id TEXT;
ALTER TABLE publications ADD COLUMN visual_deliverable_id TEXT;
"""

DRAFT_QUALITY_GATE_MIGRATION = """
CREATE TABLE IF NOT EXISTS draft_assessments(
  id TEXT PRIMARY KEY, user_id TEXT NOT NULL,
  project_id TEXT NOT NULL, draft_version_id TEXT NOT NULL,
  version_no INTEGER NOT NULL, rubric_version TEXT NOT NULL DEFAULT 'workspace-v1',
  composite_score REAL NOT NULL DEFAULT 0,
  dimensions_json TEXT NOT NULL DEFAULT '[]',
  issues_json TEXT NOT NULL DEFAULT '[]',
  strengths_json TEXT NOT NULL DEFAULT '[]',
  summary TEXT NOT NULL DEFAULT '', source_hash TEXT NOT NULL,
  provider TEXT NOT NULL DEFAULT '', model TEXT NOT NULL DEFAULT '',
  status TEXT NOT NULL DEFAULT 'ACTIVE', created_at TEXT NOT NULL,
  UNIQUE(project_id,draft_version_id,version_no),
  FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE,
  FOREIGN KEY(project_id) REFERENCES creation_projects(id) ON DELETE CASCADE,
  FOREIGN KEY(draft_version_id) REFERENCES draft_versions(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_draft_assessments_project_created
ON draft_assessments(user_id,project_id,created_at DESC);
CREATE INDEX IF NOT EXISTS idx_draft_assessments_draft_version
ON draft_assessments(draft_version_id,version_no DESC);

CREATE TABLE IF NOT EXISTS draft_revision_runs(
  id TEXT PRIMARY KEY, user_id TEXT NOT NULL,
  project_id TEXT NOT NULL, assessment_id TEXT NOT NULL,
  source_draft_version_id TEXT NOT NULL, target_draft_version_id TEXT NOT NULL,
  selected_issue_ids_json TEXT NOT NULL DEFAULT '[]',
  instructions TEXT NOT NULL DEFAULT '', change_log_json TEXT NOT NULL DEFAULT '[]',
  provider TEXT NOT NULL DEFAULT '', model TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL,
  FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE,
  FOREIGN KEY(project_id) REFERENCES creation_projects(id) ON DELETE CASCADE,
  FOREIGN KEY(assessment_id) REFERENCES draft_assessments(id) ON DELETE CASCADE,
  FOREIGN KEY(source_draft_version_id) REFERENCES draft_versions(id) ON DELETE CASCADE,
  FOREIGN KEY(target_draft_version_id) REFERENCES draft_versions(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_draft_revision_runs_project_created
ON draft_revision_runs(user_id,project_id,created_at DESC);

ALTER TABLE creation_projects ADD COLUMN candidate_draft_version_id TEXT
REFERENCES draft_versions(id) ON DELETE SET NULL;
ALTER TABLE creation_projects ADD COLUMN candidate_confirmed_at TEXT;
ALTER TABLE creation_projects ADD COLUMN quality_gate_status TEXT NOT NULL DEFAULT 'NOT_ASSESSED';

UPDATE creation_projects
SET candidate_draft_version_id=(
      SELECT prediction.draft_version_id FROM predictions prediction
      WHERE prediction.project_id=creation_projects.id AND prediction.locked=1
      ORDER BY prediction.version_no DESC LIMIT 1
    ),
    candidate_confirmed_at=(
      SELECT prediction.locked_at FROM predictions prediction
      WHERE prediction.project_id=creation_projects.id AND prediction.locked=1
      ORDER BY prediction.version_no DESC LIMIT 1
    ),
    quality_gate_status='FINAL_CANDIDATE'
WHERE EXISTS(
  SELECT 1 FROM predictions prediction
  WHERE prediction.project_id=creation_projects.id AND prediction.locked=1
);
"""

DRAFT_REVISION_DIFF_MIGRATION = """
ALTER TABLE draft_revision_runs ADD COLUMN diff_json TEXT NOT NULL DEFAULT '{}';
"""

CHANNEL_OPERATIONS_MIGRATION = """
CREATE TABLE IF NOT EXISTS content_campaigns(
  id TEXT PRIMARY KEY, user_id TEXT NOT NULL,
  title TEXT NOT NULL, core_topic TEXT NOT NULL DEFAULT '',
  source_kind TEXT NOT NULL DEFAULT 'manual', source_ref_type TEXT NOT NULL DEFAULT '',
  source_ref_id TEXT, source_chain_json TEXT NOT NULL DEFAULT '[]',
  objective TEXT NOT NULL DEFAULT '', audience TEXT NOT NULL DEFAULT '',
  status TEXT NOT NULL DEFAULT 'ACTIVE', created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL, archived_at TEXT,
  FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_campaigns_user_status_updated
ON content_campaigns(user_id,status,updated_at DESC);

CREATE TABLE IF NOT EXISTS channel_deliverables(
  id TEXT PRIMARY KEY, user_id TEXT NOT NULL, campaign_id TEXT NOT NULL,
  project_id TEXT, draft_version_id TEXT,
  channel TEXT NOT NULL, deliverable_type TEXT NOT NULL,
  title TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'DRAFT',
  planned_at TEXT, published_at TEXT, source_hash TEXT NOT NULL DEFAULT '',
  metadata_json TEXT NOT NULL DEFAULT '{}', created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL, deleted_at TEXT,
  FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE,
  FOREIGN KEY(campaign_id) REFERENCES content_campaigns(id) ON DELETE CASCADE,
  FOREIGN KEY(project_id) REFERENCES creation_projects(id) ON DELETE SET NULL,
  FOREIGN KEY(draft_version_id) REFERENCES draft_versions(id) ON DELETE SET NULL
);
CREATE INDEX IF NOT EXISTS idx_channel_deliverables_campaign
ON channel_deliverables(user_id,campaign_id,channel,status,updated_at DESC);

CREATE TABLE IF NOT EXISTS moment_posts(
  id TEXT PRIMARY KEY, user_id TEXT NOT NULL, deliverable_id TEXT NOT NULL,
  sequence_no INTEGER NOT NULL DEFAULT 1,
  content_type TEXT NOT NULL DEFAULT 'value', body TEXT NOT NULL,
  media_json TEXT NOT NULL DEFAULT '[]', status TEXT NOT NULL DEFAULT 'DRAFT',
  scheduled_at TEXT, published_at TEXT, publish_note TEXT NOT NULL DEFAULT '',
  metrics_json TEXT NOT NULL DEFAULT '{}', version_no INTEGER NOT NULL DEFAULT 1,
  created_at TEXT NOT NULL, updated_at TEXT NOT NULL, deleted_at TEXT,
  FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE,
  FOREIGN KEY(deliverable_id) REFERENCES channel_deliverables(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_moment_posts_user_status_schedule
ON moment_posts(user_id,status,scheduled_at,updated_at DESC);

CREATE TABLE IF NOT EXISTS official_articles(
  id TEXT PRIMARY KEY, user_id TEXT NOT NULL, deliverable_id TEXT NOT NULL,
  current_version_id TEXT, title TEXT NOT NULL, digest TEXT NOT NULL DEFAULT '',
  author TEXT NOT NULL DEFAULT '', status TEXT NOT NULL DEFAULT 'DRAFT',
  review_status TEXT NOT NULL DEFAULT 'NOT_REVIEWED',
  layout_key TEXT NOT NULL DEFAULT 'jianzhou_clean', cover_media_id TEXT,
  content_source_url TEXT NOT NULL DEFAULT '', remote_media_id TEXT,
  remote_publish_id TEXT, published_url TEXT, created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL, deleted_at TEXT,
  FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE,
  FOREIGN KEY(deliverable_id) REFERENCES channel_deliverables(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_official_articles_user_status_updated
ON official_articles(user_id,status,updated_at DESC);

CREATE TABLE IF NOT EXISTS official_article_versions(
  id TEXT PRIMARY KEY, user_id TEXT NOT NULL, article_id TEXT NOT NULL,
  version_no INTEGER NOT NULL, title TEXT NOT NULL, digest TEXT NOT NULL DEFAULT '',
  markdown_content TEXT NOT NULL, html_content TEXT NOT NULL,
  outline_json TEXT NOT NULL DEFAULT '[]', change_note TEXT NOT NULL DEFAULT '',
  source_hash TEXT NOT NULL, created_at TEXT NOT NULL,
  UNIQUE(article_id,version_no),
  FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE,
  FOREIGN KEY(article_id) REFERENCES official_articles(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_official_versions_article_version
ON official_article_versions(article_id,version_no DESC);

CREATE TABLE IF NOT EXISTS official_article_reviews(
  id TEXT PRIMARY KEY, user_id TEXT NOT NULL, article_id TEXT NOT NULL,
  version_id TEXT NOT NULL, status TEXT NOT NULL,
  score REAL NOT NULL DEFAULT 0, issues_json TEXT NOT NULL DEFAULT '[]',
  checks_json TEXT NOT NULL DEFAULT '{}', provider TEXT NOT NULL DEFAULT '',
  model TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL,
  FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE,
  FOREIGN KEY(article_id) REFERENCES official_articles(id) ON DELETE CASCADE,
  FOREIGN KEY(version_id) REFERENCES official_article_versions(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_official_reviews_article_created
ON official_article_reviews(article_id,created_at DESC);

CREATE TABLE IF NOT EXISTS channel_publish_records(
  id TEXT PRIMARY KEY, user_id TEXT NOT NULL, deliverable_id TEXT NOT NULL,
  channel TEXT NOT NULL, mode TEXT NOT NULL, status TEXT NOT NULL,
  remote_id TEXT, url TEXT, request_json TEXT NOT NULL DEFAULT '{}',
  response_json TEXT NOT NULL DEFAULT '{}', error_json TEXT NOT NULL DEFAULT '{}',
  published_at TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
  FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE,
  FOREIGN KEY(deliverable_id) REFERENCES channel_deliverables(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_channel_publish_deliverable_created
ON channel_publish_records(deliverable_id,created_at DESC);

CREATE TABLE IF NOT EXISTS channel_metric_snapshots(
  id TEXT PRIMARY KEY, user_id TEXT NOT NULL, deliverable_id TEXT NOT NULL,
  channel TEXT NOT NULL, tn TEXT NOT NULL, metrics_json TEXT NOT NULL DEFAULT '{}',
  source TEXT NOT NULL DEFAULT 'manual', taken_at TEXT NOT NULL,
  UNIQUE(deliverable_id,tn,source),
  FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE,
  FOREIGN KEY(deliverable_id) REFERENCES channel_deliverables(id) ON DELETE CASCADE
);

ALTER TABLE creation_projects ADD COLUMN campaign_id TEXT
REFERENCES content_campaigns(id) ON DELETE SET NULL;
ALTER TABLE content_tasks ADD COLUMN campaign_id TEXT
REFERENCES content_campaigns(id) ON DELETE SET NULL;
ALTER TABLE content_tasks ADD COLUMN deliverable_id TEXT
REFERENCES channel_deliverables(id) ON DELETE SET NULL;
ALTER TABLE content_tasks ADD COLUMN delivery_kind TEXT NOT NULL DEFAULT 'public_content';
ALTER TABLE publications ADD COLUMN campaign_id TEXT
REFERENCES content_campaigns(id) ON DELETE SET NULL;
ALTER TABLE publications ADD COLUMN deliverable_id TEXT
REFERENCES channel_deliverables(id) ON DELETE SET NULL;

INSERT OR IGNORE INTO content_campaigns(
  id,user_id,title,core_topic,source_kind,source_ref_type,source_ref_id,
  source_chain_json,objective,audience,status,created_at,updated_at,archived_at
)
SELECT 'CMP-' || id,user_id,title,title,'legacy_project','creation_project',id,
       COALESCE(source_chain_json,'[]'),COALESCE(goal,''),COALESCE(audience,''),
       CASE WHEN status IN ('ARCHIVED','CANCELLED') THEN 'ARCHIVED' ELSE 'ACTIVE' END,
       created_at,created_at,NULL
FROM creation_projects;

UPDATE creation_projects
SET campaign_id='CMP-' || id
WHERE campaign_id IS NULL;
UPDATE content_tasks
SET campaign_id=(SELECT campaign_id FROM creation_projects p WHERE p.id=content_tasks.project_id)
WHERE campaign_id IS NULL AND project_id IS NOT NULL;
UPDATE publications
SET campaign_id=(SELECT campaign_id FROM creation_projects p WHERE p.id=publications.project_id)
WHERE campaign_id IS NULL AND project_id IS NOT NULL;
"""

CHANNEL_PERFORMANCE_RETRO_MIGRATION = """
CREATE TABLE channel_metric_snapshots_v2(
  id TEXT PRIMARY KEY, user_id TEXT NOT NULL, deliverable_id TEXT NOT NULL,
  channel TEXT NOT NULL, subject_type TEXT NOT NULL DEFAULT 'deliverable',
  subject_id TEXT NOT NULL DEFAULT '', tn TEXT NOT NULL,
  metrics_json TEXT NOT NULL DEFAULT '{}', source TEXT NOT NULL DEFAULT 'manual',
  taken_at TEXT NOT NULL,
  UNIQUE(deliverable_id,subject_type,subject_id,tn,source),
  FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE,
  FOREIGN KEY(deliverable_id) REFERENCES channel_deliverables(id) ON DELETE CASCADE
);
INSERT OR IGNORE INTO channel_metric_snapshots_v2(
  id,user_id,deliverable_id,channel,subject_type,subject_id,tn,metrics_json,source,taken_at
)
SELECT id,user_id,deliverable_id,channel,'deliverable','',tn,metrics_json,source,taken_at
FROM channel_metric_snapshots;
DROP TABLE channel_metric_snapshots;
ALTER TABLE channel_metric_snapshots_v2 RENAME TO channel_metric_snapshots;
CREATE INDEX IF NOT EXISTS idx_channel_metrics_deliverable_taken
ON channel_metric_snapshots(deliverable_id,taken_at DESC);
CREATE INDEX IF NOT EXISTS idx_channel_metrics_subject
ON channel_metric_snapshots(user_id,subject_type,subject_id,tn);

CREATE TABLE IF NOT EXISTS channel_retro_reports(
  id TEXT PRIMARY KEY, user_id TEXT NOT NULL, deliverable_id TEXT NOT NULL,
  version_no INTEGER NOT NULL DEFAULT 1, snapshot_hash TEXT NOT NULL,
  report_json TEXT NOT NULL DEFAULT '{}', provider TEXT NOT NULL DEFAULT '',
  model TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL,
  UNIQUE(deliverable_id,snapshot_hash),
  UNIQUE(deliverable_id,version_no),
  FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE,
  FOREIGN KEY(deliverable_id) REFERENCES channel_deliverables(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_channel_retro_deliverable_version
ON channel_retro_reports(deliverable_id,version_no DESC);
"""

CHANNEL_PUBLISH_EVIDENCE_MIGRATION = """
ALTER TABLE channel_publish_records ADD COLUMN subject_type TEXT NOT NULL DEFAULT 'deliverable';
ALTER TABLE channel_publish_records ADD COLUMN subject_id TEXT NOT NULL DEFAULT '';

UPDATE channel_publish_records
SET subject_type='official_article',
    subject_id=COALESCE((
      SELECT official_articles.id FROM official_articles
      WHERE official_articles.deliverable_id=channel_publish_records.deliverable_id
        AND official_articles.deleted_at IS NULL
      ORDER BY official_articles.created_at DESC LIMIT 1
    ), '')
WHERE channel='wechat_official';

CREATE INDEX IF NOT EXISTS idx_channel_publish_subject_created
ON channel_publish_records(user_id,subject_type,subject_id,created_at DESC);
CREATE INDEX IF NOT EXISTS idx_channel_publish_status_created
ON channel_publish_records(user_id,deliverable_id,status,created_at DESC);
CREATE UNIQUE INDEX IF NOT EXISTS uq_creation_projects_user_campaign
ON creation_projects(user_id,campaign_id) WHERE campaign_id IS NOT NULL;
"""

OFFICIAL_VISUAL_COVER_MIGRATION = """
ALTER TABLE official_articles ADD COLUMN cover_project_id TEXT;

CREATE INDEX IF NOT EXISTS idx_official_articles_cover_project
ON official_articles(user_id,cover_project_id)
WHERE cover_project_id IS NOT NULL;
"""

INSPIRATION_AND_MOMENT_MIX_MIGRATION = """
CREATE TABLE IF NOT EXISTS content_ideas(
  id TEXT PRIMARY KEY, user_id TEXT NOT NULL,
  item_type TEXT NOT NULL DEFAULT 'self_idea',
  title TEXT NOT NULL DEFAULT '', body TEXT NOT NULL,
  personal_judgement TEXT NOT NULL DEFAULT '',
  source_kind TEXT NOT NULL DEFAULT 'manual', source_url TEXT NOT NULL DEFAULT '',
  media_json TEXT NOT NULL DEFAULT '[]', tags_json TEXT NOT NULL DEFAULT '[]',
  privacy_level TEXT NOT NULL DEFAULT 'private', status TEXT NOT NULL DEFAULT 'ACTIVE',
  created_at TEXT NOT NULL, updated_at TEXT NOT NULL, deleted_at TEXT,
  FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_content_ideas_user_type_updated
ON content_ideas(user_id,item_type,status,updated_at DESC);

CREATE TABLE IF NOT EXISTS campaign_idea_links(
  campaign_id TEXT NOT NULL, idea_id TEXT NOT NULL,
  role TEXT NOT NULL DEFAULT 'source', created_at TEXT NOT NULL,
  PRIMARY KEY(campaign_id,idea_id,role),
  FOREIGN KEY(campaign_id) REFERENCES content_campaigns(id) ON DELETE CASCADE,
  FOREIGN KEY(idea_id) REFERENCES content_ideas(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_campaign_idea_links_idea
ON campaign_idea_links(idea_id,campaign_id);

CREATE TABLE IF NOT EXISTS moment_mix_preferences(
  user_id TEXT PRIMARY KEY, window_days INTEGER NOT NULL DEFAULT 7,
  profile_key TEXT NOT NULL DEFAULT 'balanced',
  ratios_json TEXT NOT NULL DEFAULT '{}', updated_at TEXT NOT NULL,
  FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
);

ALTER TABLE moment_posts ADD COLUMN idea_id TEXT
REFERENCES content_ideas(id) ON DELETE SET NULL;
ALTER TABLE moment_posts ADD COLUMN source_refs_json TEXT NOT NULL DEFAULT '[]';
ALTER TABLE moment_posts ADD COLUMN origin_mode TEXT NOT NULL DEFAULT 'ai_campaign';

CREATE INDEX IF NOT EXISTS idx_moment_posts_idea
ON moment_posts(user_id,idea_id,updated_at DESC)
WHERE idea_id IS NOT NULL;
"""

WECHAT_PERMISSION_PROBE_MIGRATION = """
CREATE TABLE IF NOT EXISTS wechat_permission_probes(
  id TEXT PRIMARY KEY, user_id TEXT NOT NULL, account_key TEXT NOT NULL,
  status TEXT NOT NULL, reachable INTEGER NOT NULL DEFAULT 0,
  capabilities_json TEXT NOT NULL DEFAULT '{}', errors_json TEXT NOT NULL DEFAULT '[]',
  egress_ips_json TEXT NOT NULL DEFAULT '[]', next_actions_json TEXT NOT NULL DEFAULT '[]',
  checked_at TEXT NOT NULL,
  FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_wechat_permission_probes_user_account_checked
ON wechat_permission_probes(user_id,account_key,checked_at DESC);
"""

MIGRATIONS = [
    (1, "critical_indexes", INDEX_MIGRATION),
    (2, "constrained_schema_marker",
     "CREATE INDEX IF NOT EXISTS idx_schema_migrations_applied "
     "ON schema_migrations(applied_at);"),
    (3, "benchmark_sync_state", BENCHMARK_SYNC_STATE_MIGRATION),
    (4, "benchmark_custom_scan_count", BENCHMARK_SCAN_COUNT_MIGRATION),
    (5, "phase_two_reliability_and_evidence", PHASE_TWO_FOUNDATION_MIGRATION),
    (6, "legacy_publication_snapshot_schedules", LEGACY_PUBLICATION_SCHEDULE_MIGRATION),
    (7, "workspace_radar_incremental_and_artifacts", WORKSPACE_RADAR_AND_INCREMENTAL_MIGRATION),
    (8, "benchmark_signal_work_link", BENCHMARK_SIGNAL_LINK_MIGRATION),
    (9, "signal_content_asset_link", SIGNAL_CONTENT_LINK_MIGRATION),
    (10, "text_quality_and_own_account_works", TEXT_QUALITY_AND_OWN_WORKS_MIGRATION),
    (11, "radar_connectors_job_operations_and_rules",
     RADAR_CONNECTORS_AND_JOB_OPERATIONS_MIGRATION),
    (12, "own_work_review_and_creation_source_link",
     OWN_WORK_REVIEW_AND_CREATION_LINK_MIGRATION),
    (13, "target_mode_lifecycle_archives_prediction_advisor_visuals",
     TARGET_MODE_LIFECYCLE_MIGRATION),
    (14, "visual_factory_provider_assets_and_writing_quality",
     VISUAL_FACTORY_AND_WRITING_QUALITY_MIGRATION),
    (15, "visual_history_soft_delete_and_encoding_guard",
     VISUAL_HISTORY_LIFECYCLE_MIGRATION),
    (16, "unified_assets_operations_context_and_visual_batches",
     UNIFIED_ASSET_OPERATIONS_VISUAL_MIGRATION),
    (17, "visual_candidate_selection_and_page_revision",
     VISUAL_SELECTION_AND_REVISION_MIGRATION),
    (18, "asset_advisor_direction_lifecycle",
     ASSET_ADVISOR_DIRECTION_LIFECYCLE_MIGRATION),
    (19, "advisor_plan_to_creation",
     ADVISOR_PLAN_TO_CREATION_MIGRATION),
    (20, "advisor_plan_legacy_task_link",
     ADVISOR_PLAN_LEGACY_LINK_MIGRATION),
    (21, "creation_finalization_and_external_publication",
     CREATION_FINALIZATION_AND_EXTERNAL_PUBLICATION_MIGRATION),
    (22, "content_lifecycle_continuity",
     CONTENT_LIFECYCLE_CONTINUITY_MIGRATION),
    (23, "finalized_project_task_repair",
     FINALIZED_PROJECT_TASK_REPAIR_MIGRATION),
    (24, "visual_prompt_graphic_document_and_delivery_workflow",
     VISUAL_DOCUMENT_WORKFLOW_MIGRATION),
    (25, "draft_quality_gate_and_revision_workflow",
     DRAFT_QUALITY_GATE_MIGRATION),
    (26, "draft_revision_diff_evidence",
     DRAFT_REVISION_DIFF_MIGRATION),
    (27, "content_campaign_and_channel_operations",
     CHANNEL_OPERATIONS_MIGRATION),
    (28, "channel_performance_snapshots_and_retro",
     CHANNEL_PERFORMANCE_RETRO_MIGRATION),
    (29, "channel_publish_subject_evidence",
     CHANNEL_PUBLISH_EVIDENCE_MIGRATION),
    (30, "official_article_visual_cover",
     OFFICIAL_VISUAL_COVER_MIGRATION),
    (31, "inspiration_archive_and_moment_mix",
     INSPIRATION_AND_MOMENT_MIX_MIGRATION),
    (32, "wechat_permission_probe_evidence",
     WECHAT_PERMISSION_PROBE_MIGRATION),
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
    from .text_quality import (repair_legacy_database_text,
                               repair_mislabeled_description_assets,
                               repair_placeholder_draft_platforms)
    repair_legacy_database_text(conn)
    repair_mislabeled_description_assets(conn)
    repair_placeholder_draft_platforms(conn)


def _protect_legacy_secrets(conn: sqlite3.Connection) -> None:
    """Encrypt and scrub legacy plaintext keys before any migration backup."""
    from .secret_store import get_secret, set_secret

    changed = False
    for section, field, secret_name in (
        ("source", "tikhub_api_key", "tikhub_api_key"),
        ("llm", "api_key", "llm_api_key"),
        ("voice", "api_key", "asr_api_key"),
        ("image", "api_key", "image_api_key"),
        ("wechat_official", "app_secret", "wechat_app_secret"),
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
