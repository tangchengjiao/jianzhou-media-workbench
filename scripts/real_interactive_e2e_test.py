"""Low-frequency Chrome E2E against the already running real-mode source server.

Usage: python scripts/real_interactive_e2e_test.py BASE_URL ACCOUNT_LINK AUDIO_FILE
The script never reads, prints, or stores API keys.
"""
from __future__ import annotations

import json
import pathlib
import shutil
import sys
import tempfile
import time

import requests
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import Select, WebDriverWait


ROOT = pathlib.Path(__file__).resolve().parents[1]
BASE = (sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8741").rstrip("/")
LINK = sys.argv[2] if len(sys.argv) > 2 else ""
AUDIO = pathlib.Path(sys.argv[3]).resolve() if len(sys.argv) > 3 else None
OUTPUT = ROOT / "docs" / "audit" / "interactive-2026-08-05" / "real"


def chrome_path() -> str:
    for item in (shutil.which("chrome"),
                 r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
                 r"C:\Program Files\Google\Chrome\Application\chrome.exe"):
        if item and pathlib.Path(item).is_file():
            return item
    raise RuntimeError("未找到 Chrome")


def main() -> int:
    if not LINK:
        print("缺少真实公开账号链接")
        return 2
    runtime = requests.get(BASE + "/api/settings/runtime", timeout=15).json()["data"]
    if runtime.get("demo_mode"):
        print("真实 Provider 交互测试拒绝在 Demo 模式运行，请先切换为真实模式。")
        return 2
    OUTPUT.mkdir(parents=True, exist_ok=True)
    for artifact in OUTPUT.iterdir():
        if artifact.is_file():
            artifact.unlink()
    with tempfile.TemporaryDirectory(prefix="jz-real-e2e-images-") as image_dir:
        options = Options()
        options.binary_location = chrome_path()
        options.add_argument("--headless=new")
        options.add_argument("--window-size=1680,1050")
        options.add_argument("--disable-gpu")
        options.set_capability("goog:loggingPrefs", {"browser": "ALL"})
        driver = webdriver.Chrome(options=options)
        wait = WebDriverWait(driver, 150)
        results = []

        def screenshot(name: str):
            staged = pathlib.Path(image_dir) / (name + ".png")
            driver.save_screenshot(str(staged))
            shutil.copy2(staged, OUTPUT / staged.name)

        def body():
            return driver.find_element(By.TAG_NAME, "body").text

        def record(name, fn):
            started = time.time()
            try:
                fn()
                results.append({"name": name, "status": "PASS", "seconds": round(time.time() - started, 2)})
                print("[PASS]", name)
            except Exception as exc:
                screenshot("FAIL-" + str(len(results) + 1))
                results.append({"name": name, "status": "FAIL", "seconds": round(time.time() - started, 2), "error": str(exc)})
                print("[FAIL]", name, exc)
                open_modals = driver.find_elements(By.CSS_SELECTOR, ".modal.open .modal-x")
                if open_modals:
                    open_modals[-1].click()

        def real_account():
            driver.get(BASE + "/#/account")
            wait.until(EC.element_to_be_clickable((By.ID, "addOwnAccountBtn"))).click()
            link = wait.until(EC.visibility_of_element_located((By.ID, "ownAccountLinkInput")))
            link.send_keys(LINK)
            Select(driver.find_element(By.ID, "ownAccountSyncFrequency")).select_by_visible_text("每 6 小时")
            driver.find_element(By.ID, "confirmAddOwnAccountBtn").click()
            wait.until(lambda d: "已出现在账号列表中" in body() or len(d.find_elements(By.CSS_SELECTOR, ".modal.open .inline-error")) > 0)
            assert "已出现在账号列表中" in body(), body()[-800:]
            assert "真实快照" in body() and "real_tikhub" in body() and "demo_ds" not in body()
            screenshot("01-real-own-account")

        record("真实账号：Chrome 表单输入→TikHub 解析→落库→快照回显", real_account)

        def source_check():
            driver.get(BASE + "/?settings=source#/settings")
            wait.until(lambda _d: "保存并真实测试" in body())
            field = next(x for x in driver.find_elements(By.CSS_SELECTOR, "input")
                         if x.is_displayed() and x.get_attribute("placeholder") == "用于真实解析和账号资料测试")
            field.send_keys(LINK)
            driver.find_element(By.XPATH, "//button[contains(.,'保存并真实测试')]").click()
            wait.until(lambda _d: "带入“账号运营”添加" in body())
            assert "粉丝" in body() and "作品" in body() and "real_tikhub" in body()
            screenshot("02-real-source-result")

        record("系统设置数据源：保存→真实测试→账号结果与后续入口", source_check)

        def llm_check():
            driver.get(BASE + "/?settings=llm#/settings")
            wait.until(lambda _d: "测试快速模型" in body())
            driver.find_element(By.XPATH, "//button[contains(.,'测试快速模型')]").click()
            wait.until(lambda _d: "连接与最小生成测试通过" in body())
            screenshot("03-real-llm-result")

        record("大模型：Chrome 点击最小真实生成并回显模型结果", llm_check)

        if AUDIO and AUDIO.is_file():
            def asr_check():
                driver.get(BASE + "/?settings=voice#/settings")
                wait.until(lambda _d: "保存并测试音频" in body())
                driver.find_element(By.CSS_SELECTOR, "input[type=file]").send_keys(str(AUDIO))
                driver.find_element(By.XPATH, "//button[contains(.,'保存并测试音频')]").click()
                wait.until(lambda _d: "ASR 连接与真实音频转写通过" in body())
                screenshot("04-real-asr-result")

            record("ASR：选择真实 WAV→百炼转写→文本回显", asr_check)

        severe = [entry for entry in driver.get_log("browser") if entry.get("level") == "SEVERE"]
        results.append({"name": "浏览器控制台无严重错误", "status": "PASS" if not severe else "FAIL", "errors": severe})
        driver.quit()
    summary = {
        "mode": "real-provider-interaction", "browser": "Chrome",
        "passed": sum(item["status"] == "PASS" for item in results),
        "failed": sum(item["status"] == "FAIL" for item in results),
        "results": results,
    }
    (OUTPUT / "results.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if summary["failed"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
