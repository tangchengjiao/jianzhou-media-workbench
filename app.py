"""见舟 · 自媒体运营工作台 —— 单一启动入口。

用法：
    python app.py            # 默认 http://127.0.0.1:8741
    端口冲突时： set JIANZHOU_PORT=8800 && python app.py
"""
import os
import webbrowser

import uvicorn


def main() -> None:
    port = int(os.environ.get("JIANZHOU_PORT", "8741"))
    host = os.environ.get("JIANZHOU_HOST", "127.0.0.1")
    url = f"http://{host}:{port}"
    print(f"\n  见舟 · 自媒体运营工作台 v2.1")
    print(f"  本地地址：{url}（演示模式：{'开' if os.environ.get('JIANZHOU_DEMO', '1') == '1' else '关'}）\n")
    if os.environ.get("JIANZHOU_NO_BROWSER") != "1":
        try:
            webbrowser.open(url)
        except Exception:
            pass
    uvicorn.run("app.main:app", host=host, port=port, log_level="warning")


if __name__ == "__main__":
    main()
