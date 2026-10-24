"""Chrome evidence run for the current real workspace without adding/deleting data."""
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
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait


ROOT = pathlib.Path(__file__).resolve().parents[1]
BASE = "http://127.0.0.1:8741"
OUTPUT = ROOT / "docs" / "audit" / "real-user-flow-2026-08-05"


def chrome_path() -> str:
    for item in (
        shutil.which("chrome"),
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    ):
        if item and pathlib.Path(item).is_file():
            return item
    raise RuntimeError("未找到用户指定的 Chrome")


def main() -> int:
    health = requests.get(BASE + "/healthz", timeout=10).json()
    if health.get("demo_mode"):
        raise RuntimeError("当前证据验收必须运行在真实模式")
    OUTPUT.mkdir(parents=True, exist_ok=True)
    for old in list(OUTPUT.glob("*.png")) + list(OUTPUT.glob("results.json")):
        old.unlink()
    options = Options()
    options.binary_location = chrome_path()
    options.add_argument("--headless=new")
    options.add_argument("--window-size=1680,1050")
    options.add_argument("--disable-gpu")
    options.add_argument("--no-first-run")
    options.set_capability("goog:loggingPrefs", {"browser": "ALL"})
    results: list[dict] = []
    with tempfile.TemporaryDirectory(prefix="jz-real-audit-") as profile:
        options.add_argument(f"--user-data-dir={profile}")
        driver = webdriver.Chrome(options=options)
        wait = WebDriverWait(driver, 25)

        def body() -> str:
            return driver.find_element(By.TAG_NAME, "body").text

        def snap(name: str) -> None:
            target = OUTPUT / f"{name}.png"
            driver.save_screenshot(str(target))
            if target.stat().st_size < 10_000:
                raise AssertionError(f"截图疑似为空：{target.name}")

        def record(name: str, action) -> None:
            started = time.time()
            try:
                action()
                results.append({"name": name, "status": "PASS",
                                "seconds": round(time.time() - started, 2)})
                print("[PASS]", name)
            except Exception as exc:
                snap(f"FAIL-{len(results)+1:02d}")
                results.append({"name": name, "status": "FAIL",
                                "seconds": round(time.time() - started, 2),
                                "error": str(exc)})
                print("[FAIL]", name, exc)

        def nav(label: str) -> None:
            wait.until(EC.element_to_be_clickable(
                (By.XPATH, f"//button[normalize-space(.)='{label}']"))).click()
            wait.until(lambda _d: label in body())

        def shell_and_profile() -> None:
            driver.get(BASE + "/#/profile")
            wait.until(lambda _d: "IP档案" in body() and "真实 Provider" in body())
            assert "林小舟" not in body(), "真实模式仍显示演示 IP"
            groups = driver.find_elements(By.CSS_SELECTOR, ".nav-group-title")
            assert groups and all(g.get_attribute("aria-expanded") == "true" for g in groups)
            groups[0].click()
            assert groups[0].get_attribute("aria-expanded") == "false"
            nav("内容雷达")
            assert groups[0].get_attribute("aria-expanded") == "false", "导航点击导致分组状态被重置"
            groups[0].click()
            driver.get(BASE + "/#/profile")
            wait.until(lambda _d: "IP 总览" in body() and "当前激活" in body())
            assert "见舟AI" in body() and "来源" in body(), "已导入的真实 IP 档案未呈现"
            snap("01-real-profile-and-navigation")

        record("真实工作区与导航折叠状态", shell_and_profile)

        def account_form() -> None:
            driver.get(BASE + "/#/account")
            wait.until(lambda _d: "见舟AI" in body() and "真实快照" in body())
            assert "林小舟" not in body() and "demo_ds" not in body()
            driver.find_element(By.ID, "addOwnAccountBtn").click()
            field = wait.until(EC.visibility_of_element_located((By.ID, "ownAccountLinkInput")))
            assert driver.switch_to.active_element == field
            snap("02-real-own-account-form")
            field.send_keys(Keys.ESCAPE)
            wait.until(EC.invisibility_of_element_located((By.CSS_SELECTOR, ".own-account-add-modal.open")))

        record("真实账号结果与添加弹窗", account_form)

        def radar_signal_and_add() -> None:
            driver.get(BASE + "/#/radar")
            wait.until(lambda _d: "雷达信号" in body() and "真实模式已配置" in body())
            assert "平台热榜" in body() and "提取文案" in body()
            snap("03-real-radar-signals")
            driver.find_element(By.ID, "addBenchmarkBtn").click()
            link = wait.until(EC.visibility_of_element_located((By.ID, "benchmarkLinkInput")))
            count = driver.find_element(By.ID, "benchmarkScanCount")
            assert link.is_displayed() and count.get_attribute("type") == "number"
            assert "视频号" in body() and "B站" in body() and "TikTok" in body()
            snap("04-benchmark-add-form")
            link.send_keys(Keys.ESCAPE)

        record("雷达信号与对标添加入口", radar_signal_and_add)

        def benchmark_wall() -> None:
            driver.get(BASE + "/#/radar")
            wait.until(lambda _d: "雷达信号" in body())
            driver.find_element(By.XPATH, "//button[@role='tab' and contains(.,'对标账号')]").click()
            wait.until(EC.element_to_be_clickable((By.XPATH, "//button[contains(.,'打开监控墙')]"))).click()
            wait.until(lambda _d: "作品监控墙" in body()
                       and ("继续抓取历史" in body() or "已抓取全部" in body()))
            covers = driver.find_elements(By.CSS_SELECTOR, ".benchmark-cover")
            assert covers
            rect = covers[0].rect
            ratio = rect["width"] / max(rect["height"], 1)
            assert 0.70 <= ratio <= 0.80, f"抖音封面宽高比错误：{ratio:.3f}"
            assert "已缓存" in body() and "自动监控最新作品" in body()
            WebDriverWait(driver, 20).until(lambda d: d.execute_script(
                "const xs=[...document.querySelectorAll('.benchmark-cover img')].slice(0,5);"
                "return xs.length===0 || xs.filter(x=>x.complete&&x.naturalWidth>0).length>=Math.min(3,xs.length);"
            ))
            snap("05-benchmark-portrait-wall")
            analyze = driver.find_element(By.XPATH, "//button[contains(.,'分析这个作者')]")
            analyze.click()
            WebDriverWait(driver, 90).until(lambda _d: "值得学习" in body())
            card = driver.find_element(By.CSS_SELECTOR, ".benchmark-analysis")
            driver.execute_script("arguments[0].scrollIntoView({block:'start'});", card)
            time.sleep(.5)
            snap("06-benchmark-author-analysis")

        record("增量监控墙、竖版封面与作者分析", benchmark_wall)

        def radar_sources() -> None:
            driver.get(BASE + "/#/workbench")
            wait.until(lambda _d: "工作总台" in body())
            driver.get(BASE + "/#/radar")
            wait.until(lambda _d: "雷达信号" in body())
            driver.find_element(By.XPATH, "//button[contains(normalize-space(.),'数据源与监听规则')]").click()
            wait.until(lambda _d: "TikHub · Douyin Hot" in body() and "AI Hot" in body())
            assert "平台热榜" in body() and "立即同步" in body()
            snap("07-configurable-radar-sources")
            driver.find_element(By.XPATH, "//button[contains(.,'添加连接器')]").click()
            wait.until(EC.visibility_of_element_located((By.ID, "radarSourceTitle")))
            assert "行业网站 / RSS / JSON / API" in body() and "检测并预览" in body()
            snap("08-radar-source-form")
            driver.find_element(By.CSS_SELECTOR, ".modal.open .modal-x").click()

        record("通用平台热点与可选行业数据源", radar_sources)

        def model_settings() -> None:
            driver.get(BASE + "/?settings=llm#/settings")
            wait.until(lambda _d: "API Base URL" in body() and "保存并读取模型列表" in body())
            values = [item.get_attribute("value") for item in
                      driver.find_elements(By.CSS_SELECTOR, "input") if item.is_displayed()]
            assert "https://api.deepseek.com/v1" in values
            assert values.count("deepseek-v4-pro") >= 2, "分析与写作模型没有使用质量优先配置"
            snap("09-real-model-settings")

        record("可编辑 URL 与任务模型绑定", model_settings)

        severe = [entry for entry in driver.get_log("browser")
                  if entry.get("level") == "SEVERE"]
        results.append({"name": "浏览器控制台无严重错误",
                        "status": "PASS" if not severe else "FAIL",
                        "errors": severe})
        driver.quit()

    summary = {
        "mode": "current-real-user-flow",
        "browser": "Chrome",
        "passed": sum(item["status"] == "PASS" for item in results),
        "failed": sum(item["status"] == "FAIL" for item in results),
        "results": results,
    }
    (OUTPUT / "results.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if summary["failed"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
