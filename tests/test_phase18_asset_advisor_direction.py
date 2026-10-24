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


def run_isolated(source: str, timeout: int = 120) -> dict:
    with tempfile.TemporaryDirectory(prefix="jianzhou-phase18-") as temp:
        env = os.environ.copy()
        env.update({"JIANZHOU_DATA_DIR": temp, "JIANZHOU_DEMO": "1", "PYTHONUTF8": "1"})
        result = subprocess.run(
            [sys.executable, "-c", textwrap.dedent(source)], cwd=ROOT, env=env,
            text=True, encoding="utf-8", capture_output=True, timeout=timeout,
        )
        if result.returncode:
            raise AssertionError(f"isolated process failed\nstdout={result.stdout}\nstderr={result.stderr}")
        return json.loads(result.stdout.strip())


class Phase18LifecycleTests(unittest.TestCase):
    def test_analysis_assets_are_grouped_by_source_and_versions_are_preserved(self):
        result = run_isolated("""
            import json
            from app.asset_center import asset_content, sync_asset_index
            from app.db import init_db, jdumps, query, query_one, transaction
            from app.main import _ensure_settings
            from app.seed import seed_if_empty
            init_db(); _ensure_settings()
            with transaction(): seed_if_empty()
            uid = query_one('SELECT id FROM users ORDER BY created_at LIMIT 1')['id']
            source = query_one('SELECT id FROM source_contents WHERE user_id=? LIMIT 1',(uid,))
            if source:
                from app.ids import new_id
                from app.api import now
                for n in range(2):
                    aid = new_id('A')
                    query_one('SELECT 1')
                    from app.db import execute
                    execute('INSERT INTO analyses(id,user_id,source_content_id,status,result_json,model,cost,created_at) VALUES(?,?,?,?,?,?,?,?)',
                            (aid,uid,source['id'],'done',jdumps({'topic':f'测试拆解v{n+1}','audience':'知识创作者'}),'demo',0,now()))
            sync_asset_index(uid); sync_asset_index(uid)
            groups = query("SELECT * FROM asset_records WHERE user_id=? AND entity_type='analysis_group' AND deleted_at IS NULL",(uid,))
            legacy = query_one("SELECT COUNT(*) n FROM asset_records WHERE user_id=? AND asset_type='analysis' AND entity_type IN ('analysis','content_artifact') AND deleted_at IS NULL",(uid,))['n']
            detail = asset_content(groups[0]) if groups else {}
            print(json.dumps({'groups':len(groups),'legacy':legacy,'versions':len(detail.get('versions',[])),
                              'version_count':(groups[0] and json.loads(groups[0]['metadata_json']).get('version_count')) if groups else 0}))
        """)
        self.assertGreater(result["groups"], 0)
        self.assertEqual(result["legacy"], 0)
        self.assertGreaterEqual(result["versions"], 2)
        self.assertGreaterEqual(result["version_count"], 2)

    def test_cycle_change_stales_old_draft_and_computes_real_target(self):
        result = run_isolated("""
            import json
            from app.api.accounts import CycleIn, update_cycle
            from app.api import now
            from app.db import execute, init_db, jdumps, query_one, transaction
            from app.main import _ensure_settings
            from app.seed import seed_if_empty
            init_db(); _ensure_settings()
            with transaction(): seed_if_empty()
            uid = query_one('SELECT id FROM users ORDER BY created_at LIMIT 1')['id']
            execute('INSERT INTO operation_plan_drafts(id,user_id,week_start,status,plan_json,model,provider,created_at) VALUES(?,?,?,?,?,?,?,?)',
                    ('old-plan',uid,'2026-08-03','DRAFT',jdumps({'summary':'旧草案'}),'demo','demo',now()))
            response = update_cycle(CycleIn(goal='验证AI工作流选题',platforms='抖音 + 小红书',hours_per_week=8,
                                            complexity='中等',promotion='内容测试',rhythm='稳定更新'))['data']
            old = query_one('SELECT status,stale_at FROM operation_plan_drafts WHERE id=?',('old-plan',))
            print(json.dumps({'status':old['status'],'stale':bool(old['stale_at']),
                              'target':response['cycle']['weekly_target'],'effects':len(response['effects'])}))
        """)
        self.assertEqual(result["status"], "STALE")
        self.assertTrue(result["stale"])
        self.assertEqual(result["target"], 3)
        self.assertEqual(result["effects"], 3)

    def test_direction_runs_decisions_and_drafts_keep_lineage(self):
        result = run_isolated("""
            import json
            from app.api.creation import (DirectionIn, DraftGenerateIn, ProjectIn, choose_direction,
                                           create_project, generate_directions, generate_draft, get_project)
            from app.db import init_db, query_one, transaction
            from app.main import _ensure_settings
            from app.seed import seed_if_empty
            init_db(); _ensure_settings()
            with transaction(): seed_if_empty()
            pid = create_project(ProjectIn(title='方向版本化验收',source_chain=['手动创建']))['data']['id']
            candidates = generate_directions(pid)['data']
            item = candidates[0]
            choose_direction(pid, DirectionIn(direction=item['type']+'：'+item['title'],run_id=item['run_id'],candidate_index=item['candidate_index'],reason='验收选择'))
            draft = generate_draft(pid, DraftGenerateIn(style_mode='structure',platform='抖音口播'))['data']
            project = get_project(pid)['data']
            linked = query_one('SELECT direction_decision_id FROM draft_versions WHERE id=?',(draft['id'],))
            print(json.dumps({'runs':len(project['direction_runs']),'decisions':len(project['direction_decisions']),
                              'active':bool(project['direction_decision']),'draft_link':linked['direction_decision_id']}))
        """, timeout=180)
        self.assertEqual(result["runs"], 1)
        self.assertEqual(result["decisions"], 1)
        self.assertTrue(result["active"])
        self.assertTrue(result["draft_link"])

    def test_visual_page_no_longer_interpolates_json_into_vue_template(self):
        text = (ROOT / "frontend" / "assets" / "js" / "pages" / "visuals.js").read_text(encoding="utf-8")
        self.assertNotIn("${JSON.stringify(LAYOUT_LABELS)}", text)
        self.assertIn("v-for=\"(label,key) in layoutLabels\"", text)

    def test_approved_advisor_plan_opens_one_creation_project_and_tracks_progress(self):
        result = run_isolated("""
            import datetime, json
            from app.api import now
            from app.api.advisor import approve_plan, list_plans
            from app.api.creation import (DirectionIn, DraftGenerateIn, choose_direction,
                                           generate_directions, generate_draft)
            from app.api.planning import board, start_task_creation, week
            from app.db import execute, init_db, jdumps, query_one, transaction
            from app.main import _ensure_settings
            from app.seed import seed_if_empty
            init_db(); _ensure_settings()
            with transaction(): seed_if_empty()
            uid = query_one('SELECT id FROM users ORDER BY created_at LIMIT 1')['id']
            monday = datetime.date.today() - datetime.timedelta(days=datetime.date.today().weekday())
            plan = {'items':[{'title':'把运营建议真正写成一条内容','platform':'抖音',
                              'date':monday.isoformat(),'estimate_h':1.5,
                              'reason':'近期对标与账号数据都指向这个选题',
                              'evidence_ids':['signal-demo-1']}]}
            execute('INSERT INTO operation_plan_drafts(id,user_id,week_start,status,plan_json,model,provider,created_at) VALUES(?,?,?,?,?,?,?,?)',
                    ('flow-plan',uid,monday.isoformat(),'DRAFT',jdumps(plan),'demo','demo',now()))
            approved = approve_plan('flow-plan')['data']
            tid = approved['task_ids'][0]
            listed = next(plan for plan in list_plans(scope='active',limit=20,offset=0)['data'] if plan['id']=='flow-plan')
            task_before = query_one('SELECT * FROM content_tasks WHERE id=?',(tid,))
            first = start_task_creation(tid)['data']
            second = start_task_creation(tid)['data']
            candidates = generate_directions(first['project_id'])['data']
            item = candidates[0]
            choose_direction(first['project_id'], DirectionIn(
                direction=item['type']+'：'+item['title'], run_id=item['run_id'],
                candidate_index=item['candidate_index'], reason='链路验收'))
            generate_draft(first['project_id'], DraftGenerateIn(style_mode='structure',platform='抖音口播'))
            task_after = query_one('SELECT * FROM content_tasks WHERE id=?',(tid,))
            board_data = board()['data']
            card = next(card for col in board_data['columns'] for card in col['cards'] if card['id']==tid)
            calendar_event = next(ev for day in week()['data']['days'] for ev in day['events'] if ev['task_id']==tid)
            print(json.dumps({
                'before_status':task_before['status'],
                'plan_id':task_before['operation_plan_draft_id'],
                'listed_same_task':listed['tasks'][0]['id']==tid,
                'listed_action':listed['tasks'][0]['creation_action'],
                'same_project':first['project_id']==second['project_id'],
                'second_existing':second['existing'],
                'after_status':task_after['status'],
                'card_action':card['creation_action'],
                'badges':[badge['t'] for badge in card['badges']],
                'calendar_action':calendar_event['action'],
            }, ensure_ascii=False))
        """, timeout=180)
        self.assertEqual(result["before_status"], "SELECTED")
        self.assertEqual(result["plan_id"], "flow-plan")
        self.assertTrue(result["listed_same_task"])
        self.assertEqual(result["listed_action"], "开始创作")
        self.assertTrue(result["same_project"])
        self.assertTrue(result["second_existing"])
        self.assertEqual(result["after_status"], "DRAFTING")
        self.assertEqual(result["card_action"], "继续创作")
        self.assertIn("AI建议", result["badges"])
        self.assertEqual(result["calendar_action"], "继续创作")

    def test_plan_page_exposes_creation_entries_in_board_and_calendar(self):
        text = (ROOT / "frontend" / "assets" / "js" / "pages" / "create.js").read_text(encoding="utf-8")
        account_text = (ROOT / "frontend" / "assets" / "js" / "pages" / "base.js").read_text(encoding="utf-8")
        self.assertIn("startCreationById", text)
        self.assertIn("从计划到创作", text)
        self.assertIn("ev.action+' →'", text)
        self.assertIn("startAdvisorCreation", account_text)
        self.assertIn("打开完整内容计划", account_text)


if __name__ == "__main__":
    unittest.main()
