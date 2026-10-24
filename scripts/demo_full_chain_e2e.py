"""Run a user-visible demo chain and always restore real mode afterwards."""
from __future__ import annotations

import json
import pathlib
import shutil
import tempfile
import time

import requests
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait


ROOT = pathlib.Path(__file__).resolve().parents[1]
BASE = "http://127.0.0.1:8741"
OUTPUT = ROOT / "docs" / "audit" / "demo-full-chain-2026-08-05"


def call(method: str, path: str, body: dict | None = None):
    response = requests.request(method, BASE + path, json=body, timeout=30)
    response.raise_for_status()
    payload = response.json()
    if payload.get("success") is False:
        raise RuntimeError(payload["error"])
    return payload.get("data", payload)


def wait_job(path: str, job_id: str, *, timeout: float = 45) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        job = call("GET", f"{path}/{job_id}")
        if job["status"] in {"SUCCEEDED", "FAILED", "CANCELLED", "WAITING_CONFIRMATION"}:
            return job
        time.sleep(.35)
    raise TimeoutError(f"job timeout: {job_id}")


def chrome_path() -> str:
    for item in (
        shutil.which("chrome"),
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    ):
        if item and pathlib.Path(item).is_file():
            return item
    raise RuntimeError("未找到 Chrome")


def main() -> int:
    OUTPUT.mkdir(parents=True, exist_ok=True)
    results: dict = {"started_in_real_mode": not call("GET", "/api/settings/runtime")["demo_mode"]}
    driver = None
    try:
        call("PUT", "/api/settings", {"section": "execution", "values": {"mode": "demo"}})
        assert call("GET", "/api/settings/runtime")["demo_mode"] is True

        signals = call("GET", "/api/radar/signals")
        signal = next((item for item in signals if item.get("status") == "new"), signals[0])
        plan = call("POST", "/api/extraction/plans", {"signal_id": signal["id"]})
        submitted = call("POST", "/api/extraction/jobs", {
            "plan": plan,
            "authorizations": {"asr_when_no_caption": not plan.get("caption_available", True)},
        })
        extraction = wait_job("/api/extraction/jobs", submitted["job_id"])
        if extraction["status"] == "WAITING_CONFIRMATION":
            call("POST", f"/api/extraction/jobs/{extraction['id']}/authorize-asr")
            extraction = wait_job("/api/extraction/jobs", extraction["id"])
        assert extraction["status"] == "SUCCEEDED", extraction.get("error")
        source_id = extraction["output_json"]["source_content_id"]
        source = call("GET", f"/api/extraction/assets/{source_id}")
        assert source["transcript"]["available"] and source["transcript"]["chars"] > 30

        analysis_submit = call("POST", "/api/analysis/jobs", {
            "source_content_id": source_id,
            "title": source["title"],
            "source_chain": [f"雷达 #{signal['id']}", f"提取 #{source_id}"],
        })
        analysis_job = wait_job("/api/extraction/jobs", analysis_submit["job_id"])
        assert analysis_job["status"] == "SUCCEEDED", analysis_job.get("error")
        analysis_id = analysis_job["output_json"]["analysis_id"]
        detail = call("GET", f"/api/analysis/detail/{analysis_id}")
        assert detail["source"]["text"] and detail["result"].get("topic")

        pattern = call("POST", "/api/analysis/patterns", {
            "name": f"演示链路模式 · {detail['result']['topic']}",
            "note": f"来源：拆解 #{analysis_id}；演示验收创建",
        })
        comparison = call("POST", "/api/analysis/comparison-items", {
            "analysis_id": analysis_id,
            "performance_bucket": "high",
            "note": "演示链路：人工标记为高表现样本",
        })

        created = call("POST", "/api/creation/projects", {
            "title": f"演示全链路 · {detail['result']['topic']}",
            "source_chain": [f"雷达 #{signal['id']}", f"提取 #{source_id}", f"拆解 #{analysis_id}"],
            "source_content_id": source_id,
            "analysis_id": analysis_id,
        })
        project_id = created["id"]
        directions = call("GET", f"/api/creation/projects/{project_id}/directions")
        call("POST", f"/api/creation/projects/{project_id}/direction", {
            "direction": f"{directions[0]['type']}：{directions[0]['title']}"
        })
        first = call("POST", f"/api/creation/projects/{project_id}/draft")
        second = call("POST", f"/api/creation/projects/{project_id}/draft")
        multi = call("POST", f"/api/creation/projects/{project_id}/drafts/generate", {
            "platforms": ["抖音口播", "小红书图文", "B站视频简介", "视频号口播"]
        })
        saved = call("PUT", f"/api/creation/projects/{project_id}/draft", {
            "content": second["content"] + "\n\n（演示：人工二创调整后另存）",
            "platform": "抖音口播",
            "note": f"人工二创，基于 v{second['version']}",
        })
        project = call("GET", f"/api/creation/projects/{project_id}")
        assert first["autosaved"] and second["autosaved"] and multi["autosaved"]
        assert len(project["drafts"]) == 7 and saved["version"] == 7
        assert project["drafts"][0]["content"] == first["content"]

        options = Options()
        options.binary_location = chrome_path()
        options.add_argument("--headless=new")
        options.add_argument("--window-size=1680,1050")
        options.add_argument("--disable-gpu")
        options.add_argument("--no-sandbox")
        options.add_argument("--disable-dev-shm-usage")
        options.add_argument("--remote-debugging-pipe")
        options.set_capability("goog:loggingPrefs", {"browser": "ALL"})
        with tempfile.TemporaryDirectory(prefix="jz-demo-e2e-", ignore_cleanup_errors=True) as profile:
            options.add_argument(f"--user-data-dir={profile}")
            driver = webdriver.Chrome(options=options)
            wait = WebDriverWait(driver, 30)

            def body() -> str:
                return driver.find_element(By.TAG_NAME, "body").text

            driver.get(BASE + "/#/extraction")
            wait.until(lambda _d: "本地内容资产库" in body())
            assert not driver.find_elements(By.CSS_SELECTOR, ".asset-library-row")
            driver.save_screenshot(str(OUTPUT / "01-extraction-collapsed-by-default.png"))
            driver.find_element(By.CSS_SELECTOR, ".asset-library-card .collapsible-head").click()
            wait.until(lambda _d: driver.find_elements(By.CSS_SELECTOR, ".asset-library-row"))
            assert not driver.find_elements(By.CSS_SELECTOR, ".persisted-transcript pre")
            driver.save_screenshot(str(OUTPUT / "02-asset-library-expanded-original-collapsed.png"))

            driver.get(BASE + "/#/analysis")
            wait.until(lambda _d: "核心选题" in body() and "研究工具" in body())
            assert not driver.find_elements(By.CSS_SELECTOR, ".analysis-original pre")
            driver.find_element(By.CSS_SELECTOR, ".research-tools-card .collapsible-head").click()
            wait.until(lambda _d: "内容模式卡是什么" in body() and "组合拆解怎么用" in body())
            driver.save_screenshot(str(OUTPUT / "03-analysis-research-tools-explained.png"))

            driver.get(BASE + "/#/creation")
            wait.until(lambda _d: "自动保存" in body() and "版本历史" in body() and "7 版" in body())
            assert "不控制稿件保存" in body() and "恢复为新版本" in body()
            driver.save_screenshot(str(OUTPUT / "04-creation-seven-saved-versions.png"))
            severe = [entry for entry in driver.get_log("browser") if entry.get("level") == "SEVERE"]
            assert not severe, severe

        results.update({
            "signal_id": signal["id"], "source_content_id": source_id,
            "analysis_id": analysis_id, "pattern_id": pattern["id"],
            "comparison_count": comparison["count"], "project_id": project_id,
            "draft_versions": len(project["drafts"]), "screenshots": 4,
        })
    finally:
        if driver:
            driver.quit()
        call("PUT", "/api/settings", {"section": "execution", "values": {"mode": "real"}})
        results["restored_real_mode"] = not call("GET", "/api/settings/runtime")["demo_mode"]
        (OUTPUT / "results.json").write_text(
            json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    print(json.dumps(results, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
