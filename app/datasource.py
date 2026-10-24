"""数据源适配层：配置驱动 endpoint 映射执行器（借鉴参考项目 datasource.js）。

能力：resolve_account / account_overview / account_videos / video_text / video_media /
      video_comments / search_content
接入或更换供应商只改设置中的 endpoints 映射，代码零改动。

两种 Provider：
- DemoDatasource：确定性演示数据（演示模式，provider 标记 demo_ds）；
- EndpointExecutor：真实 HTTP 执行器，按 provider 配置（baseUrl/auth/endpoints）调用。
  未配置即 CONFIG_MISSING_API_KEY（fail-fast，绝不返回假数据）。
"""
import hashlib
import ipaddress
import json
import os
import socket
import time
import urllib.parse
from datetime import datetime
from email.utils import parsedate_to_datetime

import httpx

from . import config
from .db import jloads, query_one
from .errors import AppError, config_missing_api_key
from .secret_store import get_secret


# ================= 极简 JSONPath：支持 $.a.b[0].c =================

def json_path(obj, path: str):
    if obj is None or not path or not path.startswith("$"):
        return None
    tokens = [t for t in path[1:].replace("[", ".").replace("]", "").split(".") if t]
    cur = obj
    for t in tokens:
        if cur is None:
            return None
        if isinstance(cur, list) and t.isdigit():
            cur = cur[int(t)] if int(t) < len(cur) else None
        elif isinstance(cur, dict):
            cur = cur.get(t)
        else:
            return None
    return cur


def json_path_first(obj, paths):
    candidates = paths if isinstance(paths, list) else [paths]
    for path in candidates:
        value = json_path(obj, path)
        if value is not None:
            return value
    return None


def map_obj(src: dict, mapping: dict) -> dict:
    return {k: json_path_first(src, v) for k, v in (mapping or {}).items()}


def _text_value(value) -> str:
    """Normalize provider-specific scalar/list URL fields before SQLite binding."""
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, (int, float, bool)):
        return str(value)
    if isinstance(value, list):
        return _text_value(value[0]) if value else ""
    if isinstance(value, dict):
        for key in ("url", "uri", "url_list", "download_url"):
            if key in value:
                return _text_value(value[key])
    return ""


def _int_value(value) -> int:
    if value in (None, ""):
        return 0
    if isinstance(value, bool):
        return int(value)
    try:
        return int(float(str(value).replace(",", "")))
    except (TypeError, ValueError):
        text = str(value).strip()
        try:
            return int(parsedate_to_datetime(text).timestamp())
        except (TypeError, ValueError, OverflowError):
            try:
                return int(datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp())
            except (TypeError, ValueError, OverflowError):
                return 0


# ================= 错误分类翻译层（HTTP → 用户可懂） =================

def classify_http(status: int, provider: str) -> AppError:
    if status in (401, 403):
        return AppError("TIKHUB_UNAUTHORIZED", "数据接口密钥无效，请到系统设置检查 API Key",
                        stage="provider_call", provider=provider,
                        details={"http_status": status, "charged": False})
    if status in (402, 429):
        return AppError("TIKHUB_INSUFFICIENT_BALANCE", "数据接口余额不足或请求过频，请稍后再试",
                        stage="provider_call", retryable=True, provider=provider,
                        details={"http_status": status})
    if status == 404:
        return AppError("TIKHUB_ENDPOINT_NOT_FOUND", "数据接口地址不正确，请到系统设置检查 endpoints 映射",
                        stage="provider_call", provider=provider,
                        details={"http_status": status})
    return AppError("TIKHUB_PROVIDER_ERROR", f"数据接口返回异常（HTTP {status}）",
                    stage="provider_call", retryable=True, provider=provider,
                    details={"http_status": status})


def _authorization_header(value: str) -> str:
    return value if value.lower().startswith("bearer ") else "Bearer " + value


# ================= 平台识别与链接解析 =================

PLATFORM_REGISTRY = {
    "douyin": {"label": "抖音", "group": "国内主流", "id_label": "主页链接 / sec_user_id",
                "placeholder": "粘贴抖音主页或分享链接，也可输入 sec_user_id"},
    "xhs": {"label": "小红书", "group": "国内主流", "id_label": "主页链接 / user_id",
            "placeholder": "粘贴小红书用户主页、分享链接或 user_id"},
    "channels": {"label": "视频号", "group": "国内主流", "id_label": "finder username / 视频号ID",
                 "placeholder": "输入 v2_…@finder 或 sph… 视频号ID"},
    "bilibili": {"label": "B站", "group": "国内主流", "id_label": "空间链接 / UID",
                 "placeholder": "粘贴 space.bilibili.com 主页链接或数字 UID"},
    "kuaishou": {"label": "快手", "group": "国内主流", "id_label": "主页链接 / eid",
                 "placeholder": "粘贴快手 profile 主页、分享链接或 eid"},
    "weibo": {"label": "微博", "group": "国内主流", "id_label": "主页链接 / UID",
              "placeholder": "粘贴微博用户主页链接或数字 UID"},
    "tiktok": {"label": "TikTok", "group": "海外主流", "id_label": "主页链接 / 用户名 / secUid",
               "placeholder": "粘贴 TikTok @用户名主页或输入 secUid"},
}
PLATFORM_LABELS = {key: value["label"] for key, value in PLATFORM_REGISTRY.items()}
SUPPORTED_PLATFORMS = frozenset(PLATFORM_REGISTRY)


def platform_catalog() -> list[dict]:
    return [{"id": key, **value} for key, value in PLATFORM_REGISTRY.items()]


def detect_platform(link: str) -> str | None:
    s = (link or "").lower()
    if any(k in s for k in ("xiaohongshu", "xhslink", "xhs")):
        return "xhs"
    if any(k in s for k in ("douyin", "v.douyin", "iesdouyin")):
        return "douyin"
    if any(k in s for k in ("bilibili", "b23.tv")):
        return "bilibili"
    if any(k in s for k in ("channels", "weixin", "finder", "sph")):
        return "channels"
    if "kuaishou" in s:
        return "kuaishou"
    if any(k in s for k in ("weibo.com", "weibo.cn")):
        return "weibo"
    if "tiktok.com" in s:
        return "tiktok"
    return None


# ================= Demo 数据源（确定性生成） =================

_DEMO_NAMES = ["运营第6年", "崔璀", "林安说职场", "张长江聊管理", "阿May方法论", "老王说产品"]
_DEMO_TITLES = [
    "为什么你的汇报总被打断", "工作五年，我劝你学会“偷懒”", "30 岁前必须懂的 3 个职场真相",
    "周报怎么写，领导才会真的看", "被领导误解时，千万别急着解释", "晋升答辩被问住的 4 个问题",
    "大厂裁员潮下的职业选择", "向上管理不是讨好，是翻译", "我劝你别急着做副业",
    "开会发言总被抢话怎么办", "年中述职前的 3 个准备", "职场里最高级的拒绝方式",
    "为什么你带的实习生都留不住", "把经验变成方法论的 5 个步骤", "跨部门协作的潜规则",
    "普通人做自媒体的第一条内容怎么发", "5 个高效工具，第 3 个真的香", "述职报告的黄金结构",
]


class DemoDatasource:
    """演示数据源：按 uid 哈希生成确定性数据。"""
    name = "demo_ds"

    def _rng(self, uid: str) -> int:
        return int(hashlib.md5(uid.encode()).hexdigest()[:8], 16)

    def resolve_account(self, link: str, platform: str | None = None) -> dict:
        plat = platform or detect_platform(link) or "douyin"
        if plat not in SUPPORTED_PLATFORMS:
            raise AppError("PLATFORM_UNSUPPORTED", f"暂不支持平台：{plat}",
                           stage="resolve_account", provider=self.name)
        if not link.strip():
            raise AppError("RESOLVE_FAILED", "请粘贴有效的主页链接或分享口令",
                           stage="resolve_account", provider=self.name)
        h = self._rng(link)
        return {
            "uid": f"demo_{plat}_{h % 100000}",
            "platform": plat,
            "name": _DEMO_NAMES[h % len(_DEMO_NAMES)],
            "link": link.strip(),
        }

    def account_overview(self, uid: str, platform: str) -> dict:
        h = self._rng(uid)
        return {
            "uid": uid, "platform": platform,
            "name": _DEMO_NAMES[h % len(_DEMO_NAMES)],
            "avatar": "", "bio": "专注职场方法论 6 年 · 每周更新 3 条 · 咨询请私信",
            "fans": 80000 + h % 400000,
            "likes": 300000 + h % 2000000,
            "video_count": 120 + h % 300,
            "availability": {"fans": True, "likes": True, "video_count": True},
        }

    def account_videos(self, uid: str, platform: str, limit: int = 30,
                       cursor: str | None = None) -> dict:
        h = self._rng(uid)
        base = time.time()
        works = []
        n = min(max(int(limit), 1), 500)
        try:
            offset = max(0, int(cursor or 0))
        except (TypeError, ValueError):
            offset = 0
        available = 500
        n = min(n, max(0, available - offset))
        for idx in range(n):
            i = offset + idx
            seed = h + i * 7919
            likes = 800 + seed % 90000
            comments = likes // (20 + seed % 30)
            collects = likes // (6 + seed % 8)
            works.append({
                "video_id": f"v{uid[-6:]}_{i:03d}",
                "title": _DEMO_TITLES[seed % len(_DEMO_TITLES)],
                "cover": "", "url": f"https://example.com/video/{uid[-6:]}_{i}",
                "create_time": int(base - i * 86400 * (1 + seed % 3)),
                "plays": likes * (12 + seed % 30),
                "likes": likes, "comments": comments, "collects": collects,
                "shares": comments // 3, "duration": 60 + seed % 180,
            })
        next_offset = offset + len(works)
        return {"works": works, "cursor": str(next_offset),
                "has_more": next_offset < available, "pages": max(1, (len(works) + 19) // 20)}

    def video_text(self, video_id: str, platform: str, title: str = "") -> dict:
        return {"text": f"【演示文案】《{title or video_id}》：为什么你的汇报总被打断？"
                        "不是领导没耐心，是顺序讲反了。领导脑子里只有三件事：结论、风险、诉求……",
                "cost": 0.10}

    def video_comments(self, video_id: str, platform: str, limit: int = 200) -> dict:
        pool = ["两个 offer 钱差不多，大厂打杂和小公司核心岗怎么选？",
                "向上管理具体怎么做？求展开", "收藏了，下次汇报直接套用",
                "终于有人不说搞钱焦虑了", "这个模板能发一下吗"]
        return {"comments": [{"user": f"用户{i}", "text": pool[i % len(pool)],
                              "likes": 2000 - i * 37} for i in range(min(limit, 20))],
                "cost": 0.08}

    def search_content(self, keyword: str, platform: str = "douyin",
                       limit: int = 20) -> dict:
        h = self._rng(keyword)
        return {"works": [{"video_id": f"kw{h%9999}_{i}",
                           "title": f"{keyword} 相关内容 {i+1}",
                           "url": "", "platform": platform,
                           "likes": 5000 + h % 20000}
                          for i in range(min(max(int(limit), 1), 6))],
                "hot_trend": 30 + h % 60}

    def work_share_url(self, video_id: str, platform: str) -> dict:
        return {"url": canonical_platform_work_url(platform, video_id),
                "cost": 0.0, "provider": self.name}

    def platform_hot(self, platform: str, limit: int = 20) -> dict:
        topics = ["AI 办公提效", "普通人如何建立个人品牌", "短视频选题方法",
                  "职场表达", "内容创作工作流", "消费新趋势"]
        return {"items": [
            {"id": f"demo-{platform}-{i}", "title": title, "rank": i + 1,
             "heat": 980000 - i * 73000, "url": "", "raw": {"demo": True}}
            for i, title in enumerate(topics[:max(1, min(limit, len(topics)))])
        ], "provider": self.name}


# ================= 真实 HTTP 执行器（配置驱动） =================

class EndpointExecutor:
    """按 provider 配置执行 endpoint 映射。配置存于 app_settings.datasource.providers。<active>"""

    def __init__(self):
        cfg = self._load_config()
        self.active = cfg.get("active", "demo")
        self.pconf = (cfg.get("providers") or {}).get(self.active) or {}
        self.name = f"real_{self.active}"
        self._last_call = 0.0

    @staticmethod
    def _load_config() -> dict:
        row = query_one("SELECT value_json FROM app_settings WHERE key='datasource'")
        cfg = jloads(row["value_json"], {}) if row else {}
        active = cfg.get("active", "")
        provider = (cfg.get("providers") or {}).get(active) or {}
        if active == "tikhub":
            provider.setdefault("auth", {})["value"] = get_secret("tikhub_api_key")
        return cfg

    def is_configured(self) -> bool:
        auth = self.pconf.get("auth") or {}
        return bool(self.pconf.get("baseUrl") and auth.get("value"))

    def _require(self):
        if not self.is_configured():
            raise config_missing_api_key(f"数据源（{self.active}）")
        _validate_provider_url(self.pconf["baseUrl"])

    def require_configured(self) -> None:
        """Public fail-fast boundary used before creating persistent records."""
        self._require()

    def _throttle(self):
        rpm = (self.pconf.get("rateLimit") or {}).get("rpm", 30)
        gap = max(0.0, 60.0 / max(rpm, 1))
        wait = self._last_call + gap - time.time()
        if wait > 0:
            time.sleep(wait)
        self._last_call = time.time()

    def _pick_ep(self, capability: str, platform: str) -> dict | None:
        cap = (self.pconf.get("endpoints") or {}).get(capability)
        if not cap:
            return None
        if cap.get("path"):
            return cap
        return cap.get(platform) or cap.get("douyin")

    def call_endpoint(self, capability: str, args: dict) -> dict:
        self._require()
        platform = args.get("platform", "douyin")
        ep = self._pick_ep(capability, platform)
        if not ep or not ep.get("path"):
            raise AppError("ENDPOINT_NOT_MAPPED",
                           f"数据源未配置能力映射：{capability} / {platform}。"
                           "请在系统设置 → 数据源中补齐 endpoints。",
                           stage="endpoint_mapping", provider=self.name)
        try:
            result = self._call_endpoint_config(ep, args)
        except AppError:
            fallback = ep.get("fallback")
            if not fallback:
                raise
            result = self._call_endpoint_config(fallback, args)
        data = result.get("data")
        empty_mapped_object = (
            isinstance(data, dict)
            and data
            and all(value in (None, "", []) for value in data.values())
        )
        if ep.get("fallback") and (not data or empty_mapped_object):
            result = self._call_endpoint_config(ep["fallback"], args)
        return result

    def _call_endpoint_config(self, ep: dict, args: dict) -> dict:
        """Execute one concrete endpoint mapping; callers may provide a declared fallback."""
        self._throttle()
        url = self.pconf["baseUrl"].rstrip("/") + _subst(ep["path"], args)
        _validate_provider_url(url, resolve_dns=True)
        qs = []
        for k, v in (ep.get("params") or {}).items():
            qs.append(f"{k}={_subst(v, args)}")
        auth = self.pconf.get("auth") or {}
        headers = {}
        if auth.get("value"):
            if auth.get("type", "header") == "header":
                field = auth.get("field", "Authorization")
                value = auth["value"]
                if field.lower() == "authorization":
                    value = _authorization_header(value)
                headers[field] = value
            else:
                qs.append(f"{auth.get('field', 'token')}={auth['value']}")
        if qs:
            url += ("&" if "?" in url else "?") + "&".join(qs)
        method = (ep.get("method") or "GET").upper()
        try:
            with httpx.Client(timeout=float(ep.get("timeout", 45)), follow_redirects=False) as client:
                if method == "POST" and ep.get("bodyMap"):
                    body = {k: (_subst_raw(v, args) if isinstance(v, str) else v)
                            for k, v in ep["bodyMap"].items()}
                    resp = client.post(url, json=body, headers=headers)
                else:
                    resp = client.request(method, url, headers=headers)
        except httpx.HTTPError as e:
            raise AppError("PROVIDER_NETWORK_ERROR",
                           f"数据接口连接失败：{e.__class__.__name__}。请检查网络或接口地址。",
                           stage="provider_call", retryable=True, provider=self.name)
        if 300 <= resp.status_code < 400:
            raise AppError("PROVIDER_REDIRECT_BLOCKED",
                           "数据接口返回了重定向；为防止请求被转发到未授权地址，调用已停止。",
                           stage="provider_call", provider=self.name,
                           details={"http_status": resp.status_code})
        if resp.status_code != 200:
            raise classify_http(resp.status_code, self.name)
        try:
            body = resp.json()
        except ValueError:
            raise AppError("PROVIDER_PARSE_ERROR", "数据接口返回内容无法解析（非 JSON）",
                           stage="provider_call", provider=self.name)
        if ep.get("raw"):
            return {"data": body.get("data", body), "request_id": body.get("request_id")}
        if ep.get("list"):
            arr = json_path_first(body, ep["list"])
            data = [map_obj(it, ep.get("map")) for it in arr] if isinstance(arr, list) else []
            out = {"data": data}
            if ep.get("cursorPath"):
                out["cursor"] = json_path_first(body, ep["cursorPath"])
            if ep.get("hasMorePath"):
                out["hasMore"] = json_path_first(body, ep["hasMorePath"])
            return out
        return {"data": map_obj(body, ep.get("map") or {})}

    # ---- 统一能力接口（与 DemoDatasource 对齐） ----
    def resolve_account(self, link: str, platform: str | None = None) -> dict:
        self._require()
        plat = platform or detect_platform(link) or "douyin"
        if plat not in SUPPORTED_PLATFORMS:
            raise AppError("PLATFORM_UNSUPPORTED", f"暂不支持平台：{plat}",
                           stage="resolve_account", provider=self.name)
        # 抖音：短链跟随提取 sec_uid
        uid = link.strip()
        if plat == "douyin":
            uid = _resolve_douyin_uid(link) or uid
            if "/" in uid or not uid:
                raise AppError(
                    "RESOLVE_FAILED",
                    "未能从抖音主页链接中解析 sec_user_id。请粘贴完整主页链接，"
                    "或直接输入 sec_user_id 后重试。",
                    stage="resolve_account", provider=self.name,
                    details={"platform": plat},
                )
        elif plat == "xhs":
            uid = _resolve_xhs_uid(link) or uid
            if "/" in uid:
                resolved = self.call_endpoint(
                    "resolveAccount", {"uid": uid, "platform": plat, "link": uid}
                ).get("data") or {}
                uid = _text_value(resolved.get("uid"))
            if "/" in uid or not uid:
                raise AppError(
                    "RESOLVE_FAILED",
                    "未能从小红书主页链接中解析 user_id。请粘贴完整的用户主页链接，"
                    "或直接输入 user_id 后重试。",
                    stage="resolve_account", provider=self.name,
                    details={"platform": plat},
                )
        elif plat == "bilibili":
            uid = _extract_uid(link, (r"space\.bilibili\.com/(\d+)", r"^\s*(\d+)\s*$")) or ""
        elif plat == "kuaishou":
            uid = _extract_uid(
                link,
                (r"/profile/([A-Za-z0-9_-]+)", r"/user/([A-Za-z0-9_-]+)",
                 r"^\s*((?=[A-Za-z0-9_-]*[A-Za-z])[A-Za-z0-9_-]+)\s*$"),
            ) or ""
            if not uid:
                resolved = self.call_endpoint(
                    "resolveAccount", {"uid": link.strip(), "platform": plat, "link": link.strip()}
                ).get("data") or {}
                uid = _text_value(resolved.get("uid"))
        elif plat == "weibo":
            uid = _extract_uid(link, (r"weibo\.(?:com|cn)/u/(\d+)",
                                      r"weibo\.(?:com|cn)/(\d+)", r"^\s*(\d+)\s*$")) or ""
        elif plat == "channels":
            raw = link.strip()
            if "@finder" in raw:
                uid = raw
            elif raw.lower().startswith("sph"):
                resolved = self.call_endpoint(
                    "resolveAccount", {"uid": raw, "platform": plat, "link": raw}
                ).get("data") or {}
                uid = _text_value(resolved.get("uid"))
            else:
                uid = ""
        elif plat == "tiktok":
            raw = link.strip()
            if raw.startswith("MS4"):
                uid = raw
            else:
                username = _extract_uid(raw, (r"tiktok\.com/@([A-Za-z0-9._-]+)",
                                              r"^@?([A-Za-z0-9._-]+)$")) or ""
                resolved = self.call_endpoint(
                    "resolveAccount", {"uid": username, "platform": plat, "link": raw}
                ).get("data") or {}
                uid = _text_value(resolved.get("uid"))
        if not uid:
            meta = PLATFORM_REGISTRY[plat]
            raise AppError(
                "RESOLVE_FAILED", f"未能识别{meta['label']}账号。请输入{meta['id_label']}。",
                stage="resolve_account", provider=self.name, details={"platform": plat},
            )
        return {"uid": uid, "platform": plat, "name": "", "link": link.strip()}

    def account_overview(self, uid: str, platform: str) -> dict:
        r = self.call_endpoint("accountOverview", {"uid": uid, "platform": platform})
        d = r["data"]
        return {"uid": uid, "platform": platform, "name": _text_value(d.get("name")),
                "avatar": _text_value(d.get("avatar")), "bio": _text_value(d.get("bio")),
                "fans": _int_value(d.get("fans")), "likes": _int_value(d.get("likes")),
                "video_count": _int_value(d.get("video_count")),
                "availability": {
                    "fans": d.get("fans") not in (None, ""),
                    "likes": d.get("likes") not in (None, ""),
                    "video_count": d.get("video_count") not in (None, ""),
                }}

    def account_videos(self, uid: str, platform: str, limit: int = 30,
                       cursor: str | None = None) -> dict:
        target = min(max(int(limit), 1), 500)
        endpoint = self._pick_ep("accountVideos", platform) or {}
        cursor = str(cursor if cursor is not None else
                     endpoint.get("startCursor", "0" if platform == "douyin" else ""))
        page_size = max(1, int(endpoint.get("pageSize", 20)))
        max_pages = min(50, max(2, (target + page_size - 1) // page_size + 1))
        raw_works: list[dict] = []
        seen: set[str] = set()
        has_more = True
        pages = 0
        while len(raw_works) < target and has_more and pages < max_pages:
            page_limit = min(page_size, target - len(raw_works))
            r = self.call_endpoint(
                "accountVideos",
                {"uid": uid, "platform": platform, "limit": page_limit,
                 "cursor": cursor},
            )
            page = r.get("data") or []
            for item in page:
                video_id = str(item.get("video_id") or "")
                if video_id and video_id not in seen:
                    raw_works.append(item)
                    seen.add(video_id)
            pages += 1
            explicit_has_more = r.get("hasMore")
            next_cursor = r.get("cursor")
            if (next_cursor is None or next_cursor == "") and page:
                next_cursor = page[-1].get("cursor")
            if endpoint.get("cursorStep") and page:
                next_cursor = str(int(cursor or 0) + int(endpoint["cursorStep"]))
            stop_values = {str(v) for v in endpoint.get("cursorStopValues", [])}
            if explicit_has_more is None:
                has_more = bool(page) and len(page) >= min(page_size, page_limit)
            else:
                has_more = bool(explicit_has_more)
            if str(next_cursor) in stop_values:
                has_more = False
            if not has_more or next_cursor in (None, "", cursor):
                break
            cursor = str(next_cursor)
        works = [{"video_id": _text_value(w.get("video_id")),
                  "title": _text_value(w.get("title")),
                  "cover": _text_value(w.get("cover")) + _text_value(w.get("cover_token")),
                  "url": _text_value(w.get("url")), "create_time": _int_value(w.get("create_time")),
                  "plays": _int_value(w.get("plays")), "likes": _int_value(w.get("likes")),
                  "comments": _int_value(w.get("comments")), "collects": _int_value(w.get("collects")),
                  "shares": _int_value(w.get("shares")), "duration": _int_value(w.get("duration"))}
                 for w in raw_works[:target]]
        works.sort(key=lambda item: (int(item.get("create_time") or 0),
                                     str(item.get("video_id") or "")), reverse=True)
        return {"works": works, "cursor": cursor, "has_more": has_more,
                "pages": pages,
                "newest_create_time": max((int(item.get("create_time") or 0)
                                            for item in works), default=0)}

    def video_text(self, video_id: str, platform: str, title: str = "") -> dict:
        r = self.call_endpoint("videoText", {"videoId": video_id, "platform": platform})
        text = (r["data"] or {}).get("text") or ""
        if not text:
            raise AppError("CAPTION_NOT_AVAILABLE",
                           "该内容无可用原生字幕/文案。可选择下载媒体并 ASR（需明确授权）。",
                           stage="fetch_caption", provider=self.name,
                           details={"video_id": video_id})
        return {"text": text, "cost": 0.10}

    def video_media(self, video_id: str, platform: str) -> dict:
        """Resolve and download original public media after explicit user approval."""
        args = {"videoId": video_id, "platform": platform}
        ep = self._pick_ep("videoMedia", platform)
        resolved = self.call_endpoint("videoMedia", args).get("data") or {}
        candidates = []

        def add_candidate(payload: dict) -> None:
            url = _text_value((payload or {}).get("url"))
            if url and url not in candidates:
                candidates.append(url)

        add_candidate(resolved)
        if not candidates:
            raise AppError(
                "MEDIA_URL_NOT_AVAILABLE", "平台未返回可下载的公开媒体地址。",
                stage="resolve_media", provider=self.name,
                details={"video_id": video_id, "platform": platform},
            )
        limit = 100 * 1024 * 1024
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/126.0.0.0 Safari/537.36"
            ),
            "Referer": "https://www.douyin.com/",
            "Accept": "video/avc,video/mp4,video/*;q=0.9,*/*;q=0.8",
            "Range": "bytes=0-",
        }

        def download(url: str) -> tuple[bytes, str]:
            _validate_media_url(url, resolve_dns=True)
            with httpx.Client(timeout=90.0, follow_redirects=False) as client:
                current_url = url
                for _ in range(6):
                    _validate_media_url(current_url, resolve_dns=True)
                    request = client.build_request("GET", current_url, headers=headers)
                    response = client.send(request, stream=True)
                    if 300 <= response.status_code < 400 and response.headers.get("location"):
                        current_url = urllib.parse.urljoin(current_url, response.headers["location"])
                        response.close()
                        continue
                    break
                else:
                    raise AppError(
                        "MEDIA_REDIRECT_LOOP", "公开媒体下载重定向次数过多。",
                        stage="download_media", provider=self.name,
                    )
                try:
                    if response.status_code not in (200, 206):
                        raise AppError(
                            "MEDIA_DOWNLOAD_FAILED",
                            f"公开媒体下载失败（HTTP {response.status_code}）。",
                            stage="download_media", provider=self.name,
                            retryable=response.status_code in (403, 408, 425, 429)
                            or response.status_code >= 500,
                            details={"http_status": response.status_code,
                                     "cdn_host": urllib.parse.urlparse(current_url).hostname or ""},
                        )
                    content_length = int(response.headers.get("content-length") or 0)
                    if content_length > limit:
                        raise AppError(
                            "MEDIA_TOO_LARGE", "公开媒体超过 100 MB，已停止下载。",
                            stage="download_media", provider=self.name,
                            details={"limit_bytes": limit, "content_length": content_length},
                        )
                    chunks = bytearray()
                    for chunk in response.iter_bytes():
                        chunks.extend(chunk)
                        if len(chunks) > limit:
                            raise AppError(
                                "MEDIA_TOO_LARGE", "公开媒体超过 100 MB，已停止下载。",
                                stage="download_media", provider=self.name,
                                details={"limit_bytes": limit},
                            )
                    mime = response.headers.get("content-type", "video/mp4").split(";", 1)[0]
                finally:
                    response.close()
            return bytes(chunks), mime

        last_error = None
        attempted_fallback = False
        while candidates:
            media_url = candidates.pop(0)
            try:
                media_bytes, mime = download(media_url)
                return {"bytes": media_bytes, "mime_type": mime or "video/mp4",
                        "url": media_url, "cost": 0.005}
            except AppError as exc:
                last_error = exc
                fallback = (ep or {}).get("fallback")
                if fallback and not attempted_fallback:
                    attempted_fallback = True
                    fallback_data = self._call_endpoint_config(fallback, args).get("data") or {}
                    add_candidate(fallback_data)
                continue
            except httpx.HTTPError as exc:
                last_error = AppError(
                    "MEDIA_DOWNLOAD_FAILED", f"公开媒体下载失败：{exc.__class__.__name__}。",
                    stage="download_media", provider=self.name, retryable=True,
                )
                continue
        if last_error:
            raise last_error
        raise AppError(
            "MEDIA_URL_NOT_AVAILABLE", "平台未返回可下载的公开媒体地址。",
            stage="resolve_media", provider=self.name,
            details={"video_id": video_id, "platform": platform},
        )

    def video_comments(self, video_id: str, platform: str, limit: int = 200) -> dict:
        r = self.call_endpoint("videoComments", {"videoId": video_id,
                                                 "platform": platform, "limit": limit})
        return {"comments": r["data"], "cost": 0.08}

    def work_share_url(self, video_id: str, platform: str) -> dict:
        """Resolve a share URL only after an explicit user action."""
        direct = canonical_platform_work_url(platform, video_id)
        if direct:
            return {"url": direct, "cost": 0.0, "provider": self.name}
        result = self.call_endpoint(
            "workShareUrl", {"videoId": video_id, "platform": platform}
        ).get("data") or {}
        url = _text_value(result.get("url"))
        if not url.startswith("https://"):
            raise AppError(
                "SHARE_URL_NOT_AVAILABLE", "平台没有返回可公开访问的作品链接。",
                stage="work_share_url", provider=self.name, retryable=True,
                details={"platform": platform, "video_id": video_id},
            )
        return {"url": url, "cost": 0.01 if platform == "channels" else 0.0,
                "provider": self.name}

    def search_content(self, keyword: str, platform: str = "douyin",
                       limit: int = 20) -> dict:
        """Search a configured platform without manufacturing missing fields."""
        if platform not in {"douyin", "xhs", "tiktok"}:
            raise AppError(
                "KEYWORD_PLATFORM_UNSUPPORTED",
                f"{PLATFORM_LABELS.get(platform, platform)} 尚未配置关键词搜索接口。",
                stage="keyword_search", provider=self.name, retryable=False,
            )
        raw = self.call_endpoint("searchContent", {
            "platform": platform, "keyword": keyword,
            "limit": max(1, min(int(limit), 50)),
        }).get("data")
        candidates = _best_topic_list(raw)
        works = []
        for candidate in candidates:
            normalized = _normalize_search_item(candidate, platform)
            if normalized["title"]:
                works.append(normalized)
            if len(works) >= max(1, min(int(limit), 50)):
                break
        if not works:
            raise AppError(
                "PROVIDER_EMPTY_RESULT",
                "关键词接口已返回，但没有识别到作品；请检查关键词或更新平台字段映射。",
                stage="keyword_search", provider=self.name, retryable=True,
            )
        return {"works": works, "provider": self.name}

    def platform_hot(self, platform: str, limit: int = 20) -> dict:
        """Fetch and normalize a platform hot board without fabricating fields."""
        if platform not in {"douyin", "xhs", "bilibili", "kuaishou", "weibo", "zhihu"}:
            raise AppError("PLATFORM_UNSUPPORTED", f"该平台尚未配置热点榜：{platform}",
                           stage="platform_hot", provider=self.name)
        raw = self.call_endpoint("platformHot", {
            "platform": platform, "limit": max(1, min(int(limit), 50))
        }).get("data")
        candidates = _best_topic_list(raw)
        items = []
        for idx, item in enumerate(candidates[:max(1, min(int(limit), 50))]):
            normalized = _normalize_hot_item(item, platform, idx + 1)
            if normalized["title"]:
                items.append(normalized)
        if not items:
            raise AppError("PROVIDER_EMPTY_RESULT",
                           f"{platform} 热点接口返回了数据，但未识别到热搜条目；请更新字段映射。",
                           stage="platform_hot", provider=self.name, retryable=True)
        return {"items": items, "provider": self.name}


def _best_topic_list(value) -> list:
    """Find the most topic-like list inside provider-specific nested JSON."""
    lists: list[list] = []
    def walk(node):
        if isinstance(node, list):
            if node and any(isinstance(x, (dict, str)) for x in node):
                lists.append(node)
            for child in node[:100]:
                walk(child)
        elif isinstance(node, dict):
            for child in node.values():
                walk(child)
    walk(value)
    title_keys = {"word", "sentence", "keyword", "query", "title", "name",
                  "desc", "show_name", "hot_word", "note"}
    def score(items):
        total = 0
        for item in items[:50]:
            if isinstance(item, str) and item.strip():
                total += 2
            elif isinstance(item, dict):
                total += sum(1 for key in title_keys if item.get(key) not in (None, "")) * 3
                total += 1
        return total
    return max(lists, key=score, default=[])


def _normalize_hot_item(item, platform: str, rank: int) -> dict:
    if isinstance(item, str):
        item = {"title": item}
    if not isinstance(item, dict):
        item = {}
    def first(keys, default=""):
        for key in keys:
            value = item.get(key)
            if value not in (None, "", []):
                return value
        return default
    title = _text_value(first(("word", "sentence", "keyword", "query", "title",
                               "name", "desc", "show_name", "hot_word", "note")))
    heat = _int_value(first(("hot_value", "hot_score", "score", "heat",
                             "view_count", "popularity", "hotness"), 0))
    actual_rank = _int_value(first(("rank", "position", "index"), rank)) or rank
    url = _text_value(first(("url", "share_url", "link", "jump_url", "schema")))
    if not url and title:
        encoded = urllib.parse.quote(title)
        url = {
            "douyin": f"https://www.douyin.com/search/{encoded}",
            "xhs": f"https://www.xiaohongshu.com/search_result?keyword={encoded}",
            "bilibili": f"https://search.bilibili.com/all?keyword={encoded}",
            "kuaishou": f"https://www.kuaishou.com/search/video?searchKey={encoded}",
            "weibo": f"https://s.weibo.com/weibo?q={encoded}",
            "zhihu": f"https://www.zhihu.com/search?q={encoded}",
        }.get(platform, "")
    stable = hashlib.sha256(f"{platform}\0{title}".encode("utf-8")).hexdigest()[:20]
    return {"id": stable, "title": title, "rank": actual_rank, "heat": heat,
            "url": url, "raw": item}


def canonical_platform_work_url(platform: str, video_id: str) -> str:
    if not video_id:
        return ""
    return {
        "douyin": f"https://www.douyin.com/video/{video_id}",
        "xhs": f"https://www.xiaohongshu.com/explore/{video_id}",
        "bilibili": f"https://www.bilibili.com/video/{video_id}",
        "kuaishou": f"https://www.kuaishou.com/short-video/{video_id}",
    }.get(platform, "")


def _deep_first(value, keys: tuple[str, ...]):
    if isinstance(value, dict):
        for key in keys:
            candidate = value.get(key)
            if candidate not in (None, "", []):
                return candidate
        for child in value.values():
            candidate = _deep_first(child, keys)
            if candidate not in (None, "", []):
                return candidate
    elif isinstance(value, list):
        for child in value[:20]:
            candidate = _deep_first(child, keys)
            if candidate not in (None, "", []):
                return candidate
    return None


def _normalize_search_item(item, platform: str) -> dict:
    if not isinstance(item, dict):
        return {"video_id": "", "title": _text_value(item), "cover": "", "url": "",
                "platform": platform, "plays": 0, "likes": 0, "comments": 0,
                "collects": 0, "shares": 0, "create_time": 0}
    video_id = _text_value(_deep_first(item, (
        "aweme_id", "note_id", "noteId", "video_id", "object_id", "id")))
    title = _text_value(_deep_first(item, (
        "desc", "title", "display_title", "note_title", "content")))
    cover = _text_value(_deep_first(item, (
        "cover", "cover_url", "coverUrl", "image", "image_url", "thumbnail")))
    url = _text_value(_deep_first(item, ("share_url", "url", "note_url", "web_url")))
    return {
        "video_id": video_id, "title": title, "cover": cover,
        "url": url or canonical_platform_work_url(platform, video_id),
        "platform": platform,
        "plays": _int_value(_deep_first(item, ("play_count", "view_count", "views"))),
        "likes": _int_value(_deep_first(item, ("digg_count", "like_count", "likes"))),
        "comments": _int_value(_deep_first(item, ("comment_count", "comments"))),
        "collects": _int_value(_deep_first(item, ("collect_count", "collected_count", "collects"))),
        "shares": _int_value(_deep_first(item, ("share_count", "shares"))),
        "create_time": _int_value(_deep_first(item, ("create_time", "time", "published_at"))),
    }


def _subst(s: str, args: dict) -> str:
    out = str(s)
    for k, v in args.items():
        out = out.replace("{" + k + "}", urllib.parse.quote(str(v), safe=""))
    return out


def _subst_raw(s: str, args: dict) -> str:
    out = str(s)
    for k, v in args.items():
        out = out.replace("{" + k + "}", str(v))
    return out


def _extract_uid(value: str, patterns: tuple[str, ...]) -> str | None:
    import re
    for pattern in patterns:
        match = re.search(pattern, value or "", re.IGNORECASE)
        if match:
            return match.group(1)
    return None


def _resolve_douyin_uid(link: str) -> str | None:
    import re
    m = re.search(r"/user/([A-Za-z0-9_\-]+)", link) or re.search(r"sec_uid=([A-Za-z0-9_\-]+)", link)
    if m:
        return m.group(1)
    if any(k in link for k in ("v.douyin.com", "dy.tt")):
        try:
            with httpx.Client(timeout=12.0, follow_redirects=False) as client:
                cur, hops = link, 0
                while hops < 6:
                    _validate_social_redirect(cur)
                    r = client.get(cur)
                    loc = r.headers.get("location")
                    if 300 <= r.status_code < 400 and loc:
                        cur = urllib.parse.urljoin(cur, loc)
                        hops += 1
                        m = re.search(r"/user/([A-Za-z0-9_\-]+)", cur)
                        if m:
                            return m.group(1)
                        continue
                    break
        except httpx.HTTPError:
                return None
    return None


def _resolve_xhs_uid(link: str) -> str | None:
    import re
    value = (link or "").strip()
    m = (re.search(r"/user/profile/([A-Za-z0-9_-]+)", value)
         or re.search(r"[?&]user_id=([A-Za-z0-9_-]+)", value))
    if m:
        return m.group(1)
    if value and "/" not in value and " " not in value:
        return value
    if "xhslink.com" in value:
        try:
            with httpx.Client(timeout=12.0, follow_redirects=False) as client:
                cur = value
                for _ in range(6):
                    _validate_xhs_redirect(cur)
                    response = client.get(cur)
                    location = response.headers.get("location")
                    if 300 <= response.status_code < 400 and location:
                        cur = urllib.parse.urljoin(cur, location)
                        m = re.search(r"/user/profile/([A-Za-z0-9_-]+)", cur)
                        if m:
                            return m.group(1)
                        continue
                    break
        except httpx.HTTPError:
            return None
    return None


def _allowed_provider_hosts() -> set[str]:
    configured = {h.strip().lower() for h in
                  os.environ.get("JIANZHOU_PROVIDER_HOSTS", "").split(",") if h.strip()}
    return {"api.tikhub.io", "api.tikhub.dev", *configured}


def _validate_ip(address: str) -> None:
    ip = ipaddress.ip_address(address)
    if (ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved
            or ip.is_multicast or ip.is_unspecified):
        raise AppError("PROVIDER_URL_BLOCKED", "数据源地址解析到了本机或私有网络，调用已阻止。",
                       stage="provider_config", details={"address": address})


def _validate_provider_url(url: str, resolve_dns: bool = False) -> None:
    parsed = urllib.parse.urlparse(url)
    host = (parsed.hostname or "").lower()
    if parsed.scheme != "https" or not host or parsed.username or parsed.password:
        raise AppError("PROVIDER_URL_BLOCKED", "数据源必须使用不含用户名密码的 HTTPS 地址。",
                       stage="provider_config")
    if host not in _allowed_provider_hosts():
        raise AppError("PROVIDER_URL_BLOCKED",
                       "该数据源域名不在允许列表。可通过 JIANZHOU_PROVIDER_HOSTS 显式添加可信域名。",
                       stage="provider_config", details={"host": host})
    try:
        _validate_ip(host)
    except ValueError:
        pass
    if resolve_dns:
        try:
            addresses = {item[4][0] for item in socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)}
        except socket.gaierror as exc:
            raise AppError("PROVIDER_NETWORK_ERROR", "数据源域名无法解析。",
                           stage="provider_call", retryable=True,
                           details={"reason": exc.__class__.__name__})
        for address in addresses:
            _validate_ip(address)


def _validate_media_url(url: str, resolve_dns: bool = False) -> None:
    """Allow only known public ByteDance/Douyin media CDNs returned by TikHub."""
    parsed = urllib.parse.urlparse(url)
    host = (parsed.hostname or "").lower()
    allowed_suffixes = (
        "zjcdn.com", "douyinvod.com", "douyinpic.com", "byteimg.com",
        "bytedance.com", "snssdk.com", "amemv.com",
    )
    if (parsed.scheme != "https" or not host or parsed.username or parsed.password
            or not any(host == suffix or host.endswith("." + suffix)
                       for suffix in allowed_suffixes)):
        raise AppError(
            "MEDIA_URL_BLOCKED", "公开媒体地址不属于受信任的平台 CDN，已停止下载。",
            stage="resolve_media", details={"host": host},
        )
    if resolve_dns:
        try:
            addresses = {item[4][0] for item in socket.getaddrinfo(
                host, 443, type=socket.SOCK_STREAM
            )}
        except socket.gaierror as exc:
            raise AppError(
                "MEDIA_DOWNLOAD_FAILED", "公开媒体域名无法解析。",
                stage="download_media", retryable=True,
                details={"reason": exc.__class__.__name__},
            )
        for address in addresses:
            _validate_ip(address)


def _validate_social_redirect(url: str) -> None:
    parsed = urllib.parse.urlparse(url)
    host = (parsed.hostname or "").lower()
    allowed = ("douyin.com", "iesdouyin.com", "dy.tt")
    if parsed.scheme != "https" or not any(host == d or host.endswith("." + d) for d in allowed):
        raise AppError("SOCIAL_REDIRECT_BLOCKED", "短链跳转到了非抖音域名，解析已停止。",
                       stage="resolve_account", details={"host": host})


def _validate_xhs_redirect(url: str) -> None:
    parsed = urllib.parse.urlparse(url)
    host = (parsed.hostname or "").lower()
    allowed = ("xiaohongshu.com", "xhslink.com")
    if parsed.scheme != "https" or not any(host == d or host.endswith("." + d) for d in allowed):
        raise AppError("SOCIAL_REDIRECT_BLOCKED", "短链跳转到了非小红书域名，解析已停止。",
                       stage="resolve_account", details={"host": host})


# ================= 统一入口 =================

def get_datasource():
    """演示模式 → DemoDatasource；否则按 settings.datasource 配置的真实执行器。"""
    if config.DEMO_MODE:
        return DemoDatasource()
    return EndpointExecutor()


def datasource_status() -> dict:
    if config.DEMO_MODE:
        return {"mode": "demo", "configured": True, "provider": "demo_ds",
                "platforms": platform_catalog(), "max_scan_count": 500}
    ex = EndpointExecutor()
    return {"mode": "real", "configured": ex.is_configured(),
            "provider": ex.active, "platforms": platform_catalog(),
            "max_scan_count": 500}
