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
import re
import socket
import urllib.parse

from . import config
from .db import jdumps, query_one
from .errors import AppError, config_missing_api_key, llm_unavailable
from .secret_store import get_secret


def _clean_generated_draft(content: str) -> str:
    """Normalize model prose without inventing or rewriting any facts."""
    text = str(content or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    text = re.sub(r"(?m)^\s*```(?:\w+)?\s*$", "", text)
    text = re.sub(r"(?m)^\s*---+\s*$", "", text)
    text = re.sub(r"(?m)^\s{0,3}#{1,6}\s+", "", text)
    text = text.replace("**", "").replace("__", "")
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def get_setting(section: str, key: str, default=""):
    if (section, key) == ("source", "tikhub_api_key"):
        return get_secret("tikhub_api_key") or default
    if (section, key) == ("llm", "api_key"):
        return get_secret("llm_api_key") or default
    if (section, key) == ("voice", "api_key"):
        return get_secret("asr_api_key") or default
    if (section, key) == ("image", "api_key"):
        return get_secret("image_api_key") or default
    if (section, key) == ("wechat_official", "app_secret"):
        return get_secret("wechat_app_secret") or default
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
                       context: dict | None = None, platform: str = "抖音口播",
                       style_mode: str = "light") -> str:
        return (f"{title}——先说结论：大多数表达问题不是内容问题，是顺序问题。\n\n"
                f"我上个月接一个咨询，对方准备了 12 页材料，从背景开始讲。讲到第 3 页，"
                f"领导说：所以你到底要说什么？\n\n说白了，听汇报的人脑子里只有三件事："
                f"结论是什么、风险是什么、要我做什么。按这个顺序讲，30 秒就能过。\n\n"
                f"最后给你一张清单：① 结论先行 ② 风险前置 ③ 诉求明确 ④ 背景放最后。"
                f"（演示初稿 · 差异化方向：{direction}）")

    def assess_draft(self, title: str, content: str,
                     context: dict | None = None) -> dict:
        """可重复的稿件体检；不生成档位，也不写入正式预测。"""
        text = str(content or "").strip()
        length = len(text)
        opening = text[:100]
        ending = text[-140:]
        filler_hits = sum(text.count(item) for item in ("然后", "其实", "我觉得", "相当于"))
        hook_score = 78 if any(mark in opening for mark in ("先说结论", "为什么", "别", "？")) else 56
        structure_score = 76 if 2 <= len([p for p in text.split("\n\n") if p.strip()]) <= 9 else 62
        evidence_score = 68 if any(mark in text for mark in ("我", "上个月", "案例", "来源")) else 52
        action_score = 74 if any(mark in ending for mark in ("清单", "评论", "你可以", "下一步")) else 55
        voice_score = max(52, 78 - filler_hits * 2)
        dimensions = [
            {"key": "hook", "label": "开头停留", "score": hook_score, "weight": 0.24,
             "reason": "检查前100字是否迅速交代冲突或结论", "evidence": opening[:70], "confidence": "medium"},
            {"key": "structure", "label": "结构推进", "score": structure_score, "weight": 0.20,
             "reason": "检查段落是否逐步推进而非平铺", "evidence": f"当前 {len(text.splitlines())} 行", "confidence": "medium"},
            {"key": "evidence", "label": "事实可信度", "score": evidence_score, "weight": 0.20,
             "reason": "检查观点是否有本人事实或可核验依据", "evidence": "演示模式只做文本信号检查", "confidence": "low"},
            {"key": "value", "label": "用户获得感", "score": 72, "weight": 0.16,
             "reason": "正文包含可复用的方法信息", "evidence": title, "confidence": "medium"},
            {"key": "action", "label": "结尾行动", "score": action_score, "weight": 0.10,
             "reason": "检查读者看完后是否知道下一步", "evidence": ending[-80:], "confidence": "medium"},
            {"key": "voice", "label": "表达克制", "score": voice_score, "weight": 0.10,
             "reason": f"检测到常见口头填充词 {filler_hits} 次", "evidence": "确定性文本检查", "confidence": "high"},
        ]
        issues = []
        if hook_score < 70:
            issues.append({"id": "hook_focus", "dimension": "hook", "severity": "high",
                           "quote": opening[:60], "problem": "开头进入主题偏慢，用户利益没有及时出现。",
                           "action": "把结论或冲突提前到第一句。", "example": "先说结论：真正拖慢你的不是工具，而是工作顺序。",
                           "expected_impact": "提升开头停留与主题识别", "side_effect": "过度压缩会损失叙事铺垫",
                           "confidence": "medium"})
        issues.append({"id": "fact_guard", "dimension": "evidence", "severity": "medium",
                       "quote": "稿件中的个人案例与数字", "problem": "事实来源仍需人工核验，AI不能替你补造经历。",
                       "action": "保留真实案例，并补充可追溯的人物、时间或来源。", "example": "把泛称改为你能够确认的真实场景。",
                       "expected_impact": "提升可信度", "side_effect": "不能为了完整度虚构细节",
                       "confidence": "high"})
        if action_score < 70:
            issues.append({"id": "action_clarity", "dimension": "action", "severity": "medium",
                           "quote": ending[-70:], "problem": "结尾缺少明确下一步。",
                           "action": "补一个与正文价值一致的低负担行动。", "example": "先用这张清单改一遍你今天的稿子。",
                           "expected_impact": "提升收藏与执行意愿", "side_effect": "避免强行索取互动",
                           "confidence": "medium"})
        if filler_hits >= 5:
            issues.append({"id": "filler_density", "dimension": "voice", "severity": "low",
                           "quote": "然后 / 其实 / 我觉得 / 相当于", "problem": "口头填充词偏多，影响节奏。",
                           "action": "删除不承担逻辑作用的填充词。", "example": "直接保留结论和因果关系。",
                           "expected_impact": "提升口播紧凑度", "side_effect": "不要把自然口吻全部删掉",
                           "confidence": "high"})
        score = round(sum(item["score"] * item["weight"] for item in dimensions), 1)
        return {"composite_score": score, "dimensions": dimensions, "issues": issues,
                "strengths": ["主题可识别", "已有方法信息", "稿件可继续版本化优化"],
                "summary": f"当前体检 {score} 分；优先处理高严重度问题，再决定是否进入正式预测。",
                "rubric_version": "workspace-quality-v1"}

    def revise_draft(self, title: str, content: str, issues: list[dict],
                     instructions: str = "", context: dict | None = None) -> dict:
        """演示优化也只修改被选择的问题，并保留事实边界。"""
        revised = str(content or "").strip()
        selected = {str(item.get("id")) for item in issues}
        change_log = []
        if "hook_focus" in selected and not revised.startswith("先说结论"):
            revised = "先说结论：这件事真正需要解决的，不是信息不够，而是表达顺序。\n\n" + revised
            change_log.append({"issue_id": "hook_focus", "change": "将结论提前到第一屏",
                               "reason": "缩短进入核心问题的时间"})
        if "filler_density" in selected:
            before = revised
            for word in ("相当于是", "就是说", "我觉得", "其实"):
                revised = revised.replace(word, "")
            if revised != before:
                change_log.append({"issue_id": "filler_density", "change": "删除无逻辑作用的填充词",
                                   "reason": "保持自然口吻同时压缩冗余"})
        if "action_clarity" in selected and not any(x in revised[-120:] for x in ("下一步", "先用", "现在就")):
            revised += "\n\n下一步很简单：先用这张清单改一遍你今天准备发布的内容。"
            change_log.append({"issue_id": "action_clarity", "change": "补充低负担行动",
                               "reason": "让读者知道看完之后可以做什么"})
        if "fact_guard" in selected:
            change_log.append({"issue_id": "fact_guard", "change": "未自动补写任何事实",
                               "reason": "本人经历和数据必须由用户核验后人工补充"})
        instruction_trace = []
        requirement = instructions.strip()
        if requirement:
            if any(word in requirement for word in ("开头", "钩子", "第一句", "结论")):
                evidence = f"先说结论：{title}真正要解决的，是先把判断讲清楚。"
                revised = re.sub(r"^先说结论：[^\n]+\n*", "", revised).lstrip()
                revised = evidence + "\n\n" + revised
            else:
                evidence = "补充判断：" + requirement[:100].rstrip("。") + "。"
                revised = revised.rstrip() + "\n\n" + evidence
            change_log.append({"issue_id": "user_instruction", "change": "已落实本轮补充要求",
                               "reason": requirement[:120], "evidence": evidence})
            instruction_trace.append({"requirement": requirement, "applied": True,
                                      "evidence": evidence, "note": "演示模式确定性落实"})
        if revised == str(content or "").strip():
            revised += "\n\n（本轮仅记录事实核验要求，正文未被AI擅自改写。）"
        return {"content": revised, "change_log": change_log,
                "instruction_trace": instruction_trace,
                "unresolved": [item["id"] for item in issues if item.get("id") == "fact_guard"]}

    def predict_content(self, title: str, content: str, context: dict | None = None) -> dict:
        """可解释的演示预测；只用于演示模式，不冒充真实账号预测结果。"""
        length = len(content.strip())
        hook = 72 if any(mark in content[:80] for mark in ("？", "为什么", "先说结论", "别")) else 58
        clarity = 76 if 180 <= length <= 1400 else 60
        dimensions = [
            {"key": "hook", "label": "开头停留", "score": hook, "weight": 0.20,
             "reason": "前 80 字是否快速建立问题或结论", "evidence": content[:80]},
            {"key": "clarity", "label": "信息清晰度", "score": clarity, "weight": 0.18,
             "reason": f"正文长度 {length} 字，结构可继续人工复核", "evidence": "确定性长度检查"},
            {"key": "value", "label": "用户价值", "score": 68, "weight": 0.18,
             "reason": "包含可执行信息，但演示模式不推断真实受众反应", "evidence": title},
            {"key": "credibility", "label": "可信度", "score": 62, "weight": 0.16,
             "reason": "需要核验事实与本人经历来源", "evidence": "来源证据待用户确认"},
            {"key": "voice", "label": "IP一致性", "score": 70, "weight": 0.14,
             "reason": "已加载当前IP档案；演示评分不代表真实模型结论", "evidence": "当前上下文"},
            {"key": "action", "label": "互动触发", "score": 64, "weight": 0.14,
             "reason": "结尾行动与评论触发仍可增强", "evidence": content[-100:]},
        ]
        score = round(sum(item["score"] * item["weight"] for item in dimensions), 1)
        return {
            "composite_score": score,
            "dimensions": dimensions,
            "bucket_probabilities": {"低于账号基线": 0.18, "接近账号基线": 0.52,
                                     "高于账号基线": 0.25, "明显起量": 0.05},
            "center": {"metric": "相对账号近30条中位表现", "value": 1.0, "unit": "x"},
            "strengths": ["主题明确", "结构可编辑"],
            "risks": ["事实来源仍需人工确认", "冷启动预测缺少足够历史校准样本"],
            "counterfactuals": ["若开头前3秒没有明确冲突，表现可能落到基线以下",
                                "若补入本人真实案例，收藏与信任指标更可能提升"],
            "hypothesis": "明确结论开头并补充本人可核验案例，会优先改善停留与收藏。",
            "basis": "demo_explainable",
        }

    def optimize_visual_prompt(self, payload: dict) -> dict:
        """Deterministic prompt compiler used by demo mode and contract tests."""
        output_kind = str(payload.get("output_kind") or "cover")
        source = str(payload.get("source_text") or "").strip()
        title = str(payload.get("title") or "").strip() or "未命名内容"
        raw = str(payload.get("raw_prompt") or "").strip()
        input_fields = payload.get("fields") if isinstance(payload.get("fields"), dict) else {}
        sentences = [item.strip() for item in re.split(r"[。！？\n]+", source) if item.strip()]
        subtitle = str(input_fields.get("subtitle") or "").strip()
        explicit_features = input_fields.get("features") or []
        if isinstance(explicit_features, str):
            explicit_features = re.split(r"[，,、\n]+", explicit_features)
        features = [str(item).strip()[:10] for item in explicit_features if str(item).strip()][:2]
        if output_kind == "graphic_note" and not subtitle:
            subtitle = (sentences[0][:36] if sentences else str(payload.get("goal") or "").strip())
        if output_kind == "graphic_note":
            optimized = (
                "生成一组信息密度适中、逐页推进的中文知识图文。每页只承担一个信息任务，"
                "正文必须来自给定稿件，不虚构数据；统一品牌色、网格、字体层级与留白。"
                "图片模型仅生成单一焦点的极简无字视觉底图，不生成任何中文、英文、数字、Logo或伪UI，"
                "背景低对比且至少保留60%干净留白，中文由本地排版引擎稳定合成。"
            )
        else:
            optimized = (
                "采用此前AI Skill封面的科技发布会视觉语法：深黑蓝背景、克制蓝光、人物真实半身像在右侧约44%，"
                "左侧2到4行超大白蓝主标题，画面有高级空间层次但不堆叠装饰。画面只保留一个主标题、"
                "一个可选副标题和最多两个短标签；没有短标签就保留留白。人物身份与品牌资产保持一致，"
                "四周保留安全区，不堆叠小字，不呈现模板名、栏目名、内部运营标签或指标。"
            )
        if raw:
            optimized += " 本次补充要求：" + raw
        return {
            "title": title,
            "subtitle": subtitle,
            "features": features,
            "keywords": ["知识IP", "内容创作"],
            "optimized_prompt": optimized,
            "negative_prompt": "错别字，乱码，文字溢出，图片内文字，复杂拼贴，密集小物件，重复人物，伪UI截图，廉价海报，拥挤布局，低清晰度",
            "provider": self.name,
            "model": "demo-visual-planner",
        }

    def plan_graphic_document(self, payload: dict) -> dict:
        source = str(payload.get("source_text") or "").strip()
        title = str(payload.get("title") or "未命名内容").strip()
        count = max(3, min(12, int(payload.get("page_count") or 6)))
        sentences = [item.strip() for item in re.split(r"[。！？\n]+", source) if len(item.strip()) >= 4]
        narrative = str(payload.get("narrative_mode") or "auto")
        if narrative == "auto":
            if any(key in source for key in ("步骤", "教程", "怎么", "如何", "流程")):
                narrative = "tutorial"
            elif any(key in source for key in ("对比", "区别", "还是", "优缺点", "vs")):
                narrative = "comparison"
            elif any(key in source for key in ("案例", "复盘", "经历", "我曾", "实测")):
                narrative = "case_study"
            elif any(key in source for key in ("清单", "检查", "注意", "项")):
                narrative = "checklist"
            else:
                narrative = "insight"
        structures = {
            "tutorial": ["cover", "problem", "steps", "steps", "method", "checklist", "summary", "cta"],
            "insight": ["cover", "problem", "insight", "comparison", "method", "quote", "summary", "cta"],
            "case_study": ["cover", "problem", "case", "insight", "method", "checklist", "summary", "cta"],
            "comparison": ["cover", "problem", "comparison", "comparison", "insight", "method", "summary", "cta"],
            "checklist": ["cover", "problem", "checklist", "checklist", "method", "summary", "cta"],
            "story": ["cover", "problem", "case", "insight", "method", "quote", "summary", "cta"],
        }
        roles = structures.get(narrative, structures["insight"])
        layout_by_role = {"cover": "hero", "steps": "steps", "comparison": "comparison",
                          "checklist": "checklist", "quote": "quote", "summary": "summary",
                          "cta": "summary"}
        pages = []
        for index in range(count):
            if index == 0:
                role = "cover"
            elif index == count - 1:
                role = "cta" if count >= 7 else "summary"
            else:
                role = roles[min(index, len(roles) - 2)]
            excerpt = sentences[min(max(0, index - 1), len(sentences) - 1)] if sentences else title
            pages.append({
                "role": role,
                "title": title if index == 0 else excerpt[:24],
                "body": str(payload.get("goal") or "").strip() if index == 0 else excerpt[:120],
                "bullets": [] if index == 0 else [excerpt[:38]],
                "layout_key": layout_by_role.get(role, "editorial"),
                "visual_prompt": "单一视觉焦点，极简低对比背景，无文字数字，保留至少60%干净留白",
            })
        return {"title": title, "pages": pages, "narrative_mode": narrative,
                "provider": self.name, "model": "demo-visual-planner"}

    def suggest_operation_plan(self, payload: dict) -> dict:
        start = datetime.date.fromisoformat(payload["week_start"])
        desired = int(payload.get("desired_posts") or 4)
        pillars = (payload.get("stable_ip_profile") or {}).get("pillars") or ["核心方法", "实战案例"]
        items = []
        for index in range(desired):
            context = payload.get("operation_context") or {}
            own = ((context.get("own_content") or {}).get("strongest") or [])
            markets = [item for values in (context.get("market_signals") or {}).values() for item in values]
            refs = [item.get("id") for item in ([own[index % len(own)]] if own else [])
                    + ([markets[index % len(markets)]] if markets else []) if item.get("id")]
            items.append({
                "date": (start + datetime.timedelta(days=min(index * 2, 6))).isoformat(),
                "title": f"{pillars[index % len(pillars)]}：本周可验证选题 {index + 1}",
                "platform": "抖音口播" if index % 2 == 0 else "小红书图文",
                "goal": "验证收藏与互动表现",
                "estimate_h": round(float(payload.get("weekly_capacity_hours") or 8) / desired, 1),
                "reason": "结合当前IP内容支柱、自有作品表现和雷达信号生成的演示建议",
                "evidence_ids": refs,
                "evidence_note": "演示模式仅验证证据链结构，不代表真实模型判断",
                "confidence": "低",
            })
        return {"summary": "优先保持稳定发布，再用真实复盘调整选题比例。", "items": items,
                "risks": ["演示模式不代表真实模型判断", "样本不足时不要过度调整IP定位"]}

    def generate_channel_moments(self, payload: dict) -> dict:
        mode = str(payload.get("generation_mode") or "single_campaign")
        if mode == "rolling_mix":
            ideas = payload.get("ideas") or []
            posts = []
            for idea in ideas:
                body = str(idea.get("body") or "").strip()
                if not body:
                    continue
                item_type = str(idea.get("item_type") or "self_idea")
                content_type = {"life_note": "human", "work_process": "process",
                                "trust_evidence": "trust", "feedback": "trust",
                                "hot_opinion": "value"}.get(item_type, "value")
                posts.append({
                    "content_type": content_type, "body": body,
                    "source_refs": [idea.get("id")],
                    "intent": "保留用户本人原始记录并形成可编辑草稿",
                    "visual_suggestion": {
                        "format": "real_photo" if content_type == "human" else
                                  "screenshot" if content_type in {"process", "trust"} else "knowledge_card",
                        "brief": "仅选择与这条本人记录对应的真实照片或截图；没有素材时保持纯文字。",
                        "ratio": "4:3", "reason": "来源可追溯且不虚构经历",
                        "source_hint": "使用这条灵感记录绑定的真实素材",
                    },
                })
                if len(posts) >= int(payload.get("max_posts") or 5):
                    break
            if not posts:
                return {"posts": [], "strategy": "真实素材不足，不生成模拟生活内容",
                        "provider": self.name, "model": "demo-channel-editor"}
            return {"posts": posts, "strategy": "按本人记录滚动编排，不伪造生活与过程",
                    "provider": self.name, "model": "demo-channel-editor"}
        topic = str(payload.get("title") or payload.get("core_topic") or "本周内容")
        source = str(payload.get("source_content") or "").strip()
        insight = (source.split("\n")[0][:90] if source else
                   "真正影响结果的，通常不是工具本身，而是有没有形成可复用的工作流。")
        refs = [str(item) for item in (payload.get("source_chain") or []) if str(item).strip()]
        posts = [
            {"content_type": "value", "body": f"最近在整理「{topic}」。\n\n{insight}\n\n我会把完整方法放进这周的公开内容里。",
             "source_refs": refs,
             "visual_suggestion": {"format": "knowledge_card", "brief": f"把“{topic}”的唯一核心判断做成一张留白充足的知识卡，不放全文。", "ratio": "1:1", "reason": "观点内容适合用一句判断强化记忆", "source_hint": "可直接调用同母题视觉工厂；不需要伪造现场图"}},
            {"content_type": "interaction", "body": f"如果我把「{topic}」整理成一份可以直接照着检查的清单，你更想先看哪一部分？\n\n可以直接告诉我你的具体卡点，我会优先补进下一版。",
             "source_refs": refs,
             "visual_suggestion": {"format": "minimal_card", "brief": "用一个问题加两个简短选项做极简互动卡，避免信息堆叠。", "ratio": "1:1", "reason": "降低回复门槛并聚焦互动问题", "source_hint": "可调用同母题视觉工厂生成"}},
        ]
        return {"posts": posts[:int(payload.get("max_posts") or 2)], "strategy": "单一母题只派生观点与互动，不伪造过程或生活",
                "provider": self.name, "model": "demo-channel-editor"}

    def generate_official_article(self, payload: dict) -> dict:
        title = str(payload.get("title") or payload.get("core_topic") or "一套值得沉淀的方法")
        source = str(payload.get("source_content") or "").strip()
        body = source[:5000] if source else "这篇文章从一个真实问题出发，拆开方法背后的判断、步骤与边界。"
        markdown = (f"# {title}\n\n"
                    f"> 这不是把短内容简单拉长，而是把结论、依据和执行方法重新组织成一篇可复用的长文。\n\n"
                    f"## 先说结论\n\n{body}\n\n"
                    "## 为什么这个问题值得单独讲清楚\n\n"
                    "很多人卡住，不是因为完全不会，而是中间缺少可以重复执行的结构。只看结果，很容易把偶然经验当成方法。\n\n"
                    "## 把它拆成三个动作\n\n"
                    "1. 先明确当前要解决的唯一问题。\n"
                    "2. 再保留能验证判断的来源与证据。\n"
                    "3. 最后把执行结果留档，回到下一轮继续校准。\n\n"
                    "## 最后\n\n"
                    "真正有价值的系统，不是一次生成更多内容，而是让每一次研究、创作和复盘都能成为下一次的起点。")
        return {"title": title, "digest": "把一个选题从短内容扩展为有依据、可执行、可沉淀的深度长文。",
                "outline": ["先说结论", "问题背景", "三个动作", "边界与总结"],
                "markdown": markdown, "provider": self.name, "model": "demo-longform-editor"}

    def review_official_article(self, payload: dict) -> dict:
        content = str(payload.get("markdown") or "")
        issues = []
        if len(content) < 800:
            issues.append({"severity": "medium", "kind": "depth", "message": "正文偏短，深度长文需要补充证据、案例或执行细节。", "action": "补充至少一个来源明确的例子或步骤。"})
        if "演示" in content:
            issues.append({"severity": "high", "kind": "evidence", "message": "正文仍含演示标记，不能进入真实草稿箱。", "action": "替换为真实来源或删除该段。"})
        return {"score": max(0, 92 - len(issues) * 12), "status": "PASS" if not issues else "NEEDS_REVISION",
                "issues": issues, "checks": {"structure": True, "fact_boundary": not any(i["kind"] == "evidence" for i in issues),
                "layout": True, "readability": True}, "provider": self.name, "model": "demo-reviewer"}

    def review_channel_performance(self, payload: dict) -> dict:
        snapshots = payload.get("snapshots") or []
        latest = snapshots[-1] if snapshots else {}
        metrics = latest.get("metrics") or {}
        metric_lines = [f"{key}={value}" for key, value in metrics.items()]
        return {
            "summary": ("已基于当前留存快照形成演示复盘：" + "、".join(metric_lines[:5])
                        if metric_lines else "已有发布记录，但尚无足够的渠道指标。"),
            "verified_findings": [
                {"finding": f"当前最新快照记录了 {key}={value}", "evidence": latest.get("id", "")}
                for key, value in list(metrics.items())[:5]
            ],
            "unverified_hypotheses": ["单次快照不能证明内容结构与表现之间存在因果关系。"],
            "next_actions": ["按相同口径补录下一时间点数据", "样本积累后再比较渠道与内容结构差异"],
            "confidence": "low",
            "provider": self.name,
            "model": "demo-channel-retro",
        }


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
        self.last_finish_reason = ""

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

    def chat(self, model: str, system: str, user: str, max_tokens: int = 2000,
             response_format: dict | None = None) -> str:
        import httpx
        self._require()
        try:
            _validate_llm_url(self.base_url, allow_private=self.allow_private_network,
                              resolve_dns=True)
            with httpx.Client(timeout=60.0, follow_redirects=False) as client:
                payload = {
                    "model": model,
                    "max_tokens": max_tokens,
                    "messages": [{"role": "system", "content": system},
                                 {"role": "user", "content": user}],
                }
                if response_format:
                    payload["response_format"] = response_format
                resp = client.post(
                    self._endpoint("chat/completions"),
                    headers=self._headers(),
                    json=payload,
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
            choice = data["choices"][0]
            self.last_usage = data.get("usage") or {}
            self.last_model = str(data.get("model") or model)
            self.last_request_id = resp.headers.get("x-request-id", "")
            self.last_finish_reason = str(choice.get("finish_reason") or "")
            content = choice["message"].get("content") or ""
            if not str(content).strip():
                raise AppError(
                    "LLM_EMPTY_RESPONSE",
                    "模型请求成功，但没有返回可用正文。请检查模型名称、输出长度或供应商响应。",
                    stage="llm_parse",
                    provider=self.name,
                    details={"model": self.last_model or model,
                             "finish_reason": self.last_finish_reason,
                             "request_id": self.last_request_id},
                )
            return str(content).strip()
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
        # 推理型兼容模型可能先消耗 reasoning tokens；16 token 会出现 HTTP 200、
        # finish_reason=length 但正文为空的假阴性。连接测试仍要求短答，但给足协议余量。
        text = self.chat(selected, "You are a connection check.",
                         "Reply with exactly: OK", max_tokens=256)
        return {"provider": self.name, "model": selected,
                "reachable": True, "response_present": bool(text.strip())}

    def _json(self, model_key: str, system: str, user: str, *,
              json_object: bool = False):
        model = get_setting("llm", model_key)
        text = self.chat(
            model,
            system + " 只输出合法 JSON，不要使用 Markdown 代码块。",
            user,
            max_tokens=8192 if json_object else 2000,
            response_format={"type": "json_object"} if json_object else None,
        )
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
                           stage="schema_validate", provider=self.name, retryable=True,
                           details={"finish_reason": self.last_finish_reason,
                                    "content_empty": not bool(stripped)})

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
            "不得虚构时间戳、数据、案例或引语。"
            "JSON 示例（值仅示意）：{\"topic\":\"\",\"target_audience\":[],"
            "\"user_problem\":\"\",\"hook\":{},\"structure\":[],"
            "\"key_turns\":[],\"evidence\":[],\"emotional_mechanisms\":[],"
            "\"golden_quotes\":[],\"cta\":\"\",\"comment_triggers\":[],"
            "\"reusable\":[],\"non_reusable\":[]}。",
            json.dumps({"title": source_title, "transcript": transcript,
                        "context": context or {}}, ensure_ascii=False),
            json_object=True,
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
            raw_color = str(item.get("color") or "")
            valid_color = (len(raw_color) in (4, 7) and raw_color.startswith("#")
                           and all(ch in "0123456789abcdefABCDEF" for ch in raw_color[1:]))
            structure.append({
                "name": str(item.get("name") or item.get("section") or item.get("stage") or f"结构 {index + 1}"),
                "span": str(item.get("span") or item.get("time") or "未标注"),
                "weight": weight,
                "color": raw_color if valid_color else palette[index % len(palette)],
            })
        def cited_items(*keys):
            items = list_value(*keys)
            output = []
            for item in items:
                if isinstance(item, str) and item.strip():
                    output.append({"text": item.strip(), "cite": "未标注"})
                elif isinstance(item, dict):
                    text = str(item.get("text") or item.get("quote") or
                               item.get("content") or "").strip()
                    if text:
                        output.append({"text": text,
                                       "cite": str(item.get("cite") or item.get("time") or "未标注")})
            return output
        normalized = {
            "topic": str(result.get("topic") or source_title),
            "target_audience": list_value("target_audience", "audience"),
            "user_problem": str(result.get("user_problem") or ""),
            "hook": result.get("hook") if isinstance(result.get("hook"), dict) else None,
            "structure": structure,
            "key_turns": cited_items("key_turns"),
            "evidence": list_value("evidence"),
            "emotional_mechanisms": list_value("emotional_mechanisms"),
            "golden_quotes": cited_items("golden_quotes"),
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
                       context: dict | None = None, platform: str = "抖音口播",
                       style_mode: str = "light") -> str:
        """先保证内容结构，再按可控强度适配本人表达。

        ``structure`` 不注入口头禅；``light`` 只使用已确认的风格规则；
        ``deep`` 才追加少量原话样本。避免把整份访谈当作模仿素材压过内容质量。
        """
        if style_mode not in {"structure", "light", "deep"}:
            style_mode = "light"
        context = context or {}
        raw_content = context.get("content") or {}
        profile = raw_content.get("profile") or context.get("profile") or {}
        source = raw_content.get("source") or {}
        transcript = str(source.get("transcript") or "")
        compact_profile = {
            key: profile.get(key) for key in (
                "ip_name", "positioning", "audience", "key_opinion", "pillars",
                "expression_boundaries", "banned_words",
            ) if profile.get(key)
        }
        compact_source = {
            key: source.get(key) for key in ("id", "title", "platform", "url")
            if source.get(key)
        }
        if transcript:
            compact_source["transcript"] = transcript[:16000]
        payload = {
            "title": title,
            "direction": direction,
            "platform": platform,
            "goal": context.get("_goal") or "",
            "outline": context.get("_outline") or [],
            "source": compact_source,
            "stable_ip": compact_profile,
        }
        system_parts = [
            "你是资深中文内容主编。你的优先级依次是：事实可靠、观点明确、结构有推进、口语自然。",
            "先用一句有信息量的判断或真实场景建立钩子；全文只服务一个核心观点；每一段都必须推进信息。",
            "不得虚构本人经历、数据、客户案例或来源。来源没有提供的事实必须删除或明确标注待补充。",
            "不要输出 Markdown 标题、编辑说明、舞台提示或泛泛的‘大家好’；直接输出可编辑正文。",
            "避免机械复述来源，必须重新组织信息；避免为了像口语而堆叠‘然后、其实、我觉得’。",
            "除非有可核验证据，不使用‘没有之一、唯一、百分百、一定爆’等绝对化宣传；每句话尽量不超过45个汉字。",
        ]
        if platform in {"抖音口播", "视频号口播"}:
            system_parts.append("口播使用中短句和自然停顿，开头80字内交代用户为什么值得继续听。")
        elif platform == "小红书图文":
            system_parts.append("图文使用清晰小段、可扫描要点和具体步骤，但不要写成模板腔。")
        if style_mode in {"light", "deep"}:
            style = (profile.get("expression_style") or [])[:5]
            rules = (profile.get("spoken_style_rules") or [])[:5]
            system_parts.append("以下是经过确认的表达规则，只做二级适配，不能破坏钩子、结构和信息密度："
                                + json.dumps({"style": style, "rules": rules}, ensure_ascii=False))
        if style_mode == "deep":
            samples = (profile.get("voice_samples") or [])[:3]
            system_parts.append("以下原话仅用于学习节奏和句法，禁止照抄或拼贴："
                                + json.dumps(samples, ensure_ascii=False))
        return _clean_generated_draft(self.chat(
            get_setting("llm", "writing_model"),
            "\n".join(system_parts),
            json.dumps(payload, ensure_ascii=False),
            max_tokens=4000,
        ))

    def predict_content(self, title: str, content: str, context: dict | None = None) -> dict:
        result = self._json(
            "analysis_model",
            """你是内容发布前评估助手。根据最终稿、IP档案和已有账号历史做可解释预测。
不得伪造历史表现、百分比或事实；没有数据时必须明确低置信度。输出 JSON 对象，字段必须包含：
composite_score(0-100), dimensions(数组，每项含 key,label,score,weight,reason,evidence),
bucket_probabilities(低于账号基线/接近账号基线/高于账号基线/明显起量，概率合计约1),
center(metric,value,unit), strengths(数组), risks(数组), counterfactuals(数组), hypothesis, basis。
预测只评估相对表现，不承诺结果。""",
            json.dumps({"title": title, "content": content,
                        "context": context or {}}, ensure_ascii=False),
            json_object=True,
        )
        if not isinstance(result, dict) or not isinstance(result.get("dimensions"), list):
            raise AppError("LLM_OUTPUT_INVALID", "预测模型没有返回有效的评分维度。",
                           stage="schema_validate", provider=self.name)
        return result

    def assess_draft(self, title: str, content: str,
                     context: dict | None = None) -> dict:
        result = self._json(
            "analysis_model",
            """你是稿件质量体检助手，不是发布表现预测器。只根据当前稿件、已确认IP档案与来源证据，
给出可重复的发布前质量诊断；禁止输出播放档位、概率分布或承诺结果。
输出JSON对象，必须包含：composite_score(0-100)、rubric_version、summary、strengths数组、
dimensions数组（每项含key,label,score,weight,reason,evidence,confidence）、issues数组。
issues每项必须含id,dimension,severity(high/medium/low),quote,problem,action,example,
expected_impact,side_effect,confidence。问题必须指向稿件中的真实片段；不得虚构事实或用户经历。
评分用于改稿诊断，不得为了提高分数建议夸张、标题党或统一模板腔。""",
            json.dumps({"title": title, "content": content, "context": context or {}}, ensure_ascii=False),
            json_object=True,
        )
        required = {"composite_score", "dimensions", "issues", "strengths", "summary"}
        if not isinstance(result, dict) or not required.issubset(result):
            raise AppError("LLM_OUTPUT_INVALID", "稿件体检结果缺少必要字段。",
                           stage="schema_validate", provider=self.name)
        return result

    def revise_draft(self, title: str, content: str, issues: list[dict],
                     instructions: str = "", context: dict | None = None) -> dict:
        system_prompt = """你是中文内容编辑。根据用户选中的稿件体检问题和本轮补充要求，生成一个新的稿件版本。
必须保持原稿核心观点、事实边界、来源证据与个人身份；禁止虚构案例、数字、经历或引用。
不要机械追求满分，不要把稿件改成统一模板腔；建议有副作用时优先保留内容真实性。
用户填写的instructions是本轮最高优先级编辑要求：只要非空，就必须在新正文中产生可验证变化，不能只把要求抄进change_log。
输出JSON对象：content（完整新稿）、change_log（数组，每项含issue_id,change,reason）、
unresolved（仍需人工处理的问题ID数组）、instruction_trace（数组，每项含requirement、applied、evidence、note）。
instruction_trace中的evidence必须逐字出现在content中，作为用户要求已落实的证据；无法安全落实时applied=false并解释，不能伪造。
若事实不足，应保留原文并在unresolved中说明，不能补造。"""
        payload = {"title": title, "content": content, "selected_issues": issues,
                   "instructions": instructions, "context": context or {}}
        result = None
        for attempt in range(2):
            result = self._json(
                "writing_model", system_prompt,
                json.dumps(payload, ensure_ascii=False), json_object=True,
            )
            if not isinstance(result, dict) or not str(result.get("content") or "").strip():
                raise AppError("LLM_OUTPUT_INVALID", "稿件优化没有返回可用正文。",
                               stage="schema_validate", provider=self.name)
            result["content"] = _clean_generated_draft(result["content"])
            result["change_log"] = result.get("change_log") if isinstance(result.get("change_log"), list) else []
            result["unresolved"] = result.get("unresolved") if isinstance(result.get("unresolved"), list) else []
            traces = result.get("instruction_trace") if isinstance(result.get("instruction_trace"), list) else []
            valid_traces = []
            compact_content = re.sub(r"\s+", "", result["content"])
            for item in traces:
                if not isinstance(item, dict):
                    continue
                evidence = str(item.get("evidence") or "").strip()
                applied = bool(item.get("applied"))
                evidence_found = bool(evidence and re.sub(r"\s+", "", evidence) in compact_content)
                if (applied and evidence_found) or not applied:
                    valid_traces.append({"requirement": str(item.get("requirement") or instructions),
                                         "applied": applied, "evidence": evidence if evidence_found else "",
                                         "note": str(item.get("note") or "")})
            result["instruction_trace"] = valid_traces
            if not instructions.strip() or any(item["applied"] and item["evidence"] for item in valid_traces):
                return result
            if attempt == 0:
                payload["contract_feedback"] = (
                    "上一次没有提供能在新正文中逐字找到的用户要求落实证据。请重新编辑正文，"
                    "并让instruction_trace.evidence逐字出现在content中。"
                )
        raise AppError("LLM_OUTPUT_INVALID", "模型没有把本轮补充要求落实到正文；本次未创建重复版本。",
                       stage="instruction_verify", provider=self.name)

    def suggest_operation_plan(self, payload: dict) -> dict:
        result = self._json(
            "analysis_model",
            """你是创作者运营顾问。根据稳定IP档案、近30/90天真实运营画像、自有作品正文与拆解、
对标内容、平台热点、行业源、用户反馈和真实复盘生成一周建议草案。
不得修改IP核心定位，不得虚构数据，不得把建议写成已经执行。只能引用输入中存在的ID。
输出JSON对象：summary, items(每项含 date,title,platform,goal,estimate_h,reason,
evidence_ids,evidence_note,confidence), risks。
items数量必须等于 desired_posts，日期必须落在 week_start 起7天内。""",
            json.dumps(payload, ensure_ascii=False), json_object=True,
        )
        if not isinstance(result, dict) or not isinstance(result.get("items"), list):
            raise AppError("LLM_OUTPUT_INVALID", "运营顾问没有返回有效计划。",
                           stage="schema_validate", provider=self.name)
        return result

    def generate_channel_moments(self, payload: dict) -> dict:
        mode = str(payload.get("generation_mode") or "single_campaign")
        mode_rule = ("这是滚动私域编排：按输入 ratios 控制整体结构，最多生成 max_posts 条。"
                     "human/process/trust 必须引用 ideas 中对应的本人记录ID；没有真实素材就省略并在 strategy 说明缺口。"
                     if mode == "rolling_mix" else
                     "这是单母题派生：只生成1到2条，聚焦一个判断及互动承接；不得为了凑节奏生成虚假的生活或过程记录。")
        result = self._json(
            "writing_model",
            f"""你是中文个人IP朋友圈编辑。依据输入中真实存在的稿件、本人记录、来源与IP档案生成朋友圈草稿。{mode_rule}
不得虚构本人经历、客户反馈、成绩、聊天记录或当天发生的事情。朋友圈不是短视频标题：每条必须有完整观点或真实过程，语气自然，不写营销海报腔。
优先覆盖 value(价值观点)、process(过程证据)、human(真实感)、trust(可信依据)、interaction(互动承接)，但不得为了凑类型编造素材。
每条同时给出配图判断，不强迫每条配图。visual_suggestion 必须包含 format、brief、ratio、reason、source_hint：format 只能是 real_photo、screenshot、knowledge_card、minimal_card、process_collage、no_image；真实照片/截图只能建议用户选择真实素材，不能假装已经存在。知识卡只保留一个核心判断，不把整段正文塞进图片。
输出JSON对象：strategy、posts。posts每项必须含content_type、body、source_refs、intent、visual_suggestion。source_refs只能使用 allowed_source_refs 或 source_chain 中实际存在的ID。body使用自然段，不使用Markdown标题，默认不超过450字。""",
            json.dumps(payload, ensure_ascii=False), json_object=True,
        )
        posts = result.get("posts") if isinstance(result, dict) else None
        if not isinstance(posts, list) or not posts:
            raise AppError("LLM_OUTPUT_INVALID", "朋友圈生成结果没有可用草稿。",
                           stage="schema_validate", provider=self.name)
        result["provider"] = self.name
        result["model"] = self.last_model or get_setting("llm", "writing_model")
        return result

    def generate_official_article(self, payload: dict) -> dict:
        result = self._json(
            "writing_model",
            """你是中文公众号深度长文主编。把已保存稿件、来源证据、内容拆解和IP档案重新组织为一篇真正有增量的长文，而不是把短稿拉长。
必须保留核心观点和事实边界；不得虚构数据、案例、本人经历、读者反馈或引用。来源不足的地方宁可不写。
文章需要：清晰标题、摘要、开头结论、问题背景、至少两个有推进关系的正文部分、可执行的方法或判断边界、自然结尾。避免模板腔、空洞金句和硬性引流。
输出JSON对象：title、digest、outline、markdown。outline是字符串数组；markdown为完整正文，可使用一级/二级标题、引用、有序/无序列表和加粗。""",
            json.dumps(payload, ensure_ascii=False), json_object=True,
        )
        required = {"title", "digest", "outline", "markdown"}
        if not isinstance(result, dict) or not required.issubset(result) or not str(result.get("markdown") or "").strip():
            raise AppError("LLM_OUTPUT_INVALID", "公众号长文没有返回完整正文。",
                           stage="schema_validate", provider=self.name)
        result["provider"] = self.name
        result["model"] = self.last_model or get_setting("llm", "writing_model")
        return result

    def review_official_article(self, payload: dict) -> dict:
        result = self._json(
            "analysis_model",
            """你是公众号发布前审核编辑。只审核输入正文，不改写正文。检查事实与来源边界、标题承诺、逻辑结构、重复、可读性、公众号排版、合规风险和明显错字。
不得声称已经核验输入之外的事实。输出JSON对象：score(0-100)、status(PASS或NEEDS_REVISION)、issues、checks。
issues每项含severity(high/medium/low)、kind、quote、message、action；checks必须包含structure、fact_boundary、layout、readability、compliance布尔值。""",
            json.dumps(payload, ensure_ascii=False), json_object=True,
        )
        if not isinstance(result, dict) or not isinstance(result.get("issues"), list):
            raise AppError("LLM_OUTPUT_INVALID", "公众号审核结果结构无效。",
                           stage="schema_validate", provider=self.name)
        result["provider"] = self.name
        result["model"] = self.last_model or get_setting("llm", "analysis_model")
        return result

    def review_channel_performance(self, payload: dict) -> dict:
        result = self._json(
            "analysis_model",
            """你是内容渠道复盘分析师。只根据输入中的发布记录和指标快照分析朋友圈或公众号成品，不能读取未来数据，也不能把相关性写成因果。
必须把“可由数据直接验证的发现”和“尚待验证的假设”分开；没有样本基线时明确降低置信度，不得虚构平台均值、行业数据、用户反馈或收入。
输出JSON对象：summary、verified_findings、unverified_hypotheses、next_actions、confidence。
verified_findings每项含finding和evidence（引用输入中的快照ID或发布记录ID）；unverified_hypotheses和next_actions为字符串数组；confidence只能是low/medium/high。""",
            json.dumps(payload, ensure_ascii=False), json_object=True,
        )
        required = {"summary", "verified_findings", "unverified_hypotheses", "next_actions", "confidence"}
        if (not isinstance(result, dict) or not required.issubset(result)
                or not isinstance(result.get("verified_findings"), list)
                or not isinstance(result.get("next_actions"), list)):
            raise AppError("LLM_OUTPUT_INVALID", "渠道复盘没有返回可追溯的分析结果。",
                           stage="schema_validate", provider=self.name)
        result["provider"] = self.name
        result["model"] = self.last_model or get_setting("llm", "analysis_model")
        return result

    def optimize_visual_prompt(self, payload: dict) -> dict:
        result = self._json(
            "writing_model",
            """你是中文知识IP视觉总监。根据已保存稿件、IP档案、品牌资产、平台和用户补充要求，
            为封面或多页图文编译可直接交给图片模型的提示词。不得虚构稿件中不存在的数据、案例或结论。
输出JSON对象，必须包含 title、subtitle、features、keywords、optimized_prompt、negative_prompt。
title/subtitle必须简洁；features和keywords必须是字符串数组；optimized_prompt必须包含比例、信息层级、
            安全区、品牌一致性、人物参考一致性和中文可读性要求。
            当output_kind为cover时，采用高级AI产品发布会/科技知识博主视觉：深黑蓝渐变、克制蓝色轮廓光、
            真人半身像位于右侧40%-46%，左侧2-4行超大白蓝主标题，层次明确、留白充足、人物身份绝对一致。
            title是画面唯一主标题，subtitle可为空，features最多2个且每个不超过10字；没有可靠辅助信息时features返回空数组，禁止凑标签。
            keywords只供视觉语义理解、绝不作为画面文字。禁止输出或建议上画面：观点型/教程型等内容分类，
            市场热点/行业热点等来源标签，收藏率/账号均值等运营目标或指标，任何SG-/CTX-/SC-/VPR-来源编号，
            模板名、栏目名和关键词列表。optimized_prompt只能描述构图、光影、背景、人物和信息层级，
            不得加入title/subtitle/features之外的可见文字；禁止模板名、栏目角标和“AI知识IP”等装饰性文字。
            当output_kind为graphic_note时，图片模型只负责无字视觉底图：单一焦点、低对比、无文字数字Logo、
            无复杂拼贴或伪UI，并为本地中文排版保留至少60%干净留白。""",
            json.dumps(payload, ensure_ascii=False), json_object=True,
        )
        required = {"title", "subtitle", "features", "keywords", "optimized_prompt", "negative_prompt"}
        if not isinstance(result, dict) or not required.issubset(result):
            raise AppError("LLM_OUTPUT_INVALID", "视觉提示词优化结果缺少必要字段。",
                           stage="schema_validate", provider=self.name)
        result["provider"] = self.name
        result["model"] = get_setting("llm", "writing_model")
        return result

    def plan_graphic_document(self, payload: dict) -> dict:
        result = self._json(
            "writing_model",
            """你是中文知识图文主编。把一篇已保存稿件规划成3到12页、逐页推进的图文文档，
            不是生成多张封面。每页只承担一个信息任务，正文和要点必须来自输入稿件，不得虚构。
            narrative_mode表示结构偏好：auto时根据稿件自动判断；tutorial为教程步骤；insight为观点洞察；
            case_study为案例复盘；comparison为对比决策；checklist为清单；story为故事论证。
            不得机械套用cover/problem/insight固定顺序，必须让页面角色与当前稿件的信息任务匹配。
            输出JSON对象：title、pages。pages每项必须含 role、title、body、bullets、layout_key、visual_prompt。
            role只能是 cover/problem/insight/method/steps/case/comparison/checklist/quote/summary/cta；
            layout_key只能是 hero/editorial/steps/comparison/checklist/quote/summary。封面页少文字，正文页保留有价值的解释，
            最后一页总结或行动建议。visual_prompt只描述单一视觉焦点，不要求图片模型书写中文；
            必须明确极简、低对比、无文字数字、保留至少60%干净留白。页数必须等于page_count。""",
            json.dumps(payload, ensure_ascii=False), json_object=True,
        )
        pages = result.get("pages") if isinstance(result, dict) else None
        if not isinstance(pages, list) or not pages:
            raise AppError("LLM_OUTPUT_INVALID", "图文规划结果没有有效页面。",
                           stage="schema_validate", provider=self.name)
        result["provider"] = self.name
        result["model"] = get_setting("llm", "writing_model")
        return result


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
        # Clash/Surge 等 TUN 代理常把公网域名映射到 RFC 2544 的
        # 198.18.0.0/15 Fake-IP。仅对明确的官方模型 API 域名放行该网段；
        # localhost、私网 IP、自定义域名仍按原规则阻断，避免扩大 SSRF 面。
        proxy_fake_ip = ip in ipaddress.ip_network("198.18.0.0/15")
        trusted_public_host = host in {
            "api.deepseek.com",
            "api.openai.com",
            "dashscope.aliyuncs.com",
            "open.bigmodel.cn",
            "api.moonshot.cn",
        }
        if (not allow_private and (ip.is_private or ip.is_loopback or ip.is_link_local
                or ip.is_reserved or ip.is_multicast or ip.is_unspecified)
                and not (proxy_fake_ip and trusted_public_host)):
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
            if "audio is too long" in provider_message.lower():
                raise AppError(
                    "ASR_AUDIO_DURATION_EXCEEDED",
                    "单次音频超过当前 ASR 模型的 5 分钟限制；任务可切分后重试。",
                    stage="transcribe", retryable=True, provider=self.name,
                    details={"http_status": response.status_code,
                             "provider_message": provider_message,
                             "fix": "系统将按不超过 4 分钟自动切分并按顺序合并原文"},
                )
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
