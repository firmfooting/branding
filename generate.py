#!/usr/bin/env python3
"""Generate brand assets for firmfooting.

Mark: a footing — a trapezoid that is wider at the base, sitting on a ground
line. It reads as "the stable base you pour first", the same promise the tools
make: firm ground under you. Teal tile, white glyph.

Outputs (all deterministic, transparent where noted):
  assets/icon.png            256x256  square app/repo icon (teal tile + mark)
  assets/icon@2x.png         512x512
  assets/avatar.png          512x512  org avatar (same mark, avatar size)
  assets/avatar@2x.png      1024x1024
  assets/logo.png            wordmark, dark text  (light backgrounds)
  assets/logo@2x.png
  assets/dark_logo.png       wordmark, light text (dark backgrounds)
  assets/dark_logo@2x.png
  assets/logo_universal.png  wordmark, teal text  (any theme)
  assets/logo_universal@2x.png
  repos/dbml-sharepoint.png           canonical mark
  repos/dbml-sharepoint-probes.png    mark + probe beacon
  repos/dbml-sharepoint-family.png    mark + cover bar (private)

Usage:  python generate.py   (needs Pillow: pip install pillow)
"""
from __future__ import annotations

import json
import os

from PIL import Image, ImageDraw, ImageFont

HERE = os.path.dirname(os.path.abspath(__file__))
ASSETS = os.path.join(HERE, "assets")
REPOS = os.path.join(HERE, "repos")

# --- Brand palette -----------------------------------------------------------
TEAL = (42, 107, 100)           # #2A6B64  primary tile
TEAL_LIGHT = (58, 132, 124)     # hover / accent
GLYPH = (255, 255, 255, 255)    # white mark
TEXT_DARK = (31, 41, 55)        # #1F2937  for light backgrounds
TEXT_LIGHT = (249, 250, 251)    # #F9FAFB  for dark backgrounds
TEXT_TEAL = (42, 107, 100)      # universal

FONT_PATH = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
WORDMARK = "firmfooting"

SS = 4  # supersample factor for anti-aliasing


def _tile(size: int, radius_frac: float = 0.22) -> Image.Image:
    """Rounded-square teal tile, opaque."""
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([0, 0, size - 1, size - 1], radius=round(size * radius_frac),
                        fill=TEAL)
    return img


def _mark(d: ImageDraw.ImageDraw, size: int, *, beacon: bool = False, cover: bool = False):
    """Draw the footing mark (trapezoid + ground line) centred in a size square.

    beacon: add a probe dot above the trapezoid (dbml-sharepoint-probes).
    cover:  add a short cover bar above the trapezoid (dbml-sharepoint-family).
    """
    tx = (0.375 * size, 0.34 * size, 0.625 * size, 0.34 * size)
    bx = (0.235 * size, 0.55 * size, 0.765 * size, 0.55 * size)
    d.polygon([(tx[0], tx[1]), (tx[2], tx[3]), (bx[2], bx[3]), (bx[0], bx[1])],
              fill=GLYPH)
    gy = 0.585 * size
    d.rounded_rectangle([0.235 * size, gy, 0.765 * size, gy + 0.05 * size],
                        radius=round(0.025 * size), fill=GLYPH)
    if beacon:
        r = 0.05 * size
        d.ellipse([0.5 * size - r, 0.20 * size - r, 0.5 * size + r, 0.20 * size + r],
                  fill=GLYPH)
    if cover:
        cw = 0.09 * size
        d.rounded_rectangle([0.5 * size - cw, 0.24 * size, 0.5 * size + cw, 0.24 * size + 0.035 * size],
                            radius=round(0.02 * size), fill=GLYPH)


def _square(size: int, *, beacon: bool = False, cover: bool = False) -> Image.Image:
    """Supersampled teal tile + white mark."""
    big = size * SS
    img = _tile(big)
    _mark(ImageDraw.Draw(img), big, beacon=beacon, cover=cover)
    return img.resize((size, size), Image.LANCZOS)


def _wordmark(text_colour: tuple, width: int) -> Image.Image:
    """Horizontal lockup: small mark + 'firmfooting' text."""
    big_w = width * SS
    tile_h = int(big_w * 0.22)  # mark tile height
    font = ImageFont.truetype(FONT_PATH, int(tile_h * 0.80))
    ascent, _descent = font.getmetrics()
    text_bbox = font.getbbox(WORDMARK)
    text_w = text_bbox[2] - text_bbox[0]
    gap = int(tile_h * 0.35)
    total_w = tile_h + gap + text_w
    body_h = ascent  # centre on the ascender body; let the descender hang below
    total_h = max(tile_h, body_h)
    img = Image.new("RGBA", (total_w, total_h), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    # mark tile (small)
    tile = Image.new("RGBA", (tile_h, tile_h), (0, 0, 0, 0))
    td = ImageDraw.Draw(tile)
    td.rounded_rectangle([0, 0, tile_h - 1, tile_h - 1], radius=round(tile_h * 0.22), fill=TEAL)
    _mark(td, tile_h)
    img.alpha_composite(tile, (0, (total_h - tile_h) // 2))
    # text, ascender top aligned to the centred body
    text_y = (total_h - body_h) // 2 - text_bbox[1]
    d.text((tile_h + gap, text_y), WORDMARK, font=font, fill=text_colour)
    return img.resize((total_w // SS, total_h // SS), Image.LANCZOS)


def _save(img: Image.Image, path: str):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    img.save(path)


def _write_tokens():
    tokens = {
        "primary": "#2A6B64",
        "primary_light": "#3A847C",
        "glyph": "#FFFFFF",
        "text_dark": "#1F2937",
        "text_light": "#F9FAFB",
        "font": "DejaVuSans-Bold",
        "mark": "footing: trapezoid, wide base, on a ground line",
    }
    with open(os.path.join(HERE, "tokens.json"), "w") as f:
        json.dump(tokens, f, indent=2)
        f.write("\n")


def main():
    for size, tag in ((256, "icon"), (512, "icon@2x")):
        _save(_square(size), os.path.join(ASSETS, f"{tag}.png"))
    for size, tag in ((512, "avatar"), (1024, "avatar@2x")):
        _save(_square(size), os.path.join(ASSETS, f"{tag}.png"))
    for colour, name in ((TEXT_DARK, "logo"), (TEXT_LIGHT, "dark_logo"),
                         (TEXT_TEAL, "logo_universal")):
        _save(_wordmark(colour, 320), os.path.join(ASSETS, f"{name}.png"))
        _save(_wordmark(colour, 640), os.path.join(ASSETS, f"{name}@2x.png"))
    _save(_square(256), os.path.join(REPOS, "dbml-sharepoint.png"))
    _save(_square(256, beacon=True), os.path.join(REPOS, "dbml-sharepoint-probes.png"))
    _save(_square(256, cover=True), os.path.join(REPOS, "dbml-sharepoint-family.png"))
    _write_tokens()
    print("generated brand assets under", HERE)


if __name__ == "__main__":
    main()
