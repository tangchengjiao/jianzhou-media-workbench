"""外部能力适配层：Demo Provider（显式演示模式）与真实适配器（fail-fast 桩）。

原则：
- 演示模式返回确定性数据，provider 标记 demo_*；
- 真实适配器在未配置 Key 时立即抛 CONFIG_MISSING_API_KEY，绝不返回假数据；
- 配置了 Key 的真实调用走 httpx；任何失败原样抛 Typed Error，不切换适配器。
"""
import base64
import datetime
import ipaddress
import json
import os
import socket
import urllib.parse

from . import config
from .db import jdumps, query_one
from .errors import AppError, config_missing_api_key, llm_unavailable
from .secret_store import get_secret


def get_setting(section: str, key: str, default=""):
    if (section, key) == ("source", "tikhub_api_key"):
        return get_secret("tikhub_api_key") or default
    if (section, key) == ("llm", "api_key"):
        return get_secret("llm_api_key") or default
    if (section, key) == ("voice", "api_key"):
        return get_secret("asr_api_key") or default
    row = query_one("SELECT value_json FROM app_settings WHERE key=?", (section,))
    if not row:
        return default
    import json
    try:
        return json.loads(row["value_json"]).get(key, default)
    except Exception:
        return default


# ================= 社交数据（TikHub） =================

class DemoSocialProvider:
    name = "demo_tikhub"

    def sync_account(self, account: dict) -> dict:
        """演示同步：返回小幅粉丝增长。失败的账号由调用方显式构造错误。"""
        delta = 12 + hash(account["id"]) % 40
        return {"followers": (account.get("followers") or 0) + delta,
                "delta": delta, "posts": 0, "cost": 0.0,
                "provider_request_id": f"req_demo_{account['id']}"}

    def fetch_caption(self, source: dict) -> dict:
        return {"text": f"【演示字幕】《{source['title']}》的完整字幕文本："
                        "为什么你的汇报总被打断？不是领导没耐心……（demo）",
                "cost": 0.10, "provider_request_id": "req_demo_caption"}

    def asr_transcribe(self, source: dict) -> dict:
        return {"text": f"【演示 ASR 转写】《{source['title']}》：本期我们聊聊……（demo）",
                "cost": 0.0, "model": "demo_faster_whisper_small"}


class TikHubProvider:
    """真实 TikHub 适配器（首版范围：无 Key 即失败；有 Key 走真实 HTTP）。"""
    name = "tikhub"

    def __init__(self):
        self.api_key = get_setting("source", "tikhub_api_key")

    def _require_key(self):
        if not self.api_key:
            raise config_missing_api_key("TikHub")

    def sync_account(self, account: dict) -> dict:
        self._require_key()
        raise AppError("TIKHUB_PROVIDER_ERROR",
                       "真实 TikHub 同步尚未在当前环境验证通过，为避免伪造数据，"
                       "本次调用显式失败。请检查 Key 与网络后重试。",
                       stage="account_sync", retryable=True, provider="tikhub",
                       details={"charged": False})


def get_social_provider():
    return DemoSocialProvider() if config.DEMO_MODE else TikHubProvider()


# ================= LLM =================

class DemoLLMProvider:
    name = "demo_llm"

    # ---- 工作总台指令解析（规则路由，非大模型） ----
    def parse_command(self, text: str) -> dict:
        if any(k in text for k in ("提取", "队列")):
            return {"intent": "ADD_SIGNAL_TO_EXTRACTION",
                    "tools": ["radar.get_signal(#SG-2031)", "extraction.create_plan"],
                    "reply": "该动作涉及收费字幕接口，并可能下载媒体与 ASR。我已生成执行计划草稿，"
                             "<b>需要你确认后才会执行</b>——我不会在对话里替你跑完整个流程。",
                    "actions": [{"label": "查看执行计划（¥0.10）", "kind": "modal", "target": "plan", "primary": True},
                                {"label": "取消", "kind": "toast", "target": "已取消草稿，无任何调用发生"}]}
        if any(k in text for k in ("排期", "计划", "时间")):
            return {"intent": "ADJUST_PLAN",
                    "tools": ["planning.capacity", "planning.suggest"],
                    "reply": "本周剩余可投入 <b>6.5h</b>，当前已排 3 条（7.5h）。建议：保持 4 条目标，"
                             "将「述职季」选题插队会挤占<b>周六拍摄任务</b>。确认前不会改动任何排期。",
                    "actions": [{"label": "去内容计划查看", "kind": "page", "target": "plan", "primary": True}]}
        if "访谈" in text:
            return {"intent": "START_PROFILE_INTERVIEW",
                    "tools": ["profile.resume_session(#8)"],
                    "reply": "已定位到暂停的会话 #8「表达风格」（第 5 问）。访谈结论仍需逐项确认才会写入正式档案，"
                             "快速指令不会直接修改档案。",
                    "actions": [{"label": "继续访谈", "kind": "page", "target": "profile", "primary": True}]}
        if "复盘" in text:
            return {"intent": "START_RETRO", "tools": ["retro.due_list"],
                    "reply": "当前 2 条到达复盘时间：《我劝你别急着做副业》（T+7，数据已回收）、"
                             "《5 个高效工具》（低于均值 60%）。建议先复盘前者——它可能验证一条新规则。",
                    "actions": [{"label": "开始复盘", "kind": "page", "target": "retro", "primary": True}]}
        return {"intent": "SHOW_STATUS",
                "tools": ["workbench.status", "radar.top_signals"],
                "reply": "今天建议按此顺序处理：<br><b>1.</b> 修复小红书同步（401 · 数据源中断会阻塞跨平台评估）"
                         "<br><b>2.</b> 确认对标爆款《汇报总被打断》的提取计划（相关度 92%）"
                         "<br><b>3.</b> 复盘《副业》T+7（可能验证「反焦虑立场」规则）",
                "actions": [{"label": "查看账号", "kind": "page", "target": "account"},
                            {"label": "去提取", "kind": "page", "target": "extraction", "primary": True},
                            {"label": "开始复盘", "kind": "page", "target": "retro"}]}

    # ---- IP 访谈操盘手 ----
    INTERVIEW_SCRIPTS = {
        "表达风格": [
            "你写完一条稿子，通常会删掉什么？",
            "删完之后，一句话大概多少字？举个你最近改过的例子。",
            "你开头习惯怎么写？先给结论还是先讲故事？",
            "有没有你绝对不会用的词或说法？为什么？",
            "你结尾一般怎么收？会给行动清单吗？",
            "你称呼你的读者一般用什么词？",
            "如果把你文章里的口头禅挑出来，你觉得是哪句？",
            "你觉得自己情绪表达偏强还是偏克制？给个例子。",
            "你论证一个观点时，习惯先讲什么再讲什么？",
            "你的案例一般从哪里来？亲身经历还是观察？",
            "你希望自己的内容读起来像什么场景下的对话？",
            "最后一个问题：如果只能保留一个表达特征，你保留哪个？",
        ],
        "完整建档": [
            "先介绍一下你自己：你现在的职业身份和正在做的内容方向？",
            "你过去几年最重要的三段经历是什么？分别给你留下了什么能力？",
            "你的内容主要给谁看？他们现在最卡的问题是什么？",
            "你和同领域创作者最大的不同是什么？",
            "有什么观点是你在公开场合反复表达的？",
            "你的内容最终希望引导用户做什么？（关注/咨询/购买）",
        ],
    }

    def interview_opening(self, topic: str) -> str:
        script = self.INTERVIEW_SCRIPTS.get(topic, self.INTERVIEW_SCRIPTS["完整建档"])
        return (f"我们开始「{topic}」访谈。一次只问一个问题，你可以随时暂停。"
                f"第一个问题：{script[0]}")

    def interview_next(self, topic: str, idx: int, last_answer: str) -> str | None:
        script = self.INTERVIEW_SCRIPTS.get(topic, self.INTERVIEW_SCRIPTS["完整建档"])
        if idx >= len(script):
            return None
        follow = ""
        if last_answer and len(last_answer) < 12:
            follow = "（可以再具体一点吗？最好给一个真实例子。）"
        return script[idx] + follow

    def interview_extract(self, topic: str, turns: list[dict]) -> list[dict]:
        """从访谈轮次提取字段候选（演示：确定性规则）。"""
        cands = []
        answers = [t["text"] for t in turns if t["role"] == "user"]
        joined = " ".join(answers)
        if "删" in joined or "形容词" in joined:
            cands.append({"field_path": "表达习惯",
                          "value": "初稿先删形容词，句长压到 15 字以内",
                          "inference_type": "explicit", "confidence": 0.92,
                          "evidence": f"访谈第 2 轮 · “{answers[0][:40]}”"})
        if "家人们" in joined or "宝子" in joined:
            cands.append({"field_path": "禁用表达",
                          "value": "「家人们」「宝子们」",
                          "inference_type": "explicit", "confidence": 0.95,
                          "evidence": "访谈原文明确表述"})
        cands.append({"field_path": "情绪强度",
                      "value": "中等偏克制（模型推断）",
                      "inference_type": "inferred", "confidence": 0.55,
                      "evidence": f"基于 {len(answers)} 轮回答的用词统计 · 需人工确认"})
        return cands

    # ---- 内容拆解 ----
    def analyze(self, source_title: str, transcript: str = "",
                context: dict | None = None) -> dict:
        return {
            "topic": f"《{source_title}》核心议题：表层是技巧，底层是结构顺序问题（演示拆解）",
            "target_audience": ["需频繁向上汇报的职场中层"],
            "user_problem": "表达没重点，对方听不下去",
            "hook": {"text": "反常识归因开头：问题不在对方，而在顺序。", "cite": "00:00-00:08",
                     "mechanism": "反常识归因"},
            "structure": [
                {"name": "钩子", "span": "0-8s", "weight": 8, "color": "#E4572E"},
                {"name": "痛点共鸣", "span": "8-30s", "weight": 22, "color": "#F08A4B"},
                {"name": "归因翻转", "span": "30-50s", "weight": 20, "color": "#D97706"},
                {"name": "方法主体", "span": "50-120s", "weight": 70, "color": "#2E6FDB"},
                {"name": "案例", "span": "120-150s", "weight": 30, "color": "#6E56CF"},
                {"name": "CTA", "span": "150-170s", "weight": 20, "color": "#57564F"},
            ],
            "key_turns": [{"text": "问题不在讲太多，而在顺序错了。", "cite": "00:47"}],
            "evidence": ["2 个匿名真实场景（口述案例，无数据引用）"],
            "emotional_mechanisms": ["焦虑 → 释然 → 掌控感"],
            "golden_quotes": [{"text": "汇报不是邀功，是降低对方的决策成本。", "cite": "01:58"}],
            "cta": "评论区领取模板 + 提问式收尾",
            "comment_triggers": ["提问式收尾", "模板钩子"],
            "reusable": ["归因翻转结构", "模板型 CTA", "痛点具象化写法"],
            "non_reusable": ["对方个人案例与语气词", "逐字框架（与既有内容重复度 41%）"],
            "model": "demo_analysis_model", "prompt_version": "analysis_v5",
        }

    # ---- 创作 ----
    def diff_directions(self, title: str, context: dict | None = None) -> list[dict]:
        return [
            {"type": "同问题 · 不同观点", "title": "“汇报被打断是好事”",
             "note": "反转立场：说明领导在听，怕的是领导不问。风险：立场与你的代表观点冲突。",
             "cls": "b-info"},
            {"type": "同结构 · 不同案例", "title": "用我自己的 3 个咨询翻车案例",
             "note": "借用归因翻转结构，案例全部来自你的咨询记录 #C-31 #C-42，事实可引用 ✓",
             "cls": "b-pri"},
            {"type": "相邻问题延伸", "title": "从口头汇报延伸到书面汇报",
             "note": "“汇报被打断”的姐妹篇：“周报写了一堆，领导已读不回”。可接续历史回升作品。",
             "cls": "b-vio"},
        ]

    def generate_outline(self, title: str, direction: str,
                         context: dict | None = None) -> list[str]:
        return ["① 钩子：被打断是顺序问题", "② 我的咨询翻车案例",
                "③ 正确顺序：结论-风险-诉求", "④ 对照改写演示", "⑤ 清单 + 模板 CTA"]

    def generate_draft(self, title: str, direction: str,
                       context: dict | None = None, platform: str = "抖音口播") -> str:
        return (f"{title}——先说结论：大多数表达问题不是内容问题，是顺序问题。\n\n"
                f"我上个月接一个咨询，对方准备了 12 页材料，从背景开始讲。讲到第 3 页，"
                f"领导说：所以你到底要说什么？\n\n说白了，听汇报的人脑子里只有三件事："
                f"结论是什么、风险是什么、要我做什么。按这个顺序讲，30 秒就能过。\n\n"
                f"最后给你一张清单：① 结论先行 ② 风险前置 ③ 诉求明确 ④ 背景放最后。"
                f"（演示初稿 · 差异化方向：{direction}）")


class RealLLMProvider:
    """真实 LLM 适配器：OpenAI 兼容协议。未配置即 fail-fast；失败分类翻译，不自动换模型。"""
    name = "openai_compatible"
    INTERVIEW_SCRIPTS = DemoLLMProvider.INTERVIEW_SCRIPTS

    def __init__(self):
        self.api_key = get_setting("llm", "api_key")
        self.base_url = get_setting("llm", "api_base").rstrip("/")
        self.auth_mode = get_setting("llm", "auth_mode", "bearer")
        self.allow_private_network = bool(get_setting("llm", "allow_private_network", False))
        self.last_usage: dict = {}
        self.last_model = ""
        self.last_request_id = ""

    def _require(self):
        if not self.base_url:
            raise AppError("LLM_CONFIG_INCOMPLETE", "请先填写模型 API Base URL。",
                           stage="provider_config", provider=self.name)
        if self.auth_mode == "bearer" and not self.api_key:
            raise config_missing_api_key("大模型")
        _validate_llm_url(self.base_url, allow_private=self.allow_private_network)

    def _headers(self) -> dict:
        headers = {"Content-Type": "application/json"}
        if self.auth_mode == "bearer" and self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers

    def _endpoint(self, path: str) -> str:
        return f"{self.base_url}/{path.lstrip('/')}"

    def _request_error(self, response, model: str = "") -> None:
        if response.status_code in (401, 403):
            raise AppError("LLM_UNAUTHORIZED", "大模型 API Key 无效或没有该模型权限",
                           stage="llm_call", provider=self.name,
                           details={"charged": False, "http_status": response.status_code})
        if response.status_code in (402, 429):
            raise AppError("LLM_RATE_LIMITED", "大模型余额不足或请求过频，请稍后再试",
                           stage="llm_call", retryable=True, provider=self.name,
                           details={"http_status": response.status_code})
        if response.status_code != 200:
            raise AppError("LLM_PROVIDER_ERROR",
                           f"大模型返回异常（HTTP {response.status_code}）",
                           stage="llm_call", retryable=True, provider=self.name,
                           details={"http_status": response.status_code, "model": model})

    def chat(self, model: str, system: str, user: str, max_tokens: int = 2000) -> str:
        import httpx
        self._require()
        try:
            _validate_llm_url(self.base_url, allow_private=self.allow_private_network,
                              resolve_dns=True)
            with httpx.Client(timeout=60.0, follow_redirects=False) as client:
                resp = client.post(
                    self._endpoint("chat/completions"),
                    headers=self._headers(),
                    json={"model": model, "max_tokens": max_tokens,
                          "messages": [{"role": "system", "content": system},
                                       {"role": "user", "content": user}]},
                )
        except httpx.TimeoutException:
            raise llm_unavailable(model)
        except httpx.HTTPError as e:
            raise AppError("LLM_PROVIDER_UNAVAILABLE",
                           f"模型服务连接失败：{e.__class__.__name__}",
                           stage="llm_call", retryable=True, provider="openai_compatible")
        self._request_error(resp, model)
        try:
            data = resp.json()
        except ValueError:
            raise AppError("LLM_OUTPUT_INVALID", "大模型返回了非 JSON 内容",
                           stage="llm_parse", provider=self.name)
        try:
            self.last_usage = data.get("usage") or {}
            self.last_model = str(data.get("model") or model)
            self.last_request_id = resp.headers.get("x-request-id", "")
            return data["choices"][0]["message"]["content"]
        except (KeyError, IndexError):
            raise AppError("LLM_OUTPUT_INVALID", "大模型返回结构不符合 OpenAI 协议",
                           stage="llm_parse", provider="openai_compatible")

    def list_models(self) -> list[str]:
        import httpx
        self._require()
        try:
            _validate_llm_url(self.base_url, allow_private=self.allow_private_network,
                              resolve_dns=True)
            with httpx.Client(timeout=30.0, follow_redirects=False) as client:
                response = client.get(self._endpoint("models"), headers=self._headers())
        except httpx.TimeoutException:
            raise AppError("LLM_PROVIDER_UNAVAILABLE", "读取模型列表超时。",
                           stage="llm_models", retryable=True, provider=self.name)
        except httpx.HTTPError as exc:
            raise AppError("LLM_PROVIDER_UNAVAILABLE",
                           f"模型服务连接失败：{exc.__class__.__name__}",
                           stage="llm_models", retryable=True, provider=self.name)
        self._request_error(response)
        try:
            payload = response.json()
            models = [str(item["id"]) for item in payload.get("data", [])
                      if isinstance(item, dict) and item.get("id")]
        except (ValueError, AttributeError):
            models = []
        if not models:
            raise AppError("LLM_MODELS_UNAVAILABLE",
                           "服务已响应，但没有返回 OpenAI 兼容的模型列表；仍可手动填写模型名称。",
                           stage="llm_models", provider=self.name)
        return sorted(set(models), key=str.lower)

    def check_connection(self, model: str | None = None) -> dict:
        selected = (model or get_setting("llm", "fast_model")).strip()
        if not selected:
            raise AppError("LLM_CONFIG_INCOMPLETE", "请先填写用于测试的模型名称。",
                           stage="provider_config", provider=self.name)
        text = self.chat(selected, "You are a connection check.",
                         "Reply with exactly: OK", max_tokens=16)
        return {"provider": self.name, "model": selected,
                "reachable": True, "response_present": bool(text.strip())}

    def _json(self, model_key: str, system: str, user: str):
        model = get_setting("llm", model_key)
        text = self.chat(model, system + " 只输出合法 JSON，不要使用 Markdown 代码块。", user)
        stripped = text.strip()
        if stripped.startswith("```") and stripped.endswith("```"):
            lines = stripped.splitlines()
            stripped = "\n".join(lines[1:-1]).strip()
        try:
            return json.loads(stripped)
        except json.JSONDecodeError:
            decoder = json.JSONDecoder()
            for index, char in enumerate(stripped):
                if char not in "[{":
                    continue
                try:
                    value, _end = decoder.raw_decode(stripped[index:])
                    return value
                except json.JSONDecodeError:
                    continue
            raise AppError("LLM_OUTPUT_INVALID", "大模型输出不是合法 JSON，结果未写入。",
                           stage="schema_validate", provider=self.name, retryable=True)

    def parse_command(self, text: str) -> dict:
        result = self._json(
            "fast_model",
            "你是工作台指令路由器，输出 intent、tools、reply、actions 四个字段。",
            f"用户指令：{text}",
        )
        if not isinstance(result, dict) or not isinstance(result.get("actions", []), list):
            raise AppError("LLM_OUTPUT_INVALID", "指令解析结果不符合业务结构。",
                           stage="schema_validate", provider=self.name)
        return result

    def interview_opening(self, topic: str) -> str:
        script = self.INTERVIEW_SCRIPTS.get(topic, self.INTERVIEW_SCRIPTS["完整建档"])
        return f"我们开始「{topic}」访谈。一次只问一个问题，你可以随时暂停。第一个问题：{script[0]}"

    def interview_next(self, topic: str, idx: int, last_answer: str) -> str | None:
        script = self.INTERVIEW_SCRIPTS.get(topic, self.INTERVIEW_SCRIPTS["完整建档"])
        return None if idx >= len(script) else script[idx]

    def interview_extract(self, topic: str, turns: list[dict]) -> list[dict]:
        result = self._json(
            "structured_model",
            "从访谈中提取字段候选。输出 JSON 数组；每项含 field_path、value、inference_type、confidence、evidence。",
            json.dumps({"topic": topic, "turns": turns}, ensure_ascii=False),
        )
        if not isinstance(result, list):
            raise AppError("LLM_OUTPUT_INVALID", "访谈提取结果必须是数组。",
                           stage="schema_validate", provider=self.name)
        return result

    def analyze(self, source_title: str, transcript: str = "",
                context: dict | None = None) -> dict:
        result = self._json(
            "analysis_model",
            "拆解内容结构并输出对象。字段必须包含：topic 字符串；target_audience 字符串数组；"
            "user_problem 字符串；hook 对象(text,cite,mechanism)；structure 数组，每项含"
            "name,span,weight,color；key_turns 数组；evidence 字符串数组；"
            "emotional_mechanisms 字符串数组；golden_quotes 数组；cta 字符串；"
            "comment_triggers、reusable、non_reusable 均为字符串数组。没有证据的字段用空值，"
            "不得虚构时间戳、数据、案例或引语。",
            json.dumps({"title": source_title, "transcript": transcript,
                        "context": context or {}}, ensure_ascii=False),
        )
        if not isinstance(result, dict):
            raise AppError("LLM_OUTPUT_INVALID", "内容拆解结果必须是对象。",
                           stage="schema_validate", provider=self.name)
        def list_value(*keys):
            for key in keys:
                value = result.get(key)
                if isinstance(value, list):
                    return value
            return []

        raw_structure = list_value("structure", "content_structure")
        palette = ["#E4572E", "#F08A4B", "#D97706", "#2E6FDB", "#6E56CF", "#57564F"]
        structure = []
        for index, item in enumerate(raw_structure):
            if isinstance(item, str):
                item = {"name": item}
            if not isinstance(item, dict):
                continue
            try:
                weight = max(1, int(float(item.get("weight") or 1)))
            except (TypeError, ValueError):
                weight = 1
            structure.append({
                "name": str(item.get("name") or item.get("section") or item.get("stage") or f"结构 {index + 1}"),
                "span": str(item.get("span") or item.get("time") or "未标注"),
                "weight": weight,
                "color": str(item.get("color") or palette[index % len(palette)]),
            })
        normalized = {
            "topic": str(result.get("topic") or source_title),
            "target_audience": list_value("target_audience", "audience"),
            "user_problem": str(result.get("user_problem") or ""),
            "hook": result.get("hook") if isinstance(result.get("hook"), dict) else None,
            "structure": structure,
            "key_turns": list_value("key_turns"),
            "evidence": list_value("evidence"),
            "emotional_mechanisms": list_value("emotional_mechanisms"),
            "golden_quotes": list_value("golden_quotes"),
            "cta": str(result.get("cta") or ""),
            "comment_triggers": list_value("comment_triggers", "audience_questions"),
            "reusable": list_value("reusable", "reusable_parts"),
            "non_reusable": list_value("non_reusable", "non_reusable_parts"),
            "model": get_setting("llm", "analysis_model"),
            "prompt_version": "analysis_v6_schema_normalized",
            "provider_raw_keys": sorted(result.keys()),
        }
        return normalized

    def diff_directions(self, title: str, context: dict | None = None) -> list[dict]:
        result = self._json(
            "writing_model",
            "为选题生成三个差异化方向。输出数组；每项含 type、title、note、cls。",
            json.dumps({"title": title, "context": context or {}}, ensure_ascii=False),
        )
        if not isinstance(result, list) or len(result) < 3:
            raise AppError("LLM_OUTPUT_INVALID", "差异化方向不足三项。",
                           stage="schema_validate", provider=self.name)
        return result

    def generate_outline(self, title: str, direction: str,
                         context: dict | None = None) -> list[str]:
        result = self._json(
            "writing_model",
            "为短视频文案生成五步提纲。只输出 JSON 字符串数组。",
            json.dumps({"title": title, "direction": direction,
                        "context": context or {}}, ensure_ascii=False),
        )
        if not isinstance(result, list) or len(result) < 3 or not all(isinstance(x, str) for x in result):
            raise AppError("LLM_OUTPUT_INVALID", "内容提纲必须是至少三项的字符串数组。",
                           stage="schema_validate", provider=self.name)
        return result

    def generate_draft(self, title: str, direction: str,
                       context: dict | None = None, platform: str = "抖音口播") -> str:
        return self.chat(
            get_setting("llm", "writing_model"),
            "你是中文短视频文案编辑。不得虚构用户经历、数据或来源。",
            json.dumps({"title": title, "direction": direction, "platform": platform,
                        "context": context or {},
                        "instruction": "生成一版可继续编辑的初稿，不得虚构事实。"},
                       ensure_ascii=False),
        )


def _validate_llm_url(url: str, allow_private: bool = False,
                      resolve_dns: bool = False) -> None:
    parsed = urllib.parse.urlparse(url)
    host = (parsed.hostname or "").lower()
    if (not host or parsed.username or parsed.password
            or parsed.scheme not in ({"http", "https"} if allow_private else {"https"})):
        message = ("模型地址必须是合法 HTTPS URL。"
                   if not allow_private else "模型地址必须是合法 HTTP/HTTPS URL。")
        raise AppError("LLM_URL_BLOCKED", message, stage="provider_config",
                       details={"host": host})
    try:
        addresses = [host] if ipaddress.ip_address(host) else []
    except ValueError:
        addresses = []
    if resolve_dns:
        try:
            port = parsed.port or (443 if parsed.scheme == "https" else 80)
            addresses = {item[4][0] for item in socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)}
        except socket.gaierror as exc:
            raise AppError("LLM_PROVIDER_UNAVAILABLE", "模型域名无法解析。",
                           stage="llm_call", retryable=True,
                           details={"reason": exc.__class__.__name__})
    for address in addresses:
        ip = ipaddress.ip_address(address)
        if (not allow_private and (ip.is_private or ip.is_loopback or ip.is_link_local
                or ip.is_reserved or ip.is_multicast or ip.is_unspecified)):
            raise AppError("LLM_URL_BLOCKED", "模型地址解析到了本机或私有网络。",
                           stage="provider_config", details={"address": address})


def get_llm_provider():
    return DemoLLMProvider() if config.DEMO_MODE else RealLLMProvider()


# ================= STT =================

class DemoSTTProvider:
    name = "demo_stt"

    def transcribe(self, audio_bytes: bytes, context: str = "",
                   mime_type: str = "audio/wav") -> dict:
        return {"text": "（演示转写）我一般都先删形容词，把句子压到十五个字以内，"
                        "比如「本质上来说」直接改成「说白了」。",
                "model": "demo_faster_whisper_small", "cost": 0.0,
                "demo": True}


class RealSTTProvider:
    """Aliyun Model Studio Qwen ASR through its OpenAI-compatible API."""
    name = "aliyun_bailian_asr"

    def __init__(self):
        self.api_key = get_setting("voice", "api_key")
        self.base_url = str(get_setting(
            "voice", "api_base",
            "https://dashscope.aliyuncs.com/compatible-mode/v1",
        )).rstrip("/")
        self.model = str(get_setting("voice", "asr_model", "qwen3-asr-flash"))
        self.language = str(get_setting("voice", "language", "zh"))

    def _require(self) -> None:
        if get_setting("voice", "stt_provider", "aliyun_bailian") != "aliyun_bailian":
            raise AppError(
                "STT_PROVIDER_UNSUPPORTED", "当前真实模式只实现了阿里云百炼语音识别。",
                stage="provider_config", provider=self.name,
            )
        if not self.api_key:
            raise config_missing_api_key("阿里云百炼 ASR")
        if not self.base_url or not self.model:
            raise AppError("STT_CONFIG_INCOMPLETE", "请先填写 ASR API 地址和模型名称。",
                           stage="provider_config", provider=self.name)
        _validate_llm_url(self.base_url, allow_private=False)

    def transcribe(self, audio_bytes: bytes, context: str = "",
                   mime_type: str = "audio/wav") -> dict:
        import httpx
        self._require()
        if not audio_bytes:
            raise AppError("AUDIO_EMPTY", "上传的音频为空。", stage="transcribe",
                           provider=self.name, http_status=422)
        if len(audio_bytes) > config.MAX_AUDIO_UPLOAD_BYTES:
            raise AppError("AUDIO_TOO_LARGE", "音频不能超过 10 MB。", stage="transcribe",
                           provider=self.name, http_status=413,
                           details={"limit_bytes": config.MAX_AUDIO_UPLOAD_BYTES})
        audio_url = f"data:{mime_type};base64,{base64.b64encode(audio_bytes).decode('ascii')}"
        # Qwen ASR's compatible endpoint accepts an audio-only user message.
        # Keep `context` for adapter compatibility, but never send unsupported
        # system/text content that would make a valid audio request fail.
        messages = [{"role": "user", "content": [
            {"type": "input_audio", "input_audio": {"data": audio_url}},
        ]}]
        payload = {
            "model": self.model,
            "messages": messages,
            "asr_options": {"language": self.language, "enable_itn": True},
        }
        try:
            _validate_llm_url(self.base_url, resolve_dns=True)
            with httpx.Client(timeout=120.0, follow_redirects=False) as client:
                response = client.post(
                    f"{self.base_url}/chat/completions",
                    headers={"Authorization": f"Bearer {self.api_key}",
                             "Content-Type": "application/json"},
                    json=payload,
                )
        except httpx.TimeoutException:
            raise AppError("STT_PROVIDER_UNAVAILABLE", "语音识别请求超时。",
                           stage="transcribe", retryable=True, provider=self.name)
        except httpx.HTTPError as exc:
            raise AppError("STT_PROVIDER_UNAVAILABLE",
                           f"语音识别连接失败：{exc.__class__.__name__}",
                           stage="transcribe", retryable=True, provider=self.name)
        if response.status_code in (401, 403):
            raise AppError("STT_UNAUTHORIZED", "阿里云百炼 API Key 无效或无模型权限。",
                           stage="transcribe", provider=self.name,
                           details={"http_status": response.status_code})
        if response.status_code in (402, 429):
            raise AppError("STT_RATE_LIMITED", "语音识别余额不足或请求过于频繁。",
                           stage="transcribe", retryable=True, provider=self.name,
                           details={"http_status": response.status_code})
        if response.status_code != 200:
            provider_message = ""
            try:
                error_body = response.json()
                provider_message = str((error_body.get("error") or {}).get("message") or "")[:500]
            except (ValueError, AttributeError):
                provider_message = ""
            raise AppError("STT_PROVIDER_ERROR",
                           f"语音识别服务返回异常（HTTP {response.status_code}）。",
                           stage="transcribe", retryable=response.status_code >= 500,
                           provider=self.name,
                           details={"http_status": response.status_code,
                                    "provider_message": provider_message})
        try:
            data = response.json()
            text = str(data["choices"][0]["message"]["content"]).strip()
        except (ValueError, KeyError, IndexError, TypeError):
            raise AppError("STT_OUTPUT_INVALID", "语音识别返回结构不符合协议。",
                           stage="transcribe", provider=self.name)
        if not text:
            raise AppError("STT_OUTPUT_EMPTY", "语音识别没有返回文本。",
                           stage="transcribe", provider=self.name)
        usage = data.get("usage") or {}
        return {"text": text, "model": data.get("model") or self.model,
                "cost": 0.0, "demo": False, "usage": usage,
                "provider_request_id": response.headers.get("x-request-id", "")}


def get_stt_provider():
    return DemoSTTProvider() if config.DEMO_MODE else RealSTTProvider()
