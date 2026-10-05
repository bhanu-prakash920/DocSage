"""Helpers shared by parsers."""

from __future__ import annotations

import io
import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from PIL import Image

from ...errors import Cancelled


@dataclass
class ParseContext:
    data_dir: Path
    asset_dir: Path  # absolute: <data_dir>/assets/<collection>/<document>
    min_image_px: int = 96
    progress: Callable[[float, str], None] | None = None
    is_cancelled: Callable[[], bool] | None = None

    def rel(self, path: Path) -> str:
        return path.relative_to(self.data_dir).as_posix()

    def report(self, fraction: float, message: str) -> None:
        if self.is_cancelled and self.is_cancelled():
            raise Cancelled("Ingestion was cancelled.")
        if self.progress:
            self.progress(max(0.0, min(1.0, fraction)), message)

    def save_png(self, data: bytes | Image.Image, name: str) -> tuple[str, int, int] | None:
        """Save image bytes as PNG. Returns (relative path, width, height) or None if too small/invalid."""
        try:
            img = data if isinstance(data, Image.Image) else Image.open(io.BytesIO(data))
            img.load()
        except Exception:
            return None
        if min(img.size) < self.min_image_px:
            return None
        if img.mode not in ("RGB", "RGBA", "L"):
            img = img.convert("RGB")
        self.asset_dir.mkdir(parents=True, exist_ok=True)
        path = self.asset_dir / f"{name}.png"
        img.save(path, "PNG", optimize=True)
        return self.rel(path), img.size[0], img.size[1]


def rows_to_markdown(rows: list[list[str]]) -> str:
    rows = [[_cell(c) for c in r] for r in rows if any((c or "").strip() for c in r)]
    if not rows:
        return ""
    width = max(len(r) for r in rows)
    rows = [r + [""] * (width - len(r)) for r in rows]
    header, body = rows[0], rows[1:]
    lines = ["| " + " | ".join(header) + " |", "|" + "---|" * width]
    lines += ["| " + " | ".join(r) + " |" for r in body]
    return "\n".join(lines)


def _cell(value: object) -> str:
    text = "" if value is None else str(value)
    return re.sub(r"\s+", " ", text).replace("|", "\\|").strip()


CAPTION_RE = re.compile(r"^\s*(fig(ure)?\.?|table|chart|exhibit|diagram|graph|plate)\s*[\dA-Z]", re.I)


def tidy(text: str) -> str:
    text = text.replace("­", "").replace("\r", "")
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()
