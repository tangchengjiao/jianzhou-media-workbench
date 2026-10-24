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
    with tempfile.TemporaryDirectory(prefix="jianzhou-unified-v2-") as temp:
        env = os.environ.copy()
        env.update({"JIANZHOU_DATA_DIR": temp, "JIANZHOU_DEMO": "1", "PYTHONUTF8": "1"})
        result = subprocess.run(
            [sys.executable, "-c", textwrap.dedent(source)], cwd=ROOT, env=env,
            text=True, encoding="utf-8", capture_output=True, timeout=timeout,
        )
        if result.returncode:
            raise AssertionError(f"isolated process failed\nstdout={result.stdout}\nstderr={result.stderr}")
        return json.loads(result.stdout.strip())


class UnifiedAssetsOperationsVisualV2Tests(unittest.TestCase):
    def test_asset_center_indexes_business_objects_and_lineage_idempotently(self):
        result = run_isolated("""
            import json
            from app.asset_center import sync_asset_index
            from app.db import init_db, query_one, transaction
            from app.main import _ensure_settings
            from app.seed import seed_if_empty
            init_db(); _ensure_settings()
            with transaction(): seed_if_empty()
            first = sync_asset_index(); second = sync_asset_index()
            print(json.dumps({
                'first': first, 'second': second,
                'assets': query_one('SELECT COUNT(*) n FROM asset_records')['n'],
                'relations': query_one('SELECT COUNT(*) n FROM asset_relations')['n'],
                'collections_table': query_one("SELECT COUNT(*) n FROM sqlite_master WHERE type='table' AND name='asset_collections'")['n'],
                'version': query_one('PRAGMA user_version')['user_version'],
            }))
        """)
        self.assertGreater(result["assets"], 0)
        self.assertGreater(result["relations"], 0)
        self.assertEqual(result["second"]["indexed"], 0)
        self.assertEqual(result["collections_table"], 1)
        self.assertGreaterEqual(result["version"], 16)

    def test_operation_context_contains_real_coverage_and_market_evidence(self):
        result = run_isolated("""
            import json
            from app.db import init_db, query_one, transaction
            from app.main import _ensure_settings
            from app.operation_context import build_operation_context
            from app.seed import seed_if_empty
            init_db(); _ensure_settings()
            with transaction(): seed_if_empty()
            uid = query_one("SELECT id FROM users ORDER BY created_at LIMIT 1")['id']
            context = build_operation_context(uid)
            print(json.dumps({
                'id': context['id'], 'coverage': context['coverage'],
                'categories': list(context['context']['market_signals']),
                'refs': len(context['refs']),
            }, ensure_ascii=False))
        """)
        self.assertTrue(result["id"])
        self.assertIn("text_coverage", result["coverage"])
        self.assertIn("analysis_coverage", result["coverage"])
        self.assertGreater(len(result["categories"]), 0)
        self.assertGreater(result["refs"], 0)

    def test_adaptive_templates_never_report_overflow_for_long_copy(self):
        result = run_isolated("""
            import json
            from pathlib import Path
            from PIL import Image
            from app.api.creation import DraftGenerateIn, ProjectIn, create_project, generate_draft
            from app.api.visuals import CoverCreateIn, create_cover, list_templates
            from app.db import init_db, transaction
            from app.main import _ensure_settings
            from app.seed import seed_if_empty
            init_db(); _ensure_settings()
            with transaction(): seed_if_empty()
            pid = create_project(ProjectIn(title='自适应封面测试', source_chain=['手动创建']))['data']['id']
            draft = generate_draft(pid, DraftGenerateIn(style_mode='structure', platform='抖音口播'))['data']
            templates = [item for item in list_templates()['data'] if item['builtin'] and item['template_type']=='cover']
            checks = []
            for index, template in enumerate(templates):
                ratio = ['3:4','9:16','16:9','1:1'][index % 4]
                made = create_cover(CoverCreateIn(
                    creation_project_id=pid, draft_version_id=draft['id'], template_id=template['id'],
                    ratio=ratio, quantity=1, fields={
                        'main_topic':'这是一个特别长的AI知识IP核心主题用于验证模板能否自动缩小换行并始终留在安全区内',
                        'title':'从内容洞察到创作发布再到数据复盘的完整工作流应该怎样真正连接起来',
                        'subtitle':'副标题同样可能很长，所以不能依靠固定坐标硬塞进画布，而必须计算可用空间。',
                        'features':['选题定位','内容结构','素材管理','脚本创作','视觉生成','发布增长','数据复盘','规则校准','长期资产'],
                    }))['data']
                report = made['layout_report']
                path = Path(__import__('app.db', fromlist=['query_one']).query_one(
                    'SELECT preview_path FROM cover_projects WHERE id=?', (made['id'],))['preview_path'])
                with Image.open(path) as image: size = [image.width, image.height]
                checks.append({'overflow':report['overflow'], 'safe':report['safe_box'],
                               'canvas':report['canvas'], 'size':size, 'layout':report['layout']})
            print(json.dumps({'count':len(checks),'checks':checks}, ensure_ascii=False))
        """, timeout=180)
        self.assertEqual(result["count"], 4)
        self.assertTrue(all(not item["overflow"] for item in result["checks"]))
        self.assertTrue(all(item["size"] == item["canvas"] for item in result["checks"]))
        self.assertEqual(len({item["layout"] for item in result["checks"]}), 4)

    def test_graphic_outline_and_visual_batch_lifecycle_are_complete(self):
        result = run_isolated("""
            import json
            from app.api.creation import DraftGenerateIn, ProjectIn, create_project, generate_draft
            from app.api.visuals import (BatchRenameIn, CoverCreateIn, GraphicOutlineIn, RegenerateCoverIn,
                                         build_graphic_outline, create_cover, delete_batch,
                                         list_batches, regenerate_cover, rename_batch, select_cover)
            from app.db import init_db, query_one, transaction
            from app.errors import AppError
            from app.main import _ensure_settings
            from app.seed import seed_if_empty
            init_db(); _ensure_settings()
            with transaction(): seed_if_empty()
            pid = create_project(ProjectIn(title='图文批次闭环', source_chain=['手动创建']))['data']['id']
            draft = generate_draft(pid, DraftGenerateIn(style_mode='structure', platform='小红书图文'))['data']
            outline = build_graphic_outline(GraphicOutlineIn(
                creation_project_id=pid, draft_version_id=draft['id'], page_count=5))['data']
            made = create_cover(CoverCreateIn(
                creation_project_id=pid, draft_version_id=draft['id'], content_type='carousel',
                ratio='3:4', quantity=5, graphic_pages=outline['pages']))['data']
            batch = list_batches('carousel')['data'][0]
            selected = select_cover(batch['items'][0]['id'])['data']
            revision = regenerate_cover(batch['items'][0]['id'], RegenerateCoverIn(
                fields={'title':'封面修订版'}))['data']
            revised_batch = list_batches('carousel')['data'][0]
            renamed = rename_batch(batch['id'], BatchRenameIn(title='本周AI知识图文组'))['data']
            blocked = ''
            try:
                delete_batch(batch['id'])
            except AppError as error:
                blocked = error.code
            print(json.dumps({
                'outline_roles':[p['role'] for p in outline['pages']], 'items':len(made['items']),
                'batch_items':len(batch['items']), 'roles':[i['page_role'] for i in batch['items']],
                'revised_items':len(revised_batch['items']),
                'selected':selected['id'], 'selected_count':sum(1 for i in revised_batch['items'] if i['selected']),
                'revision_no':revision['revision_no'],
                'renamed':renamed['title'], 'visible':len(list_batches('carousel')['data']),
                'deleted_at':query_one('SELECT deleted_at FROM visual_batches WHERE id=?',(batch['id'],))['deleted_at'],
                'delete_blocked':blocked,
            }, ensure_ascii=False))
        """, timeout=180)
        self.assertEqual(result["items"], 5)
        self.assertEqual(result["batch_items"], 5)
        self.assertEqual(result["revised_items"], 6)
        self.assertEqual(result["selected_count"], 1)
        self.assertEqual(result["revision_no"], 2)
        self.assertEqual(result["roles"], result["outline_roles"])
        self.assertEqual(result["renamed"], "本周AI知识图文组")
        self.assertEqual(result["visible"], 1)
        self.assertFalse(result["deleted_at"])
        self.assertEqual(result["delete_blocked"], "CONTENT_STATE_INVALID")


if __name__ == "__main__":
    unittest.main()
