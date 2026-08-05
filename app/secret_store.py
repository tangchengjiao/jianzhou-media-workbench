"""Windows DPAPI-backed local secret storage.

Secrets are encrypted for the current Windows user and kept outside SQLite so
settings exports, database backups and diagnostics do not disclose API keys.
"""

from __future__ import annotations

import base64
import ctypes
import json
import os
from ctypes import wintypes
from pathlib import Path

from .config import DATA_DIR
from .errors import AppError

SECRET_FILE = DATA_DIR / "secrets.json"
_ENV_KEYS = {
    "tikhub_api_key": "JIANZHOU_TIKHUB_API_KEY",
    "llm_api_key": "JIANZHOU_LLM_API_KEY",
    "asr_api_key": "JIANZHOU_ASR_API_KEY",
}


class _DataBlob(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD),
                ("pbData", ctypes.POINTER(ctypes.c_char))]


def _dpapi(operation: str, payload: bytes) -> bytes:
    if os.name != "nt":
        raise AppError(
            "SECRET_STORE_UNAVAILABLE",
            "当前系统不支持 Windows DPAPI。请通过环境变量配置密钥，系统不会明文落盘。",
            stage="secret_store",
            http_status=503,
        )
    crypt32 = ctypes.windll.crypt32
    kernel32 = ctypes.windll.kernel32
    in_buffer = ctypes.create_string_buffer(payload)
    in_blob = _DataBlob(len(payload), ctypes.cast(in_buffer, ctypes.POINTER(ctypes.c_char)))
    out_blob = _DataBlob()
    flags = 0x01  # CRYPTPROTECT_UI_FORBIDDEN
    if operation == "protect":
        ok = crypt32.CryptProtectData(
            ctypes.byref(in_blob), "JianzhouMediaWorkbench", None, None, None,
            flags, ctypes.byref(out_blob),
        )
    else:
        ok = crypt32.CryptUnprotectData(
            ctypes.byref(in_blob), None, None, None, None,
            flags, ctypes.byref(out_blob),
        )
    if not ok:
        raise AppError(
            "SECRET_STORE_FAILED",
            "系统凭据存储操作失败，密钥未保存。",
            stage="secret_store",
            http_status=500,
        )
    try:
        return ctypes.string_at(out_blob.pbData, out_blob.cbData)
    finally:
        kernel32.LocalFree(out_blob.pbData)


def _load() -> dict[str, str]:
    if not SECRET_FILE.exists():
        return {}
    try:
        data = json.loads(SECRET_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise AppError(
            "SECRET_STORE_CORRUPT",
            "本地凭据文件无法读取。系统不会忽略损坏并返回空配置。",
            stage="secret_store",
            details={"reason": exc.__class__.__name__},
            http_status=500,
        )
    if not isinstance(data, dict):
        raise AppError("SECRET_STORE_CORRUPT", "本地凭据文件格式不正确。",
                       stage="secret_store", http_status=500)
    return {str(k): str(v) for k, v in data.items()}


def _save(values: dict[str, str]) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    temp = SECRET_FILE.with_suffix(".tmp")
    temp.write_text(json.dumps(values, ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(SECRET_FILE)


def get_secret(name: str) -> str:
    env_name = _ENV_KEYS.get(name)
    if env_name and os.environ.get(env_name):
        return os.environ[env_name]
    encoded = _load().get(name)
    if not encoded:
        return ""
    try:
        encrypted = base64.b64decode(encoded, validate=True)
        return _dpapi("unprotect", encrypted).decode("utf-8")
    except AppError:
        raise
    except Exception as exc:
        raise AppError(
            "SECRET_STORE_CORRUPT",
            "本地凭据无法解密，可能由其他 Windows 用户创建或文件已损坏。",
            stage="secret_store",
            details={"reason": exc.__class__.__name__},
            http_status=500,
        )


def set_secret(name: str, value: str) -> None:
    values = _load()
    if value:
        encrypted = _dpapi("protect", value.encode("utf-8"))
        values[name] = base64.b64encode(encrypted).decode("ascii")
    else:
        values.pop(name, None)
    _save(values)


def has_secret(name: str) -> bool:
    return bool(get_secret(name))
