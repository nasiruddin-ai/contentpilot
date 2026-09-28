"""Renders brand-styled graphics with Pillow (spec sections 43-44): quote cards,
minimal graphics, infographics and carousel slides. No AI image model involved,
so output always matches the brand kit and costs nothing."""

import io
from dataclasses import dataclass, field

from PIL import Image, ImageDraw

from app.visuals import fonts

# Canvas sizes for the supported aspect ratios.
SIZES: dict[str, tuple[int, int]] = {
    "1:1": (1080, 1080),
    "4:5": (1080, 1350),
    "16:9": (1600, 900),
    "9:16": (1080, 1920),
    "1.91:1": (1200, 628),
}

DEFAULT_COLORS = {"primary": "#0F172A", "secondary": "#1E293B", "accent": "#6366F1"}
LIGHT_TEXT, DARK_TEXT = "#FFFFFF", "#0F172A"


@dataclass
class BrandStyle:
    name: str
    primary: str = DEFAULT_COLORS["primary"]
    secondary: str = DEFAULT_COLORS["secondary"]
    accent: str = DEFAULT_COLORS["accent"]
    heading_font: str | None = None
    body_font: str | None = None

    @property
    def text(self) -> str:
        return readable_on(self.primary)


@dataclass
class Slide:
    headline: str
    subtext: str = ""
    layout: str = "headline_center"  # headline_center | text_left | quote | title_points
    points: list[str] = field(default_factory=list)
    attribution: str = ""


@dataclass
class RenderResult:
    image: Image.Image
    # Text that had to be shortened because it couldn't fit even at the smallest size.
    truncated: list[str] = field(default_factory=list)


# --- Colour ---------------------------------------------------------------------


def _rgb(hex_color: str) -> tuple[int, int, int]:
    value = hex_color.lstrip("#")
    return int(value[0:2], 16), int(value[2:4], 16), int(value[4:6], 16)


def _luminance(hex_color: str) -> float:
    def channel(c: int) -> float:
        c = c / 255
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4

    r, g, b = (channel(c) for c in _rgb(hex_color))
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast_ratio(a: str, b: str) -> float:
    """WCAG contrast ratio, 1 to 21."""
    light, dark = sorted((_luminance(a), _luminance(b)), reverse=True)
    return (light + 0.05) / (dark + 0.05)


def readable_on(background: str) -> str:
    """White or near-black text, whichever is more readable on `background`."""
    return LIGHT_TEXT if contrast_ratio(background, LIGHT_TEXT) >= contrast_ratio(background, DARK_TEXT) else DARK_TEXT


def accent_for(style: BrandStyle) -> str:
    """The brand accent, unless it would vanish against the background."""
    for color in (style.accent, style.secondary):
        if contrast_ratio(color, style.primary) >= 1.8:
            return color
    return style.text


# --- Text layout -------------------------------------------------------------------


def _wrap(text: str, font, max_width: int) -> list[str]:
    lines: list[str] = []
    for paragraph in text.split("\n"):
        line = ""
        for word in paragraph.split():
            attempt = f"{line} {word}".strip()
            if font.getlength(attempt) <= max_width or not line:
                line = attempt
            else:
                lines.append(line)
                line = word
        lines.append(line)
    return lines


def fit_text(
    text: str, family: str | None, bold: bool, box: tuple[int, int], max_size: int, min_size: int, spacing: float = 1.2
):
    """Largest font size at which `text` fits in `box`. Returns (font, lines, truncated)."""
    width, height = box
    for size in range(max_size, min_size - 1, -2):
        font = fonts.load(family, size, bold, text)
        lines = _wrap(text, font, width)
        if len(lines) * size * spacing <= height and all(font.getlength(line) <= width for line in lines):
            return font, lines, False

    font = fonts.load(family, min_size, bold, text)
    lines = _wrap(text, font, width)
    max_lines = max(1, int(height // (min_size * spacing)))
    if len(lines) > max_lines:
        lines = lines[:max_lines]
        lines[-1] = lines[-1].rstrip(".,;: ") + "…"
        return font, lines, True
    return font, lines, False


def _draw_lines(draw, lines, font, x, y, fill, spacing=1.2, align="left", width=0) -> int:
    size = font.size
    for line in lines:
        dx = x + (width - font.getlength(line)) / 2 if align == "center" else x
        fonts.draw_text(draw, (dx, y), line, font, fill)
        y += int(size * spacing)
    return y


# --- Layouts ------------------------------------------------------------------------


def render_slide(
    slide: Slide, style: BrandStyle, aspect_ratio: str, *, number: int | None = None, total: int | None = None
) -> RenderResult:
    width, height = SIZES[aspect_ratio]
    image = Image.new("RGB", (width, height), _rgb(style.primary))
    draw = ImageDraw.Draw(image)
    text, accent = style.text, accent_for(style)
    margin = int(width * 0.08)
    inner = width - 2 * margin
    footer_h = int(height * 0.08)
    truncated: list[str] = []

    def fit(value, bold, box, max_size, min_size):
        font, lines, cut = fit_text(value, style.heading_font if bold else style.body_font, bold, box, max_size, min_size)
        if cut:
            truncated.append(value)
        return font, lines

    body_top, body_bottom = margin, height - margin - footer_h
    body_h = body_bottom - body_top
    big = int(min(width, height) * 0.09)

    if slide.layout == "quote":
        quote_font = fonts.load(style.heading_font, int(big * 2.4), True)
        draw.text((margin, body_top - int(big * 0.6)), "“", font=quote_font, fill=accent)
        font, lines = fit(slide.headline, True, (inner, int(body_h * 0.62)), big, 28)
        y = _draw_lines(draw, lines, font, margin, body_top + int(big * 1.3), text)
        if slide.attribution:
            sub_font, sub_lines = fit(f"- {slide.attribution}", False, (inner, int(body_h * 0.15)), int(big * 0.45), 20)
            _draw_lines(draw, sub_lines, sub_font, margin, y + int(big * 0.5), accent)

    elif slide.layout == "title_points":
        draw.rectangle([margin, body_top, margin + int(width * 0.12), body_top + 10], fill=accent)
        font, lines = fit(slide.headline, True, (inner, int(body_h * 0.28)), int(big * 0.9), 26)
        y = _draw_lines(draw, lines, font, margin, body_top + 40, text)
        points = slide.points[:6]
        if points:
            slot = (body_bottom - y - 30) // len(points)
            for index, point in enumerate(points, start=1):
                top = y + 30 + (index - 1) * slot
                radius = int(min(slot * 0.32, big * 0.45))
                draw.ellipse([margin, top, margin + 2 * radius, top + 2 * radius], fill=accent)
                number_font = fonts.load(style.heading_font, int(radius * 1.1), True)
                label = str(index)
                draw.text(
                    (margin + radius - number_font.getlength(label) / 2, top + radius - number_font.size * 0.6),
                    label,
                    font=number_font,
                    fill=readable_on(accent),
                )
                p_font, p_lines = fit(point, False, (inner - 2 * radius - 30, slot - 10), int(big * 0.5), 20)
                _draw_lines(draw, p_lines, p_font, margin + 2 * radius + 30, top, text)

    else:
        left = slide.layout == "text_left"
        if left:
            draw.rectangle([margin, body_top, margin + 12, body_bottom], fill=accent)
            x, box_w = margin + 50, inner - 50
        else:
            draw.rectangle([margin, body_top, margin + int(width * 0.12), body_top + 10], fill=accent)
            x, box_w = margin, inner
        head_font, head_lines = fit(slide.headline, True, (box_w, int(body_h * 0.55)), big, 28)
        sub_font, sub_lines = (None, [])
        if slide.subtext:
            sub_font, sub_lines = fit(slide.subtext, False, (box_w, int(body_h * 0.3)), int(big * 0.5), 20)
        block_h = len(head_lines) * int(head_font.size * 1.2) + (
            int(big * 0.5) + len(sub_lines) * int(sub_font.size * 1.2) if sub_font else 0
        )
        y = body_top + max(40, (body_h - block_h) // 2)
        align = "left" if left else "center"
        y = _draw_lines(draw, head_lines, head_font, x, y, text, align=align, width=box_w)
        if sub_font:
            _draw_lines(draw, sub_lines, sub_font, x, y + int(big * 0.5), text, align=align, width=box_w)

    # Footer: brand name, and slide position for carousels.
    footer_size = max(18, int(footer_h * 0.35))
    footer_font = fonts.load(style.body_font, footer_size, True)
    footer_y = height - margin - footer_font.size
    fonts.draw_text(draw, (margin, footer_y), style.name, fonts.load(style.body_font, footer_size, True, style.name), accent)
    if number is not None and total:
        counter = f"{number}/{total}"
        draw.text((width - margin - footer_font.getlength(counter), footer_y), counter, font=footer_font, fill=text)
    return RenderResult(image=image, truncated=truncated)


def to_png(image: Image.Image) -> bytes:
    buffer = io.BytesIO()
    image.save(buffer, format="PNG", optimize=True)
    return buffer.getvalue()


def to_thumbnail(image: Image.Image, width: int = 400) -> bytes:
    thumb = image.copy()
    thumb.thumbnail((width, width * 2))
    buffer = io.BytesIO()
    thumb.convert("RGB").save(buffer, format="JPEG", quality=82, optimize=True)
    return buffer.getvalue()


def to_pdf(images: list[Image.Image]) -> bytes:
    """Multi-page PDF, the format LinkedIn uses for document carousels."""
    buffer = io.BytesIO()
    first, *rest = [i.convert("RGB") for i in images]
    first.save(buffer, format="PDF", save_all=True, append_images=rest, resolution=144)
    return buffer.getvalue()
