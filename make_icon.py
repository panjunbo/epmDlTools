# -*- coding: utf-8 -*-
"""从源图生成 assets/download_tools.ico（多尺寸）+ 方形 PNG。

用法::

    python make_icon.py                     # 徽章样式（默认，窗口/任务栏最清晰）
    python make_icon.py --plain             # 透明底样式（只裁边 + 补方）
    python make_icon.py path/to/new.png     # 换源图

样式
----
badge（默认）：源图主体放在圆角渐变底上，四周留白适中 —— 图标"饱满"，
    16px 的标题栏也能看出形状（纯透明底的小图在标题栏里容易糊成一团）。
plain：只按 alpha 裁掉四周透明边 + 补成正方形，透明背景。

两种样式都会生成：
    assets/download_tools.ico       16/24/32/48/64/128/256 多尺寸（Windows 按场景挑帧）
    assets/download_tools_icon.png  1024x1024 方形母版

依赖：Pillow。
"""
import os
import sys

from PIL import Image, ImageDraw, ImageFilter

ROOT = os.path.dirname(os.path.abspath(__file__))
ICO_OUT = os.path.join(ROOT, "assets", "download_tools.ico")
PNG_OUT = os.path.join(ROOT, "assets", "download_tools_icon.png")
DEFAULT_SRC = os.path.join(ROOT, "assets", "download_tools.png")

# Windows 各场景用到的尺寸（标题栏 16 / 任务栏 24,32 / 资源管理器 48 / 超大图标 256）
SIZES = [(16, 16), (24, 24), (32, 32), (48, 48), (64, 64),
         (128, 128), (256, 256)]

MASTER = 1024          # 母版边长
RADIUS_RATIO = 0.22   # 圆角半径 / 边长
ART_RATIO = 0.87      # 主体最大边 / 画布边长（留白）
GRAD_TOP = (238, 243, 250)   # 底色渐变（上）
GRAD_BOTTOM = (198, 216, 236)  # 底色渐变（下）
SHADOW_ALPHA = 90      # 投影透明度


def trim(img: Image.Image) -> Image.Image:
    """按 alpha 裁掉四周透明区域。"""
    bbox = img.getchannel("A").point(lambda v: 255 if v > 8 else 0).getbbox()
    return img.crop(bbox) if bbox else img


def fit(img: Image.Image, box: int) -> Image.Image:
    """等比缩放到 box 以内。"""
    w, h = img.size
    scale = box / max(w, h)
    return img.resize((max(1, round(w * scale)), max(1, round(h * scale))),
                      Image.LANCZOS)


def gradient(size: int, top, bottom) -> Image.Image:
    """竖向渐变（先做小条再放大，省内存也够平滑）。"""
    strip = Image.new("RGB", (1, 256))
    px = strip.load()
    for y in range(256):
        t = y / 255
        px[0, y] = tuple(round(top[i] + (bottom[i] - top[i]) * t) for i in range(3))
    return strip.resize((size, size), Image.BICUBIC).convert("RGBA")


def rounded_mask(size: int, radius: int) -> Image.Image:
    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).rounded_rectangle(
        [0, 0, size - 1, size - 1], radius=radius, fill=255)
    return mask


def build_badge(art: Image.Image) -> Image.Image:
    """圆角渐变底 + 投影 + 主体。"""
    canvas = Image.new("RGBA", (MASTER, MASTER), (0, 0, 0, 0))
    bg = gradient(MASTER, GRAD_TOP, GRAD_BOTTOM)
    bg.putalpha(rounded_mask(MASTER, int(MASTER * RADIUS_RATIO)))
    canvas.alpha_composite(bg)

    art = fit(art, int(MASTER * ART_RATIO))
    x = (MASTER - art.width) // 2
    y = (MASTER - art.height) // 2

    # 投影：主体 alpha 模糊 + 下移，纯深色半透明
    shadow = Image.new("RGBA", art.size, (60, 40, 20, 0))
    shadow.putalpha(art.getchannel("A").filter(ImageFilter.GaussianBlur(18))
                    .point(lambda v: v * SHADOW_ALPHA // 255))
    canvas.alpha_composite(shadow, (x, y + int(MASTER * 0.015)))
    canvas.alpha_composite(art, (x, y))
    return canvas


def build_plain(art: Image.Image) -> Image.Image:
    """透明底：裁边后补成正方形（四周 4% 留白）。"""
    art = fit(art, int(MASTER * 0.92))
    side = max(art.width, art.height)
    canvas = Image.new("RGBA", (side, side), (0, 0, 0, 0))
    canvas.alpha_composite(art, ((side - art.width) // 2, (side - art.height) // 2))
    return canvas


def build(src: str, plain: bool = False,
          ico_out: str = ICO_OUT, png_out: str = PNG_OUT):
    img = Image.open(src).convert("RGBA")
    content = trim(img)
    print(f"源图 {os.path.basename(src)} {img.size} → 内容 {content.size}")

    master = build_plain(content) if plain else build_badge(content)
    print(f"母版 {master.size}  样式 {'plain' if plain else 'badge'}")

    os.makedirs(os.path.dirname(ico_out), exist_ok=True)
    master.save(ico_out, format="ICO", sizes=SIZES)
    master.save(png_out, format="PNG")
    print(f"已生成 {ico_out} ({os.path.getsize(ico_out)} 字节)")
    print(f"已生成 {png_out} ({os.path.getsize(png_out)} 字节)")


if __name__ == "__main__":
    args = sys.argv[1:]
    plain = "--plain" in args
    args = [a for a in args if a != "--plain"]
    src = args[0] if args else DEFAULT_SRC
    if not os.path.isfile(src):
        print(f"源图不存在: {src}")
        sys.exit(1)
    build(src, plain=plain)
