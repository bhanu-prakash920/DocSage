"""Chroma-backed vector store. One Chroma collection per DocSage collection."""

from __future__ import annotations

import logging
import threading
from pathlib import Path
from typing import Any

import chromadb
from chromadb.config import Settings as ChromaSettings

log = logging.getLogger(__name__)


def _name(cid: str) -> str:
    return f"ds_{cid}".replace("-", "_")[:60]


class VectorStore:
    def __init__(self, path: Path):
        self.path = path
        path.mkdir(parents=True, exist_ok=True)
        self.client = chromadb.PersistentClient(
            path=str(path), settings=ChromaSettings(anonymized_telemetry=False)
        )
        self._lock = threading.RLock()

    def _collection(self, cid: str):
        return self.client.get_or_create_collection(_name(cid), metadata={"hnsw:space": "cosine"})

    def upsert(
        self,
        cid: str,
        ids: list[str],
        embeddings: list[list[float]],
        documents: list[str],
        metadatas: list[dict[str, Any]],
    ) -> None:
        if not ids:
            return
        with self._lock:
            col = self._collection(cid)
            for i in range(0, len(ids), 1000):
                col.upsert(
                    ids=ids[i : i + 1000],
                    embeddings=embeddings[i : i + 1000],
                    documents=documents[i : i + 1000],
                    metadatas=metadatas[i : i + 1000],
                )

    def delete_document(self, cid: str, document_id: str) -> None:
        with self._lock:
            self._collection(cid).delete(where={"document_id": document_id})

    def drop(self, cid: str) -> None:
        with self._lock:
            try:
                self.client.delete_collection(_name(cid))
            except Exception:  # collection may not exist yet
                log.debug("vector collection %s did not exist", cid)

    def count(self, cid: str) -> int:
        return self._collection(cid).count()

    def query(
        self, cid: str, embedding: list[float], k: int, where: dict[str, Any] | None = None
    ) -> list[tuple[str, float]]:
        col = self._collection(cid)
        n = col.count()
        if n == 0:
            return []
        result = col.query(
            query_embeddings=[embedding], n_results=min(k, n), where=where or None, include=["distances"]
        )
        ids = result["ids"][0]
        distances = result["distances"][0] if result.get("distances") else [0.0] * len(ids)
        return [(i, 1.0 - float(d)) for i, d in zip(ids, distances, strict=False)]


_store: VectorStore | None = None
_store_lock = threading.Lock()


def get_vectors() -> VectorStore:
    from ..config import get_settings

    global _store
    with _store_lock:
        path = get_settings().data_dir / "chroma"
        if _store is None or _store.path != path:
            _store = VectorStore(path)
        return _store


def reset_vectors() -> None:
    global _store
    with _store_lock:
        _store = None
