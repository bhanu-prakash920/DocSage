"""Markdown, plain text and CSV."""

from __future__ import annotations

import csv
import io
import re
from pathlib import Path

from ..schema import ParsedDocument, TableElement
from .base import ParseContext, rows_to_markdown, tidy
from .sections import section_of, split_sections

_PIPE_TABLE = re.compile(r"(?:^\|.*\|[ \t]*\n){2,}", re.M)


def parse_text(path: Path, ctx: ParseContext, kind: str) -> ParsedDocument:
    raw = path.read_text(errors="replace")
    title = path.stem
    if kind == "csv":
        rows = list(csv.reader(io.StringIO(raw)))
        md = rows_to_markdown(rows[:400])
        result = ParsedDocument(title=title, kind="csv", unit="section")
        result.pages = split_sections(md)
        # Large CSVs are split into table blocks of 40 rows so each block stays retrievable.
        header = rows[:1]
        for i in range(1, min(len(rows), 2001), 40):
            block = rows_to_markdown(header + rows[i : i + 40])
            if block:
                result.tables.append(
                    TableElement(
                        page=1 + i // 400,
                        markdown=block,
                        caption=f"{path.name} rows {i}-{min(len(rows) - 1, i + 39)}",
                    )
                )
        if len(rows) > 2001:
            result.warnings.append("Only the first 2,000 CSV rows were indexed.")
        ctx.report(1.0, "Parsed")
        return result

    text = tidy(raw)
    heading = re.search(r"^#\s+(.+)$", text, re.M)
    if heading:
        title = heading.group(1).strip()
    result = ParsedDocument(title=title, kind=kind, unit="section")
    result.pages = split_sections(text)
    if kind == "markdown":
        for match in _PIPE_TABLE.finditer(text):
            block = match.group(0).strip()
            if re.search(r"^\|\s*:?-{2,}", block, re.M):
                result.tables.append(
                    TableElement(page=section_of(match.start(), result.pages), markdown=block)
                )
    ctx.report(1.0, "Parsed")
    return result
