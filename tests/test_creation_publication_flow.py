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
    with tempfile.TemporaryDirectory(prefix="jianzhou-publication-flow-") as temp:
        env = os.environ.copy()
        env.update({"JIANZHOU_DATA_DIR": temp, "JIANZHOU_DEMO": "1", "PYTHONUTF8": "1"})
        result = subprocess.run(
            [sys.executable, "-c", textwrap.dedent(source)], cwd=ROOT, env=env,
            text=True, encoding="utf-8", capture_output=True, timeout=90,
        )
        if result.returncode:
            raise AssertionError(
                f"isolated process failed\nstdout={result.stdout}\nstderr={result.stderr}"
            )
        return json.loads(result.stdout.strip())


class CreationPublicationFlowTests(unittest.TestCase):
    def test_locking_latest_prediction_moves_linked_plan_to_ready(self):
        result = run_isolated("""
            import json
            from app.api.creation import (CandidateDraftIn, ProjectIn, confirm_candidate_draft,
                create_project, generate_draft, generate_draft_assessment, generate_prediction,
                lock_prediction)
            from app.api.planning import TaskIn, create_task, board, PublicationIn, register_publication
            from app.db import init_db, query_one, transaction
            from app.main import _ensure_settings
            from app.seed import seed_if_empty

            init_db(); _ensure_settings()
            with transaction(): seed_if_empty()
            project_id = create_project(ProjectIn(title='计划内完整状态流', source_chain=['测试']))['data']['id']
            task_id = create_task(TaskIn(title='计划内完整状态流', platform='抖音', project_id=project_id))['data']['id']
            generate_draft(project_id)
            assessed = generate_draft_assessment(project_id)['data']
            confirm_candidate_draft(project_id, CandidateDraftIn(draft_version_id=assessed['draft_version_id']))
            generated = generate_prediction(project_id)['data']
            finalized = lock_prediction(project_id)['data']
            task = query_one('SELECT * FROM content_tasks WHERE id=?', (task_id,))
            ready_column = next(item for item in board()['data']['columns'] if item['state']=='READY_TO_PUBLISH')
            publication = register_publication(PublicationIn(
                task_id=task_id, platform='抖音', url='https://example.com/plan-flow',
                final_title='计划内完整状态流'))['data']
            print(json.dumps({
                'prediction_draft': generated['details']['input_snapshot']['draft_version_id'],
                'final_draft': task['final_draft_version_id'],
                'status_before_publish': finalized['status'],
                'ready_card': any(item['id']==task_id for item in ready_column['cards']),
                'status_after_publish': query_one('SELECT status FROM content_tasks WHERE id=?',(task_id,))['status'],
                'project_after_publish': query_one('SELECT status FROM creation_projects WHERE id=?',(project_id,))['status'],
                'schedules': len(publication['schedules']),
            }, ensure_ascii=False))
        """)
        self.assertEqual(result["prediction_draft"], result["final_draft"])
        self.assertEqual(result["status_before_publish"], "READY_TO_PUBLISH")
        self.assertTrue(result["ready_card"])
        self.assertEqual(result["status_after_publish"], "PUBLISHED")
        self.assertEqual(result["project_after_publish"], "PUBLISHED")
        self.assertEqual(result["schedules"], 3)

    def test_edit_after_lock_invalidates_ready_until_new_prediction(self):
        result = run_isolated("""
            import json
            from app.api.creation import (CandidateDraftIn, DraftIn, ProjectIn,
                confirm_candidate_draft, create_project, generate_draft, generate_draft_assessment,
                generate_prediction, lock_prediction, save_draft, finalize_project)
            from app.api.planning import TaskIn, create_task
            from app.db import init_db, query_one, transaction
            from app.main import _ensure_settings
            from app.seed import seed_if_empty

            init_db(); _ensure_settings()
            with transaction(): seed_if_empty()
            project_id = create_project(ProjectIn(title='改稿失效测试', source_chain=['测试']))['data']['id']
            task_id = create_task(TaskIn(title='改稿失效测试', platform='抖音', project_id=project_id))['data']['id']
            generate_draft(project_id)
            assessed = generate_draft_assessment(project_id)['data']
            confirm_candidate_draft(project_id, CandidateDraftIn(draft_version_id=assessed['draft_version_id']))
            generate_prediction(project_id); lock_prediction(project_id)
            save_draft(project_id, DraftIn(content='这是锁定后人工修改的新稿，必须重新预测。', note='人工改稿', platform='抖音'))
            stale_status = query_one('SELECT status FROM content_tasks WHERE id=?',(task_id,))['status']
            stale_rejected = False
            try: finalize_project(project_id)
            except Exception as exc: stale_rejected = '体检' in str(exc) or '重新运行' in str(exc)
            assessed = generate_draft_assessment(project_id)['data']
            confirm_candidate_draft(project_id, CandidateDraftIn(draft_version_id=assessed['draft_version_id']))
            generate_prediction(project_id); lock_prediction(project_id)
            refreshed = query_one('SELECT status,final_draft_version_id FROM content_tasks WHERE id=?',(task_id,))
            latest = query_one('SELECT id FROM draft_versions WHERE project_id=? ORDER BY version_no DESC LIMIT 1',(project_id,))
            print(json.dumps({
                'stale_status': stale_status, 'stale_rejected': stale_rejected,
                'refreshed_status': refreshed['status'],
                'latest_bound': refreshed['final_draft_version_id']==latest['id'],
            }, ensure_ascii=False))
        """)
        self.assertEqual(result["stale_status"], "DRAFTING")
        self.assertTrue(result["stale_rejected"])
        self.assertEqual(result["refreshed_status"], "READY_TO_PUBLISH")
        self.assertTrue(result["latest_bound"])

    def test_quality_gate_optimizes_selected_issues_and_preserves_prediction_boundary(self):
        result = run_isolated("""
            import json
            from app.api.creation import (CandidateDraftIn, DraftOptimizeIn, ProjectIn,
                confirm_candidate_draft, create_project, generate_draft,
                generate_draft_assessment, generate_prediction, optimize_draft)
            from app.db import init_db, query_one, transaction
            from app.main import _ensure_settings
            from app.seed import seed_if_empty

            init_db(); _ensure_settings()
            with transaction(): seed_if_empty()
            project_id = create_project(ProjectIn(title='体检改稿闭环', source_chain=['测试']))['data']['id']
            generate_draft(project_id)
            blocked_before_candidate = False
            try: generate_prediction(project_id)
            except Exception as exc: blocked_before_candidate = '候选终稿' in str(exc) or '体检' in str(exc)
            first = generate_draft_assessment(project_id)['data']
            selected = [first['issues'][0]['id']] if first['issues'] else []
            revised = optimize_draft(project_id, DraftOptimizeIn(
                assessment_id=first['id'], selected_issue_ids=selected,
                instructions='保持事实不变，让开头更直接'))['data']
            gate_after_revision = query_one(
                'SELECT quality_gate_status,candidate_draft_version_id FROM creation_projects WHERE id=?',
                (project_id,))
            second = generate_draft_assessment(project_id)['data']
            confirm_candidate_draft(project_id, CandidateDraftIn(draft_version_id=second['draft_version_id']))
            prediction = generate_prediction(project_id)['data']
            run = query_one('SELECT * FROM draft_revision_runs WHERE target_draft_version_id=?',
                            (revised['id'],))
            print(json.dumps({
                'blocked_before_candidate': blocked_before_candidate,
                'new_version': revised['version_no'] > 1,
                'source_preserved': revised['source_draft_version_id'] == first['draft_version_id'],
                'gate_reset': gate_after_revision['quality_gate_status'] == 'NEEDS_ASSESSMENT' and gate_after_revision['candidate_draft_version_id'] is None,
                'selected_saved': json.loads(run['selected_issue_ids_json']) == selected,
                'diff_saved': bool(json.loads(run['diff_json'])),
                'instruction_applied': revised['user_instruction_applied'],
                'instruction_evidence_in_content': bool(revised['instruction_trace']) and revised['instruction_trace'][0]['evidence'] in revised['content'],
                'instruction_logged': any(item.get('issue_id')=='user_instruction' and item.get('evidence') for item in revised['change_log'] if isinstance(item,dict)),
                'prediction_uses_candidate': prediction['details']['input_snapshot']['draft_version_id'] == second['draft_version_id'],
                'assessment_not_prediction': query_one('SELECT COUNT(*) n FROM predictions WHERE project_id=?',(project_id,))['n'] == 1,
            }, ensure_ascii=False))
        """)
        self.assertTrue(result["blocked_before_candidate"])
        self.assertTrue(result["new_version"])
        self.assertTrue(result["source_preserved"])
        self.assertTrue(result["gate_reset"])
        self.assertTrue(result["selected_saved"])
        self.assertTrue(result["diff_saved"])
        self.assertTrue(result["instruction_applied"])
        self.assertTrue(result["instruction_evidence_in_content"])
        self.assertTrue(result["instruction_logged"])
        self.assertTrue(result["prediction_uses_candidate"])
        self.assertTrue(result["assessment_not_prediction"])

    def test_identical_manual_save_does_not_create_duplicate_version(self):
        result = run_isolated("""
            import json
            from app.api.creation import DraftIn, ProjectIn, create_project, generate_draft, save_draft
            from app.db import init_db, query, query_one, transaction
            from app.main import _ensure_settings
            from app.seed import seed_if_empty

            init_db(); _ensure_settings()
            with transaction(): seed_if_empty()
            pid = create_project(ProjectIn(title='重复版本拦截', source_chain=['测试']))['data']['id']
            generate_draft(pid)
            current = query_one('SELECT * FROM draft_versions WHERE project_id=? ORDER BY version_no DESC LIMIT 1',(pid,))
            rejected = False
            try:
                save_draft(pid, DraftIn(content=current['content'], platform=current['platform']))
            except Exception as exc:
                rejected = '完全相同' in str(exc)
            print(json.dumps({'rejected':rejected,
                              'count':len(query('SELECT id FROM draft_versions WHERE project_id=?',(pid,)))}, ensure_ascii=False))
        """)
        self.assertTrue(result["rejected"])
        self.assertEqual(result["count"], 1)

    def test_human_review_cannot_overwrite_ai_prediction_fields(self):
        result = run_isolated("""
            import json
            from app.api.creation import (CandidateDraftIn, PredictionIn, ProjectIn,
                confirm_candidate_draft, create_project, generate_draft,
                generate_draft_assessment, generate_prediction, save_prediction)
            from app.db import init_db, query_one, transaction
            from app.main import _ensure_settings
            from app.seed import seed_if_empty

            init_db(); _ensure_settings()
            with transaction(): seed_if_empty()
            pid = create_project(ProjectIn(title='预测不可覆盖测试', source_chain=['测试']))['data']['id']
            generate_draft(pid)
            assessment = generate_draft_assessment(pid)['data']
            confirm_candidate_draft(pid, CandidateDraftIn(draft_version_id=assessment['draft_version_id']))
            generated = generate_prediction(pid)['data']
            before = query_one('SELECT * FROM predictions WHERE id=?',(generated['id'],))
            save_prediction(pid, PredictionIn(
                goal='人工伪造目标', expected_bucket='人工改档位', confidence=.99,
                strengths='人工改优势', risks='人工改风险', hypothesis='人工改假设',
                user_judgement='我认为开头还可更稳'))
            after = query_one('SELECT * FROM predictions WHERE id=?',(generated['id'],))
            override = json.loads(after['override_json'])
            fields = ['goal','expected_bucket','confidence','strengths','risks','hypothesis']
            print(json.dumps({
                'ai_unchanged': all(before[key] == after[key] for key in fields),
                'review_saved': after['user_judgement'] == '我认为开头还可更稳',
                'original_snapshot': override['ai_original']['expected_bucket'] == before['expected_bucket'],
                'user_view_separate': override['user_view']['expected_bucket'] == '人工改档位',
            }, ensure_ascii=False))
        """)
        self.assertTrue(result["ai_unchanged"])
        self.assertTrue(result["review_saved"])
        self.assertTrue(result["original_snapshot"])
        self.assertTrue(result["user_view_separate"])

    def test_external_publication_is_deduplicated_and_supports_actual_only_retro(self):
        result = run_isolated("""
            import datetime, json
            from app.api.planning import ExternalPublicationIn, import_external_publication
            from app.api.retro import SnapshotIn, capture_snapshot, generate_retro
            from app.db import init_db, query_one, transaction
            from app.main import _ensure_settings
            from app.seed import seed_if_empty

            init_db(); _ensure_settings()
            with transaction(): seed_if_empty()
            body = ExternalPublicationIn(
                platform='抖音', url='https://example.com/spontaneous-work',
                final_title='随手发布的真实内容',
                published_at=(datetime.datetime.now()-datetime.timedelta(days=2)).isoformat(timespec='minutes'))
            created = import_external_publication(body)['data']
            duplicate = import_external_publication(body)['data']
            capture_snapshot(created['id'], SnapshotIn(
                tn='T+1', plays=1200, likes=72, comments=12, collects=36, shares=8))
            interim = generate_retro(created['id'])['data']
            interim_status = query_one('SELECT status FROM content_tasks WHERE id=?',(created['task_id'],))['status']
            capture_snapshot(created['id'], SnapshotIn(
                tn='T+7', plays=2600, likes=156, comments=25, collects=88, shares=21))
            retro = generate_retro(created['id'])['data']
            print(json.dumps({
                'entry_mode': query_one('SELECT entry_mode FROM publications WHERE id=?',(created['id'],))['entry_mode'],
                'prediction_id': query_one('SELECT prediction_id FROM publications WHERE id=?',(created['id'],))['prediction_id'],
                'deduplicated': duplicate['deduplicated'],
                'mode': retro['report']['mode'],
                'interim_stage': interim['stage'],
                'final_stage': retro['stage'],
                'versions': query_one('SELECT COUNT(*) n FROM retro_reports WHERE publication_id=?',(created['id'],))['n'],
                'interim_status': interim_status,
                'observation_count': query_one('SELECT COUNT(*) n FROM prediction_observations WHERE publication_id=?',(created['id'],))['n'],
                'task_status': query_one('SELECT status FROM content_tasks WHERE id=?',(created['task_id'],))['status'],
            }, ensure_ascii=False))
        """)
        self.assertEqual(result["entry_mode"], "external")
        self.assertIsNone(result["prediction_id"])
        self.assertTrue(result["deduplicated"])
        self.assertEqual(result["mode"], "actual_only")
        self.assertEqual(result["interim_stage"], "INTERIM")
        self.assertEqual(result["final_stage"], "FINAL")
        self.assertEqual(result["versions"], 2)
        self.assertEqual(result["interim_status"], "WAITING_RETRO")
        self.assertEqual(result["observation_count"], 0)
        self.assertEqual(result["task_status"], "RETRO_COMPLETED")

    def test_same_creation_project_cannot_create_duplicate_active_plan_cards(self):
        result = run_isolated("""
            import json
            from app.api.creation import ProjectIn, create_project
            from app.api.planning import TaskIn, create_task
            from app.db import init_db, query_one, transaction
            from app.main import _ensure_settings
            from app.seed import seed_if_empty

            init_db(); _ensure_settings()
            with transaction(): seed_if_empty()
            project_id = create_project(ProjectIn(title='去重计划任务', source_chain=['测试']))['data']['id']
            first = create_task(TaskIn(title='去重计划任务', platform='抖音', project_id=project_id))['data']
            second = create_task(TaskIn(title='去重计划任务', platform='抖音', project_id=project_id))['data']
            print(json.dumps({
                'same_id': first['id'] == second['id'],
                'existing': second['existing'],
                'count': query_one("SELECT COUNT(*) n FROM content_tasks WHERE project_id=? AND status<>'CANCELLED'",(project_id,))['n'],
            }))
        """)
        self.assertTrue(result["same_id"])
        self.assertTrue(result["existing"])
        self.assertEqual(result["count"], 1)

    def test_frontend_exposes_explicit_finalization_and_external_import(self):
        creation = (ROOT / "frontend/assets/js/pages/create.js").read_text(encoding="utf-8")
        components = (ROOT / "frontend/assets/js/components.js").read_text(encoding="utf-8")
        self.assertIn("确认当前稿为最终版并锁定预测 → 进入待发布", creation)
        self.assertIn("稿件已变化", creation)
        self.assertIn("AI 稿件体检", creation)
        self.assertIn("应用所选建议，生成一个新版本", creation)
        self.assertIn("确认 v'+latestDraft.version_no+' 为候选终稿", creation)
        self.assertIn("补录工作台外已发布内容", creation)
        self.assertIn("open-publish-import", components)
        self.assertIn("/api/publications/import", components)


if __name__ == "__main__":
    unittest.main()
