"""Docling (local, layout-aware) parser. Optional: install the 'docling' extra."""

from __future__ import annotations

from pathlib import Path

from ...errors import ConfigurationError
from ..schema import ImageElement, PageText, ParsedDocument, TableElement
from .base import ParseContext, tidy


def parse_docling(path: Path, ctx: ParseContext) -> ParsedDocument:
    try:
        from docling.datamodel.base_models import InputFormat
        from docling.datamodel.pipeline_options import PdfPipelineOptions
        from docling.document_converter import DocumentConverter, PdfFormatOption
    except ImportError as exc:
        raise ConfigurationError("Docling needs the 'docling' extra: uv sync --extra docling") from exc

    options = PdfPipelineOptions(generate_picture_images=True, images_scale=2.0)
    converter = DocumentConverter(format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=options)})
    ctx.report(0.05, "Running Docling layout analysis")
    doc = converter.convert(str(path)).document
    pages = sorted(doc.pages.keys()) if doc.pages else [1]
    result = ParsedDocument(title=doc.name or path.stem, kind=path.suffix.lstrip(".").lower())
    result.pages = [PageText(p, tidy(doc.export_to_markdown(page_no=p))) for p in pages]
    for i, table in enumerate(doc.tables):
        page = table.prov[0].page_no if table.prov else 1
        result.tables.append(
            TableElement(
                page=page, markdown=table.export_to_markdown(doc=doc), caption=table.caption_text(doc)
            )
        )
        ctx.report(0.5, f"Table {i + 1}")
    for i, picture in enumerate(doc.pictures):
        image = picture.get_image(doc)
        if image is None:
            continue
        page = picture.prov[0].page_no if picture.prov else 1
        saved = ctx.save_png(image, f"p{page}_img{i + 1}")
        if saved:
            result.images.append(
                ImageElement(
                    page=page,
                    image_path=saved[0],
                    caption=picture.caption_text(doc),
                    width=saved[1],
                    height=saved[2],
                )
            )
    ctx.report(1.0, "Parsed")
    return result
