"""数据源适配层：配置驱动 endpoint 映射执行器（借鉴参考项目 datasource.js）。

能力：resolve_account / account_overview / account_videos / video_text /
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


def map_obj(src: dict, mapping: dict) -> dict:
    return {k: json_path(src, v) for k, v in (mapping or {}).items()}


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


# ================= 平台识别与链接解析 =================

def detect_platform(link: str) -> str | None:
    s = (link or "").lower()
    if any(k in s for k in ("xiaohongshu", "xhslink", "xhs")):
        return "xhs"
    if any(k in s for k in ("douyin", "v.douyin", "iesdouyin")):
        return "douyin"
    if any(k in s for k in ("bilibili", "b23.tv")):
        return "bilibili"
    if any(k in s for k in ("channels", "weixin", "sph")):
        return "channels"
    return None


PLATFORM_LABELS = {"douyin": "抖音", "xhs": "小红书", "bilibili": "B站",
                   "channels": "视频号", "gzh": "公众号"}


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
        }

    def account_videos(self, uid: str, platform: str, limit: int = 30) -> dict:
        h = self._rng(uid)
        base = time.time()
        works = []
        n = min(limit, 12 + h % 10)
        for i in range(n):
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
        return {"works": works, "cursor": "", "has_more": False}

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

    def search_content(self, keyword: str) -> dict:
        h = self._rng(keyword)
        return {"works": [{"video_id": f"kw{h%9999}_{i}",
                           "title": f"{keyword} 相关内容 {i+1}",
                           "likes": 5000 + h % 20000} for i in range(6)],
                "hot_trend": 30 + h % 60}


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
                headers[auth.get("field", "Authorization")] = auth["value"]
            else:
                qs.append(f"{auth.get('field', 'token')}={auth['value']}")
        if qs:
            url += ("&" if "?" in url else "?") + "&".join(qs)
        method = (ep.get("method") or "GET").upper()
        try:
            with httpx.Client(timeout=30.0, follow_redirects=False) as client:
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
        if ep.get("list"):
            arr = json_path(body, ep["list"])
            data = [map_obj(it, ep.get("map")) for it in arr] if isinstance(arr, list) else []
            out = {"data": data}
            if ep.get("cursorPath"):
                out["cursor"] = json_path(body, ep["cursorPath"])
            if ep.get("hasMorePath"):
                out["hasMore"] = json_path(body, ep["hasMorePath"])
            return out
        return {"data": map_obj(body, ep.get("map") or {})}

    # ---- 统一能力接口（与 DemoDatasource 对齐） ----
    def resolve_account(self, link: str, platform: str | None = None) -> dict:
        plat = platform or detect_platform(link) or "douyin"
        # 抖音：短链跟随提取 sec_uid
        uid = link.strip()
        if plat == "douyin":
            uid = _resolve_douyin_uid(link) or uid
        return {"uid": uid, "platform": plat, "name": "", "link": link.strip()}

    def account_overview(self, uid: str, platform: str) -> dict:
        r = self.call_endpoint("accountOverview", {"uid": uid, "platform": platform})
        d = r["data"]
        return {"uid": uid, "platform": platform, "name": d.get("name") or "",
                "avatar": d.get("avatar") or "", "bio": d.get("bio") or "",
                "fans": d.get("fans") or 0, "likes": d.get("likes") or 0,
                "video_count": d.get("video_count") or 0}

    def account_videos(self, uid: str, platform: str, limit: int = 30) -> dict:
        r = self.call_endpoint("accountVideos",
                               {"uid": uid, "platform": platform, "limit": limit,
                                "cursor": "0"})
        works = [{"video_id": str(w.get("video_id") or ""),
                  "title": w.get("title") or "", "cover": w.get("cover") or "",
                  "url": w.get("url") or "", "create_time": w.get("create_time") or 0,
                  "plays": w.get("plays") or 0, "likes": w.get("likes") or 0,
                  "comments": w.get("comments") or 0, "collects": w.get("collects") or 0,
                  "shares": w.get("shares") or 0, "duration": w.get("duration") or 0}
                 for w in r["data"]]
        return {"works": works, "cursor": r.get("cursor", ""),
                "has_more": bool(r.get("hasMore"))}

    def video_text(self, video_id: str, platform: str, title: str = "") -> dict:
        r = self.call_endpoint("videoText", {"videoId": video_id, "platform": platform})
        text = (r["data"] or {}).get("text") or ""
        if not text:
            raise AppError("CAPTION_NOT_AVAILABLE",
                           "该内容无可用原生字幕/文案。可选择下载媒体并 ASR（需明确授权）。",
                           stage="fetch_caption", provider=self.name,
                           details={"video_id": video_id})
        return {"text": text, "cost": 0.10}

    def video_comments(self, video_id: str, platform: str, limit: int = 200) -> dict:
        r = self.call_endpoint("videoComments", {"videoId": video_id,
                                                 "platform": platform, "limit": limit})
        return {"comments": r["data"], "cost": 0.08}


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


def _allowed_provider_hosts() -> set[str]:
    configured = {h.strip().lower() for h in
                  os.environ.get("JIANZHOU_PROVIDER_HOSTS", "").split(",") if h.strip()}
    return {"api.tikhub.io", *configured}


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


def _validate_social_redirect(url: str) -> None:
    parsed = urllib.parse.urlparse(url)
    host = (parsed.hostname or "").lower()
    allowed = ("douyin.com", "iesdouyin.com", "dy.tt")
    if parsed.scheme != "https" or not any(host == d or host.endswith("." + d) for d in allowed):
        raise AppError("SOCIAL_REDIRECT_BLOCKED", "短链跳转到了非抖音域名，解析已停止。",
                       stage="resolve_account", details={"host": host})


# ================= 统一入口 =================

def get_datasource():
    """演示模式 → DemoDatasource；否则按 settings.datasource 配置的真实执行器。"""
    if config.DEMO_MODE:
        return DemoDatasource()
    return EndpointExecutor()


def datasource_status() -> dict:
    if config.DEMO_MODE:
        return {"mode": "demo", "configured": True, "provider": "demo_ds"}
    ex = EndpointExecutor()
    return {"mode": "real", "configured": ex.is_configured(),
            "provider": ex.active}
