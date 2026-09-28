"""Finds a font file for a brand's font name, falling back to bundled open-source fonts."""

import functools
import re
from pathlib import Path

from PIL import ImageFont

from app.core.language import has_bengali

FONT_DIRS = [Path("/usr/share/fonts"), Path("/usr/local/share/fonts"), Path("C:/Windows/Fonts")]
# Installed in the Docker image (see Dockerfile).
FALLBACK_FAMILIES = ["Inter", "DejaVuSans"]
# Fonts with Bengali glyphs (they also cover Latin letters and digits, so mixed lines work).
# Noto Sans Bengali ships in the Docker image; Nirmala UI is the Windows equivalent.
BENGALI_FAMILIES = ["NotoSansBengali", "NirmalaUI", "Nirmala"]


@functools.lru_cache(maxsize=1)
def _font_files() -> list[Path]:
    files: list[Path] = []
    for directory in FONT_DIRS:
        if directory.is_dir():
            files.extend(p for p in directory.rglob("*") if p.suffix.lower() in {".ttf", ".otf"})
    return files


def _squash(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", name.lower())


def _match(candidate: str, bold: bool) -> str | None:
    wanted = _squash(candidate)
    matches = [f for f in _font_files() if _squash(f.stem).startswith(wanted)]
    if not matches:
        return None

    def rank(path: Path) -> tuple[int, int]:
        stem = path.stem.lower()
        has_bold = "bold" in stem and "semibold" not in stem and "extrabold" not in stem
        is_plain = not any(w in stem for w in ("italic", "light", "thin", "black", "medium", "semi", "extra", "condensed"))
        wants = has_bold if bold else not has_bold
        # Prefer the requested weight, then upright regular shapes, then short names.
        return (0 if wants and (is_plain or has_bold) else 1, len(stem))

    return str(sorted(matches, key=rank)[0])


@functools.lru_cache(maxsize=64)
def find_font(family: str | None, bold: bool) -> str | None:
    """Path of the best matching installed font, or None."""
    for candidate in [family, *FALLBACK_FAMILIES]:
        if candidate and (path := _match(candidate, bold)):
            return path
    return None


@functools.lru_cache(maxsize=8)
def find_bengali_font(bold: bool) -> str | None:
    for candidate in BENGALI_FAMILIES:
        if path := _match(candidate, bold):
            return path
    return None


# Latin letters (with accents). Bengali fonts have digits and punctuation but no Latin letters.
_LATIN_RUN = re.compile(r"([A-Za-zÀ-ɏ][A-Za-zÀ-ɏ'’-]*)")


class MixedFont:
    """Bengali text that may contain English words: Latin letters are drawn with the brand
    font, everything else with the Bengali font, on one shared baseline."""

    def __init__(self, bengali: ImageFont.FreeTypeFont, latin: ImageFont.FreeTypeFont) -> None:
        self.bengali, self.latin = bengali, latin
        self.size = bengali.size
        self.path = bengali.path

    def _runs(self, text: str) -> list[tuple[str, ImageFont.FreeTypeFont]]:
        parts = _LATIN_RUN.split(text)
        # re.split with a group alternates: other, latin, other, latin, ...
        return [(part, self.latin if i % 2 else self.bengali) for i, part in enumerate(parts) if part]

    def getlength(self, text: str) -> float:
        return sum(font.getlength(part) for part, font in self._runs(text))

    def draw(self, draw, xy: tuple[float, float], text: str, fill) -> None:
        x, y = xy
        baseline = y + self.bengali.getmetrics()[0]
        for part, font in self._runs(text):
            draw.text((x, baseline), part, font=font, fill=fill, anchor="ls")
            x += font.getlength(part)


def _truetype(path: str | None, size: int) -> ImageFont.FreeTypeFont:
    if path:
        return ImageFont.truetype(path, size)
    # Pillow's built-in scalable font: always available.
    return ImageFont.load_default(size=size)


def load(family: str | None, size: int, bold: bool = False, text: str | None = None):
    """The brand font, or a Bengali + brand font pair when `text` contains Bengali (brand fonts rarely do)."""
    latin = _truetype(find_font(family, bold), size)
    bengali_path = find_bengali_font(bold) if has_bengali(text) else None
    if bengali_path:
        return MixedFont(ImageFont.truetype(bengali_path, size), latin)
    return latin


def draw_text(draw, xy: tuple[float, float], text: str, font, fill) -> None:
    """draw.text that also handles MixedFont."""
    if isinstance(font, MixedFont):
        font.draw(draw, xy, text, fill)
    else:
        draw.text(xy, text, font=font, fill=fill)
