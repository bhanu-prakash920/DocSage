from __future__ import annotations

import pytest

from docsage.agent.service import ask
from docsage.errors import DocSageError
from docsage.providers.factory import set_chat_override
from docsage.retrieval.retriever import RetrievalOptions, retrieve, rrf
from docsage.samples import DECK, REPORT
from docsage.store import get_db
from tests.fakes import FakeChat, fake_web


def test_rrf_rewards_agreement():
    scores = rrf([(["a", "b", "c"], 1.0), (["b", "a"], 1.0)])
    assert scores["a"] == scores["b"] > scores["c"]


def test_samples_are_ready(sample_env):
    docs = get_db().list_documents(sample_env["id"])
    assert len(docs) == 4 and all(d["status"] == "ready" for d in docs)
    stats = get_db().stats(sample_env["id"])
    assert stats["chunks"]["table"] >= 5 and stats["chunks"]["image"] >= 2


def test_hybrid_retrieval_finds_the_table(sample_env):
    contexts, trace = retrieve(
        sample_env["id"], "storage segment revenue 2025", RetrievalOptions(top_k=4, hybrid=True)
    )
    assert trace.sparse > 0 and trace.dense > 0
    assert any(c.modality == "table" and c.filename == REPORT and c.page == 2 for c in contexts)


def test_filters_restrict_modality_and_document(sample_env):
    deck = next(d for d in get_db().list_documents(sample_env["id"]) if d["filename"] == DECK)
    contexts, _ = retrieve(
        sample_env["id"],
        "pre-orders by region",
        RetrievalOptions(top_k=5, modalities=["table"], document_ids=[deck["id"]]),
    )
    assert contexts and all(c.modality == "table" and c.document_id == deck["id"] for c in contexts)


def test_parent_expansion_merges_chunks_from_one_page(sample_env):
    contexts, _ = retrieve(
        sample_env["id"],
        "Halcyon revenue dividend outlook guidance",
        RetrievalOptions(top_k=6, parent_expansion=True),
    )
    text_pages = [(c.document_id, c.page) for c in contexts if c.modality == "text"]
    assert len(text_pages) == len(set(text_pages))


def test_offline_agentic_answer_cites_sources(sample_env):
    result = ask(
        sample_env["id"], "What is the response time for a SEV2 incident?", mode="agentic", persist=False
    )
    assert result["type"] == "done" and "15 minutes" in result["answer"]
    assert any(s["cited"] for s in result["sources"])
    assert [t["step"] for t in result["trace"]][:3] == ["rewrite", "retrieve", "grade"]


def test_generative_agent_sufficient_path(sample_env):
    fake = FakeChat(sufficient=True)
    set_chat_override(fake)
    from docsage.config import configure

    configure(
        data_dir=str(sample_env["data_dir"]),
        llm_provider="anthropic",
        embedding_provider="hash",
        api_token=None,
    )
    result = ask(sample_env["id"], "How much did storage make?", mode="agentic", persist=False)
    assert result["type"] == "done", result
    assert fake.calls.count("grade") == 1 and "web" not in [t["step"] for t in result["trace"]]
    assert result["sources"][0]["cited"] and result["usage"]["cost_usd"] > 0


def test_generative_agent_retries_then_uses_web(sample_env, monkeypatch):
    from docsage.agent import graph
    from docsage.config import configure

    monkeypatch.setattr(graph, "web_search", fake_web)
    fake = FakeChat(sufficient=[False, False])
    set_chat_override(fake)
    configure(
        data_dir=str(sample_env["data_dir"]),
        llm_provider="anthropic",
        embedding_provider="hash",
        api_token=None,
        tavily_api_key="tvly-test",
        agent_max_retries=1,
    )
    result = ask(sample_env["id"], "What was revenue per segment?", mode="agentic", persist=False)
    steps = [t["step"] for t in result["trace"]]
    assert steps == ["rewrite", "retrieve", "grade", "rewrite", "retrieve", "grade", "web", "generate"]
    assert result["used_web"] and any(s["modality"] == "web" for s in result["sources"])


def test_conversation_history_feeds_rewrite(sample_env):
    from docsage.config import configure

    fake = FakeChat()
    set_chat_override(fake)
    configure(
        data_dir=str(sample_env["data_dir"]),
        llm_provider="anthropic",
        embedding_provider="hash",
        api_token=None,
    )
    first = ask(sample_env["id"], "Tell me about the Orbit S2 launch", mode="simple")
    second = ask(
        sample_env["id"], "When does it ship?", mode="simple", conversation_id=first["conversation_id"]
    )
    assert second["standalone_question"].endswith("(rewritten)")
    assert len(get_db().conversation_turns(first["conversation_id"])) == 2


def test_query_budget_is_enforced(sample_env):
    from docsage.config import configure

    set_chat_override(FakeChat())
    configure(
        data_dir=str(sample_env["data_dir"]),
        llm_provider="anthropic",
        embedding_provider="hash",
        api_token=None,
        max_query_cost_usd=0.000001,
    )
    result = ask(sample_env["id"], "anything", mode="agentic", persist=False)
    assert result["type"] == "error" and "budget" in result["message"]


def test_graph_rag_and_llm_rerank(tmp_path):
    from docsage import library
    from docsage.config import configure
    from tests.conftest import _reset

    _reset(tmp_path / "g", graph_rag=True)
    fake = FakeChat()
    set_chat_override(fake)
    configure(
        data_dir=str(tmp_path / "g"),
        llm_provider="anthropic",
        embedding_provider="hash",
        api_token=None,
        graph_rag=True,
        summarize_images=True,
    )
    collection = library.load_samples()
    assert fake.images_seen >= 2 and "entities" in fake.calls
    contexts, trace = retrieve(
        collection["id"], "Halcyon Grid storage", RetrievalOptions(top_k=4, graph=True, rerank="llm")
    )
    assert trace.graph > 0 and trace.reranked and contexts


def test_empty_question_rejected(sample_env):
    with pytest.raises(DocSageError):
        list(
            __import__("docsage.agent.service", fromlist=["run_query"]).run_query(
                sample_env["id"],
                __import__("docsage.agent.service", fromlist=["QueryRequest"]).QueryRequest("  "),
            )
        )
