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
        settings_result = dump_dom(browser, f"{base}/?settings=llm#/settings", root / "settings")
        voice_result = dump_dom(browser, f"{base}/?settings=voice#/settings", root / "voice")
        extraction_result = dump_dom(browser, f"{base}/#/extraction", root / "extraction")
        retro_result = dump_dom(browser, f"{base}/#/retro", root / "retro")
    dom = result.stdout
    radar_dom = radar_result.stdout
    add_dom = add_result.stdout
    settings_dom = settings_result.stdout
    voice_dom = voice_result.stdout
    extraction_dom = extraction_result.stdout
    retro_dom = retro_result.stdout
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
        "模型设置支持自定义 URL": settings_result.returncode == 0 and "API Base URL" in settings_dom,
        "模型名称可按任务绑定": all(label in settings_dom for label in (
            "快速分类 / 指令路由", "深度分析模型", "写作模型", "结构化抽取模型")),
        "模型列表与真实测试入口可见": "保存并读取模型列表" in settings_dom and "测试快速模型" in settings_dom,
        "百炼 ASR 可配置并有真实音频测试入口": voice_result.returncode == 0
            and "qwen3-asr-flash" in voice_dom and "保存并测试音频" in voice_dom,
        "内容提取支持真实音频上传": extraction_result.returncode == 0
            and "上传真实音频" in extraction_dom and "上传并生成转写计划" in extraction_dom,
        "复盘页面使用真实快照口径": retro_result.returncode == 0
            and "真实发布" in retro_dom and "待采集快照" in retro_dom,
    }
    for name, passed in checks.items():
        print(f"  {'[PASS]' if passed else '[FAIL]'} {name}")
    if not checks["复盘页面使用真实快照口径"]:
        print("    debug:", {"returncode": retro_result.returncode,
              "has_real_publication": "真实发布" in retro_dom,
              "has_pending_snapshot": "待采集快照" in retro_dom,
              "dom_chars": len(retro_dom), "stderr": retro_result.stderr[-300:]})
    return 0 if all(checks.values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
