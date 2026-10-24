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
    with tempfile.TemporaryDirectory(prefix="jianzhou-interaction-test-") as temp:
        env = os.environ.copy()
        env.update({
            "JIANZHOU_DATA_DIR": temp,
            "JIANZHOU_DEMO": "1" if demo else "0",
            "PYTHONUTF8": "1",
        })
        result = subprocess.run(
            [sys.executable, "-c", textwrap.dedent(source)], cwd=ROOT, env=env,
            text=True, encoding="utf-8", capture_output=True, timeout=60,
        )
        if result.returncode:
            raise AssertionError(
                f"isolated process failed\nstdout={result.stdout}\nstderr={result.stderr}"
            )
        return json.loads(result.stdout.strip())


class InteractionClosureTests(unittest.TestCase):
    def test_account_page_exposes_navigation_used_by_plan_button(self):
        text = (ROOT / "frontend" / "assets" / "js" / "pages" / "base.js").read_text(encoding="utf-8")
        account_component = text.split("window.PageAccount =", 1)[1]
        self.assertIn("methods: {\n      go,", account_component)
        self.assertIn("@click=\"go('plan')\"", account_component)

    def test_account_form_backend_persists_resolved_result_and_snapshot(self):
        result = run_isolated("""
            import json
            from app.api.accounts import AccountIn, add_account, account_detail
            from app.db import init_db, query_one, transaction
            from app.main import _ensure_settings
            from app.seed import seed_if_empty
            init_db(); _ensure_settings()
            with transaction(): seed_if_empty()
            created = add_account(AccountIn(
                platform="douyin", link="https://www.douyin.com/user/interaction-test",
                sync_frequency="每天", source_mode="provider",
            ))["data"]
            detail = account_detail(created["id"])["data"]
            print(json.dumps({
                "provider": detail["bound_provider"], "status": detail["status"],
                "homepage": detail["homepage"], "frequency": detail["sync_frequency"],
                "snapshots": len(detail["snapshots"]), "uid": detail["extra"]["uid"],
            }))
        """)
        self.assertEqual(result["provider"], "demo_ds")
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["frequency"], "每天")
        self.assertEqual(result["snapshots"], 1)
        self.assertTrue(result["homepage"])
        self.assertTrue(result["uid"].startswith("demo_douyin_"))

    def test_real_account_missing_key_fails_without_creating_record(self):
        result = run_isolated("""
            import json
            from app.api.accounts import AccountIn, add_account
            from app.db import init_db, query_one, transaction
            from app.errors import AppError
            from app.main import _ensure_settings
            from app.seed import seed_if_empty
            init_db(); _ensure_settings()
            with transaction(): seed_if_empty()
            code = None
            try:
                add_account(AccountIn(platform="douyin", link="sec_uid_test"))
            except AppError as exc:
                code = exc.code
            print(json.dumps({"code": code, "count": query_one(
                "SELECT COUNT(*) c FROM accounts WHERE user_id='u-local'")["c"]}))
        """, demo=False)
        self.assertEqual(result, {"code": "CONFIG_MISSING_API_KEY", "count": 0})

    def test_profile_version_cycle_and_comparison_are_persistent(self):
        result = run_isolated("""
            import json
            from app.api.accounts import CycleIn, get_cycle, update_cycle
            from app.api.analysis import ComparisonItemIn, add_comparison_item, comparison_items
            from app.api.profile import ProfileVersionIn, create_version, get_active_profile
            from app.db import init_db, query_one, transaction
            from app.main import _ensure_settings
            from app.seed import seed_if_empty
            init_db(); _ensure_settings()
            with transaction(): seed_if_empty()
            profile = get_active_profile()["data"]["content"]
            profile["positioning"] = "端到端测试后的新定位"
            version = create_version(ProfileVersionIn(
                content=profile, reason="交互闭环测试", activate=True))["data"]
            update_cycle(CycleIn(goal="验证真实交互闭环", hours_per_week=9))
            analysis = query_one("SELECT id FROM analyses WHERE user_id='u-zhou' LIMIT 1")
            added = add_comparison_item(ComparisonItemIn(
                analysis_id=analysis["id"], performance_bucket="high",
                note="账号中位数 2 倍以上"))["data"]
            print(json.dumps({
                "version": version["version_no"],
                "positioning": get_active_profile()["data"]["content"]["positioning"],
                "cycle": get_cycle()["data"]["goal"],
                "comparison_count": added["count"],
                "comparison_bucket": comparison_items()["data"][0]["performance_bucket"],
            }, ensure_ascii=False))
        """)
        self.assertEqual(result["positioning"], "端到端测试后的新定位")
        self.assertEqual(result["cycle"], "验证真实交互闭环")
        self.assertEqual(result["comparison_count"], 1)
        self.assertEqual(result["comparison_bucket"], "high")

    def test_multi_platform_generation_does_not_persist_partial_batch(self):
        result = run_isolated("""
            import json
            from unittest.mock import patch
            from app.api.creation import MultiDraftIn, generate_multi_platform_drafts
            from app.db import execute, init_db, jdumps, query_one

            init_db()
            execute("INSERT INTO users(id,name,avatar,created_at) VALUES('u-zhou','user','','now')")
            execute(
                "INSERT INTO creation_projects(id,user_id,title,goal,audience,source_chain_json,"
                "diff_strategy,outline_json,context_json,checks_json,status,created_at,"
                "source_content_id,analysis_id) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                ('cp-atomic','u-zhou','主题','目标','受众',jdumps([]),'方向',jdumps([]),
                 jdumps({'context_run_id':'ctx-1','content':{}}),jdumps({}),'DRAFTING','now',None,None),
            )

            class Provider:
                name = 'fake-real'
                last_usage = {}
                last_model = 'fake'
                calls = 0
                def generate_draft(self, *args, **kwargs):
                    self.calls += 1
                    if self.calls == 2:
                        raise RuntimeError('provider timeout')
                    return 'first draft'

            failed = False
            try:
                with patch('app.api.creation.get_llm_provider', return_value=Provider()):
                    generate_multi_platform_drafts(
                        'cp-atomic', MultiDraftIn(platforms=['抖音口播','小红书图文']))
            except RuntimeError:
                failed = True
            count = query_one("SELECT COUNT(*) c FROM draft_versions WHERE project_id='cp-atomic'")['c']
            print(json.dumps({'failed': failed, 'draft_count': count}))
        """)
        self.assertEqual(result, {"failed": True, "draft_count": 0})


if __name__ == "__main__":
    unittest.main()
