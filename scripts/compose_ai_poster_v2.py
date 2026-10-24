from pathlib import Path

from PIL import Image, ImageDraw, ImageFont, ImageFilter


ROOT = Path(r"D:\见舟新媒体工作台项目")
BACKGROUND_PATH = Path(
    r"C:\Users\10336\.codex\generated_images\019fd753-fabe-79b1-b21d-83958c701b97"
    r"\exec-91001c82-76f6-42d0-b14a-1c9b8554e4b6.png"
)
PORTRAIT_PATH = Path(
    r"D:\Temp\UserTemp\codex-clipboard-3283bb66-e552-4d4f-9c13-3a4f693da04d.jpg"
)
QR_SOURCE_PATH = Path(
    r"D:\Temp\UserTemp\codex-clipboard-f2ab40a1-6b25-42a0-a5f9-40f67f3dee83.jpg"
)
OUTPUT_DIR = ROOT / "output" / "imagegen"
POSTER_PATH = OUTPUT_DIR / "tang-chengjiao-ai-enterprise-poster-v2.png"
QR_CROP_PATH = OUTPUT_DIR / "wechat-qr-original-crop-v2.png"

FONT_BOLD = r"C:\Windows\Fonts\simhei.ttf"
FONT_BODY = r"C:\Windows\Fonts\Deng.ttf"
FONT_BODY_BOLD = r"C:\Windows\Fonts\Dengb.ttf"

CANVAS = (1242, 1656)


def f(path: str, size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(path, size=size)


def rounded_mask(size: tuple[int, int], radius: int) -> Image.Image:
    mask = Image.new("L", size, 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, size[0] - 1, size[1] - 1), radius=radius, fill=255)
    return mask


def draw_card(
    layer: Image.Image,
    box: tuple[int, int, int, int],
    radius: int = 22,
    fill: tuple[int, int, int, int] = (5, 23, 52, 205),
    outline: tuple[int, int, int, int] = (38, 131, 232, 165),
    width: int = 2,
) -> None:
    ImageDraw.Draw(layer).rounded_rectangle(box, radius=radius, fill=fill, outline=outline, width=width)


def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    bg = Image.open(BACKGROUND_PATH).convert("RGBA").resize(CANVAS, Image.Resampling.LANCZOS)

    # Add a subtle readable veil without hiding the generated texture.
    veil = Image.new("RGBA", CANVAS, (0, 0, 0, 0))
    vd = ImageDraw.Draw(veil)
    vd.rectangle((0, 0, 750, 1656), fill=(0, 7, 22, 58))
    vd.ellipse((-320, 90, 820, 950), fill=(0, 73, 173, 35))
    veil = veil.filter(ImageFilter.GaussianBlur(75))
    bg = Image.alpha_composite(bg, veil)

    ui = Image.new("RGBA", CANVAS, (0, 0, 0, 0))
    d = ImageDraw.Draw(ui)
    white = (247, 251, 255, 255)
    muted = (177, 200, 226, 255)
    blue = (56, 161, 255, 255)
    cyan = (60, 220, 255, 255)

    # Brand/role rail.
    d.rounded_rectangle((58, 54, 350, 102), radius=24, fill=(7, 64, 127, 205), outline=(60, 172, 255, 220), width=2)
    d.ellipse((76, 71, 88, 83), fill=cyan)
    d.text((103, 64), "企业 AI 落地顾问", font=f(FONT_BODY_BOLD, 27), fill=white)

    # Headline and positioning.
    d.text((58, 150), "让 AI 真正", font=f(FONT_BOLD, 62), fill=white)
    d.text((58, 230), "嵌入企业增长", font=f(FONT_BOLD, 78), fill=blue)
    d.rectangle((58, 326, 535, 331), fill=(53, 159, 255, 220))
    d.text((58, 358), "唐成蛟", font=f(FONT_BOLD, 42), fill=white)
    d.text((216, 370), "AI FOR BUSINESS", font=f(FONT_BODY_BOLD, 20), fill=(99, 182, 255, 255))

    # Short personal introduction with more information than v1.
    d.text((58, 432), "聚焦传统企业 AI 落地", font=f(FONT_BODY_BOLD, 31), fill=white)
    intro = [
        "从真实业务出发，不停留在了解和学习；",
        "围绕内容营销、销售增长等场景，",
        "找到真正适合企业自己的 AI 解决方案。",
    ]
    for i, line in enumerate(intro):
        d.text((58, 482 + i * 43), line, font=f(FONT_BODY, 27), fill=muted)

    # Process cards.
    d.text((58, 640), "从需求到落地的完整服务", font=f(FONT_BODY_BOLD, 29), fill=white)
    cards = [
        ("01", "业务需求诊断", "梳理流程、目标与真实痛点"),
        ("02", "企业方案输出", "匹配业务场景与适用 AI 能力"),
        ("03", "落地执行陪跑", "从工具搭建到团队实际应用"),
    ]
    start_y = 692
    for i, (num, title, desc) in enumerate(cards):
        y = start_y + i * 116
        draw_card(ui, (58, y, 730, y + 94), radius=18, fill=(5, 29, 64, 196), outline=(35, 119, 218, 150), width=1)
        d.rounded_rectangle((76, y + 20, 127, y + 71), radius=13, fill=(9, 83, 166, 230))
        d.text((85, y + 29), num, font=f(FONT_BODY_BOLD, 21), fill=(126, 211, 255, 255))
        d.text((151, y + 13), title, font=f(FONT_BODY_BOLD, 29), fill=white)
        d.text((151, y + 51), desc, font=f(FONT_BODY, 23), fill=muted)

    # Experience block.
    draw_card(ui, (58, 1062, 730, 1267), radius=22, fill=(3, 22, 50, 210), outline=(40, 133, 230, 165), width=2)
    d.text((82, 1086), "服务经验", font=f(FONT_BODY_BOLD, 25), fill=blue)
    d.text((82, 1127), "地产  ·  教育  ·  电商  ·  家居", font=f(FONT_BOLD, 31), fill=white)
    d.line((82, 1177, 700, 1177), fill=(40, 114, 198, 140), width=2)
    d.text((82, 1196), "复合优势", font=f(FONT_BODY_BOLD, 24), fill=blue)
    d.text((205, 1197), "AI 领域 IP  ·  内容生产  ·  线上获客", font=f(FONT_BODY_BOLD, 24), fill=(220, 237, 255, 255))

    # Closing invitation.
    d.text((58, 1320), "期待与您交流真实业务痛点", font=f(FONT_BOLD, 35), fill=white)
    d.text((58, 1370), "让 AI 真正进入企业，而不是停留在概念里。", font=f(FONT_BODY, 26), fill=muted)
    d.rounded_rectangle((58, 1432, 550, 1490), radius=16, fill=(10, 91, 181, 215))
    d.text((84, 1445), "诊断问题  ·  共创方案  ·  推动落地", font=f(FONT_BODY_BOLD, 26), fill=white)
    d.text((58, 1530), "深度交流与合作  /  TANG CHENGJIAO", font=f(FONT_BODY_BOLD, 20), fill=(92, 173, 245, 255))

    # Portrait card shell. The actual portrait is the user's source image, not AI-generated.
    draw_card(ui, (778, 116, 1186, 619), radius=28, fill=(2, 13, 35, 228), outline=(61, 168, 255, 210), width=3)

    # Small QR shell, intentionally secondary in the layout.
    draw_card(ui, (758, 1098, 1212, 1632), radius=26, fill=(2, 18, 42, 230), outline=(51, 151, 247, 205), width=2)
    d.text((784, 1120), "微信扫码 · 进一步交流", font=f(FONT_BODY_BOLD, 25), fill=white)

    bg = Image.alpha_composite(bg, ui)

    # Use the supplied portrait directly. Only proportional downscaling and rounded clipping
    # are applied; face, hair, glasses, clothing and pose are never regenerated.
    portrait = Image.open(PORTRAIT_PATH).convert("RGB")
    portrait_size = (368, 443)
    portrait_small = portrait.resize(portrait_size, Image.Resampling.LANCZOS).convert("RGBA")
    portrait_small.putalpha(rounded_mask(portrait_size, 20))
    bg.alpha_composite(portrait_small, (798, 136))

    portrait_label = Image.new("RGBA", CANVAS, (0, 0, 0, 0))
    pld = ImageDraw.Draw(portrait_label)
    pld.rounded_rectangle((814, 524, 1150, 566), radius=18, fill=(2, 24, 56, 224))
    pld.text((838, 532), "唐成蛟  ·  企业 AI 落地顾问", font=f(FONT_BODY_BOLD, 20), fill=white)
    bg = Image.alpha_composite(bg, portrait_label)

    # Preserve the original QR pixels. Crop only, then paste 1:1 without resizing,
    # enhancement, recoloring or model processing.
    qr_source = Image.open(QR_SOURCE_PATH).convert("RGB")
    qr_box = (68, 232, 484, 648)
    qr_crop = qr_source.crop(qr_box)
    qr_crop.save(QR_CROP_PATH, format="PNG", compress_level=0)

    qr_plate = Image.new("RGB", (416, 416), "white")
    qr_plate.paste(qr_crop, (0, 0))
    bg.paste(qr_plate.convert("RGBA"), (778, 1189))

    final = bg.convert("RGB")
    final.save(POSTER_PATH, format="PNG", optimize=True)

    pasted = Image.open(POSTER_PATH).convert("RGB").crop((778, 1189, 1194, 1605))
    assert pasted.tobytes() == qr_crop.tobytes(), "QR pixels changed during compositing"

    print(f"poster={POSTER_PATH}")
    print(f"portrait_source={PORTRAIT_PATH}")
    print("portrait_ai_regenerated=False")
    print(f"qr_crop={QR_CROP_PATH}")
    print(f"qr_size={qr_crop.size}")
    print("qr_pixels_identical=True")


if __name__ == "__main__":
    main()
