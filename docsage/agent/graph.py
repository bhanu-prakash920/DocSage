"""The DocSage answering agent as an explicit LangGraph state machine.

simple:   rewrite -> retrieve -> generate
agentic:  rewrite -> retrieve -> grade -> (generate | retry rewrite | web search -> generate)
"""

from __future__ import annotations

import time
from collections.abc import Callable
from functools import lru_cache
from typing import Any, TypedDict

from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, START, StateGraph

from .. import tasks
from ..config import Settings
from ..providers import get_chat_model
from ..providers.base import UsageMeter, metering
from ..retrieval.retriever import RetrievalOptions, retrieve, web_search
from ..retrieval.types import Context, Verdict


class AgentState(TypedDict, total=False):
    question: str
    history: list[dict[str, str]]
    query: str
    attempts: int
    contexts: list[Context]
    verdict: Verdict | None
    used_web: bool
    answer: str
    steps: list[dict[str, Any]]


class Runtime(TypedDict):
    cid: str
    mode: str
    options: RetrievalOptions
    web_search: bool
    max_retries: int
    settings: Settings
    meter: UsageMeter
    emit: Callable[[dict[str, Any]], None]


def _rt(config: RunnableConfig) -> Runtime:
    return config["configurable"]["runtime"]  # type: ignore[index]


class _Step:
    def __init__(self, rt: Runtime, state: AgentState, name: str, label: str):
        self.rt, self.state, self.name, self.label = rt, state, name, label
        self.detail: dict[str, Any] = {}

    def __enter__(self) -> _Step:
        self.t0 = time.perf_counter()
        self.rt["emit"]({"type": "step", "step": self.name, "label": self.label, "status": "running"})
        self._meter = metering(self.rt["meter"])
        self._meter.__enter__()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self._meter.__exit__(exc_type, exc, tb)
        ms = round((time.perf_counter() - self.t0) * 1000)
        record = {
            "step": self.name,
            "label": self.label,
            "ms": ms,
            "detail": self.detail,
            "status": "error" if exc else "done",
        }
        self.state.setdefault("steps", []).append(record)
        self.rt["emit"]({"type": "step", **record})


def rewrite_node(state: AgentState, config: RunnableConfig) -> AgentState:
    rt = _rt(config)
    attempts = state.get("attempts", 0)
    verdict = state.get("verdict")
    feedback = verdict.missing if attempts and verdict is not None else ""
    label = "Refine query" if attempts else "Understand question"
    with _Step(rt, state, "rewrite", label) as step:
        llm = get_chat_model("fast", rt["settings"])
        query = tasks.rewrite_query(llm, state["question"], state.get("history", []), feedback)
        step.detail = {"query": query, "changed": query != state["question"], "attempt": attempts + 1}
    return {"query": query, "steps": state.get("steps", [])}


def retrieve_node(state: AgentState, config: RunnableConfig) -> AgentState:
    rt = _rt(config)
    with _Step(rt, state, "retrieve", "Search documents") as step:
        found, trace = retrieve(rt["cid"], state["query"], rt["options"], rt["settings"])
        previous = state.get("contexts") or []
        verdict = state.get("verdict")
        if previous and verdict:  # retry: keep sources the grader found useful
            keep = [c for c in previous if c.n in set(verdict.useful_sources)]
            seen = {c.chunk_id for c in keep}
            found = keep + [c for c in found if c.chunk_id not in seen]
            found = found[: rt["options"].top_k + 2]
        for i, c in enumerate(found):
            c.n = i + 1
        step.detail = {
            "dense": trace.dense,
            "sparse": trace.sparse,
            "graph": trace.graph,
            "candidates": trace.candidates,
            "reranked": trace.reranked,
            "returned": len(found),
            "ms": trace.ms,
        }
    rt["emit"]({"type": "sources", "sources": [c.public() for c in found]})
    return {"contexts": found, "steps": state.get("steps", [])}


def grade_node(state: AgentState, config: RunnableConfig) -> AgentState:
    rt = _rt(config)
    with _Step(rt, state, "grade", "Check evidence") as step:
        verdict = tasks.grade(
            get_chat_model("fast", rt["settings"]), state["question"], state.get("contexts", [])
        )
        step.detail = {
            "relevant": verdict.relevant,
            "sufficient": verdict.sufficient,
            "missing": verdict.missing,
            "method": verdict.method,
        }
    rt["emit"](
        {
            "type": "verdict",
            "relevant": verdict.relevant,
            "sufficient": verdict.sufficient,
            "missing": verdict.missing,
        }
    )
    return {"verdict": verdict, "attempts": state.get("attempts", 0) + 1, "steps": state.get("steps", [])}


def web_node(state: AgentState, config: RunnableConfig) -> AgentState:
    rt = _rt(config)
    contexts = list(state.get("contexts", []))
    with _Step(rt, state, "web", "Search the web") as step:
        results = web_search(state["query"], rt["settings"], start_n=len(contexts) + 1)
        step.detail = {"results": len(results)}
    contexts.extend(results)
    rt["emit"]({"type": "sources", "sources": [c.public() for c in contexts]})
    return {"contexts": contexts, "used_web": bool(results), "steps": state.get("steps", [])}


def generate_node(state: AgentState, config: RunnableConfig) -> AgentState:
    rt = _rt(config)
    parts: list[str] = []
    with _Step(rt, state, "generate", "Write answer") as step:
        llm = get_chat_model("main", rt["settings"])
        for token in tasks.answer_stream(
            llm, state["question"], state.get("contexts", []), state.get("history", [])
        ):
            parts.append(token)
            rt["emit"]({"type": "token", "text": token})
        step.detail = {"model": llm.label, "chars": sum(len(p) for p in parts)}
    return {"answer": "".join(parts), "steps": state.get("steps", [])}


def after_retrieve(state: AgentState, config: RunnableConfig) -> str:
    return "grade" if _rt(config)["mode"] == "agentic" else "generate"


def after_grade(state: AgentState, config: RunnableConfig) -> str:
    rt = _rt(config)
    verdict = state.get("verdict")
    if verdict is None or verdict.sufficient:
        return "generate"
    if state.get("attempts", 0) <= rt["max_retries"]:
        return "rewrite"
    if rt["web_search"] and rt["settings"].has_key("tavily"):
        return "web"
    return "generate"


@lru_cache(maxsize=1)
def build_graph():
    g = StateGraph(AgentState)
    g.add_node("rewrite", rewrite_node)
    g.add_node("retrieve", retrieve_node)
    g.add_node("grade", grade_node)
    g.add_node("web", web_node)
    g.add_node("generate", generate_node)
    g.add_edge(START, "rewrite")
    g.add_edge("rewrite", "retrieve")
    g.add_conditional_edges("retrieve", after_retrieve, {"grade": "grade", "generate": "generate"})
    g.add_conditional_edges(
        "grade", after_grade, {"generate": "generate", "rewrite": "rewrite", "web": "web"}
    )
    g.add_edge("web", "generate")
    g.add_edge("generate", END)
    return g.compile()
