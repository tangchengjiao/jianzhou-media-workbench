"""Visual production domain: covers, graphic documents, prompts and deliverables."""
from __future__ import annotations

import hashlib
import io
import json
import re
from pathlib import Path

from fastapi import APIRouter, File, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageFont, ImageOps

from .. import config
from ..db import execute, jdumps, jloads, query, query_one, transaction
from ..errors import AppError, not_found, state_invalid, validation_failed
from ..ids import new_id
from ..repositories import owned_row
from . import current_user_id, now, ok

router = APIRouter(prefix="/visuals", tags=["visuals"])

RATIOS = {"3:4": (900, 1200), "9:16": (810, 1440), "16:9": (1280, 720), "1:1": (1080, 1080)}
GENERATION_MODES = {"local_template", "ai_hybrid", "ai_full", "ai_background"}
GRAPHIC_ROLES = {"cover", "problem", "insight", "method", "steps", "case", "comparison",
                 "checklist", "quote", "summary", "cta"}
GRAPHIC_LAYOUTS = {"hero", "editorial", "steps", "comparison", "checklist", "quote", "summary"}
GRAPHIC_ROLE_LABELS = {
    "cover": "封面", "problem": "问题", "insight": "核心洞察", "method": "方法",
    "steps": "步骤", "case": "案例", "comparison": "对比", "checklist": "清单",
    "quote": "金句", "summary": "总结", "cta": "行动建议",
}
GRAPHIC_NARRATIVES = {
    "auto", "tutorial", "insight", "case_study", "comparison", "checklist", "story",
}

COVER_PROMPT = """生成 {{RATIO}} 的高完成度中文知识IP封面，恢复“AI产品发布会 / 科技知识博主”视觉语法。
构图优先采用：人物真实半身像位于右侧约40%-46%，左侧是超大主标题；深黑蓝渐变背景，
有克制的蓝色轮廓光、科技线条、空间层次和少量HUD结构，但背景不能抢文字，不能做成廉价赛博海报。
主标题必须有强烈字号层级，白色与科技蓝搭配，2-4行内完成阅读；人物五官、年龄、发型和真实身份不得改变。
只有在确实提供辅助短标签时，才可在底部做最多两个简洁知识模块；没有内容就保留留白，绝不自动补标签。
画面可见文字严格限定为：主标题「{{TITLE}}」；可选副标题「{{SUBTITLE}}」；可选辅助短标签「{{FEATURES}}」。
核心主题「{{MAIN_TOPIC}}」只用于理解画面语义；如果它与主标题相同或近似，绝对不能再次排版。
除上述可见文字白名单外，不得在画面中新增任何栏目名、内容类型、平台热点、运营目标、
账号指标、预测结论、来源编号、关键词列表、模板名、标签名或解释性小字。
第一眼只读清人物与一个核心标题，最多两级标题和两个辅助短标签；四周保留至少6%安全区，
保持品牌色和人物身份一致。不得出现模板名称或“AI知识IP”等栏目角标。避免廉价营销感、拥挤布局、复杂信息卡、乱码、错别字、
人物五官变化和低清晰度。"""

GRAPHIC_PROMPT = """把已保存稿件制作成 {{RATIO}} 中文知识图文。每页只承担一个信息任务，
正文必须忠于稿件，统一品牌色、网格、字体层级和留白；封面少文字，正文页保留完整解释。
视觉底图必须极简、无任何文字数字或Logo、单一视觉焦点、低对比背景，并为中文排版预留至少60%的干净留白。
禁止复杂拼贴、重复人物、密集小物件、伪UI截图、拥挤装饰和把每一页都做成封面。"""

BUILTIN_TEMPLATES = [
    {"suffix": "tech-launch-v3", "name": "见舟AI · 科技发布会", "ratio": "3:4",
     "type": "cover", "layout": "person_right_title_left", "background": ["#07111f", "#102a52"],
     "accent": "#2f8cff", "text": "#ffffff", "person_ratio": 0.44},
    {"suffix": "black-orange-v3", "name": "工具测评 · 黑橙高对比", "ratio": "3:4",
     "type": "cover", "layout": "tool_split", "background": ["#151515", "#322018"],
     "accent": "#f05a2a", "text": "#ffffff", "person_ratio": 0.42},
    {"suffix": "clean-blue-cover-v4", "name": "极简蓝白 · 结论封面", "ratio": "3:4",
     "type": "cover", "layout": "clean_statement", "background": ["#f7f9fc", "#e7effc"],
     "accent": "#246bda", "text": "#172033", "person_ratio": 0.40},
    {"suffix": "editorial-cover-v4", "name": "杂志编辑 · 人物观点", "ratio": "3:4",
     "type": "cover", "layout": "editorial_portrait", "background": ["#f3ede3", "#e1d7c7"],
     "accent": "#d84a2b", "text": "#26211d", "person_ratio": 0.46},
    {"suffix": "sticky-note-v3", "name": "知识便签 · 重点拆解", "ratio": "3:4",
     "type": "graphic", "layout": "note_stack", "background": ["#f4efe5", "#dfd5c6"],
     "accent": "#e4572e", "text": "#25211d", "person_ratio": 0.0},
    {"suffix": "clean-editorial-v3", "name": "清爽编辑 · 知识卡片", "ratio": "3:4",
     "type": "graphic", "layout": "minimal_editorial", "background": ["#f7f8fb", "#e9eef7"],
     "accent": "#2e6fdb", "text": "#172033", "person_ratio": 0.0},
    {"suffix": "paper-margin-v4", "name": "纸张留白 · 深度观点", "ratio": "3:4",
     "type": "graphic", "layout": "paper_margin", "background": ["#fbfaf7", "#eee9df"],
     "accent": "#d84a2b", "text": "#25211d", "person_ratio": 0.0},
    {"suffix": "dark-data-v4", "name": "深色数据 · 方法清单", "ratio": "3:4",
     "type": "graphic", "layout": "dark_data", "background": ["#0d1525", "#182742"],
     "accent": "#54a3ff", "text": "#f7f9ff", "person_ratio": 0.0},
    {"suffix": "soft-green-v4", "name": "柔和绿意 · 案例复盘", "ratio": "3:4",
     "type": "graphic", "layout": "soft_case", "background": ["#f5f8f4", "#e3eee5"],
     "accent": "#2f7c58", "text": "#1f3027", "person_ratio": 0.0},
    {"suffix": "blueprint-v4", "name": "蓝图卡片 · 教程步骤", "ratio": "3:4",
     "type": "graphic", "layout": "blueprint_steps", "background": ["#eef5ff", "#dce9fb"],
     "accent": "#246bda", "text": "#16243b", "person_ratio": 0.0},
]

PROMPT_PRESETS = [
    ("cover-smart-v1", "知识IP封面 · 智能默认", "cover", COVER_PROMPT,
     "错别字，乱码，文字溢出，人物身份变化，廉价海报，拥挤布局，低清晰度"),
    ("graphic-smart-v1", "知识图文 · 逐页叙事", "graphic_note", GRAPHIC_PROMPT,
     "多张重复封面，空洞口号，虚构数据，错别字，乱码，文字溢出，布局拥挤"),
]


COVER_INTERNAL_LABELS = {
    "观点型", "教程型", "案例型", "清单型", "市场热点", "行业热点", "平台热点",
    "对标高表现", "账号均值", "历史均值", "预测档位", "内容类型", "写作中",
}
COVER_INTERNAL_PATTERNS = (
    re.compile(r"\b(?:SG|CTX|SC|VPR|VB|CV|A)-[A-Za-z0-9-]{4,}\b", re.I),
    re.compile(r"(?:收藏|点赞|评论|分享|完播|互动|转化)率?\s*(?:>|<|=|≥|≤|大于|小于|高于|低于)\s*(?:账号|历史)?均值"),
    re.compile(r"(?:置信度|预测档位|账号均值|历史均值)\s*[:：]?\s*\d+(?:\.\d+)?%?"),
)


def _compact_cover_text(value, limit: int) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip(" \t\r\n,，、;；|｜")
    return text[:limit].rstrip(" ,，、;；|｜")


def _cover_key(value: str) -> str:
    return re.sub(r"[\W_]+", "", str(value or "").lower(), flags=re.UNICODE)


def _is_internal_cover_text(value: str) -> bool:
    text = _compact_cover_text(value, 200)
    if not text:
        return False
    compact = _cover_key(text)
    if any(_cover_key(label) == compact for label in COVER_INTERNAL_LABELS):
        return True
    return any(pattern.search(text) for pattern in COVER_INTERNAL_PATTERNS)


def _clean_cover_items(values, *, limit: int, item_limit: int,
                       against: list[str] | None = None) -> list[str]:
    if isinstance(values, str):
        values = re.split(r"[\n,，、]+", values)
    result: list[str] = []
    seen = {_cover_key(item) for item in (against or []) if _cover_key(item)}
    for value in values or []:
        text = _compact_cover_text(value, item_limit)
        key = _cover_key(text)
        if not text or not key or _is_internal_cover_text(text):
            continue
        if key in seen or any(key in item or item in key for item in seen if len(item) >= 4 and len(key) >= 4):
            continue
        seen.add(key)
        result.append(text)
        if len(result) >= limit:
            break
    return result


def _sanitize_cover_fields(values: dict | None, fallback_title: str = "") -> dict:
    """Create one stable cover contract and keep operational metadata off-canvas."""
    raw = dict(values or {})
    fallback = _compact_cover_text(fallback_title, 36)
    topic = _compact_cover_text(raw.get("main_topic") or fallback, 36)
    if _is_internal_cover_text(topic):
        topic = fallback
    title = _compact_cover_text(raw.get("title") or topic or fallback, 32)
    if _is_internal_cover_text(title):
        title = topic or fallback
    subtitle = _compact_cover_text(raw.get("subtitle"), 28)
    if _is_internal_cover_text(subtitle) or _cover_key(subtitle) == _cover_key(title):
        subtitle = ""
    features = _clean_cover_items(raw.get("features"), limit=2, item_limit=10,
                                  against=[title, subtitle])
    keywords = _clean_cover_items(raw.get("keywords"), limit=5, item_limit=16,
                                  against=[topic, title, subtitle, *features])
    category = _compact_cover_text(raw.get("category") or "知识IP", 16)
    if _is_internal_cover_text(category):
        category = "知识IP"
    return {
        "main_topic": topic or title,
        "title": title or topic or "未命名主题",
        "subtitle": subtitle,
        "category": category,
        "features": features,
        "keywords": keywords,
    }


def _safe_cover_direction(value: str) -> str:
    text = _compact_cover_text(value, 1400)
    for pattern in COVER_INTERNAL_PATTERNS:
        text = pattern.sub("", text)
    for label in COVER_INTERNAL_LABELS:
        text = text.replace(label, "")
    return re.sub(r"\s{2,}", " ", text).strip(" ，,；;。")


def _cover_visible_contract(fields: dict) -> str:
    labels = [fields.get("title") or ""]
    if fields.get("subtitle"):
        labels.append(fields["subtitle"])
    labels.extend(fields.get("features") or [])
    return (
        "最终画面文字白名单（必须逐字遵守）：" + "｜".join(labels) + "。"
        "只允许这些文字各出现一次；空字段不补写；禁止把主题、领域、关键词、来源证据、"
        "运营指标、内容分类或设计说明排进画面。"
    )


def _visual_dir(uid: str) -> Path:
    path = config.DATA_DIR / "visuals" / uid
    path.mkdir(parents=True, exist_ok=True)
    return path


def _font(size: int, bold: bool = False):
    choices = (["C:/Windows/Fonts/msyhbd.ttc", "C:/Windows/Fonts/simhei.ttf"] if bold
               else ["C:/Windows/Fonts/msyh.ttc", "C:/Windows/Fonts/simsun.ttc"])
    for path in choices:
        if Path(path).exists():
            return ImageFont.truetype(path, max(10, int(size)))
    return ImageFont.load_default()


def _hex(value: str, fallback: tuple[int, int, int]) -> tuple[int, int, int]:
    raw = str(value or "").lstrip("#")
    try:
        return tuple(int(raw[i:i + 2], 16) for i in (0, 2, 4)) if len(raw) == 6 else fallback
    except ValueError:
        return fallback


def _wrap(draw: ImageDraw.ImageDraw, value: str, font, width: int, lines: int) -> list[str]:
    text = re.sub(r"\s+", " ", str(value or "").strip())
    if not text:
        return []
    result, current = [], ""
    for char in text:
        trial = current + char
        if not current or draw.textbbox((0, 0), trial, font=font)[2] <= width:
            current = trial
        else:
            result.append(current)
            current = char
            if len(result) >= lines:
                break
    if current and len(result) < lines:
        result.append(current)
    consumed = sum(len(item) for item in result)
    if consumed < len(text) and result:
        last = result[-1]
        while last and draw.textbbox((0, 0), last + "…", font=font)[2] > width:
            last = last[:-1]
        result[-1] = (last or result[-1][:1]) + "…"
    return result


def _fit(draw: ImageDraw.ImageDraw, value: str, width: int, height: int,
         preferred: int, minimum: int, max_lines: int):
    for size in range(preferred, minimum - 1, -2):
        font = _font(size, True)
        lines = _wrap(draw, value, font, width, max_lines)
        line_height = int(size * 1.18)
        if lines and line_height * len(lines) <= height:
            return font, lines, line_height
    font = _font(minimum, True)
    return font, _wrap(draw, value, font, width, max_lines), int(minimum * 1.18)


def _gradient(size: tuple[int, int], colors: list[str]) -> Image.Image:
    width, height = size
    start, end = _hex(colors[0], (7, 17, 31)), _hex(colors[-1], (16, 42, 82))
    image = Image.new("RGB", size, start)
    draw = ImageDraw.Draw(image)
    for y in range(height):
        p = y / max(1, height - 1)
        color = tuple(int(start[i] + (end[i] - start[i]) * p) for i in range(3))
        draw.line((0, y, width, y), fill=color)
    return image


def _paste_person(image: Image.Image, person_path: str | None, box: tuple[int, int, int, int]) -> bool:
    if not person_path or not Path(person_path).is_file():
        return False
    try:
        person = ImageOps.fit(Image.open(person_path).convert("RGBA"),
                              (box[2] - box[0], box[3] - box[1]), Image.Resampling.LANCZOS,
                              centering=(0.5, 0.30))
        image.paste(person, (box[0], box[1]), person)
        return True
    except Exception:
        return False


def _render_cover(uid: str, entity_id: str, fields: dict, ratio: str,
                  person_path: str | None, template: dict,
                  background_path: str | None = None) -> tuple[Path, dict]:
    fields = _sanitize_cover_fields(fields)
    width, height = RATIOS[ratio]
    if background_path and Path(background_path).is_file():
        image = ImageOps.fit(Image.open(background_path).convert("RGB"), (width, height), Image.Resampling.LANCZOS)
    else:
        image = _gradient((width, height), template.get("background") or ["#07111f", "#102a52"])
    draw = ImageDraw.Draw(image, "RGBA")
    accent = _hex(template.get("accent"), (47, 140, 255))
    text_color = _hex(template.get("text"), (255, 255, 255))
    margin = int(min(width, height) * 0.055)
    safe = (margin, margin, width - margin, height - margin)
    layout = template.get("layout") or "person_right_title_left"
    person_ratio = float(template.get("person_ratio") or 0)
    person_width = int(width * person_ratio) if person_path else 0
    if person_width:
        _paste_person(image, person_path, (width - person_width, int(height * .12), width, height))
    content_right = width - margin - (int(person_width * .76) if person_width else 0)
    content_width = max(int(width * .42), content_right - margin)
    # 核心主题只参与语义，不重复排版；画面只保留一个主标题层级。
    topic = str(fields.get("main_topic") or "")
    title = str(fields.get("title") or topic or "未命名主题")
    subtitle = str(fields.get("subtitle") or "")
    title_font, title_lines, title_h = _fit(draw, title, content_width, int(height * .32),
                                            int(height * .078), int(height * .038), 4)
    y = int(height * .17)
    for line in title_lines:
        draw.text((margin, y), line, font=title_font, fill=text_color)
        y += title_h
    underline_y = y + int(height * .012)
    draw.rounded_rectangle((margin, underline_y, margin + min(content_width, int(width * .23)),
                            underline_y + max(4, int(height * .006))), radius=4, fill=(*accent, 235))
    y = underline_y + int(height * .032)
    if subtitle:
        sub_font = _font(int(height * .024), True)
        for line in _wrap(draw, subtitle, sub_font, content_width, 2):
            draw.text((margin, y), line, font=sub_font, fill=(*accent, 235))
            y += int(height * .034)
    features = [str(x) for x in (fields.get("features") or []) if str(x).strip()][:2]
    chip_y = height - margin - int(height * .09)
    chip_x = margin
    chip_font = _font(int(height * .016), True)
    for value in features:
        label = value[:8]
        tw = draw.textbbox((0, 0), label, font=chip_font)[2]
        box_width = tw + 28
        if chip_x + box_width > width - margin:
            break
        draw.rounded_rectangle((chip_x, chip_y, chip_x + box_width, chip_y + int(height * .038)),
                               radius=14, fill=(*accent, 190))
        draw.text((chip_x + 14, chip_y + 8), label, font=chip_font, fill=(255, 255, 255))
        chip_x += box_width + 10
    path = _visual_dir(uid) / f"{entity_id}.png"
    image.save(path, "PNG", optimize=True)
    report = {"canvas": [width, height], "safe_box": list(safe), "overflow": False,
              "layout": layout, "topic_visible": False, "title_lines": len(title_lines),
              "visible_text": [item for item in [title, subtitle, *features] if item]}
    return path, report


def _render_graphic_page(uid: str, revision_id: str, page: dict, ratio: str,
                         template: dict, background_path: str | None = None) -> tuple[Path, dict]:
    width, height = RATIOS[ratio]
    if background_path and Path(background_path).is_file():
        image = ImageOps.fit(Image.open(background_path).convert("RGB"), (width, height), Image.Resampling.LANCZOS)
        image = ImageEnhance.Color(image).enhance(.62)
        image = ImageEnhance.Contrast(image).enhance(.82)
        image = image.filter(ImageFilter.GaussianBlur(radius=max(1, int(min(width, height) * .0016))))
    else:
        image = _gradient((width, height), template.get("background") or ["#f7f8fb", "#e9eef7"])
    draw = ImageDraw.Draw(image, "RGBA")
    accent = _hex(template.get("accent"), (46, 111, 219))
    text_color = _hex(template.get("text"), (23, 32, 51))
    dark = sum(text_color) > 620
    surface = (13, 21, 36, 238) if dark else (255, 255, 255, 242)
    surface_soft = (255, 255, 255, 24) if dark else (255, 255, 255, 178)
    muted = (220, 228, 241, 220) if dark else (68, 78, 92, 220)
    margin = int(min(width, height) * .065)
    role = str(page.get("role") or "insight")
    layout = str(page.get("layout_key") or "editorial")
    template_layout = str(template.get("layout") or "minimal_editorial")
    content_top = margin
    if background_path:
        # AI 只贡献顶部视觉带；正文全部落入高对比内容面板，避免文字压在复杂场景上。
        visual_height = int(height * (.34 if layout != "hero" else .39))
        draw.rounded_rectangle((margin, visual_height - 20, width - margin, height - margin),
                               radius=max(20, int(width * .025)), fill=surface)
        content_top = visual_height + int(height * .035)
    elif template_layout in {"note_stack", "paper_margin"}:
        draw.rounded_rectangle((margin, margin, width - margin, height - margin),
                               radius=max(20, int(width * .025)), fill=surface_soft,
                               outline=(*accent, 90), width=max(1, int(width * .002)))
        content_top = margin + int(height * .025)
    elif template_layout == "dark_data":
        draw.rounded_rectangle((margin, margin, width - margin, height - margin),
                               radius=max(20, int(width * .025)), fill=(7, 13, 24, 75),
                               outline=(*accent, 95), width=max(1, int(width * .002)))
        content_top = margin + int(height * .025)

    role_label = GRAPHIC_ROLE_LABELS.get(role, "正文")
    badge_w = min(int(width * .29), max(int(width * .13), len(role_label) * int(height * .019) + 34))
    draw.rounded_rectangle((margin, content_top, margin + badge_w, content_top + int(height * .042)),
                           radius=max(10, int(height * .012)), fill=(*accent, 235))
    draw.text((margin + 15, content_top + int(height * .008)), role_label,
              font=_font(int(height * .017), True), fill=(255, 255, 255))
    title = str(page.get("title") or "未命名页面")
    title_top = content_top + int(height * .075)
    title_font, title_lines, title_h = _fit(draw, title, width - margin * 2, int(height * .19),
                                            int(height * (.052 if layout == "hero" else .046)),
                                            int(height * .029), 3)
    y = title_top
    for line in title_lines:
        draw.text((margin, y), line, font=title_font, fill=text_color)
        y += title_h
    draw.rounded_rectangle((margin, y + 12, margin + int(width * .15), y + 20), radius=4, fill=accent)
    y += int(height * .055)
    body = str(page.get("body") or "")
    bullets = [str(x).strip() for x in (page.get("bullets") or []) if str(x).strip()][:6]
    if not bullets and body:
        bullets = [item.strip() for item in re.split(r"[。；;\n]+", body) if item.strip()][:5]
    body_font = _font(int(height * .022))
    bullet_font = _font(int(height * .021), True)

    if layout == "hero":
        for line in _wrap(draw, body, body_font, width - margin * 2, 4):
            draw.text((margin, y), line, font=body_font, fill=muted)
            y += int(height * .036)
    elif layout in {"steps", "checklist"}:
        items = bullets or [body]
        card_h = int(height * .082)
        for number, item in enumerate(items[:5], 1):
            if y + card_h > height - margin * 1.8:
                break
            draw.rounded_rectangle((margin, y, width - margin, y + card_h), radius=14,
                                   fill=surface_soft, outline=(*accent, 70), width=1)
            if layout == "steps":
                marker = f"{number:02d}"
            else:
                marker = "✓"
            draw.text((margin + 16, y + int(card_h * .24)), marker, font=_font(int(height * .020), True), fill=accent)
            lines = _wrap(draw, item, bullet_font, width - margin * 2 - 70, 2)
            for line_no, line in enumerate(lines):
                draw.text((margin + 62, y + int(card_h * .20) + line_no * int(height * .030)),
                          line, font=bullet_font, fill=text_color)
            y += card_h + int(height * .014)
    elif layout == "comparison":
        items = bullets or [item.strip() for item in re.split(r"[。；;\n]+", body) if item.strip()]
        midpoint = max(1, (len(items) + 1) // 2)
        columns = [items[:midpoint], items[midpoint:]]
        gap = int(width * .035); col_w = int((width - margin * 2 - gap) / 2)
        panel_h = min(int(height * .42), height - margin - y - int(height * .04))
        for col, values in enumerate(columns):
            x = margin + col * (col_w + gap)
            draw.rounded_rectangle((x, y, x + col_w, y + panel_h), radius=16,
                                   fill=surface_soft, outline=(*accent, 75), width=1)
            draw.text((x + 16, y + 14), "A" if col == 0 else "B",
                      font=_font(int(height * .020), True), fill=accent)
            item_y = y + int(height * .055)
            for item in values[:3]:
                for line in _wrap(draw, item, body_font, col_w - 32, 3):
                    draw.text((x + 16, item_y), line, font=body_font, fill=text_color)
                    item_y += int(height * .031)
                item_y += int(height * .015)
        y += panel_h
    elif layout == "quote":
        quote = body or (bullets[0] if bullets else title)
        quote_font, quote_lines, quote_h = _fit(draw, quote, width - margin * 2, int(height * .42),
                                               int(height * .038), int(height * .026), 7)
        draw.text((margin, y - int(height * .02)), "“", font=_font(int(height * .075), True), fill=(*accent, 115))
        y += int(height * .06)
        for line in quote_lines:
            draw.text((margin + int(width * .04), y), line, font=quote_font, fill=text_color)
            y += quote_h
    else:
        body_lines = 5 if layout == "summary" else 7
        for line in _wrap(draw, body, body_font, width - margin * 2, body_lines):
            draw.text((margin, y), line, font=body_font, fill=muted)
            y += int(height * .034)
        y += int(height * .012)
        for item in bullets[:4]:
            if y > height - margin * 2.2:
                break
            draw.ellipse((margin, y + 8, margin + 12, y + 20), fill=accent)
            lines = _wrap(draw, item, bullet_font, width - margin * 2 - 30, 2)
            for line in lines:
                draw.text((margin + 28, y), line, font=bullet_font, fill=text_color)
                y += int(height * .032)
            y += int(height * .010)
    draw.text((width - margin - 80, height - margin), f"{int(page.get('page_index') or 1):02d}",
              font=_font(int(height * .018), True), fill=(*accent, 220))
    path = _visual_dir(uid) / f"{revision_id}.png"
    image.save(path, "PNG", optimize=True)
    return path, {"canvas": [width, height], "safe_box": [margin, margin, width - margin, height - margin],
                  "overflow": y > height - margin, "layout": layout,
                  "template_layout": template_layout, "ai_visual_band": bool(background_path)}


def _profile_row(uid: str) -> dict:
    row = query_one("SELECT * FROM visual_profiles WHERE user_id=?", (uid,))
    if not row:
        stamp = now()
        execute("INSERT INTO visual_profiles(id,user_id,brand_json,updated_at) VALUES(?,?,?,?)",
                (new_id("VP"), uid, jdumps({"colors": ["#07111f", "#2f8cff"], "voice": "专业、克制、可信"}), stamp))
        row = query_one("SELECT * FROM visual_profiles WHERE user_id=?", (uid,))
    return row


def _ensure_builtin(uid: str) -> str:
    stamp = now()
    active_ids = []
    for item in BUILTIN_TEMPLATES:
        template_id = f"CVT-{uid}-{item['suffix']}"
        active_ids.append(template_id)
        data = {"layout": item["layout"], "background": item["background"], "accent": item["accent"],
                "text": item["text"], "person_ratio": item["person_ratio"],
                "supported_ratios": list(RATIOS),
                "safe_zone": {"top": .055, "right": .055, "bottom": .065, "left": .055}}
        values = (item["name"], item["ratio"], jdumps(data),
                  COVER_PROMPT if item["type"] == "cover" else GRAPHIC_PROMPT,
                  stamp, item["type"], template_id, uid)
        if query_one("SELECT id FROM cover_templates WHERE id=? AND user_id=?", (template_id, uid)):
            execute("UPDATE cover_templates SET name=?,ratio=?,template_json=?,prompt_template=?,builtin=1,active=1,updated_at=?,template_type=?,layout_version=3 WHERE id=? AND user_id=?", values)
        else:
            execute("INSERT INTO cover_templates(id,user_id,name,ratio,template_json,prompt_template,builtin,active,created_at,updated_at,template_type,layout_version) VALUES(?,?,?,?,?,?,1,1,?,?,?,3)",
                    (template_id, uid, item["name"], item["ratio"], jdumps(data),
                     COVER_PROMPT if item["type"] == "cover" else GRAPHIC_PROMPT,
                     stamp, stamp, item["type"]))
    placeholders = ",".join("?" for _ in active_ids)
    execute(f"UPDATE cover_templates SET active=0,updated_at=? WHERE user_id=? AND builtin=1 AND id NOT IN ({placeholders})",
            (stamp, uid, *active_ids))
    return active_ids[0]


def _ensure_prompt_presets(uid: str) -> None:
    stamp = now()
    for suffix, name, scope, template, negative in PROMPT_PRESETS:
        preset_id = f"VPP-{uid}-{suffix}"
        if query_one("SELECT id FROM visual_prompt_presets WHERE id=?", (preset_id,)):
            execute("UPDATE visual_prompt_presets SET name=?,scope=?,template_text=?,negative_prompt=?,active=1,updated_at=? WHERE id=? AND user_id=?",
                    (name, scope, template, negative, stamp, preset_id, uid))
        else:
            execute("INSERT INTO visual_prompt_presets(id,user_id,name,scope,template_text,negative_prompt,builtin,active,created_at,updated_at) VALUES(?,?,?,?,?,?,1,1,?,?)",
                    (preset_id, uid, name, scope, template, negative, stamp, stamp))


def _draft_for_project(uid: str, project: dict, draft_id: str | None) -> dict:
    if draft_id:
        draft = query_one("SELECT d.* FROM draft_versions d JOIN creation_projects p ON p.id=d.project_id WHERE d.id=? AND p.id=? AND p.user_id=?",
                          (draft_id, project["id"], uid))
    else:
        draft = query_one("SELECT * FROM draft_versions WHERE project_id=? ORDER BY version_no DESC LIMIT 1", (project["id"],))
    if not draft or not str(draft.get("content") or "").strip():
        raise state_invalid("请先保存非空稿件，再进入视觉生产。")
    return draft


def _source_snapshot(uid: str, project: dict, draft: dict) -> dict:
    profile = query_one("SELECT content_json,version_no FROM profile_versions WHERE user_id=? AND active=1 ORDER BY created_at DESC LIMIT 1", (uid,))
    return {"project_id": project["id"], "project_title": project.get("title") or "",
            "goal": project.get("goal") or "", "platform": draft.get("platform") or "",
            "draft_version_id": draft["id"], "draft_version": draft.get("version_no"),
            "draft_content": draft.get("content") or "", "ip_profile": jloads(profile.get("content_json") if profile else None, {}),
            "ip_profile_version": profile.get("version_no") if profile else None}


def _compile(template: str, fields: dict, ratio: str) -> str:
    replacements = {"{{RATIO}}": ratio, "{{MAIN_TOPIC}}": str(fields.get("main_topic") or fields.get("title") or ""),
                    "{{TITLE}}": str(fields.get("title") or ""), "{{SUBTITLE}}": str(fields.get("subtitle") or ""),
                    "{{CATEGORY}}": str(fields.get("category") or ""),
                    "{{FEATURES}}": "、".join(map(str, fields.get("features") or [])),
                    "{{KEYWORDS}}": "、".join(map(str, fields.get("keywords") or []))}
    output = template
    for key, value in replacements.items():
        output = output.replace(key, value)
    return output


def _reference_paths(uid: str, use_person: bool, use_logo: bool = True,
                     asset_ids: list[str] | None = None) -> list[str]:
    profile = _profile_row(uid)
    paths = []
    if use_person and profile.get("person_image_path"):
        paths.append(profile["person_image_path"])
    if use_logo and profile.get("logo_path"):
        paths.append(profile["logo_path"])
    for asset_id in asset_ids or []:
        row = query_one("SELECT path FROM visual_assets WHERE id=? AND user_id=? AND active=1", (asset_id, uid))
        if row:
            paths.append(row["path"])
    return list(dict.fromkeys(paths))[:16]


def _save_upload(uid: str, kind: str, file: UploadFile) -> dict:
    raw = file.file.read(12 * 1024 * 1024)
    if not raw or len(raw) > 10 * 1024 * 1024:
        raise validation_failed("图片不能为空且不能超过 10 MB。")
    try:
        image = Image.open(io.BytesIO(raw)); image.verify()
    except Exception:
        raise validation_failed("上传文件不是有效图片。")
    suffix = {"JPEG": ".jpg", "PNG": ".png", "WEBP": ".webp"}.get(image.format, ".png")
    asset_id = new_id("VA")
    path = _visual_dir(uid) / f"{asset_id}{suffix}"
    path.write_bytes(raw)
    stamp = now()
    execute("INSERT INTO visual_assets(id,user_id,kind,name,path,metadata_json,active,created_at,updated_at) VALUES(?,?,?,?,?,?,1,?,?)",
            (asset_id, uid, kind, file.filename or f"{kind}{suffix}", str(path), jdumps({"bytes": len(raw)}), stamp, stamp))
    column = "person_image_path" if kind == "person" else "logo_path"
    execute(f"UPDATE visual_profiles SET {column}=?,updated_at=? WHERE user_id=?", (str(path), stamp, uid))
    return {"id": asset_id, "kind": kind, "name": file.filename or path.name,
            "file_url": f"/api/visuals/assets/{asset_id}/file", "note": "视觉资产已保存，可在生成时引用。"}


@router.get("/profile")
def get_profile():
    uid = current_user_id(); row = _profile_row(uid)
    return ok({"brand": jloads(row.get("brand_json"), {}), "default_template_id": row.get("default_template_id"),
               "has_person_image": bool(row.get("person_image_path")), "has_logo": bool(row.get("logo_path"))})


@router.post("/profile/person-image")
def upload_person_image(file: UploadFile = File(...)):
    return ok(_save_upload(current_user_id(), "person", file))


@router.post("/profile/logo")
def upload_logo(file: UploadFile = File(...)):
    return ok(_save_upload(current_user_id(), "logo", file))


@router.get("/profile/person-image/file")
def profile_person_file():
    row = _profile_row(current_user_id()); path = Path(row.get("person_image_path") or "")
    if not path.is_file(): raise not_found("人物参考图", "default")
    return FileResponse(path)


@router.get("/profile/logo/file")
def profile_logo_file():
    row = _profile_row(current_user_id()); path = Path(row.get("logo_path") or "")
    if not path.is_file(): raise not_found("品牌 Logo", "default")
    return FileResponse(path)


@router.get("/assets")
def list_visual_assets():
    rows = query("SELECT * FROM visual_assets WHERE user_id=? AND active=1 ORDER BY updated_at DESC", (current_user_id(),))
    for row in rows:
        row["metadata"] = jloads(row.pop("metadata_json"), {})
        row["file_url"] = f"/api/visuals/assets/{row['id']}/file"
        row.pop("path", None)
    return ok(rows)


@router.get("/assets/{asset_id}/file")
def visual_asset_file(asset_id: str):
    row = query_one("SELECT path FROM visual_assets WHERE id=? AND user_id=? AND active=1", (asset_id, current_user_id()))
    path = Path(row.get("path") or "") if row else None
    if not path or not path.is_file(): raise not_found("视觉资产", asset_id)
    return FileResponse(path)


@router.delete("/assets/{asset_id}")
def delete_visual_asset(asset_id: str):
    uid = current_user_id()
    row = query_one("SELECT id FROM visual_assets WHERE id=? AND user_id=? AND active=1", (asset_id, uid))
    if not row: raise not_found("视觉资产", asset_id)
    execute("UPDATE visual_assets SET active=0,updated_at=? WHERE id=? AND user_id=?", (now(), asset_id, uid))
    return ok({"id": asset_id, "note": "资产已归档；历史成品仍保留引用。"})


def _template_out(row: dict, uid: str) -> dict:
    result = dict(row); result["template"] = jloads(result.pop("template_json"), {})
    result["is_default"] = row["id"] == _profile_row(uid).get("default_template_id")
    result["preview_url"] = f"/api/visuals/templates/{row['id']}/preview"
    return result


@router.get("/templates")
def list_templates(template_type: str = "all"):
    uid = current_user_id(); default = _ensure_builtin(uid)
    profile = _profile_row(uid)
    if not profile.get("default_template_id"):
        execute("UPDATE visual_profiles SET default_template_id=?,updated_at=? WHERE user_id=?", (default, now(), uid))
    params: list = [uid]; where = "user_id=? AND active=1"
    if template_type != "all": where += " AND template_type=?"; params.append(template_type)
    return ok([_template_out(row, uid) for row in query(f"SELECT * FROM cover_templates WHERE {where} ORDER BY builtin DESC,created_at", tuple(params))])


@router.get("/templates/{template_id}/preview")
def template_preview(template_id: str):
    uid = current_user_id(); _ensure_builtin(uid)
    row = query_one("SELECT * FROM cover_templates WHERE id=? AND user_id=? AND active=1", (template_id, uid))
    if not row: raise not_found("视觉模板", template_id)
    preview_id = f"template-{hashlib.sha1(template_id.encode()).hexdigest()[:12]}"
    template_data = jloads(row.get("template_json"), {})
    if row.get("template_type") == "graphic":
        path, _ = _render_graphic_page(uid, preview_id,
                                       {"page_index": 2, "role": "method", "layout_key": "steps",
                                        "title": "把复杂方法讲清楚", "body": "一页只讲一个信息任务，先结论，再给执行路径。",
                                        "bullets": ["先明确问题", "拆成三个动作", "最后给出检查标准"]},
                                       row.get("ratio") or "3:4", template_data)
    else:
        path, _ = _render_cover(uid, preview_id,
                                {"main_topic": "AI内容工作台", "title": "让创作真正流动起来", "subtitle": "从洞察到发布复盘",
                                 "category": "知识IP", "features": ["内容洞察", "稿件创作", "数据复盘"]},
                                row.get("ratio") or "3:4", None, template_data)
    return FileResponse(path, media_type="image/png")


class TemplateIn(BaseModel):
    name: str = Field(min_length=2, max_length=80)
    ratio: str = "3:4"
    template_type: str = "cover"
    prompt_template: str = Field(min_length=20, max_length=12000)
    template: dict = {}


@router.post("/templates")
def create_template(body: TemplateIn):
    uid = current_user_id()
    if body.ratio not in RATIOS or body.template_type not in {"cover", "graphic"}:
        raise validation_failed("模板必须明确属于封面或图文，并使用支持的比例。")
    template_id = new_id("CVT"); stamp = now()
    execute("INSERT INTO cover_templates(id,user_id,name,ratio,template_json,prompt_template,builtin,active,created_at,updated_at,template_type,layout_version) VALUES(?,?,?,?,?,?,0,1,?,?,?,3)",
            (template_id, uid, body.name.strip(), body.ratio, jdumps(body.template), body.prompt_template.strip(), stamp, stamp, body.template_type))
    return ok({"id": template_id, "note": "自定义模板已保存。"})


@router.put("/templates/{template_id}")
def update_template(template_id: str, body: TemplateIn):
    uid = current_user_id(); row = query_one("SELECT builtin FROM cover_templates WHERE id=? AND user_id=? AND active=1", (template_id, uid))
    if not row: raise not_found("视觉模板", template_id)
    if row["builtin"]: raise state_invalid("内置模板不可覆盖，请复制为自定义模板。")
    execute("UPDATE cover_templates SET name=?,ratio=?,template_json=?,prompt_template=?,template_type=?,updated_at=? WHERE id=? AND user_id=?",
            (body.name.strip(), body.ratio, jdumps(body.template), body.prompt_template.strip(), body.template_type, now(), template_id, uid))
    return ok({"id": template_id, "note": "模板已更新。"})


@router.post("/templates/{template_id}/default")
def set_default_template(template_id: str):
    uid = current_user_id()
    if not query_one("SELECT id FROM cover_templates WHERE id=? AND user_id=? AND active=1", (template_id, uid)):
        raise not_found("视觉模板", template_id)
    execute("UPDATE visual_profiles SET default_template_id=?,updated_at=? WHERE user_id=?", (template_id, now(), uid))
    return ok({"id": template_id, "note": "已设为默认封面模板。"})


@router.delete("/templates/{template_id}")
def delete_template(template_id: str):
    uid = current_user_id(); row = query_one("SELECT builtin FROM cover_templates WHERE id=? AND user_id=? AND active=1", (template_id, uid))
    if not row: raise not_found("视觉模板", template_id)
    if row["builtin"]: raise state_invalid("内置模板不能删除。")
    execute("UPDATE cover_templates SET active=0,updated_at=? WHERE id=? AND user_id=?", (now(), template_id, uid))
    return ok({"id": template_id, "note": "模板已归档。"})


class PromptPresetIn(BaseModel):
    name: str = Field(min_length=2, max_length=80)
    scope: str = "cover"
    template_text: str = Field(min_length=20, max_length=16000)
    negative_prompt: str = Field(default="", max_length=4000)


@router.get("/prompt-presets")
def list_prompt_presets(scope: str = "all"):
    uid = current_user_id(); _ensure_prompt_presets(uid)
    params: list = [uid]; where = "user_id=? AND active=1"
    if scope != "all": where += " AND scope=?"; params.append(scope)
    return ok(query(f"SELECT * FROM visual_prompt_presets WHERE {where} ORDER BY builtin DESC,updated_at DESC", tuple(params)))


@router.post("/prompt-presets")
def create_prompt_preset(body: PromptPresetIn):
    uid = current_user_id()
    if body.scope not in {"cover", "graphic_note"}: raise validation_failed("提示词预设必须属于封面或图文。")
    preset_id = new_id("VPP"); stamp = now()
    execute("INSERT INTO visual_prompt_presets(id,user_id,name,scope,template_text,negative_prompt,builtin,active,created_at,updated_at) VALUES(?,?,?,?,?,?,0,1,?,?)",
            (preset_id, uid, body.name.strip(), body.scope, body.template_text.strip(), body.negative_prompt.strip(), stamp, stamp))
    return ok({"id": preset_id, "note": "提示词预设已保存。"})


@router.delete("/prompt-presets/{preset_id}")
def delete_prompt_preset(preset_id: str):
    uid = current_user_id(); row = query_one("SELECT builtin FROM visual_prompt_presets WHERE id=? AND user_id=? AND active=1", (preset_id, uid))
    if not row: raise not_found("提示词预设", preset_id)
    if row["builtin"]: raise state_invalid("内置提示词预设不能删除。")
    execute("UPDATE visual_prompt_presets SET active=0,updated_at=? WHERE id=? AND user_id=?", (now(), preset_id, uid))
    return ok({"id": preset_id, "note": "提示词预设已归档。"})


class PromptOptimizeIn(BaseModel):
    creation_project_id: str
    draft_version_id: str | None = None
    output_kind: str = "cover"
    prompt_mode: str = "smart"
    preset_id: str | None = None
    ratio: str = "3:4"
    raw_prompt: str = Field(default="", max_length=8000)
    fields: dict = {}


@router.post("/prompts/optimize")
def optimize_prompt(body: PromptOptimizeIn):
    uid = current_user_id(); _ensure_prompt_presets(uid)
    if body.output_kind not in {"cover", "graphic_note"} or body.prompt_mode not in {"smart", "custom"}:
        raise validation_failed("提示词模式或视觉类型无效。")
    project = owned_row("creation_projects", body.creation_project_id, uid, label="创作项目")
    draft = _draft_for_project(uid, project, body.draft_version_id)
    preset = query_one("SELECT * FROM visual_prompt_presets WHERE id=? AND user_id=? AND active=1",
                       (body.preset_id, uid)) if body.preset_id else query_one(
        "SELECT * FROM visual_prompt_presets WHERE user_id=? AND scope=? AND builtin=1 AND active=1 ORDER BY created_at LIMIT 1",
        (uid, body.output_kind))
    if not preset: raise not_found("提示词预设", body.preset_id or body.output_kind)
    snapshot = _source_snapshot(uid, project, draft)
    payload = {"output_kind": body.output_kind, "title": project.get("title") or "", "goal": project.get("goal") or "",
               "platform": draft.get("platform") or "", "ratio": body.ratio, "source_text": draft.get("content") or "",
               "raw_prompt": body.raw_prompt, "ip_profile": snapshot.get("ip_profile") or {}, "fields": body.fields}
    from ..providers import get_llm_provider
    optimized = get_llm_provider().optimize_visual_prompt(payload)
    fields = {"main_topic": body.fields.get("main_topic") or optimized.get("title") or project.get("title") or "",
              "title": body.fields.get("title") or optimized.get("title") or project.get("title") or "",
              "subtitle": body.fields.get("subtitle") or optimized.get("subtitle") or "",
              "category": body.fields.get("category") or "知识IP",
              "features": body.fields.get("features") or optimized.get("features") or [],
              "keywords": body.fields.get("keywords") or optimized.get("keywords") or []}
    if body.output_kind == "cover":
        fields = _sanitize_cover_fields(fields, project.get("title") or "")
    compiled = _compile(preset["template_text"], fields, body.ratio)
    optimized_direction = (str(optimized.get("optimized_prompt") or "")
                           if body.output_kind == "graphic_note"
                           else _safe_cover_direction(optimized.get("optimized_prompt") or ""))
    if optimized_direction:
        compiled += "\n\n视觉总监优化要求：" + optimized_direction
    if body.output_kind == "cover":
        compiled += "\n\n" + _cover_visible_contract(fields)
    run_id = new_id("VPR"); source_hash = hashlib.sha256((draft.get("content") or "").encode("utf-8")).hexdigest()
    execute("INSERT INTO visual_prompt_runs(id,user_id,creation_project_id,draft_version_id,output_kind,prompt_mode,preset_id,source_text_hash,raw_user_prompt,optimized_prompt,compiled_prompt,negative_prompt,source_snapshot_json,provider,model,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (run_id, uid, project["id"], draft["id"], body.output_kind, body.prompt_mode, preset["id"], source_hash,
              body.raw_prompt, optimized_direction, compiled,
             optimized.get("negative_prompt") or preset.get("negative_prompt") or "", jdumps(snapshot),
             optimized.get("provider") or "", optimized.get("model") or "", now()))
    return ok({"id": run_id, "raw_prompt": body.raw_prompt, "optimized_prompt": optimized_direction,
               "compiled_prompt": compiled, "negative_prompt": optimized.get("negative_prompt") or preset.get("negative_prompt") or "",
               "fields": fields, "sources": {"project_id": project["id"], "draft_version_id": draft["id"],
                                               "ip_profile_version": snapshot.get("ip_profile_version")},
               "provider": optimized.get("provider") or "", "model": optimized.get("model") or ""})


class GraphicOutlineIn(BaseModel):
    creation_project_id: str
    draft_version_id: str | None = None
    page_count: int = Field(default=6, ge=3, le=12)
    narrative_mode: str = "auto"


@router.post("/graphic-outline")
def build_graphic_outline(body: GraphicOutlineIn):
    uid = current_user_id(); project = owned_row("creation_projects", body.creation_project_id, uid, label="创作项目")
    draft = _draft_for_project(uid, project, body.draft_version_id)
    if body.narrative_mode not in GRAPHIC_NARRATIVES:
        raise validation_failed("图文叙事策略无效。")
    from ..providers import get_llm_provider
    planned = get_llm_provider().plan_graphic_document({"title": project.get("title") or "", "goal": project.get("goal") or "",
                                                        "platform": draft.get("platform") or "", "source_text": draft.get("content") or "",
                                                        "page_count": body.page_count,
                                                        "narrative_mode": body.narrative_mode})
    pages = []
    for index, item in enumerate((planned.get("pages") or [])[:body.page_count], 1):
        role = str(item.get("role") or ("cover" if index == 1 else "insight"))
        layout = str(item.get("layout_key") or ("hero" if index == 1 else "editorial"))
        pages.append({"index": index, "page_index": index, "role": role if role in GRAPHIC_ROLES else "insight",
                      "title": str(item.get("title") or project.get("title") or "")[:160],
                      "body": str(item.get("body") or "")[:2000],
                      "bullets": [str(x)[:240] for x in (item.get("bullets") or [])[:8]],
                      "layout_key": layout if layout in GRAPHIC_LAYOUTS else "editorial",
                      "visual_prompt": str(item.get("visual_prompt") or "")[:2000], "locked": False})
    if len(pages) < 3: raise state_invalid("图文规划少于 3 页，请检查稿件内容后重试。")
    return ok({"project_id": project["id"], "draft_version_id": draft["id"], "title": planned.get("title") or project.get("title"),
               "pages": pages, "provider": planned.get("provider") or "", "model": planned.get("model") or "",
               "narrative_mode": planned.get("narrative_mode") or body.narrative_mode,
               "note": "已按稿件内容和叙事策略生成逐页结构；可继续编辑、排序和锁定后再出图。"})


class CoverCreateIn(BaseModel):
    creation_project_id: str
    draft_version_id: str | None = None
    template_id: str | None = None
    ratio: str = "3:4"
    content_type: str = "cover"
    generation_mode: str = "local_template"
    quantity: int = Field(default=1, ge=1, le=12)
    use_person: bool = False
    use_logo: bool = True
    reference_asset_ids: list[str] = []
    prompt_run_id: str | None = None
    custom_prompt: str = Field(default="", max_length=8000)
    fields: dict = {}
    graphic_pages: list[dict] = []


def _provider_generate(uid: str, prompt: str, ratio: str, references: list[str]) -> dict:
    from ..image_provider import get_image_provider
    from ..providers import get_setting
    return get_image_provider().generate(prompt, ratio,
                                         quality=str(get_setting("image", "quality", "high") or "high"),
                                         reference_images=references)


def _prompt_for_request(uid: str, body: CoverCreateIn, template: dict, fields: dict, project: dict, draft: dict) -> tuple[str, str | None]:
    fields = _sanitize_cover_fields(fields, project.get("title") or "")
    base_template = template.get("prompt_template") or COVER_PROMPT
    optimized_direction = ""
    prompt_run_id = None
    if body.prompt_run_id:
        run = query_one("SELECT * FROM visual_prompt_runs WHERE id=? AND user_id=? AND creation_project_id=? AND draft_version_id=?",
                        (body.prompt_run_id, uid, project["id"], draft["id"]))
        if not run: raise not_found("提示词版本", body.prompt_run_id)
        preset = query_one("SELECT template_text FROM visual_prompt_presets WHERE id=? AND user_id=? AND active=1",
                           (run.get("preset_id"), uid))
        if preset and preset.get("template_text"):
            base_template = preset["template_text"]
        optimized_direction = _safe_cover_direction(run.get("optimized_prompt") or "")
        prompt_run_id = run["id"]
    prompt = _compile(base_template, fields, body.ratio)
    if optimized_direction:
        prompt += "\n\n视觉总监优化要求：" + optimized_direction
    if body.custom_prompt.strip(): prompt += "\n\n本次补充要求：" + body.custom_prompt.strip()
    prompt += "\n\n" + _cover_visible_contract(fields)
    return prompt, prompt_run_id


@router.post("/covers")
def create_cover(body: CoverCreateIn):
    uid = current_user_id()
    if body.ratio not in RATIOS or body.generation_mode not in GENERATION_MODES:
        raise validation_failed("画面比例或生成方式无效。")
    if body.content_type not in {"cover", "carousel", "long_image"}:
        raise validation_failed("视觉类型必须是封面、图文或长图。")
    if body.content_type == "cover" and body.quantity > 4:
        raise validation_failed("单次封面候选最多 4 张。")
    project = owned_row("creation_projects", body.creation_project_id, uid, label="创作项目")
    draft = _draft_for_project(uid, project, body.draft_version_id)
    profile = _profile_row(uid); default_id = _ensure_builtin(uid)
    template_id = body.template_id or profile.get("default_template_id") or default_id
    template = query_one("SELECT * FROM cover_templates WHERE id=? AND user_id=? AND active=1", (template_id, uid))
    if not template: raise not_found("视觉模板", template_id)
    template_data = jloads(template.get("template_json"), {})
    fields = _sanitize_cover_fields({
        "main_topic": project.get("title") or "", "title": project.get("title") or "",
        "subtitle": "", "category": "知识IP", "features": [], "keywords": [],
        **body.fields,
    }, project.get("title") or "")
    prompt, prompt_run_id = _prompt_for_request(uid, body, template, fields, project, draft)
    batch_id = new_id("VB"); stamp = now()
    execute("INSERT INTO visual_batches(id,user_id,title,creation_project_id,draft_version_id,template_id,content_type,ratio,generation_mode,quantity,status,request_json,created_at,updated_at,deleted_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,NULL)",
            (batch_id, uid, f"{project.get('title') or '未命名'} · 封面候选", project["id"], draft["id"], template_id,
             body.content_type, body.ratio, body.generation_mode, body.quantity, "GENERATING",
             jdumps(body.model_dump() if hasattr(body, "model_dump") else body.dict()), stamp, stamp))
    references = _reference_paths(uid, body.use_person, body.use_logo, body.reference_asset_ids)
    items = []
    try:
        for index in range(1, body.quantity + 1):
            page_source = (body.graphic_pages[index - 1]
                           if body.content_type == "carousel" and index <= len(body.graphic_pages) else {})
            page_role = str(page_source.get("role") or "cover")
            item_fields = dict(fields)
            if page_source:
                item_fields.update({
                    "main_topic": str(page_source.get("title") or fields.get("main_topic") or ""),
                    "title": str(page_source.get("title") or fields.get("title") or ""),
                    "subtitle": str(page_source.get("body") or ""),
                    "features": page_source.get("bullets") or [],
                })
            item_fields = _sanitize_cover_fields(item_fields, project.get("title") or "")
            cover_id = new_id("CV"); provider = "local_compositor"; model = ""; cost = 0.0; source_path = None
            generation = {"batch_id": batch_id, "index": index, "total": body.quantity,
                          "mode": body.generation_mode, "prompt_run_id": prompt_run_id,
                          "reference_count": len(references)}
            if body.generation_mode in {"ai_full", "ai_hybrid", "ai_background"}:
                direct = body.generation_mode == "ai_full"
                provider_prompt = prompt if direct else (
                    prompt + "\n只生成无文字、无数字、无Logo的视觉底图；保留足够留白供后续中文排版。")
                result = _provider_generate(uid, provider_prompt, body.ratio, references)
                provider, model, cost = result["provider"], result["model"], float(result.get("cost") or 0)
                source_path = str(_visual_dir(uid) / f"{cover_id}-provider.png"); Path(source_path).write_bytes(result["bytes"])
                generation.update({"request_id": result.get("request_id") or "", "revised_prompt": result.get("revised_prompt") or ""})
                if direct:
                    preview = Path(source_path); layout = {"canvas": list(RATIOS[body.ratio]), "safe_box": None,
                                                           "overflow": None, "layout": "provider_full_image",
                                                           "content_check": "人工确认中文与身份一致性"}
                else:
                    preview, layout = _render_cover(uid, cover_id, item_fields, body.ratio,
                                                    profile.get("person_image_path") if body.use_person else None,
                                                    template_data, source_path)
            else:
                preview, layout = _render_cover(uid, cover_id, item_fields, body.ratio,
                                                profile.get("person_image_path") if body.use_person else None,
                                                template_data)
            generation["layout_report"] = layout
            execute("INSERT INTO cover_projects(id,user_id,creation_project_id,draft_version_id,template_id,fields_json,prompt,preview_path,status,created_at,updated_at,content_type,ratio,provider,model,generation_mode,cost,source_image_path,generation_json,error_message,deleted_at,visual_batch_id,item_index,page_role,revision_no,replaces_cover_id) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,NULL,?,?,?,1,NULL)",
                    (cover_id, uid, project["id"], draft["id"], template_id, jdumps(item_fields), prompt, str(preview), "PREVIEW_READY",
                     now(), now(), body.content_type, body.ratio, provider, model, body.generation_mode, cost, source_path,
                     jdumps(generation), "", batch_id, index, page_role))
            items.append({"id": cover_id, "status": "PREVIEW_READY", "fields": item_fields, "page_role": page_role,
                          "provider": provider, "model": model, "layout_report": layout,
                          "preview_url": f"/api/visuals/covers/{cover_id}/preview"})
        execute("UPDATE visual_batches SET status='READY',updated_at=? WHERE id=?", (now(), batch_id))
    except Exception:
        execute("UPDATE visual_batches SET status='FAILED',updated_at=? WHERE id=?", (now(), batch_id)); raise
    try:
        from ..asset_center import sync_asset_index
        sync_asset_index(uid)
    except Exception:
        pass
    return ok({**items[0], "items": items, "batch_id": batch_id,
               "identity_note": "已把选中的人物/品牌资产作为参考发送给图片模型。" if references else "本次未使用参考资产。"})


def _cover_out(row: dict) -> dict:
    result = dict(row); result["fields"] = jloads(result.pop("fields_json"), {})
    result["generation"] = jloads(result.pop("generation_json"), {})
    result["preview_url"] = f"/api/visuals/covers/{result['id']}/preview"
    return result


@router.get("/covers")
def list_covers():
    return ok([_cover_out(row) for row in query("SELECT * FROM cover_projects WHERE user_id=? AND deleted_at IS NULL ORDER BY created_at DESC,item_index", (current_user_id(),))])


@router.get("/batches")
def list_batches(content_type: str = "all"):
    uid = current_user_id(); params: list = [uid]; where = "user_id=? AND deleted_at IS NULL"
    if content_type != "all": where += " AND content_type=?"; params.append(content_type)
    rows = query(f"SELECT * FROM visual_batches WHERE {where} ORDER BY created_at DESC", tuple(params))
    for row in rows:
        row["request"] = jloads(row.pop("request_json"), {})
        row["items"] = [_cover_out(item) for item in query("SELECT * FROM cover_projects WHERE visual_batch_id=? AND user_id=? AND deleted_at IS NULL ORDER BY item_index,revision_no", (row["id"], uid))]
        for item in row["items"]: item["selected"] = item["id"] == row.get("selected_cover_id")
    return ok(rows)


class BatchRenameIn(BaseModel):
    title: str = Field(min_length=2, max_length=120)


@router.put("/batches/{batch_id}")
def rename_batch(batch_id: str, body: BatchRenameIn):
    uid = current_user_id()
    if not query_one("SELECT id FROM visual_batches WHERE id=? AND user_id=? AND deleted_at IS NULL", (batch_id, uid)):
        raise not_found("视觉批次", batch_id)
    execute("UPDATE visual_batches SET title=?,updated_at=? WHERE id=? AND user_id=?", (body.title.strip(), now(), batch_id, uid))
    return ok({"id": batch_id, "title": body.title.strip()})


@router.delete("/batches/{batch_id}")
def delete_batch(batch_id: str, force: bool = False):
    uid = current_user_id()
    batch = query_one("SELECT id,selected_cover_id FROM visual_batches WHERE id=? AND user_id=? AND deleted_at IS NULL", (batch_id, uid))
    if not batch:
        raise not_found("视觉批次", batch_id)
    adopted = bool(batch.get("selected_cover_id") and query_one(
        "SELECT id FROM visual_deliverables WHERE user_id=? AND kind='cover' AND entity_id=? AND selected=1",
        (uid, batch["selected_cover_id"])))
    if adopted and not force:
        raise state_invalid("该合集包含已同步到创作项目的封面。请先选定其他封面，再归档整个合集。")
    stamp = now()
    with transaction():
        if adopted:
            _detach_deliverable(uid, "cover", batch["selected_cover_id"])
        execute("UPDATE visual_batches SET selected_cover_id=NULL WHERE id=? AND user_id=?", (batch_id, uid))
        execute("UPDATE visual_batches SET deleted_at=?,updated_at=? WHERE id=? AND user_id=?", (stamp, stamp, batch_id, uid))
        execute("UPDATE cover_projects SET deleted_at=?,updated_at=? WHERE visual_batch_id=? AND user_id=? AND deleted_at IS NULL", (stamp, stamp, batch_id, uid))
    return ok({"id": batch_id, "detached": adopted,
               "note": "封面合集已从当前工作区删除；原始文件仍保留在本地备份中。"})


def _upsert_deliverable(uid: str, project_id: str, draft_id: str, kind: str, entity_id: str, metadata: dict) -> str:
    current = query_one("SELECT id FROM visual_deliverables WHERE user_id=? AND kind=? AND entity_id=?", (uid, kind, entity_id))
    deliverable_id = current["id"] if current else new_id("VDL"); stamp = now()
    with transaction():
        execute("UPDATE visual_deliverables SET selected=0,updated_at=? WHERE user_id=? AND creation_project_id=? AND kind=?",
                (stamp, uid, project_id, kind))
        if current:
            execute("UPDATE visual_deliverables SET selected=1,status='SELECTED',metadata_json=?,updated_at=? WHERE id=? AND user_id=?",
                    (jdumps(metadata), stamp, deliverable_id, uid))
        else:
            execute("INSERT INTO visual_deliverables(id,user_id,creation_project_id,draft_version_id,kind,entity_id,status,selected,metadata_json,created_at,updated_at) VALUES(?,?,?,?,?,?,'SELECTED',1,?,?,?)",
                    (deliverable_id, uid, project_id, draft_id, kind, entity_id, jdumps(metadata), stamp, stamp))
        execute("UPDATE creation_projects SET visual_status='READY',visual_deliverable_id=? WHERE id=? AND user_id=?",
                (deliverable_id, project_id, uid))
        execute("UPDATE content_tasks SET visual_status='READY',visual_deliverable_id=? WHERE project_id=? AND user_id=? AND status<>'CANCELLED'",
                (deliverable_id, project_id, uid))
    return deliverable_id


def _detach_deliverable(uid: str, kind: str, entity_id: str) -> bool:
    """Remove an adopted visual from the active workflow without erasing its audit row."""
    row = query_one("SELECT id,creation_project_id FROM visual_deliverables WHERE user_id=? AND kind=? AND entity_id=? AND selected=1",
                    (uid, kind, entity_id))
    if not row:
        return False
    stamp = now()
    execute("UPDATE visual_deliverables SET selected=0,status='REMOVED',updated_at=? WHERE id=? AND user_id=?",
            (stamp, row["id"], uid))
    execute("UPDATE creation_projects SET visual_status='PENDING',visual_deliverable_id=NULL WHERE id=? AND user_id=? AND visual_deliverable_id=?",
            (row["creation_project_id"], uid, row["id"]))
    execute("UPDATE content_tasks SET visual_status='PENDING',visual_deliverable_id=NULL WHERE project_id=? AND user_id=? AND visual_deliverable_id=? AND status<>'CANCELLED'",
            (row["creation_project_id"], uid, row["id"]))
    return True


@router.post("/covers/{cover_id}/select")
def select_cover(cover_id: str):
    uid = current_user_id(); row = query_one("SELECT * FROM cover_projects WHERE id=? AND user_id=? AND deleted_at IS NULL", (cover_id, uid))
    if not row or not row.get("visual_batch_id"): raise not_found("视觉成品", cover_id)
    execute("UPDATE visual_batches SET selected_cover_id=?,updated_at=? WHERE id=? AND user_id=?",
            (cover_id, now(), row["visual_batch_id"], uid))
    deliverable_id = _upsert_deliverable(uid, row["creation_project_id"], row["draft_version_id"], "cover", cover_id,
                                         {"batch_id": row["visual_batch_id"], "ratio": row["ratio"], "provider": row["provider"]})
    return ok({"id": cover_id, "batch_id": row["visual_batch_id"], "deliverable_id": deliverable_id,
               "note": "封面已选定，并已同步到创作项目和内容计划。"})


class RegenerateCoverIn(BaseModel):
    fields: dict = {}


@router.post("/covers/{cover_id}/regenerate")
def regenerate_cover(cover_id: str, body: RegenerateCoverIn):
    uid = current_user_id(); old = query_one("SELECT * FROM cover_projects WHERE id=? AND user_id=? AND deleted_at IS NULL", (cover_id, uid))
    if not old: raise not_found("视觉成品", cover_id)
    template = query_one("SELECT * FROM cover_templates WHERE id=? AND user_id=?", (old.get("template_id"), uid))
    profile = _profile_row(uid); fields = {**jloads(old.get("fields_json"), {}), **body.fields}
    new_id_value = new_id("CV")
    preview, report = _render_cover(uid, new_id_value, fields, old.get("ratio") or "3:4",
                                    profile.get("person_image_path"), jloads(template.get("template_json") if template else None, {}),
                                    old.get("source_image_path"))
    revision = int(old.get("revision_no") or 1) + 1
    generation = jloads(old.get("generation_json"), {}); generation.update({"regenerated_from": cover_id, "layout_report": report})
    execute("INSERT INTO cover_projects(id,user_id,creation_project_id,draft_version_id,template_id,fields_json,prompt,preview_path,status,created_at,updated_at,content_type,ratio,provider,model,generation_mode,cost,source_image_path,generation_json,error_message,deleted_at,visual_batch_id,item_index,page_role,revision_no,replaces_cover_id) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,NULL,?,?,?,?,?)",
            (new_id_value, uid, old.get("creation_project_id"), old.get("draft_version_id"), old.get("template_id"), jdumps(fields),
             old.get("prompt") or "", str(preview), "PREVIEW_READY", now(), now(), old.get("content_type") or "cover",
             old.get("ratio") or "3:4", "local_compositor", "", "layout_revision", 0.0, old.get("source_image_path"),
             jdumps(generation), "", old.get("visual_batch_id"), old.get("item_index") or 1, old.get("page_role") or "cover", revision, cover_id))
    return ok({"id": new_id_value, "batch_id": old.get("visual_batch_id"), "revision_no": revision,
               "layout_report": report, "preview_url": f"/api/visuals/covers/{new_id_value}/preview",
               "note": "已追加新版，旧版仍保留用于比较和追溯。"})


@router.get("/covers/{cover_id}/preview")
def cover_preview(cover_id: str):
    row = query_one("SELECT preview_path FROM cover_projects WHERE id=? AND user_id=? AND deleted_at IS NULL", (cover_id, current_user_id()))
    path = Path(row.get("preview_path") or "") if row else None
    if not path or not path.is_file(): raise not_found("视觉预览", cover_id)
    return FileResponse(path, media_type="image/png", filename=f"{cover_id}.png")


@router.delete("/covers/{cover_id}")
def delete_cover(cover_id: str, force: bool = False):
    uid = current_user_id()
    row = query_one("SELECT id,visual_batch_id FROM cover_projects WHERE id=? AND user_id=? AND deleted_at IS NULL", (cover_id, uid))
    if not row:
        raise not_found("视觉历史", cover_id)
    selected = query_one("SELECT id FROM visual_deliverables WHERE user_id=? AND kind='cover' AND entity_id=? AND selected=1",
                         (uid, cover_id))
    batch_selected = query_one("SELECT id FROM visual_batches WHERE id=? AND user_id=? AND selected_cover_id=? AND deleted_at IS NULL",
                               (row.get("visual_batch_id"), uid, cover_id)) if row.get("visual_batch_id") else None
    if (selected or batch_selected) and not force:
        raise state_invalid("该封面已经同步到创作项目或内容计划。请先选定其他候选，再删除当前封面。")
    stamp = now(); removed_batch = False
    with transaction():
        if selected:
            _detach_deliverable(uid, "cover", cover_id)
        if batch_selected:
            execute("UPDATE visual_batches SET selected_cover_id=NULL,updated_at=? WHERE id=? AND user_id=?",
                    (stamp, row.get("visual_batch_id"), uid))
        execute("UPDATE cover_projects SET deleted_at=?,updated_at=? WHERE id=? AND user_id=?", (stamp, stamp, cover_id, uid))
        if row.get("visual_batch_id") and not query_one(
                "SELECT id FROM cover_projects WHERE visual_batch_id=? AND user_id=? AND deleted_at IS NULL LIMIT 1",
                (row["visual_batch_id"], uid)):
            execute("UPDATE visual_batches SET deleted_at=?,updated_at=?,selected_cover_id=NULL WHERE id=? AND user_id=?",
                    (stamp, stamp, row["visual_batch_id"], uid))
            removed_batch = True
    return ok({"id": cover_id, "batch_id": row.get("visual_batch_id"), "batch_deleted": removed_batch,
               "detached": bool(selected), "note": "封面已从当前工作区删除。"})


class GraphicDocumentCreateIn(BaseModel):
    creation_project_id: str
    draft_version_id: str | None = None
    title: str = Field(default="", max_length=160)
    platform: str = "小红书图文"
    ratio: str = "3:4"
    theme_key: str = "editorial"
    generation_mode: str = "local_template"
    template_id: str | None = None
    prompt_run_id: str | None = None
    use_person: bool = False
    use_logo: bool = True
    reference_asset_ids: list[str] = []
    pages: list[dict] = Field(min_length=3, max_length=12)


def _graphic_out(row: dict, include_pages: bool = True) -> dict:
    result = dict(row)
    if include_pages:
        pages = query("SELECT * FROM graphic_pages WHERE document_id=? AND deleted_at IS NULL ORDER BY page_index", (row["id"],))
        for page in pages:
            page["bullets"] = jloads(page.pop("bullets_json"), [])
            revision = query_one("SELECT * FROM graphic_page_revisions WHERE id=? AND deleted_at IS NULL", (page.get("selected_revision_id"),)) if page.get("selected_revision_id") else None
            page["revision"] = revision
            page["preview_url"] = f"/api/visuals/graphic-pages/{page['id']}/preview" if revision and revision.get("image_path") else ""
        result["pages"] = pages
    return result


@router.post("/graphic-documents")
def create_graphic_document(body: GraphicDocumentCreateIn):
    uid = current_user_id()
    if body.ratio not in RATIOS or body.generation_mode not in GENERATION_MODES:
        raise validation_failed("图文比例或生成方式无效。")
    project = owned_row("creation_projects", body.creation_project_id, uid, label="创作项目")
    draft = _draft_for_project(uid, project, body.draft_version_id)
    _ensure_builtin(uid)
    template = query_one("SELECT * FROM cover_templates WHERE id=? AND user_id=? AND active=1 AND template_type='graphic'",
                         (body.template_id, uid)) if body.template_id else query_one(
        "SELECT * FROM cover_templates WHERE user_id=? AND active=1 AND template_type='graphic' ORDER BY builtin DESC,created_at LIMIT 1", (uid,))
    if not template: raise state_invalid("缺少可用的图文模板。")
    prompt_run = query_one("SELECT * FROM visual_prompt_runs WHERE id=? AND user_id=? AND output_kind='graphic_note'",
                           (body.prompt_run_id, uid)) if body.prompt_run_id else None
    document_id = new_id("GDC"); stamp = now(); source_hash = hashlib.sha256((draft.get("content") or "").encode()).hexdigest()
    execute("INSERT INTO graphic_documents(id,user_id,creation_project_id,draft_version_id,title,platform,ratio,theme_key,generation_mode,prompt_run_id,status,version_no,source_hash,created_at,updated_at,deleted_at) VALUES(?,?,?,?,?,?,?,?,?,?, 'GENERATING',1,?,?,?,NULL)",
            (document_id, uid, project["id"], draft["id"], body.title.strip() or project.get("title") or "未命名图文",
             body.platform, body.ratio, template["id"], body.generation_mode, prompt_run.get("id") if prompt_run else None,
             source_hash, stamp, stamp))
    template_data = jloads(template.get("template_json"), {})
    references = _reference_paths(uid, body.use_person, body.use_logo, body.reference_asset_ids)
    created_pages = []
    try:
        for index, source in enumerate(body.pages, 1):
            role = str(source.get("role") or ("cover" if index == 1 else "insight"))
            layout = str(source.get("layout_key") or ("hero" if index == 1 else "editorial"))
            page_id = new_id("GPG"); revision_id = new_id("GPR")
            page = {"id": page_id, "page_index": index, "role": role if role in GRAPHIC_ROLES else "insight",
                    "title": str(source.get("title") or "")[:160], "body": str(source.get("body") or "")[:2400],
                    "bullets": [str(x)[:300] for x in (source.get("bullets") or [])[:8]],
                    "layout_key": layout if layout in GRAPHIC_LAYOUTS else "editorial",
                    "visual_prompt": str(source.get("visual_prompt") or "")[:3000]}
            prompt = ((prompt_run.get("compiled_prompt") + "\n\n") if prompt_run else GRAPHIC_PROMPT + "\n\n") + (
                f"第{index}页，角色{page['role']}。标题：{page['title']}。正文：{page['body']}。"
                f"要点：{'；'.join(page['bullets'])}。版式：{page['layout_key']}。{page['visual_prompt']}")
            provider = "local_compositor"; model = ""; cost = 0.0; request_data = {"reference_count": len(references)}
            background = None
            if body.generation_mode in {"ai_full", "ai_hybrid", "ai_background"}:
                direct = body.generation_mode == "ai_full"
                provider_prompt = prompt if direct else prompt + (
                    "\n本次只生成这一页的无字视觉底图：严禁任何中文、英文、数字、Logo、水印、伪UI、"
                    "复杂拼贴、密集小物件或重复人物。只保留一个视觉焦点，整体低对比、低饱和，"
                    "至少60%画面为连续干净留白；不要生成卡片边框和正文区域，中文将由本地排版引擎添加。")
                result = _provider_generate(uid, provider_prompt, body.ratio, references)
                provider, model, cost = result["provider"], result["model"], float(result.get("cost") or 0)
                background = str(_visual_dir(uid) / f"{revision_id}-provider.png"); Path(background).write_bytes(result["bytes"])
                request_data.update({"request_id": result.get("request_id") or "", "provider_source": background})
                if direct:
                    image_path = Path(background); report = {"layout": "provider_full_image", "content_check": "人工确认中文准确性"}
                else:
                    image_path, report = _render_graphic_page(uid, revision_id, page, body.ratio, template_data, background)
            else:
                image_path, report = _render_graphic_page(uid, revision_id, page, body.ratio, template_data)
            request_data["layout_report"] = report
            execute("INSERT INTO graphic_pages(id,user_id,document_id,page_index,role,title,body,bullets_json,layout_key,visual_prompt,locked,status,selected_revision_id,created_at,updated_at,deleted_at) VALUES(?,?,?,?,?,?,?,?,?,?,?, 'READY',?,?,?,NULL)",
                    (page_id, uid, document_id, index, page["role"], page["title"], page["body"], jdumps(page["bullets"]),
                     page["layout_key"], page["visual_prompt"], 1 if source.get("locked") else 0, revision_id, now(), now()))
            execute("INSERT INTO graphic_page_revisions(id,user_id,page_id,revision_no,image_path,prompt,provider,model,generation_mode,cost,request_json,created_at,deleted_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,NULL)",
                    (revision_id, uid, page_id, 1, str(image_path), prompt, provider, model, body.generation_mode, cost, jdumps(request_data), now()))
            created_pages.append(page_id)
        execute("UPDATE graphic_documents SET status='READY',updated_at=? WHERE id=? AND user_id=?", (now(), document_id, uid))
    except Exception:
        execute("UPDATE graphic_documents SET status='FAILED',updated_at=? WHERE id=? AND user_id=?", (now(), document_id, uid)); raise
    return ok(_graphic_out(query_one("SELECT * FROM graphic_documents WHERE id=?", (document_id,))))


@router.get("/graphic-documents")
def list_graphic_documents():
    return ok([_graphic_out(row, include_pages=False) for row in query(
        "SELECT * FROM graphic_documents WHERE user_id=? AND deleted_at IS NULL ORDER BY updated_at DESC", (current_user_id(),))])


@router.get("/graphic-documents/{document_id}")
def get_graphic_document(document_id: str):
    row = query_one("SELECT * FROM graphic_documents WHERE id=? AND user_id=? AND deleted_at IS NULL", (document_id, current_user_id()))
    if not row: raise not_found("图文文档", document_id)
    return ok(_graphic_out(row))


class GraphicDocumentUpdateIn(BaseModel):
    title: str = Field(min_length=1, max_length=160)


@router.put("/graphic-documents/{document_id}")
def update_graphic_document(document_id: str, body: GraphicDocumentUpdateIn):
    uid = current_user_id()
    row = query_one("SELECT * FROM graphic_documents WHERE id=? AND user_id=? AND deleted_at IS NULL",
                    (document_id, uid))
    if not row:
        raise not_found("图文文档", document_id)
    with transaction():
        detached = _detach_deliverable(uid, "graphic_note", document_id) if row.get("status") == "FINALIZED" else False
        execute("UPDATE graphic_documents SET title=?,status=CASE WHEN status='FINALIZED' THEN 'DRAFT' ELSE status END,"
                "selected_export_id=CASE WHEN status='FINALIZED' THEN NULL ELSE selected_export_id END,updated_at=? "
                "WHERE id=? AND user_id=?", (body.title.strip(), now(), document_id, uid))
    return ok({"id": document_id, "title": body.title.strip(), "detached": detached,
               "note": "文档标题已保存。" + (" 原定稿已解除同步，请编辑完成后重新定稿。" if detached else "")})


class GraphicPageUpdateIn(BaseModel):
    title: str | None = None
    body: str | None = None
    bullets: list[str] | None = None
    role: str | None = None
    layout_key: str | None = None
    visual_prompt: str | None = None
    locked: bool | None = None


@router.put("/graphic-pages/{page_id}")
def update_graphic_page(page_id: str, body: GraphicPageUpdateIn):
    uid = current_user_id(); row = query_one(
        "SELECT p.*,d.status document_status FROM graphic_pages p JOIN graphic_documents d ON d.id=p.document_id "
        "WHERE p.id=? AND p.user_id=? AND p.deleted_at IS NULL AND d.deleted_at IS NULL", (page_id, uid))
    if not row: raise not_found("图文页面", page_id)
    values = body.model_dump(exclude_none=True) if hasattr(body, "model_dump") else body.dict(exclude_none=True)
    if row.get("locked") and set(values) - {"locked"}: raise state_invalid("页面已锁定，请先解锁再编辑。")
    role = values.get("role", row["role"]); layout = values.get("layout_key", row["layout_key"])
    if role not in GRAPHIC_ROLES or layout not in GRAPHIC_LAYOUTS: raise validation_failed("页面角色或版式无效。")
    substantive = bool(set(values) - {"locked"})
    with transaction():
        detached = (_detach_deliverable(uid, "graphic_note", row["document_id"])
                    if substantive and row.get("document_status") == "FINALIZED" else False)
        if substantive and row.get("document_status") == "FINALIZED":
            execute("UPDATE graphic_documents SET status='DRAFT',selected_export_id=NULL,updated_at=? WHERE id=? AND user_id=?",
                    (now(), row["document_id"], uid))
        execute("UPDATE graphic_pages SET role=?,title=?,body=?,bullets_json=?,layout_key=?,visual_prompt=?,locked=?,status='DRAFT',updated_at=? WHERE id=? AND user_id=?",
                (role, values.get("title", row["title"]), values.get("body", row["body"]),
                 jdumps(values.get("bullets", jloads(row.get("bullets_json"), []))), layout,
                 values.get("visual_prompt", row["visual_prompt"]), int(values.get("locked", row["locked"])), now(), page_id, uid))
    return ok({"id": page_id, "detached": detached,
               "note": "页面内容已保存；旧图仍保留，重新生成后会新增版本。" +
                       (" 原定稿已解除同步。" if detached else "")})


class GraphicReorderIn(BaseModel):
    page_ids: list[str] = Field(min_length=3, max_length=12)


@router.post("/graphic-documents/{document_id}/reorder")
def reorder_graphic_pages(document_id: str, body: GraphicReorderIn):
    uid = current_user_id(); rows = query("SELECT id FROM graphic_pages WHERE document_id=? AND user_id=? AND deleted_at IS NULL", (document_id, uid))
    if {row["id"] for row in rows} != set(body.page_ids): raise validation_failed("排序必须包含该文档的全部页面且不能重复。")
    with transaction():
        for index, page_id in enumerate(body.page_ids, 1):
            execute("UPDATE graphic_pages SET page_index=?,updated_at=? WHERE id=? AND user_id=?", (-index, now(), page_id, uid))
        for index, page_id in enumerate(body.page_ids, 1):
            execute("UPDATE graphic_pages SET page_index=?,updated_at=? WHERE id=? AND user_id=?", (index, now(), page_id, uid))
    return ok({"id": document_id, "page_ids": body.page_ids, "note": "页面顺序已保存。"})


@router.post("/graphic-pages/{page_id}/regenerate")
def regenerate_graphic_page(page_id: str):
    uid = current_user_id(); page = query_one("SELECT p.*,d.ratio,d.generation_mode,d.theme_key FROM graphic_pages p JOIN graphic_documents d ON d.id=p.document_id WHERE p.id=? AND p.user_id=? AND p.deleted_at IS NULL AND d.deleted_at IS NULL", (page_id, uid))
    if not page: raise not_found("图文页面", page_id)
    template = query_one("SELECT * FROM cover_templates WHERE id=? AND user_id=? AND active=1 AND template_type='graphic'",
                         (page.get("theme_key"), uid)) if page.get("theme_key") else None
    if not template:
        template = query_one("SELECT * FROM cover_templates WHERE user_id=? AND active=1 AND template_type='graphic' ORDER BY builtin DESC,created_at LIMIT 1", (uid,))
    revision_no = int((query_one("SELECT MAX(revision_no) n FROM graphic_page_revisions WHERE page_id=?", (page_id,)) or {}).get("n") or 0) + 1
    revision_id = new_id("GPR"); page["bullets"] = jloads(page.get("bullets_json"), [])
    template_data = jloads(template.get("template_json") if template else None, {})
    mode = page.get("generation_mode") or "local_template"
    prompt = (f"第{page.get('page_index')}页，角色{page.get('role')}。标题：{page.get('title')}。"
              f"正文：{page.get('body')}。要点：{'；'.join(page['bullets'])}。{page.get('visual_prompt') or ''}")
    provider = "local_compositor"; model = ""; cost = 0.0; request_data = {}; background = None
    if mode in {"ai_full", "ai_hybrid", "ai_background"}:
        direct = mode == "ai_full"
        provider_prompt = prompt if direct else prompt + (
            "\n只生成无字极简视觉底图：无任何文字数字Logo或伪UI、无复杂拼贴，单一视觉焦点、低对比，"
            "至少60%连续干净留白，中文由本地排版引擎添加。")
        result = _provider_generate(uid, provider_prompt, page.get("ratio") or "3:4", [])
        provider, model, cost = result["provider"], result["model"], float(result.get("cost") or 0)
        background = str(_visual_dir(uid) / f"{revision_id}-provider.png")
        Path(background).write_bytes(result["bytes"])
        request_data = {"request_id": result.get("request_id") or "", "provider_source": background}
        if direct:
            image_path = Path(background)
            report = {"layout": "provider_full_image", "content_check": "人工确认中文准确性"}
        else:
            image_path, report = _render_graphic_page(uid, revision_id, page, page.get("ratio") or "3:4", template_data, background)
    else:
        image_path, report = _render_graphic_page(uid, revision_id, page, page.get("ratio") or "3:4", template_data)
    request_data["layout_report"] = report
    execute("INSERT INTO graphic_page_revisions(id,user_id,page_id,revision_no,image_path,prompt,provider,model,generation_mode,cost,request_json,created_at,deleted_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,NULL)",
            (revision_id, uid, page_id, revision_no, str(image_path), prompt, provider, model, mode, cost, jdumps(request_data), now()))
    execute("UPDATE graphic_pages SET selected_revision_id=?,status='READY',updated_at=? WHERE id=? AND user_id=?", (revision_id, now(), page_id, uid))
    return ok({"id": page_id, "revision_id": revision_id, "revision_no": revision_no,
               "preview_url": f"/api/visuals/graphic-pages/{page_id}/preview", "note": "已追加页面新版本。"})


@router.get("/graphic-pages/{page_id}/preview")
def graphic_page_preview(page_id: str):
    row = query_one("SELECT r.image_path FROM graphic_pages p JOIN graphic_page_revisions r ON r.id=p.selected_revision_id WHERE p.id=? AND p.user_id=? AND p.deleted_at IS NULL", (page_id, current_user_id()))
    path = Path(row.get("image_path") or "") if row else None
    if not path or not path.is_file(): raise not_found("图文页面预览", page_id)
    return FileResponse(path, media_type="image/png", filename=f"{page_id}.png")


@router.post("/graphic-documents/{document_id}/finalize")
def finalize_graphic_document(document_id: str):
    uid = current_user_id(); document = query_one("SELECT * FROM graphic_documents WHERE id=? AND user_id=? AND deleted_at IS NULL", (document_id, uid))
    if not document: raise not_found("图文文档", document_id)
    pages = query("SELECT * FROM graphic_pages WHERE document_id=? AND user_id=? AND deleted_at IS NULL ORDER BY page_index", (document_id, uid))
    if len(pages) < 3 or any(not page.get("selected_revision_id") for page in pages):
        raise state_invalid("至少需要 3 个已生成页面才能定稿。")
    manifest = {"document_id": document_id, "title": document["title"], "ratio": document["ratio"],
                "pages": [{"id": p["id"], "page_index": p["page_index"], "revision_id": p["selected_revision_id"]} for p in pages]}
    export_id = new_id("VEX")
    execute("INSERT INTO visual_exports(id,user_id,document_id,manifest_json,status,created_at,deleted_at) VALUES(?,?,?,?, 'READY',?,NULL)",
            (export_id, uid, document_id, jdumps(manifest), now()))
    execute("UPDATE graphic_documents SET status='FINALIZED',selected_export_id=?,updated_at=? WHERE id=? AND user_id=?",
            (export_id, now(), document_id, uid))
    deliverable_id = _upsert_deliverable(uid, document["creation_project_id"], document["draft_version_id"],
                                         "graphic_note", document_id, {"export_id": export_id, "page_count": len(pages), "ratio": document["ratio"]})
    return ok({"id": document_id, "export_id": export_id, "deliverable_id": deliverable_id,
               "note": "图文已定稿，并已同步到创作项目和内容计划。"})


@router.delete("/graphic-documents/{document_id}")
def delete_graphic_document(document_id: str, force: bool = False):
    uid = current_user_id()
    if not query_one("SELECT id FROM graphic_documents WHERE id=? AND user_id=? AND deleted_at IS NULL", (document_id, uid)):
        raise not_found("图文文档", document_id)
    adopted = bool(query_one("SELECT id FROM visual_deliverables WHERE user_id=? AND kind='graphic_note' AND entity_id=? AND selected=1",
                             (uid, document_id)))
    if adopted and not force:
        raise state_invalid("该图文文档已经定稿并同步到创作项目或内容计划，不能直接归档。请先更换视觉交付物。")
    stamp = now()
    with transaction():
        if adopted:
            _detach_deliverable(uid, "graphic_note", document_id)
        execute("UPDATE graphic_documents SET deleted_at=?,updated_at=? WHERE id=? AND user_id=?", (stamp, stamp, document_id, uid))
        execute("UPDATE graphic_pages SET deleted_at=?,updated_at=? WHERE document_id=? AND user_id=? AND deleted_at IS NULL", (stamp, stamp, document_id, uid))
    return ok({"id": document_id, "detached": adopted,
               "note": "图文文档已从当前工作区删除；本地备份仍保留恢复能力。"})
