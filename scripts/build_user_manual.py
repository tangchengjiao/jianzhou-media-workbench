"""Build the share-safe Jianzhou Workbench user manual as a polished PDF."""

from __future__ import annotations

import argparse
import html
from pathlib import Path

from PIL import Image as PILImage
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    Image,
    KeepTogether,
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)


ORANGE = colors.HexColor("#F15A2B")
ORANGE_DARK = colors.HexColor("#C9431B")
INK = colors.HexColor("#1F2328")
MUTED = colors.HexColor("#6D716B")
CREAM = colors.HexColor("#F7F4EE")
WHITE = colors.white
LINE = colors.HexColor("#DED9D0")
TEAL = colors.HexColor("#167C80")
TEAL_PALE = colors.HexColor("#E6F3F2")
ORANGE_PALE = colors.HexColor("#FCEAE3")
BLUE_PALE = colors.HexColor("#EAF2FF")
WARN_PALE = colors.HexColor("#FFF3D9")


def register_fonts() -> tuple[str, str]:
    regular_candidates = [
        Path(r"C:\Windows\Fonts\msyh.ttc"),
        Path(r"C:\Windows\Fonts\simhei.ttf"),
        Path("/System/Library/Fonts/PingFang.ttc"),
    ]
    bold_candidates = [
        Path(r"C:\Windows\Fonts\msyhbd.ttc"),
        Path(r"C:\Windows\Fonts\simhei.ttf"),
        Path("/System/Library/Fonts/PingFang.ttc"),
    ]
    regular = next((p for p in regular_candidates if p.exists()), None)
    bold = next((p for p in bold_candidates if p.exists()), regular)
    if not regular:
        raise RuntimeError("No Chinese font found. Install Microsoft YaHei or PingFang.")
    pdfmetrics.registerFont(TTFont("JZRegular", str(regular)))
    pdfmetrics.registerFont(TTFont("JZBold", str(bold)))
    pdfmetrics.registerFontFamily(
        "JZRegular", normal="JZRegular", bold="JZBold", italic="JZRegular", boldItalic="JZBold"
    )
    return "JZRegular", "JZBold"


def build_styles(font: str, bold: str):
    styles = getSampleStyleSheet()
    return {
        "cover_title": ParagraphStyle(
            "cover_title", fontName=bold, fontSize=30, leading=40, textColor=INK,
            alignment=TA_LEFT, spaceAfter=8 * mm,
        ),
        "cover_sub": ParagraphStyle(
            "cover_sub", fontName=font, fontSize=13, leading=22, textColor=MUTED,
            alignment=TA_LEFT,
        ),
        "h1": ParagraphStyle(
            "h1", fontName=bold, fontSize=22, leading=30, textColor=INK,
            spaceAfter=4 * mm,
        ),
        "h2": ParagraphStyle(
            "h2", fontName=bold, fontSize=14, leading=21, textColor=INK,
            spaceBefore=2 * mm, spaceAfter=2 * mm,
        ),
        "body": ParagraphStyle(
            "body", fontName=font, fontSize=9.5, leading=16, textColor=INK,
            spaceAfter=2.4 * mm,
        ),
        "small": ParagraphStyle(
            "small", fontName=font, fontSize=8.2, leading=13, textColor=MUTED,
        ),
        "caption": ParagraphStyle(
            "caption", fontName=font, fontSize=8, leading=12, textColor=MUTED,
            alignment=TA_CENTER, spaceBefore=1.5 * mm, spaceAfter=2.5 * mm,
        ),
        "callout_title": ParagraphStyle(
            "callout_title", fontName=bold, fontSize=10, leading=15, textColor=INK,
        ),
        "callout_body": ParagraphStyle(
            "callout_body", fontName=font, fontSize=9, leading=15, textColor=INK,
        ),
        "table_head": ParagraphStyle(
            "table_head", fontName=bold, fontSize=8.8, leading=13, textColor=WHITE,
            alignment=TA_LEFT,
        ),
        "table": ParagraphStyle(
            "table", fontName=font, fontSize=8.2, leading=13, textColor=INK,
        ),
        "step_num": ParagraphStyle(
            "step_num", fontName=bold, fontSize=16, leading=18, textColor=WHITE,
            alignment=TA_CENTER,
        ),
    }


def p(text: str, style, escape: bool = False) -> Paragraph:
    return Paragraph(html.escape(text) if escape else text, style)


def callout(title: str, body: str, styles, background=TEAL_PALE, accent=TEAL):
    marker = Table(
        [[p("!", styles["step_num"]) ]], colWidths=[10 * mm], rowHeights=[10 * mm],
        style=TableStyle([
            ("BACKGROUND", (0, 0), (-1, -1), accent),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ]),
    )
    content = [p(title, styles["callout_title"]), Spacer(1, 0.8 * mm), p(body, styles["callout_body"])]
    box = Table([[marker, content]], colWidths=[13 * mm, 149 * mm], hAlign="LEFT")
    box.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), background),
        ("BOX", (0, 0), (-1, -1), 0.7, accent),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 4 * mm),
        ("RIGHTPADDING", (0, 0), (-1, -1), 4 * mm),
        ("TOPPADDING", (0, 0), (-1, -1), 4 * mm),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4 * mm),
    ]))
    return box


def steps(items: list[tuple[str, str]], styles):
    rows = []
    for index, (title, body) in enumerate(items, 1):
        badge = Table(
            [[p(str(index), styles["step_num"]) ]], colWidths=[10 * mm], rowHeights=[10 * mm],
            style=TableStyle([
                ("BACKGROUND", (0, 0), (-1, -1), ORANGE),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ]),
        )
        rows.append([badge, [p(title, styles["callout_title"]), p(body, styles["callout_body"]) ]])
    table = Table(rows, colWidths=[14 * mm, 148 * mm], hAlign="LEFT")
    table.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("TOPPADDING", (0, 0), (-1, -1), 3 * mm),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3 * mm),
        ("LINEBELOW", (1, 0), (1, -2), 0.4, LINE),
        ("LEFTPADDING", (0, 0), (-1, -1), 2 * mm),
        ("RIGHTPADDING", (0, 0), (-1, -1), 2 * mm),
    ]))
    return table


def info_table(headers: list[str], rows: list[list[str]], widths, styles):
    data = [[p(h, styles["table_head"]) for h in headers]]
    data.extend([[p(cell, styles["table"]) for cell in row] for row in rows])
    table = Table(data, colWidths=widths, repeatRows=1, hAlign="LEFT")
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), TEAL),
        ("BACKGROUND", (0, 1), (-1, -1), WHITE),
        ("GRID", (0, 0), (-1, -1), 0.45, LINE),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 2.6 * mm),
        ("RIGHTPADDING", (0, 0), (-1, -1), 2.6 * mm),
        ("TOPPADDING", (0, 0), (-1, -1), 2.3 * mm),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 2.3 * mm),
    ]))
    return table


def screenshot(path: Path, caption: str, styles, max_height=94 * mm):
    if not path.exists():
        raise FileNotFoundError(path)
    with PILImage.open(path) as source:
        width, height = source.size
    display_width = 166 * mm
    display_height = display_width * height / width
    if display_height > max_height:
        display_height = max_height
        display_width = display_height * width / height
    image = Image(str(path), width=display_width, height=display_height, hAlign="CENTER")
    image._restrictSize(166 * mm, max_height)
    return KeepTogether([image, p(caption, styles["caption"])])


def add_title(story, number: str, title: str, subtitle: str, styles):
    story.append(p(f'<font color="#F15A2B">{number}</font>&nbsp;&nbsp;{title}', styles["h1"]))
    story.append(p(subtitle, styles["body"]))


def page_background(canvas, doc):
    canvas.saveState()
    width, height = A4
    canvas.setFillColor(CREAM)
    canvas.rect(0, 0, width, height, stroke=0, fill=1)
    canvas.setStrokeColor(LINE)
    canvas.setLineWidth(0.5)
    canvas.line(18 * mm, 14 * mm, width - 18 * mm, 14 * mm)
    canvas.setFillColor(MUTED)
    canvas.setFont("JZRegular", 7.5)
    canvas.drawString(18 * mm, 8.5 * mm, "见舟工作台 v2.1.0 · 当前上下文冻结版 · 2026-08-08")
    canvas.drawRightString(width - 18 * mm, 8.5 * mm, f"{doc.page}")
    canvas.restoreState()


def cover_background(canvas, doc):
    canvas.saveState()
    width, height = A4
    canvas.setFillColor(CREAM)
    canvas.rect(0, 0, width, height, stroke=0, fill=1)
    canvas.setFillColor(ORANGE)
    canvas.rect(0, height - 18 * mm, width, 18 * mm, stroke=0, fill=1)
    canvas.setFillColor(TEAL)
    canvas.circle(width - 26 * mm, 30 * mm, 17 * mm, stroke=0, fill=1)
    canvas.setFillColor(ORANGE_PALE)
    canvas.circle(width - 42 * mm, 48 * mm, 28 * mm, stroke=0, fill=1)
    canvas.setStrokeColor(LINE)
    canvas.line(18 * mm, 14 * mm, width - 18 * mm, 14 * mm)
    canvas.setFillColor(MUTED)
    canvas.setFont("JZRegular", 7.5)
    canvas.drawString(18 * mm, 8.5 * mm, "可分享用户手册 · 不包含任何 API Key 或个人业务数据")
    canvas.drawRightString(width - 18 * mm, 8.5 * mm, "2026.08")
    canvas.restoreState()


def build_manual(output: Path, shots: Path) -> None:
    font, bold = register_fonts()
    styles = build_styles(font, bold)
    output.parent.mkdir(parents=True, exist_ok=True)
    doc = SimpleDocTemplate(
        str(output), pagesize=A4, rightMargin=18 * mm, leftMargin=18 * mm,
        topMargin=18 * mm, bottomMargin=18 * mm, title="见舟工作台完整使用说明书 v2.1.0",
        author="见舟工作台", subject="安装、配置、操作、隐私与故障排查",
    )
    story = []

    story.extend([
        Spacer(1, 34 * mm),
        p('<font color="#F15A2B">见舟工作台</font><br/>完整使用说明书', styles["cover_title"]),
        p("从安装、配置，到内容雷达、提取、拆解、创作、发布与复盘。<br/>面向第一次使用的创作者，也面向需要真实 Provider 的运营者。", styles["cover_sub"]),
        Spacer(1, 14 * mm),
        callout(
            "本手册对应哪个版本？",
            "对应 2026-08-08 当前上下文冻结版 v2.1.0，不自动跟随后续开发分支变化。Windows x64 包已完成全新目录启动验收；macOS 包必须在 macOS 原生环境分别构建和验收。",
            styles, ORANGE_PALE, ORANGE,
        ),
        Spacer(1, 12 * mm),
        p("安全承诺", styles["h2"]),
        p("发布包、说明书和示例截图均不包含作者自己的 API Key、账号数据库、上传文件、日志或浏览器资料。请不要把自己的本地数据目录转发给其他人。", styles["body"]),
        Spacer(1, 18 * mm),
        p("建议阅读顺序：先看第 1—4 章完成启动和配置，再按你的工作流阅读第 5—12 章。", styles["small"]),
        PageBreak(),
    ])

    add_title(story, "01", "3 分钟开始使用", "第一次打开时，只做这四件事。", styles)
    story.append(steps([
        ("解压并启动", "Windows：保留整个发布文件夹，双击 EXE。应用会在本机启动服务并打开浏览器；若浏览器未自动打开，访问 http://127.0.0.1:8741。"),
        ("先留在演示模式", "确认左上角页面正常、导航可点、内容雷达和创作工坊可打开。演示数据会明确标记，不代表真实账号结果。"),
        ("按需配置 Provider", "打开“系统设置”，依次配置数据来源、大模型、图片模型和语音模型。只使用某项能力，就只配置对应服务。"),
        ("逐项真实测试后再切换", "每个 Provider 先点击页面里的“保存并测试”。全部通过后，在“运行模式”切换为真实模式。失败时不要反复提交，先看错误码和 Trace ID。"),
    ], styles))
    story.append(Spacer(1, 4 * mm))
    story.append(callout(
        "重要：EXE 不是云服务",
        "网页地址是本机 127.0.0.1，关闭应用后页面会停止响应。你的业务数据保存在自己的电脑，不会因为把 EXE 分享给别人而自动同步给对方。",
        styles,
    ))
    story.append(PageBreak())

    add_title(story, "02", "认识首页与两种运行模式", "先确认自己看到的是演示数据还是真实 Provider。", styles)
    story.append(screenshot(shots / "01-workbench.png", "图 2-1 · 演示模式首页：紫色“演示数据”徽标必须清晰可见。演示中的异常任务用于展示错误态，不是你自己的账号。", styles, 105 * mm))
    story.append(info_table(
        ["模式", "数据来源", "适合场景", "失败行为"],
        [
            ["演示模式", "内置、明确标识的示例数据", "体验页面和完整交互", "可展示示例错误态"],
            ["真实模式", "你配置的 TikHub / LLM / ASR / 图片服务", "真实同步、分析与创作", "明确失败并保留错误码，不返回假数据"],
        ], [27 * mm, 49 * mm, 42 * mm, 48 * mm], styles,
    ))
    story.append(Spacer(1, 3 * mm))
    story.append(callout("不要混淆", "从演示模式切到真实模式后，未配置的能力会直接报“缺少配置”。这是安全设计，不是系统偷偷失效。", styles, WARN_PALE, ORANGE))
    story.append(PageBreak())

    add_title(story, "03", "第一次配置清单", "不是所有 Key 都必填；按你要使用的链路配置。", styles)
    story.append(info_table(
        ["能力", "在哪里配置", "何时需要", "通过标准"],
        [
            ["公开账号与热榜", "系统设置 → 数据来源", "同步抖音等平台、对标账号、热点", "真实账号测试返回账号名或平台 ID"],
            ["分析与写作", "系统设置 → 大模型", "内容拆解、作者分析、提纲、初稿", "读取模型列表或最小生成测试成功"],
            ["图片生成", "系统设置 → 图片模型", "视觉工厂 AI 背景或封面", "最小图片测试成功"],
            ["音视频转写", "系统设置 → 语音模型", "上传音频、无字幕视频的 ASR", "上传短音频测试成功"],
            ["媒体转换", "FFmpeg（系统级）", "某些音视频格式转换", "系统诊断显示 FFmpeg 可用"],
        ], [35 * mm, 45 * mm, 47 * mm, 39 * mm], styles,
    ))
    story.append(Spacer(1, 5 * mm))
    story.append(callout(
        "密钥怎么保存？",
        "Windows 使用当前系统用户的 DPAPI；macOS 使用登录钥匙串。数据库、备份和设置接口只记录“是否已配置”，不会回传明文。更换电脑时请在新电脑重新填写 Key。",
        styles,
    ))
    story.append(Spacer(1, 4 * mm))
    story.append(p("建议测试顺序", styles["h2"]))
    story.append(p("数据来源 → 大模型 → 语音模型 → 图片模型。每次只改一个分区，保存并测试成功后再继续，出现问题更容易定位。", styles["body"]))
    story.append(PageBreak())

    add_title(story, "04", "配置 TikHub 数据来源", "对标账号、自己的公开作品和平台热榜都依赖这里。", styles)
    story.append(screenshot(shots / "08-settings-data.png", "图 4-1 · 系统设置 → 数据来源。Key 输入框在截图中保持为空。", styles, 102 * mm))
    story.append(steps([
        ("填写 API Base URL", "默认使用 https://api.tikhub.io；大陆网络可按页面提示使用服务商提供的可用域名。不要自行拼接具体接口路径。"),
        ("粘贴自己的 TikHub API Key", "只在自己的电脑中填写。分享截图、日志或数据库前不要暴露 Key。"),
        ("用公开账号做真实测试", "选择平台并填主页链接或平台 ID，再点“保存并真实测试”。只以真实返回为准。"),
        ("再进入账号运营或内容雷达", "测试成功后，添加自己的账号用于运营；添加他人公开账号用于对标。二者的数据语义不同。"),
    ], styles))
    story.append(callout(
        "平台能力边界",
        "抖音是当前主要真实验收链路。小红书、B站、快手、微博、视频号、TikTok 已有入口和字段映射，但可用字段取决于 TikHub 当时的接口、权限和平台返回。视频号可能不返回粉丝数或公开封面；系统应显示“未知”，不会伪造。",
        styles, WARN_PALE, ORANGE,
    ))
    story.append(PageBreak())

    add_title(story, "05", "配置大模型", "兼容 OpenAI Chat Completions 协议的服务；模型名可读取也可手填。", styles)
    story.append(screenshot(shots / "09-settings-llm.png", "图 5-1 · 系统设置 → 大模型。API 地址、鉴权方式和四类任务模型均可修改。", styles, 108 * mm))
    story.append(info_table(
        ["字段", "用途", "建议"],
        [
            ["API Base URL", "模型服务根地址", "通常填写到 /v1；以服务商文档为准"],
            ["快速分类模型", "总台指令路由和轻任务", "优先低延迟、低成本"],
            ["深度分析模型", "拆解与作者分析", "优先稳定推理与长文本"],
            ["写作模型", "方向、提纲、初稿和改写", "优先中文表达质量"],
            ["结构化抽取模型", "访谈字段和 JSON 输出", "优先严格遵循格式"],
            ["Embedding", "当前仅保存名称", "尚未接入，不要把它当作已生效"],
        ], [40 * mm, 65 * mm, 61 * mm], styles,
    ))
    story.append(callout("正确的验收动作", "先“保存并读取模型列表”；列表不可用时可手填准确模型名。再点击“测试快速模型”，只有真实请求成功才算配置完成。测试可能产生少量费用。", styles))
    story.append(PageBreak())

    add_title(story, "06", "配置语音识别与图片模型", "ASR 解决转写；图片模型服务视觉生成。两者互不替代。", styles)
    story.append(screenshot(shots / "10-settings-voice.png", "图 6-1 · 系统设置 → 语音模型。默认面向阿里云百炼 qwen3-asr-flash；TTS 明确未接入。", styles, 92 * mm))
    story.append(info_table(
        ["项目", "你需要填写", "测试方法"],
        [
            ["语音识别 ASR", "Base URL、API Key、ASR 模型、语言、热词", "上传一段短且清晰的授权音频，点击“保存并测试音频”"],
            ["图片模型", "兼容图片接口的 Base URL、Key、模型名、比例和质量", "执行最小图片测试，确认返回的是可用图像"],
            ["FFmpeg", "不需要 Key；系统级可执行程序", "在系统诊断中确认可用；Windows 可运行随包附带的可选安装脚本"],
        ], [38 * mm, 78 * mm, 50 * mm], styles,
    ))
    story.append(callout(
        "授权与成本",
        "只上传你拥有处理权的音视频。平台有字幕时优先使用原生正文或字幕；只有缺少可用文本且你主动选择时才进入 ASR。图片与语音测试可能计费。",
        styles, BLUE_PALE, TEAL,
    ))
    story.append(PageBreak())

    add_title(story, "07", "建立 IP 档案与自己的账号", "先定义“我是谁”，再让系统分析“账号现在怎样”。", styles)
    story.append(screenshot(shots / "02-profile.png", "图 7-1 · IP 档案：资料、访谈和证据字段会沉淀为版本化档案，不需要每次重新生成。", styles, 58 * mm))
    story.append(screenshot(shots / "03-accounts.png", "图 7-2 · 账号运营：同步自己的公开作品、快照和运营周期。演示模式只显示演示账号。", styles, 58 * mm))
    story.append(info_table(
        ["模块", "放什么", "不要放什么"],
        [
            ["IP 档案", "定位、人设、经历、受众、观点、语言风格和证据来源", "未经确认的模型猜测"],
            ["账号运营", "自己的公开账号、作品、粉丝快照、排序与运营周期", "他人的对标账号"],
            ["对标账号", "用于研究的公开账号和作品", "自己的私密后台数据"],
        ], [34 * mm, 70 * mm, 62 * mm], styles,
    ))
    story.append(callout("作品同步没到最新怎么办？", "先点击“同步最近作品”，再检查平台是否返回 has_more、时间游标和最新发布时间。若 Provider 仍只返回旧数据，保留 Trace ID；不要反复创建重复账号。", styles, WARN_PALE, ORANGE))
    story.append(PageBreak())

    add_title(story, "08", "内容雷达与对标账号", "先添加监控对象，再同步、筛选、提取和分析。", styles)
    story.append(screenshot(shots / "04-radar.png", "图 8-1 · 内容雷达：雷达信号、对标账号和热点数据源分开管理。", styles, 72 * mm))
    story.append(screenshot(shots / "05-add-benchmark.png", "图 8-2 · 添加对标账号：选择平台、填写 1—500 条目标数量，再输入公开主页链接或平台 ID。", styles, 80 * mm))
    story.append(PageBreak())
    story.append(p('<font color="#F15A2B">内容雷达</font>&nbsp;&nbsp;从同步到分析', styles["h1"]))
    story.append(p("对标作品只同步一次并持续沉淀；后续按作品 ID 去重和增量更新。", styles["body"]))
    story.append(steps([
        ("添加账号", "使用公开主页链接或平台 ID；系统解析后在后台同步，不需要一直停留在弹窗。"),
        ("继续同步", "首次未抓全时进入账号详情继续拉取；系统按作品 ID 去重，已沉淀作品不会每次重新创建。"),
        ("筛选高表现", "按时间、点赞、评论、收藏、播放或相对账号均值排序；平台缺失指标不参与虚假计算。"),
        ("进入提取或作者分析", "单条作品可提取，作者可做聚合分析。所有结果保留来源链。"),
    ], styles))
    story.append(callout("行业数据源、关键词、用户反馈、历史起量", "它们是不同的信号来源，不等于“再配一个对标账号”。行业源关注外部站点或主题；关键词订阅平台检索；用户反馈来自评论与人工记录；历史起量来自自己的发布快照。", styles))
    story.append(Spacer(1, 4 * mm))
    story.append(info_table(
        ["信号来源", "如何配置", "适合回答"],
        [
            ["行业数据源", "添加网站、RSS 或平台主题源", "行业最近在发生什么"],
            ["关键词", "设置平台、关键词和同步频率", "目标主题有哪些新内容"],
            ["用户反馈", "同步评论或手动记录反馈", "受众真实在问什么"],
            ["历史起量", "登记自己的发布并采集快照", "自己的什么内容正在增长"],
            ["手动关注", "收藏单条信号并备注原因", "我暂时不想错过什么"],
        ], [38 * mm, 62 * mm, 66 * mm], styles,
    ))
    story.append(PageBreak())

    add_title(story, "09", "内容提取：把原始内容沉淀下来", "提取不是一次性页面结果，而是可追溯资产。", styles)
    story.append(screenshot(shots / "06-extraction.png", "图 9-1 · 内容提取：任务按阶段显示成功或失败，失败不会被标成完成。", styles, 108 * mm))
    story.append(info_table(
        ["文本优先级", "含义", "是否调用 ASR"],
        [
            ["有效本地缓存", "之前已沉淀的完整文本", "否"],
            ["用户导入正文", "你主动粘贴或导入的正文", "否"],
            ["平台原生正文 / 字幕", "Provider 返回的公开文本", "否"],
            ["用户主动选择 ASR", "授权音频或含音频视频", "是"],
        ], [48 * mm, 78 * mm, 40 * mm], styles,
    ))
    story.append(callout(
        "MEDIA_URL_NOT_AVAILABLE 怎么办？",
        "说明平台没有返回可下载的公开媒体地址。系统不会伪造成功。可改用平台原生字幕、手动导入正文，或在你拥有授权时上传本地音视频。",
        styles, WARN_PALE, ORANGE,
    ))
    story.append(PageBreak())

    add_title(story, "10", "内容拆解与创作工坊", "先理解可复用结构，再用自己的身份和事实写出来。", styles)
    story.append(screenshot(shots / "07-creation.png", "图 10-1 · 创作工坊：从选择来源到锁定版本共有明确步骤；加载失败时会显示原因，不应无限旋转。", styles, 106 * mm))
    story.append(info_table(
        ["阶段", "主要输入", "主要产出"],
        [
            ["内容拆解", "完整提取资产", "核心选题、受众问题、开头钩子、结构、论据、情绪和行动点"],
            ["IP 匹配", "已确认的 IP 档案", "可讲视角、身份边界、语言风格"],
            ["差异化方向", "原内容结构 + 你的事实", "新的选题角度，不复制原文"],
            ["初稿与适配", "提纲、平台和风格", "可追溯版本、原创检查结果"],
        ], [36 * mm, 66 * mm, 64 * mm], styles,
    ))
    story.append(callout("乱码或错误字符", "如果原文出现乱码，先停止下游生成并保留 Trace ID。正确处理顺序是修复源文本编码或重新提取，然后再拆解；不要让乱码继续污染 IP 档案和创作稿。", styles, WARN_PALE, ORANGE))
    story.append(PageBreak())

    add_title(story, "11", "视觉工厂与内容计划", "视觉资产与文字版本一起进入发布计划。", styles)
    story.append(screenshot(shots / "11-visuals.png", "图 11-1 · 视觉工厂：按平台比例生成和管理视觉版本；小红书/竖版作品优先使用 3:4 或 9:16。", styles, 62 * mm))
    story.append(screenshot(shots / "12-planning.png", "图 11-2 · 内容计划：候选、已选题、提纲、写作、制作、待发布形成看板。", styles, 62 * mm))
    story.append(steps([
        ("从已选题开始创作", "计划卡片进入创作工坊后会复用同一项目，不会每次重复创建。"),
        ("保留文字与视觉版本", "每次生成、修改和选择都会成为资产版本，正式发布前锁定最终版本。"),
        ("登记发布", "填写真实平台链接、平台作品 ID 和发布时间，建立发布证据链。"),
        ("创建复盘快照", "发布后按 T+1 / T+3 / T+7 或自定义节点回收数据；不要用演示数据替代。"),
    ], styles))
    story.append(PageBreak())

    add_title(story, "12", "数据复盘、资产与备份", "沉淀后的数据会复用，不需要每次重新抓取和重新分析。", styles)
    story.append(screenshot(shots / "13-retro.png", "图 12-1 · 数据复盘：只对已登记发布的真实作品建立快照与复盘。", styles, 96 * mm))
    story.append(info_table(
        ["数据", "保存在什么位置", "分享注意"],
        [
            ["SQLite 数据库", "Windows：%LOCALAPPDATA%/JianzhouMediaWorkbench/app.db", "不要随 EXE 一起转发"],
            ["macOS 数据", "~/Library/Application Support/JianzhouMediaWorkbench/", "不要随 .app 一起压缩"],
            ["上传文件与日志", "同一数据目录的 uploads / logs", "排障前检查是否含账号文本"],
            ["备份", "数据目录 backups", "只在需要迁移时加密传输"],
            ["API Key", "Windows DPAPI / macOS 登录钥匙串", "不会进入数据库；换电脑重新配置"],
        ], [36 * mm, 78 * mm, 52 * mm], styles,
    ))
    story.append(callout("分享时只发什么？", "只分享正式发布文件夹或其 ZIP。不要分享项目 data 目录、浏览器截图缓存、日志、数据库、secrets.json、.env 或你自己的配置导出。", styles, ORANGE_PALE, ORANGE))
    story.append(PageBreak())

    add_title(story, "13", "故障排查：先看错误码，再看 Trace ID", "错误被明示是为了让问题可定位，不是把失败藏起来。", styles)
    story.append(info_table(
        ["现象 / 错误", "通常原因", "处理方法"],
        [
            ["白屏或一直加载", "前端资源未加载、后端接口失败、旧缓存", "重启应用；访问 healthz；清浏览器缓存；看系统诊断"],
            ["TIKHUB_UNAUTHORIZED", "Key 失效、复制错误或鉴权格式错误", "重新粘贴自己的 Key，保存后用公开账号真实测试"],
            ["MEDIA_URL_NOT_AVAILABLE", "平台未返回可下载媒体地址", "改用字幕/正文，或上传授权本地文件"],
            ["ASR 转写失败", "Key、模型、格式、大小或网络问题", "先在语音设置用短音频测试，再回到提取任务重试"],
            ["模型请求失败", "Base URL、模型名、Key 或服务商限额", "读取模型列表；测试快速模型；检查服务商余额"],
            ["账号作品不是最新", "Provider 缓存、分页游标或平台接口延迟", "同步最近作品；保留 Trace ID；避免重复添加账号"],
            ["视频号缺封面/粉丝", "上游接口没有返回对应字段", "以未知显示；不要把 0 当成真实值"],
        ], [43 * mm, 59 * mm, 64 * mm], styles,
    ))
    story.append(Spacer(1, 4 * mm))
    story.append(callout("排障信息怎么发？", "提供：操作时间、页面、错误码、失败阶段、Provider、Trace ID。不要发送 API Key、完整数据库、个人目录截图或未脱敏日志。", styles))
    story.append(PageBreak())

    add_title(story, "14", "Windows 与 macOS 安装说明", "两个平台需要各自在原生系统构建和验收。", styles)
    story.append(info_table(
        ["平台", "交付形式", "当前状态", "首次启动提示"],
        [
            ["Windows x64", "单文件 EXE + 手册 + 校验值", "已在全新数据目录完成启动和接口验收", "未签名包可能触发 SmartScreen；仅从可信发布渠道获取"],
            ["macOS Apple Silicon", ".app ZIP", "必须在 macOS arm64 原生构建后再发布", "未签名/未公证应用会被 Gatekeeper 拦截"],
            ["macOS Intel", ".app ZIP", "必须在 macOS x64 原生构建后再发布", "需独立验收，不能用 arm64 包冒充通用包"],
        ], [36 * mm, 47 * mm, 50 * mm, 33 * mm], styles,
    ))
    story.append(Spacer(1, 4 * mm))
    story.append(p("Windows 用户", styles["h2"]))
    story.append(p("解压后双击 EXE。若系统弹出来源提示，先核对 SHA-256 与发布者提供的值；不要从二次转载链接下载。需要媒体转换时，可运行发布文件夹中的“安装FFmpeg（可选）.cmd”。", styles["body"]))
    story.append(p("macOS 用户", styles["h2"]))
    story.append(p("只使用明确标注架构的 macOS 原生构建包。开发者发布前应完成 codesign、notarization 与 stapling；在此之前，不应把构建脚本或 Windows 产物描述成可直接分享的 macOS 应用。", styles["body"]))
    story.append(callout("校验值的作用", "SHA-256 用于确认文件在传输后没有变化。它不能替代代码签名，但能发现下载损坏或文件被替换。", styles, BLUE_PALE, TEAL))
    story.append(PageBreak())

    add_title(story, "15", "发布者检查表", "每次对外分享前逐项确认。", styles)
    story.append(info_table(
        ["检查项", "必须满足"],
        [
            ["版本冻结", "发布清单记录版本、构建时间、基础 commit 和当前工作区状态"],
            ["功能测试", "自动化测试全部通过；在全新数据目录启动；首页与核心资源返回 200"],
            ["隐私扫描", "EXE / ZIP 不含 app.db、uploads、logs、backups、.env、secrets.json、API Key 或个人目录"],
            ["真实能力表述", "演示能力、真实能力、部分支持、尚未接入均明确标识"],
            ["Windows", "EXE SHA-256 已生成；手册和隐私说明与包一起分发"],
            ["macOS", "在目标架构原生构建；完成签名/公证；全新用户目录验收后才发布"],
            ["说明书", "截图不含个人路径和 Key；首次配置、错误处理和数据位置写清楚"],
        ], [49 * mm, 117 * mm], styles,
    ))
    story.append(Spacer(1, 7 * mm))
    story.append(callout(
        "遇到问题时",
        "先在系统设置 → 系统诊断查看 Provider 状态和错误日志，再按 Trace ID 定位。反馈时只发最小、已脱敏的信息。",
        styles, ORANGE_PALE, ORANGE,
    ))
    story.append(Spacer(1, 18 * mm))
    story.append(p("开始使用", styles["h1"]))
    story.append(p("打开工作总台 → 确认演示模式 → 配置一个 Provider → 真实测试 → 再切换真实模式。<br/>每一步都有证据，整条链路才真正可用。", styles["body"]))

    doc.build(story, onFirstPage=cover_background, onLaterPages=page_background)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--shots", type=Path, default=Path(r"D:\JianzhouManualCapture3\shots"))
    parser.add_argument(
        "--output", type=Path,
        default=Path("output/pdf/见舟工作台-完整使用说明书-v2.1.0.pdf"),
    )
    args = parser.parse_args()
    build_manual(args.output.resolve(), args.shots.resolve())
    print(args.output.resolve())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
