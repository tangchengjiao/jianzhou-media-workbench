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
    with tempfile.TemporaryDirectory(prefix="jianzhou-phase2-") as temp:
        env = os.environ.copy()
        env.update({"JIANZHOU_DATA_DIR": temp, "JIANZHOU_DEMO": "1" if demo else "0",
                    "PYTHONUTF8": "1"})
        result = subprocess.run(
            [sys.executable, "-c", textwrap.dedent(source)], cwd=ROOT, env=env,
            text=True, encoding="utf-8", capture_output=True, timeout=60,
        )
        if result.returncode != 0:
            raise AssertionError(
                f"isolated process failed\nstdout={result.stdout}\nstderr={result.stderr}"
            )
        return json.loads(result.stdout.strip())


class PhaseTwoPipelineTests(unittest.TestCase):
    def test_job_idempotency_steps_and_recovery(self):
        result = run_isolated("""
            import json
            from app.db import init_db, execute, query_one
            from app.jobs import create_job, recover_interrupted_jobs
            init_db()
            execute("INSERT INTO users VALUES(?,?,?,?)", ('u1','A','a','now'))
            first = create_job('u1','analysis',{'x':1},idempotency_key='same')
            second = create_job('u1','analysis',{'x':2},idempotency_key='same')
            execute("UPDATE jobs SET status='RUNNING',attempt_count=1 WHERE id=?", (first['id'],))
            recovered = recover_interrupted_jobs()
            row = query_one("SELECT * FROM jobs WHERE id=?", (first['id'],))
            print(json.dumps({'same':first['id']==second['id'],'status':row['status'],
                              'recovered':recovered,'schema':query_one('PRAGMA user_version')['user_version']}))
        """)
        self.assertTrue(result["same"])
        self.assertEqual(result["status"], "QUEUED")
        self.assertEqual(result["recovered"]["requeued"], 1)
        self.assertEqual(result["schema"], 6)

    def test_context_is_strictly_user_isolated_and_persisted(self):
        result = run_isolated("""
            import json
            from app.context_resolver import resolve_context
            from app.db import init_db, execute, jdumps, query_one
            init_db()
            for uid in ('u1','u2'): execute("INSERT INTO users VALUES(?,?,?,?)",(uid,uid,uid,'now'))
            execute("INSERT INTO profile_versions VALUES(?,?,?,?,?,?,?,?)",('pv1','u1','1',jdumps({'voice':'u1'}),'','','now',1))
            execute("INSERT INTO profile_versions VALUES(?,?,?,?,?,?,?,?)",('pv2','u2','1',jdumps({'voice':'secret-u2'}),'','','now',1))
            execute("INSERT INTO source_contents VALUES(?,?,?,?,?,?,?,?,?,?)",('sc1','u1',None,'Own','local','','','transcribed',jdumps({'asr':{'exists':True,'text':'own transcript'}}),'now'))
            ctx = resolve_context('u1','analysis',source_content_id='sc1')
            raw = json.dumps(ctx,ensure_ascii=False)
            print(json.dumps({'own':'own transcript' in raw,'cross':'secret-u2' in raw,
                              'stored':bool(query_one('SELECT id FROM context_runs WHERE id=?',(ctx['run_id'],))),
                              'checksum':len(ctx['checksum'])}))
        """)
        self.assertTrue(result["own"])
        self.assertFalse(result["cross"])
        self.assertTrue(result["stored"])
        self.assertEqual(result["checksum"], 64)

    @unittest.skipUnless(os.name == "nt", "DPAPI is Windows-only")
    def test_bailian_asr_openai_audio_contract(self):
        result = run_isolated("""
            import json
            from unittest.mock import patch
            from app.api.settings import SettingsIn, migrate_legacy_secrets, put_settings
            from app.db import init_db
            from app.main import _ensure_settings
            from app.providers import RealSTTProvider
            init_db(); _ensure_settings(); migrate_legacy_secrets()
            put_settings(SettingsIn(section='voice', values={
                'stt_provider':'aliyun_bailian','protocol':'openai_compatible_audio',
                'auth_mode':'bearer','api_base':'https://dashscope.aliyuncs.com/compatible-mode/v1',
                'api_key':'test-asr-secret','asr_model':'qwen3-asr-flash','tts_provider':'system',
                'interview_mode':'push','language':'zh','hot_words':'Jianzhou',
            }))
            captured = {}
            class Response:
                status_code=200; headers={'x-request-id':'req-1'}
                def json(self): return {'model':'qwen3-asr-flash','choices':[{'message':{'content':'测试转写'}}],'usage':{'total_tokens':3}}
            class Client:
                def __init__(self,**kw): pass
                def __enter__(self): return self
                def __exit__(self,*args): pass
                def post(self,url,headers,json): captured.update(url=url,headers=headers,payload=json); return Response()
            with patch('httpx.Client',Client), patch('app.providers._validate_llm_url'):
                output=RealSTTProvider().transcribe(b'RIFF-demo',mime_type='audio/wav')
            audio=captured['payload']['messages'][-1]['content'][0]['input_audio']['data']
            print(json.dumps({'text':output['text'],'model':output['model'],
                              'path':captured['url'].endswith('/chat/completions'),
                              'data_url':audio.startswith('data:audio/wav;base64,'),
                              'bearer':captured['headers']['Authorization']=='Bearer test-asr-secret'}))
        """)
        self.assertEqual(result["text"], "测试转写")
        self.assertEqual(result["model"], "qwen3-asr-flash")
        self.assertTrue(result["path"])
        self.assertTrue(result["data_url"])
        self.assertTrue(result["bearer"])

    def test_publication_snapshot_retro_rule_version_chain(self):
        result = run_isolated("""
            import json
            from app.api.planning import PublicationIn, register_publication
            from app.api.retro import SnapshotIn, approve_suggestion, capture_snapshot, generate_retro
            from app.db import init_db, execute, jdumps, query_one
            init_db()
            execute("INSERT INTO users VALUES(?,?,?,?)",('u-zhou','舟','z','now'))
            execute("INSERT INTO creation_projects VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",('cp1','u-zhou','真实链路','收藏','','[]','','[]','{}','{}','DRAFTING','now'))
            execute("INSERT INTO predictions VALUES(?,?,?,?,?,?,?,?,?,?,?)",('pred1','cp1','收藏','高于均值',0.7,'优势','风险','假设','判断',1,'now'))
            execute("INSERT INTO content_tasks VALUES(?,?,?,?,?,?,?,?,?,?)",('ct1','u-zhou','cp1','真实链路','READY_TO_PUBLISH','抖音','2026-08-05',1.0,'high',jdumps([])))
            pub=register_publication(PublicationIn(task_id='ct1',platform='抖音',url='https://example.com/work/1',final_title='真实链路'))['data']
            snap=capture_snapshot(pub['id'],SnapshotIn(tn='T+1',plays=1000,likes=50,comments=10,collects=20))['data']
            retro=generate_retro(pub['id'])['data']
            approved=approve_suggestion(retro['rule_suggestion_id'])['data']
            print(json.dumps({'schedules':len(pub['schedules']),'snapshot':bool(snap['id']),
              'engagement':retro['report']['actual']['engagement_rate'],
              'version':bool(approved['package_version_id']),
              'task':query_one("SELECT status FROM content_tasks WHERE id='ct1'")['status']}))
        """)
        self.assertEqual(result["schedules"], 3)
        self.assertTrue(result["snapshot"])
        self.assertEqual(result["engagement"], 8.0)
        self.assertTrue(result["version"])
        self.assertEqual(result["task"], "RETRO_COMPLETED")


if __name__ == "__main__":
    unittest.main()
