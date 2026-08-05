"""Low-volume real-provider E2E: uploaded audio to retro evidence chain.

The script never contains or prints API keys. Configure them through DPAPI or
JIANZHOU_LLM_API_KEY / JIANZHOU_ASR_API_KEY before starting the source server.
"""
from __future__ import annotations

import pathlib
import sys
import time

import httpx


BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8741"
AUDIO = pathlib.Path(sys.argv[2]) if len(sys.argv) > 2 else None
PASSED = 0


def check(name: str, condition: bool) -> None:
    global PASSED
    if not condition:
        raise AssertionError(name)
    PASSED += 1
    print(f"  [PASS] {name}")


def wait_job(client: httpx.Client, jid: str, timeout: float = 180) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        response = client.get(f"/api/extraction/jobs/{jid}")
        payload = response.json()
        if response.status_code != 200:
            raise AssertionError(payload)
        job = payload["data"]
        if job["status"] in {"SUCCEEDED", "FAILED", "CANCELLED", "WAITING_CONFIRMATION"}:
            return job
        time.sleep(0.5)
    raise TimeoutError(jid)


def main() -> int:
    if not AUDIO or not AUDIO.is_file():
        print("usage: real_chain_test.py BASE_URL AUDIO_FILE")
        return 2
    with httpx.Client(base_url=BASE, timeout=180) as client:
        runtime = client.get("/api/settings/runtime").json()["data"]
        check("源码服务处于真实模式", runtime["demo_mode"] is False)

        with AUDIO.open("rb") as stream:
            upload = client.post(
                "/api/extraction/assets/upload",
                data={"title": "真实 Provider 端到端验收", "platform": "local"},
                files={"file": (AUDIO.name, stream, "audio/wav")},
            ).json()["data"]
        check("真实音频上传并生成不可变素材", bool(upload["asset_id"]))

        plan = client.post("/api/extraction/plans", json={
            "asset_id": upload["asset_id"], "title": "真实 Provider 端到端验收",
            "platform": "local",
        }).json()["data"]
        check("音频执行计划指向上传素材", plan["source"]["asset_id"] == upload["asset_id"])
        submitted = client.post("/api/extraction/jobs", json={
            "plan": plan, "authorizations": {"asr_when_no_caption": True},
        }).json()["data"]
        extraction = wait_job(client, submitted["job_id"])
        check("百炼 ASR 提取任务成功", extraction["status"] == "SUCCEEDED")
        source_id = extraction["output_json"]["source_content_id"]
        assets = client.get(f"/api/extraction/assets/{source_id}").json()["data"]
        check("真实转写与证据事件已持久化",
              assets["assets"]["asr"]["exists"] and len(assets["evidence"]) >= 2)

        analysis_submit = client.post("/api/analysis/jobs", json={
            "source_content_id": source_id, "title": assets["title"],
            "source_chain": [f"素材 #{upload['asset_id']}", f"提取 #{source_id}"],
        }).json()["data"]
        analysis_job = wait_job(client, analysis_submit["job_id"])
        check("DeepSeek 内容拆解任务成功", analysis_job["status"] == "SUCCEEDED")
        analysis_id = analysis_job["output_json"]["analysis_id"]
        analysis = client.get(f"/api/analysis/detail/{analysis_id}").json()["data"]
        check("拆解结果满足前端稳定 Schema",
              all(key in analysis["result"] for key in ("topic", "structure", "reusable", "non_reusable")))

        project = client.post("/api/creation/projects", json={
            "title": "真实链路如何避免演示数据", "source_content_id": source_id,
            "source_chain": [f"拆解 #{analysis_id}"], "goal": "验证真实链路完整性",
        }).json()["data"]
        pid = project["id"]
        directions = client.get(f"/api/creation/projects/{pid}/directions").json()["data"]
        check("DeepSeek 返回三类差异化方向", len(directions) >= 3)
        outline = client.post(f"/api/creation/projects/{pid}/direction", json={
            "direction": f"{directions[0]['type']}：{directions[0]['title']}"
        }).json()["data"]
        check("DeepSeek 提纲满足结构契约", len(outline["outline"]) >= 3)
        drafts = client.post(f"/api/creation/projects/{pid}/drafts/generate", json={
            "platforms": ["抖音口播", "小红书图文"],
        }).json()["data"]["drafts"]
        check("真实生成两个平台稿件", len(drafts) == 2 and all(d["content"] for d in drafts))

        prediction = {
            "goal": "验证真实链路完整性", "expected_bucket": "待基线校准",
            "confidence": 0.55, "strengths": "证据链完整", "risks": "样本量不足",
            "hypothesis": "显式来源与多平台改写可提升复用效率", "user_judgement": "测试记录",
        }
        client.post(f"/api/creation/projects/{pid}/prediction", json=prediction).raise_for_status()
        client.post(f"/api/creation/projects/{pid}/prediction/lock").raise_for_status()
        task = client.post("/api/planning/tasks", json={
            "title": "真实 Provider 端到端验收", "project_id": pid, "platform": "抖音",
        }).json()["data"]
        for status in ("SELECTED", "OUTLINING", "DRAFTING", "PRODUCING", "READY_TO_PUBLISH"):
            client.patch(f"/api/planning/tasks/{task['id']}", json={"status": status}).raise_for_status()
        publication = client.post("/api/publications", json={
            "task_id": task["id"], "platform": "抖音",
            "url": "https://example.com/real-provider-e2e", "final_title": "真实 Provider 端到端验收",
            "note": "自动化验收记录，不代表真实平台发布",
        }).json()["data"]
        check("发布登记创建三个真实待采集计划", len(publication["schedules"]) == 3)
        client.post(f"/api/retro/publications/{publication['id']}/snapshots", json={
            "tn": "T+1", "plays": 100, "likes": 8, "comments": 2, "collects": 5,
        }).raise_for_status()
        retro = client.post(f"/api/retro/publications/{publication['id']}/generate").json()["data"]
        check("快照进入复盘并产生待确认规则", bool(retro["rule_suggestion_id"]))
    print(f"结果：{PASSED} 通过 · 0 失败")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
