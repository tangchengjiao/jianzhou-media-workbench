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
    with tempfile.TemporaryDirectory(prefix="jianzhou-test-") as temp:
        env = os.environ.copy()
        env.update({
            "JIANZHOU_DATA_DIR": temp,
            "JIANZHOU_DEMO": "1" if demo else "0",
            "PYTHONUTF8": "1",
        })
        result = subprocess.run(
            [sys.executable, "-c", textwrap.dedent(source)],
            cwd=ROOT,
            env=env,
            text=True,
            encoding="utf-8",
            capture_output=True,
            timeout=45,
        )
        if result.returncode != 0:
            raise AssertionError(f"isolated process failed\nstdout={result.stdout}\nstderr={result.stderr}")
        return json.loads(result.stdout.strip())


class FoundationTests(unittest.TestCase):
    def test_clean_demo_database_is_consistent(self):
        result = run_isolated("""
            import json
            from app.api.settings import migrate_legacy_secrets
            from app.db import init_db, query, query_one, transaction
            from app.main import _ensure_settings
            from app.seed import seed_if_empty
            init_db(); _ensure_settings(); migrate_legacy_secrets()
            with transaction():
                seed_if_empty()
            print(json.dumps({
                "users": query_one("SELECT COUNT(*) c FROM users")["c"],
                "benchmarks": query_one("SELECT COUNT(*) c FROM benchmark_accounts")["c"],
                "foreign_keys": query_one("PRAGMA foreign_keys")["foreign_keys"],
                "foreign_key_errors": len(query("PRAGMA foreign_key_check")),
                "integrity": query_one("PRAGMA integrity_check")["integrity_check"],
                "schema_version": query_one("PRAGMA user_version")["user_version"],
            }))
        """)
        self.assertEqual(result["users"], 2)
        self.assertEqual(result["benchmarks"], 2)
        self.assertEqual(result["foreign_keys"], 1)
        self.assertEqual(result["foreign_key_errors"], 0)
        self.assertEqual(result["integrity"], "ok")
        self.assertGreaterEqual(result["schema_version"], 1)

    def test_real_mode_has_minimal_user_and_typed_missing_config(self):
        result = run_isolated("""
            import json
            from app.api.settings import migrate_legacy_secrets
            from app.db import init_db, query_one, transaction
            from app.errors import AppError
            from app.main import _ensure_settings
            from app.providers import get_llm_provider
            from app.seed import seed_if_empty
            init_db(); _ensure_settings(); migrate_legacy_secrets()
            with transaction():
                seed_if_empty()
            error = None
            try:
                get_llm_provider().parse_command("今天做什么")
            except AppError as exc:
                error = exc.code
            print(json.dumps({
                "users": query_one("SELECT COUNT(*) c FROM users")["c"],
                "benchmarks": query_one("SELECT COUNT(*) c FROM benchmark_accounts")["c"],
                "error": error,
            }))
        """, demo=False)
        self.assertEqual(result, {
            "users": 1,
            "benchmarks": 0,
            "error": "CONFIG_MISSING_API_KEY",
        })

    @unittest.skipUnless(os.name == "nt", "DPAPI is Windows-only")
    def test_secret_is_encrypted_and_never_returned(self):
        result = run_isolated("""
            import json
            from app.api.settings import SettingsIn, get_settings, migrate_legacy_secrets, put_settings
            from app.db import init_db
            from app.main import _ensure_settings
            from app.secret_store import SECRET_FILE, get_secret
            init_db(); _ensure_settings(); migrate_legacy_secrets()
            put_settings(SettingsIn(section="llm", values={"api_key": "test-secret-value"}))
            response = get_settings()
            raw_response = json.dumps(response, ensure_ascii=False)
            raw_file = SECRET_FILE.read_text(encoding="utf-8")
            print(json.dumps({
                "round_trip": get_secret("llm_api_key") == "test-secret-value",
                "plaintext_in_file": "test-secret-value" in raw_file,
                "plaintext_in_response": "test-secret-value" in raw_response,
                "raw_key_in_response": "\\\"api_key\\\"" in raw_response,
                "configured": response["data"]["llm"]["api_key_configured"],
            }))
        """)
        self.assertTrue(result["round_trip"])
        self.assertFalse(result["plaintext_in_file"])
        self.assertFalse(result["plaintext_in_response"])
        self.assertFalse(result["raw_key_in_response"])
        self.assertTrue(result["configured"])

    def test_transaction_rolls_back(self):
        result = run_isolated("""
            import json
            from app.db import execute, init_db, query_one, transaction
            init_db()
            try:
                with transaction():
                    execute("INSERT INTO users VALUES(?,?,?,?)", ("rollback-user", "x", "x", "now"))
                    raise RuntimeError("stop")
            except RuntimeError:
                pass
            print(json.dumps({"count": query_one(
                "SELECT COUNT(*) c FROM users WHERE id='rollback-user'"
            )["c"]}))
        """)
        self.assertEqual(result["count"], 0)

    def test_ids_are_collision_resistant(self):
        from app.ids import new_id
        values = {new_id("test") for _ in range(5000)}
        self.assertEqual(len(values), 5000)
        self.assertTrue(all(value.startswith("test-") for value in values))

    def test_pyinstaller_spec_resolves_project_root_defensively(self):
        spec = (ROOT / "packaging" / "pyinstaller.spec").read_text(encoding="utf-8")
        self.assertIn('ROOT / "app.py"', spec)
        self.assertIn('ROOT / "frontend"', spec)
        self.assertIn("无法解析项目根目录", spec)


if __name__ == "__main__":
    unittest.main()
