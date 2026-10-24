from pathlib import Path
import hashlib

from PIL import Image, ImageDraw, ImageFont, ImageFilter


ROOT = Path(r"D:\见舟新媒体工作台项目")
BASE_PATH = Path(
    r"C:\Users\10336\.codex\generated_images\019fd753-fabe-79b1-b21d-83958c701b97"
    r"\exec-7e3a4783-e600-4cb5-88ea-0dfe3f9f3617.png"
)
QR_SOURCE_PATH = Path(
    r"D:\Temp\UserTemp\codex-clipboard-f2ab40a1-6b25-42a0-a5f9-40f67f3dee83.jpg"
)
OUTPUT_DIR = ROOT / "output" / "imagegen"
POSTER_PATH = OUTPUT_DIR / "tang-chengjiao-ai-enterprise-poster.png"
QR_CROP_PATH = OUTPUT_DIR / "wechat-qr-original-crop.png"

FONT_BOLD = r"C:\Windows\Fonts\simhei.ttf"
FONT_BODY = r"C:\Windows\Fonts\Deng.ttf"
FONT_BODY_BOLD = r"C:\Windows\Fonts\Dengb.ttf"


def font(path: str, size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(path, size=size)


def rounded_alpha_rect(
    layer: Image.Image,
    box: tuple[int, int, int, int],
    radius: int,
    fill: tuple[int, int, int, int],
    outline: tuple[int, int, int, int] | None = None,
    width: int = 1,
) -> None:
    d = ImageDraw.Draw(layer)
    d.rounded_rectangle(box, radius=radius, fill=fill, outline=outline, width=width)


def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    base = Image.open(BASE_PATH).convert("RGBA")
    w, h = base.size

    # Darken the left copy area while retaining the generated blue texture.
    shade = Image.new("RGBA", base.size, (0, 0, 0, 0))
    px = shade.load()
    for x in range(min(650, w)):
        t = x / 650
        alpha = int(126 * (1 - t) ** 1.7)
        for y in range(h):
            px[x, y] = (0, 7, 20, alpha)
    base = Image.alpha_composite(base, shade)

    # Subtle glow behind the headline.
    glow = Image.new("RGBA", base.size, (0, 0, 0, 0))
    gd = ImageDraw.Draw(glow)
    gd.ellipse((-140, 110, 570, 760), fill=(0, 92, 255, 48))
    glow = glow.filter(ImageFilter.GaussianBlur(90))
    base = Image.alpha_composite(base, glow)

    ui = Image.new("RGBA", base.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(ui)
    white = (246, 250, 255, 255)
    muted = (179, 198, 224, 255)
    blue = (49, 151, 255, 255)
    cyan = (62, 220, 255, 255)

    # Top identity rail.
    d.rounded_rectangle((50, 48, 331, 92), radius=22, fill=(9, 66, 132, 195), outline=(56, 165, 255, 210), width=2)
    d.ellipse((66, 63, 78, 75), fill=cyan)
    d.text((91, 58), "企业 AI 落地顾问", font=font(FONT_BODY_BOLD, 25), fill=white)

    d.text((50, 121), "唐成蛟", font=font(FONT_BOLD, 48), fill=white)
    d.text((50, 177), "TANG CHENGJIAO  ·  AI FOR BUSINESS", font=font(FONT_BODY_BOLD, 18), fill=(91, 172, 255, 255))

    # Main headline.
    d.rectangle((50, 237, 58, 372), fill=(39, 148, 255, 255))
    d.text((82, 222), "让 AI 真正", font=font(FONT_BOLD, 55), fill=white, stroke_width=1, stroke_fill=(6, 27, 61, 255))
    d.text((82, 292), "嵌入企业增长", font=font(FONT_BOLD, 67), fill=(64, 171, 255, 255), stroke_width=1, stroke_fill=(4, 37, 87, 255))

    d.text((50, 402), "不止了解 AI，更要把 AI 用进真实业务", font=font(FONT_BODY_BOLD, 29), fill=white)
    d.text((50, 446), "结合企业实际场景，找到真正适合自己的解决方案", font=font(FONT_BODY, 24), fill=muted)

    # Delivery process card.
    rounded_alpha_rect(ui, (50, 502, 536, 575), 18, (4, 24, 55, 205), (39, 139, 255, 180), 2)
    d.text((70, 517), "业务诊断", font=font(FONT_BODY_BOLD, 26), fill=white)
    d.text((184, 517), "→", font=font(FONT_BODY_BOLD, 27), fill=blue)
    d.text((226, 517), "方案输出", font=font(FONT_BODY_BOLD, 26), fill=white)
    d.text((340, 517), "→", font=font(FONT_BODY_BOLD, 27), fill=blue)
    d.text((382, 517), "落地执行", font=font(FONT_BODY_BOLD, 26), fill=white)

    # Capability tags.
    tag_y = 601
    tags = [("内容营销", 50, 176), ("销售增长", 193, 319), ("业务提效", 336, 462)]
    for label, x1, x2 in tags:
        d.rounded_rectangle((x1, tag_y, x2, tag_y + 50), radius=14, fill=(9, 49, 94, 185), outline=(26, 118, 226, 180), width=1)
        d.text((x1 + 17, tag_y + 10), label, font=font(FONT_BODY_BOLD, 24), fill=(221, 239, 255, 255))

    d.text((50, 682), "服务行业", font=font(FONT_BODY_BOLD, 22), fill=blue)
    d.text((50, 719), "地产 · 教育 · 电商 · 家居", font=font(FONT_BOLD, 30), fill=white)
    d.text((50, 764), "兼具 AI 领域 IP 内容生产与线上获客实战经验", font=font(FONT_BODY, 23), fill=muted)

    # Invitation quote.
    d.line((50, 827, 514, 827), fill=(47, 133, 223, 165), width=2)
    d.text((50, 852), "期待与您交流真实痛点", font=font(FONT_BODY_BOLD, 28), fill=white)
    d.text((50, 892), "让 AI 真正进入企业，而不是停留在概念里。", font=font(FONT_BODY, 23), fill=(165, 197, 232, 255))

    # QR card. The QR itself is pasted later with no scaling or pixel alteration.
    rounded_alpha_rect(ui, (40, 944, 516, 1410), 26, (2, 18, 42, 226), (53, 153, 255, 205), 2)
    d.text((67, 958), "微信扫码  ·  深度交流与合作", font=font(FONT_BODY_BOLD, 24), fill=white)

    base = Image.alpha_composite(base, ui)

    # Exact crop from the supplied WeChat image. No resize, enhancement, recolor,
    # filtering, or model processing is applied to the QR pixels.
    qr_source = Image.open(QR_SOURCE_PATH).convert("RGB")
    qr_box = (68, 232, 484, 648)
    qr_crop = qr_source.crop(qr_box)
    qr_crop.save(QR_CROP_PATH, format="PNG", compress_level=0)
    base.paste(qr_crop.convert("RGBA"), (68, 993))

    final = base.convert("RGB")
    final.save(POSTER_PATH, format="PNG", optimize=True)

    # Pixel-integrity assertion: the pasted area must match the exact source crop.
    final_check = Image.open(POSTER_PATH).convert("RGB").crop((68, 993, 484, 1409))
    assert list(final_check.getdata()) == list(qr_crop.getdata())

    digest = hashlib.sha256(qr_crop.tobytes()).hexdigest()
    print(f"poster={POSTER_PATH}")
    print(f"qr_crop={QR_CROP_PATH}")
    print(f"qr_size={qr_crop.size}")
    print(f"qr_pixel_sha256={digest}")
    print("qr_pixels_identical=True")


if __name__ == "__main__":
    main()
