from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class Context:
    """One numbered source handed to the answering model and shown to the user."""

    n: int
    chunk_id: str | None
    document_id: str | None
    filename: str
    page: int | None
    modality: str  # text | table | image | web
    text: str
    score: float = 0.0
    title: str | None = None
    asset_path: str | None = None
    url: str | None = None
    signals: dict[str, Any] = field(default_factory=dict)
    cited: bool = False

    def label(self) -> str:
        if self.modality == "web":
            return f"web: {self.title or self.url}"
        loc = f"p.{self.page}" if self.page else ""
        return f"{self.filename} {loc} ({self.modality})".strip()

    def as_prompt(self, max_chars: int = 4000) -> str:
        body = self.text if len(self.text) <= max_chars else self.text[:max_chars] + " …"
        attrs = f'id="{self.n}" type="{self.modality}" source="{self.label()}"'
        return f"<source {attrs}>\n{body}\n</source>"

    def public(self, preview_chars: int = 1600) -> dict[str, Any]:
        data = asdict(self)
        data["text"] = self.text[:preview_chars]
        data["truncated"] = len(self.text) > preview_chars
        return data


@dataclass
class Verdict:
    relevant: bool
    sufficient: bool
    missing: str = ""
    useful_sources: list[int] = field(default_factory=list)
    method: str = "llm"
