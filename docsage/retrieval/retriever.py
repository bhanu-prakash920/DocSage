"""Hybrid retrieval: dense + BM25 (+ entity graph), reciprocal-rank fusion, re-ranking and
small-to-big parent expansion, with metadata filters on modality and document."""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Any

from rank_bm25 import BM25Okapi

from .. import tasks
from ..config import Settings, get_settings
from ..errors import ProviderError
from ..providers import get_chat_model, get_embedding_model
from ..providers.offline_provider import tokenize
from ..store import get_db, get_vectors
from .types import Context

log = logging.getLogger(__name__)

RRF_K = 60
PARENT_WINDOW = 3200


@dataclass
class RetrievalOptions:
    top_k: int = 6
    hybrid: bool = True
    rerank: str = "none"  # none | llm | cross-encoder
    parent_expansion: bool = True
    graph: bool = False
    modalities: list[str] | None = None
    document_ids: list[str] | None = None

    @classmethod
    def from_settings(cls, s: Settings, **overrides: Any) -> RetrievalOptions:
        base = cls(
            top_k=s.top_k,
            hybrid=s.hybrid,
            rerank=s.rerank,
            parent_expansion=s.parent_expansion,
            graph=s.graph_rag,
        )
        for key, value in overrides.items():
            if value is not None and hasattr(base, key):
                setattr(base, key, value)
        return base


@dataclass
class RetrievalTrace:
    query: str
    dense: int = 0
    sparse: int = 0
    graph: int = 0
    candidates: int = 0
    reranked: bool = False
    ms: dict[str, float] = field(default_factory=dict)


# ---------------------------------------------------------------------------------------------
# BM25 index cache (rebuilt when a collection's version changes)
# ---------------------------------------------------------------------------------------------
class _Sparse:
    def __init__(self, rows: list[dict[str, Any]]):
        self.rows = rows
        corpus = [tokenize(f"{r.get('title') or ''} {r['text']}") or ["_"] for r in rows]
        self.bm25 = BM25Okapi(corpus) if rows else None

    def search(
        self, query: str, k: int, modalities: list[str] | None, doc_ids: list[str] | None
    ) -> list[str]:
        if not self.bm25:
            return []
        terms = tokenize(query)
        if not terms:
            return []
        scores = self.bm25.get_scores(terms)
        order = sorted(range(len(scores)), key=lambda i: -scores[i])
        out = []
        for i in order:
            if scores[i] <= 0:
                break
            row = self.rows[i]
            if modalities and row["modality"] not in modalities:
                continue
            if doc_ids and row["document_id"] not in doc_ids:
                continue
            out.append(row["id"])
            if len(out) >= k:
                break
        return out


_sparse_cache: dict[str, tuple[int, _Sparse]] = {}
_sparse_lock = threading.Lock()


def _sparse_for(cid: str, version: int) -> _Sparse:
    with _sparse_lock:
        cached = _sparse_cache.get(cid)
        if cached and cached[0] == version:
            return cached[1]
        index = _Sparse(get_db().collection_chunks(cid))
        _sparse_cache[cid] = (version, index)
        return index


# ---------------------------------------------------------------------------------------------
# Cross-encoder (optional, local)
# ---------------------------------------------------------------------------------------------
_ce_lock = threading.Lock()
_ce_models: dict[str, Any] = {}


def _cross_encoder(name: str) -> Any:
    with _ce_lock:
        if name not in _ce_models:
            try:
                from sentence_transformers import CrossEncoder

                _ce_models[name] = CrossEncoder(name, device="cpu")
            except Exception as exc:
                raise ProviderError(
                    f"Could not load cross-encoder '{name}': {exc}",
                    hint="Install the 'local' extra or switch re-ranking to 'llm'.",
                ) from exc
        return _ce_models[name]


# ---------------------------------------------------------------------------------------------
def rrf(rankings: list[tuple[list[str], float]]) -> dict[str, float]:
    scores: dict[str, float] = {}
    for ids, weight in rankings:
        for rank, cid in enumerate(ids):
            scores[cid] = scores.get(cid, 0.0) + weight / (RRF_K + rank + 1)
    return scores


def _where(opts: RetrievalOptions) -> dict[str, Any] | None:
    clauses = []
    if opts.modalities:
        clauses.append({"modality": {"$in": list(opts.modalities)}})
    if opts.document_ids:
        clauses.append({"document_id": {"$in": list(opts.document_ids)}})
    if not clauses:
        return None
    return clauses[0] if len(clauses) == 1 else {"$and": clauses}


def query_entities(cid: str, query: str) -> list[str]:
    db = get_db()
    known = db.collection_entities(cid)
    if not known:
        return []
    q = query.lower()
    found = {e for e in known if len(e) >= 3 and f" {e} " in f" {q} "}
    found |= set(tasks.heuristic_entities(query)) & set(known)
    return sorted(found)


def retrieve(
    cid: str, query: str, opts: RetrievalOptions, settings: Settings | None = None
) -> tuple[list[Context], RetrievalTrace]:
    settings = settings or get_settings()
    db, vectors = get_db(), get_vectors()
    collection = db.get_collection(cid)
    trace = RetrievalTrace(query=query)
    pool = max(opts.top_k * 4, 24)

    t0 = time.perf_counter()
    embedder = get_embedding_model(
        {
            "provider": collection["embedding_provider"],
            "model": collection["embedding_model"],
            "dim": collection["embedding_dim"],
        },
        settings,
    )
    dense = vectors.query(cid, embedder.embed_query(query), pool, _where(opts))
    dense_ids = [i for i, _ in dense]
    dense_sim = dict(dense)
    trace.dense = len(dense_ids)
    trace.ms["dense"] = round((time.perf_counter() - t0) * 1000, 1)

    rankings: list[tuple[list[str], float]] = [(dense_ids, 1.0)]
    sparse_ids: list[str] = []
    if opts.hybrid:
        t1 = time.perf_counter()
        sparse_ids = _sparse_for(cid, collection["version"]).search(
            query, pool, opts.modalities, opts.document_ids
        )
        rankings.append((sparse_ids, 1.0))
        trace.sparse = len(sparse_ids)
        trace.ms["sparse"] = round((time.perf_counter() - t1) * 1000, 1)
    graph_ids: list[str] = []
    if opts.graph:
        ents = query_entities(cid, query)
        graph_ids = db.chunks_for_entities(cid, ents, limit=pool) if ents else []
        rankings.append((graph_ids, 0.6))
        trace.graph = len(graph_ids)

    fused = rrf(rankings)
    ordered = sorted(fused, key=lambda i: -fused[i])
    shortlist = ordered[: (24 if opts.rerank != "none" else opts.top_k * 2)]
    rows = db.chunks_by_ids(shortlist)
    if opts.modalities or opts.document_ids:  # graph hits bypass the vector filter
        rows = {
            k: v
            for k, v in rows.items()
            if (not opts.modalities or v["modality"] in opts.modalities)
            and (not opts.document_ids or v["document_id"] in opts.document_ids)
        }
    candidates = [rows[i] for i in shortlist if i in rows]
    trace.candidates = len(candidates)

    signals: dict[str, dict[str, Any]] = {}
    for row in candidates:
        rid = row["id"]
        signals[rid] = {
            "fused": round(fused[rid], 5),
            "dense_rank": dense_ids.index(rid) + 1 if rid in dense_sim else None,
            "dense_sim": round(dense_sim[rid], 4) if rid in dense_sim else None,
            "bm25_rank": sparse_ids.index(rid) + 1 if rid in sparse_ids else None,
            "graph": rid in graph_ids,
        }

    if opts.rerank != "none" and candidates:
        t2 = time.perf_counter()
        temp = [_to_context(i + 1, r, 0.0) for i, r in enumerate(candidates)]
        if opts.rerank == "cross-encoder":
            model = _cross_encoder(settings.cross_encoder_model)
            scores = [float(s) for s in model.predict([(query, c.text[:1500]) for c in temp])]
        else:
            scores = tasks.rerank_scores(get_chat_model("fast", settings), query, temp)
        for row, score in zip(candidates, scores, strict=True):
            signals[row["id"]]["rerank"] = round(score, 4)
        candidates = [
            r
            for _, r in sorted(
                zip(scores, candidates, strict=True), key=lambda p: (-p[0], -fused[p[1]["id"]])
            )
        ]
        trace.reranked = True
        trace.ms["rerank"] = round((time.perf_counter() - t2) * 1000, 1)

    top = candidates[: opts.top_k]
    contexts = (
        _expand(top, fused, signals)
        if opts.parent_expansion
        else [_to_context(i + 1, r, fused[r["id"]], signals[r["id"]]) for i, r in enumerate(top)]
    )
    trace.ms["total"] = round((time.perf_counter() - t0) * 1000, 1)
    return contexts, trace


def _to_context(n: int, row: dict[str, Any], score: float, signals: dict[str, Any] | None = None) -> Context:
    return Context(
        n=n,
        chunk_id=row["id"],
        document_id=row["document_id"],
        filename=row.get("filename", ""),
        page=row["page"],
        modality=row["modality"],
        text=row["text"],
        score=score,
        title=row.get("title"),
        asset_path=row.get("asset_path"),
        signals=signals or {},
    )


def _expand(
    rows: list[dict[str, Any]], fused: dict[str, float], signals: dict[str, dict[str, Any]]
) -> list[Context]:
    """Replace small text chunks with their surrounding page text, merging chunks from one page."""
    parents = get_db().parents_by_ids([r["parent_id"] for r in rows if r.get("parent_id")])
    out: list[Context] = []
    by_parent: dict[str, Context] = {}
    for row in rows:
        pid: str | None = row.get("parent_id")
        parent = parents.get(pid) if pid else None
        if pid is None or row["modality"] != "text" or parent is None:
            out.append(_to_context(len(out) + 1, row, fused[row["id"]], signals[row["id"]]))
            continue
        if pid in by_parent:
            by_parent[pid].signals.setdefault("merged_chunks", []).append(row["id"])
            continue
        full = parent["text"]
        if len(full) <= PARENT_WINDOW:
            text = full
        else:
            anchor = full.find(row["text"][:80])
            anchor = anchor if anchor >= 0 else 0
            start = max(0, anchor - PARENT_WINDOW // 3)
            end = min(len(full), start + PARENT_WINDOW)
            text = ("… " if start else "") + full[start:end] + (" …" if end < len(full) else "")
        ctx = _to_context(
            len(out) + 1, {**row, "text": text}, fused[row["id"]], {**signals[row["id"]], "expanded": True}
        )
        by_parent[pid] = ctx
        out.append(ctx)
    return out


def web_search(query: str, settings: Settings, start_n: int, max_results: int = 4) -> list[Context]:
    key = settings.key("tavily")
    if not key:
        return []
    try:
        from tavily import TavilyClient

        result = TavilyClient(api_key=key).search(query, max_results=max_results, search_depth="basic")
    except Exception as exc:
        raise ProviderError(f"Web search failed: {exc}") from exc
    out = []
    for i, item in enumerate(result.get("results", [])):
        out.append(
            Context(
                n=start_n + i,
                chunk_id=None,
                document_id=None,
                filename="",
                page=None,
                modality="web",
                text=item.get("content", "")[:3000],
                score=float(item.get("score") or 0),
                title=item.get("title"),
                url=item.get("url"),
            )
        )
    return out
