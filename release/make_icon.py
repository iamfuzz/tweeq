#!/usr/bin/env python3
"""Draw the Tweeq app icon (an original monogram: a bold "T" on a dark rounded square) and write
app/ui/icon.png (256x256, used by Godot) and app/ui/icon.ico (16-256 px, used for Tweeq.exe, the installer and shortcuts).

    python3 release/make_icon.py
"""
import os

from PIL import Image, ImageDraw

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "..", "app", "ui")
BG_TOP, BG_BOTTOM = (38, 52, 84), (14, 20, 38)
ACCENT = (255, 196, 66)
SIZES = (16, 24, 32, 48, 64, 128, 256)


def draw(size: int) -> Image.Image:
    """Render at 4x and scale down so edges are smooth at every size."""
    s = size * 4
    img = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    grad = Image.new("RGBA", (s, s))
    gd = ImageDraw.Draw(grad)
    for y in range(s):
        t = y / (s - 1)
        gd.line([(0, y), (s, y)], fill=tuple(int(a + (b - a) * t) for a, b in zip(BG_TOP, BG_BOTTOM)) + (255,))
    mask = Image.new("L", (s, s), 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, s - 1, s - 1], radius=int(s * 0.22), fill=255)
    img.paste(grad, (0, 0), mask)
    d = ImageDraw.Draw(img)
    m = int(s * 0.20)                       # margin
    bar_h, stem_w = int(s * 0.15), int(s * 0.19)
    top = m + int(s * 0.04)
    d.rounded_rectangle([m, top, s - m, top + bar_h], radius=int(s * 0.04), fill=ACCENT)           # the bar of the T
    cx = s // 2
    d.rounded_rectangle([cx - stem_w // 2, top, cx + stem_w // 2, s - m], radius=int(s * 0.04), fill=ACCENT)   # the stem
    return img.resize((size, size), Image.LANCZOS)


def main():
    os.makedirs(OUT, exist_ok=True)
    draw(256).save(os.path.join(OUT, "icon.png"))
    big = draw(256)
    big.save(os.path.join(OUT, "icon.ico"), format="ICO", sizes=[(n, n) for n in SIZES])
    print("wrote", os.path.join(OUT, "icon.png"), "and icon.ico", SIZES)


if __name__ == "__main__":
    main()
