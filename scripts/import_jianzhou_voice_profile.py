"""Import user-authorized Jianzhou dialogue voice samples into a new profile version."""
from __future__ import annotations

import requests
import re


BASE = "http://127.0.0.1:8741"


def main() -> int:
    active_response = requests.get(f"{BASE}/api/profile/active", timeout=15)
    active_response.raise_for_status()
    content = active_response.json()["data"]["content"]
    content["spoken_style_rules"] = [
        "偏快节奏，以中短句为主；允许少量‘其实、然后、啊、而已’等口语毛边，不要全部磨成书面语",
        "先给明确判断，再补真实经历、流程或边界；分类之后必须补逻辑桥，不能突然跳结论",
        "讲踩坑时可以直接说‘后面发现根本执行不了’，但不使用毒舌或人身攻击",
        "列流程时保留‘我的输入是什么、规则是什么、输出是什么、最后人怎么样去检查’的本人句法",
        "冷流量开头优先使用画面指向或真实动作，少用抽象评价用户状态的句子",
        "每条只保留少量口头禅，不能机械堆叠‘其实、然后、这是最核心的’",
    ]
    content["voice_samples"] = [
        "我先说结论。",
        "工具它只是辅助，脚本也只是辅助，真正人才是核心。",
        "能简单化的动作就不要变复杂，这是最核心的。",
        "用户要先清晰自己的具体业务是什么，真正想把什么落地。",
        "一条口播视频不是丢给AI，它就能直接剪好的。",
        "我的素材是什么，规则是什么，输出是什么，最后人怎么样去检查？",
        "我以前就是踩过这个坑，想把标准化做得很完整，但后面发现根本执行不了。",
        "不然你只是换了一个更高级的剪辑工具而已，底层还是在靠感觉。",
        "企业AI落地没那么简单，做内容也没那么好拿到结果。",
        "我能接受的人设风格程度就真诚但有判断吧。",
    ]
    # Discard any broken paths inherited from an earlier console-encoding import.
    refs = [str(ref) for ref in content.get("source_refs", []) if str(ref).count("?") < 3]
    content["source_refs"] = refs
    for ref in (
        r"D:\见舟IP操盘\IP实例\见舟AI\02-定位\访谈记录.md",
        r"D:\见舟IP操盘\IP实例\见舟AI\03-人设\原话样本池.md",
        r"D:\见舟IP操盘\IP实例\见舟AI\03-人设\口吻指纹.md",
    ):
        if ref not in refs:
            refs.append(ref)
    sources = content.setdefault("field_sources", {})
    sources["voice_samples"] = {
        "src": "03-人设/原话样本池.md + 02-定位/访谈记录.md",
        "quote": "保留本人原话样本，以学习节奏与逻辑，不机械拼贴。",
    }
    sources["spoken_style_rules"] = {
        "src": "03-人设/口吻指纹.md（本人朗读修正）",
        "quote": "口语句前要保留逻辑桥；不能只学口头句式，忽略上下句承接。",
    }
    status = re.sub(r"\s*\?{5,}\s*", " ", str(content.get("validation_status") or "")).strip()
    note = "已补入对谈原话声纹与本人朗读修正规则；生成结果仍需本人朗读校准。"
    if note not in status:
        status = (status + " " + note).strip()
    content["validation_status"] = status
    response = requests.post(
        f"{BASE}/api/profile/versions",
        json={
            "content": content,
            "reason": "修复编码并补入深度访谈原话声纹、口语逻辑桥和本人朗读修正规则",
            "activate": True,
            "source": "本地IP实例 · 访谈记录 / 原话样本池 / 口吻指纹",
        },
        timeout=20,
    )
    response.raise_for_status()
    result = response.json()["data"]
    check = requests.get(f"{BASE}/api/profile/active", timeout=15).json()["data"]["content"]
    assert check["voice_samples"][2] == "能简单化的动作就不要变复杂，这是最核心的。"
    assert "逻辑桥" in check["spoken_style_rules"][1]
    print(f"{result['version_no']} {result['id']} voice_samples={len(check['voice_samples'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
