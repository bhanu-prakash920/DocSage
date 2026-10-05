"""Document ingestion: parse -> chunk -> describe tables and figures -> entities -> embed -> index."""

from __future__ import annotations

import logging
import shutil
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .. import tasks
from ..concurrency import pmap
from ..config import Settings, get_settings
from ..errors import BudgetExceeded, Cancelled, ConfigurationError, DocSageError
from ..providers import get_chat_model, get_embedding_model
from ..providers.base import UsageMeter, metering
from ..store import get_db, get_vectors
from .chunking import chunk_pages
from .parsers import parse
from .parsers.base import ParseContext
from .schema import ImageElement, ParsedDocument, TableElement

log = logging.getLogger(__name__)

TABLE_TEXT_LIMIT = 8000

ProgressFn = Callable[[float, str, str], None]  # fraction, stage, message


@dataclass
class IngestResult:
    document_id: str
    status: str
    chunks: dict[str, int]
    usage: dict[str, Any]
    warnings: list[str]
    duration_s: float


def asset_dir_for(settings: Settings, collection_id: str, document_id: str) -> Path:
    return settings.data_dir / "assets" / collection_id / document_id


def _fatal(exc: BaseException) -> bool:
    return isinstance(exc, (BudgetExceeded, Cancelled, ConfigurationError)) or (
        isinstance(exc, DocSageError) and any(w in exc.message for w in ("API key", "rejected the API"))
    )


def _describe_all(
    items: list[Any],
    describe: Callable[[Any], str],
    fallback: Callable[[Any], str],
    workers: int,
    warnings: list[str],
    label: str,
    progress: Callable[[int], None] | None = None,
) -> list[str]:
    errors: list[BaseException] = []

    def run(item: Any) -> str:
        try:
            return describe(item)
        except BaseException as exc:
            if _fatal(exc):
                raise
            errors.append(exc)
            return fallback(item)

    out = pmap(run, items, workers=workers, on_done=progress)
    if errors:
        if len(errors) == len(items) and len(items) >= 2:
            raise errors[0]
        warnings.append(
            f"{len(errors)} of {len(items)} {label} could not be described by the model "
            f"and use caption text instead ({errors[0]})."
        )
    return out


def ingest_document(
    document_id: str,
    *,
    progress: ProgressFn | None = None,
    is_cancelled: Callable[[], bool] | None = None,
    settings: Settings | None = None,
) -> IngestResult:
    settings = settings or get_settings()
    db, vectors = get_db(), get_vectors()
    doc = db.get_document(document_id)
    collection = db.get_collection(doc["collection_id"])
    cid = collection["id"]
    started = time.monotonic()
    meter = UsageMeter(budget_usd=settings.max_ingest_cost_usd or None, label="ingestion")
    warnings: list[str] = []

    def report(fraction: float, stage: str, message: str) -> None:
        if is_cancelled and is_cancelled():
            raise Cancelled("Ingestion was cancelled.")
        if progress:
            progress(fraction, stage, message)

    db.update_document(
        document_id, status="processing", error=None, parser=settings.parser, chunking=settings.chunking
    )
    asset_dir = asset_dir_for(settings, cid, document_id)
    try:
        with metering(meter):
            # Fresh asset folder per run: stale figures from earlier runs never leak in (fixes C1/C2).
            shutil.rmtree(asset_dir, ignore_errors=True)
            embedder = get_embedding_model(
                {
                    "provider": collection["embedding_provider"],
                    "model": collection["embedding_model"],
                    "dim": collection["embedding_dim"],
                },
                settings,
            )
            llm = get_chat_model("fast", settings)

            report(0.02, "parsing", "Reading the document")
            ctx = ParseContext(
                data_dir=settings.data_dir,
                asset_dir=asset_dir,
                min_image_px=settings.min_image_px,
                progress=lambda f, m: report(0.02 + f * 0.23, "parsing", m),
                is_cancelled=is_cancelled,
            )
            parsed: ParsedDocument = parse(Path(doc["path"]), ctx, settings)
            warnings.extend(parsed.warnings)
            if not settings.summarize_images and not settings.summarize_tables:
                pass
            if settings.ocr_tables and parsed.kind == "pdf":
                from .parsers.image import ocr_tables

                for img in [i for i in parsed.images if i.kind == "scan"]:
                    parsed.tables.extend(ocr_tables(settings.data_dir / img.image_path, img.page))

            report(0.25, "chunking", f"Chunking {parsed.page_count} {parsed.unit}s ({settings.chunking})")
            drafts = chunk_pages(
                settings.chunking,
                parsed.pages,
                size=settings.chunk_size,
                overlap=settings.chunk_overlap,
                llm=llm,
                embedder=embedder,
                progress=lambda f: report(0.25 + f * 0.25, "chunking", f"Chunking ({settings.chunking})"),
            )

            # --- tables ------------------------------------------------------------------------
            tables = parsed.tables
            report(0.5, "tables", f"Summarising {len(tables)} tables")

            def table_fallback(t: TableElement) -> str:
                return tasks.describe_table(
                    get_chat_model("fast", _offline(settings)), t.markdown, caption=t.caption
                )

            if settings.summarize_tables and llm.generative:
                table_desc = _describe_all(
                    tables,
                    lambda t: tasks.describe_table(llm, t.markdown, caption=t.caption),
                    table_fallback,
                    settings.llm_concurrency,
                    warnings,
                    "tables",
                    lambda d: report(
                        0.5 + 0.15 * d / max(1, len(tables)),
                        "tables",
                        f"Summarised {d} of {len(tables)} tables",
                    ),
                )
            else:
                table_desc = [table_fallback(t) for t in tables]

            # --- figures -----------------------------------------------------------------------
            images = parsed.images
            report(0.65, "figures", f"Describing {len(images)} figures")

            def image_fallback(img: ImageElement) -> str:
                return tasks.describe_image(
                    get_chat_model("fast", _offline(settings)),
                    b"",
                    caption=img.caption,
                    context=img.context,
                    page=img.page,
                    kind=img.kind,
                )

            def image_describe(img: ImageElement) -> str:
                data = (settings.data_dir / img.image_path).read_bytes()
                return tasks.describe_image(
                    llm, data, caption=img.caption, context=img.context, page=img.page, kind=img.kind
                )

            if settings.summarize_images and llm.generative and llm.supports_vision:
                image_desc = _describe_all(
                    images,
                    image_describe,
                    image_fallback,
                    settings.llm_concurrency,
                    warnings,
                    "figures",
                    lambda d: report(
                        0.65 + 0.17 * d / max(1, len(images)),
                        "figures",
                        f"Described {d} of {len(images)} figures",
                    ),
                )
            else:
                image_desc = [image_fallback(i) for i in images]

            # --- records -------------------------------------------------------------------------
            unit = parsed.unit
            parents = [
                {"id": f"{document_id}:{unit[0]}{p.number}", "page": p.number, "text": p.text}
                for p in parsed.pages
                if p.text.strip()
            ]
            parent_ids = {p["page"]: p["id"] for p in parents}
            records: list[dict[str, Any]] = []
            for d in drafts:
                records.append(
                    {
                        "modality": "text",
                        "page": d.page,
                        "title": d.title,
                        "text": d.text,
                        "parent_id": parent_ids.get(d.page),
                    }
                )
            for t, desc in zip(tables, table_desc, strict=True):
                md = (
                    t.markdown
                    if len(t.markdown) <= TABLE_TEXT_LIMIT
                    else t.markdown[:TABLE_TEXT_LIMIT] + "\n…"
                )
                records.append(
                    {
                        "modality": "table",
                        "page": t.page,
                        "title": t.caption or None,
                        "text": f"{desc}\n\n{md}".strip(),
                        "asset_path": t.image_path,
                        "bbox": t.bbox,
                        "meta": {"markdown_chars": len(t.markdown)},
                    }
                )
            for img, desc in zip(images, image_desc, strict=True):
                records.append(
                    {
                        "modality": "image",
                        "page": img.page,
                        "title": img.caption or None,
                        "text": desc,
                        "asset_path": img.image_path,
                        "bbox": img.bbox,
                        "meta": {"kind": img.kind, "width": img.width, "height": img.height},
                    }
                )
            for i, r in enumerate(records):
                r["id"] = f"{document_id}:{i}"
                r["ordinal"] = i
                r.setdefault("meta", {})
                r["meta"]["unit"] = unit

            # --- entities (GraphRAG) -----------------------------------------------------------
            entities: list[tuple[str, str]] = []
            if settings.graph_rag and records:
                report(0.82, "entities", "Extracting entities")
                batches = [records[i : i + 8] for i in range(0, len(records), 8)]
                results = pmap(
                    lambda b: tasks.extract_entities(llm, [r["text"] for r in b]),
                    batches,
                    workers=settings.llm_concurrency,
                )
                for batch, ents in zip(batches, results, strict=True):
                    for r, names in zip(batch, ents, strict=False):
                        entities.extend((e, r["id"]) for e in names if e)

            # --- embed & index -------------------------------------------------------------------
            report(0.86, "embedding", f"Embedding {len(records)} chunks with {embedder.label}")
            title = parsed.title
            embed_texts = [
                f"{title} | {unit} {r['page']}"
                + (f" | {r['title']}" if r.get("title") else "")
                + f"\n{r['text']}"
                for r in records
            ]
            vecs = embedder.embed_documents(embed_texts) if records else []
            report(0.96, "indexing", "Writing to the index")
            vectors.delete_document(cid, document_id)
            vectors.upsert(
                cid,
                [r["id"] for r in records],
                vecs,
                [r["text"][:2000] for r in records],
                [
                    {"document_id": document_id, "modality": r["modality"], "page": int(r["page"])}
                    for r in records
                ],
            )
            db.replace_document_content(document_id, cid, parents, records, entities)

        counts = {m: sum(1 for r in records if r["modality"] == m) for m in ("text", "table", "image")}
        duration = round(time.monotonic() - started, 2)
        usage = meter.snapshot()
        db.update_document(
            document_id,
            status="ready",
            error=None,
            pages=parsed.page_count,
            n_text=counts["text"],
            n_table=counts["table"],
            n_image=counts["image"],
            usage={
                **usage,
                "warnings": warnings,
                "title": parsed.title,
                "unit": unit,
                "llm": llm.label,
                "embedding": embedder.label,
            },
            cost_usd=usage["cost_usd"],
            duration_s=duration,
        )
        db.bump_version(cid)
        report(1.0, "done", f"Indexed {len(records)} chunks")
        return IngestResult(document_id, "ready", counts, usage, warnings, duration)
    except BaseException as exc:
        usage = meter.snapshot()
        status = "cancelled" if isinstance(exc, Cancelled) else "failed"
        message = exc.message if isinstance(exc, DocSageError) else f"Unexpected error: {exc}"
        if not isinstance(exc, DocSageError):
            log.exception("ingestion failed for %s", document_id)
        try:
            vectors.delete_document(cid, document_id)
            db.clear_document_content(document_id)
        except Exception:  # best effort cleanup
            log.warning("cleanup after failed ingestion did not complete", exc_info=True)
        db.update_document(
            document_id,
            status=status,
            error=message,
            usage={**usage, "warnings": warnings},
            cost_usd=usage["cost_usd"],
            duration_s=round(time.monotonic() - started, 2),
        )
        db.bump_version(cid)
        raise


def _offline(settings: Settings) -> Settings:
    return settings.model_copy(update={"llm_provider": "offline"})
