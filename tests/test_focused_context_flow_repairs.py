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
    with tempfile.TemporaryDirectory(prefix="jianzhou-context-flow-") as temp:
        env = os.environ.copy()
        env.update({"JIANZHOU_DATA_DIR": temp, "JIANZHOU_DEMO": "1", "PYTHONUTF8": "1"})
        result = subprocess.run(
            [sys.executable, "-c", textwrap.dedent(source)], cwd=ROOT, env=env,
            text=True, encoding="utf-8", capture_output=True, timeout=90,
        )
        if result.returncode:
            raise AssertionError(f"isolated process failed\nstdout={result.stdout}\nstderr={result.stderr}")
        return json.loads(result.stdout.strip())


class FocusedContextFlowRepairTests(unittest.TestCase):
    def test_asset_index_names_drafts_with_parent_project_context(self):
        result = run_isolated("""
            import json
            from app.api.creation import ProjectIn, create_project, generate_draft
            from app.asset_center import sync_asset_index
            from app.db import init_db, jloads, query_one, transaction
            from app.main import _ensure_settings
            from app.seed import seed_if_empty

            init_db(); _ensure_settings()
            with transaction(): seed_if_empty()
            pid = create_project(ProjectIn(title='个人知识库选题', source_chain=['测试']))['data']['id']
            generated = generate_draft(pid)['data']
            sync_asset_index()
            asset = query_one("SELECT * FROM asset_records WHERE asset_type='draft' AND entity_id=?", (generated['id'],))
            metadata = jloads(asset['metadata_json'], {})
            print(json.dumps({'title':asset['title'], 'project_id':metadata.get('project_id'),
                              'project_title':metadata.get('project_title'),
                              'version_no':metadata.get('version_no')}, ensure_ascii=False))
        """)
        self.assertIn("个人知识库选题", result["title"])
        self.assertEqual(result["project_title"], "个人知识库选题")
        self.assertTrue(result["project_id"])
        self.assertEqual(result["version_no"], 1)

    def test_frontend_preserves_asset_context_and_limits_graphic_history_to_three_columns(self):
        system_js = (ROOT / "frontend/assets/js/pages/system.js").read_text(encoding="utf-8")
        creation_js = (ROOT / "frontend/assets/js/pages/create.js").read_text(encoding="utf-8")
        css = (ROOT / "frontend/assets/css/app.css").read_text(encoding="utf-8")
        self.assertIn("params.set('draft', asset.entity_id)", system_js)
        self.assertIn("params.get('draft')", creation_js)
        self.assertIn("requestedDraft || this.p.drafts", creation_js)
        self.assertIn("repeat(3,minmax(0,1fr));align-items:start;column-gap:12px", css)
        self.assertNotIn(".graphic-document-pages{grid-template-columns:repeat(auto-fit,minmax(220px,1fr))", css)


if __name__ == "__main__":
    unittest.main()
