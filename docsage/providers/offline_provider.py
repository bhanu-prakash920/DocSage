"""Key-free providers so DocSage runs (and is testable) with no API access.

* ``OfflineChat`` marks the pipeline as non-generative. Every LLM step has a deterministic
  fallback in :mod:`docsage.tasks` (extractive answers, heuristic grading, caption-based figure
  descriptions), so the full workflow still runs end to end.
* ``LocalEmbeddings`` uses a cached sentence-transformers model on CPU/GPU.
* ``HashEmbeddings`` is a dependency-free feature-hashing embedder used as the last resort.
"""

from __future__ import annotations

import math
import re
import threading
import zlib
from collections import Counter
from itertools import pairwise
from typing import Any

from ..errors import ProviderError
from .base import ChatModel, EmbeddingModel

_WORD = re.compile(r"[a-z0-9]+(?:[.'][a-z0-9]+)*")
STOPWORDS = frozenset(
    [
        "a",
        "an",
        "and",
        "are",
        "as",
        "at",
        "be",
        "but",
        "by",
        "for",
        "from",
        "has",
        "have",
        "in",
        "is",
        "it",
        "its",
        "of",
        "on",
        "or",
        "that",
        "the",
        "this",
        "to",
        "was",
        "were",
        "will",
        "with",
        "what",
        "which",
        "who",
        "whom",
        "how",
        "why",
        "when",
        "where",
        "do",
        "does",
        "did",
        "can",
        "could",
        "should",
        "would",
        "there",
        "their",
        "they",
        "them",
        "these",
        "those",
        "than",
        "then",
        "so",
        "if",
        "into",
        "about",
        "over",
        "under",
        "not",
        "no",
        "yes",
        "i",
        "you",
        "he",
        "she",
        "we",
        "our",
        "your",
        "his",
        "her",
        "my",
        "me",
        "us",
        "been",
        "being",
        "also",
        "any",
        "all",
        "such",
        "may",
        "might",
        "must",
        "shall",
    ]
)


def tokenize(text: str, *, keep_stopwords: bool = False) -> list[str]:
    tokens = _WORD.findall(text.lower())
    out = []
    for t in tokens:
        if not keep_stopwords and t in STOPWORDS:
            continue
        if len(t) > 4 and t.endswith("ies"):
            t = t[:-3] + "y"
        elif len(t) > 3 and t.endswith("s") and not t.endswith("ss"):
            t = t[:-1]
        out.append(t)
    return out


class OfflineChat(ChatModel):
    provider = "offline"
    generative = False
    supports_vision = False

    def __init__(self, model: str = "extractive"):
        super().__init__(model)

    def generate(self, messages, *, system=None, max_tokens=4096, json_schema=None) -> str:
        raise ProviderError(
            "This step needs a language model, but DocSage is running in offline mode.",
            hint="Add ANTHROPIC_API_KEY, GOOGLE_API_KEY or OPENAI_API_KEY to .env.",
        )


class HashEmbeddings(EmbeddingModel):
    provider = "hash"
    batch_size = 512

    def __init__(self, model: str = "feature-hash-v1", dim: int = 512):
        super().__init__(model, dim)

    def _vector(self, text: str) -> list[float]:
        vec = [0.0] * self.dim
        tokens = tokenize(text)
        grams = tokens + [f"{a}_{b}" for a, b in pairwise(tokens)]
        for gram, count in Counter(grams).items():
            h = zlib.crc32(gram.encode())
            sign = 1.0 if (h >> 31) & 1 else -1.0
            weight = (1.0 + math.log(count)) * (0.6 if "_" in gram else 1.0)
            vec[h % self.dim] += sign * weight
        return vec

    def _embed(self, texts: list[str], *, query: bool) -> list[list[float]]:
        return [self._vector(t) for t in texts]


_st_lock = threading.Lock()
_st_models: dict[str, Any] = {}

_QUERY_PREFIX = {"BAAI/bge": "Represent this sentence for searching relevant passages: "}


class LocalEmbeddings(EmbeddingModel):
    provider = "local"
    batch_size = 32

    def __init__(self, model: str, dim: int | None = None):
        self._model = _load_sentence_transformer(model)
        super().__init__(model, dim or int(self._model.get_sentence_embedding_dimension()))
        self.query_prefix = next((v for k, v in _QUERY_PREFIX.items() if model.startswith(k)), "")

    def _embed(self, texts: list[str], *, query: bool) -> list[list[float]]:
        if query and self.query_prefix:
            texts = [self.query_prefix + t for t in texts]
        arr = self._model.encode(
            texts, batch_size=self.batch_size, normalize_embeddings=True, show_progress_bar=False
        )
        return arr.tolist()


def local_embeddings_available() -> bool:
    """Cheap check (no import): importing sentence-transformers pulls in torch and takes seconds."""
    import importlib.util

    return importlib.util.find_spec("sentence_transformers") is not None


def _load_sentence_transformer(name: str) -> Any:
    with _st_lock:
        if name not in _st_models:
            try:
                from sentence_transformers import SentenceTransformer
            except ImportError as exc:  # pragma: no cover - depends on optional extra
                raise ProviderError("Local embeddings need the 'local' extra: uv sync --extra local") from exc
            try:
                _st_models[name] = SentenceTransformer(name, device="cpu")
            except Exception as exc:
                raise ProviderError(f"Could not load local embedding model '{name}': {exc}") from exc
        return _st_models[name]
