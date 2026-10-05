"""Query service: runs the agent in a worker thread and streams events to callers (SSE, CLI)."""

from __future__ import annotations

import logging
import queue
import threading
import time
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

from ..config import Settings, get_settings
from ..errors import DocSageError, NotFound
from ..providers.base import UsageMeter
from ..retrieval.retriever import RetrievalOptions
from ..store import get_db
from ..store.db import new_id, now
from ..tasks import cited_numbers
from .graph import build_graph

log = logging.getLogger(__name__)

_DONE = object()


@dataclass
class QueryRequest:
    question: str
    conversation_id: str | None = None
    mode: str | None = None
    top_k: int | None = None
    hybrid: bool | None = None
    rerank: str | None = None
    parent_expansion: bool | None = None
    web_search: bool | None = None
    graph: bool | None = None
    modalities: list[str] | None = None
    document_ids: list[str] | None = None
    persist: bool = True


def _history(conversation_id: str | None, turns: int) -> list[dict[str, str]]:
    if not conversation_id:
        return []
    history: list[dict[str, str]] = []
    for q in get_db().conversation_turns(conversation_id)[-turns:]:
        if q["status"] != "done":
            continue
        history.append({"role": "user", "content": q["question"]})
        history.append({"role": "assistant", "content": q["answer"] or ""})
    return history


def run_query(cid: str, req: QueryRequest, settings: Settings | None = None) -> Iterator[dict[str, Any]]:
    """Yield events: start, step, sources, verdict, token, done | error."""
    settings = settings or get_settings()
    db = get_db()
    collection = db.get_collection(cid)
    question = req.question.strip()
    if not question:
        raise DocSageError("Ask a question first.")
    if req.conversation_id:
        conv = db.get_conversation(req.conversation_id)
        if conv["collection_id"] != cid:
            raise NotFound("That conversation belongs to a different collection.")
        conversation_id = conv["id"]
    elif req.persist:
        conversation_id = db.create_conversation(cid, question)["id"]
    else:
        conversation_id = None

    mode = req.mode or settings.query_mode
    options = RetrievalOptions.from_settings(
        settings,
        top_k=req.top_k,
        hybrid=req.hybrid,
        rerank=req.rerank,
        parent_expansion=req.parent_expansion,
        graph=req.graph,
        modalities=req.modalities or None,
        document_ids=req.document_ids or None,
    )
    web = settings.web_search if req.web_search is None else req.web_search
    meter = UsageMeter(budget_usd=settings.max_query_cost_usd or None, label="this question")
    events: queue.Queue[Any] = queue.Queue()
    query_id = new_id("q")
    history = _history(conversation_id, settings.history_turns)
    started = time.monotonic()
    result: dict[str, Any] = {}

    def emit(event: dict[str, Any]) -> None:
        events.put(event)

    def worker() -> None:
        state: dict[str, Any] = {"question": question, "history": history, "attempts": 0, "steps": []}
        error: BaseException | None = None
        try:
            runtime = {
                "cid": cid,
                "mode": mode,
                "options": options,
                "web_search": web,
                "max_retries": settings.agent_max_retries,
                "settings": settings,
                "meter": meter,
                "emit": emit,
            }
            final = build_graph().invoke(
                state, config={"configurable": {"runtime": runtime}, "recursion_limit": 25}
            )
            result.update(final)
        except BaseException as exc:  # reported to the client as an error event
            error = exc
            if not isinstance(exc, DocSageError):
                log.exception("query failed")
        latency = round((time.monotonic() - started) * 1000)
        answer = result.get("answer", "")
        contexts = result.get("contexts", [])
        cited = cited_numbers(answer)
        for c in contexts:
            c.cited = c.n in cited
        verdict = result.get("verdict")
        usage = meter.snapshot()
        record = {
            "id": query_id,
            "collection_id": cid,
            "conversation_id": conversation_id,
            "question": question,
            "standalone_question": result.get("query"),
            "answer": answer,
            "status": "failed" if error else "done",
            "error": (error.message if isinstance(error, DocSageError) else str(error)) if error else None,
            "mode": mode,
            "options": {
                "top_k": options.top_k,
                "hybrid": options.hybrid,
                "rerank": options.rerank,
                "parent_expansion": options.parent_expansion,
                "graph": options.graph,
                "modalities": options.modalities,
                "document_ids": options.document_ids,
                "web_search": web,
            },
            "sources": [c.public() for c in contexts],
            "trace": result.get("steps") or state.get("steps"),
            "verdict": vars(verdict) if verdict else None,
            "used_web": int(bool(result.get("used_web"))),
            "latency_ms": latency,
            "input_tokens": usage["input_tokens"],
            "output_tokens": usage["output_tokens"],
            "cost_usd": usage["cost_usd"],
            "created_at": now(),
        }
        if req.persist:
            try:
                db.save_query(record)
                if conversation_id:
                    db.touch_conversation(conversation_id)
            except Exception:
                log.exception("could not persist query")
        if error:
            emit(
                {
                    "type": "error",
                    "message": record["error"],
                    "hint": getattr(error, "hint", None),
                    "query_id": query_id,
                    "usage": usage,
                }
            )
        else:
            emit(
                {
                    "type": "done",
                    "query_id": query_id,
                    "conversation_id": conversation_id,
                    "answer": answer,
                    "sources": record["sources"],
                    "standalone_question": record["standalone_question"],
                    "verdict": record["verdict"],
                    "used_web": bool(record["used_web"]),
                    "latency_ms": latency,
                    "usage": usage,
                    "trace": record["trace"],
                }
            )
        events.put(_DONE)

    yield {
        "type": "start",
        "query_id": query_id,
        "conversation_id": conversation_id,
        "mode": mode,
        "collection": collection["id"],
    }
    thread = threading.Thread(target=worker, name=f"query-{query_id}", daemon=True)
    thread.start()
    while True:
        event = events.get()
        if event is _DONE:
            break
        yield event


def ask(cid: str, question: str, **kwargs: Any) -> dict[str, Any]:
    """Blocking helper: run a query and return the final event (used by evaluation and tests)."""
    final: dict[str, Any] = {}
    sources: list[dict[str, Any]] = []
    for event in run_query(cid, QueryRequest(question=question, **kwargs)):
        if event["type"] == "sources":
            sources = event["sources"]
        if event["type"] in ("done", "error"):
            final = event
    final.setdefault("sources", sources)
    return final
