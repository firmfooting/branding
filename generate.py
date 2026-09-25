#!/usr/bin/env python3
"""Generate every firmfooting brand asset from one set of numbers.

The mark is a footing: a trapezoid, wider at the base, sitting on a ground line
that runs past it on both sides. It is drawn on a 16-unit grid, so at 16, 32
and 48 pixels every horizontal edge lands on a whole pixel.

Everything is vector first. Each asset is a scene of shapes; the same scene is
written out as SVG and rasterised to PNG, so the two can never disagree. The
wordmark is set in Barlow, vendored under fonts/, and converted to outlines, so
neither output depends on the fonts installed on the machine.

The palette is checked before anything is written: every colour pairing the
brand relies on has a minimum WCAG contrast ratio, and a pairing that falls
short stops the run.

Usage:
  python generate.py            write every asset, tokens.json, web/, README table
  python generate.py --check    render in memory and fail if any file is stale

Needs Pillow and fontTools (see requirements.txt).
"""
from __future__ import annotations

import argparse
import io
import json
import math
import os
import re
import sys
from dataclasses import dataclass, field

from fontTools.pens.basePen import BasePen
from fontTools.pens.recordingPen import DecomposingRecordingPen
from fontTools.pens.svgPathPen import SVGPathPen
from fontTools.pens.transformPen import TransformPen
from fontTools.ttLib import TTFont
from PIL import Image, ImageChops, ImageDraw

HERE = os.path.dirname(os.path.abspath(__file__))
SS = 4  # supersample factor; masks are drawn at SS x size and box-filtered down


# --- Palette ------------------------------------------------------------------
# Teal is the brand. Concrete is the neutral: grey with a slight lean toward the
# teal, named for what a footing is poured from.

TEAL = {
    50: "#EEF6F5", 100: "#D5EAE7", 200: "#A9D3CD", 300: "#74B8AE",
    400: "#4E9C92", 500: "#3A847C", 600: "#2A6B64", 700: "#22564F",
    800: "#1B433E", 900: "#13302C", 950: "#0B1E1B",
}
CONCRETE = {
    0: "#FFFFFF", 50: "#F6F8F7", 100: "#EAEEED", 200: "#D6DDDB", 300: "#B7C1BE",
    400: "#8E9A97", 500: "#6B7774", 600: "#515C59", 700: "#3B4442",
    800: "#262D2C", 900: "#182020", 950: "#0E1414",
}
SCALES = {"teal": TEAL, "concrete": CONCRETE}

# Role -> (scale, step). Use roles in assets and docs; the steps are the ramp.
ROLES = {
    "brand": ("teal", 600),         # the tile; links and buttons on light grounds
    "brand-strong": ("teal", 700),  # hover and pressed on light grounds
    "brand-bright": ("teal", 300),  # links and accents on dark grounds
    "universal": ("teal", 500),     # wordmark text that must hold on any ground
    "glyph": ("concrete", 0),       # the mark, on the tile
    "ink": ("concrete", 900),       # text on light grounds
    "chalk": ("concrete", 50),      # text on dark grounds
    "paper": ("concrete", 50),      # light ground
    "night": ("teal", 950),         # dark ground (social cards)
    "rule": ("concrete", 200),      # hairlines on light grounds
}

# Grounds the assets are actually shown on, as well as the brand's own.
GROUNDS = {"white": "#FFFFFF", "github-dark": "#0D1117"}


def role(name: str) -> str:
    scale, step = ROLES[name]
    return SCALES[scale][step]


def _hex_rgb(h: str) -> tuple[int, int, int]:
    return tuple(int(h[i:i + 2], 16) for i in (1, 3, 5))


def _luminance(h: str) -> float:
    def lin(c: int) -> float:
        c = c / 255
        return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4
    r, g, b = (lin(c) for c in _hex_rgb(h))
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def _mix(a: str, b: str, t: float) -> str:
    """sRGB blend of two colours, t of the way from a to b."""
    return "#" + "".join(f"{round(x + (y - x) * t):02X}" for x, y in zip(_hex_rgb(a), _hex_rgb(b)))


def contrast(a: str, b: str) -> float:
    la, lb = sorted((_luminance(a), _luminance(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


def _colour(ref: str) -> str:
    return GROUNDS[ref] if ref in GROUNDS else role(ref)


# (foreground, background, minimum, what depends on it). 4.5 is WCAG AA for body
# text, 7 is AAA, 3 is the floor for large text and for graphics (WCAG 1.4.11).
CONTRAST_RULES = [
    ("glyph", "brand", 4.5, "the mark on its tile"),
    ("ink", "white", 7.0, "wordmark and body text, light theme"),
    ("ink", "paper", 7.0, "body text on the light ground"),
    ("chalk", "github-dark", 7.0, "wordmark and body text, dark theme"),
    ("chalk", "night", 7.0, "names on social cards"),
    ("brand", "white", 4.5, "links on light grounds"),
    ("brand-bright", "github-dark", 4.5, "links on dark grounds"),
    ("brand-bright", "night", 4.5, "taglines on social cards"),
    ("universal", "white", 3.0, "universal wordmark, light theme"),
    ("universal", "github-dark", 3.0, "universal wordmark, dark theme"),
]


def check_contrast() -> list[tuple[str, str, float, float, str]]:
    rows, failures = [], []
    for fg, bg, minimum, use in CONTRAST_RULES:
        ratio = contrast(_colour(fg), _colour(bg))
        rows.append((fg, bg, ratio, minimum, use))
        if ratio < minimum:
            failures.append(f"{fg} on {bg}: {ratio:.2f}:1, needs {minimum}:1 ({use})")
    if failures:
        sys.exit("palette fails its contrast rules:\n  " + "\n  ".join(failures))
    return rows


# --- Shapes -------------------------------------------------------------------
# A shape knows how to write itself as SVG and how to flatten itself into
# polygons for the rasteriser. Coordinates are scene units; scenes are scaled to
# pixels at render time.

def _n(v: float) -> str:
    """Compact, stable number formatting for SVG."""
    s = f"{v:.3f}".rstrip("0").rstrip(".")
    return "0" if s in ("-0", "") else s


@dataclass(frozen=True)
class Poly:
    points: tuple[tuple[float, float], ...]

    def svg(self) -> str:
        return '<path d="M' + "L".join(f"{_n(x)} {_n(y)}" for x, y in self.points) + 'Z"/>'

    def contours(self) -> list[list[tuple[float, float]]]:
        return [list(self.points)]

    def moved(self, s: float, dx: float, dy: float) -> "Poly":
        return Poly(tuple((x * s + dx, y * s + dy) for x, y in self.points))


@dataclass(frozen=True)
class RRect:
    x: float
    y: float
    w: float
    h: float
    r: float = 0.0

    def svg(self) -> str:
        rx = f' rx="{_n(self.r)}"' if self.r else ""
        return (f'<rect x="{_n(self.x)}" y="{_n(self.y)}" width="{_n(self.w)}" '
                f'height="{_n(self.h)}"{rx}/>')

    def contours(self) -> list[list[tuple[float, float]]]:
        x0, y0, x1, y1, r = self.x, self.y, self.x + self.w, self.y + self.h, self.r
        if not r:
            return [[(x0, y0), (x1, y0), (x1, y1), (x0, y1)]]
        pts, steps = [], 24
        for cx, cy, a0 in ((x1 - r, y0 + r, -90), (x1 - r, y1 - r, 0),
                           (x0 + r, y1 - r, 90), (x0 + r, y0 + r, 180)):
            for i in range(steps + 1):
                a = math.radians(a0 + 90 * i / steps)
                pts.append((cx + r * math.cos(a), cy + r * math.sin(a)))
        return [pts]

    def moved(self, s: float, dx: float, dy: float) -> "RRect":
        return RRect(self.x * s + dx, self.y * s + dy, self.w * s, self.h * s, self.r * s)


@dataclass(frozen=True)
class Circle:
    cx: float
    cy: float
    r: float

    def svg(self) -> str:
        return f'<circle cx="{_n(self.cx)}" cy="{_n(self.cy)}" r="{_n(self.r)}"/>'

    def contours(self) -> list[list[tuple[float, float]]]:
        steps = 96
        return [[(self.cx + self.r * math.cos(2 * math.pi * i / steps),
                  self.cy + self.r * math.sin(2 * math.pi * i / steps)) for i in range(steps)]]

    def moved(self, s: float, dx: float, dy: float) -> "Circle":
        return Circle(self.cx * s + dx, self.cy * s + dy, self.r * s)


class _FlattenPen(BasePen):
    """Collects glyph outlines as polygons, flattening curves."""

    STEPS = 12

    def __init__(self):
        super().__init__(None)
        self.contours: list[list[tuple[float, float]]] = []

    def _moveTo(self, pt):
        self.contours.append([pt])

    def _lineTo(self, pt):
        self.contours[-1].append(pt)

    def _curveToOne(self, p1, p2, p3):
        p0 = self._getCurrentPoint()
        for i in range(1, self.STEPS + 1):
            t = i / self.STEPS
            u = 1 - t
            self.contours[-1].append(tuple(
                u ** 3 * a + 3 * u * u * t * b + 3 * u * t * t * c + t ** 3 * d
                for a, b, c, d in zip(p0, p1, p2, p3)))

    def _qCurveToOne(self, p1, p2):
        p0 = self._getCurrentPoint()
        for i in range(1, self.STEPS + 1):
            t = i / self.STEPS
            u = 1 - t
            self.contours[-1].append(tuple(
                u * u * a + 2 * u * t * b + t * t * c for a, b, c in zip(p0, p1, p2)))


@dataclass(frozen=True)
class Outline:
    """A glyph outline, filled even-odd. `ops` is a fontTools recording in font
    units; `m` is the affine transform (a, b, c, d, e, f) into scene units."""
    ops: tuple
    m: tuple[float, float, float, float, float, float]

    def _replay(self, pen):
        tp = TransformPen(pen, self.m)
        for op, args in self.ops:
            getattr(tp, op)(*args)

    def svg(self) -> str:
        pen = SVGPathPen(None, ntos=_n)
        self._replay(pen)
        return f'<path d="{pen.getCommands()}"/>'

    def contours(self) -> list[list[tuple[float, float]]]:
        pen = _FlattenPen()
        self._replay(pen)
        return pen.contours

    def moved(self, s: float, dx: float, dy: float) -> "Outline":
        a, b, c, d, e, f = self.m
        return Outline(self.ops, (a * s, b * s, c * s, d * s, e * s + dx, f * s + dy))


@dataclass
class Layer:
    colour: str
    shapes: list = field(default_factory=list)
    clip: list | None = None  # raster only: keep the layer inside these shapes


@dataclass
class Scene:
    w: float
    h: float
    layers: list[Layer]
    title: str = ""


def _moved(shapes, s, dx, dy):
    return [sh.moved(s, dx, dy) for sh in shapes]


# --- Type ---------------------------------------------------------------------

class Font:
    def __init__(self, filename: str):
        self.tt = TTFont(os.path.join(HERE, "fonts", filename))
        self.upm = self.tt["head"].unitsPerEm
        self.glyphs = self.tt.getGlyphSet()
        self.cmap = self.tt.getBestCmap()
        self._ops: dict[str, tuple] = {}

    def _record(self, name: str) -> tuple:
        if name not in self._ops:
            pen = DecomposingRecordingPen(self.glyphs)
            self.glyphs[name].draw(pen)
            self._ops[name] = tuple((op, tuple(args)) for op, args in pen.value)
        return self._ops[name]

    def set(self, text: str, size: float, x: float, baseline: float,
            tracking: float = 0.0) -> tuple[list[Outline], float]:
        """Outlines for `text` at `size`, starting at x on `baseline`, and the
        advance. Tracking is in em. No shaping or kerning: every string set
        with this is short, lowercase or plain, and checked by eye."""
        k = size / self.upm
        out, pen_x = [], x
        for ch in text:
            name = self.cmap[ord(ch)]
            ops = self._record(name)
            if ops:
                out.append(Outline(ops, (k, 0, 0, -k, pen_x, baseline)))
            pen_x += self.glyphs[name].width * k + tracking * size
        return out, pen_x - x - tracking * size


SEMIBOLD = Font("Barlow-SemiBold.ttf")
MEDIUM = Font("Barlow-Medium.ttf")
WORDMARK = "firmfooting"


# --- The mark -----------------------------------------------------------------
# All in 16-grid units. Every y is a whole unit, so the flat edges are
# pixel-exact at 16, 32 and 48 px.

GRID = 16
TILE_RADIUS = 3.5
FOOTING = Poly(((6, 5), (10, 5), (13, 9), (3, 9)))   # 4 wide on top, 10 at the base
GROUND = RRect(2, 10, 12, 1, 0.5)                      # runs a unit past the base each side
BEACON = Circle(8, 2.75, 1.1)                          # the probe: dbml-sharepoint-probes
COVER = RRect(6, 3, 4, 1, 0.5)                         # the lid: dbml-sharepoint-family


def mark_shapes(*, beacon: bool = False, cover: bool = False) -> list:
    shapes = [FOOTING, GROUND]
    if beacon:
        shapes.append(BEACON)
    if cover:
        shapes.append(COVER)
    return shapes


def tile_scene(*, radius: float = TILE_RADIUS, **variant) -> Scene:
    return Scene(GRID, GRID, [Layer(role("brand"), [RRect(0, 0, GRID, GRID, radius)]),
                              Layer(role("glyph"), mark_shapes(**variant))], "firmfooting")


def mark_scene(colour: str) -> Scene:
    """The glyph alone, cropped to the ground line's width, for one-colour use."""
    x0, y0 = GROUND.x, FOOTING.points[0][1]
    return Scene(GROUND.w, GROUND.y + GROUND.h - y0,
                 [Layer(colour, _moved(mark_shapes(), 1, -x0, -y0))], "firmfooting")


# Lockup: the text stands on the same line as the footing. Its baseline is the
# bottom of the ground line, and the gap to the tile is five grid units.
LOCKUP_SIZE = 13.5
LOCKUP_GAP = 5
LOCKUP_TRACKING = -0.01


def lockup_scene(text_colour: str) -> Scene:
    text, advance = SEMIBOLD.set(WORDMARK, LOCKUP_SIZE, GRID + LOCKUP_GAP,
                                 GROUND.y + GROUND.h, LOCKUP_TRACKING)
    tile = tile_scene()
    return Scene(GRID + LOCKUP_GAP + advance, GRID,
                 tile.layers + [Layer(text_colour, text)], "firmfooting")



# Product lockups: the tile, the product's name standing on the ground line in
# Barlow SemiBold, and "firmfooting" above it, smaller, in the accent teal. The
# org's own lockup is the wordmark alone; a product endorses it.
PRODUCT_SIZE = 9.0
ENDORSE_SIZE = 3.6
ENDORSE_BASELINE = 3.4


def product_lockup_scene(name: str, text_colour: str, accent: str) -> Scene:
    x = GRID + LOCKUP_GAP * 0.8
    text, advance = SEMIBOLD.set(name, PRODUCT_SIZE, x, GROUND.y + GROUND.h, LOCKUP_TRACKING)
    endorse, endorse_w = MEDIUM.set(WORDMARK, ENDORSE_SIZE, x + PRODUCT_SIZE * 0.03,
                                    ENDORSE_BASELINE, 0.02)
    width = x + max(advance, endorse_w)
    return Scene(width, GRID, tile_scene().layers + [Layer(accent, endorse), Layer(text_colour, text)],
                 f"{name}, by firmfooting")


# --- Social cards -------------------------------------------------------------
# 1280 x 640, GitHub's size for a repository social preview. Drawn in pixels.

REPOS = {
    # slug: (display name, tagline lines)
    ".github": ("firmfooting", ["Safe, plain tooling for", "M365 and SharePoint operators."]),
    "dbml-sharepoint": ("dbml-sharepoint", ["A DBML schema in, fail-closed", "SharePoint lists out."]),
    "formwork": ("formwork", ["SharePoint pages as YAML,", "poured from the browser console."]),
    "vsdxkit": ("vsdxkit", ["Create, edit and analyse Visio", ".vsdx files with Python."]),
    "renovate-config": ("renovate-config", ["One dependency policy,", "extended by every repository."]),
    "branding": ("branding", ["The mark, colour and type", "of firmfooting."]),
}


def _hatch(x0, y0, x1, y1, pitch, weight) -> list:
    """45-degree earth hatching, the section-drawing convention for ground."""
    h = y1 - y0
    lines, x = [], x0 - h
    while x < x1:
        lines.append(Poly(((x, y1), (x + weight, y1), (x + weight + h, y0), (x + h, y0))))
        x += pitch
    return lines


def social_scene(slug: str) -> Scene:
    name, tagline = REPOS[slug]
    W, H = 1280, 640
    left = 88
    layers = [Layer(role("night"), [RRect(0, 0, W, H)])]

    # Drafting grid, faint.
    grid = [RRect(x, 0, 1, H) for x in range(32, W, 32)]
    grid += [RRect(0, y, W, 1) for y in range(32, H, 32)]
    layers.append(Layer(_mix(TEAL[950], TEAL[900], 0.5), grid))

    # Ground: the mark's own geometry at 26 px per unit, run the full width.
    u = 26
    ground_top = 528
    ground = RRect(0, ground_top, W, u)
    layers.append(Layer(TEAL[800], _hatch(0, ground_top + u, W, H, 22, 4),
                        clip=[RRect(0, ground_top + u, W, H - ground_top - u)]))
    footing_x = W - left - 10 * u - 3 * u
    footing = FOOTING.moved(u, footing_x - 3 * u, ground_top - 10 * u)
    layers.append(Layer(role("universal"), [ground]))
    layers.append(Layer(role("chalk"), [footing]))

    # Lockup, top left (the org card is the lockup, so it skips this).
    if slug != ".github":
        s = 48 / GRID
        lock = lockup_scene(role("chalk"))
        for layer in lock.layers:
            layers.append(Layer(layer.colour, _moved(layer.shapes, s, left, 72)))

    # Name, tagline, address.
    size = 116 if len(name) <= 12 else 96
    text, _ = SEMIBOLD.set(name, size, left - size * 0.04, 318, -0.015)
    layers.append(Layer(role("chalk"), text))
    lines = []
    for i, line in enumerate(tagline):
        glyphs, _ = MEDIUM.set(line, 40, left, 388 + i * 50)
        lines += glyphs
    layers.append(Layer(role("brand-bright"), lines))
    url = "github.com/firmfooting" + ("" if slug == ".github" else f"/{slug}")
    glyphs, _ = MEDIUM.set(url, 26, left, 494, 0.01)
    layers.append(Layer(TEAL[400], glyphs))
    return Scene(W, H, layers, name)


# --- Rendering ----------------------------------------------------------------

def render_svg(scene: Scene, width: float | None = None) -> str:
    w = width or scene.w
    h = w * scene.h / scene.w
    out = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {_n(scene.w)} {_n(scene.h)}" '
           f'width="{_n(w)}" height="{_n(h)}" role="img" aria-label="{scene.title}">',
           f"<title>{scene.title}</title>"]
    for layer in scene.layers:
        rule = ' fill-rule="evenodd"' if any(isinstance(s, Outline) for s in layer.shapes) else ""
        out.append(f'<g fill="{layer.colour}"{rule}>' + "".join(s.svg() for s in layer.shapes) + "</g>")
    out.append("</svg>")
    return "\n".join(out) + "\n"


def _mask(shapes, size: tuple[int, int], k: float) -> Image.Image:
    mask = Image.new("1", size, 0)
    draw = ImageDraw.Draw(mask)
    for shape in shapes:
        contours = [[(x * k, y * k) for x, y in c] for c in shape.contours() if len(c) > 2]
        if not isinstance(shape, Outline):
            for c in contours:
                draw.polygon(c, fill=1)
            continue
        # Even-odd: XOR each contour into a patch the size of the glyph.
        xs = [x for c in contours for x, _ in c]
        ys = [y for c in contours for _, y in c]
        if not xs:
            continue
        bx, by = math.floor(min(xs)) - 1, math.floor(min(ys)) - 1
        pw, ph = math.ceil(max(xs)) - bx + 2, math.ceil(max(ys)) - by + 2
        patch = Image.new("1", (pw, ph), 0)
        for c in contours:
            one = Image.new("1", (pw, ph), 0)
            ImageDraw.Draw(one).polygon([(x - bx, y - by) for x, y in c], fill=1)
            patch = ImageChops.logical_xor(patch, one)
        mask.paste(1, (bx, by), patch)
    return mask


def render_png(scene: Scene, width: int, height: int | None = None) -> Image.Image:
    height = height or round(width * scene.h / scene.w)
    big = (width * SS, height * SS)
    k = big[0] / scene.w
    img = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    for layer in scene.layers:
        mask = _mask(layer.shapes, big, k)
        if layer.clip:
            mask = ImageChops.logical_and(mask, _mask(layer.clip, big, k))
        alpha = mask.convert("L").resize((width, height), Image.BOX)
        paint = Image.new("RGBA", (width, height), _hex_rgb(layer.colour) + (255,))
        paint.putalpha(alpha)
        img.alpha_composite(paint)
    return img


def _flatten(img: Image.Image) -> Image.Image:
    """Opaque RGB, for images that must not carry transparency."""
    return img.convert("RGB")


# --- Outputs ------------------------------------------------------------------

def build() -> dict[str, object]:
    """Path -> PIL image, list of (size, image) for an .ico, or text."""
    out: dict[str, object] = {}
    icon = tile_scene()
    # The org avatar is full bleed: GitHub rounds avatars itself, and a tile with
    # its own rounded, transparent corners shows the page through them.
    avatar = tile_scene(radius=0)

    out["assets/icon.svg"] = render_svg(icon, 256)
    out["assets/favicon.svg"] = render_svg(icon, 32)
    out["assets/icon.png"] = render_png(icon, 256)
    out["assets/icon@2x.png"] = render_png(icon, 512)
    out["assets/favicon.ico"] = [(s, render_png(icon, s)) for s in (16, 32, 48)]
    out["assets/apple-touch-icon.png"] = _flatten(render_png(avatar, 180))
    out["assets/avatar.svg"] = render_svg(avatar, 512)
    out["assets/avatar.png"] = _flatten(render_png(avatar, 512))
    out["assets/avatar@2x.png"] = _flatten(render_png(avatar, 1024))

    for colour, name in ((role("brand"), "mark"), (role("glyph"), "mark_white"),
                         (role("ink"), "mark_ink")):
        out[f"assets/{name}.svg"] = render_svg(mark_scene(colour), 192)

    for colour, name in ((role("ink"), "logo"), (role("chalk"), "dark_logo"),
                         (role("universal"), "logo_universal")):
        scene = lockup_scene(colour)
        out[f"assets/{name}.svg"] = render_svg(scene, 320)
        out[f"assets/{name}.png"] = render_png(scene, 320)
        out[f"assets/{name}@2x.png"] = render_png(scene, 640)

    for slug, (name, _) in REPOS.items():
        if slug in (".github", "branding"):
            continue
        for suffix, colour, accent in (("", role("ink"), role("brand")),
                                       ("_dark", role("chalk"), role("brand-bright"))):
            # Sized by height, so every product's tile is the same 64 px.
            scene = product_lockup_scene(name, colour, accent)
            out[f"lockups/{slug}{suffix}.svg"] = render_svg(scene, scene.w * 4)

    for slug in REPOS:
        out[f"social/{slug.lstrip('.')}.png"] = _flatten(render_png(social_scene(slug), 1280))

    for suffix, variant in (("", {}), ("-probes", {"beacon": True}), ("-family", {"cover": True})):
        out[f"repos/dbml-sharepoint{suffix}.png"] = render_png(tile_scene(**variant), 256)
        out[f"repos/dbml-sharepoint{suffix}.svg"] = render_svg(tile_scene(**variant), 256)

    out["tokens.json"] = tokens_json()
    out["web/firmfooting.css"] = css_tokens()
    out["web/docusaurus.css"] = css_docusaurus()
    out["web/sphinx-rtd.css"] = css_sphinx_rtd()
    return out


def tokens_json() -> str:
    """Design tokens in the W3C Design Tokens Community Group format."""
    def colour(v):
        return {"$type": "color", "$value": v}
    tokens = {
        "color": {name: {str(step): colour(v) for step, v in scale.items()}
                  for name, scale in SCALES.items()},
        "role": {name: {**colour("{color.%s.%d}" % ROLES[name]),
                        "$description": _ROLE_NOTES[name]} for name in ROLES},
        "font": {
            "display": {"$type": "fontFamily", "$value": ["Barlow", "system-ui", "sans-serif"],
                        "$description": "Wordmark, headings and names. SemiBold (600)."},
            "body": {"$type": "fontFamily",
                     "$value": ["system-ui", "-apple-system", "Segoe UI", "Roboto", "sans-serif"],
                     "$description": "Running text: the reader's own UI face."},
            "mono": {"$type": "fontFamily",
                     "$value": ["ui-monospace", "Cascadia Code", "SFMono-Regular", "Consolas", "monospace"],
                     "$description": "Code, paste-ins and file names."},
        },
        "mark": {"$description": "Footing: trapezoid (4 wide on top, 10 at the base, 4 tall) "
                                 "on a ground line 12 wide and 1 tall, one unit below, on a "
                                 "16-unit grid. Tile corner radius 3.5."},
    }
    return json.dumps(tokens, indent=2) + "\n"


_ROLE_NOTES = {
    "brand": "The tile; links and buttons on light grounds.",
    "brand-strong": "Hover and pressed states on light grounds.",
    "brand-bright": "Links and accents on dark grounds.",
    "universal": "Wordmark text that must hold on light and dark grounds alike.",
    "glyph": "The mark, on the tile.",
    "ink": "Text on light grounds.",
    "chalk": "Text on dark grounds.",
    "paper": "Light ground.",
    "night": "Dark ground.",
    "rule": "Hairlines and borders on light grounds.",
}

_CSS_HEAD = ("/* Generated by firmfooting/branding generate.py. Do not edit; "
             "change the palette there. */\n")
_FONT_IMPORT = ("@import url('https://fonts.googleapis.com/css2?family=Barlow:wght@500;600"
                "&display=swap');\n")


def css_tokens() -> str:
    lines = [_CSS_HEAD, ":root {"]
    for name, scale in SCALES.items():
        lines += [f"  --ff-{name}-{step}: {v};" for step, v in scale.items()]
    lines += [f"  --ff-{name}: var(--ff-{ROLES[name][0]}-{ROLES[name][1]});" for name in ROLES]
    lines += ["  --ff-font-display: Barlow, system-ui, sans-serif;",
              "  --ff-font-body: system-ui, -apple-system, 'Segoe UI', Roboto, sans-serif;",
              "  --ff-font-mono: ui-monospace, 'Cascadia Code', SFMono-Regular, Consolas, monospace;",
              "}", ""]
    return "\n".join(lines)


def css_docusaurus() -> str:
    """Infima variables for a Docusaurus classic theme (src/css/custom.css)."""
    light = {"": 600, "-dark": 700, "-darker": 700, "-darkest": 800,
             "-light": 500, "-lighter": 400, "-lightest": 300}
    dark = {"": 300, "-dark": 400, "-darker": 500, "-darkest": 600,
            "-light": 200, "-lighter": 100, "-lightest": 50}

    def block(selector, steps, extra):
        body = [f"  --ifm-color-primary{k}: {TEAL[v]};" for k, v in steps.items()]
        return "\n".join([f"{selector} {{", *body, *extra, "}"])

    return "\n".join([
        _CSS_HEAD + _FONT_IMPORT,
        block(":root", light, [
            f"  --ifm-font-color-base: {role('ink')};",
            f"  --ifm-background-color: {CONCRETE[0]};",
            "  --ifm-heading-font-family: Barlow, system-ui, sans-serif;",
            "  --ifm-heading-font-weight: 600;",
            "  --ifm-font-family-monospace: ui-monospace, 'Cascadia Code', SFMono-Regular, Consolas, monospace;",
            f"  --ifm-footer-background-color: {TEAL[950]};",
        ]),
        "",
        block("[data-theme='dark']", dark, [
            f"  --ifm-font-color-base: {CONCRETE[100]};",
            f"  --ifm-background-color: {CONCRETE[950]};",
            f"  --ifm-navbar-background-color: {CONCRETE[950]};",
            f"  --ifm-footer-background-color: {TEAL[950]};",
        ]),
        "",
        ".navbar__title { font-family: var(--ifm-heading-font-family); font-weight: 600; }",
        "",
    ])


def css_sphinx_rtd() -> str:
    """Overrides for sphinx_rtd_theme (html_static_path + html_css_files)."""
    return "\n".join([
        _CSS_HEAD + _FONT_IMPORT,
        f".wy-side-nav-search, .wy-nav-top {{ background: {TEAL[600]}; }}",
        f".wy-side-nav-search input[type=text] {{ border-color: {TEAL[700]}; }}",
        f".wy-menu-vertical p.caption {{ color: {TEAL[200]}; }}",
        f".wy-nav-side {{ background: {TEAL[950]}; }}",
        f"a, a:visited {{ color: {TEAL[600]}; }}",
        f"a:hover {{ color: {TEAL[700]}; }}",
        ".rst-content h1, .rst-content h2, .rst-content h3, .rst-content h4,",
        ".wy-side-nav-search > a { font-family: Barlow, system-ui, sans-serif; font-weight: 600; }",
        "/* The theme lets html_logo fill the sidebar's width; the mark reads at 64 px. */",
        ".wy-side-nav-search > a img.logo,",
        ".wy-side-nav-search .wy-dropdown > a img.logo { width: 64px; }",
        "",
    ])


def palette_markdown(rows) -> str:
    lines = ["| Role | Value | Use |", "|---|---|---|"]
    for name in ROLES:
        scale, step = ROLES[name]
        lines.append(f"| `{name}` | `{role(name)}` ({scale} {step}) | {_ROLE_NOTES[name]} |")
    lines += ["", "Every pairing the brand relies on is checked on each run; one that "
              "falls below its minimum stops the run.", "",
              "| Foreground | Background | Ratio | Minimum | Depends on it |", "|---|---|---|---|---|"]
    for fg, bg, ratio, minimum, use in rows:
        lines.append(f"| `{fg}` | `{bg}` | {ratio:.2f}:1 | {minimum:g}:1 | {use} |")
    lines += ["", "| Step | " + " | ".join(str(s) for s in TEAL) + " |",
              "|---|" + "---|" * len(TEAL),
              "| teal | " + " | ".join(f"`{v}`" for v in TEAL.values()) + " |", "",
              "| Step | " + " | ".join(str(s) for s in CONCRETE) + " |",
              "|---|" + "---|" * len(CONCRETE),
              "| concrete | " + " | ".join(f"`{v}`" for v in CONCRETE.values()) + " |"]
    return "\n".join(lines)


def readme_text(rows) -> str:
    path = os.path.join(HERE, "README.md")
    with open(path, encoding="utf-8") as f:
        text = f.read()
    return re.sub(r"(<!-- palette:start -->\n).*?(\n<!-- palette:end -->)",
                  lambda m: m.group(1) + palette_markdown(rows) + m.group(2),
                  text, flags=re.S)


# --- Write / check ------------------------------------------------------------

def _png_bytes(img: Image.Image) -> bytes:
    buf = io.BytesIO()
    img.save(buf, "PNG", optimize=True)
    return buf.getvalue()


def _same_pixels(path: str, img: Image.Image) -> bool:
    try:
        with Image.open(path) as old:
            return old.mode == img.mode and old.size == img.size and old.tobytes() == img.tobytes()
    except OSError:
        return False


def _same_ico(path: str, frames) -> bool:
    try:
        with Image.open(path) as ico:
            for size, img in frames:
                frame = ico.ico.getimage((size, size)).convert("RGBA")
                if frame.tobytes() != img.tobytes():
                    return False
            return True
    except (OSError, KeyError):
        return False


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check", action="store_true",
                        help="fail if any generated file differs from what would be written")
    args = parser.parse_args()

    rows = check_contrast()
    outputs = build()
    outputs["README.md"] = readme_text(rows)

    stale = []
    for rel, value in outputs.items():
        path = os.path.join(HERE, rel)
        if isinstance(value, str):
            same = os.path.exists(path) and open(path, encoding="utf-8").read() == value
        elif isinstance(value, list):
            same = _same_ico(path, value)
        else:
            same = _same_pixels(path, value)
        if same:
            continue
        stale.append(rel)
        if args.check:
            continue
        os.makedirs(os.path.dirname(path) or HERE, exist_ok=True)
        if isinstance(value, str):
            with open(path, "w", encoding="utf-8", newline="\n") as f:
                f.write(value)
        elif isinstance(value, list):
            largest = value[-1][1]
            largest.save(path, sizes=[(s, s) for s, _ in value],
                         append_images=[img for _, img in value[:-1]])
        else:
            with open(path, "wb") as f:
                f.write(_png_bytes(value))

    if args.check:
        if stale:
            print("stale; run python generate.py and commit the result:")
            print("\n".join(f"  {rel}" for rel in stale))
            return 1
        print(f"all {len(outputs)} generated files are current")
        return 0
    print(f"wrote {len(stale)} of {len(outputs)} files" if stale else "nothing to write")
    return 0


if __name__ == "__main__":
    sys.exit(main())
