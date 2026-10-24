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
    with tempfile.TemporaryDirectory(prefix="jianzhou-visual-") as temp:
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


class VisualFactoryAndWritingQualityTests(unittest.TestCase):
    def test_complete_image_endpoint_is_normalized_and_async_result_is_parsed(self):
        from app.image_provider import RealImageProvider, normalize_image_endpoint

        self.assertEqual(
            normalize_image_endpoint(
                "https://api.apimart.ai/v1/images/generations", "images/generations"
            ),
            ("https://api.apimart.ai/v1", "images/generations"),
        )

        class Response:
            status_code = 200

            @staticmethod
            def json():
                return {"data": {"status": "completed", "cost": 0.01,
                                 "result": {"images": [{"url": ["https://cdn.example/image.png"]}]}}}

        class Client:
            @staticmethod
            def get(*_args, **_kwargs):
                return Response()

        provider = object.__new__(RealImageProvider)
        provider.base_url = "https://api.apimart.ai/v1"
        provider.api_key = "test"
        provider.auth_mode = "bearer"
        url, cost = provider._poll_async_image(Client(), "task-safe")
        self.assertEqual(url, "https://cdn.example/image.png")
        self.assertEqual(cost, 0.01)

    def test_generated_draft_cleanup_removes_model_markdown_wrappers(self):
        from app.providers import _clean_generated_draft

        cleaned = _clean_generated_draft("# 标题\n\n---\n\n**正文判断**\n\n```\n结尾\n```")
        self.assertEqual(cleaned, "标题\n\n正文判断\n\n结尾")

    def test_style_modes_and_ab_compare_are_saved_as_immutable_versions(self):
        result = run_isolated("""
            import json
            from app.api.creation import (DraftCompareIn, DraftGenerateIn, ProjectIn,
                                           create_project, generate_draft,
                                           generate_draft_comparison)
            from app.db import init_db, query, transaction
            from app.main import _ensure_settings
            from app.seed import seed_if_empty

            init_db(); _ensure_settings()
            with transaction(): seed_if_empty()
            pid = create_project(ProjectIn(title='风格质量回归测试', source_chain=['手动创建']))['data']['id']
            first = generate_draft(pid, DraftGenerateIn(style_mode='structure', platform='抖音'))['data']
            compared = generate_draft_comparison(pid, DraftCompareIn(platform='抖音口播'))['data']
            rows = query("SELECT version_no,style_mode,provider,model,quality_json "
                         "FROM draft_versions WHERE project_id=? ORDER BY version_no", (pid,))
            print(json.dumps({'first_mode':first['style_mode'],
                              'compare_modes':[item['mode'] for item in compared['drafts']],
                              'rows':rows}, ensure_ascii=False))
        """)
        self.assertEqual(result["first_mode"], "structure")
        self.assertEqual(result["compare_modes"], ["structure", "light"])
        self.assertEqual([row["style_mode"] for row in result["rows"]], [
            "structure", "structure", "light",
        ])
        self.assertTrue(all(row["provider"] for row in result["rows"]))
        self.assertTrue(all(row["quality_json"].startswith("{") for row in result["rows"]))

    def test_visual_factory_persists_multi_ratio_carousel_and_history(self):
        result = run_isolated("""
            import json
            from pathlib import Path
            from PIL import Image
            from app.api.creation import DraftGenerateIn, ProjectIn, create_project, generate_draft
            from app.api.visuals import CoverCreateIn, create_cover, list_covers, list_templates
            from app.db import init_db, query, transaction
            from app.main import _ensure_settings
            from app.seed import seed_if_empty

            init_db(); _ensure_settings()
            with transaction(): seed_if_empty()
            pid = create_project(ProjectIn(title='视觉工厂链路测试', source_chain=['手动创建']))['data']['id']
            generate_draft(pid, DraftGenerateIn(style_mode='structure', platform='抖音口播'))
            templates = list_templates()['data']
            created = create_cover(CoverCreateIn(
                creation_project_id=pid, content_type='carousel', ratio='1:1',
                generation_mode='local_template', quantity=3, use_person=False,
                fields={'main_topic':'AI内容闭环','title':'从选题到复盘','subtitle':'真实链路测试',
                        'category':'AI知识IP','features':['选题','创作','复盘'],
                        'keywords':['AI','内容']}))['data']
            rows = query("SELECT id,preview_path,content_type,ratio,provider,generation_mode "
                         "FROM cover_projects WHERE creation_project_id=? ORDER BY created_at", (pid,))
            sizes = []
            for row in rows:
                path = Path(row['preview_path'])
                with Image.open(path) as image:
                    sizes.append([image.width, image.height, path.exists()])
            history = list_covers()['data']
            print(json.dumps({'template_count':len(templates), 'item_count':len(created['items']),
                              'rows':rows, 'sizes':sizes,
                              'history_ids':[item['id'] for item in history]}, ensure_ascii=False))
        """)
        self.assertGreaterEqual(result["template_count"], 3)
        self.assertEqual(result["item_count"], 3)
        self.assertEqual(len(result["rows"]), 3)
        self.assertTrue(all(row["content_type"] == "carousel" for row in result["rows"]))
        self.assertTrue(all(row["provider"] == "local_compositor" for row in result["rows"]))
        self.assertTrue(all(size == [1080, 1080, True] for size in result["sizes"]))
        self.assertTrue(all(row["id"] in result["history_ids"] for row in result["rows"]))

    def test_visual_history_delete_is_scoped_and_hidden(self):
        result = run_isolated("""
            import json
            from app.api.creation import DraftGenerateIn, ProjectIn, create_project, generate_draft
            from app.api.visuals import CoverCreateIn, create_cover, delete_cover, list_covers
            from app.db import init_db, query_one, transaction
            from app.main import _ensure_settings
            from app.seed import seed_if_empty

            init_db(); _ensure_settings()
            with transaction(): seed_if_empty()
            pid = create_project(ProjectIn(title='删除链路测试', source_chain=['手动创建']))['data']['id']
            draft = generate_draft(pid, DraftGenerateIn(style_mode='structure', platform='抖音口播'))['data']
            created = create_cover(CoverCreateIn(
                creation_project_id=pid, draft_version_id=draft['id'], quantity=1,
                fields={'main_topic':'可删除历史','title':'软删除验证'}))['data']
            cover_id = created['id']
            deletion = delete_cover(cover_id)['data']
            row = query_one('SELECT deleted_at FROM cover_projects WHERE id=?', (cover_id,))
            visible_ids = [item['id'] for item in list_covers()['data']]
            print(json.dumps({'deletion':deletion, 'deleted_at':row['deleted_at'],
                              'visible_ids':visible_ids}, ensure_ascii=False))
        """)
        self.assertTrue(result["deleted_at"])
        self.assertNotIn(result["deletion"]["id"], result["visible_ids"])

    def test_question_mark_platform_is_repaired_and_new_invalid_value_is_rejected(self):
        result = run_isolated("""
            import json
            from app.api.creation import DraftGenerateIn, ProjectIn, create_project, generate_draft
            from app.db import execute, init_db, query_one, transaction
            from app.errors import AppError
            from app.main import _ensure_settings
            from app.seed import seed_if_empty
            from app.text_quality import repair_placeholder_draft_platforms

            init_db(); _ensure_settings()
            with transaction(): seed_if_empty()
            pid = create_project(ProjectIn(title='编码保护测试', source_chain=['手动创建']))['data']['id']
            valid = generate_draft(pid, DraftGenerateIn(style_mode='structure', platform='抖音口播'))['data']
            execute('UPDATE draft_versions SET platform=? WHERE id=?', ('????', valid['id']))
            repaired = repair_placeholder_draft_platforms(__import__('app.db', fromlist=['connect']).connect())
            platform = query_one('SELECT platform FROM draft_versions WHERE id=?', (valid['id'],))['platform']
            code = ''
            try:
                generate_draft(pid, DraftGenerateIn(style_mode='light', platform='????'))
            except AppError as exc:
                code = exc.code
            print(json.dumps({'platform':platform, 'repaired':repaired, 'code':code}, ensure_ascii=False))
        """)
        self.assertEqual(result["platform"], "抖音口播")
        self.assertEqual(result["repaired"]["rows"], 1)
        self.assertEqual(result["code"], "VALIDATION_FAILED")


if __name__ == "__main__":
    unittest.main()
