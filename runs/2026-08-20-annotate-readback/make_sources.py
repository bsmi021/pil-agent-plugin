#!/usr/bin/env python
"""Build the synthetic source images for the W4 read-back bundle.

Kept in the bundle so the read-back is reproducible: the busy image is a real
render already committed to this repository, but the light, dark and edge-case
sources are generated here rather than pulled from anywhere unversioned.

Run from the repository root:  python runs/2026-08-20-annotate-readback/make_sources.py
"""

from pathlib import Path

from PIL import Image, ImageDraw

HERE = Path(__file__).resolve().parent
SOURCES = HERE / "sources"


def light_ui_mock():
    """A light-background layout: black numerals are the correct contrast choice."""
    image = Image.new("RGB", (700, 500), (246, 246, 244))
    draw = ImageDraw.Draw(image)
    draw.rectangle([24, 24, 676, 96], fill=(228, 232, 238))
    draw.rectangle([40, 44, 240, 60], fill=(150, 158, 170))
    draw.rectangle([40, 68, 170, 78], fill=(186, 192, 202))
    draw.rectangle([24, 120, 330, 300], fill=(255, 255, 255), outline=(214, 218, 224), width=2)
    draw.rectangle([48, 150, 306, 166], fill=(120, 170, 210))
    draw.rectangle([48, 182, 260, 198], fill=(120, 170, 210))
    draw.rectangle([48, 214, 290, 230], fill=(200, 210, 220))
    draw.rectangle([360, 120, 676, 300], fill=(252, 248, 236), outline=(230, 220, 190), width=2)
    draw.ellipse([420, 150, 560, 270], fill=(240, 190, 120))
    draw.rectangle([24, 330, 676, 470], fill=(255, 255, 255), outline=(214, 218, 224), width=2)
    for index in range(6):
        x = 60 + index * 100
        draw.rectangle([x, 440 - (index + 1) * 16, x + 54, 450], fill=(110, 180, 150))
    return image


def dark_dashboard():
    """A dark-background layout: white numerals are the correct contrast choice."""
    image = Image.new("RGB", (700, 500), (18, 20, 26))
    draw = ImageDraw.Draw(image)
    draw.rectangle([20, 20, 680, 90], fill=(30, 34, 44))
    draw.rectangle([36, 44, 210, 62], fill=(70, 80, 100))
    draw.rectangle([20, 110, 340, 290], fill=(26, 30, 40), outline=(48, 54, 68), width=2)
    for index in range(7):
        x = 44 + index * 40
        draw.rectangle([x, 270 - (index % 4 + 1) * 34, x + 26, 274], fill=(60, 130, 200))
    draw.rectangle([370, 110, 680, 290], fill=(26, 30, 40), outline=(48, 54, 68), width=2)
    draw.ellipse([440, 140, 610, 265], outline=(190, 90, 70), width=14)
    draw.rectangle([20, 320, 680, 470], fill=(26, 30, 40), outline=(48, 54, 68), width=2)
    draw.line([(40, 440), (180, 380), (320, 410), (460, 350), (660, 370)], fill=(90, 200, 160), width=4)
    return image


def right_edge_case():
    """The F2 reproduction: content pinned to the right frame edge."""
    image = Image.new("RGB", (400, 300), (245, 245, 240))
    ImageDraw.Draw(image).rectangle([380, 80, 399, 200], fill=(200, 40, 40))
    return image


def overlapping_case():
    """The F3 reproduction: two boxes with near-equal tops a few pixels apart."""
    image = Image.new("RGB", (400, 300), (245, 245, 240))
    ImageDraw.Draw(image).rectangle([30, 10, 160, 140], fill=(120, 180, 220))
    return image


def main():
    SOURCES.mkdir(parents=True, exist_ok=True)
    for name, builder in (
        ("light_ui.png", light_ui_mock),
        ("dark_dashboard.png", dark_dashboard),
        ("edge_right.png", right_edge_case),
        ("edge_overlap.png", overlapping_case),
    ):
        builder().save(SOURCES / name, format="PNG")
        print(SOURCES / name)


if __name__ == "__main__":
    main()
