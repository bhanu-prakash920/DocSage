"""Command-line interface: ``docsage serve | ingest | ask | eval | samples | collections | status``."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from rich.console import Console
from rich.live import Live
from rich.markdown import Markdown
from rich.progress import BarColumn, Progress, TextColumn, TimeElapsedColumn
from rich.table import Table

from . import __version__
from .config import configure, get_settings
from .errors import DocSageError

console = Console()
err = Console(stderr=True)


def _apply_overrides(args: argparse.Namespace) -> None:
    overrides: dict[str, Any] = {}
    for key in ("data_dir", "llm_provider", "parser", "chunking", "embedding_provider"):
        value = getattr(args, key, None)
        if value:
            overrides[key] = value
    if overrides:
        configure(**overrides)
    from .logging_setup import configure_logging

    configure_logging("DEBUG" if getattr(args, "verbose", False) else "WARNING")


def cmd_status(args: argparse.Namespace) -> int:
    from .providers.factory import provider_status

    st = provider_status()
    table = Table(title=f"DocSage {__version__}", show_header=False)
    table.add_row("Answer model", f"{st['llm_provider']}: {st['llm_model']}")
    table.add_row("Fast model", f"{st['llm_provider']}: {st['llm_fast_model']}")
    table.add_row(
        "Embeddings", f"{st['embedding']['provider']}: {st['embedding']['model']} ({st['embedding']['dim']}d)"
    )
    table.add_row("Web search", "available" if st["web_search_available"] else "off (no TAVILY_API_KEY)")
    table.add_row("Keys", ", ".join(k for k, v in st["keys"].items() if v) or "none")
    table.add_row("Data directory", str(get_settings().data_dir.resolve()))
    console.print(table)
    if st["error"]:
        err.print(f"[red]Problem:[/red] {st['error']}")
        return 1
    if not st["generative"]:
        console.print(
            "[yellow]Offline mode:[/yellow] answers are extracted passages. Add an API key for generated answers."
        )
    return 0


def _collection(name: str | None) -> dict[str, Any]:
    from . import library

    if name:
        try:
            return library.find_collection(name)
        except DocSageError:
            return library.create_collection(name)
    return library.ensure_default_collection()


def cmd_ingest(args: argparse.Namespace) -> int:
    from . import library
    from .ingest.estimate import estimate_file
    from .ingest.pipeline import ingest_document
    from .store import get_db

    root = Path(args.path).expanduser()
    if not root.exists():
        err.print(f"[red]{root} does not exist.[/red]")
        return 2
    files = library.iter_supported(root)
    if not files:
        err.print("[red]No supported files found.[/red]")
        return 2
    settings = get_settings()
    if args.dry_run:
        table = Table("File", "Pages", "Figures", "Tables", "Model calls", "Est. cost (USD)")
        total = 0.0
        for f in files:
            e = estimate_file(f, settings)
            total += e["cost_usd"]
            table.add_row(
                f.name,
                str(e["pages"]),
                str(e["images"]),
                str(e["tables"]),
                str(e["llm_calls"]),
                f"{e['cost_low_usd']:.3f} – {e['cost_high_usd']:.3f}",
            )
        console.print(table)
        console.print(
            f"Estimated total: about ${total:.3f} with {settings.chunking} chunking. Nothing was ingested."
        )
        return 0
    collection = _collection(args.collection)
    db = get_db()
    failures = 0
    with Progress(
        TextColumn("{task.description}"),
        BarColumn(),
        TextColumn("{task.fields[stage]}"),
        TimeElapsedColumn(),
        console=console,
    ) as progress:
        for f in files:
            doc = library.stage_file(collection["id"], f.name, f.read_bytes(), settings)
            if doc.get("duplicate") and doc["status"] == "ready" and not args.force:
                console.print(f"[dim]Unchanged, skipped:[/dim] {f.name}")
                continue
            task = progress.add_task(f.name[:40], total=1.0, stage="")

            def update(fraction: float, stage: str, message: str, task=task) -> None:
                progress.update(task, completed=fraction, stage=message[:50])

            try:
                result = ingest_document(doc["id"], progress=update)
                c = result.chunks
                progress.update(
                    task,
                    completed=1.0,
                    stage=f"{c['text']} text · {c['table']} tables · {c['image']} figures · "
                    f"${result.usage['cost_usd']:.4f}",
                )
                for w in result.warnings:
                    progress.console.print(f"[yellow]  {f.name}: {w}[/yellow]")
            except DocSageError as exc:
                failures += 1
                progress.update(task, stage=f"[red]failed: {exc.message}[/red]")
    ready = db.scalar(
        "SELECT COUNT(*) FROM documents WHERE collection_id=? AND status='ready'", (collection["id"],)
    )
    console.print(
        f"Collection [bold]{collection['name']}[/bold] ({collection['id']}): {ready} documents ready."
    )
    return 1 if failures else 0


def _ask_once(cid: str, question: str, conversation_id: str | None, mode: str | None) -> str | None:
    from .agent.service import QueryRequest, run_query

    answer, sources, conv, done = "", [], conversation_id, None
    with Live(console=console, refresh_per_second=12, transient=False) as live:
        for event in run_query(
            cid, QueryRequest(question=question, conversation_id=conversation_id, mode=mode)
        ):
            kind = event["type"]
            if kind == "start":
                conv = event["conversation_id"]
            elif kind == "step" and event["status"] == "running":
                live.update(f"[dim]{event['label']}…[/dim]")
            elif kind == "token":
                answer += event["text"]
                live.update(Markdown(answer))
            elif kind == "done":
                sources, done = event["sources"], event
                live.update(Markdown(event["answer"]))
            elif kind == "error":
                live.update(
                    f"[red]{event['message']}[/red]"
                    + (f"\n[dim]{event['hint']}[/dim]" if event.get("hint") else "")
                )
    if done:
        usage = done["usage"]
        console.print(
            f"[dim]{done['latency_ms']} ms · {usage['calls']} model calls · ${usage['cost_usd']:.4f}"
            f"{' · used web search' if done['used_web'] else ''}[/dim]"
        )
    for s in sources:
        if s.get("cited"):
            where = s.get("url") or f"{s['filename']} p.{s['page']} ({s['modality']})"
            console.print(f"  [{s['n']}] {where}", style="cyan")
    return conv


def cmd_ask(args: argparse.Namespace) -> int:
    collection = _collection(args.collection)
    cid = collection["id"]
    if args.question:
        _ask_once(cid, args.question, None, args.mode)
        return 0
    console.print(f"Asking [bold]{collection['name']}[/bold]. Type a question, or 'exit' to quit.")
    conversation = None
    while True:
        try:
            question = console.input("[bold]You:[/bold] ").strip()
        except (EOFError, KeyboardInterrupt):
            console.print()
            return 0
        if question.lower() in ("exit", "quit"):
            return 0
        if not question:
            continue
        try:
            conversation = _ask_once(cid, question, conversation, args.mode)
        except DocSageError as exc:
            err.print(f"[red]{exc.message}[/red]")


def cmd_collections(args: argparse.Namespace) -> int:
    from .store import get_db

    table = Table("ID", "Name", "Documents", "Chunks", "Embeddings")
    for c in get_db().list_collections():
        table.add_row(
            c["id"],
            c["name"],
            f"{c['ready_documents']}/{c['documents']}",
            str(c["chunks"]),
            f"{c['embedding_provider']}:{c['embedding_model']}",
        )
    console.print(table)
    return 0


def cmd_samples(args: argparse.Namespace) -> int:
    from . import library
    from .jobs import JobRunner, set_runner

    set_runner(JobRunner(synchronous=True))
    with console.status("Generating and ingesting the sample collection…"):
        c = library.load_samples()
    console.print(f"Sample collection ready: [bold]{c['name']}[/bold] ({c['id']}).")
    return 0


def cmd_eval(args: argparse.Namespace) -> int:
    from .eval.runner import resolve_configs, run_eval, summary_markdown
    from .store import get_db

    collection = _collection(args.collection)
    db = get_db()
    if args.golden:
        items = json.loads(Path(args.golden).read_text())
        for item in items:
            db.add_eval_item(collection["id"], **item, source="import")
    strategies = args.compare_chunking.split(",") if args.compare_chunking else None
    configs = resolve_configs(args.presets.split(","), strategies)
    job = db.create_job(collection["id"], "eval")
    run = db.create_eval_run(collection["id"], configs, job["id"])
    with Progress(TextColumn("{task.description}"), BarColumn(), TimeElapsedColumn(), console=console) as p:
        task = p.add_task("Evaluating", total=1.0)
        run_eval(run["id"], progress=lambda f, s, m: p.update(task, completed=f, description=m[:70]))
    result = db.get_eval_run(run["id"])
    title = f"{collection['name']} · {datetime.now(UTC):%Y-%m-%d} · judge {result['results']['judge']}"
    md = summary_markdown(result["results"]["summary"], title)
    console.print(Markdown(md))
    if args.out:
        out = Path(args.out)
        out.mkdir(parents=True, exist_ok=True)
        path = out / f"{collection['id']}-{datetime.now(UTC):%Y%m%d-%H%M%S}.md"
        path.write_text(md + "\n")
        (path.with_suffix(".json")).write_text(json.dumps(result["results"], indent=2, default=str))
        console.print(f"Saved {path}")
    return 0


def cmd_serve(args: argparse.Namespace) -> int:
    import uvicorn

    s = get_settings()
    host, port = args.host or s.host, args.port or s.port
    console.print(f"DocSage {__version__} on http://{host}:{port}  (API docs at /api/docs)")
    uvicorn.run("docsage.api.app:app", host=host, port=port, reload=args.reload, log_level="warning")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="docsage", description="Multimodal agentic document intelligence.")
    p.add_argument("--version", action="version", version=f"docsage {__version__}")
    p.add_argument(
        "--data-dir", dest="data_dir", help="Where indexes and uploads are stored (default ./data)."
    )
    p.add_argument(
        "--llm-provider",
        dest="llm_provider",
        choices=["auto", "anthropic", "google", "openai", "ollama", "offline"],
    )
    p.add_argument("-v", "--verbose", action="store_true")
    sub = p.add_subparsers(dest="command", required=True)

    s = sub.add_parser("serve", help="Run the API and web interface.")
    s.add_argument("--host")
    s.add_argument("--port", type=int)
    s.add_argument("--reload", action="store_true")
    s.set_defaults(fn=cmd_serve)

    s = sub.add_parser("ingest", help="Ingest a file or a folder of files.")
    s.add_argument("path")
    s.add_argument("-c", "--collection", help="Collection name or id (created if missing).")
    s.add_argument("--parser", choices=["pymupdf", "pymupdf4llm", "llamaparse", "LlamaParse", "docling"])
    s.add_argument("--chunking", choices=["recursive", "semantic", "agentic", "proposition"])
    s.add_argument("--dry-run", action="store_true", help="Estimate model calls and cost without ingesting.")
    s.add_argument("--force", action="store_true", help="Re-ingest files that are unchanged.")
    s.set_defaults(fn=cmd_ingest)

    s = sub.add_parser("ask", help="Ask questions in the terminal.")
    s.add_argument("-c", "--collection")
    s.add_argument("-q", "--question", help="Ask one question and exit.")
    s.add_argument("--mode", choices=["simple", "agentic"])
    s.set_defaults(fn=cmd_ask)

    s = sub.add_parser("eval", help="Benchmark retrieval and answering configurations.")
    s.add_argument("-c", "--collection")
    s.add_argument(
        "--presets", default="dense,hybrid,agentic", help="Comma list: dense,hybrid,hybrid+rerank,agentic"
    )
    s.add_argument(
        "--chunking",
        dest="compare_chunking",
        help="Comma list of chunking strategies to compare (re-ingests).",
    )
    s.add_argument("--golden", help="JSON file with evaluation items to import first.")
    s.add_argument("--out", default="eval/results", help="Folder for the markdown and JSON report.")
    s.set_defaults(fn=cmd_eval)

    sub.add_parser("samples", help="Create the sample collection.").set_defaults(fn=cmd_samples)
    sub.add_parser("collections", help="List collections.").set_defaults(fn=cmd_collections)
    sub.add_parser("status", help="Show provider configuration and check keys.").set_defaults(fn=cmd_status)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    _apply_overrides(args)
    try:
        return int(args.fn(args) or 0)
    except DocSageError as exc:
        err.print(f"[red]{exc.message}[/red]" + (f"\n[dim]{exc.hint}[/dim]" if exc.hint else ""))
        return 1
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
