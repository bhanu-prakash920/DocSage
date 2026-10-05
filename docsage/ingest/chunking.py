"""Chunking strategies.

* ``recursive``   – paragraph/sentence-aware packing with overlap. No model calls; the baseline.
* ``semantic``    – splits where the embedding distance between neighbouring sentences spikes.
* ``agentic``     – one LLM call per page groups numbered units into topical, titled chunks.
                    Roughly 30x cheaper than the proposition chunker.
* ``proposition`` – decomposes text into standalone propositions and allocates them one by one
                    into topical chunks with hash-based ids. The most expensive option by far;
                    kept for benchmarking.

Chunking runs per page so every chunk keeps an exact page (or slide/section) citation.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable

import numpy as np
from pydantic import BaseModel

from ..providers.base import ChatModel, EmbeddingModel, check_budget, user
from ..tasks import prompt, sentences
from .schema import ChunkDraft, PageText

log = logging.getLogger(__name__)

MIN_CHUNK = 120


# ---------------------------------------------------------------------------------------------
# Units
# ---------------------------------------------------------------------------------------------
def units(text: str) -> list[str]:
    """Paragraph-level units; long paragraphs are split into sentences. Tables stay whole."""
    out: list[str] = []
    for block in re.split(r"\n\s*\n", text):
        block = block.strip()
        if not block:
            continue
        if block.startswith("|") or block.startswith("#") or len(block) <= 400:
            out.append(block)
        else:
            out.extend(sentences(block))
    return out


def _split_long(text: str, size: int) -> list[str]:
    if len(text) <= size:
        return [text]
    parts, current = [], ""
    for sent in sentences(text) or [text]:
        while len(sent) > size:  # pathological: no sentence boundaries
            parts.append(sent[:size])
            sent = sent[size:]
        if current and len(current) + len(sent) + 1 > size:
            parts.append(current)
            current = sent
        else:
            current = f"{current} {sent}".strip()
    if current:
        parts.append(current)
    return parts


def recursive_chunks(text: str, page: int, size: int, overlap: int) -> list[ChunkDraft]:
    pieces: list[str] = []
    for u in units(text):
        pieces.extend(_split_long(u, size))
    chunks: list[str] = []
    current: list[str] = []
    length = 0
    for piece in pieces:
        if current and length + len(piece) + 2 > size:
            chunks.append("\n\n".join(current))
            # carry trailing pieces as overlap
            carry: list[str] = []
            carried = 0
            for prev in reversed(current):
                if carried + len(prev) > overlap:
                    break
                carry.insert(0, prev)
                carried += len(prev)
            current, length = carry, carried
        current.append(piece)
        length += len(piece) + 2
    if current:
        chunks.append("\n\n".join(current))
    return _merge_small([ChunkDraft(c, page) for c in chunks if c.strip()], size)


def _merge_small(chunks: list[ChunkDraft], size: int) -> list[ChunkDraft]:
    """Fold chunks shorter than MIN_CHUNK into a neighbour on the same page (backward, else forward)."""
    merged: list[ChunkDraft] = []
    carry: ChunkDraft | None = None
    for c in chunks:
        if carry is not None:
            if carry.page == c.page:
                c = ChunkDraft(carry.text + "\n\n" + c.text, c.page, c.title or carry.title)
            else:
                merged.append(carry)
            carry = None
        if len(c.text) < MIN_CHUNK:
            prev = merged[-1] if merged else None
            if prev and prev.page == c.page and len(prev.text) + len(c.text) < size * 1.25:
                merged[-1] = ChunkDraft(prev.text + "\n\n" + c.text, c.page, prev.title)
            else:
                carry = c
            continue
        merged.append(c)
    if carry is not None:
        merged.append(carry)
    return merged


# ---------------------------------------------------------------------------------------------
# Semantic
# ---------------------------------------------------------------------------------------------
def semantic_chunks(
    pages: list[PageText], embedder: EmbeddingModel, size: int, percentile: float = 88.0
) -> list[ChunkDraft]:
    per_page_units = [(p.number, units(p.text)) for p in pages]
    flat = [(page, u) for page, us in per_page_units for u in us]
    if not flat:
        return []
    # Embed each unit with its neighbours for a smoother signal (the "buffer" trick).
    windows = [" ".join(u for _, u in flat[max(0, i - 1) : i + 2]) for i in range(len(flat))]
    vectors = np.asarray(embedder.embed_documents(windows), dtype=np.float32)
    distances = 1 - np.sum(vectors[:-1] * vectors[1:], axis=1) if len(flat) > 1 else np.array([])
    threshold = float(np.percentile(distances, percentile)) if distances.size else 1.0

    out: list[ChunkDraft] = []
    current: list[str] = []
    current_page = flat[0][0]
    for i, (page, unit) in enumerate(flat):
        boundary = i > 0 and (distances[i - 1] > threshold or page != current_page)
        too_big = sum(len(x) for x in current) + len(unit) > size
        if current and (boundary or too_big):
            out.extend(ChunkDraft(t, current_page) for t in _split_long("\n\n".join(current), size))
            current = []
        current_page = page
        current.append(unit)
    if current:
        out.extend(ChunkDraft(t, current_page) for t in _split_long("\n\n".join(current), size))
    return _merge_small(out, size)


# ---------------------------------------------------------------------------------------------
# Agentic (two-stage: LLM outline per page, deterministic assembly)
# ---------------------------------------------------------------------------------------------
class _Group(BaseModel):
    title: str
    start: int
    end: int


class _Groups(BaseModel):
    groups: list[_Group]


def _normalise_groups(groups: list[_Group], n: int) -> list[_Group]:
    """Repair overlaps and gaps so every unit lands in exactly one contiguous group."""
    cleaned: list[_Group] = []
    cursor = 1
    for g in sorted(groups, key=lambda g: g.start):
        start, end = max(g.start, cursor), min(g.end, n)
        if end < start:
            continue
        if start > cursor:  # gap -> attach to previous group, or open one
            if cleaned:
                cleaned[-1].end = start - 1
            else:
                cleaned.append(_Group(title=g.title, start=cursor, end=start - 1))
        cleaned.append(_Group(title=g.title.strip()[:80], start=start, end=end))
        cursor = end + 1
    if cursor <= n:
        if cleaned:
            cleaned[-1].end = n
        else:
            cleaned.append(_Group(title="", start=1, end=n))
    return cleaned


def agentic_chunks(
    pages: list[PageText],
    llm: ChatModel,
    size: int,
    overlap: int,
    progress: Callable[[float], None] | None = None,
) -> list[ChunkDraft]:
    out: list[ChunkDraft] = []
    for idx, page in enumerate(pages):
        if progress:
            progress(idx / max(1, len(pages)))
        us = units(page.text)
        if not us:
            continue
        if len(page.text) <= size * 0.6 or not llm.generative:
            out.extend(recursive_chunks(page.text, page.number, size, overlap))
            continue
        groups: list[_Group] = []
        for offset in range(0, len(us), 60):  # keep each call bounded
            window = us[offset : offset + 60]
            numbered = "\n".join(f"[{i + 1}] {u[:600]}" for i, u in enumerate(window))
            check_budget()
            try:
                result = llm.generate_json(
                    [user(numbered)], _Groups, system=prompt("agentic_chunk"), max_tokens=2048
                )
                part = _normalise_groups(result.groups, len(window))
            except Exception as exc:  # degrade gracefully for this window
                log.warning("agentic chunking failed on page %s: %s", page.number, exc)
                part = [_Group(title="", start=1, end=len(window))]
            groups.extend(_Group(title=g.title, start=g.start + offset, end=g.end + offset) for g in part)
        for g in groups:
            body = "\n\n".join(us[g.start - 1 : g.end])
            for piece in _split_long(body, size) if len(body) > size else [body]:
                out.append(ChunkDraft(piece, page.number, g.title or None))
    return _merge_small(out, size)


# ---------------------------------------------------------------------------------------------
# Proposition chunking
# ---------------------------------------------------------------------------------------------
class _Props(BaseModel):
    propositions: list[str]


class _Alloc(BaseModel):
    chunk_id: int


class _Meta(BaseModel):
    title: str
    summary: str


def proposition_chunks(
    pages: list[PageText],
    llm: ChatModel,
    size: int,
    overlap: int,
    progress: Callable[[float], None] | None = None,
) -> list[ChunkDraft]:
    if not llm.generative:
        return [c for p in pages for c in recursive_chunks(p.text, p.number, size, overlap)]
    out: list[ChunkDraft] = []
    for idx, page in enumerate(pages):
        if progress:
            progress(idx / max(1, len(pages)))
        if not page.text.strip():
            continue
        # Fresh state for every page/document: fixes the chunk leak between documents (C3).
        chunks: dict[int, dict] = {}
        props = llm.generate_json(
            [user(page.text[:12000])], _Props, system=prompt("propositions"), max_tokens=8192
        ).propositions
        for prop in props:
            check_budget()
            if chunks:
                summaries = "\n".join(f"{cid}: {c['summary']}" for cid, c in chunks.items())
                choice = llm.generate_json(
                    [user(f"Proposition: {prop}\n\nChunks:\n{summaries}")],
                    _Alloc,
                    system=prompt("allocate"),
                    max_tokens=256,
                ).chunk_id
            else:
                choice = -1
            if choice not in chunks:  # ids are assigned by code, never invented by the model (C4)
                choice = len(chunks) + 1
                chunks[choice] = {"props": [], "title": "", "summary": ""}
            chunks[choice]["props"].append(prop)
            meta = llm.generate_json(
                [user("\n".join(chunks[choice]["props"]))],
                _Meta,
                system=prompt("chunk_summary"),
                max_tokens=512,
            )
            chunks[choice].update(title=meta.title, summary=meta.summary)
        for c in chunks.values():
            text = " ".join(c["props"])
            for piece in _split_long(text, size):
                out.append(ChunkDraft(piece, page.number, c["title"] or None))
    return out


def chunk_pages(
    strategy: str,
    pages: list[PageText],
    *,
    size: int,
    overlap: int,
    llm: ChatModel,
    embedder: EmbeddingModel,
    progress: Callable[[float], None] | None = None,
) -> list[ChunkDraft]:
    if strategy == "semantic":
        return semantic_chunks(pages, embedder, size)
    if strategy == "agentic":
        return agentic_chunks(pages, llm, size, overlap, progress)
    if strategy == "proposition":
        return proposition_chunks(pages, llm, size, overlap, progress)
    return [c for p in pages for c in recursive_chunks(p.text, p.number, size, overlap)]
