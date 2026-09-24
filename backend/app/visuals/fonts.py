"""Finds a font file for a brand's font name, falling back to bundled open-source fonts."""

import functools
import re
from pathlib import Path

from PIL import ImageFont

FONT_DIRS = [Path("/usr/share/fonts"), Path("/usr/local/share/fonts"), Path("C:/Windows/Fonts")]
# Installed in the Docker image (see Dockerfile).
FALLBACK_FAMILIES = ["Inter", "DejaVuSans"]


@functools.lru_cache(maxsize=1)
def _font_files() -> list[Path]:
    files: list[Path] = []
    for directory in FONT_DIRS:
        if directory.is_dir():
            files.extend(p for p in directory.rglob("*") if p.suffix.lower() in {".ttf", ".otf"})
    return files


def _squash(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", name.lower())


@functools.lru_cache(maxsize=64)
def find_font(family: str | None, bold: bool) -> str | None:
    """Path of the best matching installed font, or None."""
    for candidate in [family, *FALLBACK_FAMILIES]:
        if not candidate:
            continue
        wanted = _squash(candidate)
        matches = [f for f in _font_files() if _squash(f.stem).startswith(wanted)]
        if not matches:
            continue

        def rank(path: Path) -> tuple[int, int]:
            stem = path.stem.lower()
            has_bold = "bold" in stem and "semibold" not in stem and "extrabold" not in stem
            is_plain = not any(w in stem for w in ("italic", "light", "thin", "black", "medium", "semi", "extra", "condensed"))
            wants = has_bold if bold else not has_bold
            # Prefer the requested weight, then upright regular shapes, then short names.
            return (0 if wants and (is_plain or has_bold) else 1, len(stem))

        return str(sorted(matches, key=rank)[0])
    return None


def load(family: str | None, size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    path = find_font(family, bold)
    if path:
        return ImageFont.truetype(path, size)
    # Pillow's built-in scalable font: always available.
    return ImageFont.load_default(size=size)
