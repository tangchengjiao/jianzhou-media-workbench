"""Browser-level smoke test that proves Vue mounted instead of serving a blank shell."""

from __future__ import annotations

import os
import pathlib
import shutil
import subprocess
import sys
import tempfile


BASE_URL = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8741/"


def find_chrome() -> str | None:
    candidates = [
        shutil.which("chrome"),
        shutil.which("msedge"),
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    ]
    return next((p for p in candidates if p and pathlib.Path(p).is_file()), None)


def dump_dom(browser: str, url: str, profile: pathlib.Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            browser, "--headless=new", "--disable-gpu", "--no-first-run",
            "--no-default-browser-check", f"--user-data-dir={profile}",
            "--virtual-time-budget=5000", "--dump-dom", url,
        ],
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        timeout=30,
        env=os.environ.copy(),
    )


def main() -> int:
    browser = find_chrome()
    if not browser:
        print("浏览器冒烟测试失败：未找到 Chrome 或 Edge")
        return 2
    base = BASE_URL.rstrip("/")
    with tempfile.TemporaryDirectory(prefix="jianzhou-browser-") as temp_dir:
        root = pathlib.Path(temp_dir)
        result = dump_dom(browser, BASE_URL, root / "home")
        radar_result = dump_dom(browser, f"{base}/#/radar", root / "radar")
        add_result = dump_dom(browser, f"{base}/?radar=add#/radar", root / "add")
    dom = result.stdout
    radar_dom = radar_result.stdout
    add_dom = add_result.stdout
    checks = {
        "浏览器正常退出": result.returncode == 0,
        "Vue 已移除 v-cloak": "v-cloak" not in dom,
        "应用外壳已挂载": 'class="app"' in dom,
        "工作总台已渲染": "工作总台" in dom,
        "未显示挂载失败页": "界面加载失败" not in dom,
        "内容雷达页面可渲染": radar_result.returncode == 0 and "内容雷达" in radar_dom,
        "对标账号入口可见": "添加对标账号" in radar_dom and "对标账号" in radar_dom,
        "添加账号表单可打开": add_result.returncode == 0 and "benchmarkLinkInput" in add_dom,
        "扫描条数可自由填写": 'id="benchmarkScanCount"' in add_dom and 'type="number"' in add_dom,
        "主流平台选项已加载": all(name in add_dom for name in ("视频号", "B站", "快手", "微博", "TikTok")),
        "导航分组默认全部展开": 'nav-group collapsed' not in dom,
    }
    for name, passed in checks.items():
        print(f"  {'[PASS]' if passed else '[FAIL]'} {name}")
    return 0 if all(checks.values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
