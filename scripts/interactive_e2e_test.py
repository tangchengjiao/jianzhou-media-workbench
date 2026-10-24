"""Chrome interaction E2E: clicks, types, submits, and verifies visible results.

Unlike browser_smoke_test.py, this script proves user flows rather than DOM presence.
It starts an isolated demo-mode source server, so destructive clicks never touch the
user's real database. Real-provider checks remain in real_chain_test.py.
"""
from __future__ import annotations

import json
import os
import pathlib
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from contextlib import closing

import requests
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import Select, WebDriverWait


ROOT = pathlib.Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "docs" / "audit" / "interactive-2026-08-05" / "current"


def free_port() -> int:
    with closing(socket.socket()) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def chrome_path() -> str:
    candidates = [
        shutil.which("chrome"),
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    ]
    for candidate in candidates:
        if candidate and pathlib.Path(candidate).is_file():
            return candidate
    raise RuntimeError("未找到用户指定的 Chrome")


def wait_server(base: str, timeout: float = 25) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            if requests.get(base + "/api/settings/runtime", timeout=1).status_code == 200:
                return
        except requests.RequestException:
            pass
        time.sleep(.25)
    raise RuntimeError("隔离测试服务未在规定时间内启动")


class Runner:
    def __init__(self, driver: webdriver.Chrome, base: str, image_stage: pathlib.Path):
        self.driver = driver
        self.base = base
        self.wait = WebDriverWait(driver, 20)
        self.image_stage = image_stage
        self.results: list[dict] = []

    def screenshot(self, name: str) -> None:
        staged = self.image_stage / f"{name}.png"
        target = OUTPUT / f"{name}.png"
        self.driver.save_screenshot(str(staged))
        shutil.copy2(staged, target)

    def record(self, name: str, fn) -> None:
        started = time.time()
        try:
            fn()
            self.results.append({"name": name, "status": "PASS", "seconds": round(time.time() - started, 2)})
            print(f"[PASS] {name}")
        except Exception as exc:
            self.screenshot("FAIL-" + str(len(self.results) + 1))
            self.results.append({"name": name, "status": "FAIL", "seconds": round(time.time() - started, 2), "error": str(exc)})
            print(f"[FAIL] {name}: {exc}")

    def body(self) -> str:
        return self.driver.find_element(By.TAG_NAME, "body").text

    def wait_text(self, text: str) -> None:
        self.wait.until(lambda d: text in d.find_element(By.TAG_NAME, "body").text)

    def nav(self, label: str, expected: str | None = None) -> None:
        button = self.wait.until(EC.element_to_be_clickable((By.XPATH, f"//button[normalize-space(.)='{label}']")))
        button.click()
        self.wait_text(expected or label)

    def click_contains(self, text: str) -> None:
        self.wait.until(EC.element_to_be_clickable((By.XPATH, f"//button[contains(normalize-space(.),'{text}')]"))).click()

    def replace(self, element, value: str) -> None:
        element.clear()
        element.send_keys(value)


def main() -> int:
    OUTPUT.mkdir(parents=True, exist_ok=True)
    for artifact in OUTPUT.iterdir():
        if artifact.is_file():
            artifact.unlink()
    port = free_port()
    base = f"http://127.0.0.1:{port}"
    with tempfile.TemporaryDirectory(prefix="jz-e2e-data-") as data_dir, tempfile.TemporaryDirectory(prefix="jz-e2e-images-") as image_dir:
        env = os.environ.copy()
        env.update({
            "JIANZHOU_DATA_DIR": data_dir,
            "JIANZHOU_DEMO": "1",
            "JIANZHOU_PORT": str(port),
            "JIANZHOU_NO_BROWSER": "1",
            "PYTHONUTF8": "1",
        })
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        server = subprocess.Popen(
            [sys.executable, "app.py"], cwd=ROOT, env=env,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            creationflags=flags,
        )
        driver = None
        try:
            wait_server(base)
            options = Options()
            options.binary_location = chrome_path()
            options.add_argument("--headless=new")
            options.add_argument("--window-size=1680,1050")
            options.add_argument("--disable-gpu")
            options.add_argument("--no-first-run")
            options.set_capability("goog:loggingPrefs", {"browser": "ALL"})
            driver = webdriver.Chrome(options=options)
            runner = Runner(driver, base, pathlib.Path(image_dir))

            def shell_and_navigation():
                driver.get(base + "/#/workbench")
                runner.wait_text("工作总台")
                for label in ("IP档案", "账号运营", "内容雷达", "内容提取", "内容拆解",
                              "创作工坊", "内容计划", "数据复盘", "成长记录", "资料中心", "系统设置"):
                    runner.nav(label)
                runner.nav("工作总台")
                groups = driver.find_elements(By.CSS_SELECTOR, ".nav-group-title")
                before = groups[0].get_attribute("aria-expanded")
                groups[0].click()
                after = groups[0].get_attribute("aria-expanded")
                assert before != after
                groups[0].click()
                runner.screenshot("01-navigation")

            runner.record("全站导航逐项点击、分组只由用户主动收起", shell_and_navigation)

            def workbench_command_flow():
                runner.nav("工作总台")
                before = len(driver.find_elements(By.CSS_SELECTOR, ".chat-log .msg"))
                runner.click_contains("今天最值得处理什么")
                runner.wait.until(lambda d: len(d.find_elements(By.CSS_SELECTOR, ".chat-log .msg")) >= before + 2)
                assert "白名单工具" in runner.body()

            runner.record("工作总台：快捷指令调用后端并返回可执行动作", workbench_command_flow)

            def own_account_flow():
                runner.nav("账号运营")
                runner.click_contains("添加账号")
                runner.wait.until(EC.visibility_of_element_located((By.ID, "ownAccountLinkInput")))
                assert driver.switch_to.active_element.get_attribute("id") == "ownAccountLinkInput"
                runner.replace(driver.find_element(By.ID, "ownAccountLinkInput"), "https://www.douyin.com/user/e2e-own")
                Select(driver.find_element(By.ID, "ownAccountSyncFrequency")).select_by_visible_text("每天")
                driver.find_element(By.ID, "confirmAddOwnAccountBtn").click()
                runner.wait_text("已出现在账号列表中")
                runner.wait_text("查看账号结果")
                runner.screenshot("02-own-account-result")
                driver.find_element(By.ID, "editCycleBtn").click()
                modal = runner.wait.until(EC.visibility_of_element_located((By.CSS_SELECTOR, ".account-cycle-modal.open")))
                goal = modal.find_element(By.CSS_SELECTOR, "input")
                runner.replace(goal, "验证端到端闭环")
                modal.find_element(By.XPATH, ".//button[contains(.,'保存周期')]").click()
                runner.wait_text("验证端到端闭环")

            runner.record("账号运营：弹窗输入→解析→落库→卡片/详情/快照→周期保存", own_account_flow)

            def benchmark_flow():
                runner.nav("内容雷达")
                driver.find_element(By.ID, "addBenchmarkBtn").click()
                runner.wait.until(EC.visibility_of_element_located((By.ID, "benchmarkLinkInput")))
                runner.replace(driver.find_element(By.ID, "benchmarkLinkInput"), "https://www.douyin.com/user/e2e-benchmark")
                count = driver.find_element(By.ID, "benchmarkScanCount")
                runner.replace(count, "7")
                runner.click_contains("添加并后台同步")
                runner.wait.until(EC.invisibility_of_element_located((By.CSS_SELECTOR, ".benchmark-add-modal.open")))
                runner.wait.until(lambda d: len(d.find_elements(By.CSS_SELECTOR, ".benchmark-work")) == 7)
                runner.screenshot("03-benchmark-wall")
                runner.click_contains("分析这个作者")
                runner.wait.until(EC.visibility_of_element_located((By.CSS_SELECTOR, ".benchmark-analysis")))
                runner.wait_text("值得学习")

            runner.record("内容雷达：添加对标→自填条数→作品墙→作者分析", benchmark_flow)

            def extraction_source_flow():
                runner.nav("内容提取")
                runner.click_contains("新建提取")
                select = runner.wait.until(EC.visibility_of_element_located((By.ID, "extractionSignalSelect")))
                options = Select(select).options
                assert len(options) > 1
                Select(select).select_by_index(1)
                runner.click_contains("生成执行计划")
                runner.wait.until(EC.visibility_of_element_located((By.ID, "planModalTitle")))
                runner.wait_text("雷达 #")
                runner.screenshot("04-extraction-source-plan")
                driver.find_element(By.XPATH, "//div[@id='planModalTitle']/following-sibling::button").click()

            runner.record("内容提取：选择真实雷达信号→生成单次确认计划", extraction_source_flow)

            def settings_source_flow():
                runner.nav("系统设置")
                runner.click_contains("数据来源")
                visible = [x for x in driver.find_elements(By.CSS_SELECTOR, "input") if x.is_displayed()]
                test_input = next(x for x in visible if x.get_attribute("placeholder") == "用于真实解析和账号资料测试")
                runner.replace(test_input, "https://www.douyin.com/user/e2e-source")
                runner.click_contains("保存数据源")
                runner.wait_text("已保存并生效")
                runner.click_contains("保存并真实测试")
                runner.wait_text("CONFIG_MISSING_API_KEY")
                runner.screenshot("05-settings-explicit-error")
                driver.find_element(By.CSS_SELECTOR, ".modal.open .modal-x").click()
                driver.get(base + "/?account=add&platform=douyin&link=https%3A%2F%2Fwww.douyin.com%2Fuser%2Fe2e-source#/account")
                runner.wait.until(EC.visibility_of_element_located((By.ID, "ownAccountLinkInput")))
                assert driver.find_element(By.ID, "ownAccountLinkInput").get_attribute("value").endswith("e2e-source")
                driver.switch_to.active_element.send_keys(Keys.ESCAPE)
                runner.wait.until(EC.invisibility_of_element_located((By.CSS_SELECTOR, ".own-account-add-modal.open")))

            runner.record("系统设置：保存结果→缺少 Key 显式失败→链接可带入账号添加", settings_source_flow)

            def profile_version_flow():
                runner.nav("IP档案")
                driver.find_element(By.ID, "createProfileVersionBtn").click()
                reason = runner.wait.until(EC.visibility_of_element_located((By.ID, "profileVersionReason")))
                assert driver.switch_to.active_element.get_attribute("id") == "profileVersionReason"
                runner.replace(reason, "端到端交互测试")
                runner.click_contains("创建并激活版本")
                runner.wait_text("端到端交互测试")
                runner.screenshot("06-profile-version")

            runner.record("IP 档案：创建并激活不可变版本", profile_version_flow)

            def profile_interview_flow():
                runner.nav("IP档案")
                runner.click_contains("开始访谈")
                runner.wait_text("访谈进行中")
                answer = runner.wait.until(EC.visibility_of_element_located((By.CSS_SELECTOR, "input[placeholder^='输入回答']")))
                runner.replace(answer, "我希望帮助内容创作者建立可追溯、可复盘的生产链路。")
                runner.click_contains("回答")
                runner.wait_text("可追溯、可复盘")
                runner.click_contains("暂停（保存进度）")
                runner.wait_text("已保存进度")

            runner.record("IP 档案：开始访谈→提交回答→暂停并保存进度", profile_interview_flow)

            def analysis_compare_flow():
                runner.nav("内容拆解")
                before_badges = driver.find_elements(By.CSS_SELECTOR, ".research-tools-card .badge")
                before_comparisons = 0
                before_patterns = 0
                for badge in before_badges:
                    text = badge.text.strip()
                    if text.startswith("对比样本"):
                        digits = "".join(ch for ch in text if ch.isdigit())
                        before_comparisons = int(digits or 0)
                    if text.startswith("模式卡"):
                        digits = "".join(ch for ch in text if ch.isdigit())
                        before_patterns = int(digits or 0)
                button = runner.wait.until(EC.element_to_be_clickable((By.ID, "addComparisonBtn")))
                button.click()
                modal = runner.wait.until(EC.visibility_of_element_located((By.CSS_SELECTOR, ".analysis-compare-modal.open")))
                Select(modal.find_element(By.CSS_SELECTOR, "select")).select_by_value("high")
                runner.replace(modal.find_element(By.CSS_SELECTOR, "input"), "高于账号中位数 2 倍")
                modal.find_element(By.XPATH, ".//button[contains(.,'加入对比组')]").click()
                runner.wait.until(lambda _d: any(
                    ("对比样本" in badge.text and
                     int("".join(ch for ch in badge.text if ch.isdigit()) or 0) >= max(1, before_comparisons))
                    for badge in driver.find_elements(By.CSS_SELECTOR, ".research-tools-card .badge")
                ))
                runner.click_contains("保存为模式卡")
                pattern_modal = runner.wait.until(EC.visibility_of_element_located((By.CSS_SELECTOR, ".analysis-pattern-modal.open")))
                name = pattern_modal.find_element(By.CSS_SELECTOR, "input")
                runner.replace(name, "端到端真实模式卡")
                pattern_modal.find_element(By.XPATH, ".//button[contains(.,'保存草稿')]").click()
                runner.wait.until(lambda _d: any(
                    ("模式卡" in badge.text and
                     int("".join(ch for ch in badge.text if ch.isdigit()) or 0) >= before_patterns + 1)
                    for badge in driver.find_elements(By.CSS_SELECTOR, ".research-tools-card .badge")
                ))
                runner.screenshot("07-analysis-comparison")

            runner.record("内容拆解：加入表现对比组→按真实来源保存模式卡", analysis_compare_flow)

            def visual_factory_flow():
                runner.nav("视觉工厂")
                runner.wait_text("生成工作台")
                runner.wait.until(EC.visibility_of_element_located((By.CSS_SELECTOR, ".visual-layout")))
                person_toggle = runner.wait.until(EC.presence_of_element_located(
                    (By.CSS_SELECTOR, ".visual-output input[type='checkbox']")))
                assert not person_toggle.is_selected(), "人物原图不应默认启用，避免把已有成品海报再次叠进封面"
                before = len(driver.find_elements(By.CSS_SELECTOR, ".visual-history-card"))
                runner.click_contains("生成并保存到视觉历史")
                runner.wait.until(lambda _d: "张视觉稿已生成并保存" in runner.body()
                                  or len(driver.find_elements(By.CSS_SELECTOR, ".visual-result")) >= 1)
                tabs = driver.find_elements(By.CSS_SELECTOR, ".tabs button")
                next(tab for tab in tabs if "生成历史" in tab.text).click()
                runner.wait.until(lambda _d: len(driver.find_elements(By.CSS_SELECTOR, ".visual-history-card")) >= before + 1)
                runner.screenshot("08-visual-factory-history")

            runner.record("视觉工厂：独立入口→本地模板生成→结果存档→历史可复用", visual_factory_flow)

            def modal_keyboard_flow():
                runner.nav("账号运营")
                driver.find_element(By.ID, "addOwnAccountBtn").click()
                runner.wait.until(EC.visibility_of_element_located((By.CSS_SELECTOR, ".own-account-add-modal.open")))
                driver.switch_to.active_element.send_keys(Keys.ESCAPE)
                runner.wait.until(EC.invisibility_of_element_located((By.CSS_SELECTOR, ".own-account-add-modal.open")))
                driver.find_element(By.ID, "addOwnAccountBtn").click()
                assert driver.find_element(By.CSS_SELECTOR, ".own-account-add-modal").get_attribute("class").endswith("open")
                driver.find_element(By.CSS_SELECTOR, ".own-account-add-modal .modal-x").click()

            runner.record("键盘 Escape 关闭弹窗后可再次正常打开", modal_keyboard_flow)

            severe = [entry for entry in driver.get_log("browser")
                      if entry.get("level") == "SEVERE"
                      and not ("/api/datasource/check" in entry.get("message", "")
                               and "status of 400" in entry.get("message", ""))]
            runner.results.append({"name": "浏览器控制台无严重错误", "status": "PASS" if not severe else "FAIL", "errors": severe})
            print(("[PASS]" if not severe else "[FAIL]") + " 浏览器控制台无严重错误")

            summary = {
                "mode": "isolated-demo-interaction",
                "browser": "Chrome",
                "base_url": base,
                "passed": sum(item["status"] == "PASS" for item in runner.results),
                "failed": sum(item["status"] == "FAIL" for item in runner.results),
                "results": runner.results,
            }
            (OUTPUT / "results.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
            print(json.dumps(summary, ensure_ascii=False, indent=2))
            return 0 if summary["failed"] == 0 else 1
        finally:
            if driver:
                driver.quit()
            server.terminate()
            try:
                server.wait(timeout=5)
            except subprocess.TimeoutExpired:
                server.kill()


if __name__ == "__main__":
    raise SystemExit(main())
