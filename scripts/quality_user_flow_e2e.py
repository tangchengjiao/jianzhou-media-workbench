"""Current real-workspace UX regression for text quality and own-account works."""
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
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import Select, WebDriverWait


ROOT = pathlib.Path(__file__).resolve().parents[1]
BASE = "http://127.0.0.1:8741"
OUTPUT = ROOT / "docs" / "audit" / "quality-pass-2026-08-05"
ROUTES = {
    "workbench": "工作总台", "profile": "IP档案", "account": "账号运营",
    "radar": "内容雷达", "extraction": "内容提取", "analysis": "内容拆解",
    "creation": "创作工坊", "plan": "内容计划", "retro": "数据复盘",
    "growth": "成长记录", "packages": "资料中心", "settings": "系统设置",
}
SUSPICIOUS = ("Ã", "Â", "â€", "ðŸ", "ï¼", "ã€", "ç¬", "é›", "æœ", "å¯")


def chrome_path() -> str:
    for item in (
        shutil.which("chrome"),
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    ):
        if item and pathlib.Path(item).is_file():
            return item
    raise RuntimeError("未找到用户当前使用的 Chrome")


def assert_clean_text(text: str, context: str) -> None:
    c1 = [char for char in text if "\u0080" <= char <= "\u009f"]
    markers = [marker for marker in SUSPICIOUS if marker in text]
    if c1 or markers:
        raise AssertionError(f"{context} 仍含疑似乱码：C1={len(c1)}, markers={markers}")


def main() -> int:
    health = requests.get(BASE + "/healthz", timeout=10).json()
    if health.get("demo_mode"):
        raise RuntimeError("验收要求真实模式，当前却是演示模式")
    OUTPUT.mkdir(parents=True, exist_ok=True)
    for old in OUTPUT.glob("*"):
        if old.is_file():
            old.unlink()

    options = Options()
    options.binary_location = chrome_path()
    options.add_argument("--headless=new")
    options.add_argument("--window-size=1680,1050")
    options.add_argument("--disable-gpu")
    options.add_argument("--no-first-run")
    options.set_capability("goog:loggingPrefs", {"browser": "ALL"})
    results: list[dict] = []

    with tempfile.TemporaryDirectory(prefix="jz-quality-pass-") as profile:
        options.add_argument(f"--user-data-dir={profile}")
        driver = webdriver.Chrome(options=options)
        wait = WebDriverWait(driver, 30)

        def body() -> str:
            return driver.find_element(By.TAG_NAME, "body").text

        def open_route(route: str) -> str:
            driver.get(f"{BASE}/#/{route}")
            expected = ROUTES[route]
            wait.until(lambda _d: expected in body() and "界面加载失败" not in body())
            if route == "account":
                wait.until(lambda _d: "见舟AI" in body())
            elif route == "analysis":
                wait.until(lambda _d: "核心选题" in body())
            time.sleep(.25)
            text = body()
            assert len(text) > 40, f"{route} 页面内容异常为空"
            assert_clean_text(text, route)
            return text

        def snap(name: str) -> None:
            target = OUTPUT / f"{name}.png"
            driver.save_screenshot(str(target))
            if target.stat().st_size < 12_000:
                raise AssertionError(f"截图疑似为空：{target.name}")

        def run(name: str, fn) -> None:
            started = time.time()
            try:
                detail = fn() or {}
                results.append({"name": name, "status": "PASS", "seconds": round(time.time() - started, 2), **detail})
                print("[PASS]", name)
            except Exception as exc:
                snap(f"FAIL-{len(results)+1:02d}")
                results.append({"name": name, "status": "FAIL", "seconds": round(time.time() - started, 2), "error": str(exc)})
                print("[FAIL]", name, exc)

        def all_routes() -> dict:
            page_lengths = {}
            for route in ROUTES:
                page_lengths[route] = len(open_route(route))
            return {"routes": page_lengths}

        run("十二个主页面可加载且无疑似乱码", all_routes)

        def workbench_quality() -> None:
            text = open_route("workbench")
            assert "任务队列" in text and "自动成功" in text
            brand = driver.find_element(By.CSS_SELECTOR, ".brand").rect
            topbar = driver.find_element(By.CSS_SELECTOR, ".topbar").rect
            title = driver.find_element(By.CSS_SELECTOR, ".tb-title").rect
            section = driver.find_element(By.CSS_SELECTOR, ".content > section").rect
            assert abs(brand["height"] - topbar["height"]) <= 1
            assert abs((brand["y"] + brand["height"]) - (topbar["y"] + topbar["height"])) <= 1
            assert abs(title["x"] - section["x"]) <= 1
            snap("01-workbench-clean-task-queue")

        run("工作总台历史任务标题已修复", workbench_quality)

        def profile_quality() -> None:
            text = open_route("profile")
            assert "见舟AI" in text and "v1.4 · 当前激活" in text
            assert "新媒体AI工作流与内容生产系统" in text
            assert "产品阶梯" in text and "核心信任资产" in text
            assert "资料状态" in text and "Gate未完全通过" in text
            assert "本人原话样本" in text and "已进入生成上下文" in text
            assert "能简单化的动作就不要变复杂" in text
            assert "D:\\见舟IP操盘\\IP实例\\见舟AI" in text
            snap("02-profile-local-instance-import")

        run("本地见舟AI资料已进入真实IP档案并保留来源状态", profile_quality)

        def extraction_quality() -> None:
            text = open_route("extraction")
            assert "拖拽文件到这里，或点击选择" in text
            assert "上传并生成转写计划" in text
            assert "本地内容资产库" in text and "提取一次，原文和拆解结果持续保留" in text
            assert not driver.find_elements(By.CSS_SELECTOR, ".asset-library-row")
            driver.find_element(By.CSS_SELECTOR, ".asset-library-card .collapsible-head").click()
            wait.until(lambda _d: len(driver.find_elements(By.CSS_SELECTOR, ".asset-library-row")) >= 5,
                       "资产库未显示至少 5 条真实资产")
            primary_row = driver.find_element(By.XPATH, "//div[contains(@class,'asset-library-row')][contains(.,'4062 字')]")
            driver.execute_script("arguments[0].click();", primary_row)
            wait.until(lambda _d: "展开完整原文" in driver.find_element(By.CSS_SELECTOR, ".transcript-summary").get_attribute("textContent")
                       and "4062 字" in driver.find_element(By.CSS_SELECTOR, ".transcript-summary").get_attribute("textContent"),
                       "首条 4062 字原文摘要未加载")
            first_summary = driver.find_element(By.CSS_SELECTOR, ".transcript-summary").get_attribute("textContent")
            driver.execute_script("arguments[0].click();", driver.find_element(By.CSS_SELECTOR, ".transcript-summary"))
            first_text = driver.find_element(By.CSS_SELECTOR, ".persisted-transcript pre").get_attribute("textContent")
            assert len(first_text) >= 3000 and "skill" in first_text.lower()
            rows = driver.find_elements(By.CSS_SELECTOR, ".asset-library-row")
            rows[1].click()
            wait.until(lambda _d: driver.find_element(By.CSS_SELECTOR, ".transcript-summary").get_attribute("textContent") != first_summary,
                       "第二条资产摘要未切换")
            driver.execute_script("arguments[0].click();", driver.find_element(By.CSS_SELECTOR, ".transcript-summary"))
            wait.until(lambda _d: driver.find_element(By.CSS_SELECTOR, ".persisted-transcript pre").get_attribute("textContent") != first_text,
                       "第二条完整原文未切换")
            assert len(driver.find_element(By.CSS_SELECTOR, ".persisted-transcript pre").get_attribute("textContent")) >= 1000
            primary_row = driver.find_element(By.XPATH, "//div[contains(@class,'asset-library-row')][contains(.,'4062 字')]")
            driver.execute_script("arguments[0].click();", primary_row)
            wait.until(lambda _d: driver.find_element(By.CSS_SELECTOR, ".transcript-summary").get_attribute("textContent") == first_summary,
                       "切回第一条资产摘要失败")
            driver.execute_script("arguments[0].click();", driver.find_element(By.CSS_SELECTOR, ".transcript-summary"))
            wait.until(lambda _d: driver.find_element(By.CSS_SELECTOR, ".persisted-transcript pre").get_attribute("textContent") == first_text,
                       "切回第一条完整原文失败")
            transcript = driver.find_element(By.CSS_SELECTOR, ".persisted-transcript")
            driver.execute_script("arguments[0].scrollIntoView({block:'center'});", transcript)
            snap("02b-extraction-full-original-text")
            drop = driver.find_element(By.CSS_SELECTOR, ".media-file-drop")
            title = driver.find_element(By.CSS_SELECTOR, ".upload-title-field .input")
            assert abs(drop.rect["height"] - title.rect["height"]) <= 3
            library = driver.find_element(By.CSS_SELECTOR, ".asset-library-card")
            driver.execute_script("arguments[0].scrollIntoView({block:'start'});", library)
            snap("02-extraction-local-library-and-original-text")
            failed = driver.find_elements(By.CSS_SELECTOR, ".task-row.failed .btn-danger")
            if failed:
                driver.execute_script("arguments[0].click();", failed[0])
                wait.until(EC.visibility_of_element_located((By.CSS_SELECTOR, ".modal.open .modal-card")))
                error_text = driver.find_element(By.CSS_SELECTOR, ".modal.open").text
                assert "返回任务处理" in error_text and "去修复配置" not in error_text
                driver.find_element(By.CSS_SELECTOR, ".modal.open .modal-x").click()

        run("本地内容资产库可切换历史来源并查看完整原文", extraction_quality)

        def analysis_quality() -> None:
            open_route("analysis")
            wait.until(lambda _d: len(driver.find_elements(By.CSS_SELECTOR, ".analysis-history-item")) >= 5)
            assert not driver.find_elements(By.CSS_SELECTOR, ".analysis-original pre")
            driver.find_element(By.XPATH, "//button[contains(.,'展开原文')]").click()
            wait.until(lambda _d: len(driver.find_element(By.CSS_SELECTOR, ".analysis-original pre").text) >= 3000)
            text = body()
            assert "原始文案" in text and "历史拆解" in text and "内容结构" in text
            assert len(driver.find_elements(By.CSS_SELECTOR, ".struct .seg")) >= 4
            original = driver.find_element(By.CSS_SELECTOR, ".analysis-original pre").text
            history = driver.find_elements(By.CSS_SELECTOR, ".analysis-history-item")
            history[1].click()
            driver.find_element(By.XPATH, "//button[contains(.,'展开原文')]").click()
            wait.until(lambda _d: driver.find_element(By.CSS_SELECTOR, ".analysis-original pre").text != original)
            assert len(driver.find_element(By.CSS_SELECTOR, ".analysis-original pre").text) >= 1000
            driver.find_elements(By.CSS_SELECTOR, ".analysis-history-item")[0].click()
            driver.find_element(By.XPATH, "//button[contains(.,'展开原文')]").click()
            wait.until(lambda _d: driver.find_element(By.CSS_SELECTOR, ".analysis-original pre").text == original)
            snap("03-analysis-history-with-full-original")

        run("历史拆解可切换且每条都能回看完整原文", analysis_quality)

        def creation_quality() -> None:
            open_route("creation")
            projects = requests.get(BASE + "/api/creation/projects", timeout=15).json()["data"]
            target = next((item for item in projects if requests.get(
                BASE + "/api/creation/projects/" + item["id"], timeout=15,
            ).json()["data"]["drafts"]), None)
            assert target, "真实工作区至少应保留一个已有稿件版本的项目"
            Select(driver.find_element(By.CSS_SELECTOR, ".page-head select")).select_by_value(target["id"])
            wait.until(lambda _d: target["title"] in body() and "差异化方向" in body())
            text = body()
            assert "加载中" not in text
            assert "Context Resolver" in text and "稿件工作区" in text
            assert "生成方向建议" in text and "按需调用模型" in text
            assert "每次生成都会立即新增一个不可变版本" in text
            assert "检查" in text and "风格画像量化匹配" in text
            assert "版本历史" in text and "恢复为新版本" in text
            draft = driver.find_element(By.CSS_SELECTOR, ".draft-area").get_attribute("value")
            assert len(draft) > 500
            snap("04-creation-real-project-and-draft")

        run("真实拆解已进入创作项目且创作工坊不再卡加载", creation_quality)

        def own_account_works() -> None:
            open_route("account")
            douyin_card = wait.until(EC.presence_of_element_located((
                By.XPATH,
                "//div[contains(@class,'card')][.//b[contains(.,'抖音')] and contains(.,'见舟AI')]",
            )))
            douyin_card.find_element(By.XPATH, ".//button[contains(.,'查看数据')]").click()
            wait.until(lambda _d: "我的公开作品" in body() and len(driver.find_elements(By.CSS_SELECTOR, ".own-work-card")) >= 20)
            text = body()
            assert "见舟AI" in text and "我的公开作品" in text and "已沉淀" in text
            assert "最新作品：2026/8/4" in text or "最新作品：2026-08-04" in text
            assert "一键自动拆完一张海报" in text
            assert "同步最近作品" in text and "补录作品链接" in text
            works = driver.find_elements(By.CSS_SELECTOR, ".own-work-card")
            assert len(works) >= 20
            sort_control = driver.find_element(By.CSS_SELECTOR, "select[aria-label='作品排序']")
            filter_control = driver.find_element(By.CSS_SELECTOR, "select[aria-label='作品筛选']")
            assert sort_control.get_attribute("value") == "latest"
            assert filter_control.get_attribute("value") == "all"
            assert driver.find_elements(By.CSS_SELECTOR, ".own-work-review select")
            section = driver.find_element(By.CSS_SELECTOR, ".own-works-section")
            driver.execute_script("arguments[0].scrollIntoView({block:'start'});", section)
            wait.until(lambda _d: driver.execute_script(
                "return [...document.querySelectorAll('.own-work-card img')].slice(0,5).every(img => img.complete && img.naturalWidth > 0)"
            ))
            time.sleep(.5)
            snap("04-own-account-persisted-works")

            first_extract = driver.find_element(By.CSS_SELECTOR, ".own-work-card .btn-pri")
            first_extract.click()
            wait.until(EC.visibility_of_element_located((By.CSS_SELECTOR, ".modal.open .modal-card")))
            modal_text = driver.find_element(By.CSS_SELECTOR, ".modal.open").text
            assert "执行计划" in modal_text and "一键自动拆完一张海报" in modal_text
            snap("05-own-work-single-extraction-plan")
            driver.find_element(By.CSS_SELECTOR, ".modal.open .modal-x").click()

            channels_card = driver.find_element(By.XPATH,
                "//div[contains(@class,'card')][.//b[contains(.,'视频号')]]")
            channels_card.find_element(By.XPATH, ".//button[contains(.,'查看数据')]").click()
            wait.until(lambda _d: "我的公开作品" in body()
                       and len(driver.find_elements(By.CSS_SELECTOR, ".own-work-card")) == 15)
            videos = driver.find_elements(By.CSS_SELECTOR, ".own-work-card img")
            assert len(videos) >= 5
            wait.until(lambda d: d.execute_script(
                "return [...document.querySelectorAll('.own-work-card img')].slice(0,3)"
                ".filter(v=>v.complete&&v.naturalWidth>0).length>=2"
            ))
            assert "生成并打开" in body() and "复制账号 ID" in body()
            snap("07-channels-preview-and-lazy-original-link")
            wait.until(EC.invisibility_of_element_located((By.CSS_SELECTOR, ".modal.open")))

            boxes = driver.find_elements(By.CSS_SELECTOR, ".own-work-card input[type='checkbox']")[:2]
            for box in boxes:
                driver.execute_script("arguments[0].click();", box)
            driver.find_element(By.XPATH, "//button[contains(.,'批量提取（2）')]").click()
            wait.until(lambda _d: "数据源预估" in driver.find_element(By.CSS_SELECTOR, ".modal.open").text)
            assert "¥0.20" in driver.find_element(By.CSS_SELECTOR, ".modal.open").text
            snap("06-own-work-batch-confirmation")
            driver.find_element(By.CSS_SELECTOR, ".modal.open .modal-x").click()

        run("自有作品沉淀、单条提取与批量确认入口", own_account_works)

        def new_benchmark() -> None:
            open_route("radar")
            driver.find_element(By.XPATH, "//button[@role='tab' and contains(.,'对标账号')]").click()
            wait.until(lambda _d: "里昂说AI" in body())
            card = driver.find_element(By.XPATH, "//article[contains(.,'里昂说AI')]")
            button = card.find_element(By.XPATH, ".//button[contains(.,'打开监控墙')]")
            button.click()
            wait.until(lambda _d: "里昂说AI" in body() and "作品监控墙" in body())
            assert "24" in body() and driver.find_elements(By.CSS_SELECTOR, ".benchmark-work")
            links = driver.find_elements(By.CSS_SELECTOR, ".benchmark-work a[href]")
            assert any("douyin.com/video/" in (link.get_attribute("href") or "") for link in links)
            wait.until(lambda d: d.execute_script(
                "const xs=[...document.querySelectorAll('.benchmark-cover img')].slice(0,5);"
                "return xs.length>0 && xs.filter(x=>x.complete&&x.naturalWidth>0).length>=Math.min(3,xs.length);"
            ))
            snap("07-new-benchmark-leon-ai-wall")

        run("新对标账号真实抓取与原作链接", new_benchmark)

        def radar_source_probe() -> None:
            open_route("radar")
            driver.find_element(By.XPATH, "//button[@role='tab' and contains(.,'数据源与监听规则')]").click()
            wait.until(lambda _d: "数据源与监听规则" in body() and "AIHot" in body())
            assert "获取 30" in body() and "去重 30" in body()
            driver.find_element(By.XPATH, "//button[contains(.,'添加连接器')]").click()
            wait.until(EC.visibility_of_element_located((By.ID, "radarSourceTitle")))
            modal = driver.find_element(By.CSS_SELECTOR, ".modal.open")
            field = modal.find_element(By.CSS_SELECTOR, "input[placeholder*='aihot']")
            field.send_keys("https://aihot.virxact.com/")
            modal.find_element(By.XPATH, ".//button[contains(.,'检测并预览')]").click()
            wait.until(lambda _d: "连接成功" in modal.text and "AIHot REST API" in modal.text)
            snap("08-aihot-real-source-probe")
            modal.find_element(By.CSS_SELECTOR, ".modal-x").click()

        run("AIHot 连接器识别、同步记录与真实预览", radar_source_probe)

        severe = [entry for entry in driver.get_log("browser") if entry.get("level") == "SEVERE"]
        results.append({"name": "浏览器控制台无严重错误", "status": "PASS" if not severe else "FAIL", "errors": severe})
        driver.quit()

    summary = {
        "mode": "real", "browser": "Chrome", "schema_version": health.get("schema_version"),
        "passed": sum(item["status"] == "PASS" for item in results),
        "failed": sum(item["status"] == "FAIL" for item in results), "results": results,
    }
    (OUTPUT / "results.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if summary["failed"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
