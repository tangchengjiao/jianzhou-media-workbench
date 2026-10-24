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
    with tempfile.TemporaryDirectory(prefix="jianzhou-text-quality-") as temp:
        env = os.environ.copy()
        env.update({"JIANZHOU_DATA_DIR": temp, "JIANZHOU_DEMO": "1" if demo else "0", "PYTHONUTF8": "1"})
        result = subprocess.run(
            [sys.executable, "-c", textwrap.dedent(source)], cwd=ROOT, env=env,
            text=True, encoding="utf-8", capture_output=True, timeout=90,
        )
        if result.returncode:
            raise AssertionError(
                f"isolated process failed\nstdout={result.stdout}\nstderr={result.stderr}"
            )
        return json.loads(result.stdout.strip())


class TextQualityAndOwnWorksTests(unittest.TestCase):
    def test_content_library_and_analysis_detail_keep_full_original_text(self):
        result = run_isolated("""
            import json
            from app.api.analysis import analysis_detail, list_analysis
            from app.api.extraction import assets, list_assets_endpoint
            from app.db import execute, init_db, jdumps
            init_db()
            execute("INSERT INTO users(id,name,avatar,created_at) VALUES(?,?,?,?)",
                    ("u-zhou","见舟","","2026-08-05T00:00:00"))
            original = "第一段原始文案。\\n第二段仍然必须能被回看。"
            layers = {"metadata":{"exists":True},
                      "asr":{"exists":True,"immutable":True,"text":original},
                      "media":{"exists":True}}
            execute("INSERT INTO source_contents VALUES(?,?,?,?,?,?,?,?,?,?)",
                    ("SC-history","u-zhou",None,"历史来源","douyin","作者",
                     "https://www.douyin.com/video/123","transcribed",jdumps(layers),
                     "2026-08-05T00:00:00"))
            execute("INSERT INTO analyses VALUES(?,?,?,?,?,?,?,?)",
                    ("A-history","u-zhou","SC-history","done",
                     jdumps({"topic":"历史拆解"}),"test-model",0,
                     "2026-08-05T00:01:00"))
            library = list_assets_endpoint()["data"]
            detail = assets("SC-history")["data"]
            analysis = analysis_detail("A-history")["data"]
            listed = list_analysis()["data"]["analyses"][0]
            print(json.dumps({"library":library,"detail":detail["transcript"],
                              "linked":detail["analyses"],"source":analysis["source"],
                              "listed":listed}, ensure_ascii=False))
        """)
        self.assertEqual(result["library"][0]["text_chars"], 21)
        self.assertEqual(result["library"][0]["analysis_count"], 1)
        self.assertEqual(result["detail"]["text"], "第一段原始文案。\n第二段仍然必须能被回看。")
        self.assertEqual(result["linked"][0]["id"], "A-history")
        self.assertEqual(result["source"]["text"], result["detail"]["text"])
        self.assertEqual(result["listed"]["original_text_kind"], "原始 ASR 转写")

    def test_long_audio_is_split_below_provider_limit_and_reassembled_in_order(self):
        result = run_isolated("""
            import json, pathlib, shutil, subprocess, tempfile
            from unittest.mock import patch
            from app.jobs import _transcribe_segmented_audio
            ffmpeg = shutil.which("ffmpeg")
            if not ffmpeg:
                print(json.dumps({"skipped":True})); raise SystemExit
            class FakeSTT:
                name = "fake-stt"
                def __init__(self): self.calls = 0
                def transcribe(self, payload, context="", mime_type=""):
                    self.calls += 1
                    return {"text":f"第{self.calls}段", "model":"fake", "cost":0.1,
                            "provider_request_id":f"r{self.calls}"}
            with tempfile.TemporaryDirectory() as temp:
                source = pathlib.Path(temp) / "long.mp3"
                made = subprocess.run([ffmpeg,"-hide_banner","-loglevel","error","-y",
                    "-f","lavfi","-i","anullsrc=r=16000:cl=mono","-t","490",
                    "-b:a","16k",str(source)], capture_output=True)
                if made.returncode: raise RuntimeError(made.stderr.decode(errors="ignore"))
                stt = FakeSTT()
                with patch("app.jobs._set"):
                    output = _transcribe_segmented_audio("J-test",source,stt,"长音频",pathlib.Path(temp))
                print(json.dumps({"output":output,"calls":stt.calls}))
        """)
        if result.get("skipped"):
            self.skipTest("FFmpeg unavailable")
        self.assertEqual(result["calls"], 3)
        self.assertEqual(result["output"]["segment_count"], 3)
        self.assertEqual(result["output"]["text"], "第1段\n\n第2段\n\n第3段")
        self.assertAlmostEqual(result["output"]["cost"], 0.3)

    def test_own_work_performance_sort_metadata_and_manual_review_persist(self):
        result = run_isolated("""
            import json
            from app.account_works import persist_account_works
            from app.api.accounts import AccountWorkReviewIn, account_detail, review_account_work
            from app.db import execute, init_db, jdumps
            init_db()
            execute("INSERT INTO users(id,name,avatar,created_at) VALUES('u-zhou','见舟','','now')")
            execute("INSERT INTO accounts VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                    ('acc','u-zhou','douyin','@a','own','ok','每天',None,1,'demo','',jdumps({'uid':'u'})))
            persist_account_works('acc',[
                {'video_id':'new','title':'新作品','create_time':200,'likes':100,'comments':20,'collects':30,'shares':10},
                {'video_id':'old','title':'旧作品','create_time':100,'likes':5,'comments':0,'collects':0,'shares':0}], 'now')
            detail=account_detail('acc')['data']
            target=detail['works'][0]
            review_account_work('acc',target['id'],AccountWorkReviewIn(performance_label='good',review_note='继续复用'))
            refreshed=account_detail('acc')['data']['works']
            print(json.dumps({'order':[w['video_id'] for w in refreshed],
                              'bands':[w['performance_band'] for w in refreshed],
                              'label':refreshed[0]['performance_label'],
                              'note':refreshed[0]['review_note']}))
        """)
        self.assertEqual(result["order"], ["new", "old"])
        self.assertEqual(result["bands"], ["high", "low"])
        self.assertEqual(result["label"], "good")
        self.assertEqual(result["note"], "继续复用")

    def test_reversible_utf8_mojibake_is_repaired_without_touching_chinese(self):
        from app.text_quality import repair_mojibake, repair_text_tree

        original = "第7集｜本地知识库有三个最大的好处"
        broken = original.encode("utf-8").decode("latin-1")
        self.assertEqual(repair_mojibake(broken), original)
        self.assertEqual(repair_mojibake(original), original)
        self.assertEqual(
            repair_text_tree({"title": broken, "nested": [original]}),
            {"title": original, "nested": [original]},
        )

    def test_one_time_database_repair_updates_job_source_and_analysis_json(self):
        result = run_isolated("""
            import json
            from app.db import execute, init_db, jdumps, query_one
            from app.text_quality import repair_legacy_database_text, repair_mislabeled_description_assets
            init_db()
            execute("INSERT INTO users(id,name,avatar,created_at) VALUES(?,?,?,?)", ("u-zhou","见舟","","2026-08-05T00:00:00"))
            original = "第7集｜本地知识库有三个最大的好处"
            broken = original.encode("utf-8").decode("latin-1")
            execute("DELETE FROM data_repairs WHERE name='utf8_latin1_mojibake_v1'")
            execute("DELETE FROM data_repairs WHERE name='description_mislabeled_as_caption_v1'")
            execute("INSERT INTO source_contents(id,user_id,signal_id,platform,title,author,url,assets_json,created_at) VALUES(?,?,?,?,?,?,?,?,?)",
                    ("SC-test","u-zhou",None,"douyin",broken,"作者","https://example.test",jdumps({"caption":{"exists":True,"text":broken,"trace":"real_tikhub 字幕接口"},"asr":{"exists":False}}),"2026-08-05T00:00:00"))
            execute("INSERT INTO jobs(id,user_id,task_type,status,current_step,progress,input_json,plan_json,output_json,error_json,trace_id,cost_est,cost_actual,created_at,started_at,finished_at,attempt_count,max_attempts,idempotency_key,lease_until,heartbeat_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    ("J-test","u-zhou","extraction","SUCCEEDED","done",100,jdumps({"title":broken}),jdumps({"source":{"title":broken}}),jdumps({}),None,"trace-test",0,0,"2026-08-05T00:00:00",None,None,1,3,None,None,None))
            stats = repair_legacy_database_text(__import__('app.db',fromlist=['connect']).connect())
            description_stats = repair_mislabeled_description_assets(__import__('app.db',fromlist=['connect']).connect())
            source = query_one("SELECT title,status,assets_json FROM source_contents WHERE id='SC-test'")
            job = query_one("SELECT input_json,plan_json FROM jobs WHERE id='J-test'")
            print(json.dumps({"stats":stats,"description_stats":description_stats,"source":source,"job":job}, ensure_ascii=False))
        """)
        self.assertGreaterEqual(result["stats"]["rows"], 2)
        self.assertEqual(result["source"]["title"], "第7集｜本地知识库有三个最大的好处")
        self.assertEqual(result["source"]["status"], "metadata_only")
        self.assertIn("第7集", result["source"]["assets_json"])
        self.assertEqual(result["description_stats"]["sources"], 1)
        self.assertIn("第7集", result["job"]["input_json"])
        self.assertIn("第7集", result["job"]["plan_json"])

    def test_own_account_works_persist_and_open_extraction_plan(self):
        result = run_isolated("""
            import json
            from app.account_works import persist_account_works
            from app.api.extraction import PlanIn, create_plan
            from app.db import execute, init_db, jdumps, query_one
            init_db()
            execute("INSERT INTO users(id,name,avatar,created_at) VALUES(?,?,?,?)", ("u-zhou","见舟","","2026-08-05T00:00:00"))
            execute("INSERT INTO accounts VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                    ("acc-own","u-zhou","douyin","@见舟AI","own","ok","每 6 小时",None,1,"demo","",jdumps({"uid":"uid-own","name":"见舟AI"})))
            stats1 = persist_account_works("acc-own", [{"video_id":"v-own-1","title":"自己的真实作品","cover":"https://example.test/cover.jpg","url":"https://example.test/work","create_time":1700000000,"likes":42}], "2026-08-05T00:00:00")
            stats2 = persist_account_works("acc-own", [{"video_id":"v-own-1","title":"自己的真实作品（更新）","url":"https://example.test/work","create_time":1700000000,"likes":50}], "2026-08-05T01:00:00")
            work = query_one("SELECT * FROM account_works WHERE account_id='acc-own'")
            plan = create_plan(PlanIn(account_work_id=work["id"]))["data"]
            print(json.dumps({"first":stats1,"second":stats2,"work":work,"source":plan["source"]}, ensure_ascii=False))
        """)
        self.assertEqual(result["first"], {"created": 1, "updated": 0})
        self.assertEqual(result["second"], {"created": 0, "updated": 1})
        self.assertEqual(result["work"]["likes"], 50)
        self.assertEqual(result["source"]["account_work_id"], result["work"]["id"])
        self.assertEqual(result["source"]["video_id"], "v-own-1")
        self.assertEqual(result["source"]["title"], "自己的真实作品（更新）")
        self.assertEqual(result["source"]["url"], "https://example.test/work")

    def test_real_plan_never_treats_platform_description_as_caption(self):
        result = run_isolated("""
            import json
            from app.account_works import persist_account_works
            from app.api.extraction import PlanIn, create_plan
            from app.db import execute, init_db, jdumps, query_one
            from app.main import _ensure_settings
            init_db(); _ensure_settings()
            execute("INSERT INTO users(id,name,avatar,created_at) VALUES(?,?,?,?)", ("u-local","本地用户","","2026-08-05T00:00:00"))
            execute("INSERT INTO accounts VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                    ("acc-real","u-local","douyin","@真实账号","own","ok","每 6 小时",None,1,"real_tikhub","",jdumps({"uid":"uid-real","name":"真实账号"})))
            persist_account_works("acc-real", [{"video_id":"v-real-1","title":"作品发布描述","create_time":1700000000}], "2026-08-05T00:00:00")
            work = query_one("SELECT id FROM account_works WHERE account_id='acc-real'")
            plan = create_plan(PlanIn(account_work_id=work["id"]))["data"]
            print(json.dumps({"caption":plan["caption_available"],"steps":plan["steps"],"cost":plan["cost"]}, ensure_ascii=False))
        """, demo=False)
        self.assertFalse(result["caption"])
        self.assertTrue(any("不冒充逐字字幕" in step for step in result["steps"]))
        self.assertIn("公开媒体", result["cost"]["数据源"])

    def test_heic_account_cover_uses_owned_local_proxy(self):
        result = run_isolated("""
            import json
            from app.account_works import persist_account_works
            from app.api.accounts import account_detail
            from app.db import execute, init_db, jdumps
            init_db()
            execute("INSERT INTO users(id,name,avatar,created_at) VALUES(?,?,?,?)",
                    ("u-zhou","见舟","","2026-08-05T00:00:00"))
            execute("INSERT INTO accounts VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                    ("acc-own","u-zhou","douyin","@见舟AI","own","ok","每天",None,1,
                     "real_tikhub","",jdumps({"uid":"uid-own","name":"见舟AI"})))
            persist_account_works("acc-own", [{"video_id":"heic-1","title":"真实作品",
                "cover":"https://p11-sign.douyinpic.com/demo.heic?x-signature=test",
                "create_time":1700000000}], "2026-08-05T00:00:00")
            work = account_detail("acc-own")["data"]["works"][0]
            print(json.dumps({"raw":work["cover"], "display":work["cover_display_url"]}))
        """)
        self.assertTrue(result["raw"].endswith("x-signature=test"))
        self.assertEqual(
            result["display"],
            "/api/accounts/acc-own/works/OW-f30e7585ee5f22c5aea9317a/cover",
        )


if __name__ == "__main__":
    unittest.main()
