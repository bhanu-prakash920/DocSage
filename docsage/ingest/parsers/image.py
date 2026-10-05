"""Standalone image files: described by the vision model, optionally OCR'd for tables."""

from __future__ import annotations

import logging
from pathlib import Path

from ...config import Settings
from ..schema import ImageElement, PageText, ParsedDocument, TableElement
from .base import ParseContext

log = logging.getLogger(__name__)


def parse_image(path: Path, ctx: ParseContext, settings: Settings) -> ParsedDocument:
    result = ParsedDocument(title=path.stem, kind="image")
    result.pages.append(PageText(1, ""))
    previous = ctx.min_image_px
    ctx.min_image_px = 16  # the file itself is the content; keep small images
    saved = ctx.save_png(path.read_bytes(), "image")
    ctx.min_image_px = previous
    if not saved:
        result.warnings.append("The image could not be decoded.")
        return result
    result.images.append(
        ImageElement(
            page=1,
            image_path=saved[0],
            caption=path.stem.replace("_", " "),
            width=saved[1],
            height=saved[2],
            kind="image-file",
        )
    )
    if settings.ocr_tables:
        result.tables.extend(ocr_tables(path, page=1))
    ctx.report(1.0, "Parsed")
    return result


def ocr_tables(path: Path, page: int) -> list[TableElement]:
    """Optional img2table + EasyOCR extraction (install the 'ocr' extra)."""
    try:
        from img2table.document import Image as Img2TableImage
        from img2table.ocr import EasyOCR
    except ImportError:
        log.info("ocr_tables is on but img2table is not installed; skipping OCR tables")
        return []
    out = []
    try:
        tables = Img2TableImage(str(path)).extract_tables(
            ocr=EasyOCR(lang=["en"]), implicit_rows=True, borderless_tables=True
        )
        for table in tables:
            md = table.df.to_markdown(index=False) if table.df is not None else ""
            if md:
                out.append(TableElement(page=page, markdown=md, caption=table.title or ""))
    except Exception as exc:
        log.warning("OCR table extraction failed: %s", exc)
    return out
