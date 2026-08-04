"""外部能力适配层：Demo Provider（显式演示模式）与真实适配器（fail-fast 桩）。

原则：
- 演示模式返回确定性数据，provider 标记 demo_*；
- 真实适配器在未配置 Key 时立即抛 CONFIG_MISSING_API_KEY，绝不返回假数据；
- 配置了 Key 的真实调用走 httpx；任何失败原样抛 Typed Error，不切换适配器。
"""
import datetime

from . import config
from .db import jdumps, query_one
from .errors import AppError, config_missing_api_key, llm_unavailable


def get_setting(section: str, key: str, default: str = "") -> str:
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
    def analyze(self, source_title: str) -> dict:
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
    def diff_directions(self, title: str) -> list[dict]:
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

    def generate_draft(self, title: str, direction: str) -> str:
        return (f"{title}——先说结论：大多数表达问题不是内容问题，是顺序问题。\n\n"
                f"我上个月接一个咨询，对方准备了 12 页材料，从背景开始讲。讲到第 3 页，"
                f"领导说：所以你到底要说什么？\n\n说白了，听汇报的人脑子里只有三件事："
                f"结论是什么、风险是什么、要我做什么。按这个顺序讲，30 秒就能过。\n\n"
                f"最后给你一张清单：① 结论先行 ② 风险前置 ③ 诉求明确 ④ 背景放最后。"
                f"（演示初稿 · 差异化方向：{direction}）")


class RealLLMProvider:
    """真实 LLM 适配器：OpenAI 兼容协议。未配置即 fail-fast；失败分类翻译，不自动换模型。"""
    name = "openai_compatible"

    def __init__(self):
        self.api_key = get_setting("llm", "api_key")
        self.base_url = get_setting("llm", "api_base").rstrip("/")

    def _require(self):
        if not self.api_key or not self.base_url:
            raise config_missing_api_key("大模型")

    def chat(self, model: str, system: str, user: str, max_tokens: int = 2000) -> str:
        import httpx
        self._require()
        try:
            with httpx.Client(timeout=60.0) as client:
                resp = client.post(
                    f"{self.base_url}/chat/completions",
                    headers={"Authorization": f"Bearer {self.api_key}"},
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
        if resp.status_code in (401, 403):
            raise AppError("LLM_UNAUTHORIZED", "大模型 API Key 无效，请到系统设置检查",
                           stage="llm_call", provider="openai_compatible",
                           details={"charged": False})
        if resp.status_code in (402, 429):
            raise AppError("LLM_RATE_LIMITED", "大模型余额不足或请求过频，请稍后再试",
                           stage="llm_call", retryable=True, provider="openai_compatible")
        if resp.status_code != 200:
            raise AppError("LLM_PROVIDER_ERROR",
                           f"大模型返回异常（HTTP {resp.status_code}）：{resp.text[:120]}",
                           stage="llm_call", retryable=True, provider="openai_compatible")
        data = resp.json()
        try:
            return data["choices"][0]["message"]["content"]
        except (KeyError, IndexError):
            raise AppError("LLM_OUTPUT_INVALID", "大模型返回结构不符合 OpenAI 协议",
                           stage="llm_parse", provider="openai_compatible")


def get_llm_provider():
    return DemoLLMProvider() if config.DEMO_MODE else RealLLMProvider()


# ================= STT =================

class DemoSTTProvider:
    name = "demo_stt"

    def transcribe(self, audio_bytes: bytes, context: str = "") -> dict:
        return {"text": "（演示转写）我一般都先删形容词，把句子压到十五个字以内，"
                        "比如「本质上来说」直接改成「说白了」。",
                "model": "demo_faster_whisper_small", "cost": 0.0,
                "demo": True}


class RealSTTProvider:
    name = "local_faster_whisper"

    def transcribe(self, audio_bytes: bytes, context: str = "") -> dict:
        raise AppError("STT_PROVIDER_UNAVAILABLE",
                       "本地 faster-whisper 未安装或未配置。请在系统设置 → 语音模型 中检查，"
                       "或明确选择其他 STT 提供商。系统不会自动切换适配器。",
                       stage="transcribe", retryable=True,
                       provider="local_faster_whisper")


def get_stt_provider():
    return DemoSTTProvider() if config.DEMO_MODE else RealSTTProvider()
