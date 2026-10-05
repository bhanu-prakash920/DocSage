"""Split long, page-less documents into numbered sections so citations stay precise."""

from __future__ import annotations

import re

from ..schema import PageText

SECTION_CHARS = 3500


def split_sections(markdown: str, target: int = SECTION_CHARS) -> list[PageText]:
    blocks = re.split(r"\n(?=#{1,2} )", markdown)
    sections: list[str] = []
    current = ""
    for block in blocks:
        if current and len(current) + len(block) > target:
            sections.append(current)
            current = ""
        while len(block) > target * 1.5:
            cut = block.rfind("\n\n", 0, target)
            cut = cut if cut > target // 3 else target
            sections.append((current + block[:cut]).strip())
            current, block = "", block[cut:]
        current = f"{current}\n{block}" if current else block
    if current.strip():
        sections.append(current)
    return [PageText(i + 1, s.strip()) for i, s in enumerate(sections) if s.strip()] or [PageText(1, "")]


def section_of(offset: int, sections: list[PageText]) -> int:
    """Section number containing the character offset in the joined markdown."""
    total = 0
    for s in sections:
        total += len(s.text) + 1
        if offset < total:
            return s.number
    return sections[-1].number if sections else 1
