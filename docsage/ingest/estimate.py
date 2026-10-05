"""Cheap pre-ingestion estimate of model calls and cost (powers --dry-run and the upload review)."""

from __future__ import annotations

import contextlib
import shutil
import tempfile
from pathlib import Path
from typing import Any

from ..config import Settings
from ..providers.factory import default_embedding_spec, get_chat_model
from ..providers.pricing import cost_of, price_for
from .parsers import kind_for, parse
from .parsers.base import ParseContext

IMAGE_IN, IMAGE_OUT = 1700, 260
TABLE_OUT = 120


def _scan_pdf(path: Path) -> dict[str, int]:
    import pymupdf

    with pymupdf.open(path) as doc:
        pages = doc.page_count
        chars = 0
        images = 0
        text_pages = 0
        for page in doc:
            text = page.get_text("text")
            chars += len(text)
            text_pages += len(text) > 600
            images += sum(1 for img in page.get_images(full=True) if min(img[2], img[3]) >= 96)
        # Tables are counted on a sample to keep the estimate fast on long PDFs.
        sample = list(range(0, pages, max(1, pages // 12)))[:12]
        found = 0
        for i in sample:
            with contextlib.suppress(Exception):
                found += len(doc[i].find_tables().tables)
        tables = round(found * pages / max(1, len(sample)))
    return {"pages": pages, "chars": chars, "images": images, "tables": tables, "dense_pages": text_pages}


def _scan_other(path: Path, settings: Settings) -> dict[str, int]:
    tmp = Path(tempfile.mkdtemp(prefix="docsage-est-"))
    try:
        ctx = ParseContext(data_dir=tmp, asset_dir=tmp / "a", min_image_px=settings.min_image_px)
        parsed = parse(path, ctx, settings.model_copy(update={"parser": "pymupdf"}))
        chars = sum(len(p.text) for p in parsed.pages)
        return {
            "pages": parsed.page_count,
            "chars": chars,
            "images": len(parsed.images),
            "tables": len(parsed.tables),
            "dense_pages": sum(len(p.text) > 600 for p in parsed.pages),
        }
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def estimate_file(path: Path, settings: Settings) -> dict[str, Any]:
    kind = kind_for(path)
    scan = _scan_pdf(path) if kind == "pdf" else _scan_other(path, settings)
    llm = get_chat_model("fast", settings)
    embed = default_embedding_spec(settings)
    calls = 0
    llm_in = llm_out = 0
    if llm.generative:
        if settings.summarize_images and llm.supports_vision:
            calls += scan["images"]
            llm_in += scan["images"] * IMAGE_IN
            llm_out += scan["images"] * IMAGE_OUT
        if settings.summarize_tables:
            calls += scan["tables"]
            llm_in += scan["tables"] * 900
            llm_out += scan["tables"] * TABLE_OUT
        if settings.chunking == "agentic":
            calls += scan["dense_pages"]
            llm_in += scan["chars"] // 4 + scan["dense_pages"] * 350
            llm_out += scan["dense_pages"] * 220
        elif settings.chunking == "proposition":
            props = scan["chars"] // 110
            calls += scan["pages"] + props * 2
            llm_in += scan["chars"] // 2 + props * 900
            llm_out += scan["chars"] // 4 + props * 80
        if settings.graph_rag:
            batches = max(1, scan["chars"] // 9000 + (scan["images"] + scan["tables"]) // 8)
            calls += batches
            llm_in += scan["chars"] // 4 + batches * 300
            llm_out += batches * 400
    embed_tokens = int(scan["chars"] / 4 * (3.2 if settings.chunking == "semantic" else 1.15))
    embed_tokens += scan["images"] * IMAGE_OUT + scan["tables"] * 600
    cost = cost_of(llm.model, llm_in, llm_out) + cost_of(embed.model, embed_tokens, 0)
    priced = (not llm.generative or price_for(llm.model) is not None) and (
        embed.provider in ("local", "hash", "ollama") or price_for(embed.model) is not None
    )
    return {
        **scan,
        "kind": kind,
        "llm_calls": calls,
        "llm_model": llm.label,
        "embedding_model": f"{embed.provider}:{embed.model}",
        "embedding_tokens": embed_tokens,
        "cost_usd": round(cost, 4),
        "cost_low_usd": round(cost * 0.6, 4),
        "cost_high_usd": round(cost * 1.5, 4),
        "priced": priced,
        "chunking": settings.chunking,
        "over_budget": bool(settings.max_ingest_cost_usd and cost * 1.5 > settings.max_ingest_cost_usd),
    }
