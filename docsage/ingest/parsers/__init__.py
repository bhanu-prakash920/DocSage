"""File-type detection and parser dispatch."""

from __future__ import annotations

from pathlib import Path

from ...config import Settings
from ...errors import ConfigurationError, UnsupportedFile
from ..schema import ParsedDocument
from .base import ParseContext

PDF_LIKE = {".pdf", ".epub", ".xps", ".fb2", ".mobi", ".cbz"}
IMAGE_EXT = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".tif", ".tiff"}
SUPPORTED: dict[str, str] = {
    **{e: "pdf" for e in PDF_LIKE},
    ".docx": "docx",
    ".pptx": "pptx",
    ".html": "html",
    ".htm": "html",
    ".md": "markdown",
    ".markdown": "markdown",
    ".txt": "text",
    ".csv": "csv",
    **{e: "image" for e in IMAGE_EXT},
}


def kind_for(path: str | Path) -> str:
    ext = Path(path).suffix.lower()
    if ext not in SUPPORTED:
        raise UnsupportedFile(
            f"'{Path(path).name}' is not a supported file type.",
            hint="Supported: " + ", ".join(sorted(SUPPORTED)),
        )
    return SUPPORTED[ext]


def parse(path: Path, ctx: ParseContext, settings: Settings) -> ParsedDocument:
    kind = kind_for(path)
    parser = settings.parser
    if parser == "llamaparse" and kind == "pdf":
        from .llamaparse import parse_llamaparse

        if not settings.has_key("llama_cloud"):
            raise ConfigurationError("LlamaParse is selected but LLAMA_CLOUD_API_KEY is not set.")
        return parse_llamaparse(path, ctx, settings)
    if parser == "docling" and kind in ("pdf", "docx", "pptx", "html"):
        from .docling import parse_docling

        return parse_docling(path, ctx)
    if kind == "pdf":
        from .pdf import parse_pdf

        return parse_pdf(path, ctx)
    if kind == "docx":
        from .office import parse_docx

        return parse_docx(path, ctx)
    if kind == "pptx":
        from .office import parse_pptx

        return parse_pptx(path, ctx)
    if kind == "html":
        from .web import parse_html

        return parse_html(path, ctx)
    if kind in ("markdown", "text", "csv"):
        from .text import parse_text

        return parse_text(path, ctx, kind)
    if kind == "image":
        from .image import parse_image

        return parse_image(path, ctx, settings)
    raise UnsupportedFile(f"No parser for {kind}.")
