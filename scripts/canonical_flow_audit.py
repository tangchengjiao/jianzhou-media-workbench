"""Canonical source runtime navigation audit with screenshot evidence."""
from __future__ import annotations

import asyncio
import json
from datetime import datetime
from pathlib import Path

from playwright.async_api import async_playwright

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "docs" / "audit" / "canonical-unified-flow-2026-08-09"
URL = "http://127.0.0.1:8741/"


async def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    results: list[dict] = []
    console_errors: list[str] = []
    page_errors: list[str] = []

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page(viewport={"width": 1600, "height": 1000}, device_scale_factor=1)
        page.on("console", lambda msg: console_errors.append(msg.text) if msg.type == "error" else None)
        page.on("pageerror", lambda exc: page_errors.append(str(exc)))

        async def snapshot_dom(label: str) -> None:
            text = await page.locator("body").inner_text()
            (OUT / f"{label}-dom.txt").write_text(text, encoding="utf-8")

        async def capture(index: int, slug: str, expected: list[str]) -> None:
            await page.wait_for_timeout(900)
            body = await page.locator("body").inner_text()
            missing = [item for item in expected if item not in body]
            bad_markers = [item for item in (
                "页面发生了可定位的运行时错误", "加载失败", "go is not a function",
                "视觉工厂加载失败", "资料中心加载失败",
            ) if item in body]
            image = OUT / f"{index:02d}-{slug}.png"
            await page.screenshot(path=str(image), full_page=True)
            await snapshot_dom(f"{index:02d}-{slug}")
            results.append({
                "step": index,
                "slug": slug,
                "url": page.url,
                "expected": expected,
                "missing": missing,
                "bad_markers": bad_markers,
                "health": "ok" if not missing and not bad_markers else "failed",
                "screenshot": str(image),
            })

        async def click_nav(name: str, expected_hash: str) -> None:
            await snapshot_dom(f"before-{expected_hash}")
            button = page.get_by_role("button", name=name, exact=True)
            await button.wait_for(state="visible")
            await button.click()
            await page.wait_for_function(
                "expected => location.hash === '#/' + expected", arg=expected_hash
            )

        await page.goto(URL, wait_until="networkidle")
        await capture(1, "workbench", ["工作总台", "视觉工厂", "资料中心"])

        await click_nav("视觉工厂", "visuals")
        await capture(2, "visual-factory-studio", ["视觉工厂", "封面生成", "图文生成", "生成历史"])

        await snapshot_dom("before-visual-assets-tab")
        await page.get_by_role("button", name="IP资产与模板", exact=True).click()
        await capture(3, "visual-assets-templates", ["IP视觉资产", "模板库", "新建模板"])

        await click_nav("资料中心", "packages")
        await capture(4, "asset-center", ["资料中心", "原始内容", "拆解与模式", "视觉资产", "发布与复盘"])

        await click_nav("内容计划", "plan")
        await capture(5, "content-plan", ["内容计划", "内容库存看板", "待发布", "发布登记"])

        await click_nav("创作工坊", "creation")
        await capture(6, "creation-workshop", ["创作工坊", "选择来源", "版本历史", "AI 发布前预测"])

        await click_nav("数据复盘", "retro")
        await capture(7, "publication-retro", ["数据复盘", "真实发布", "已存快照", "规则建议"])

        await click_nav("系统设置", "settings")
        await snapshot_dom("before-system-diagnostics-tab")
        await page.get_by_role("button", name="系统诊断", exact=True).click()
        await capture(8, "system-version-diagnostics", [
            "当前版本与数据源", "workspace-schema23-visual-assets-lifecycle",
            "视觉工厂", "统一资料中心", "创作生命周期",
        ])

        await browser.close()

    payload = {
        "captured_at": datetime.now().isoformat(timespec="seconds"),
        "base_url": URL,
        "steps": results,
        "console_errors": console_errors,
        "page_errors": page_errors,
        "passed": all(item["health"] == "ok" for item in results)
                  and not console_errors and not page_errors,
    }
    (OUT / "results.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    if not payload["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    asyncio.run(main())
