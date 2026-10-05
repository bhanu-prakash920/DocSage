"""Evaluation harness: run a golden set against a matrix of retrieval/answering configurations."""

from __future__ import annotations

import logging
import shutil
import statistics
import time
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

from .. import tasks
from ..agent.service import ask
from ..config import get_settings
from ..errors import Cancelled, DocSageError
from ..providers import get_chat_model
from ..providers.base import UsageMeter, metering
from ..retrieval.types import Context
from ..store import get_db, get_vectors
from ..store.db import now

log = logging.getLogger(__name__)

PRESETS: dict[str, dict[str, Any]] = {
    "dense": {"mode": "simple", "hybrid": False, "rerank": "none", "parent_expansion": False},
    "hybrid": {"mode": "simple", "hybrid": True, "rerank": "none", "parent_expansion": True},
    "hybrid+rerank": {"mode": "simple", "hybrid": True, "rerank": "llm", "parent_expansion": True},
    "agentic": {"mode": "agentic", "hybrid": True, "rerank": "none", "parent_expansion": True},
}


def resolve_configs(names: list[str], chunking: Sequence[str] | None = None) -> list[dict[str, Any]]:
    configs = []
    strategies: list[str | None] = list(chunking) if chunking else [None]
    for chunk in strategies:
        for name in names:
            if name not in PRESETS:
                raise DocSageError(f"Unknown evaluation preset '{name}'. Choose from {', '.join(PRESETS)}.")
            label = name if chunk is None else f"{chunk} / {name}"
            configs.append({"name": label, "preset": name, "chunking": chunk, **PRESETS[name]})
    return configs


def _match(source: dict[str, Any], item: dict[str, Any]) -> bool:
    expected = item.get("expected_document")
    if not expected or source.get("modality") == "web":
        return False
    if source.get("filename") != expected and source.get("document_id") != expected:
        return False
    page = item.get("expected_page")
    return page is None or source.get("page") == page


def _shadow_collection(
    base: dict[str, Any],
    strategy: str,
    run_id: str,
    progress: Callable[[str], None],
    is_cancelled: Callable[[], bool],
) -> str:
    """Re-ingest the base collection's documents with another chunking strategy."""
    from ..ingest.pipeline import ingest_document

    db = get_db()
    settings = get_settings()
    shadow = db.create_collection(
        f"eval {run_id} {strategy}",
        "evaluation shadow collection",
        {
            "provider": base["embedding_provider"],
            "model": base["embedding_model"],
            "dim": base["embedding_dim"],
        },
        hidden=True,
    )
    docs = [d for d in db.list_documents(base["id"]) if d["status"] == "ready"]
    for i, d in enumerate(docs):
        progress(f"Re-ingesting {d['filename']} with {strategy} chunking ({i + 1}/{len(docs)})")
        copy = db.create_document(
            collection_id=shadow["id"],
            filename=d["filename"],
            path=d["path"],
            sha256=d["sha256"],
            size_bytes=d["size_bytes"],
            kind=d["kind"],
            status="queued",
        )
        ingest_document(
            copy["id"], is_cancelled=is_cancelled, settings=settings.model_copy(update={"chunking": strategy})
        )
    return shadow["id"]


def drop_collection(cid: str) -> None:
    settings = get_settings()
    get_vectors().drop(cid)
    get_db().delete_collection(cid)
    shutil.rmtree(Path(settings.data_dir) / "assets" / cid, ignore_errors=True)


def run_eval(
    run_id: str,
    progress: Callable[[float, str, str], None] | None = None,
    is_cancelled: Callable[[], bool] | None = None,
) -> dict[str, Any]:
    db = get_db()
    run = db.get_eval_run(run_id)
    cid = run["collection_id"]
    base = db.get_collection(cid)
    items = db.list_eval_items(cid)
    configs = run["configs"]
    if not items:
        raise DocSageError("Add at least one evaluation question first.")
    db.update_eval_run(run_id, status="running")
    judge_llm = get_chat_model("fast")
    meter = UsageMeter(label="evaluation")
    per_item: list[dict[str, Any]] = []
    shadows: dict[str, str] = {}
    total = len(items) * len(configs)
    done = 0

    def report(message: str) -> None:
        if is_cancelled and is_cancelled():
            raise Cancelled("Evaluation was cancelled.")
        if progress:
            progress(done / max(1, total), "evaluating", message)

    try:
        for config in configs:
            target = cid
            if config.get("chunking"):
                strategy = config["chunking"]
                if strategy not in shadows:
                    shadows[strategy] = _shadow_collection(
                        base, strategy, run_id, report, is_cancelled or (lambda: False)
                    )
                target = shadows[strategy]
            for item in items:
                report(f"{config['name']}: {item['question'][:60]}")
                t0 = time.monotonic()
                result = ask(
                    target,
                    item["question"],
                    mode=config["mode"],
                    hybrid=config["hybrid"],
                    rerank=config["rerank"],
                    parent_expansion=config["parent_expansion"],
                    web_search=False,
                    persist=False,
                )
                latency = round((time.monotonic() - t0) * 1000)
                sources = result.get("sources", [])
                ranks = [i + 1 for i, s in enumerate(sources) if _match(s, item)]
                answer = result.get("answer", "") if result.get("type") == "done" else ""
                contexts = [
                    Context(**{k: v for k, v in s.items() if k in Context.__dataclass_fields__})
                    for s in sources
                ]
                with metering(meter):
                    scores = (
                        tasks.judge(judge_llm, item["question"], answer, contexts, item["reference_answer"])
                        if answer
                        else {
                            "faithfulness": 0.0,
                            "relevance": 0.0,
                            "correctness": 0.0,
                            "notes": result.get("message", "no answer"),
                            "method": "none",
                        }
                    )
                usage = result.get("usage") or {}
                per_item.append(
                    {
                        "config": config["name"],
                        "item_id": item["id"],
                        "question": item["question"],
                        "answer": answer,
                        "error": result.get("message") if result.get("type") == "error" else None,
                        "hit": bool(ranks) if item.get("expected_document") else None,
                        "rr": (1 / ranks[0] if ranks else 0.0) if item.get("expected_document") else None,
                        "latency_ms": latency,
                        "cost_usd": usage.get("cost_usd", 0.0),
                        **scores,
                    }
                )
                done += 1
    finally:
        for shadow in shadows.values():
            drop_collection(shadow)

    summary = []
    for config in configs:
        rows = [r for r in per_item if r["config"] == config["name"]]

        def mean(key: str, rows: list[dict[str, Any]] = rows) -> float | None:
            vals = [r[key] for r in rows if r.get(key) is not None]
            return round(statistics.fmean(float(v) for v in vals), 4) if vals else None

        summary.append(
            {
                "config": config["name"],
                "hit_rate": mean("hit"),
                "mrr": mean("rr"),
                "faithfulness": mean("faithfulness"),
                "relevance": mean("relevance"),
                "correctness": mean("correctness"),
                "latency_ms": mean("latency_ms"),
                "cost_usd": round(sum(r["cost_usd"] or 0 for r in rows), 5),
                "errors": sum(1 for r in rows if r["error"]),
            }
        )
    total_cost = round(sum(s["cost_usd"] for s in summary) + meter.cost_usd, 5)
    results = {
        "summary": summary,
        "items": per_item,
        "judge": judge_llm.label,
        "judge_cost_usd": round(meter.cost_usd, 5),
        "n_items": len(items),
    }
    db.update_eval_run(run_id, status="succeeded", results=results, cost_usd=total_cost, finished_at=now())
    return {"run_id": run_id, "summary": summary}


def submit_eval(cid: str, configs: list[dict[str, Any]]) -> dict[str, Any]:
    from ..jobs import get_runner

    db = get_db()
    job = db.create_job(cid, "eval")
    run = db.create_eval_run(cid, configs, job["id"])

    def work(progress, is_cancelled):
        try:
            return run_eval(run["id"], progress, is_cancelled)
        except BaseException as exc:
            message = exc.message if isinstance(exc, DocSageError) else str(exc)
            db.update_eval_run(
                run["id"],
                status="cancelled" if isinstance(exc, Cancelled) else "failed",
                error=message,
                finished_at=now(),
            )
            raise

    get_runner().submit(job, work)
    return db.get_eval_run(run["id"])


def summary_markdown(summary: list[dict[str, Any]], title: str) -> str:
    def fmt(v: Any, pct: bool = False) -> str:
        if v is None:
            return "–"
        return f"{v * 100:.0f}%" if pct else (f"{v:.2f}" if isinstance(v, float) else str(v))

    lines = [
        f"### {title}",
        "",
        "| Configuration | Hit rate | MRR | Faithfulness | Relevance | Correctness | "
        "Latency (ms) | Cost (USD) |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for s in summary:
        lines.append(
            f"| {s['config']} | {fmt(s['hit_rate'], True)} | {fmt(s['mrr'])} | {fmt(s['faithfulness'])} | "
            f"{fmt(s['relevance'])} | {fmt(s['correctness'])} | {fmt(s['latency_ms'] and round(s['latency_ms']))}"
            f" | {s['cost_usd']:.4f} |"
        )
    return "\n".join(lines)
