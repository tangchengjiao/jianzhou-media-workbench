"""Safe local JPEG cache for provider cover images unsupported by browsers.

Douyin's current App V3 response can contain signed HEIC/HEIF cover URLs.  The
signature prevents changing the filename suffix and Chromium does not decode
those formats, so the desktop app converts them once and serves the cached
JPEG on subsequent requests.
"""
from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import threading
import urllib.parse
import uuid
from pathlib import Path

import httpx

from . import config
from .datasource import _validate_media_url
from .errors import AppError


_MAX_COVER_BYTES = 15 * 1024 * 1024
_locks_guard = threading.Lock()
_locks: dict[str, threading.Lock] = {}


def browser_needs_cover_proxy(url: str) -> bool:
    path = urllib.parse.urlparse(str(url or "")).path.lower()
    return path.endswith((".heic", ".heif"))


def _lock_for(cache_key: str) -> threading.Lock:
    with _locks_guard:
        return _locks.setdefault(cache_key, threading.Lock())


def _download_cover(url: str, destination: Path) -> None:
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/127.0 Safari/537.36"
        ),
        "Accept": "image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8",
        "Referer": "https://www.douyin.com/",
    }
    current_url = url
    with httpx.Client(timeout=httpx.Timeout(45.0, connect=20.0),
                      follow_redirects=False) as client:
        for _ in range(6):
            _validate_media_url(current_url, resolve_dns=True)
            with client.stream("GET", current_url, headers=headers) as response:
                if 300 <= response.status_code < 400 and response.headers.get("location"):
                    current_url = urllib.parse.urljoin(
                        current_url, response.headers["location"]
                    )
                    continue
                if response.status_code != 200:
                    raise AppError(
                        "COVER_DOWNLOAD_FAILED",
                        f"平台封面下载失败（HTTP {response.status_code}）。",
                        stage="cover_download", provider="platform_cdn",
                        retryable=response.status_code >= 500,
                        details={"http_status": response.status_code}, http_status=502,
                    )
                content_type = response.headers.get("content-type", "").lower()
                if content_type and not content_type.startswith("image/"):
                    raise AppError(
                        "COVER_CONTENT_INVALID", "平台返回的封面不是图片。",
                        stage="cover_download", provider="platform_cdn",
                        details={"content_type": content_type}, http_status=502,
                    )
                size = 0
                with destination.open("wb") as output:
                    for chunk in response.iter_bytes(64 * 1024):
                        size += len(chunk)
                        if size > _MAX_COVER_BYTES:
                            raise AppError(
                                "COVER_TOO_LARGE", "平台封面超过 15 MB，已停止下载。",
                                stage="cover_download", provider="platform_cdn",
                                http_status=502,
                            )
                        output.write(chunk)
                if size < 32:
                    raise AppError(
                        "COVER_CONTENT_INVALID", "平台返回的封面内容为空。",
                        stage="cover_download", provider="platform_cdn", http_status=502,
                    )
                return
    raise AppError(
        "COVER_REDIRECT_LIMIT", "平台封面重定向次数过多，已停止下载。",
        stage="cover_download", provider="platform_cdn", http_status=502,
    )


def cached_jpeg_cover(url: str) -> Path:
    """Download and convert a trusted provider cover, returning a stable JPEG."""
    _validate_media_url(url, resolve_dns=True)
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise AppError(
            "COVER_CONVERTER_MISSING",
            "当前封面为 HEIC/HEIF，本机缺少 FFmpeg，暂时无法转换为浏览器可显示格式。",
            stage="cover_convert", provider="ffmpeg", http_status=503,
        )

    cache_key = hashlib.sha256(url.encode("utf-8")).hexdigest()
    cache_dir = config.DATA_DIR / "cover_cache"
    destination = cache_dir / f"{cache_key}.jpg"
    if destination.is_file() and destination.stat().st_size > 100:
        return destination

    cache_dir.mkdir(parents=True, exist_ok=True)
    with _lock_for(cache_key):
        if destination.is_file() and destination.stat().st_size > 100:
            return destination
        nonce = uuid.uuid4().hex
        source = cache_dir / f".{cache_key}-{nonce}.heic"
        converted = cache_dir / f".{cache_key}-{nonce}.jpg"
        try:
            _download_cover(url, source)
            completed = subprocess.run(
                [ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
                 "-i", str(source), "-frames:v", "1", "-q:v", "3", str(converted)],
                capture_output=True, timeout=45, check=False,
            )
            if completed.returncode or not converted.is_file() or converted.stat().st_size < 100:
                raise AppError(
                    "COVER_CONVERSION_FAILED",
                    "HEIC/HEIF 封面转换失败，原始作品数据不受影响。",
                    stage="cover_convert", provider="ffmpeg", retryable=True,
                    details={"return_code": completed.returncode}, http_status=502,
                )
            os.replace(converted, destination)
            return destination
        finally:
            source.unlink(missing_ok=True)
            converted.unlink(missing_ok=True)
