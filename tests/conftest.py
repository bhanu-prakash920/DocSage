from __future__ import annotations

import os
from pathlib import Path

import pytest

for key in (
    "ANTHROPIC_API_KEY",
    "GOOGLE_API_KEY",
    "GEMINI_API_KEY",
    "OPENAI_API_KEY",
    "TAVILY_API_KEY",
    "LLAMA_CLOUD_API_KEY",
):
    os.environ.pop(key, None)
os.environ["DOCSAGE_LLM_PROVIDER"] = "offline"
os.environ["DOCSAGE_EMBEDDING_PROVIDER"] = "hash"


def _reset(data_dir: Path, **overrides):
    from docsage.config import configure
    from docsage.jobs import JobRunner, set_runner
    from docsage.providers.factory import clear_cache, set_chat_override
    from docsage.retrieval import retriever
    from docsage.store.db import reset_db
    from docsage.store.vectors import reset_vectors

    reset_db()
    reset_vectors()
    clear_cache()
    set_chat_override(None)
    retriever._sparse_cache.clear()
    set_runner(JobRunner(synchronous=True))
    params = {
        "data_dir": str(data_dir),
        "llm_provider": "offline",
        "embedding_provider": "hash",
        "api_token": None,
        "max_ingest_cost_usd": 2.0,
        **overrides,
    }
    return configure(**params)


@pytest.fixture()
def env(tmp_path):
    settings = _reset(tmp_path / "data")
    yield settings
    from docsage.providers.factory import set_chat_override

    set_chat_override(None)


@pytest.fixture(scope="module")
def sample_collection(tmp_path_factory):
    """Offline-ingested sample collection shared by a test module."""
    data = tmp_path_factory.mktemp("samples")
    _reset(data)
    from docsage import library

    collection = library.load_samples()
    return {"id": collection["id"], "data_dir": data}


@pytest.fixture()
def sample_env(sample_collection):
    _reset(sample_collection["data_dir"])
    return sample_collection
