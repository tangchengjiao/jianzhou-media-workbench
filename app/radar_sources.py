"""Configurable radar sources with persistent runs and signal deduplication."""
from __future__ import annotations

import datetime
import hashlib
import json
import time
import urllib.parse
import xml.etree.ElementTree as ET
from html.parser import HTMLParser

import httpx

from .datasource import PLATFORM_LABELS, get_datasource
from .db import execute, jdumps, jloads, query, query_one, transaction
from .errors import AppError, not_found
from .ids import new_id


_RADAR_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/126.0.0.0 Safari/537.36 JianzhouRadar/2.1"
    ),
    "Accept": "application/json, application/feed+json, application/rss+xml, "
              "application/atom+xml, text/xml;q=0.9, text/html;q=0.8, */*;q=0.5",
}


def _public_get(url: str, *, attempts: int = 3) -> httpx.Response:
    """Retry transient connect/TLS failures without weakening certificate checks."""
    last_error: httpx.HTTPError | None = None
    timeout = httpx.Timeout(45.0, connect=45.0)
    for attempt in range(1, max(1, attempts) + 1):
        try:
            return httpx.get(
                url, timeout=timeout, follow_redirects=False,
                headers=_RADAR_HEADERS,
            )
        except (httpx.ConnectTimeout, httpx.ConnectError,
                httpx.ReadTimeout, httpx.RemoteProtocolError) as exc:
            last_error = exc
            if attempt < attempts:
                time.sleep(0.35 * attempt)
    assert last_error is not None
    raise last_error


def now() -> str:
    return datetime.datetime.now().isoformat(timespec="seconds")


def create_source(user_id: str, kind: str, name: str, platform: str | None,
                  config: dict, frequency_hours: int) -> dict:
    source_id = new_id("RSRC")
    execute(
        "INSERT INTO radar_sources(id,user_id,kind,name,platform,config_json,enabled,status,"
        "frequency_hours,cursor,last_run_at,next_run_at,last_error,created_at) "
        "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (source_id, user_id, kind, name.strip(), platform, jdumps(config), 1, "idle",
         max(1, min(int(frequency_hours), 168)), None, None, None, None, now()),
    )
    return get_source(source_id, user_id)


def get_source(source_id: str, user_id: str) -> dict:
    row = query_one("SELECT * FROM radar_sources WHERE id=? AND user_id=?",
                    (source_id, user_id))
    if not row:
        raise not_found("雷达数据源", source_id)
    row["config"] = jloads(row.pop("config_json"), {})
    row["runs"] = query(
        "SELECT id,status,fetched,created,deduplicated,started_at,finished_at,error_json "
        "FROM radar_fetch_runs WHERE source_id=? ORDER BY started_at DESC LIMIT 8",
        (source_id,),
    )
    for run in row["runs"]:
        run["error"] = jloads(run.pop("error_json"), None)
    return row


def list_sources(user_id: str) -> list[dict]:
    return [get_source(row["id"], user_id) for row in query(
        "SELECT id FROM radar_sources WHERE user_id=? ORDER BY created_at DESC", (user_id,))]


def sync_source(source_id: str, user_id: str) -> dict:
    source = get_source(source_id, user_id)
    if source["kind"] == "external_feed" and _is_aihot_url(
            str(source.get("config", {}).get("url") or "")):
        original_url = str(source["config"].get("url") or "")
        source["config"] = {
            **source["config"],
            "requested_url": source["config"].get("requested_url") or original_url,
            "url": "https://aihot.virxact.com/api/v1/items?mode=selected&window=24h&limit=50",
            "connector": "aihot",
        }
        execute("UPDATE radar_sources SET config_json=? WHERE id=?",
                (jdumps(source["config"]), source_id))
    run_id = new_id("RFR")
    execute(
        "INSERT INTO radar_fetch_runs(id,user_id,source_id,status,fetched,created,deduplicated,"
        "cursor_in,cursor_out,error_json,started_at,finished_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
        (run_id, user_id, source_id, "RUNNING", 0, 0, 0, source.get("cursor"),
         None, None, now(), None),
    )
    execute("UPDATE radar_sources SET status='syncing',last_error=NULL WHERE id=?", (source_id,))
    try:
        if source["kind"] == "tikhub_hot":
            ds = get_datasource()
            response = ds.platform_hot(source.get("platform") or "douyin",
                                       int(source["config"].get("limit") or 20))
            items, provider = response["items"], response.get("provider", ds.name)
        elif source["kind"] == "external_feed":
            items = _fetch_external_feed(source["config"])
            provider = str(source["config"].get("connector") or "external_feed")
        elif source["kind"] == "keyword_watch":
            ds = get_datasource()
            items = []
            for keyword in source["config"].get("keywords", []):
                result = ds.search_content(
                    str(keyword), source.get("platform") or "douyin",
                    int(source["config"].get("limit") or 20),
                )
                for work in result.get("works") or []:
                    items.append({**work, "keyword": keyword,
                                  "id": work.get("video_id") or work.get("url")})
            provider = getattr(ds, "name", "datasource")
        else:
            raise AppError("RADAR_SOURCE_UNSUPPORTED", f"不支持的数据源类型：{source['kind']}",
                           stage="radar_source", retryable=False)
        created = _persist_items(source, items, provider)
        next_run = (datetime.datetime.now() + datetime.timedelta(
            hours=int(source["frequency_hours"])
        )).isoformat(timespec="seconds")
        execute("UPDATE radar_sources SET status='ready',last_run_at=?,next_run_at=?,"
                "last_error=NULL,failure_count=0,last_success_at=? WHERE id=?",
                (now(), next_run, now(), source_id))
        execute("UPDATE radar_fetch_runs SET status='SUCCEEDED',fetched=?,created=?,"
                "deduplicated=?,finished_at=? WHERE id=?",
                (len(items), created, max(0, len(items) - created), now(), run_id))
        return {"source_id": source_id, "run_id": run_id, "fetched": len(items),
                "created": created, "deduplicated": max(0, len(items) - created)}
    except Exception as exc:
        error = exc.to_dict() if isinstance(exc, AppError) else {
            "code": "RADAR_SYNC_FAILED", "message": str(exc), "retryable": True}
        failures = int(source.get("failure_count") or 0) + 1
        backoff_hours = min(168, 2 ** min(failures - 1, 7))
        retry_at = (datetime.datetime.now() + datetime.timedelta(hours=backoff_hours)).isoformat(
            timespec="seconds")
        execute("UPDATE radar_sources SET status='error',last_run_at=?,last_error=?,"
                "failure_count=?,next_run_at=? WHERE id=?",
                (now(), error["message"], failures, retry_at, source_id))
        execute("UPDATE radar_fetch_runs SET status='FAILED',error_json=?,finished_at=? WHERE id=?",
                (jdumps(error), now(), run_id))
        raise


def _persist_items(source: dict, items: list[dict], provider: str) -> int:
    created = 0
    profile = query_one("SELECT content_json FROM profile_versions WHERE user_id=? AND active=1",
                        (source["user_id"],))
    profile_text = (profile.get("content_json") or "").lower() if profile else ""
    keywords = [str(x).strip().lower() for x in source["config"].get("keywords", [])
                if str(x).strip()]
    for idx, item in enumerate(items):
        title = str(item.get("title") or "").strip()
        if not title:
            continue
        stable = hashlib.sha256(
            f"{source['id']}\0{item.get('id') or item.get('url') or title}".encode("utf-8")
        ).hexdigest()[:24]
        signal_id = f"SG-RAD-{stable}"
        if query_one("SELECT id FROM signals WHERE id=?", (signal_id,)):
            continue
        haystack = title.lower()
        matches = sum(1 for word in keywords if word in haystack or word in profile_text)
        relevance = min(95, 50 + matches * 15)
        platform = source.get("platform") or source["name"]
        rank = int(item.get("rank") or idx + 1)
        metrics = {"rank": rank, "heat": int(item.get("heat") or 0),
                   "source_id": source["id"]}
        with transaction():
            execute(
                "INSERT INTO signals(id,user_id,source_type,platform,author,title,url,published_at,"
                "metrics_json,rel_perf,relevance,timeliness,status,category,source,fetched_at,"
                "content_id,radar_source_id) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (signal_id, source["user_id"],
                 ("平台热榜" if source["kind"] == "tikhub_hot" else
                  "关键词监听" if source["kind"] == "keyword_watch" else "行业数据源"),
                 PLATFORM_LABELS.get(platform, platform), source["name"], title,
                 item.get("url", ""), item.get("published_at", ""), jdumps(metrics),
                 f"热榜 #{rank}" if rank else "", relevance, "刚刚同步", "new",
                 ("platform_hot" if source["kind"] == "tikhub_hot" else
                  "kw" if source["kind"] == "keyword_watch" else "external"),
                 provider, now(), None, source["id"]),
            )
        created += 1
    return created


def _fetch_external_feed(config: dict) -> list[dict]:
    url = str(config.get("url") or "").strip()
    if not url:
        raise AppError("RADAR_SOURCE_URL_MISSING", "外部 Feed URL 不能为空。",
                       stage="external_feed", retryable=False)
    from .providers import _validate_llm_url
    _validate_llm_url(url, allow_private=False)
    try:
        response = _public_get(url)
        if 300 <= response.status_code < 400:
            raise AppError(
                "RADAR_SOURCE_REDIRECT_BLOCKED",
                "外部 Feed 返回了重定向；请填写最终 HTTPS 地址。",
                stage="external_feed", retryable=False,
            )
        response.raise_for_status()
    except httpx.HTTPError as exc:
        raise AppError("RADAR_SOURCE_NETWORK_ERROR", f"外部 Feed 获取失败：{exc}",
                       stage="external_feed", retryable=True)
    content_type = response.headers.get("content-type", "").lower()
    connector = str(config.get("connector") or "")
    if connector == "aihot" or _is_aihot_url(url):
        if "html" in content_type:
            response = _public_get(
                "https://aihot.virxact.com/api/v1/items?mode=selected&window=24h&limit=50"
            )
            response.raise_for_status()
        return _aihot_items(response.json())
    if "json" in content_type or url.lower().endswith(".json"):
        return _json_feed_items(response.json(), config)
    if "html" in content_type:
        discovered = _discover_feed_url(response.text, url)
        if not discovered:
            raise AppError(
                "RADAR_SOURCE_NOT_STRUCTURED",
                "这个地址是普通网页，未发现 RSS、Atom 或可识别 API。请填写该网站的订阅/API 地址。",
                stage="external_feed", retryable=False,
            )
        discovered_config = {**config, "url": discovered}
        return _fetch_external_feed(discovered_config)
    return _rss_items(response.text)


def _json_feed_items(payload, config: dict | None = None) -> list[dict]:
    config = config or {}
    values = _dot_path(payload, str(config.get("items_path") or "items")) if isinstance(payload, dict) else payload
    if not isinstance(values, list):
        raise AppError("RADAR_SOURCE_PARSE_ERROR", "JSON Feed 未包含 items 数组。",
                       stage="external_feed", retryable=False)
    return [{"id": str(_dot_path(x, str(config.get("id_path") or "id")) or
                        _dot_path(x, str(config.get("url_path") or "url")) or
                        _dot_path(x, str(config.get("title_path") or "title")) or ""),
             "title": str(_dot_path(x, str(config.get("title_path") or "title")) or x.get("name") or ""),
             "url": str(_dot_path(x, str(config.get("url_path") or "url")) or x.get("external_url") or ""),
             "published_at": str(_dot_path(x, str(config.get("date_path") or "date_published")) or
                                 x.get("published_at") or ""),
             "rank": i + 1, "heat": int(x.get("heat") or 0)}
            for i, x in enumerate(values[:50]) if isinstance(x, dict)]


def _rss_items(text: str) -> list[dict]:
    try:
        root = ET.fromstring(text)
    except ET.ParseError as exc:
        raise AppError("RADAR_SOURCE_PARSE_ERROR", f"RSS/Atom 解析失败：{exc}",
                       stage="external_feed", retryable=False)
    items = []
    nodes = list(root.findall(".//item")) or list(root.findall(".//{*}entry"))
    for idx, node in enumerate(nodes[:50]):
        title = node.findtext("title") or node.findtext("{*}title") or ""
        link = node.findtext("link") or ""
        if not link:
            link_node = node.find("{*}link")
            link = link_node.get("href", "") if link_node is not None else ""
        published = (node.findtext("pubDate") or node.findtext("{*}published") or
                     node.findtext("{*}updated") or "")
        items.append({"id": link or title, "title": title, "url": link,
                      "published_at": published, "rank": idx + 1, "heat": 0})
    return items


class _FeedDiscoveryParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.hrefs: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag.lower() != "link":
            return
        values = {str(k).lower(): str(v or "") for k, v in attrs}
        rel = values.get("rel", "").lower()
        mime = values.get("type", "").lower()
        if "alternate" in rel and any(x in mime for x in ("rss", "atom", "json")):
            if values.get("href"):
                self.hrefs.append(values["href"])


def _discover_feed_url(html: str, base_url: str) -> str:
    parser = _FeedDiscoveryParser()
    parser.feed(html[:2_000_000])
    return urllib.parse.urljoin(base_url, parser.hrefs[0]) if parser.hrefs else ""


def _is_aihot_url(url: str) -> bool:
    return (urllib.parse.urlparse(url).hostname or "").lower() == "aihot.virxact.com"


def _aihot_items(payload: dict) -> list[dict]:
    values = payload.get("items") if isinstance(payload, dict) else []
    if not isinstance(values, list):
        raise AppError("RADAR_SOURCE_PARSE_ERROR", "AIHot API 未返回 items 数组。",
                       stage="external_feed", retryable=False)
    items = []
    for index, value in enumerate(values[:100]):
        if not isinstance(value, dict):
            continue
        links = value.get("links") or {}
        items.append({
            "id": str(value.get("id") or links.get("original") or value.get("title") or ""),
            "title": str(value.get("title") or value.get("originalTitle") or ""),
            "url": str(links.get("original") or links.get("aihot") or ""),
            "published_at": str(value.get("publishedAt") or value.get("discoveredAt") or ""),
            "rank": index + 1, "heat": int(value.get("score") or 0),
        })
    return items


def _dot_path(value, path: str):
    current = value
    for token in [part for part in path.replace("$.", "").split(".") if part]:
        if isinstance(current, dict):
            current = current.get(token)
        elif isinstance(current, list) and token.isdigit():
            index = int(token)
            current = current[index] if index < len(current) else None
        else:
            return None
    return current


def probe_source(url: str) -> dict:
    """Inspect a URL before persistence and return the resolved connector plus a real preview."""
    requested = str(url or "").strip()
    if not requested:
        raise AppError("RADAR_SOURCE_URL_MISSING", "请填写数据源地址。",
                       stage="source_probe", retryable=False)
    if _is_aihot_url(requested):
        resolved = "https://aihot.virxact.com/api/v1/items?mode=selected&window=24h&limit=50"
        items = _fetch_external_feed({"url": resolved, "connector": "aihot"})
        return {"connector": "aihot", "source_type": "AIHot REST API",
                "requested_url": requested, "resolved_url": resolved,
                "config": {"url": resolved, "connector": "aihot"},
                "sample_count": len(items), "sample": items[:3]}
    from .providers import _validate_llm_url
    _validate_llm_url(requested, allow_private=False)
    try:
        response = _public_get(requested)
        if 300 <= response.status_code < 400:
            raise AppError(
                "RADAR_SOURCE_REDIRECT_BLOCKED",
                "数据源返回了重定向，请填写最终 HTTPS 地址。",
                stage="source_probe", retryable=False,
            )
        response.raise_for_status()
    except AppError:
        raise
    except httpx.HTTPError as exc:
        raise AppError("RADAR_SOURCE_NETWORK_ERROR", f"数据源连接失败：{exc}",
                       stage="source_probe", retryable=True)
    content_type = response.headers.get("content-type", "").lower()
    resolved = requested
    connector = "json_feed" if "json" in content_type else "rss_atom"
    if "html" in content_type:
        resolved = _discover_feed_url(response.text, requested)
        if not resolved:
            raise AppError(
                "RADAR_SOURCE_NOT_STRUCTURED",
                "该网页未公开 RSS、Atom 或可识别 API，不能作为稳定自动数据源。",
                stage="source_probe", retryable=False,
            )
        connector = "discovered_feed"
    items = _fetch_external_feed({"url": resolved, "connector": connector})
    return {"connector": connector, "source_type": "公开结构化订阅",
            "requested_url": requested, "resolved_url": resolved,
            "config": {"url": resolved, "connector": connector},
            "sample_count": len(items), "sample": items[:3]}
