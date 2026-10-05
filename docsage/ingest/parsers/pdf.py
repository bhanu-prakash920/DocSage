"""PDF (and other MuPDF-readable formats): page markdown, native tables, raster + vector figures."""

from __future__ import annotations

import logging
from collections import Counter
from pathlib import Path

import pymupdf

from ..schema import ImageElement, PageText, ParsedDocument, TableElement
from .base import CAPTION_RE, ParseContext, tidy

log = logging.getLogger(__name__)
RENDER_DPI = 150


def _page_markdown(doc: pymupdf.Document) -> list[str]:
    try:
        import pymupdf4llm

        chunks = pymupdf4llm.to_markdown(doc, page_chunks=True, ignore_images=True, show_progress=False)
        if isinstance(chunks, list) and len(chunks) == doc.page_count:
            return [tidy(c.get("text", "")) for c in chunks]
    except Exception as exc:  # fall back to plain text extraction
        log.warning("pymupdf4llm failed (%s); using plain text extraction", exc)
    return [tidy(page.get_text("text")) for page in doc]


def _clamp(rect: pymupdf.Rect, page: pymupdf.Page, margin: float = 6) -> pymupdf.Rect:
    """Expand by a margin and clamp to the page so crops never wrap or come back empty (fixes C5)."""
    r = pymupdf.Rect(rect.x0 - margin, rect.y0 - margin, rect.x1 + margin, rect.y1 + margin)
    return r & page.rect


def _overlap(a: pymupdf.Rect, b: pymupdf.Rect) -> float:
    inter = a & b
    if inter.is_empty:
        return 0.0
    return inter.get_area() / max(1.0, min(a.get_area(), b.get_area()))


def _caption_and_context(page: pymupdf.Page, rect: pymupdf.Rect) -> tuple[str, str]:
    blocks = [b for b in page.get_text("blocks") if b[6] == 0 and b[4].strip()]
    caption, context_parts = "", []
    best = None
    for x0, y0, x1, y1, text, *_ in blocks:
        b = pymupdf.Rect(x0, y0, x1, y1)
        horizontal = min(b.x1, rect.x1) - max(b.x0, rect.x0) > 0
        below = 0 <= b.y0 - rect.y1 <= 60
        above = 0 <= rect.y0 - b.y1 <= 45
        clean = " ".join(text.split())
        if horizontal and (below or above):
            score = (2 if CAPTION_RE.match(clean) else 0) + (1 if below else 0)
            if best is None or score > best[0]:
                best = (score, clean)
        if horizontal and (-150 <= b.y0 - rect.y1 <= 150 or -150 <= rect.y0 - b.y1 <= 150):
            context_parts.append(clean)
    if best:
        caption = best[1][:300]
    return caption, " ".join(context_parts)[:600]


def parse_pdf(path: Path, ctx: ParseContext) -> ParsedDocument:
    doc = pymupdf.open(path)
    try:
        title = (doc.metadata or {}).get("title") or path.stem
        result = ParsedDocument(title=title.strip() or path.stem, kind="pdf")
        texts = _page_markdown(doc)
        n = doc.page_count

        # Images that repeat on most pages are logos or decoration.
        xref_pages: Counter[int] = Counter()
        for page in doc:
            for xref in {img[0] for img in page.get_images(full=True)}:
                xref_pages[xref] += 1
        decorative = {x for x, c in xref_pages.items() if n >= 4 and c > n / 2}

        for index, page in enumerate(doc):
            number = index + 1
            ctx.report(index / max(1, n), f"Parsing page {number} of {n}")
            text = texts[index] if index < len(texts) else ""
            result.pages.append(PageText(number, text))
            table_rects: list[pymupdf.Rect] = []

            # --- tables (native, structured) ---------------------------------------------------
            try:
                found = page.find_tables()
                tables = list(found.tables) if hasattr(found, "tables") else list(found)
            except Exception as exc:
                log.debug("find_tables failed on page %s: %s", number, exc)
                tables = []
            for t_index, table in enumerate(tables):
                if table.row_count < 2 or table.col_count < 2:
                    continue
                md = table.to_markdown(clean=False).strip()
                if not md:
                    continue
                rect = pymupdf.Rect(table.bbox)
                table_rects.append(rect)
                crop = _clamp(rect, page)
                saved = ctx.save_png(
                    page.get_pixmap(clip=crop, dpi=RENDER_DPI).tobytes("png"), f"p{number}_table{t_index + 1}"
                )
                caption, _ = _caption_and_context(page, rect)
                result.tables.append(
                    TableElement(
                        page=number,
                        markdown=md,
                        caption=caption,
                        bbox=tuple(rect),
                        image_path=saved[0] if saved else None,
                    )
                )

            # --- raster images -------------------------------------------------------------------
            seen_rects: list[pymupdf.Rect] = []
            for i_index, img in enumerate(page.get_images(full=True)):
                xref = img[0]
                if xref in decorative:
                    continue
                for rect in page.get_image_rects(xref):
                    if (
                        rect.width < 40
                        or rect.height < 40
                        or any(_overlap(rect, r) > 0.8 for r in seen_rects)
                    ):
                        continue
                    seen_rects.append(rect)
                    clip = _clamp(rect, page, margin=0)
                    saved = ctx.save_png(
                        page.get_pixmap(clip=clip, dpi=RENDER_DPI).tobytes("png"),
                        f"p{number}_img{i_index + 1}",
                    )
                    if not saved:
                        continue
                    caption, context = _caption_and_context(page, rect)
                    result.images.append(
                        ImageElement(
                            page=number,
                            image_path=saved[0],
                            caption=caption,
                            context=context,
                            bbox=tuple(rect),
                            width=saved[1],
                            height=saved[2],
                        )
                    )

            # --- vector figures (charts and diagrams drawn as paths) -------------------------------
            page_area = page.rect.get_area()
            try:
                clusters = page.cluster_drawings()
            except Exception:
                clusters = []
            added = 0
            for rect in sorted(clusters, key=lambda r: -r.get_area()):
                share = rect.get_area() / page_area
                if added >= 2 or not (0.05 <= share <= 0.8) or rect.width < 100 or rect.height < 80:
                    continue
                if any(_overlap(rect, r) > 0.5 for r in table_rects + seen_rects):
                    continue
                drawings = [d for d in page.get_drawings() if pymupdf.Rect(d["rect"]).intersects(rect)]
                if len(drawings) < 8:
                    continue
                saved = ctx.save_png(
                    page.get_pixmap(clip=_clamp(rect, page), dpi=RENDER_DPI).tobytes("png"),
                    f"p{number}_fig{added + 1}",
                )
                if not saved:
                    continue
                added += 1
                seen_rects.append(rect)
                caption, context = _caption_and_context(page, rect)
                result.images.append(
                    ImageElement(
                        page=number,
                        image_path=saved[0],
                        caption=caption,
                        context=context,
                        bbox=tuple(rect),
                        width=saved[1],
                        height=saved[2],
                        kind="vector",
                    )
                )

            # --- scanned pages: hand the full page to the vision model ---------------------------
            if len(text.strip()) < 25 and (page.get_images() or page.get_drawings()):
                saved = ctx.save_png(page.get_pixmap(dpi=RENDER_DPI).tobytes("png"), f"p{number}_scan")
                if saved:
                    result.images.append(
                        ImageElement(
                            page=number,
                            image_path=saved[0],
                            caption=f"Scanned page {number}",
                            width=saved[1],
                            height=saved[2],
                            kind="scan",
                        )
                    )
                    result.warnings.append(
                        f"Page {number} has no text layer; it is described by the vision model."
                    )
        ctx.report(1.0, "Parsed")
        return result
    finally:
        doc.close()
