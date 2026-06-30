"""Cover art generator.

Takes a user-supplied base image from ``cover/`` and overlays the mixtape
title on top using one of a few built-in synthwave/retro presets. The
result is square, 1500x1500, and suitable for the Mixcloud upload
``picture`` field.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from PIL import Image, ImageDraw, ImageFilter, ImageFont

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

#: Final cover dimensions. Mixcloud recommends >= 1000x1000; we go a bit bigger
#: so the text overlay stays crisp if the platform downscales.
COVER_SIZE = 1500

#: Preferred filenames — tried first so users can pin a specific image if they
#: drop several into the folder.
_PREFERRED_BASE_NAMES = ("cover_base.jpg", "cover_base.jpeg", "cover_base.png")

#: Image extensions we recognize when falling back to "any image in the folder".
_BASE_EXTS = (".jpg", ".jpeg", ".png", ".webp", ".bmp")

#: Supported preset ids.
PRESETS = ("neon", "chrome", "outrun")

#: Font candidates, tried in order. Absolute paths are checked directly; bare
#: names are resolved via PIL's own font search.
_FONT_CANDIDATES = (
    r"C:\Windows\Fonts\impact.ttf",
    r"C:\Windows\Fonts\bahnschrift.ttf",
    r"C:\Windows\Fonts\arialbd.ttf",
    "Impact.ttf",
    "Arial Bold.ttf",
    "DejaVuSans-Bold.ttf",
)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def find_base_image(cover_dir: Path) -> Path | None:
    """Return the base image to use from ``cover_dir``, or None.

    Lookup strategy:
      1. If a file with one of the preferred names exists
         (``cover_base.jpg`` / ``.jpeg`` / ``.png``, case-insensitive),
         use it. This lets power users pin a specific image.
      2. Otherwise, use the first image file (by name sort) with a
         recognized extension. This is the common path — the user just
         drops *any* image into the folder and it works.
    """
    if not cover_dir.exists():
        return None
    files = [p for p in cover_dir.iterdir() if p.is_file()]

    # 1. Preferred names (case-insensitive).
    by_lower = {p.name.lower(): p for p in files}
    for name in _PREFERRED_BASE_NAMES:
        hit = by_lower.get(name.lower())
        if hit is not None:
            return hit

    # 2. Any image file, alphabetical order for determinism.
    images = sorted(
        (p for p in files if p.suffix.lower() in _BASE_EXTS),
        key=lambda p: p.name.lower(),
    )
    return images[0] if images else None


def generate_cover(
    base_image_path: Path,
    title: str,
    preset: str,
    out_path: Path,
    text_scale: float = 1.0,
) -> Path:
    """Render ``title`` onto ``base_image_path`` using ``preset`` and save
    the result to ``out_path``. Returns the output path.

    ``text_scale`` is a multiplier applied to the preset's starting font
    size. Values < 1.0 lower the ceiling (smaller text for short titles);
    values > 1.0 raise it. The auto-fit floor is unchanged, so very long
    titles still shrink to fit regardless of the caller's choice.
    ``1.0`` (default) preserves the original behavior.
    """
    if preset not in PRESETS:
        preset = "neon"

    base = Image.open(base_image_path).convert("RGB")
    base = _square_crop_and_resize(base, COVER_SIZE)

    # Dispatch to preset-specific renderer.
    if preset == "neon":
        out = _render_neon(base, title, text_scale)
    elif preset == "chrome":
        out = _render_chrome(base, title, text_scale)
    else:  # outrun
        out = _render_outrun(base, title, text_scale)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out.save(out_path, "JPEG", quality=92)
    return out_path


# ---------------------------------------------------------------------------
# Image helpers
# ---------------------------------------------------------------------------

def _square_crop_and_resize(img: Image.Image, size: int) -> Image.Image:
    """Center-crop to a square, then resize to ``size`` x ``size``."""
    w, h = img.size
    side = min(w, h)
    left = (w - side) // 2
    top = (h - side) // 2
    img = img.crop((left, top, left + side, top + side))
    return img.resize((size, size), Image.LANCZOS)


def _load_font(size: int) -> ImageFont.ImageFont:
    for candidate in _FONT_CANDIDATES:
        try:
            return ImageFont.truetype(candidate, size=size)
        except (OSError, IOError):
            continue
    # Last-ditch: PIL's bitmap default. Looks rough but never crashes.
    return ImageFont.load_default()


def _wrap_title(draw: ImageDraw.ImageDraw, title: str, font: ImageFont.ImageFont,
                max_width: int) -> list[str]:
    """Greedy word-wrap so that no line exceeds ``max_width`` pixels.
    Falls back to character-wrap if a single word is wider than the box."""
    words = title.split()
    if not words:
        return [""]
    lines: list[str] = []
    current = words[0]
    for word in words[1:]:
        test = f"{current} {word}"
        if _text_width(draw, test, font) <= max_width:
            current = test
        else:
            lines.append(current)
            current = word
    lines.append(current)
    # Hard-break any line that is still too wide (very long single word).
    fixed: list[str] = []
    for line in lines:
        if _text_width(draw, line, font) <= max_width:
            fixed.append(line)
            continue
        chunk = ""
        for ch in line:
            if _text_width(draw, chunk + ch, font) <= max_width:
                chunk += ch
            else:
                if chunk:
                    fixed.append(chunk)
                chunk = ch
        if chunk:
            fixed.append(chunk)
    # Mixcloud covers are busy enough — clamp to 3 lines.
    return fixed[:3]


def _text_width(draw: ImageDraw.ImageDraw, text: str,
                font: ImageFont.ImageFont) -> int:
    if not text:
        return 0
    l, t, r, b = draw.textbbox((0, 0), text, font=font)
    return r - l


def _text_height(draw: ImageDraw.ImageDraw, text: str,
                 font: ImageFont.ImageFont) -> int:
    if not text:
        return 0
    l, t, r, b = draw.textbbox((0, 0), text, font=font)
    return b - t


def _fit_font_size(draw: ImageDraw.ImageDraw, title: str, max_width: int,
                   start: int, minimum: int,
                   text_scale: float = 1.0) -> tuple[ImageFont.ImageFont, list[str]]:
    """Shrink the font until the wrapped title fits within ``max_width``.
    Returns the chosen font and the wrapped lines.

    ``text_scale`` multiplies ``start`` (the ceiling), clamped so the scaled
    ceiling is never below ``minimum``. This lets callers make short titles
    render smaller without breaking the shrink-to-fit fallback for long ones.
    """
    start = max(int(start * text_scale), minimum)
    size = start
    while size >= minimum:
        font = _load_font(size)
        lines = _wrap_title(draw, title.upper(), font, max_width)
        if all(_text_width(draw, line, font) <= max_width for line in lines):
            return font, lines
        size -= 10
    font = _load_font(minimum)
    lines = _wrap_title(draw, title.upper(), font, max_width)
    return font, lines


# ---------------------------------------------------------------------------
# Preset renderers
# ---------------------------------------------------------------------------

def _render_neon(base: Image.Image, title: str, text_scale: float = 1.0) -> Image.Image:
    """Hot pink fill + cyan outer glow, bottom third, with a translucent
    black gradient so the text reads on any background."""
    img = base.copy()
    img = _apply_bottom_scrim(img, strength=180)

    draw = ImageDraw.Draw(img, "RGBA")
    max_width = int(COVER_SIZE * 0.86)
    font, lines = _fit_font_size(draw, title, max_width, start=190, minimum=60,
                                 text_scale=text_scale)

    line_h = max(_text_height(draw, line, font) for line in lines) if lines else 0
    spacing = int(line_h * 0.18)
    total_h = line_h * len(lines) + spacing * max(len(lines) - 1, 0)
    # Anchor the block near the bottom (inside the scrim) rather than
    # centering it mid-image, so the title sits low and doesn't cover the
    # artwork's focal point.
    bottom_margin = int(COVER_SIZE * 0.07)
    y0 = COVER_SIZE - bottom_margin - total_h

    # Cyan glow layer (blurred fat outline).
    glow = Image.new("RGBA", img.size, (0, 0, 0, 0))
    gdraw = ImageDraw.Draw(glow)
    for i, line in enumerate(lines):
        lw = _text_width(gdraw, line, font)
        x = (COVER_SIZE - lw) // 2
        y = y0 + i * (line_h + spacing)
        gdraw.text((x, y), line, font=font, fill=(0, 255, 255, 255),
                   stroke_width=10, stroke_fill=(0, 255, 255, 255))
    glow = glow.filter(ImageFilter.GaussianBlur(radius=14))
    img.alpha_composite(glow) if img.mode == "RGBA" else img.paste(glow, (0, 0), glow)

    # Hot pink fill with a thin dark outline for definition.
    for i, line in enumerate(lines):
        lw = _text_width(draw, line, font)
        x = (COVER_SIZE - lw) // 2
        y = y0 + i * (line_h + spacing)
        draw.text((x, y), line, font=font, fill=(255, 46, 150, 255),
                  stroke_width=3, stroke_fill=(30, 0, 30, 255))

    return img.convert("RGB")


def _render_chrome(base: Image.Image, title: str, text_scale: float = 1.0) -> Image.Image:
    """Metallic silver gradient fill + heavy drop shadow. Cinematic, bold."""
    img = base.copy()
    img = _apply_bottom_scrim(img, strength=200)

    draw = ImageDraw.Draw(img, "RGBA")
    max_width = int(COVER_SIZE * 0.86)
    font, lines = _fit_font_size(draw, title, max_width, start=200, minimum=60,
                                 text_scale=text_scale)

    line_h = max(_text_height(draw, line, font) for line in lines) if lines else 0
    spacing = int(line_h * 0.18)
    total_h = line_h * len(lines) + spacing * max(len(lines) - 1, 0)
    # Anchor the block near the bottom (inside the scrim) rather than
    # centering it mid-image, so the title sits low and doesn't cover the
    # artwork's focal point.
    bottom_margin = int(COVER_SIZE * 0.07)
    y0 = COVER_SIZE - bottom_margin - total_h

    # Drop shadow (offset + blur).
    shadow = Image.new("RGBA", img.size, (0, 0, 0, 0))
    sdraw = ImageDraw.Draw(shadow)
    for i, line in enumerate(lines):
        lw = _text_width(sdraw, line, font)
        x = (COVER_SIZE - lw) // 2
        y = y0 + i * (line_h + spacing)
        sdraw.text((x + 8, y + 10), line, font=font,
                   fill=(0, 0, 0, 230), stroke_width=4, stroke_fill=(0, 0, 0, 230))
    shadow = shadow.filter(ImageFilter.GaussianBlur(radius=6))
    img.alpha_composite(shadow) if img.mode == "RGBA" else img.paste(shadow, (0, 0), shadow)

    # Chrome gradient text: render white text into a mask, then paste a
    # vertical silver gradient through it.
    text_layer = Image.new("L", img.size, 0)
    tdraw = ImageDraw.Draw(text_layer)
    for i, line in enumerate(lines):
        lw = _text_width(tdraw, line, font)
        x = (COVER_SIZE - lw) // 2
        y = y0 + i * (line_h + spacing)
        tdraw.text((x, y), line, font=font, fill=255,
                   stroke_width=3, stroke_fill=255)

    gradient = _vertical_gradient(
        img.size,
        top=(240, 245, 255),
        mid=(130, 140, 160),
        bottom=(245, 245, 250),
    )
    img.paste(gradient, (0, 0), text_layer)
    return img.convert("RGB")


def _render_outrun(base: Image.Image, title: str, text_scale: float = 1.0) -> Image.Image:
    """Magenta→orange gradient fill with subtle scanlines, centered."""
    img = base.copy()
    img = _apply_bottom_scrim(img, strength=160)

    draw = ImageDraw.Draw(img, "RGBA")
    max_width = int(COVER_SIZE * 0.86)
    font, lines = _fit_font_size(draw, title, max_width, start=200, minimum=60,
                                 text_scale=text_scale)

    line_h = max(_text_height(draw, line, font) for line in lines) if lines else 0
    spacing = int(line_h * 0.18)
    total_h = line_h * len(lines) + spacing * max(len(lines) - 1, 0)
    # Anchor the block near the bottom (inside the scrim) rather than
    # centering it mid-image, so the title sits low and doesn't cover the
    # artwork's focal point.
    bottom_margin = int(COVER_SIZE * 0.07)
    y0 = COVER_SIZE - bottom_margin - total_h

    # Glow (hot magenta, blurred).
    glow = Image.new("RGBA", img.size, (0, 0, 0, 0))
    gdraw = ImageDraw.Draw(glow)
    for i, line in enumerate(lines):
        lw = _text_width(gdraw, line, font)
        x = (COVER_SIZE - lw) // 2
        y = y0 + i * (line_h + spacing)
        gdraw.text((x, y), line, font=font, fill=(255, 60, 160, 255),
                   stroke_width=8, stroke_fill=(255, 60, 160, 255))
    glow = glow.filter(ImageFilter.GaussianBlur(radius=12))
    img.alpha_composite(glow) if img.mode == "RGBA" else img.paste(glow, (0, 0), glow)

    # Magenta→orange vertical gradient text fill.
    text_layer = Image.new("L", img.size, 0)
    tdraw = ImageDraw.Draw(text_layer)
    for i, line in enumerate(lines):
        lw = _text_width(tdraw, line, font)
        x = (COVER_SIZE - lw) // 2
        y = y0 + i * (line_h + spacing)
        tdraw.text((x, y), line, font=font, fill=255,
                   stroke_width=4, stroke_fill=255)

    gradient = _vertical_gradient(
        img.size,
        top=(255, 80, 200),   # hot pink / magenta
        mid=(255, 120, 80),   # coral
        bottom=(255, 200, 60), # amber
    )
    img.paste(gradient, (0, 0), text_layer)

    # Subtle horizontal scanlines across the whole image.
    img = _apply_scanlines(img, alpha=22, gap=3)
    return img.convert("RGB")


# ---------------------------------------------------------------------------
# Pixel utilities
# ---------------------------------------------------------------------------

def _apply_bottom_scrim(img: Image.Image, strength: int) -> Image.Image:
    """Add a bottom-weighted translucent black gradient so overlaid text
    stays legible even on busy backgrounds.

    Implemented as a 1xh alpha-channel gradient resized to full width —
    the heavy lifting happens entirely in Pillow's C layer. The naive
    per-pixel Python loop was ~1.4s on a 1500x1500 image; this version
    is well under 30 ms.
    """
    if img.mode != "RGBA":
        img = img.convert("RGBA")
    w, h = img.size
    top = int(h * 0.40)

    # Build a 1-pixel-wide alpha ramp: fully transparent down to ``top``,
    # then a linear ramp 0 -> strength for the bottom 60%.
    alpha_col = Image.new("L", (1, h), 0)
    px = alpha_col.load()
    denom = max(h - top - 1, 1)
    for y in range(top, h):
        px[0, y] = int(strength * (y - top) / denom)
    alpha = alpha_col.resize((w, h), Image.BILINEAR)

    scrim = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    scrim.putalpha(alpha)
    return Image.alpha_composite(img, scrim)


def _vertical_gradient(size: tuple[int, int], top: tuple[int, int, int],
                       mid: tuple[int, int, int],
                       bottom: tuple[int, int, int]) -> Image.Image:
    """Three-stop vertical gradient as an RGB image."""
    w, h = size
    grad = Image.new("RGB", (1, h), (0, 0, 0))
    pixels = grad.load()
    half = h // 2
    for y in range(h):
        if y < half:
            t = y / max(half - 1, 1)
            r = int(top[0] + (mid[0] - top[0]) * t)
            g = int(top[1] + (mid[1] - top[1]) * t)
            b = int(top[2] + (mid[2] - top[2]) * t)
        else:
            t = (y - half) / max(h - half - 1, 1)
            r = int(mid[0] + (bottom[0] - mid[0]) * t)
            g = int(mid[1] + (bottom[1] - mid[1]) * t)
            b = int(mid[2] + (bottom[2] - mid[2]) * t)
        pixels[0, y] = (r, g, b)
    return grad.resize(size, Image.BILINEAR)


def _apply_scanlines(img: Image.Image, alpha: int, gap: int) -> Image.Image:
    """Overlay thin horizontal dark scanlines for a retro CRT look."""
    if img.mode != "RGBA":
        img = img.convert("RGBA")
    w, h = img.size
    overlay = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    for y in range(0, h, gap):
        draw.line([(0, y), (w, y)], fill=(0, 0, 0, alpha), width=1)
    return Image.alpha_composite(img, overlay)
