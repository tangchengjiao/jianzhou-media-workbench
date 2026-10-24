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
    with tempfile.TemporaryDirectory(prefix="jianzhou-connectors-") as temp:
        env = os.environ.copy()
        env.update({"JIANZHOU_DATA_DIR": temp,
                    "JIANZHOU_DEMO": "1" if demo else "0", "PYTHONUTF8": "1"})
        result = subprocess.run(
            [sys.executable, "-c", textwrap.dedent(source)], cwd=ROOT, env=env,
            text=True, encoding="utf-8", capture_output=True, timeout=90,
        )
        if result.returncode:
            raise AssertionError(
                f"isolated process failed\nstdout={result.stdout}\nstderr={result.stderr}"
            )
        return json.loads(result.stdout.strip())


class ConnectorJobAndChannelsTests(unittest.TestCase):
    def test_douyin_latest_sync_uses_app_v3_with_explicit_latest_sort_and_fallback(self):
        from app.config import DEFAULT_SETTINGS

        endpoint = DEFAULT_SETTINGS["datasource"]["providers"]["tikhub"]["endpoints"][
            "accountVideos"]["douyin"]
        self.assertEqual(endpoint["path"], "/api/v1/douyin/app/v3/fetch_user_post_videos")
        self.assertEqual(endpoint["params"]["sort_type"], "0")
        self.assertEqual(endpoint["fallback"]["path"],
                         "/api/v1/douyin/web/fetch_user_post_videos")

    def test_douyin_media_uses_high_quality_cn_endpoint_with_web_fallback(self):
        from app.config import DEFAULT_SETTINGS

        endpoint = DEFAULT_SETTINGS["datasource"]["providers"]["tikhub"]["endpoints"][
            "videoMedia"]["douyin"]
        self.assertEqual(
            endpoint["path"],
            "/api/v1/douyin/app/v3/fetch_video_high_quality_play_url",
        )
        self.assertEqual(endpoint["params"]["region"], "CN")
        self.assertEqual(endpoint["map"]["url"], "$.data.original_video_url")
        self.assertEqual(endpoint["fallback"]["path"],
                         "/api/v1/douyin/web/fetch_one_video")

    def test_asr_authorization_resumes_same_job_and_preserves_media_identity(self):
        result = run_isolated("""
            import json
            from app.api.extraction import authorize_asr
            from app.db import execute, init_db, jdumps, query_one
            from app.jobs import create_job
            init_db()
            execute("INSERT INTO users(id,name,avatar,created_at) VALUES('u-zhou','user','','now')")
            job = create_job('u-zhou','extraction',{
                'title':'真实作品','signal_id':'sg-1','platform':'douyin',
                'video_id':'778899','url':'https://www.douyin.com/video/778899',
                'caption_available':False,'authorizations':{'asr_when_no_caption':False}})
            execute("UPDATE jobs SET status='WAITING_CONFIRMATION',attempt_count=1 WHERE id=?",(job['id'],))
            out = authorize_asr(job['id'])['data']
            row = query_one("SELECT status,input_json FROM jobs WHERE id=?",(job['id'],))
            payload = json.loads(row['input_json'])
            print(json.dumps({'same':out['job_id']==job['id'],'status':row['status'],
                              'platform':payload['platform'],'video_id':payload['video_id'],
                              'authorized':payload['authorizations']['asr_when_no_caption']}))
        """)
        self.assertEqual(result, {"same": True, "status": "QUEUED", "platform": "douyin",
                                  "video_id": "778899", "authorized": True})

    def test_aihot_payload_uses_nested_links_and_camel_case_date(self):
        from app.radar_sources import _aihot_items

        items = _aihot_items({"items": [{
            "id": "ai-1", "title": "AI 行业新闻", "score": 88,
            "publishedAt": "2026-08-05T10:00:00Z",
            "links": {"original": "https://source.example/item",
                      "aihot": "https://aihot.virxact.com/items/ai-1"},
        }]})
        self.assertEqual(items[0]["url"], "https://source.example/item")
        self.assertEqual(items[0]["published_at"], "2026-08-05T10:00:00Z")
        self.assertEqual(items[0]["heat"], 88)

    def test_channels_mapping_reads_real_v2_shape(self):
        from app.config import DEFAULT_SETTINGS
        from app.datasource import map_obj

        mapping = DEFAULT_SETTINGS["datasource"]["providers"]["tikhub"]["endpoints"][
            "accountVideos"]["channels"]["map"]
        mapped = map_obj({
            "id": "14941130915890399732",
            "title": [{"shortTitle": "真实视频号标题"}],
            "media": {"cover_url": "https://img.example/cover.jpg", "cover_url_token": "?token=abc", "duration": 66},
            "read_count": 1200, "like_count": 88,
        }, mapping)
        self.assertEqual(mapped["video_id"], "14941130915890399732")
        self.assertEqual(mapped["title"], "真实视频号标题")
        self.assertEqual(mapped["cover"], "https://img.example/cover.jpg")
        self.assertEqual(mapped["cover_token"], "?token=abc")
        self.assertEqual(mapped["duration"], 66)

    def test_active_operations_are_deduplicated_but_completed_sync_can_run_again(self):
        result = run_isolated("""
            import json
            from app.db import execute, init_db
            from app.jobs import create_job
            init_db()
            execute("INSERT INTO users(id,name,avatar,created_at) VALUES('u','user','','now')")
            first = create_job("u", "account_sync", {"account_id":"a"})
            second = create_job("u", "account_sync", {"account_id":"a"})
            execute("UPDATE jobs SET status='SUCCEEDED' WHERE id=?", (first["id"],))
            third = create_job("u", "account_sync", {"account_id":"a"})
            print(json.dumps({"first":first["id"],"second":second["id"],
                              "second_reused":second.get("reused",False),"third":third["id"]}))
        """)
        self.assertEqual(result["first"], result["second"])
        self.assertTrue(result["second_reused"])
        self.assertNotEqual(result["first"], result["third"])

    def test_extraction_can_reuse_completed_business_result(self):
        result = run_isolated("""
            import json
            from app.db import execute, init_db
            from app.jobs import create_job
            init_db()
            execute("INSERT INTO users(id,name,avatar,created_at) VALUES('u','user','','now')")
            data = {"account_work_id":"work-1","platform":"channels","video_id":"100"}
            first = create_job("u", "extraction", data, reuse_succeeded=True)
            execute("UPDATE jobs SET status='SUCCEEDED' WHERE id=?", (first["id"],))
            second = create_job("u", "extraction", data, reuse_succeeded=True)
            print(json.dumps({"same":first["id"]==second["id"],
                              "reused":second.get("reused",False)}))
        """)
        self.assertEqual(result, {"same": True, "reused": True})

    def test_failed_radar_source_gets_persistent_backoff(self):
        result = run_isolated("""
            import json
            from unittest.mock import patch
            from app.db import execute, init_db, query_one
            from app.radar_sources import create_source, sync_source
            init_db()
            execute("INSERT INTO users(id,name,avatar,created_at) VALUES('u','user','','now')")
            source = create_source("u", "external_feed", "bad", None,
                                   {"url":"https://example.com/feed.xml"}, 24)
            try:
                with patch("app.radar_sources._fetch_external_feed", side_effect=RuntimeError("boom")):
                    sync_source(source["id"], "u")
            except RuntimeError:
                pass
            row = query_one("SELECT failure_count,next_run_at,last_error FROM radar_sources WHERE id=?",
                            (source["id"],))
            print(json.dumps(row))
        """)
        self.assertEqual(result["failure_count"], 1)
        self.assertTrue(result["next_run_at"])
        self.assertEqual(result["last_error"], "boom")


if __name__ == "__main__":
    unittest.main()
