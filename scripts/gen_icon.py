# -*- coding: utf-8 -*-
"""生成 llama.cpp-fnos 应用图标（fpk/app/ui/images/icon_256.png / icon_64.png）。

设计：squircle 圆角矩形 + 对角渐变深青背景，居中现代扁平 llama 头，
右下角绿色三点连线点缀（呼应向量检索 / API 连接）。
4x 超采样绘制后 LANCZOS 缩小，保证边缘平滑。

用法：python scripts/gen_icon.py
依赖：Pillow（pip install pillow）
"""
import os

from PIL import Image, ImageDraw, ImageOps

S = 1024  # 超采样画布

# 配色（与 fpk/app/ui/web/index.html 页头 SVG logo 保持一致）
BG_FROM = (26, 58, 84)      # 左上 深蓝
BG_TO = (18, 84, 76)        # 右下 深青绿
CREAM = (247, 241, 227, 255)
INNER_EAR = (203, 173, 132, 255)
FEATURE = (51, 41, 31, 255)
BLUSH = (242, 178, 140, 110)
GREEN = (62, 207, 142, 255)
GREEN_DIM = (34, 160, 105, 255)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT_DIR = os.path.join(ROOT, "fpk", "app", "ui", "images")


def diag_gradient(size, c1, c2):
    """左上 c1 -> 右下 c2 的对角双色渐变。"""
    base = Image.linear_gradient("L")            # 256x256，上 0 下 255
    diag = base.rotate(45, expand=True, resample=Image.BICUBIC)
    l = (diag.width - 256) // 2
    t = (diag.height - 256) // 2
    diag = diag.crop((l, t, l + 256, t + 256))
    return ImageOps.colorize(diag.resize(size), c1, c2).convert("RGBA")


def layer(size):
    img = Image.new("RGBA", size, (0, 0, 0, 0))
    return img, ImageDraw.Draw(img)


def rotated_ellipse(box, angle, fill):
    """画一个旋转椭圆，返回图层（供 paste_center 按中心粘贴）。"""
    img = Image.new("RGBA", (box[2] - box[0], box[3] - box[1]), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.ellipse((0, 0, img.width - 1, img.height - 1), fill=fill)
    img = img.rotate(angle, resample=Image.BICUBIC, expand=True)
    return img


def paste_center(canvas, img, cx, cy):
    canvas.alpha_composite(img, (int(cx - img.width / 2), int(cy - img.height / 2)))


def draw_icon():
    s = S
    img = diag_gradient((s, s), BG_FROM, BG_TO)
    d = ImageDraw.Draw(img)

    # ---- llama 头部阴影（浮起感）----
    sh_img, sh = layer((s, s))
    sh.ellipse((int(0.245 * s), int(0.30 * s), int(0.755 * s), int(0.875 * s)),
               fill=(0, 0, 0, 45))
    img.alpha_composite(sh_img)

    # ---- 耳朵（先画，被脸盖住下半部）----
    for sign, ang in ((-1, 10), (1, -10)):
        ear = rotated_ellipse(
            (0, 0, int(0.130 * s), int(0.230 * s)), ang, CREAM)
        paste_center(img, ear, 0.5 * s + sign * 0.143 * s, 0.315 * s)
        ear_in = rotated_ellipse(
            (0, 0, int(0.060 * s), int(0.145 * s)), ang, INNER_EAR)
        paste_center(img, ear_in, 0.5 * s + sign * 0.143 * s, 0.325 * s)

    # ---- 脸 ----
    d.ellipse((int(0.245 * s), int(0.275 * s), int(0.755 * s), int(0.855 * s)),
              fill=CREAM)
    # 脸部左上柔光（三层递减，避免硬分界）
    for dy, alpha in ((0.00, 30), (0.05, 18), (0.11, 9)):
        hl_img, hl = layer((s, s))
        hl.ellipse((int(0.31 * s), int((0.305 + dy) * s),
                    int(0.69 * s), int((0.585 + dy) * s)),
                   fill=(255, 255, 255, alpha))
        img.alpha_composite(hl_img)
    d = ImageDraw.Draw(img)

    # ---- 五官 ----
    for sign in (-1, 1):  # 眼睛
        r = int(0.027 * s)
        ex, ey = 0.5 * s + sign * 0.098 * s, 0.505 * s
        d.ellipse((ex - r, ey - r, ex + r, ey + r), fill=FEATURE)
    for sign in (-1, 1):  # 腮红
        bx, by = 0.5 * s + sign * 0.168 * s, 0.588 * s
        rx, ry = 0.052 * s, 0.034 * s
        d.ellipse((bx - rx, by - ry, bx + rx, by + ry), fill=BLUSH)
    # 微笑（下半弧，PIL 角度顺时针、0 度在 3 点钟方向）
    d.arc((int(0.415 * s), int(0.535 * s), int(0.585 * s), int(0.655 * s)),
          start=25, end=155, fill=FEATURE, width=int(0.016 * s))

    # ---- 右下角绿色向量点缀：三点连线，由小到大 ----
    p_small = (0.725 * s, 0.888 * s)
    p_mid = (0.792 * s, 0.855 * s)
    p_big = (0.865 * s, 0.818 * s)
    d.line([p_small, p_big], fill=GREEN_DIM, width=int(0.011 * s))
    for p, r in ((p_small, 0.027), (p_mid, 0.037), (p_big, 0.050)):
        rr = r * s
        d.ellipse((p[0] - rr, p[1] - rr, p[0] + rr, p[1] + rr), fill=GREEN)

    # ---- 顶部整体高光 ----
    top_img, top = layer((s, s))
    top.ellipse((int(-0.2 * s), int(-0.35 * s), int(1.2 * s), int(0.22 * s)),
                fill=(255, 255, 255, 14))
    img.alpha_composite(top_img)

    # ---- squircle 圆角裁剪 ----
    mask = Image.new("L", (s, s), 0)
    ImageDraw.Draw(mask).rounded_rectangle(
        (0, 0, s - 1, s - 1), radius=int(0.225 * s), fill=255)
    out = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    out.paste(img, (0, 0), mask)
    return out


def main():
    icon = draw_icon()
    os.makedirs(OUT_DIR, exist_ok=True)
    p256 = os.path.join(OUT_DIR, "icon_256.png")
    p64 = os.path.join(OUT_DIR, "icon_64.png")
    icon.resize((256, 256), Image.LANCZOS).save(p256)
    icon.resize((256, 256), Image.LANCZOS).resize((64, 64), Image.LANCZOS).save(p64)
    print("生成完成 ->", p256)
    print("           ", p64)


if __name__ == "__main__":
    main()
