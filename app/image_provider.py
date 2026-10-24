"""真实图片生成 Provider。

只支持显式配置的 OpenAI-compatible ``images/generations`` 协议。真实模式下
配置缺失或请求失败都会直接报错，不会用本地模板冒充 AI 生成结果。
"""
from __future__ import annotations

import base64
import io
import time
import urllib.parse
from pathlib import Path

from PIL import Image, ImageDraw

from . import config
from .errors import AppError, config_missing_api_key
from .providers import _validate_llm_url, get_setting


RATIO_SIZES = {
    "3:4": "1024x1536",
    "9:16": "1024x1536",
    "16:9": "1536x1024",
    "1:1": "1024x1024",
}


def normalize_image_endpoint(api_base: str, generation_path: str = "images/generations") -> tuple[str, str]:
    """Accept either an API root or a complete ``images/generations`` URL."""
    base = str(api_base or "").strip().rstrip("/")
    path = str(generation_path or "images/generations").strip().strip("/")
    if not base:
        return base, path
    parsed = urllib.parse.urlparse(base)
    suffix = "/images/generations"
    parsed_path = parsed.path.rstrip("/")
    if parsed_path.endswith(suffix):
        root_path = parsed_path[:-len(suffix)] or ""
        base = urllib.parse.urlunparse((parsed.scheme, parsed.netloc, root_path, "", "", "")).rstrip("/")
        path = "images/generations"
    return base, path


class DemoImageProvider:
    name = "demo_image"
    model = "demo-gradient"

    def generate(self, prompt: str, ratio: str, *, quality: str = "high",
                 reference_images: list[str] | None = None) -> dict:
        width, height = (768, 1024) if ratio == "3:4" else ((720, 1280) if ratio == "9:16"
                         else ((1280, 720) if ratio == "16:9" else (1024, 1024)))
        image = Image.new("RGB", (width, height), "#07111f")
        draw = ImageDraw.Draw(image, "RGBA")
        for y in range(height):
            t = y / max(1, height - 1)
            draw.line((0, y, width, y), fill=(7 + int(15*t), 17 + int(35*t), 31 + int(75*t), 255))
        for index in range(10):
            x = int(width * (index / 9))
            draw.line((x, 0, max(0, x-int(width*.18)), height), fill=(47, 140, 255, 35), width=2)
        output = io.BytesIO()
        image.save(output, "PNG")
        return {"bytes": output.getvalue(), "provider": self.name, "model": self.model,
                "request_id": "demo-image", "revised_prompt": prompt, "cost": 0.0}


class RealImageProvider:
    name = "openai_compatible_image"

    def __init__(self) -> None:
        base_url = str(get_setting("image", "api_base") or "")
        self.api_key = str(get_setting("image", "api_key") or "").strip()
        self.model = str(get_setting("image", "model") or "").strip()
        generation_path = str(get_setting("image", "generation_path", "images/generations") or "")
        self.base_url, self.path = normalize_image_endpoint(base_url, generation_path)
        self.auth_mode = str(get_setting("image", "auth_mode", "bearer") or "bearer")
        self.allow_private = bool(get_setting("image", "allow_private_network", False))

    def _require(self) -> None:
        if not self.base_url or not self.model:
            raise AppError("IMAGE_CONFIG_INCOMPLETE",
                           "请先在系统设置的“图片模型”中填写 API Base URL 和模型名称。",
                           stage="image_config", provider=self.name)
        if self.auth_mode == "bearer" and not self.api_key:
            raise config_missing_api_key("图片模型")
        _validate_llm_url(self.base_url, allow_private=self.allow_private, resolve_dns=True)

    def _endpoint(self) -> str:
        return f"{self.base_url}/{self.path}"

    def _headers(self) -> dict:
        headers = {"Content-Type": "application/json"}
        if self.auth_mode == "bearer":
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers

    def _is_apimart(self) -> bool:
        return (urllib.parse.urlparse(self.base_url).hostname or "").lower() == "api.apimart.ai"

    def _poll_async_image(self, client, task_id: str) -> tuple[str, float]:
        deadline = time.monotonic() + 210
        task_url = f"{self.base_url}/tasks/{urllib.parse.quote(task_id, safe='')}"
        while time.monotonic() < deadline:
            try:
                response = client.get(task_url, headers=self._headers())
            except Exception as exc:
                raise AppError("IMAGE_PROVIDER_UNAVAILABLE", "图片任务状态查询失败。",
                               stage="image_poll", retryable=True, provider=self.name,
                               details={"reason": exc.__class__.__name__})
            if response.status_code >= 400:
                raise AppError("IMAGE_PROVIDER_ERROR",
                               f"图片任务状态查询异常（HTTP {response.status_code}）。",
                               stage="image_poll", retryable=response.status_code >= 500,
                               provider=self.name)
            payload = response.json()
            data = payload.get("data") or {}
            status = str(data.get("status") or "").lower()
            if status == "completed":
                images = ((data.get("result") or {}).get("images") or [])
                urls = (images[0].get("url") or []) if images else []
                if not urls:
                    raise AppError("IMAGE_OUTPUT_INVALID", "图片任务完成但没有返回图片地址。",
                                   stage="image_parse", retryable=True, provider=self.name)
                return str(urls[0]), float(data.get("cost") or 0)
            if status in {"failed", "cancelled"}:
                reason = str(data.get("fail_reason") or data.get("error") or "")[:500]
                raise AppError("IMAGE_PROVIDER_ERROR", "图片生成任务失败。",
                               stage="image_generate", retryable=False, provider=self.name,
                               details={"provider_message": reason, "task_status": status})
            time.sleep(3)
        raise AppError("IMAGE_PROVIDER_TIMEOUT", "图片生成任务等待超时，本次没有保存伪造结果。",
                       stage="image_poll", retryable=True, provider=self.name)

    def _download_image(self, client, url: str) -> bytes:
        parsed = urllib.parse.urlparse(url)
        if parsed.scheme != "https" or not parsed.hostname:
            raise ValueError("provider returned an unsafe image URL")
        _validate_llm_url(url, allow_private=False, resolve_dns=True)
        downloaded = client.get(url)
        downloaded.raise_for_status()
        return downloaded.content

    @staticmethod
    def _reference_data_urls(reference_images: list[str] | None) -> list[str]:
        """Convert explicitly selected local/remote references to provider-safe inputs."""
        values: list[str] = []
        for value in reference_images or []:
            candidate = str(value or "").strip()
            if not candidate:
                continue
            if candidate.startswith("https://") or candidate.startswith("data:image/"):
                values.append(candidate)
                continue
            path = Path(candidate)
            if not path.is_file():
                continue
            suffix = path.suffix.lower()
            mime = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp"}.get(
                suffix, "image/png")
            encoded = base64.b64encode(path.read_bytes()).decode("ascii")
            values.append(f"data:{mime};base64,{encoded}")
        return values[:16]

    def generate(self, prompt: str, ratio: str, *, quality: str = "high",
                 reference_images: list[str] | None = None) -> dict:
        import httpx
        self._require()
        payload = {"model": self.model, "prompt": prompt, "n": 1}
        references = self._reference_data_urls(reference_images)
        if self._is_apimart():
            payload.update({"size": ratio if ratio in RATIO_SIZES else "3:4",
                            "resolution": "2k" if quality == "high" else "1k"})
            if references:
                payload["image_urls"] = references
        else:
            payload.update({"size": RATIO_SIZES.get(ratio, "1024x1536"),
                            "quality": quality, "response_format": "b64_json"})
            if references:
                # Several OpenAI-compatible gateways accept image_urls on their
                # generations endpoint. Unsupported gateways return a visible
                # provider error instead of silently discarding identity refs.
                payload["image_urls"] = references
        try:
            with httpx.Client(timeout=210.0, follow_redirects=False) as client:
                response = client.post(self._endpoint(), headers=self._headers(), json=payload)
                if response.status_code >= 400:
                    message = ""
                    try:
                        error_body = response.json()
                        message = str((error_body.get("error") or {}).get("message") or error_body.get("message") or "")[:500]
                    except Exception:
                        pass
                    raise AppError("IMAGE_PROVIDER_ERROR",
                                   f"图片模型返回异常（HTTP {response.status_code}）。",
                                   stage="image_generate", retryable=response.status_code >= 500,
                                   provider=self.name, details={"provider_message": message})
                body = response.json()
                data = body.get("data") or []
                first = data[0] if isinstance(data, list) and data else {}
                task_id = str(first.get("task_id") or "") if isinstance(first, dict) else ""
                async_url, async_cost = (self._poll_async_image(client, task_id)
                                         if task_id else ("", 0.0))
                if async_url:
                    raw = self._download_image(client, async_url)
                    item = {"url": async_url}
                else:
                    item = first if isinstance(first, dict) else {}
                    encoded = item.get("b64_json")
                    if encoded:
                        raw = base64.b64decode(encoded, validate=True)
                    else:
                        raw = self._download_image(client, str(item.get("url") or ""))
        except httpx.TimeoutException:
            raise AppError("IMAGE_PROVIDER_TIMEOUT", "图片模型生成超时，本次没有保存伪造结果。",
                           stage="image_generate", retryable=True, provider=self.name)
        except httpx.RequestError as exc:
            raise AppError("IMAGE_PROVIDER_UNAVAILABLE", "无法连接图片模型服务。",
                           stage="image_generate", retryable=True, provider=self.name,
                           details={"reason": exc.__class__.__name__})
        except AppError:
            raise
        try:
            image = Image.open(io.BytesIO(raw))
            image.verify()
        except Exception as exc:
            raise AppError("IMAGE_OUTPUT_INVALID", "图片模型没有返回有效图片。",
                           stage="image_parse", retryable=True, provider=self.name,
                           details={"reason": exc.__class__.__name__})
        return {
            "bytes": raw, "provider": self.name,
            "model": str(body.get("model") or self.model),
            "request_id": task_id or response.headers.get("x-request-id", ""),
            "revised_prompt": str(item.get("revised_prompt") or prompt),
            "cost": async_cost,
        }

    def check_connection(self) -> dict:
        result = self.generate(
            "Premium dark navy AI product-launch background, subtle blue light, clean empty layout, no text, no person",
            "1:1", quality="low",
        )
        return {"provider": result["provider"], "model": result["model"],
                "reachable": True, "image_bytes": len(result["bytes"]),
                "request_id": result["request_id"]}


def get_image_provider():
    return DemoImageProvider() if config.DEMO_MODE else RealImageProvider()
