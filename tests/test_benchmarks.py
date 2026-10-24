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
    with tempfile.TemporaryDirectory(prefix="jianzhou-benchmark-test-") as temp:
        env = os.environ.copy()
        env.update({
            "JIANZHOU_DATA_DIR": temp,
            "JIANZHOU_DEMO": "1" if demo else "0",
            "PYTHONUTF8": "1",
        })
        result = subprocess.run(
            [sys.executable, "-c", textwrap.dedent(source)],
            cwd=ROOT,
            env=env,
            text=True,
            encoding="utf-8",
            capture_output=True,
            timeout=60,
        )
        if result.returncode != 0:
            raise AssertionError(
                f"isolated process failed\nstdout={result.stdout}\nstderr={result.stderr}"
            )
        return json.loads(result.stdout.strip())


class BenchmarkFlowTests(unittest.TestCase):
    def test_demo_account_to_work_to_signal_flow(self):
        result = run_isolated("""
            import json
            from app.benchmarks import add_benchmark, work_to_signal
            from app.db import init_db, query, query_one, transaction
            from app.main import _ensure_settings
            from app.seed import seed_if_empty
            init_db(); _ensure_settings()
            with transaction():
                seed_if_empty()
            created = add_benchmark(
                "u-zhou", "https://www.douyin.com/user/test-account",
                "douyin", "30", "test", scan_count=47,
            )
            account = query_one(
                "SELECT * FROM benchmark_accounts WHERE id=?", (created["id"],)
            )
            work = query_one(
                "SELECT * FROM benchmark_works WHERE bm_id=? ORDER BY create_time DESC",
                (created["id"],),
            )
            signal = work_to_signal(created["id"], work["video_id"])
            saved_signal = query_one(
                "SELECT * FROM signals WHERE id=?", (signal["signal_id"],)
            )
            columns = {row["name"] for row in query("PRAGMA table_info(benchmark_accounts)")}
            print(json.dumps({
                "sync_status": account["sync_status"],
                "works": query_one(
                    "SELECT COUNT(*) c FROM benchmark_works WHERE bm_id=?", (created["id"],)
                )["c"],
                "signal_category": saved_signal["category"],
                "schema_version": query_one("PRAGMA user_version")["user_version"],
                "scan_count": account["scan_count"],
                "has_sync_columns": {"sync_status", "last_error_code", "last_error_message"} <= columns,
            }))
        """)
        self.assertEqual(result["sync_status"], "ready")
        self.assertGreater(result["works"], 0)
        self.assertEqual(result["signal_category"], "manual")
        self.assertGreaterEqual(result["schema_version"], 13)
        self.assertEqual(result["scan_count"], 47)
        self.assertTrue(result["has_sync_columns"])

    def test_real_mode_missing_key_creates_no_benchmark_shell(self):
        result = run_isolated("""
            import json
            from app.benchmarks import add_benchmark
            from app.db import init_db, query_one, transaction
            from app.errors import AppError
            from app.main import _ensure_settings
            from app.seed import seed_if_empty
            init_db(); _ensure_settings()
            with transaction():
                seed_if_empty()
            code = None
            try:
                add_benchmark("u-local", "https://www.douyin.com/user/abc", "douyin")
            except AppError as exc:
                code = exc.code
            print(json.dumps({
                "error": code,
                "accounts": query_one("SELECT COUNT(*) c FROM benchmark_accounts")["c"],
            }))
        """, demo=False)
        self.assertEqual(result, {"error": "CONFIG_MISSING_API_KEY", "accounts": 0})

    def test_heic_benchmark_cover_uses_owned_local_proxy(self):
        result = run_isolated("""
            import json
            from app.api import set_current_user
            from app.api.benchmarks import benchmark_detail
            from app.benchmarks import add_benchmark
            from app.db import execute, init_db, query_one, transaction
            from app.main import _ensure_settings
            from app.seed import seed_if_empty
            init_db(); _ensure_settings()
            with transaction():
                seed_if_empty()
            set_current_user("u-zhou")
            created = add_benchmark(
                "u-zhou", "https://www.douyin.com/user/cover-account",
                "douyin", scan_count=1,
            )
            work = query_one(
                "SELECT id FROM benchmark_works WHERE bm_id=? LIMIT 1",
                (created["id"],),
            )
            execute(
                "UPDATE benchmark_works SET cover=? WHERE id=?",
                ("https://p11-sign.douyinpic.com/demo.heic?x-signature=test", work["id"]),
            )
            detail = benchmark_detail(created["id"])["data"]
            saved = next(item for item in detail["works"] if item["id"] == work["id"])
            print(json.dumps({"raw": saved["cover"], "display": saved["cover_display_url"]}))
        """)
        self.assertTrue(result["raw"].endswith("x-signature=test"))
        self.assertTrue(result["display"].endswith("/cover"))
        self.assertIn("/api/benchmarks/", result["display"])

    def test_real_executor_paginates_and_deduplicates(self):
        from app.datasource import EndpointExecutor

        executor = object.__new__(EndpointExecutor)
        executor.pconf = {"endpoints": {"accountVideos": {
            "douyin": {"pageSize": 20, "startCursor": "0"}
        }}}
        calls = []

        def fake_call(capability, args):
            calls.append(dict(args))
            start = 0 if args["cursor"] == "0" else 20
            data = [
                {"video_id": f"v{i}", "title": f"work {i}", "cursor": "next"}
                for i in range(start, start + 20)
            ]
            return {
                "data": data,
                "cursor": "next" if start == 0 else "done",
                "hasMore": start == 0,
            }

        executor.call_endpoint = fake_call
        result = executor.account_videos("uid", "douyin", limit=30)
        self.assertEqual(len(result["works"]), 30)
        self.assertEqual(len({work["video_id"] for work in result["works"]}), 30)
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[0]["limit"], 20)
        self.assertEqual(calls[1]["cursor"], "next")

    def test_xhs_profile_link_resolves_user_id(self):
        from app.datasource import _resolve_xhs_uid

        self.assertEqual(
            _resolve_xhs_uid(
                "https://www.xiaohongshu.com/user/profile/5f123abc987?xsec_token=demo"
            ),
            "5f123abc987",
        )
        self.assertEqual(_resolve_xhs_uid("raw_user_id_123"), "raw_user_id_123")

    def test_kuaishou_profile_and_eid_resolve_without_paid_lookup(self):
        from app.datasource import EndpointExecutor

        ds = EndpointExecutor.__new__(EndpointExecutor)
        ds.name = "tikhub"
        ds._require = lambda: None
        ds.call_endpoint = lambda *_args, **_kwargs: self.fail("direct eid should not call resolve API")
        for value in ("https://www.kuaishou.com/profile/3xz63mn6fngqtiq", "3xz63mn6fngqtiq"):
            resolved = ds.resolve_account(value, "kuaishou")
            self.assertEqual(resolved["uid"], "3xz63mn6fngqtiq")

    def test_supported_platform_catalog_and_bearer_header(self):
        from app.datasource import (
            SUPPORTED_PLATFORMS, _authorization_header, platform_catalog,
        )

        expected = {"douyin", "xhs", "channels", "bilibili", "kuaishou", "weibo", "tiktok"}
        self.assertEqual(set(SUPPORTED_PLATFORMS), expected)
        self.assertEqual({item["id"] for item in platform_catalog()}, expected)
        self.assertEqual(_authorization_header("abc"), "Bearer abc")
        self.assertEqual(_authorization_header("Bearer abc"), "Bearer abc")


if __name__ == "__main__":
    unittest.main()
