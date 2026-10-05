"""LlamaParse (cloud) for text, with local PyMuPDF extraction for tables and figures.
"""

from __future__ import annotations

from pathlib import Path

from ...config import Settings
from ...errors import ConfigurationError, ProviderError
from ..schema import PageText, ParsedDocument
from .base import ParseContext, tidy
from .pdf import parse_pdf


def parse_llamaparse(path: Path, ctx: ParseContext, settings: Settings) -> ParsedDocument:
    try:
        from llama_parse import LlamaParse
    except ImportError as exc:
        raise ConfigurationError(
            "LlamaParse needs the 'llamaparse' extra: uv sync --extra llamaparse"
        ) from exc
    local = parse_pdf(path, ctx)
    try:
        parser = LlamaParse(api_key=settings.key("llama_cloud"), result_type="markdown", verbose=False)
        pages = parser.load_data(str(path))
    except Exception as exc:
        raise ProviderError(f"LlamaParse failed: {exc}") from exc
    if len(pages) == local.page_count:
        local.pages = [PageText(i + 1, tidy(p.text)) for i, p in enumerate(pages)]
    else:
        local.warnings.append("LlamaParse page count differed from the PDF; using local text extraction.")
    return local
