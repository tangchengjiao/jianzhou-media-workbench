from __future__ import annotations

import json
import os
import pathlib
import subprocess
import sys
import tempfile
import textwrap
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]


def run_isolated(source: str, *, demo: bool = True) -> dict:
    with tempfile.TemporaryDirectory(prefix="jianzhou-phase3-") as temp:
        env = os.environ.copy()
        env.update({"JIANZHOU_DATA_DIR": temp,
                    "JIANZHOU_DEMO": "1" if demo else "0",
                    "PYTHONUTF8": "1"})
        result = subprocess.run(
            [sys.executable, "-c", textwrap.dedent(source)], cwd=ROOT, env=env,
            text=True, encoding="utf-8", capture_output=True, timeout=90,
        )
        if result.returncode:
            raise AssertionError(f"isolated process failed\nstdout={result.stdout}\nstderr={result.stderr}")
        return json.loads(result.stdout.strip())


class PhaseThreeRadarTests(unittest.TestCase):
    def test_demo_and_real_workspaces_are_isolated(self):
        result = run_isolated("""
            import json
            from app import config
            from app.api import current_user_id
            from app.db import init_db, query_one, transaction
            from app.main import _ensure_settings
            from app.seed import ensure_data_scopes, seed_if_empty
            init_db(); _ensure_settings()
            with transaction(): seed_if_empty(); ensure_data_scopes()
            demo_uid = current_user_id()
            demo_profiles = query_one("SELECT COUNT(*) c FROM profile_versions WHERE user_id=?", (demo_uid,))["c"]
            config.set_demo_mode(False)
            real_uid = current_user_id()
            real_profiles = query_one("SELECT COUNT(*) c FROM profile_versions WHERE user_id=?", (real_uid,))["c"]
            real_accounts = query_one("SELECT COUNT(*) c FROM accounts WHERE user_id=?", (real_uid,))["c"]
            print(json.dumps({"demo_uid":demo_uid,"real_uid":real_uid,
                "demo_profiles":demo_profiles,"real_profiles":real_profiles,
                "real_accounts":real_accounts}))
        """)
        self.assertEqual(result["demo_uid"], "u-zhou")
        self.assertEqual(result["real_uid"], "u-local")
        self.assertGreater(result["demo_profiles"], 0)
        self.assertEqual(result["real_profiles"], 0)
        self.assertEqual(result["real_accounts"], 0)

    def test_incremental_backfill_snapshots_and_analysis_cache(self):
        result = run_isolated("""
            import json
            from app.api.extraction import PlanIn, create_plan
            from app.benchmarks import add_benchmark, author_analysis, scan_benchmark
            from app.db import init_db, query_one, transaction
            from app.main import _ensure_settings
            from app.seed import ensure_data_scopes, seed_if_empty
            init_db(); _ensure_settings()
            with transaction(): seed_if_empty(); ensure_data_scopes()
            bm = add_benchmark("u-zhou", "https://www.douyin.com/user/incremental", "douyin", scan_count=10)
            first = query_one("SELECT * FROM benchmark_accounts WHERE id=?", (bm["id"],))
            backfill = scan_benchmark(bm["id"], kind="backfill", requested_count=15)
            second = query_one("SELECT * FROM benchmark_accounts WHERE id=?", (bm["id"],))
            scan_benchmark(bm["id"], kind="refresh", requested_count=10)
            third = query_one("SELECT * FROM benchmark_accounts WHERE id=?", (bm["id"],))
            linked_signal = query_one(
                "SELECT id,benchmark_work_id FROM signals WHERE user_id='u-zhou' "
                "AND benchmark_work_id IS NOT NULL ORDER BY fetched_at DESC LIMIT 1")
            extraction_plan = create_plan(PlanIn(signal_id=linked_signal["id"]))["data"]
            a1 = author_analysis(bm["id"]); a2 = author_analysis(bm["id"])
            print(json.dumps({"first_cursor":first["next_cursor"],"second_cursor":second["next_cursor"],
                "refresh_cursor":third["next_cursor"],
                "linked_work":linked_signal["benchmark_work_id"],
                "video_id":extraction_plan["source"]["video_id"],
                "works":query_one("SELECT COUNT(*) c FROM benchmark_works WHERE bm_id=?",(bm["id"],))["c"],
                "snapshots":query_one("SELECT COUNT(*) c FROM benchmark_work_snapshots WHERE bm_id=?",(bm["id"],))["c"],
                "new":backfill["new"],"cached":a2["cached"],
                "analyses":query_one("SELECT COUNT(*) c FROM author_analyses WHERE bm_id=?",(bm["id"],))["c"]}))
        """)
        self.assertEqual(result["first_cursor"], "10")
        self.assertEqual(result["second_cursor"], "25")
        self.assertEqual(result["refresh_cursor"], "25")
        self.assertTrue(result["linked_work"].startswith("bw-"))
        self.assertTrue(result["video_id"].startswith("v"))
        self.assertEqual(result["works"], 25)
        self.assertEqual(result["new"], 15)
        self.assertGreaterEqual(result["snapshots"], 25)
        self.assertTrue(result["cached"])
        self.assertEqual(result["analyses"], 1)

    def test_radar_source_is_persistent_and_deduplicated(self):
        result = run_isolated("""
            import json
            from app.db import init_db, query_one, transaction
            from app.main import _ensure_settings
            from app.radar_sources import create_source, sync_source
            from app.seed import ensure_data_scopes, seed_if_empty
            init_db(); _ensure_settings()
            with transaction(): seed_if_empty(); ensure_data_scopes()
            src = create_source("u-zhou", "tikhub_hot", "抖音热榜", "douyin", {"limit":6,"keywords":["内容"]}, 12)
            first = sync_source(src["id"], "u-zhou"); second = sync_source(src["id"], "u-zhou")
            print(json.dumps({"first":first,"second":second,
                "signals":query_one("SELECT COUNT(*) c FROM signals WHERE radar_source_id=?",(src["id"],))["c"],
                "runs":query_one("SELECT COUNT(*) c FROM radar_fetch_runs WHERE source_id=?",(src["id"],))["c"]}))
        """)
        self.assertEqual(result["first"]["created"], 6)
        self.assertEqual(result["second"]["created"], 0)
        self.assertEqual(result["signals"], 6)
        self.assertEqual(result["runs"], 2)

    def test_unified_job_status_endpoint_closes_background_frontend_flow(self):
        result = run_isolated("""
            import json
            from fastapi.testclient import TestClient
            from app.jobs import create_job
            from app.main import create_app
            with TestClient(create_app()) as client:
                job = create_job("u-zhou", "benchmark_refresh", {
                    "title":"endpoint smoke", "bm_id":"missing-on-purpose"})
                response = client.get(f"/api/jobs/{job['id']}")
                payload = response.json()
                print(json.dumps({"status":response.status_code,
                    "success":payload["success"], "id":payload["data"]["id"]}))
        """)
        self.assertEqual(result["status"], 200)
        self.assertTrue(result["success"])
        self.assertTrue(result["id"].startswith("J-"))


if __name__ == "__main__":
    unittest.main()
