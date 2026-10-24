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


def run_isolated(source: str) -> dict:
    with tempfile.TemporaryDirectory(prefix="jianzhou-inspiration-") as temp:
        env = os.environ.copy()
        env.update({"JIANZHOU_DATA_DIR": temp, "JIANZHOU_DEMO": "1", "PYTHONUTF8": "1"})
        result = subprocess.run(
            [sys.executable, "-c", textwrap.dedent(source)], cwd=ROOT, env=env,
            text=True, encoding="utf-8", capture_output=True, timeout=90,
        )
        if result.returncode != 0:
            raise AssertionError(
                f"isolated process failed\nstdout={result.stdout}\nstderr={result.stderr}"
            )
        return json.loads(result.stdout.strip())


class InspirationAndMomentFlowTests(unittest.TestCase):
    def test_runtime_does_not_log_provider_query_secrets_at_info_level(self):
        result = run_isolated("""
            import json, logging, runpy
            entrypoint = runpy.run_path('app.py')
            entrypoint['_configure_logging']()
            print(json.dumps({
                'httpx_level': logging.getLogger('httpx').level,
                'httpcore_level': logging.getLogger('httpcore').level,
            }))
        """)
        self.assertGreaterEqual(result["httpx_level"], 30)
        self.assertGreaterEqual(result["httpcore_level"], 30)

    def test_manual_reflection_and_idea_creation_are_persistent_and_linked(self):
        result = run_isolated("""
            import json
            from app.api import set_current_user
            from app.api.channels import ManualMomentIn, create_manual_moment, list_moments
            from app.api.creation import ProjectIn, create_project
            from app.api.ideas import IdeaIn, IdeaPatch, add_idea, edit_idea, list_ideas
            from app.asset_center import sync_asset_index
            from app.db import init_db, query, query_one, transaction
            from app.main import _ensure_settings
            from app.seed import seed_if_empty

            init_db(); _ensure_settings()
            with transaction(): seed_if_empty()
            set_current_user('u-zhou')

            idea = add_idea(IdeaIn(
                title='我对内容工具的一个新判断', body='今天真实用了一遍，我发现稳定的证据链比一次生成更重要。',
                item_type='self_idea', personal_judgement='这应该成为下一条口播的核心判断。',
            ))['data']
            edited = edit_idea(idea['id'], IdeaPatch(
                body='今天真实用了一遍，我发现稳定的证据链比一次生成更重要，而且必须能复用。'
            ))['data']
            project = create_project(ProjectIn(
                title='证据链为什么比一次生成更重要', idea_id=idea['id'], audience='内容创作者'
            ))['data']
            manual = create_manual_moment(ManualMomentIn(
                title='今晚的一点真实感受', body='今天改系统时，我更确定了：真实记录不能被自动生成替代。',
                personal_judgement='这是我的原话，先保留，不需要 AI 润色。', content_type='human'
            ))['data']
            moments = list_moments()['data']
            manual_row = next(item for item in moments if item['id'] == manual['id'])
            sync_asset_index()
            print(json.dumps({
                'schema': query_one('PRAGMA user_version')['user_version'],
                'idea_count': len(list_ideas()['data']),
                'edited_body': edited['body'],
                'project_idea_id': project['idea_id'],
                'project_task_status': project['plan_task_status'],
                'source_chain': query_one('SELECT source_chain_json FROM creation_projects WHERE id=?', (project['id'],))['source_chain_json'],
                'manual_origin': manual_row['origin_mode'],
                'manual_body': manual_row['body'],
                'manual_idea_type': manual_row['idea']['item_type'],
                'manual_task_status': query_one('SELECT status FROM content_tasks WHERE deliverable_id=?', (manual['deliverable_id'],))['status'],
                'idea_assets': query_one("SELECT COUNT(*) n FROM asset_records WHERE user_id='u-zhou' AND asset_type='idea'")['n'],
                'fk_errors': len(query('PRAGMA foreign_key_check')),
            }, ensure_ascii=False))
        """)
        self.assertGreaterEqual(result["schema"], 31)
        self.assertEqual(result["idea_count"], 2)
        self.assertIn("必须能复用", result["edited_body"])
        self.assertIn(result["project_idea_id"], result["source_chain"])
        self.assertEqual(result["project_task_status"], "CANDIDATE")
        self.assertEqual(result["manual_origin"], "manual")
        self.assertIn("真实记录不能", result["manual_body"])
        self.assertEqual(result["manual_idea_type"], "life_note")
        self.assertEqual(result["manual_task_status"], "PRODUCING")
        self.assertGreaterEqual(result["idea_assets"], 2)
        self.assertEqual(result["fk_errors"], 0)

    def test_rolling_mix_uses_only_owned_idea_evidence_and_saves_preferences(self):
        result = run_isolated("""
            import json
            from app.api import set_current_user
            from app.api.channels import (
                MomentMixIn, MomentMixPreferenceIn, compose_moment_mix,
                list_moments, moment_preferences, save_moment_preferences,
            )
            from app.api.ideas import IdeaIn, add_idea
            from app.db import init_db, query, transaction
            from app.main import _ensure_settings
            from app.seed import seed_if_empty

            init_db(); _ensure_settings()
            with transaction(): seed_if_empty()
            set_current_user('u-zhou')
            life = add_idea(IdeaIn(
                body='周末散步时我发现，停下来以后反而更容易想清楚接下来真正要做的事。',
                title='散步后的感受', item_type='life_note'
            ))['data']
            work = add_idea(IdeaIn(
                body='今天我把创作链路从灵感到发布重新走了一遍，卡点终于暴露出来。',
                title='一次真实工作记录', item_type='work_process'
            ))['data']
            saved = save_moment_preferences(MomentMixPreferenceIn(
                window_days=14, profile_key='balanced',
                ratios={'value': 10, 'process': 40, 'human': 40, 'trust': 5, 'interaction': 5}
            ))['data']
            mixed = compose_moment_mix(MomentMixIn(
                window_days=14, max_posts=5, idea_ids=[life['id'], work['id']], ratios=saved['ratios']
            ))['data']
            posts = [item for item in list_moments()['data'] if item['deliverable_id'] == mixed['deliverable_id']]
            print(json.dumps({
                'count': len(posts),
                'origins': sorted({item['origin_mode'] for item in posts}),
                'types': sorted({item['content_type'] for item in posts}),
                'idea_ids': sorted({item['idea_id'] for item in posts}),
                'all_traceable': all(item['source_refs'] and item['idea_id'] in item['source_refs'] for item in posts),
                'prefs': moment_preferences()['data'],
                'task_statuses': [row['status'] for row in query('SELECT status FROM content_tasks WHERE deliverable_id=?', (mixed['deliverable_id'],))],
            }, ensure_ascii=False))
        """)
        self.assertEqual(result["count"], 2)
        self.assertEqual(result["origins"], ["ai_mix"])
        self.assertEqual(result["types"], ["human", "process"])
        self.assertEqual(len(result["idea_ids"]), 2)
        self.assertTrue(result["all_traceable"])
        self.assertEqual(result["prefs"]["window_days"], 14)
        self.assertEqual(sum(result["prefs"]["ratios"].values()), 100)
        self.assertEqual(result["task_statuses"], ["PRODUCING"])


if __name__ == "__main__":
    unittest.main()
