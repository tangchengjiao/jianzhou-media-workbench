"""Capture the benchmark monitoring wall and report broken cover images."""
from __future__ import annotations

import argparse
import asyncio
import json
import urllib.request
from pathlib import Path

from playwright.async_api import async_playwright


ROOT = Path(__file__).resolve().parents[1]
BASE_URL = "http://127.0.0.1:8741"
OUT = ROOT / "docs" / "audit" / "benchmark-cover-repair-2026-08-09"


def benchmark_id() -> str:
    with urllib.request.urlopen(f"{BASE_URL}/api/benchmarks", timeout=15) as response:
        payload = json.load(response)
    rows = payload.get("data") or []
    if not rows:
        raise RuntimeError("No benchmark account is available for the audit.")
    return str(max(rows, key=lambda row: int(row.get("works_count") or 0))["id"])


async def capture(label: str) -> dict:
    OUT.mkdir(parents=True, exist_ok=True)
    target = f"{BASE_URL}/?radar=benchmarks&benchmark={benchmark_id()}#/radar"
    console_errors: list[str] = []
    page_errors: list[str] = []
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        page = await browser.new_page(
            viewport={"width": 2048, "height": 1080}, device_scale_factor=1
        )
        page.on(
            "console",
            lambda message: console_errors.append(message.text)
            if message.type == "error"
            else None,
        )
        page.on("pageerror", lambda error: page_errors.append(str(error)))
        await page.goto(target, wait_until="networkidle")
        await page.get_by_text("作品监控墙", exact=True).wait_for(state="visible")
        await page.wait_for_timeout(2500)
        images = await page.locator(".benchmark-cover img").evaluate_all(
            """nodes => nodes.map((node) => ({
                src: node.currentSrc || node.src,
                complete: node.complete,
                naturalWidth: node.naturalWidth,
                naturalHeight: node.naturalHeight,
                alt: node.alt,
            }))"""
        )
        screenshot = OUT / f"{label}.png"
        await page.screenshot(path=str(screenshot), full_page=True)
        await browser.close()
    result = {
        "label": label,
        "url": target,
        "image_count": len(images),
        "loaded_count": sum(1 for image in images if image["naturalWidth"] > 0),
        "broken_count": sum(1 for image in images if image["naturalWidth"] == 0),
        "broken_images": [image for image in images if image["naturalWidth"] == 0],
        "console_errors": console_errors,
        "page_errors": page_errors,
        "screenshot": str(screenshot),
    }
    (OUT / f"{label}.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return result


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("label", choices=("before", "after"))
    args = parser.parse_args()
    result = await capture(args.label)
    print(json.dumps(result, ensure_ascii=True, indent=2))
    if args.label == "after" and (
        result["broken_count"] or result["console_errors"] or result["page_errors"]
    ):
        raise SystemExit(1)


if __name__ == "__main__":
    asyncio.run(main())
