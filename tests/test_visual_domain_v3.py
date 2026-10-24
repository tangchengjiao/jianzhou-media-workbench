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


def isolated(source: str) -> dict:
    with tempfile.TemporaryDirectory(prefix="jianzhou-visual-v3-") as temp:
        env = os.environ.copy()
        env.update({"JIANZHOU_DATA_DIR": temp, "JIANZHOU_DEMO": "1", "PYTHONUTF8": "1"})
        result = subprocess.run([sys.executable, "-c", textwrap.dedent(source)], cwd=ROOT, env=env,
                                text=True, encoding="utf-8", capture_output=True, timeout=180)
        if result.returncode:
            raise AssertionError(f"isolated process failed\nstdout={result.stdout}\nstderr={result.stderr}")
        return json.loads(result.stdout.strip())


class VisualDomainV3Tests(unittest.TestCase):
    def test_visual_factory_layout_keeps_management_actions_and_container_reflow(self):
        script = (ROOT / "frontend" / "assets" / "js" / "pages" / "visuals.js").read_text(encoding="utf-8")
        styles = (ROOT / "frontend" / "assets" / "css" / "app.css").read_text(encoding="utf-8")
        brand_index = script.index("tab==='brand'")
        editor_index = script.index('class="visual-preset-editor"')
        history_index = script.index("historyKind==='cover'")
        self.assertLess(brand_index, editor_index)
        self.assertLess(editor_index, history_index)
        self.assertIn("deleteCover(batch, item)", script)
        self.assertIn("deleteAsset(asset)", script)
        self.assertIn("deletePromptPreset(item)", script)
        self.assertIn("saveAndRegeneratePage(editingPage)", script)
        self.assertIn("graphic.narrative_mode", script)
        self.assertIn("?force=true", script)
        self.assertIn("@container (max-width:1180px)", styles)
        self.assertIn(".visual-upload-card .visual-upload-copy", styles)
        self.assertIn(".graphic-saved-page-editor", styles)
        self.assertIn(".cover-content-contract", styles)
        self.assertIn("grid-template-columns:repeat(3,minmax(0,360px))", styles)
        self.assertIn("width:calc(100% - 4px)", styles)

    def test_cover_contract_removes_internal_labels_metrics_and_duplicate_visible_text(self):
        result = isolated("""
            import json
            from app.api.creation import DraftGenerateIn, ProjectIn, create_project, generate_draft
            from app.api.visuals import CoverCreateIn, create_cover
            from app.db import init_db, query_one, transaction
            from app.main import _ensure_settings
            from app.seed import seed_if_empty
            init_db(); _ensure_settings()
            with transaction(): seed_if_empty()
            title = 'AI Skill 是什么、构成与工作原理'
            pid = create_project(ProjectIn(title=title, source_chain=['手动创建']))['data']['id']
            draft = generate_draft(pid, DraftGenerateIn(style_mode='structure', platform='抖音口播'))['data']
            made = create_cover(CoverCreateIn(
                creation_project_id=pid, draft_version_id=draft['id'], generation_mode='local_template',
                fields={'main_topic':title, 'title':title, 'subtitle':'收藏率 > 账号均值',
                        'features':['观点型','市场热点','讲清工作原理'],
                        'keywords':[title,'SG-HOT-543d5801cdc6a07f7554','AI']}))['data']
            row = query_one('SELECT fields_json,prompt,generation_json FROM cover_projects WHERE id=?',(made['id'],))
            fields = json.loads(row['fields_json']); generation = json.loads(row['generation_json'])
            print(json.dumps({'fields':fields,'prompt':row['prompt'],
                              'visible':generation['layout_report']['visible_text']}, ensure_ascii=False))
        """)
        self.assertEqual(result["fields"]["title"], "AI Skill 是什么、构成与工作原理")
        self.assertEqual(result["fields"]["subtitle"], "")
        self.assertEqual(result["fields"]["features"], ["讲清工作原理"])
        self.assertNotIn("观点型", result["prompt"])
        self.assertNotIn("市场热点", result["prompt"])
        self.assertNotIn("收藏率 > 账号均值", result["prompt"])
        self.assertEqual(result["visible"], ["AI Skill 是什么、构成与工作原理", "讲清工作原理"])

    def test_prompt_run_persists_source_and_three_prompt_layers(self):
        result = isolated("""
            import json
            from app.api.creation import DraftGenerateIn, ProjectIn, create_project, generate_draft
            from app.api.visuals import PromptOptimizeIn, list_prompt_presets, optimize_prompt
            from app.db import init_db, query_one, transaction
            from app.main import _ensure_settings
            from app.seed import seed_if_empty
            init_db(); _ensure_settings()
            with transaction(): seed_if_empty()
            pid = create_project(ProjectIn(title='视觉提示词链路', source_chain=['手动创建']))['data']['id']
            draft = generate_draft(pid, DraftGenerateIn(style_mode='structure', platform='小红书图文'))['data']
            preset = [x for x in list_prompt_presets('cover')['data'] if x['scope']=='cover'][0]
            made = optimize_prompt(PromptOptimizeIn(
                creation_project_id=pid, draft_version_id=draft['id'], output_kind='cover',
                preset_id=preset['id'], raw_prompt='更像产品发布会，但不要堆字'))['data']
            row = query_one('SELECT * FROM visual_prompt_runs WHERE id=?', (made['id'],))
            source = json.loads(row['source_snapshot_json'])
            print(json.dumps({'raw':row['raw_user_prompt'], 'optimized':bool(row['optimized_prompt']),
                              'compiled':bool(row['compiled_prompt']), 'draft':source['draft_version_id'],
                              'hash':len(row['source_text_hash'])}, ensure_ascii=False))
        """)
        self.assertEqual(result["raw"], "更像产品发布会，但不要堆字")
        self.assertTrue(result["optimized"] and result["compiled"])
        self.assertTrue(result["draft"])
        self.assertEqual(result["hash"], 64)

    def test_graphic_document_pages_versions_finalize_and_delivery_are_linked(self):
        result = isolated("""
            import json
            from app.api.creation import DraftGenerateIn, ProjectIn, create_project, generate_draft
            from app.api.visuals import (GraphicDocumentCreateIn, GraphicOutlineIn,
                build_graphic_outline, create_graphic_document, finalize_graphic_document,
                get_graphic_document, regenerate_graphic_page)
            from app.db import init_db, query_one, transaction
            from app.main import _ensure_settings
            from app.seed import seed_if_empty
            init_db(); _ensure_settings()
            with transaction(): seed_if_empty()
            pid = create_project(ProjectIn(title='完整图文文档', source_chain=['手动创建']))['data']['id']
            draft = generate_draft(pid, DraftGenerateIn(style_mode='structure', platform='小红书图文'))['data']
            outline = build_graphic_outline(GraphicOutlineIn(
                creation_project_id=pid, draft_version_id=draft['id'], page_count=6))['data']
            document = create_graphic_document(GraphicDocumentCreateIn(
                creation_project_id=pid, draft_version_id=draft['id'], title='完整图文文档',
                generation_mode='local_template', pages=outline['pages']))['data']
            page = document['pages'][2]
            revision = regenerate_graphic_page(page['id'])['data']
            finalized = finalize_graphic_document(document['id'])['data']
            saved = get_graphic_document(document['id'])['data']
            project = query_one('SELECT visual_status,visual_deliverable_id FROM creation_projects WHERE id=?',(pid,))
            print(json.dumps({'pages':len(saved['pages']), 'roles':[x['role'] for x in saved['pages']],
                              'revision':revision['revision_no'], 'status':saved['status'],
                              'deliverable':finalized['deliverable_id'], 'project':project}, ensure_ascii=False))
        """)
        self.assertEqual(result["pages"], 6)
        self.assertGreaterEqual(len(set(result["roles"])), 4)
        self.assertEqual(result["revision"], 2)
        self.assertEqual(result["status"], "FINALIZED")
        self.assertEqual(result["project"]["visual_status"], "READY")
        self.assertEqual(result["project"]["visual_deliverable_id"], result["deliverable"])

    def test_reference_images_are_encoded_for_image_provider(self):
        from app.image_provider import RealImageProvider
        from PIL import Image

        with tempfile.TemporaryDirectory(prefix="jianzhou-ref-") as temp:
            path = pathlib.Path(temp) / "person.png"
            Image.new("RGB", (24, 24), "#123456").save(path)
            values = RealImageProvider._reference_data_urls([str(path), "https://cdn.example/style.png"])
        self.assertEqual(len(values), 2)
        self.assertTrue(values[0].startswith("data:image/png;base64,"))
        self.assertEqual(values[1], "https://cdn.example/style.png")

    def test_selected_cover_requires_explicit_force_then_detaches_cleanly(self):
        result = isolated("""
            import json
            from app.api.creation import DraftGenerateIn, ProjectIn, create_project, generate_draft
            from app.api.visuals import CoverCreateIn, create_cover, delete_cover, select_cover
            from app.db import init_db, query_one, transaction
            from app.errors import AppError
            from app.main import _ensure_settings
            from app.seed import seed_if_empty
            init_db(); _ensure_settings()
            with transaction(): seed_if_empty()
            pid = create_project(ProjectIn(title='候选删除保护', source_chain=['手动创建']))['data']['id']
            draft = generate_draft(pid, DraftGenerateIn(style_mode='structure', platform='抖音口播'))['data']
            batch = create_cover(CoverCreateIn(
                creation_project_id=pid, draft_version_id=draft['id'], generation_mode='local_template', quantity=2))['data']
            first, second = batch['items']
            select_cover(first['id'])
            blocked = ''
            try:
                delete_cover(first['id'])
            except AppError as error:
                blocked = error.code
            forced = delete_cover(first['id'], force=True)['data']
            remaining = query_one('SELECT COUNT(*) n FROM cover_projects WHERE visual_batch_id=? AND deleted_at IS NULL',(batch['batch_id'],))['n']
            project = query_one('SELECT visual_status,visual_deliverable_id FROM creation_projects WHERE id=?',(pid,))
            print(json.dumps({'blocked':blocked,'forced':forced,'remaining':remaining,'project':project}))
        """)
        self.assertEqual(result["blocked"], "CONTENT_STATE_INVALID")
        self.assertEqual(result["remaining"], 1)
        self.assertTrue(result["forced"]["detached"])
        self.assertEqual(result["project"]["visual_status"], "PENDING")
        self.assertIsNone(result["project"]["visual_deliverable_id"])

    def test_narrative_modes_and_graphic_layouts_are_not_fixed_to_one_sequence(self):
        result = isolated("""
            import json
            from app.api.creation import DraftGenerateIn, ProjectIn, create_project, generate_draft
            from app.api.visuals import GraphicDocumentCreateIn, GraphicOutlineIn, build_graphic_outline, create_graphic_document
            from app.db import init_db, transaction
            from app.main import _ensure_settings
            from app.seed import seed_if_empty
            init_db(); _ensure_settings()
            with transaction(): seed_if_empty()
            pid = create_project(ProjectIn(title='结构自适应测试', source_chain=['手动创建']))['data']['id']
            draft = generate_draft(pid, DraftGenerateIn(style_mode='structure', platform='小红书图文'))['data']
            tutorial = build_graphic_outline(GraphicOutlineIn(
                creation_project_id=pid, draft_version_id=draft['id'], page_count=7,
                narrative_mode='tutorial'))['data']
            comparison = build_graphic_outline(GraphicOutlineIn(
                creation_project_id=pid, draft_version_id=draft['id'], page_count=7,
                narrative_mode='comparison'))['data']
            document = create_graphic_document(GraphicDocumentCreateIn(
                creation_project_id=pid, draft_version_id=draft['id'], title='多版式测试',
                generation_mode='local_template', pages=tutorial['pages']))['data']
            print(json.dumps({
                'tutorial':[p['role'] for p in tutorial['pages']],
                'comparison':[p['role'] for p in comparison['pages']],
                'layouts':[p['layout_key'] for p in document['pages']],
                'overflow':[p['revision']['request_json'] for p in document['pages']],
            }, ensure_ascii=False))
        """)
        self.assertNotEqual(result["tutorial"], result["comparison"])
        self.assertIn("steps", result["tutorial"])
        self.assertIn("comparison", result["comparison"])
        self.assertGreaterEqual(len(set(result["layouts"])), 3)

    def test_finalized_graphic_can_be_explicitly_deleted_and_workflow_is_detached(self):
        result = isolated("""
            import json
            from app.api.creation import DraftGenerateIn, ProjectIn, create_project, generate_draft
            from app.api.visuals import (GraphicDocumentCreateIn, GraphicOutlineIn, build_graphic_outline,
                create_graphic_document, delete_graphic_document, finalize_graphic_document, list_graphic_documents)
            from app.db import init_db, query_one, transaction
            from app.errors import AppError
            from app.main import _ensure_settings
            from app.seed import seed_if_empty
            init_db(); _ensure_settings()
            with transaction(): seed_if_empty()
            pid = create_project(ProjectIn(title='图文删除测试', source_chain=['手动创建']))['data']['id']
            draft = generate_draft(pid, DraftGenerateIn(style_mode='structure', platform='小红书图文'))['data']
            outline = build_graphic_outline(GraphicOutlineIn(
                creation_project_id=pid, draft_version_id=draft['id'], page_count=5,
                narrative_mode='case_study'))['data']
            document = create_graphic_document(GraphicDocumentCreateIn(
                creation_project_id=pid, draft_version_id=draft['id'], title='可删除定稿',
                generation_mode='local_template', pages=outline['pages']))['data']
            finalize_graphic_document(document['id'])
            blocked = ''
            try:
                delete_graphic_document(document['id'])
            except AppError as error:
                blocked = error.code
            deleted = delete_graphic_document(document['id'], force=True)['data']
            project = query_one('SELECT visual_status,visual_deliverable_id FROM creation_projects WHERE id=?',(pid,))
            print(json.dumps({'blocked':blocked,'deleted':deleted,'visible':len(list_graphic_documents()['data']),
                              'project':project}, ensure_ascii=False))
        """)
        self.assertEqual(result["blocked"], "CONTENT_STATE_INVALID")
        self.assertTrue(result["deleted"]["detached"])
        self.assertEqual(result["visible"], 0)
        self.assertEqual(result["project"]["visual_status"], "PENDING")
        self.assertIsNone(result["project"]["visual_deliverable_id"])


if __name__ == "__main__":
    unittest.main()
