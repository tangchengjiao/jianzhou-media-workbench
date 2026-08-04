"""全局配置：路径、演示模式、常量。

演示模式（DEMO_MODE）是显式状态，不是静默兜底：
- 默认开启，UI 全局展示「演示数据」徽标；
- 关闭后未配置真实 Key 的适配器将立即 fail-fast（CONFIG_MISSING_API_KEY）。
"""
import os
import sys
from pathlib import Path

PACKAGE_DIR = Path(__file__).resolve().parent
RESOURCE_DIR = Path(getattr(sys, "_MEIPASS", PACKAGE_DIR.parent)).resolve()
BASE_DIR = RESOURCE_DIR


def _default_data_dir() -> Path:
    override = os.environ.get("JIANZHOU_DATA_DIR", "").strip()
    if override:
        return Path(override).expanduser().resolve()
    if getattr(sys, "frozen", False):
        local_app_data = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA")
        if local_app_data:
            return (Path(local_app_data) / "JianzhouMediaWorkbench").resolve()
        return (Path.home() / ".jianzhou-media-workbench").resolve()
    return (RESOURCE_DIR / "data").resolve()


DATA_DIR = _default_data_dir()
DB_PATH = DATA_DIR / "app.db"
BACKUP_DIR = DATA_DIR / "backups"
LOG_DIR = DATA_DIR / "logs"
UPLOAD_DIR = DATA_DIR / "uploads"
FRONTEND_DIR = RESOURCE_DIR / "frontend"

DEMO_MODE = os.environ.get("JIANZHOU_DEMO", "1") == "1"
MAX_IMPORT_UPLOAD_BYTES = int(os.environ.get("JIANZHOU_MAX_IMPORT_BYTES", str(10 * 1024 * 1024)))
MAX_AUDIO_UPLOAD_BYTES = int(os.environ.get("JIANZHOU_MAX_AUDIO_BYTES", str(25 * 1024 * 1024)))

APP_NAME = "见舟 · 自媒体运营工作台"
APP_VERSION = "2.1.0"

# 成本预算默认值（可在系统设置中修改）
DEFAULT_SETTINGS = {
    "source": {
        "tikhub_api_key": "",
        "sync_frequency": "每 6 小时 · 关键词每日 · 快照 T+1/3/7",
        "daily_request_limit": 500,
        "cache_policy": "元数据 24h · 媒体 30 天 · ASR 永久",
    },
    "voice": {
        "stt_provider": "本地 faster-whisper（small · CPU）",
        "tts_provider": "系统语音",
        "interview_mode": "按键说话（推荐 · 可审计）",
        "language": "zh",
        "hot_words": "TikHub, 归因翻转, 林小舟, 向上管理",
    },
    "llm": {
        "fast_model": "gpt-4o-mini",
        "analysis_model": "claude-sonnet-4",
        "writing_model": "claude-sonnet-4",
        "structured_model": "gpt-4o-mini",
        "embedding_model": "本地 bge-m3",
        "api_base": "",
        "api_key": "",
    },
    "cost": {
        "daily_budget": 20.0,
        "monthly_budget": 300.0,
        "confirm_threshold": 1.0,
        "max_media_minutes": 10,
        "max_comments": 1000,
        "cache_days": 30,
    },
    "local": {
        "auto_backup": "每日 03:00 · 保留 7 份",
    },
    "appearance": {
        "theme": "light",
    },
    # 数据源：配置驱动的 endpoint 映射（借鉴参考项目）。换供应商零代码，只改这里。
    "datasource": {
        "active": "tikhub",
        "providers": {
            "tikhub": {
                "baseUrl": "https://api.tikhub.io",
                "auth": {"type": "header", "field": "Authorization", "value": ""},
                "rateLimit": {"rpm": 30},
                "endpoints": {
                    "accountOverview": {
                        "douyin": {"path": "/api/v1/douyin/web/fetch_user_info",
                                   "params": {"sec_uid": "{uid}"},
                                   "map": {"name": "$.data.nickname", "avatar": "$.data.avatar_larger.url_list[0]",
                                           "bio": "$.data.signature", "fans": "$.data.follower_count",
                                           "likes": "$.data.total_favorited", "video_count": "$.data.aweme_count"}},
                        "xhs": {"path": "/api/v1/xiaohongshu/web/get_user_info",
                                "params": {"share_text": "{uid}"},
                                "map": {"name": "$.data.nickname", "avatar": "$.data.avatar",
                                        "bio": "$.data.desc", "fans": "$.data.fans",
                                        "likes": "$.data.liked_count", "video_count": "$.data.notes_count"}},
                    },
                    "accountVideos": {
                        "douyin": {"path": "/api/v1/douyin/web/fetch_user_post_videos",
                                   "params": {"sec_uid": "{uid}", "cursor": "{cursor}", "count": "20"},
                                   "list": "$.data.aweme_list", "cursorPath": "$.data.cursor",
                                   "hasMorePath": "$.data.has_more",
                                   "map": {"video_id": "$.aweme_id", "title": "$.desc",
                                           "cover": "$.video.cover.url_list[0]", "url": "$.share_url",
                                           "create_time": "$.create_time", "plays": "$.statistics.play_count",
                                           "likes": "$.statistics.digg_count", "comments": "$.statistics.comment_count",
                                           "collects": "$.statistics.collect_count", "shares": "$.statistics.share_count",
                                           "duration": "$.duration"}},
                        "xhs": {"path": "/api/v1/xiaohongshu/web/get_user_posted_notes",
                                "params": {"share_text": "{uid}", "cursor": "{cursor}"},
                                "list": "$.data.notes", "cursorPath": "$.data.cursor",
                                "hasMorePath": "$.data.has_more",
                                "map": {"video_id": "$.note_id", "title": "$.title",
                                        "cover": "$.cover.url", "url": "$.url",
                                        "create_time": "$.time", "plays": "$.plays",
                                        "likes": "$.liked_count", "comments": "$.comment_count",
                                        "collects": "$.collected_count", "shares": "$.share_count",
                                        "duration": "$.duration"}},
                    },
                    "videoText": {
                        "douyin": {"path": "/api/v1/douyin/web/fetch_video_text",
                                   "params": {"aweme_id": "{videoId}"},
                                   "map": {"text": "$.data.text"}},
                    },
                    "videoComments": {
                        "douyin": {"path": "/api/v1/douyin/web/fetch_video_comments",
                                   "params": {"aweme_id": "{videoId}", "cursor": "0", "count": "50"},
                                   "list": "$.data.comments",
                                   "map": {"user": "$.user.nickname", "text": "$.text",
                                           "likes": "$.digg_count"}},
                    },
                },
            },
        },
        "_note": "endpoints 按供应商实际文档调整；此处为 TikHub 文档示例映射，未经真实 Key 验证",
    },
    "scheduler": {
        "enabled": True,
        "benchmark_patrol_time": "09:00",
        "snapshot_time": "21:00",
    },
}

# 内容项目状态机（PRD v2.1 R4）
CONTENT_STATES = [
    "CANDIDATE", "SELECTED", "OUTLINING", "DRAFTING", "PRODUCING",
    "READY_TO_PUBLISH", "PUBLISHED", "WAITING_RETRO",
    "RETRO_COMPLETED", "ARCHIVED",
]
CONTENT_STATE_LABELS = {
    "CANDIDATE": "候选", "SELECTED": "已选题", "OUTLINING": "提纲中",
    "DRAFTING": "写作中", "PRODUCING": "制作中",
    "READY_TO_PUBLISH": "待发布", "PUBLISHED": "已发布",
    "WAITING_RETRO": "待复盘", "RETRO_COMPLETED": "已完成",
    "ARCHIVED": "已归档", "PAUSED": "已暂停", "CANCELLED": "已取消",
    "BLOCKED": "阻塞",
}

JOB_STEPS_EXTRACTION = [
    "resolve_source", "check_cache", "fetch_native_text",
    "fetch_caption_or_asr", "validate_transcript", "persist_assets",
]
