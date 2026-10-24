"""Capture a share-safe, same-build screenshot set for the Huapi product book."""
from __future__ import annotations

import asyncio
import json
from datetime import datetime
from pathlib import Path

from playwright.async_api import async_playwright


ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "output" / "huapi-book" / "见舟工作台花皮书" / "sources" / "screenshots"
BASE = "http://127.0.0.1:8762"


PAGES = [
    ("01-workbench", "/#/workbench", ["工作总台", "内容雷达", "创作工坊"]),
    ("02-profile", "/#/profile", ["IP档案", "访谈"]),
    ("03-account", "/#/account", ["账号运营", "添加账号"]),
    ("04-radar", "/#/radar", ["内容雷达", "添加对标账号"]),
    ("05-extraction", "/#/extraction", ["内容提取", "提取任务"]),
    ("06-analysis", "/#/analysis", ["内容拆解"]),
    ("07-creation", "/#/creation", ["创作工坊", "差异化方向"]),
    ("08-visual-cover", "/#/visuals", ["视觉工厂", "封面工作室"]),
    ("09-visual-graphic", "/#/visuals", ["视觉工厂", "图文笔记工作室"]),
    ("09b-visual-brand", "/?visual_tab=brand#/visuals", ["IP视觉资产库", "提示词预设"]),
    ("09c-visual-history", "/?visual_tab=history#/visuals", ["成品历史", "封面合集"]),
    ("10-plan", "/#/plan", ["内容计划", "内容库存"]),
    ("11-channels-overview", "/?channel_tab=overview#/channels", ["渠道运营", "内容母题"]),
    ("12-channels-moments", "/?channel_tab=moments#/channels", ["朋友圈工作台", "自己写感悟"]),
    ("13-channels-official", "/?channel_tab=official#/channels", ["公众号深度长文"]),
    ("14-retro", "/#/retro", ["数据复盘", "真实发布"]),
    ("15-assets", "/#/packages", ["资料中心", "全部资产"]),
    ("16-settings-runtime", "/?settings=execution#/settings", ["系统设置", "运行模式"]),
    ("17-settings-source", "/?settings=source#/settings", ["数据来源", "TikHub"]),
    ("18-settings-models", "/?settings=llm#/settings", ["大模型", "API Base URL"]),
    ("19-settings-image", "/?settings=image#/settings", ["图片模型", "API Base URL"]),
    ("20-settings-voice", "/?settings=voice#/settings", ["语音模型", "ASR"]),
    ("21-settings-diagnostics", "/?settings=diag#/settings", ["系统诊断", "当前版本"]),
]


async def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    results: list[dict] = []
    console_errors: list[str] = []
    page_errors: list[str] = []

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(
            viewport={"width": 1600, "height": 1000},
            device_scale_factor=1,
            color_scheme="light",
        )
        page = await context.new_page()
        page.on("console", lambda msg: console_errors.append(msg.text) if msg.type == "error" else None)
        page.on("pageerror", lambda exc: page_errors.append(str(exc)))

        for name, route, expected in PAGES:
            await page.goto(BASE + route, wait_until="networkidle")
            await page.wait_for_timeout(900)
            if name == "09-visual-graphic":
                await page.locator(".visual-domain-switch button").nth(1).click()
                await page.wait_for_timeout(500)
            body = await page.locator("body").inner_text()
            missing = [item for item in expected if item not in body]
            bad = [item for item in (
                "页面发生了可定位的运行时错误",
                "加载失败",
                "go is not a function",
                "视觉工厂加载失败",
                "资料中心加载失败",
            ) if item in body]
            target = OUT / f"{name}.png"
            await page.screenshot(path=str(target), full_page=False)
            results.append({
                "name": name,
                "route": route,
                "expected": expected,
                "missing": missing,
                "bad_markers": bad,
                "bytes": target.stat().st_size,
                "ok": not missing and not bad and target.stat().st_size > 20_000,
            })

        await browser.close()

    payload = {
        "captured_at": datetime.now().isoformat(timespec="seconds"),
        "base_url": BASE,
        "pages": results,
        "console_errors": console_errors,
        "page_errors": page_errors,
        "passed": all(item["ok"] for item in results) and not page_errors,
    }
    (OUT / "capture-results.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    if not payload["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    asyncio.run(main())
