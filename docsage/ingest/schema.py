"""Intermediate structures produced by parsers and consumed by the ingestion pipeline."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

BBox = tuple[float, float, float, float]


@dataclass
class PageText:
    number: int  # 1-based page, slide or section number
    text: str


@dataclass
class TableElement:
    page: int
    markdown: str
    caption: str = ""
    bbox: BBox | None = None
    image_path: str | None = None  # relative to the data directory


@dataclass
class ImageElement:
    page: int
    image_path: str  # relative to the data directory
    caption: str = ""
    context: str = ""
    bbox: BBox | None = None
    width: int = 0
    height: int = 0
    kind: Literal["raster", "vector", "scan", "image-file"] = "raster"


@dataclass
class ParsedDocument:
    title: str
    kind: str
    unit: Literal["page", "slide", "section"] = "page"
    pages: list[PageText] = field(default_factory=list)
    tables: list[TableElement] = field(default_factory=list)
    images: list[ImageElement] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def page_count(self) -> int:
        return len(self.pages)


@dataclass
class ChunkDraft:
    text: str
    page: int
    title: str | None = None
