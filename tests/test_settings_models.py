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
    with tempfile.TemporaryDirectory(prefix="jianzhou-settings-") as temp:
        env = os.environ.copy()
        env.update({"JIANZHOU_DATA_DIR": temp, "JIANZHOU_DEMO": "1", "PYTHONUTF8": "1"})
        result = subprocess.run(
            [sys.executable, "-c", textwrap.dedent(source)], cwd=ROOT, env=env,
            text=True, encoding="utf-8", capture_output=True, timeout=50,
        )
        if result.returncode != 0:
            raise AssertionError(f"isolated process failed\nstdout={result.stdout}\nstderr={result.stderr}")
        return json.loads(result.stdout.strip())


class ModelSettingsTests(unittest.TestCase):
    def test_proxy_fake_ip_is_only_allowed_for_known_public_model_hosts(self):
        result = run_isolated("""
            import json, socket
            from unittest.mock import patch
            from app.errors import AppError
            from app.providers import _validate_llm_url

            fake_answer = [(socket.AF_INET, socket.SOCK_STREAM, 6, '',
                            ('198.18.0.14', 443))]
            with patch('app.providers.socket.getaddrinfo', return_value=fake_answer):
                _validate_llm_url('https://api.deepseek.com', resolve_dns=True)
                blocked = None
                try:
                    _validate_llm_url('https://models.example.com', resolve_dns=True)
                except AppError as exc:
                    blocked = exc.code
            print(json.dumps({'official_passed': True, 'custom_blocked': blocked}))
        """)
        self.assertEqual(result, {
            "official_passed": True,
            "custom_blocked": "LLM_URL_BLOCKED",
        })

    def test_custom_model_names_and_runtime_mode_reach_real_provider(self):
        result = run_isolated("""
            import json
            from app import config
            from app.api.settings import SettingsIn, get_settings, put_settings
            from app.db import init_db
            from app.main import _apply_execution_mode, _ensure_settings
            from app.providers import RealLLMProvider, get_llm_provider
            init_db(); _ensure_settings(); _apply_execution_mode()
            put_settings(SettingsIn(section='llm', values={
                'protocol': 'openai_compatible', 'auth_mode': 'none',
                'allow_private_network': False, 'api_base': 'https://models.example.com',
                'fast_model': 'fast-custom', 'analysis_model': 'analysis-custom',
                'writing_model': 'writer-custom', 'structured_model': 'json-custom',
                'embedding_model': 'embed-reserved',
            }))
            put_settings(SettingsIn(section='execution', values={'mode': 'real'}))
            settings = get_settings()['data']
            provider = get_llm_provider()
            print(json.dumps({
                'demo_mode': config.DEMO_MODE,
                'provider_real': isinstance(provider, RealLLMProvider),
                'api_base': settings['llm']['api_base'],
                'models': [settings['llm'][k] for k in (
                    'fast_model','analysis_model','writing_model','structured_model')],
            }))
        """)
        self.assertFalse(result["demo_mode"])
        self.assertTrue(result["provider_real"])
        self.assertEqual(result["api_base"], "https://models.example.com/v1")
        self.assertEqual(result["models"], [
            "fast-custom", "analysis-custom", "writer-custom", "json-custom",
        ])

    def test_local_openai_compatible_models_and_chat_contract(self):
        result = run_isolated("""
            import json, threading
            from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
            from app.api.settings import SettingsIn, put_settings
            from app.db import init_db
            from app.main import _ensure_settings
            from app.providers import RealLLMProvider

            requests = []
            class Handler(BaseHTTPRequestHandler):
                def log_message(self, *_): pass
                def do_GET(self):
                    requests.append({'method':'GET','path':self.path})
                    if self.path != '/v1/models':
                        self.send_error(404); return
                    body = json.dumps({'object':'list','data':[{'id':'local-fast'},{'id':'local-writer'}]}).encode()
                    self.send_response(200); self.send_header('Content-Type','application/json')
                    self.send_header('Content-Length',str(len(body))); self.end_headers(); self.wfile.write(body)
                def do_POST(self):
                    length = int(self.headers.get('Content-Length','0'))
                    payload = json.loads(self.rfile.read(length) or b'{}')
                    requests.append({'method':'POST','path':self.path,'payload':payload,
                                     'authorization':self.headers.get('Authorization')})
                    if self.path != '/v1/chat/completions':
                        self.send_error(404); return
                    body = json.dumps({'choices':[{'message':{'content':'OK'}}], 'model':payload.get('model')}).encode()
                    self.send_response(200); self.send_header('Content-Type','application/json')
                    self.send_header('Content-Length',str(len(body))); self.end_headers(); self.wfile.write(body)

            server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
            threading.Thread(target=server.serve_forever, daemon=True).start()
            init_db(); _ensure_settings()
            put_settings(SettingsIn(section='llm', values={
                'protocol':'openai_compatible','auth_mode':'none','allow_private_network':True,
                'api_base':f'http://127.0.0.1:{server.server_port}/v1',
                'fast_model':'local-fast','analysis_model':'local-fast',
                'writing_model':'local-writer','structured_model':'local-fast',
                'embedding_model':'unused',
            }))
            provider = RealLLMProvider()
            models = provider.list_models()
            check = provider.check_connection('local-fast')
            server.shutdown()
            print(json.dumps({'models':models,'reachable':check['reachable'],'model':check['model'],
                              'requests':requests}))
        """)
        self.assertEqual(result["models"], ["local-fast", "local-writer"])
        self.assertTrue(result["reachable"])
        self.assertEqual(result["model"], "local-fast")
        self.assertEqual([item["path"] for item in result["requests"]], [
            "/v1/models", "/v1/chat/completions",
        ])
        chat = result["requests"][1]
        self.assertEqual(chat["payload"]["model"], "local-fast")
        self.assertEqual(chat["payload"]["messages"][1]["content"], "Reply with exactly: OK")
        self.assertIsNone(chat["authorization"])

    def test_structured_analysis_requests_json_object_mode(self):
        result = run_isolated("""
            import json, threading
            from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
            from app.api.settings import SettingsIn, put_settings
            from app.db import init_db
            from app.main import _ensure_settings
            from app.providers import RealLLMProvider

            received = {}
            class Handler(BaseHTTPRequestHandler):
                def log_message(self, *_): pass
                def do_POST(self):
                    length = int(self.headers.get('Content-Length','0'))
                    payload = json.loads(self.rfile.read(length) or b'{}')
                    received.update(payload)
                    content = json.dumps({
                        'topic':'真实主题','target_audience':[],'user_problem':'',
                        'hook':{},'structure':[],'key_turns':[],'evidence':[],
                        'emotional_mechanisms':[],'golden_quotes':[],'cta':'',
                        'comment_triggers':[],'reusable':[],'non_reusable':[]},
                        ensure_ascii=False)
                    body = json.dumps({'choices':[{'message':{'content':content}}],
                                       'model':payload.get('model')}, ensure_ascii=False).encode()
                    self.send_response(200); self.send_header('Content-Type','application/json')
                    self.send_header('Content-Length',str(len(body))); self.end_headers(); self.wfile.write(body)

            server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
            threading.Thread(target=server.serve_forever, daemon=True).start()
            init_db(); _ensure_settings()
            put_settings(SettingsIn(section='llm', values={
                'protocol':'openai_compatible','auth_mode':'none','allow_private_network':True,
                'api_base':f'http://127.0.0.1:{server.server_port}/v1',
                'analysis_model':'analysis-json'}))
            output = RealLLMProvider().analyze('标题', '文案', {})
            server.shutdown()
            print(json.dumps({'format':received.get('response_format'),
                              'topic':output['topic']}))
        """)
        self.assertEqual(result["format"], {"type": "json_object"})
        self.assertEqual(result["topic"], "真实主题")

    def test_openai_compatible_image_provider_contract_and_no_fallback(self):
        result = run_isolated("""
            import base64, io, json, threading
            from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
            from PIL import Image
            from app.api.settings import SettingsIn, put_settings
            from app.db import init_db
            from app.errors import AppError
            from app.image_provider import RealImageProvider
            from app.main import _ensure_settings

            output = io.BytesIO()
            Image.new('RGB', (32, 32), '#123456').save(output, 'PNG')
            png = base64.b64encode(output.getvalue()).decode()
            received = []
            fail = [False]
            class Handler(BaseHTTPRequestHandler):
                def log_message(self, *_): pass
                def do_POST(self):
                    length = int(self.headers.get('Content-Length','0'))
                    received.append(json.loads(self.rfile.read(length) or b'{}'))
                    if fail[0]:
                        body = json.dumps({'error':{'message':'provider failed'}}).encode()
                        self.send_response(503); self.send_header('Content-Type','application/json')
                        self.send_header('Content-Length',str(len(body))); self.end_headers(); self.wfile.write(body)
                        return
                    body = json.dumps({'model':'image2-test','data':[{'b64_json':png}]}).encode()
                    self.send_response(200); self.send_header('Content-Type','application/json')
                    self.send_header('Content-Length',str(len(body))); self.end_headers(); self.wfile.write(body)

            server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
            threading.Thread(target=server.serve_forever, daemon=True).start()
            init_db(); _ensure_settings()
            put_settings(SettingsIn(section='image', values={
                'protocol':'openai_compatible_images','auth_mode':'none',
                'allow_private_network':True,
                'api_base':f'http://127.0.0.1:{server.server_port}/v1',
                'model':'image2-test','generation_path':'images/generations',
                'default_mode':'ai_background','default_ratio':'3:4',
                'default_variants':2,'quality':'high'}))
            generated = RealImageProvider().generate('background only, no text', '3:4')
            fail[0] = True
            unavailable = None
            try:
                RealImageProvider().generate('must fail', '1:1')
            except AppError as exc:
                unavailable = exc.code
            server.shutdown()
            print(json.dumps({'model':generated['model'],'bytes':len(generated['bytes']),
                              'payload':received,'unavailable':unavailable}))
        """)
        self.assertEqual(result["model"], "image2-test")
        self.assertGreater(result["bytes"], 50)
        self.assertEqual(result["payload"][0]["model"], "image2-test")
        self.assertEqual(result["payload"][0]["size"], "1024x1536")
        self.assertEqual(result["payload"][0]["response_format"], "b64_json")
        self.assertEqual(result["unavailable"], "IMAGE_PROVIDER_ERROR")


if __name__ == "__main__":
    unittest.main()
