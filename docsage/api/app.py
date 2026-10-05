"""FastAPI application: JSON API under /api, server-sent events for answers, and the web UI."""

from __future__ import annotations

import json
import logging
import os
import random
from collections.abc import Iterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Literal

from fastapi import Depends, FastAPI, File, HTTPException, Query, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .. import __version__, library, tasks
from ..agent.service import QueryRequest, run_query
from ..config import EDITABLE_FIELDS, Settings, get_settings, update_settings
from ..errors import DocSageError, NotFound
from ..eval.runner import PRESETS, resolve_configs, submit_eval
from ..jobs import get_runner, submit_ingest
from ..logging_setup import configure_logging
from ..providers import get_chat_model
from ..providers.base import UsageMeter, metering
from ..providers.factory import clear_cache, provider_status
from ..store import get_db

log = logging.getLogger(__name__)

WEB_DIST = Path(os.environ.get("DOCSAGE_WEB_DIST") or Path(__file__).resolve().parents[2] / "web" / "dist")


@asynccontextmanager
async def lifespan(app: FastAPI):
    s = get_settings()
    configure_logging(s.log_level, s.log_json)
    s.data_dir.mkdir(parents=True, exist_ok=True)
    interrupted = get_db().fail_interrupted_jobs()
    if interrupted:
        log.warning("marked %s interrupted jobs as failed", interrupted)
    yield
    get_runner().shutdown()


def require_token(request: Request) -> None:
    token = get_settings().api_token
    if token is None:
        return
    expected = token.get_secret_value()
    header = request.headers.get("authorization", "")
    supplied = (
        header.removeprefix("Bearer ").strip()
        if header.startswith("Bearer ")
        else request.query_params.get("token", "")
    )
    if supplied != expected:
        raise HTTPException(status_code=401, detail="Missing or invalid API token.")


def create_app() -> FastAPI:
    s = get_settings()
    app = FastAPI(
        title="DocSage API",
        version=__version__,
        lifespan=lifespan,
        docs_url="/api/docs",
        openapi_url="/api/openapi.json",
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=s.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.exception_handler(DocSageError)
    async def domain_error(_: Request, exc: DocSageError) -> JSONResponse:
        return JSONResponse(status_code=exc.status_code, content={"error": exc.message, "hint": exc.hint})

    @app.exception_handler(HTTPException)
    async def http_error(_: Request, exc: HTTPException) -> JSONResponse:
        return JSONResponse(status_code=exc.status_code, content={"error": exc.detail, "hint": None})

    @app.exception_handler(Exception)
    async def unexpected(_: Request, exc: Exception) -> JSONResponse:
        log.exception("unhandled error")
        return JSONResponse(
            status_code=500, content={"error": "Something went wrong on the server.", "hint": str(exc)[:300]}
        )

    from fastapi import APIRouter

    api = APIRouter(prefix="/api", dependencies=[Depends(require_token)])
    register_routes(api)
    app.include_router(api)

    @app.get("/api/health", include_in_schema=False)
    def health() -> dict[str, Any]:
        st = provider_status()
        return {
            "status": "ok",
            "version": __version__,
            "llm": st["llm_provider"],
            "generative": st["generative"],
            "auth_required": get_settings().api_token is not None,
        }

    if WEB_DIST.exists():
        app.mount("/assets", StaticFiles(directory=WEB_DIST / "assets"), name="web-assets")

        @app.get("/{full_path:path}", include_in_schema=False)
        def spa(full_path: str) -> FileResponse:
            if full_path.startswith("api/"):
                raise HTTPException(status_code=404, detail="Not found")
            candidate = (WEB_DIST / full_path).resolve()
            if full_path and candidate.is_file() and WEB_DIST.resolve() in candidate.parents:
                return FileResponse(candidate)
            return FileResponse(WEB_DIST / "index.html")

    return app


# =============================================================================================
# Request models
# =============================================================================================
class CollectionIn(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    description: str = Field(default="", max_length=400)


class CollectionPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=80)
    description: str | None = Field(default=None, max_length=400)


class IngestMany(BaseModel):
    document_ids: list[str] | None = None


class QueryIn(BaseModel):
    question: str = Field(min_length=1, max_length=4000)
    conversation_id: str | None = None
    mode: Literal["simple", "agentic"] | None = None
    top_k: int | None = Field(default=None, ge=1, le=30)
    hybrid: bool | None = None
    rerank: Literal["none", "llm", "cross-encoder"] | None = None
    parent_expansion: bool | None = None
    web_search: bool | None = None
    graph: bool | None = None
    modalities: list[Literal["text", "table", "image"]] | None = None
    document_ids: list[str] | None = None


class EvalItemIn(BaseModel):
    question: str = Field(min_length=3, max_length=1000)
    reference_answer: str = Field(default="", max_length=2000)
    expected_document: str | None = None
    expected_page: int | None = Field(default=None, ge=1)
    tag: str | None = None


class EvalImport(BaseModel):
    items: list[EvalItemIn] = Field(max_length=500)


class EvalGenerate(BaseModel):
    count: int = Field(default=8, ge=1, le=30)


class EvalRunIn(BaseModel):
    presets: list[str] = Field(default_factory=lambda: ["dense", "hybrid", "agentic"], min_length=1)
    chunking: list[Literal["recursive", "semantic", "agentic", "proposition"]] | None = None


# =============================================================================================
# Routes
# =============================================================================================
def _doc_public(doc: dict[str, Any]) -> dict[str, Any]:
    out = {k: v for k, v in doc.items() if k != "path"}
    return out


def register_routes(api) -> None:
    # -- settings ---------------------------------------------------------------------------------
    @api.get("/settings")
    def read_settings() -> dict[str, Any]:
        s = get_settings()
        return {
            "values": s.editable_dict(),
            "status": provider_status(s),
            "editable": list(EDITABLE_FIELDS),
            "options": _options(),
            "data_dir": str(s.data_dir),
        }

    @api.patch("/settings")
    def patch_settings(changes: dict[str, Any]) -> dict[str, Any]:
        try:
            update_settings(changes)
        except ValueError as exc:
            message = str(exc).split("\n")[0] if "validation" not in str(exc) else _short_validation(exc)
            raise DocSageError(message) from exc
        clear_cache()
        return read_settings()

    # -- collections ----------------------------------------------------------------------------
    @api.get("/collections")
    def list_collections() -> list[dict[str, Any]]:
        return get_db().list_collections()

    @api.post("/collections", status_code=201)
    def create_collection(body: CollectionIn) -> dict[str, Any]:
        return library.create_collection(body.name, body.description)

    @api.get("/collections/{cid}")
    def get_collection(cid: str) -> dict[str, Any]:
        return get_db().get_collection(cid)

    @api.patch("/collections/{cid}")
    def patch_collection(cid: str, body: CollectionPatch) -> dict[str, Any]:
        return get_db().update_collection(cid, name=body.name, description=body.description)

    @api.delete("/collections/{cid}", status_code=204)
    def delete_collection(cid: str) -> None:
        library.delete_collection(cid)

    @api.get("/collections/{cid}/stats")
    def collection_stats(cid: str) -> dict[str, Any]:
        db = get_db()
        db.get_collection(cid)
        return {
            **db.stats(cid),
            "recent_queries": db.list_queries(cid, 8),
            "recent_jobs": db.list_jobs(cid, limit=8),
        }

    @api.post("/samples", status_code=201)
    def samples() -> dict[str, Any]:
        return library.load_samples()

    # -- documents ------------------------------------------------------------------------------
    @api.get("/collections/{cid}/documents")
    def list_documents(cid: str) -> list[dict[str, Any]]:
        db = get_db()
        db.get_collection(cid)
        return [_doc_public(d) for d in db.list_documents(cid)]

    @api.post("/collections/{cid}/uploads", status_code=201)
    async def upload(cid: str, files: list[UploadFile] = File(...)) -> dict[str, Any]:
        get_db().get_collection(cid)
        s = get_settings()
        staged, errors = [], []
        for f in files:
            data = await f.read(s.max_upload_mb * 1024 * 1024 + 1)
            try:
                staged.append(_doc_public(library.stage_file(cid, f.filename or "upload", data, s)))
            except DocSageError as exc:
                errors.append({"filename": f.filename, "error": exc.message, "hint": exc.hint})
        return {"documents": staged, "errors": errors}

    @api.post("/collections/{cid}/ingest")
    def ingest_many(cid: str, body: IngestMany) -> list[dict[str, Any]]:
        db = get_db()
        docs = db.list_documents(cid)
        wanted = set(body.document_ids or [])
        targets = [d for d in docs if (d["id"] in wanted) or (not wanted and d["status"] == "staged")]
        return [submit_ingest(d["id"]) for d in targets]

    @api.get("/documents/{doc_id}")
    def get_document(doc_id: str) -> dict[str, Any]:
        db = get_db()
        doc = _doc_public(db.get_document(doc_id))
        doc["chunks"] = db.document_chunks(doc_id, limit=400)
        return doc

    @api.post("/documents/{doc_id}/ingest")
    def ingest_document(doc_id: str) -> dict[str, Any]:
        return submit_ingest(doc_id)

    @api.delete("/documents/{doc_id}", status_code=204)
    def delete_document(doc_id: str) -> None:
        library.delete_document(doc_id)

    # -- jobs -----------------------------------------------------------------------------------
    @api.get("/jobs")
    def list_jobs(
        collection_id: str | None = None, active: bool = False, limit: int = Query(default=30, le=200)
    ) -> list[dict[str, Any]]:
        return get_db().list_jobs(collection_id, active=active, limit=limit)

    @api.get("/jobs/{job_id}")
    def get_job(job_id: str) -> dict[str, Any]:
        return get_db().get_job(job_id)

    @api.post("/jobs/{job_id}/cancel")
    def cancel_job(job_id: str) -> dict[str, Any]:
        get_runner().cancel(job_id)
        return get_db().get_job(job_id)

    # -- querying ---------------------------------------------------------------------------------
    @api.post("/collections/{cid}/query")
    def query(cid: str, body: QueryIn) -> StreamingResponse:
        db = get_db()
        collection = db.get_collection(cid)
        ready = db.scalar("SELECT COUNT(*) FROM documents WHERE collection_id=? AND status='ready'", (cid,))
        if not ready:
            raise DocSageError(
                f"'{collection['name']}' has no indexed documents yet.",
                hint="Upload documents in the Library and wait for ingestion to finish.",
            )
        events = run_query(cid, QueryRequest(**body.model_dump()))
        first = next(events)  # surfaces validation errors as JSON before streaming starts

        def stream() -> Iterator[bytes]:
            yield _sse(first)
            for event in events:
                yield _sse(event)

        return StreamingResponse(
            stream(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @api.get("/collections/{cid}/conversations")
    def conversations(cid: str) -> list[dict[str, Any]]:
        return get_db().list_conversations(cid)

    @api.get("/conversations/{conv_id}")
    def conversation(conv_id: str) -> dict[str, Any]:
        db = get_db()
        return {**db.get_conversation(conv_id), "turns": db.conversation_turns(conv_id)}

    @api.delete("/conversations/{conv_id}", status_code=204)
    def delete_conversation(conv_id: str) -> None:
        db = get_db()
        db.get_conversation(conv_id)
        db.delete_conversation(conv_id)

    # -- overview -------------------------------------------------------------------------------
    @api.get("/overview")
    def overview() -> dict[str, Any]:
        db = get_db()
        return {
            **db.stats(),
            "collections": db.list_collections(),
            "recent_jobs": db.list_jobs(limit=8),
            "status": provider_status(),
        }

    # -- evaluation ------------------------------------------------------------------------------
    @api.get("/eval/presets")
    def eval_presets() -> dict[str, Any]:
        return PRESETS

    @api.get("/collections/{cid}/eval/items")
    def eval_items(cid: str) -> list[dict[str, Any]]:
        return get_db().list_eval_items(cid)

    @api.post("/collections/{cid}/eval/items", status_code=201)
    def add_eval_item(cid: str, body: EvalItemIn) -> dict[str, Any]:
        get_db().get_collection(cid)
        return get_db().add_eval_item(cid, **body.model_dump())

    @api.post("/collections/{cid}/eval/items/import", status_code=201)
    def import_eval_items(cid: str, body: EvalImport) -> list[dict[str, Any]]:
        db = get_db()
        db.get_collection(cid)
        return [db.add_eval_item(cid, **i.model_dump(), source="import") for i in body.items]

    @api.post("/collections/{cid}/eval/items/generate", status_code=201)
    def generate_eval_items(cid: str, body: EvalGenerate) -> list[dict[str, Any]]:
        db = get_db()
        db.get_collection(cid)
        chunks = [c for c in db.collection_chunks(cid) if len(c["text"]) > 200]
        if not chunks:
            raise DocSageError("There are no indexed passages to write questions from yet.")
        random.Random(42).shuffle(chunks)
        picked = chunks[: body.count]
        rows = db.chunks_by_ids([c["id"] for c in picked])
        meter = UsageMeter(budget_usd=0.5, label="question generation")
        with metering(meter):
            generated = tasks.generate_questions(get_chat_model("fast"), [c["text"] for c in picked])
        out = []
        for index, question, answer in generated:
            chunk = rows.get(picked[index]["id"], {})
            out.append(
                db.add_eval_item(
                    cid,
                    question=question,
                    reference_answer=answer,
                    expected_document=chunk.get("filename"),
                    expected_page=chunk.get("page"),
                    tag=chunk.get("modality"),
                    source="generated",
                )
            )
        return out

    @api.delete("/eval/items/{item_id}", status_code=204)
    def delete_eval_item(item_id: str) -> None:
        get_db().delete_eval_item(item_id)

    @api.get("/collections/{cid}/eval/runs")
    def eval_runs(cid: str) -> list[dict[str, Any]]:
        return get_db().list_eval_runs(cid)

    @api.post("/collections/{cid}/eval/runs", status_code=201)
    def start_eval(cid: str, body: EvalRunIn) -> dict[str, Any]:
        db = get_db()
        db.get_collection(cid)
        if not db.list_eval_items(cid):
            raise DocSageError("Add at least one evaluation question first.")
        if any(r["status"] in ("queued", "running") for r in db.list_eval_runs(cid, 5)):
            raise DocSageError("An evaluation is already running for this collection.")
        return submit_eval(cid, resolve_configs(body.presets, body.chunking))

    @api.get("/eval/runs/{run_id}")
    def eval_run(run_id: str) -> dict[str, Any]:
        return get_db().get_eval_run(run_id)

    # -- assets ---------------------------------------------------------------------------------
    @api.get("/files/{path:path}")
    def asset(path: str) -> FileResponse:
        root = (get_settings().data_dir / "assets").resolve()
        target = (get_settings().data_dir / path).resolve()
        if root not in target.parents or not target.is_file():
            raise NotFound("Asset not found.")
        return FileResponse(target, headers={"Cache-Control": "private, max-age=3600"})


def _sse(event: dict[str, Any]) -> bytes:
    return f"data: {json.dumps(event, default=str)}\n\n".encode()


def _short_validation(exc: Exception) -> str:
    text = str(exc)
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    return "Invalid setting: " + (" ".join(lines[1:3]) if len(lines) > 2 else text)[:300]


def _options() -> dict[str, list[str]]:
    fields = Settings.model_fields
    out = {}
    for name in (
        "llm_provider",
        "embedding_provider",
        "parser",
        "chunking",
        "rerank",
        "query_mode",
        "anthropic_effort",
    ):
        annotation = fields[name].annotation
        args = getattr(annotation, "__args__", ())
        out[name] = [a for a in args if isinstance(a, str)]
    return out


app = create_app()
