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
    with tempfile.TemporaryDirectory(prefix="jianzhou-target-") as temp:
        env = os.environ.copy()
        env.update({"JIANZHOU_DATA_DIR": temp, "JIANZHOU_DEMO": "1", "PYTHONUTF8": "1"})
        result = subprocess.run(
            [sys.executable, "-c", textwrap.dedent(source)], cwd=ROOT, env=env,
            text=True, encoding="utf-8", capture_output=True, timeout=60,
        )
        if result.returncode:
            raise AssertionError(f"isolated process failed\nstdout={result.stdout}\nstderr={result.stderr}")
        return json.loads(result.stdout.strip())


class TargetModeLifecycleTests(unittest.TestCase):
    def test_profile_candidate_preview_commit_and_archive_revision(self):
        result = run_isolated("""
            import json
            from app.db import execute, init_db, jdumps, query, query_one, transaction
            from app.main import _ensure_settings
            from app.seed import seed_if_empty
            from app.api.profile import (CandidateEditIn, ChangeSetIn, confirm_candidate,
                edit_candidate, preview_change_set, confirm_change_set,
                ArchiveRevisionIn, revise_interview_archive)
            init_db(); _ensure_settings()
            with transaction(): seed_if_empty()
            execute("INSERT INTO interview_sessions VALUES(?,?,?,?,?,?,?,?,?)",
                    ('iv-target','u-zhou','档案纠偏','text','REVIEW_REQUIRED',1,1,'now','now'))
            execute("INSERT INTO interview_turns VALUES(?,?,?,?,?,?,?)",
                    ('it-target','iv-target',1,'user','我希望内容更强调真实实践',None,'now'))
            execute("INSERT INTO profile_field_candidates"
                    "(id,user_id,session_id,field_path,value,inference_type,confidence,evidence,status,created_at)"
                    " VALUES(?,?,?,?,?,?,?,?,?,?)",
                    ('fc-target','u-zhou','iv-target','代表观点','旧值','explicit',0.9,'访谈原话','pending','now'))
            edit_candidate('fc-target', CandidateEditIn(value='真实实践优先于概念包装', decision_note='本人修正'))
            confirm_candidate('fc-target')
            preview = preview_change_set(ChangeSetIn(session_id='iv-target'))['data']
            committed = confirm_change_set(preview['id'])['data']
            archive = revise_interview_archive('iv-target', ArchiveRevisionIn(
                title='档案纠偏访谈', summary='强调真实实践', corrected_transcript='我：我希望内容更强调真实实践',
                tags=['价值观'], notes='本人校正'))['data']
            print(json.dumps({
                'active_count': query_one("SELECT COUNT(*) n FROM profile_versions WHERE user_id='u-zhou' AND active=1")['n'],
                'version': committed['version_no'],
                'opinion': json.loads(query_one("SELECT content_json FROM profile_versions WHERE id=?",(committed['profile_version_id'],))['content_json'])['key_opinion'],
                'candidate_status': query_one("SELECT status FROM profile_field_candidates WHERE id='fc-target'")['status'],
                'archive_revision': archive['revision_no'],
                'raw_turns': query_one("SELECT COUNT(*) n FROM interview_turns WHERE session_id='iv-target'")['n'],
            }))
        """)
        self.assertEqual(result["active_count"], 1)
        self.assertEqual(result["opinion"], "真实实践优先于概念包装")
        self.assertEqual(result["candidate_status"], "applied")
        self.assertEqual(result["archive_revision"], 1)
        self.assertEqual(result["raw_turns"], 1)

    def test_ai_prediction_cover_and_advisor_are_persistent(self):
        result = run_isolated("""
            import json
            from app.db import init_db, query_one, transaction
            from app.main import _ensure_settings
            from app.seed import seed_if_empty
            from app.api.creation import (CandidateDraftIn, ProjectIn, confirm_candidate_draft,
                create_project, generate_draft, generate_draft_assessment, generate_prediction,
                lock_prediction)
            from app.api.visuals import CoverCreateIn, create_cover
            from app.api.advisor import PlanDraftIn, generate_portrait, generate_plan, approve_plan
            import datetime
            init_db(); _ensure_settings()
            with transaction(): seed_if_empty()
            pid = create_project(ProjectIn(title='AI工作流如何真正落地', source_chain=['手动创建']))['data']['id']
            draft = generate_draft(pid)['data']
            assessed = generate_draft_assessment(pid)['data']
            confirm_candidate_draft(pid, CandidateDraftIn(draft_version_id=assessed['draft_version_id']))
            prediction = generate_prediction(pid)['data']
            locked = lock_prediction(pid)['data']
            cover = create_cover(CoverCreateIn(creation_project_id=pid,
                draft_version_id=query_one("SELECT id FROM draft_versions WHERE project_id=? ORDER BY version_no DESC",(pid,))['id']))['data']
            portrait = generate_portrait()['data']
            plan = generate_plan(PlanDraftIn(week_start=datetime.date.today(), desired_posts=3))['data']
            approved = approve_plan(plan['id'])['data']
            print(json.dumps({
                'draft_nonempty': bool(draft['content'].strip()),
                'prediction_dimensions': len(prediction['details']['dimensions']),
                'confidence_samples': prediction['details']['calibration']['completed_retro_samples'],
                'locked': locked['locked'], 'integrity': len(locked['integrity_hash']),
                'cover_status': cover['status'], 'cover_saved': query_one("SELECT COUNT(*) n FROM cover_projects WHERE id=?",(cover['id'],))['n'],
                'portrait_boundary': portrait['diagnosis']['boundary'],
                'approved_tasks': len(approved['task_ids']),
            }, ensure_ascii=False))
        """)
        self.assertTrue(result["draft_nonempty"])
        self.assertGreaterEqual(result["prediction_dimensions"], 5)
        self.assertTrue(result["locked"])
        self.assertEqual(result["integrity"], 64)
        self.assertEqual(result["cover_status"], "PREVIEW_READY")
        self.assertEqual(result["cover_saved"], 1)
        self.assertIn("不自动改写", result["portrait_boundary"])
        self.assertEqual(result["approved_tasks"], 3)

    def test_publish_snapshots_retro_append_observation(self):
        result = run_isolated("""
            import json
            from app.db import execute, init_db, query_one, transaction
            from app.main import _ensure_settings
            from app.seed import seed_if_empty
            from app.api.creation import (CandidateDraftIn, ProjectIn, confirm_candidate_draft,
                create_project, generate_draft, generate_draft_assessment, generate_prediction,
                lock_prediction)
            from app.api.planning import PublicationIn, register_publication
            from app.api.retro import SnapshotIn, capture_snapshot, generate_retro
            init_db(); _ensure_settings()
            with transaction(): seed_if_empty()
            pid = create_project(ProjectIn(title='复盘链路测试', source_chain=['手动创建']))['data']['id']
            generate_draft(pid)
            assessed = generate_draft_assessment(pid)['data']
            confirm_candidate_draft(pid, CandidateDraftIn(draft_version_id=assessed['draft_version_id']))
            generate_prediction(pid); finalized = lock_prediction(pid)['data']
            task_id = finalized['task_ids'][0]
            pub = register_publication(PublicationIn(task_id=task_id,platform='抖音',url='https://example.com/target',final_title='复盘链路测试'))['data']
            snap = capture_snapshot(pub['id'], SnapshotIn(tn='T+1',plays=1000,likes=80,comments=10,collects=30,shares=12,follows=6,completion_rate=41.2))['data']
            interim = generate_retro(pub['id'])['data']
            capture_snapshot(pub['id'], SnapshotIn(tn='T+7',plays=2100,likes=170,comments=22,collects=68,shares=26,follows=15,completion_rate=45.1))
            retro = generate_retro(pub['id'])['data']
            print(json.dumps({
                'schedules': len(pub['schedules']), 'shares': retro['report']['actual']['shares'],
                'comparison': bool(retro['report']['comparison']),
                'interim_stage': interim['stage'], 'final_stage': retro['stage'],
                'observations': query_one("SELECT COUNT(*) n FROM prediction_observations WHERE publication_id=?",(pub['id'],))['n'],
                'suggestion_status': query_one("SELECT status FROM rule_suggestions WHERE id=?",(retro['rule_suggestion_id'],))['status'],
            }))
        """)
        self.assertEqual(result["schedules"], 3)
        self.assertEqual(result["shares"], 26)
        self.assertTrue(result["comparison"])
        self.assertEqual(result["interim_stage"], "INTERIM")
        self.assertEqual(result["final_stage"], "FINAL")
        self.assertEqual(result["observations"], 2)
        self.assertEqual(result["suggestion_status"], "pending")


if __name__ == "__main__":
    unittest.main()
