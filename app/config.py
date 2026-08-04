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
        "mappingVersion": 3,
        "active": "tikhub",
        "providers": {
            "tikhub": {
                "baseUrl": "https://api.tikhub.io",
                "auth": {"type": "header", "field": "Authorization", "value": ""},
                "rateLimit": {"rpm": 30},
                "endpoints": {
                    "resolveAccount": {
                        "xhs": {"path": "/api/v1/xiaohongshu/app_v2/get_user_info",
                                "params": {"share_text": "{link}"},
                                "map": {"uid": ["$.data.data.user_id", "$.data.data.id"]}},
                        "kuaishou": {"path": "/api/v1/kuaishou/web/fetch_get_user_id",
                                     "params": {"share_link": "{link}"},
                                     "map": {"uid": ["$.data.user_id", "$.data.data.user_id",
                                                     "$.data.data"]}},
                        "channels": {"path": "/api/v1/wechat_channels/v2/fetch_channel_id_to_username",
                                     "method": "POST", "timeout": 45,
                                     "bodyMap": {"channel_id": "{uid}", "raw": False},
                                     "map": {"uid": ["$.data.finder_username", "$.data.username"]}},
                        "tiktok": {"path": "/api/v1/tiktok/web/fetch_user_profile",
                                   "params": {"uniqueId": "{uid}"},
                                   "map": {"uid": ["$.data.userInfo.user.secUid",
                                                    "$.data.userInfo.user.sec_uid"]}},
                    },
                    "accountOverview": {
                        "douyin": {"path": "/api/v1/douyin/web/handler_user_profile",
                                   "params": {"sec_user_id": "{uid}"},
                                   "map": {"name": "$.data.user.nickname", "avatar": "$.data.user.avatar_larger.url_list[0]",
                                           "bio": "$.data.user.signature", "fans": "$.data.user.follower_count",
                                           "likes": "$.data.user.total_favorited", "video_count": "$.data.user.aweme_count"}},
                        "xhs": {"path": "/api/v1/xiaohongshu/app_v2/get_user_info",
                                "params": {"user_id": "{uid}"},
                                "map": {"name": "$.data.data.nickname", "avatar": "$.data.data.images",
                                        "bio": "$.data.data.desc", "fans": "$.data.data.fans",
                                        "likes": "$.data.data.liked", "video_count": "$.data.data.notes_count"}},
                        "bilibili": {"path": "/api/v1/bilibili/web/fetch_user_profile",
                                     "params": {"uid": "{uid}"},
                                     "map": {"name": ["$.data.data.name", "$.data.name"],
                                             "avatar": ["$.data.data.face", "$.data.face"],
                                             "bio": ["$.data.data.sign", "$.data.sign"],
                                             "fans": ["$.data.data.follower", "$.data.follower"],
                                             "likes": ["$.data.data.likes", "$.data.likes"],
                                             "video_count": ["$.data.data.archive_count", "$.data.archive_count"]}},
                        "kuaishou": {"path": "/api/v1/kuaishou/web/fetch_user_info",
                                     "params": {"user_id": "{uid}"}, "timeout": 45,
                                     "map": {"name": "$.data.userProfile.profile.user_name",
                                             "avatar": "$.data.userProfile.profile.headurl",
                                             "bio": "$.data.userProfile.profile.user_text",
                                             "fans": "$.data.userProfile.ownerCount.fan",
                                             "likes": "$.data.userProfile.ownerCount.like",
                                             "video_count": "$.data.userProfile.ownerCount.photo_public"}},
                        "weibo": {"path": "/api/v1/weibo/web_v2/fetch_user_basic_info",
                                  "params": {"uid": "{uid}"},
                                  "map": {"name": ["$.data.data.screen_name", "$.data.screen_name"],
                                          "avatar": ["$.data.data.avatar_hd", "$.data.avatar_hd"],
                                          "bio": ["$.data.data.description", "$.data.description"],
                                          "fans": ["$.data.data.followers_count", "$.data.followers_count"],
                                          "likes": ["$.data.data.attitudes_count", "$.data.attitudes_count"],
                                          "video_count": ["$.data.data.statuses_count", "$.data.statuses_count"]}},
                        "channels": {"path": "/api/v1/wechat_channels/v2/fetch_user_profile",
                                     "method": "POST", "timeout": 45,
                                     "bodyMap": {"username": "{uid}", "raw": False},
                                     "map": {"name": "$.data.nickname", "avatar": "$.data.head_url",
                                             "bio": "$.data.signature", "fans": "$.data.fans_count",
                                             "likes": "$.data.like_count", "video_count": "$.data.feeds_count"}},
                        "tiktok": {"path": "/api/v1/tiktok/web/fetch_user_profile",
                                   "params": {"secUid": "{uid}"},
                                   "map": {"name": "$.data.userInfo.user.nickname",
                                           "avatar": ["$.data.userInfo.user.avatarLarger",
                                                      "$.data.userInfo.user.avatarMedium"],
                                           "bio": "$.data.userInfo.user.signature",
                                           "fans": "$.data.userInfo.stats.followerCount",
                                           "likes": "$.data.userInfo.stats.heartCount",
                                           "video_count": "$.data.userInfo.stats.videoCount"}},
                    },
                    "accountVideos": {
                        "douyin": {"path": "/api/v1/douyin/web/fetch_user_post_videos",
                                   "params": {"sec_user_id": "{uid}", "max_cursor": "{cursor}", "count": "{limit}"},
                                   "list": "$.data.aweme_list", "cursorPath": "$.data.max_cursor",
                                   "hasMorePath": "$.data.has_more",
                                   "map": {"video_id": "$.aweme_id", "title": "$.desc",
                                           "cover": "$.video.cover.url_list[0]", "url": "$.share_url",
                                           "create_time": "$.create_time", "plays": "$.statistics.play_count",
                                           "likes": "$.statistics.digg_count", "comments": "$.statistics.comment_count",
                                           "collects": "$.statistics.collect_count", "shares": "$.statistics.share_count",
                                           "duration": "$.duration"}},
                        "xhs": {"path": "/api/v1/xiaohongshu/app_v2/get_user_posted_notes",
                                "params": {"user_id": "{uid}", "cursor": "{cursor}"},
                                "startCursor": "", "pageSize": 20,
                                "list": "$.data.data.notes", "hasMorePath": "$.data.data.has_more",
                                "map": {"video_id": "$.id", "title": "$.title",
                                        "cover": "$.images_list[0].url_size_large", "url": "$.url",
                                        "create_time": "$.create_time", "plays": "$.view_count",
                                        "likes": "$.likes", "comments": "$.comments_count",
                                        "collects": "$.collected_count", "shares": "$.share_count",
                                        "duration": "$.duration", "cursor": "$.cursor"}},
                        "bilibili": {"path": "/api/v1/bilibili/web/fetch_user_post_videos",
                                     "params": {"uid": "{uid}", "pn": "{cursor}", "order": "pubdate"},
                                     "startCursor": "1", "cursorStep": 1, "pageSize": 30,
                                     "list": ["$.data.data.list.vlist", "$.data.list.vlist"],
                                     "map": {"video_id": ["$.bvid", "$.aid"], "title": "$.title",
                                             "cover": "$.pic", "url": "$.arcurl", "create_time": "$.created",
                                             "plays": "$.play", "likes": "$.like", "comments": "$.video_review",
                                             "collects": "$.favorites", "shares": "$.share", "duration": "$.length"}},
                        "kuaishou": {"path": "/api/v1/kuaishou/web/fetch_user_post",
                                     "params": {"user_id": "{uid}", "pcursor": "{cursor}"},
                                     "startCursor": "", "pageSize": 20,
                                     "list": ["$.data.visionProfilePhotoList.feeds", "$.data.feeds"],
                                     "cursorPath": ["$.data.visionProfilePhotoList.pcursor", "$.data.pcursor"],
                                     "cursorStopValues": ["no_more", "no-more", "-1"],
                                     "map": {"video_id": ["$.photo.photoId", "$.photo.id", "$.photo_id"],
                                             "title": ["$.photo.caption", "$.caption"],
                                             "cover": ["$.photo.coverUrls[0].url", "$.coverUrls[0].url"],
                                             "url": "$.share_info.share_url", "create_time": ["$.photo.timestamp", "$.timestamp"],
                                             "plays": ["$.photo.viewCount", "$.view_count"],
                                             "likes": ["$.photo.likeCount", "$.like_count"],
                                             "comments": ["$.photo.commentCount", "$.comment_count"],
                                             "collects": ["$.photo.collectCount", "$.collect_count"],
                                             "shares": ["$.photo.shareCount", "$.share_count"],
                                             "duration": ["$.photo.duration", "$.duration"]}},
                        "weibo": {"path": "/api/v1/weibo/app/fetch_user_timeline",
                                  "params": {"uid": "{uid}", "page": "{cursor}", "filter_type": "all"},
                                  "startCursor": "1", "cursorStep": 1, "pageSize": 20,
                                  "list": ["$.data.data.statuses", "$.data.statuses", "$.data.data.list"],
                                  "map": {"video_id": ["$.idstr", "$.id"], "title": ["$.text_raw", "$.text"],
                                          "cover": ["$.page_info.page_pic.url", "$.pic_infos.0.large.url"],
                                          "url": "$.url_struct.0.long_url", "create_time": "$.created_at",
                                          "plays": "$.page_info.play_count", "likes": "$.attitudes_count",
                                          "comments": "$.comments_count", "collects": "$.favorites_count",
                                          "shares": "$.reposts_count", "duration": "$.page_info.media_info.duration"}},
                        "channels": {"path": "/api/v1/wechat_channels/v2/fetch_user_videos",
                                     "method": "POST", "timeout": 45,
                                     "bodyMap": {"username": "{uid}", "last_buffer": "{cursor}", "raw": False},
                                     "startCursor": "", "pageSize": 20,
                                     "list": ["$.data.videos", "$.data.feeds", "$.data.list"],
                                     "cursorPath": ["$.data.last_buffer", "$.data.next_buffer"],
                                     "hasMorePath": ["$.data.has_more", "$.data.hasMore"],
                                     "map": {"video_id": ["$.object_id", "$.id"], "title": ["$.description", "$.desc"],
                                             "cover": ["$.cover_url", "$.cover"], "url": "$.share_url",
                                             "create_time": ["$.create_time", "$.createtime"],
                                             "plays": ["$.read_count", "$.view_count"], "likes": "$.like_count",
                                             "comments": "$.comment_count", "collects": "$.fav_count",
                                             "shares": "$.forward_count", "duration": "$.duration"}},
                        "tiktok": {"path": "/api/v1/tiktok/web/fetch_user_post",
                                   "params": {"secUid": "{uid}", "cursor": "{cursor}", "count": "20",
                                              "coverFormat": "2", "post_item_list_request_type": "0"},
                                   "startCursor": "0", "pageSize": 20,
                                   "list": "$.data.itemList", "cursorPath": "$.data.cursor",
                                   "hasMorePath": "$.data.hasMore",
                                   "map": {"video_id": "$.id", "title": "$.desc",
                                           "cover": ["$.video.cover", "$.video.dynamicCover"],
                                           "url": "$.shareUrl", "create_time": "$.createTime",
                                           "plays": "$.stats.playCount", "likes": "$.stats.diggCount",
                                           "comments": "$.stats.commentCount", "collects": "$.stats.collectCount",
                                           "shares": "$.stats.shareCount", "duration": "$.video.duration"}},
                    },
                    "videoText": {
                        "douyin": {"path": "/api/v1/douyin/web/fetch_one_video",
                                   "params": {"aweme_id": "{videoId}"},
                                   "map": {"text": "$.data.aweme_detail.desc"}},
                    },
                    "videoComments": {
                        "douyin": {"path": "/api/v1/douyin/web/fetch_video_comments",
                                   "params": {"aweme_id": "{videoId}", "cursor": "0", "count": "50"},
                                   "list": "$.data.comments",
                                   "map": {"user": "$.user.nickname", "text": "$.text",
                                           "likes": "$.digg_count"}},
                        "xhs": {"path": "/api/v1/xiaohongshu/app_v2/get_note_comments",
                                "params": {"note_id": "{videoId}"},
                                "list": "$.data.data.comments",
                                "map": {"user": "$.user_info.nickname", "text": "$.content",
                                        "likes": "$.like_count"}},
                    },
                },
            },
        },
        "_note": "TikHub 映射按 2026-08 官方文档维护；抖音已用真实 Key 验收，其余平台需用各平台样本逐一验收",
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
