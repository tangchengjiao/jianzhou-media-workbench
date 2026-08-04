"""见舟工作台冒烟测试：核心功能链路 + 无兜底专项断言。

用法：先启动服务（python app.py），再执行：
    python scripts/smoke_test.py [http://127.0.0.1:8741]

覆盖：
  1. 健康检查与演示模式标识
  2. 完整链路：信号 → 提取计划 → 确认执行 → 拆解 → 选题 → 初稿 → 排期 → 发布登记 → 复盘 → 规则确认
  3. 无兜底专项：未授权 ASR 不自动执行 / 预测锁定后禁止修改 / 非法状态转换被拒绝 /
     部分导入明示失败行 / 所有错误携带 Trace ID
"""
import sys
import time

import httpx

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8741"
PASS, FAIL = 0, 0


def check(name: str, cond: bool, detail: str = "") -> None:
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ✓ {name}")
    else:
        FAIL += 1
        print(f"  ✗ {name}  {detail}")


def wait_job(client: httpx.Client, job_id: str, timeout: float = 20.0) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        r = client.get(f"/api/extraction/jobs/{job_id}")
        job = r.json()["data"]
        if job["status"] in ("SUCCEEDED", "FAILED", "WAITING_CONFIRMATION", "CANCELLED"):
            return job
        time.sleep(0.5)
    raise TimeoutError(f"任务 {job_id} 超时未结束")


def main() -> int:
    print(f"\n见舟工作台冒烟测试 → {BASE}\n")
    with httpx.Client(base_url=BASE, timeout=15) as c:
        # ---------- 1. 基础可用性 ----------
        print("【1】基础可用性")
        r = c.get("/healthz")
        check("健康检查", r.status_code == 200 and r.json()["ok"])
        r = c.get("/api/settings/runtime")
        check("演示模式显式标识（demo_mode=true）", r.json()["data"]["demo_mode"] is True)
        r = c.get("/")
        check("前端首页可访问", r.status_code == 200 and "见舟" in r.text)
        r = c.get("/api/workbench/status")
        check("工作总台状态聚合", r.status_code == 200 and r.json()["data"]["cycle"]["weekly_target"] == 4)

        # ---------- 2. 完整业务链路 ----------
        print("【2】完整链路：雷达 → 提取 → 拆解 → 创作 → 计划 → 发布 → 复盘 → 规则")

        # 雷达信号
        r = c.get("/api/radar/signals")
        signals = r.json()["data"]
        check("雷达信号列表", len(signals) >= 5)
        sig = next((s for s in signals
                    if s["source_type"].startswith("对标") and "偷懒" not in s["title"]),
                   signals[0])

        # 提取：先计划（不执行），计划含成本与默认不授权
        r = c.post("/api/extraction/plans", json={"signal_id": sig["id"]})
        plan = r.json()["data"]
        check("执行计划生成（未执行）", plan["requires_confirmation"] is True)
        check("ASR 授权默认关闭", plan["authorizations"]["asr_when_no_caption"] is False)

        # 确认执行 → 任务完成 → 资产分层
        r = c.post("/api/extraction/jobs", json={"plan": plan, "authorizations": {"asr_when_no_caption": False}})
        job_id = r.json()["data"]["job_id"]
        job = wait_job(c, job_id)
        check("提取任务成功完成", job["status"] == "SUCCEEDED", str(job.get("error")))
        sc_id = job["output_json"]["source_content_id"]
        r = c.get(f"/api/extraction/assets/{sc_id}")
        assets = r.json()["data"]["assets"]
        check("资产分层：原生字幕不可覆盖", assets["caption"]["exists"] and assets["caption"]["immutable"])
        check("未触发 ASR 时 ASR 资产为空", not assets["asr"]["exists"])

        # 拆解
        r = c.post("/api/analysis/jobs",
                   json={"source_content_id": sc_id, "title": plan["source"]["title"],
                         "source_chain": [f"雷达 #{sig['id']}"]})
        ajob_id = r.json()["data"]["job_id"]
        deadline = time.time() + 20
        analysis_id = None
        while time.time() < deadline:
            r = c.get("/api/analysis/list")
            done = [a for a in r.json()["data"]["analyses"] if a["source_content_id"] == sc_id]
            if done:
                analysis_id = done[0]["id"]
                break
            time.sleep(0.6)
        check("拆解任务完成并写库", analysis_id is not None)

        # 创作：项目 → 方向 → 提纲 → 初稿
        r = c.post("/api/creation/projects",
                   json={"title": "冒烟测试选题", "source_chain": [f"拆解 #{analysis_id}"]})
        pid = r.json()["data"]["id"]
        r = c.get(f"/api/creation/projects/{pid}/directions")
        dirs = r.json()["data"]
        check("差异化方向 ≥ 3 类", len(dirs) >= 3)
        r = c.post(f"/api/creation/projects/{pid}/direction",
                   json={"direction": dirs[1]["type"]})
        check("提纲生成", len(r.json()["data"]["outline"]) >= 3)
        r = c.post(f"/api/creation/projects/{pid}/draft")
        check("初稿生成 + 检查执行", "checks" in r.json()["data"])

        # 发布前判断：保存 → 锁定 → 锁定后修改必须被拒绝
        pred = {"goal": "收藏率 > 均值", "expected_bucket": "1.2–1.8×", "confidence": 0.6,
                "strengths": "案例密度", "risks": "同质化", "hypothesis": "归因翻转有效",
                "user_judgement": "值得试"}
        r = c.post(f"/api/creation/projects/{pid}/prediction", json=pred)
        check("发布前判断保存", r.json()["success"] or r.json()["data"])
        r = c.post(f"/api/creation/projects/{pid}/prediction/lock")
        check("发布前判断锁定", r.json()["data"]["locked"] is True)
        r = c.post(f"/api/creation/projects/{pid}/prediction", json=pred)
        err = r.json().get("error", {})
        check("锁定后修改被拒绝（PREDICTION_ALREADY_LOCKED）",
              err.get("code") == "PREDICTION_ALREADY_LOCKED")
        check("错误携带 Trace ID", bool(err.get("trace_id")))

        # 计划：创建任务 → 状态机
        r = c.post("/api/planning/tasks", json={"title": "冒烟测试内容", "project_id": pid})
        tid = r.json()["data"]["id"]
        r = c.patch(f"/api/planning/tasks/{tid}", json={"status": "SELECTED"})
        check("候选 → 已选题 转换", r.status_code == 200)
        r = c.patch(f"/api/planning/tasks/{tid}", json={"status": "PUBLISHED"})
        check("非法转换被拒绝（CONTENT_STATE_INVALID）",
              r.json().get("error", {}).get("code") == "CONTENT_STATE_INVALID")
        # 合法推进到待发布
        for st in ("OUTLINING", "DRAFTING", "PRODUCING", "READY_TO_PUBLISH"):
            c.patch(f"/api/planning/tasks/{tid}", json={"status": st})

        # 发布登记
        r = c.post("/api/publications",
                   json={"task_id": tid, "platform": "抖音", "url": "https://example.com/smoke",
                         "final_title": "冒烟测试内容"})
        check("发布登记接口可用（不以此证明快照调度已实现）", r.status_code == 200)

        # 复盘与规则
        r = c.get("/api/retro/report/P-201")
        check("演示复盘样本可读取（不以此证明自动回收链路）",
              r.json()["data"]["prediction"]["locked"] == 1 and len(r.json()["data"]["snapshots"]) == 3)
        r = c.get("/api/rules/suggestions")
        sug = next(s for s in r.json()["data"] if s["status"] == "pending")
        r = c.post(f"/api/rules/suggestions/{sug['id']}/approve")
        check("规则建议人工确认后生效", r.status_code == 200)
        r = c.get("/api/rules")
        check("规则已进入经验库", any(x["text"] == sug["rule_text"] for x in r.json()["data"]))

        # ---------- 3. 无兜底专项 ----------
        print("【3】无静默兜底专项")
        # 无字幕且未授权 ASR → 任务必须停在待决策，不得自动转写、不得成功
        no_caption_signal = next((s for s in signals if "偷懒" in s["title"]), None)
        check("存在无原生字幕演示信号", no_caption_signal is not None)
        if no_caption_signal is None:
            raise AssertionError("演示种子缺少用于授权测试的无字幕信号")
        r = c.post("/api/extraction/plans", json={"signal_id": no_caption_signal["id"]})
        plan2 = r.json()["data"]
        check("识别无原生字幕来源", plan2["caption_available"] is False)
        r = c.post("/api/extraction/jobs", json={"plan": plan2, "authorizations": {"asr_when_no_caption": False}})
        job2 = wait_job(c, r.json()["data"]["job_id"])
        check("未授权 ASR → 停在待决策（不成功不失败）",
              job2["status"] == "WAITING_CONFIRMATION", job2["status"])
        check("未授权时不产生 ASR 资产", "source_content_id" not in job2["output_json"])

        # 同步失败：小红书账号 → Typed Error，不返回假数据
        r = c.post("/api/accounts/acc-xhs/sync")
        xhs_job = r.json()["data"]["job_id"]
        deadline = time.time() + 15
        final = None
        while time.time() < deadline:
            rows = c.get("/api/extraction/jobs").json()["data"]
            time.sleep(0.6)
            r2 = c.get("/api/workbench/tasks").json()["data"]
            # 直接查 job 详情接口（extraction 只列提取类，改用诊断 trace 查 jobs 表）
            r3 = c.get(f"/api/extraction/jobs/{xhs_job}")
            if r3.status_code == 200:
                j = r3.json()["data"]
                if j["status"] in ("FAILED", "SUCCEEDED"):
                    final = j
                    break
        check("账号同步失败显式 FAILED", final is not None and final["status"] == "FAILED")
        check("失败错误码 TIKHUB_UNAUTHORIZED + 阶段 + Trace",
              final is not None and final["error"]["code"] == "TIKHUB_UNAUTHORIZED"
              and final["error"]["stage"] == "account_sync" and bool(final["error"]["trace_id"]))

        # 部分导入：失败行必须明示
        csv_content = "标题,发布时间,点赞数\n正常标题,2026/7/1,100\n,2026/7/2,88\n"
        r = c.post("/api/imports/upload?target_package=内容语料包",
                   files={"file": ("test.csv", csv_content.encode("utf-8"), "text/csv")})
        imp = r.json()["data"]
        check("部分导入：状态为 partial 且失败行明示",
              imp["status"] == "partial" and imp["failed"] == 1 and imp["errors"][0]["row"] == 2)

        # 访谈：文字访谈 → 字段候选 → 确认
        r = c.post("/api/profile/interviews", json={"topic": "完整建档", "mode": "text"})
        sid = r.json()["data"]["id"]
        r = c.post(f"/api/profile/interviews/{sid}/turns", json={"text": "我做职场方法论，不写入门内容，从来不说家人们。"})
        r = c.post(f"/api/profile/interviews/{sid}/finish")
        cands = r.json()["data"]["candidates"]
        check("访谈生成字段候选", len(cands) >= 1)
        r = c.post(f"/api/profile/candidates/{cands[0]}/confirm")
        check("字段候选确认（不自动写档案）", r.status_code == 200)

        # 诊断与用量
        r = c.get("/api/diagnostics")
        check("系统诊断真实探测", len(r.json()["data"]["checks"]) >= 4)
        r = c.get("/api/usage/summary")
        check("用量台账累计（含提取/拆解费用）", r.json()["data"]["today"] >= 0.4)

        # 创作者切换：预览 → 激活 → 切回
        r = c.get("/api/users/u-tea/switch-preview")
        check("切换预览（关闭/激活清单）", "will_close" in r.json()["data"])
        r = c.post("/api/users/u-tea/activate")
        check("切换到第二创作者", r.json()["data"]["active_user"] == "u-tea")
        r = c.get("/api/profile/active")
        check("用户隔离：新用户无档案时报 NOT_FOUND",
              r.json().get("error", {}).get("code") == "NOT_FOUND")
        c.post("/api/users/u-zhou/activate")

    print(f"\n结果：{PASS} 通过 · {FAIL} 失败\n")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
