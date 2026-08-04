"""见舟 · 自媒体运营工作台 —— 单一启动入口。

用法：
    python app.py            # 默认 http://127.0.0.1:8741
    端口冲突时： set JIANZHOU_PORT=8800 && python app.py
"""
import os
import logging
import webbrowser
from logging.handlers import RotatingFileHandler

import uvicorn

from app.config import LOG_DIR
from app.main import app as asgi_app


def _configure_logging() -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    if not any(isinstance(h, RotatingFileHandler) for h in root.handlers):
        handler = RotatingFileHandler(
            LOG_DIR / "app.log", maxBytes=5 * 1024 * 1024, backupCount=5,
            encoding="utf-8",
        )
        handler.setFormatter(logging.Formatter(
            "%(asctime)s %(levelname)s %(name)s %(message)s"
        ))
        root.addHandler(handler)


def main() -> None:
    port = int(os.environ.get("JIANZHOU_PORT", "8741"))
    host = os.environ.get("JIANZHOU_HOST", "127.0.0.1")
    if host not in {"127.0.0.1", "localhost", "::1"}:
        raise RuntimeError(
            "当前版本尚未实现远程访问认证，JIANZHOU_HOST 只允许绑定本机回环地址"
        )
    if not 1 <= port <= 65535:
        raise ValueError("JIANZHOU_PORT 必须在 1 到 65535 之间")
    _configure_logging()
    url = f"http://{host}:{port}"
    print(f"\n  见舟 · 自媒体运营工作台 v2.1")
    print(f"  本地地址：{url}")
    print("  运行模式：启动后读取「系统设置 → 运行模式」\n")
    if os.environ.get("JIANZHOU_NO_BROWSER") != "1":
        try:
            webbrowser.open(url)
        except Exception:
            pass
    uvicorn.run(asgi_app, host=host, port=port, log_level="warning")


if __name__ == "__main__":
    main()
