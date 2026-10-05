"""HTML parser: readable text, structured tables and embedded (data URI or local) images."""

from __future__ import annotations

import base64
import re
from pathlib import Path

from bs4 import BeautifulSoup

from ..schema import ImageElement, ParsedDocument, TableElement
from .base import ParseContext, rows_to_markdown, tidy
from .sections import section_of, split_sections


def parse_html(path: Path, ctx: ParseContext) -> ParsedDocument:
    soup = BeautifulSoup(path.read_text(errors="replace"), "html.parser")
    for tag in soup(["script", "style", "noscript", "nav", "footer", "header", "form", "svg"]):
        tag.decompose()
    title = (soup.title.string.strip() if soup.title and soup.title.string else "") or path.stem
    result = ParsedDocument(title=title, kind="html", unit="section")
    root = soup.body or soup
    lines: list[str] = []
    tables: list[tuple[int, str, str]] = []
    images: list[tuple[int, bytes, str]] = []

    for el in root.find_all(
        ["h1", "h2", "h3", "h4", "p", "li", "pre", "blockquote", "table", "img", "figcaption"]
    ):
        if el.find_parent("table") is not None and el.name != "table":
            continue
        offset = sum(len(x) + 2 for x in lines)
        if el.name == "table":
            rows = [
                [c.get_text(" ", strip=True) for c in tr.find_all(["th", "td"])] for tr in el.find_all("tr")
            ]
            md = rows_to_markdown(rows)
            caption = el.find("caption")
            if md:
                lines.append(md)
                caption_text = caption.get_text(" ", strip=True) if caption else ""
                tables.append((offset, md, caption_text))
        elif el.name == "img":
            data = _image_bytes(str(el.get("src") or ""), path)
            if data:
                images.append((offset, data, str(el.get("alt") or "")))
        else:
            text = el.get_text(" ", strip=True)
            if not text:
                continue
            if el.name in ("h1", "h2", "h3", "h4"):
                text = "#" * int(el.name[1]) + " " + text
            elif el.name == "li":
                text = f"- {text}"
            lines.append(text)
    markdown = tidy("\n\n".join(lines))
    result.pages = split_sections(markdown)
    for offset, md, table_caption in tables:
        result.tables.append(
            TableElement(page=section_of(offset, result.pages), markdown=md, caption=table_caption)
        )
    for i, (offset, data, alt) in enumerate(images):
        page = section_of(offset, result.pages)
        saved = ctx.save_png(data, f"s{page}_img{i + 1}")
        if saved:
            result.images.append(
                ImageElement(page=page, image_path=saved[0], caption=alt, width=saved[1], height=saved[2])
            )
    if len(images) == 0 and root.find("img"):
        result.warnings.append("Remote images were skipped; only embedded or local images are indexed.")
    ctx.report(1.0, "Parsed")
    return result


def _image_bytes(src: str, html_path: Path) -> bytes | None:
    if src.startswith("data:image"):
        match = re.match(r"data:image/[\w+.-]+;base64,(.*)", src, re.S)
        return base64.b64decode(match.group(1)) if match else None
    if re.match(r"^[a-z]+://", src) or not src:
        return None
    candidate = (html_path.parent / src).resolve()
    if candidate.is_file() and html_path.parent.resolve() in candidate.parents:
        return candidate.read_bytes()
    return None
