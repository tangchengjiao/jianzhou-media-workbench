"""WeChat Official Account connector with explicit permission probes.

External mutations only happen from explicit API actions in ``api.channels``.
The connector never falls back to a fake draft or silently publishes content.
"""
from __future__ import annotations

import time
import re
from pathlib import Path

import httpx

from .errors import AppError, config_missing_api_key
from .providers import get_setting


API_BASE = "https://api.weixin.qq.com"
_token_cache: dict[str, object] = {"token": "", "expires_at": 0.0, "app_id": ""}


def connector_settings() -> dict:
    return {
        "enabled": bool(get_setting("wechat_official", "enabled", False)),
        "account_name": str(get_setting("wechat_official", "account_name", "")),
        "original_id": str(get_setting("wechat_official", "original_id", "")).strip(),
        "account_type": str(get_setting("wechat_official", "account_type", "subscription")),
        "verified": bool(get_setting("wechat_official", "verified", False)),
        "app_id": str(get_setting("wechat_official", "app_id", "")).strip(),
        "app_secret": str(get_setting("wechat_official", "app_secret", "")).strip(),
        "default_author": str(get_setting("wechat_official", "default_author", "见舟AI")),
        "default_thumb_media_id": str(get_setting("wechat_official", "default_thumb_media_id", "")).strip(),
        "draft_push_enabled": bool(get_setting("wechat_official", "draft_push_enabled", True)),
        "publish_enabled": bool(get_setting("wechat_official", "publish_enabled", False)),
        "auto_publish_enabled": bool(get_setting("wechat_official", "auto_publish_enabled", False)),
        "ip_whitelist_note": str(get_setting("wechat_official", "ip_whitelist_note", "")),
    }


class WeChatOfficialClient:
    provider = "wechat_official"

    def __init__(self):
        self.settings = connector_settings()

    def _require(self) -> None:
        if not self.settings["enabled"]:
            raise AppError("WECHAT_OFFICIAL_DISABLED", "公众号连接尚未启用。请先在系统设置中完成配置。",
                           stage="wechat_config", provider=self.provider)
        if not self.settings["app_id"]:
            raise AppError("WECHAT_APP_ID_MISSING", "缺少公众号 AppID。",
                           stage="wechat_config", provider=self.provider)
        if not self.settings["app_secret"]:
            raise config_missing_api_key("微信公众号 AppSecret")

    @staticmethod
    def _egress_ips(message: str) -> list[str]:
        """Extract actionable IPv4 addresses without persisting credentials or tokens."""
        values: list[str] = []
        for candidate in re.findall(r"(?<![\d.])(?:\d{1,3}\.){3}\d{1,3}(?![\d.])", str(message or "")):
            parts = candidate.split(".")
            if len(parts) == 4 and all(part.isdigit() and 0 <= int(part) <= 255 for part in parts):
                if candidate not in values:
                    values.append(candidate)
        return values

    @staticmethod
    def _translate(payload: dict, stage: str) -> None:
        code = int(payload.get("errcode") or 0)
        if not code:
            return
        message = str(payload.get("errmsg") or "微信接口返回错误")
        fixes = {
            40001: "AppSecret 不正确或已重置，请更新后重试。",
            40164: "当前服务器出口 IP 不在公众号白名单，请在公众平台基本配置中加入该 IP。",
            48001: "当前公众号没有该接口权限；可继续使用 HTML 导出与人工粘贴草稿箱。",
            45009: "达到接口调用频率限制，请稍后重试。",
        }
        details = {"errcode": code, "errmsg": message}
        egress_ips = WeChatOfficialClient._egress_ips(message) if code == 40164 else []
        if egress_ips:
            details["egress_ips"] = egress_ips
            details["fix"] = "把出口 IP 加入微信公众平台 → 设置与开发 → 基本配置 → IP 白名单后重新测试。"
        raise AppError(
            "WECHAT_API_ERROR",
            fixes.get(code, f"微信接口失败：{message}"),
            stage=stage, provider="wechat_official", retryable=code in {45009, -1},
            details=details,
        )

    def access_token(self, force: bool = False) -> str:
        self._require()
        now = time.time()
        if (not force and _token_cache["token"] and _token_cache["app_id"] == self.settings["app_id"]
                and float(_token_cache["expires_at"] or 0) > now + 90):
            return str(_token_cache["token"])
        try:
            response = httpx.get(
                f"{API_BASE}/cgi-bin/token",
                params={"grant_type": "client_credential", "appid": self.settings["app_id"],
                        "secret": self.settings["app_secret"]},
                timeout=30.0, follow_redirects=False,
            )
        except httpx.HTTPError as exc:
            raise AppError("WECHAT_UNAVAILABLE", f"公众号连接失败：{exc.__class__.__name__}",
                           stage="wechat_token", provider=self.provider, retryable=True)
        if response.status_code != 200:
            raise AppError("WECHAT_UNAVAILABLE", f"公众号服务返回 HTTP {response.status_code}",
                           stage="wechat_token", provider=self.provider, retryable=response.status_code >= 500)
        payload = response.json()
        self._translate(payload, "wechat_token")
        token = str(payload.get("access_token") or "")
        if not token:
            raise AppError("WECHAT_TOKEN_INVALID", "公众号服务没有返回 access_token。",
                           stage="wechat_token", provider=self.provider)
        _token_cache.update({"token": token, "app_id": self.settings["app_id"],
                             "expires_at": now + int(payload.get("expires_in") or 7200)})
        return token

    def _post(self, path: str, payload: dict, stage: str) -> dict:
        token = self.access_token()
        try:
            response = httpx.post(f"{API_BASE}{path}", params={"access_token": token},
                                  json=payload, timeout=45.0, follow_redirects=False)
        except httpx.HTTPError as exc:
            raise AppError("WECHAT_UNAVAILABLE", f"公众号请求失败：{exc.__class__.__name__}",
                           stage=stage, provider=self.provider, retryable=True)
        if response.status_code != 200:
            raise AppError("WECHAT_UNAVAILABLE", f"公众号服务返回 HTTP {response.status_code}",
                           stage=stage, provider=self.provider, retryable=response.status_code >= 500)
        result = response.json()
        self._translate(result, stage)
        return result

    def probe(self) -> dict:
        capability = {"token": False, "draft": False, "publish": False}
        states = {
            "token": {"enabled": bool(self.settings["enabled"]), "verified": False,
                      "status": "PENDING" if self.settings["enabled"] else "DISABLED",
                      "reason": "等待真实连接测试" if self.settings["enabled"] else "本地设置未启用公众号连接"},
            "draft": {"enabled": bool(self.settings["draft_push_enabled"]), "verified": False,
                      "status": "PENDING" if self.settings["draft_push_enabled"] else "DISABLED",
                      "reason": "等待草稿箱只读权限测试" if self.settings["draft_push_enabled"] else "本地设置未启用草稿箱上传"},
            "publish": {"enabled": bool(self.settings["publish_enabled"]), "verified": False,
                        "status": "NOT_VERIFIED" if self.settings["publish_enabled"] else "DISABLED",
                        "reason": "正式发布不能用探测请求验证；仅在一次显式真实发布成功后形成证据"
                        if self.settings["publish_enabled"] else "本地设置未启用正式发布"},
        }
        errors: list[dict] = []
        egress_ips: list[str] = []
        if not self.settings["enabled"]:
            return {
                "status": "DISABLED", "reachable": False, "capabilities": capability,
                "capability_states": states, "errors": [], "egress_ips": [],
                "next_actions": ["先启用公众号连接并保存 AppID、AppSecret，再测试真实权限。"],
                "account_name": self.settings["account_name"],
                "account_type": self.settings["account_type"], "verified": self.settings["verified"],
            }
        try:
            token = self.access_token(force=True)
            capability["token"] = bool(token)
            states["token"].update({"verified": bool(token), "status": "VERIFIED",
                                    "reason": "微信已返回有效 access_token"})
        except AppError as exc:
            error = exc.to_dict()
            errors.append({"scope": "token", **error})
            egress_ips.extend(error.get("details", {}).get("egress_ips") or [])
            states["token"].update({"status": "BLOCKED", "reason": exc.message})
            if states["draft"]["enabled"]:
                states["draft"].update({"status": "BLOCKED", "reason": "Token 尚未通过，无法继续验证草稿箱权限"})
            next_actions = ["检查 AppID 与 AppSecret，并重新测试。"]
            if egress_ips:
                next_actions = [
                    f"把出口 IP {', '.join(dict.fromkeys(egress_ips))} 加入微信公众平台 IP 白名单。",
                    "保存后重新点击“测试真实权限”；通过前仍可复制 HTML 手动进入公众号草稿箱。",
                ]
            return {
                "status": "BLOCKED", "reachable": False, "capabilities": capability,
                "capability_states": states, "errors": errors,
                "egress_ips": list(dict.fromkeys(egress_ips)), "next_actions": next_actions,
                "account_name": self.settings["account_name"],
                "account_type": self.settings["account_type"], "verified": self.settings["verified"],
            }

        try:
            result = self._post("/cgi-bin/draft/batchget", {"offset": 0, "count": 1, "no_content": 1},
                                "wechat_draft_probe")
            capability["draft"] = "total_count" in result or "item_count" in result
            states["draft"].update({
                "verified": capability["draft"],
                "status": "VERIFIED" if capability["draft"] else "UNAVAILABLE",
                "reason": "已通过草稿箱只读接口验证" if capability["draft"] else "微信响应中没有草稿箱权限证据",
            })
        except AppError as exc:
            error = exc.to_dict()
            errors.append({"scope": "draft", **error})
            egress_ips.extend(error.get("details", {}).get("egress_ips") or [])
            states["draft"].update({"status": "BLOCKED", "reason": exc.message})
        # A read-only probe must never claim that the mutating publish permission was verified.
        capability["publish"] = False
        status = "READY_FOR_DRAFT" if capability["draft"] else "PARTIAL"
        next_actions = (["草稿箱权限已验证；正式发布仍需显式操作并以微信成功回执为准。"]
                        if capability["draft"] else ["根据草稿箱错误修复权限；期间可复制 HTML 手动粘贴。"])
        return {"status": status, "reachable": bool(token), "capabilities": capability,
                "capability_states": states, "errors": errors,
                "egress_ips": list(dict.fromkeys(egress_ips)), "next_actions": next_actions,
                "account_name": self.settings["account_name"],
                "account_type": self.settings["account_type"], "verified": self.settings["verified"]}

    def upload_permanent_image(self, path: Path) -> dict:
        if not path.exists() or not path.is_file():
            raise AppError("WECHAT_IMAGE_NOT_FOUND", "用于公众号封面的图片不存在。",
                           stage="wechat_material", provider=self.provider)
        token = self.access_token()
        mime = "image/png" if path.suffix.lower() == ".png" else "image/jpeg"
        try:
            with path.open("rb") as stream:
                response = httpx.post(
                    f"{API_BASE}/cgi-bin/material/add_material",
                    params={"access_token": token, "type": "image"},
                    files={"media": (path.name, stream, mime)}, timeout=60.0,
                    follow_redirects=False,
                )
        except httpx.HTTPError as exc:
            raise AppError("WECHAT_UNAVAILABLE", f"上传封面素材失败：{exc.__class__.__name__}",
                           stage="wechat_material", provider=self.provider, retryable=True)
        if response.status_code != 200:
            raise AppError("WECHAT_UNAVAILABLE", f"公众号素材服务返回 HTTP {response.status_code}",
                           stage="wechat_material", provider=self.provider,
                           retryable=response.status_code >= 500)
        result = response.json()
        self._translate(result, "wechat_material")
        if not result.get("media_id"):
            raise AppError("WECHAT_MATERIAL_INVALID", "微信没有返回封面素材 media_id。",
                           stage="wechat_material", provider=self.provider)
        return result

    def add_draft(self, article: dict) -> dict:
        if not self.settings["draft_push_enabled"]:
            raise AppError("WECHAT_DRAFT_DISABLED", "当前设置禁止上传公众号草稿。",
                           stage="wechat_draft", provider=self.provider)
        thumb = str(article.get("thumb_media_id") or self.settings["default_thumb_media_id"] or "")
        if not thumb:
            raise AppError("WECHAT_THUMB_REQUIRED", "上传草稿前需要封面素材 media_id。请先配置默认封面或上传本篇封面。",
                           stage="wechat_draft", provider=self.provider)
        payload = {"articles": [{
            "title": str(article.get("title") or "")[:64],
            "author": str(article.get("author") or self.settings["default_author"] or "")[:16],
            "digest": str(article.get("digest") or "")[:120],
            "content": str(article.get("content") or ""),
            "content_source_url": str(article.get("content_source_url") or ""),
            "thumb_media_id": thumb,
            "need_open_comment": 1,
            "only_fans_can_comment": 0,
        }]}
        return self._post("/cgi-bin/draft/add", payload, "wechat_draft")

    def publish(self, media_id: str) -> dict:
        if not self.settings["publish_enabled"]:
            raise AppError("WECHAT_PUBLISH_DISABLED", "当前仅允许上传草稿箱；自动发布尚未启用。",
                           stage="wechat_publish", provider=self.provider)
        return self._post("/cgi-bin/freepublish/submit", {"media_id": media_id}, "wechat_publish")

    def publish_status(self, publish_id: str) -> dict:
        return self._post("/cgi-bin/freepublish/get", {"publish_id": publish_id}, "wechat_publish_status")
