"""A scripted chat model that exercises every generative code path without network access."""

from __future__ import annotations

import json
import re
from collections.abc import Iterator, Sequence
from typing import Any

from docsage.providers.base import ChatModel, ImagePart, Message, record_usage
from docsage.tasks import prompt


class FakeChat(ChatModel):
    provider = "fake"

    def __init__(
        self, sufficient: bool | list[bool] = True, answer: str = "Storage revenue was 79 million euros [1]."
    ):
        super().__init__("fake-1")
        self.sufficient = sufficient if isinstance(sufficient, list) else [sufficient]
        self.answer = answer
        self.calls: list[str] = []
        self.images_seen = 0

    def _task(self, system: str | None) -> str:
        for name in (
            "rewrite",
            "grade",
            "describe_image",
            "describe_table",
            "agentic_chunk",
            "propositions",
            "allocate",
            "chunk_summary",
            "rerank",
            "entities",
            "judge",
            "eval_questions",
            "answer",
        ):
            if system == prompt(name):
                return name
        return "unknown"

    def generate(self, messages: Sequence[Message], *, system=None, max_tokens=4096, json_schema=None) -> str:
        task = self._task(system)
        self.calls.append(task)
        record_usage("claude-haiku-4-5", 100, 20)
        text = messages[-1].text()
        if any(isinstance(p, ImagePart) for m in messages for p in m.parts()):
            self.images_seen += 1
        if task == "rewrite":
            latest = text.split("Latest message:", 1)[-1].split("\n")[0].strip()
            return json.dumps({"query": f"{latest} (rewritten)"})
        if task == "grade":
            ok = self.sufficient.pop(0) if len(self.sufficient) > 1 else self.sufficient[0]
            return json.dumps(
                {
                    "relevant": True,
                    "sufficient": ok,
                    "missing": "" if ok else "segment revenue",
                    "useful_sources": [1],
                }
            )
        if task == "describe_image":
            return (
                "A bar chart of installed capacity: Wind 1240 MW, Solar 610 MW, Storage 455 MW, Gas 300 MW."
            )
        if task == "describe_table":
            return "Revenue by segment for 2024 and 2025."
        if task == "agentic_chunk":
            n = len(re.findall(r"^\[\d+\]", text, re.M))
            groups = [{"title": f"Topic {i}", "start": i, "end": min(n, i + 1)} for i in range(1, n + 1, 2)]
            return json.dumps({"groups": groups})
        if task == "propositions":
            sents = [s.strip() for s in re.split(r"(?<=\.)\s+", text) if len(s.strip()) > 10][:4]
            return json.dumps({"propositions": sents})
        if task == "allocate":
            return json.dumps({"chunk_id": 1})
        if task == "chunk_summary":
            return json.dumps({"title": "Topic", "summary": "A topic."})
        if task == "rerank":
            n = len(re.findall(r"^\[\d+\]", text.split("Passages:", 1)[-1], re.M))
            return json.dumps(
                {"scores": [{"id": i, "score": 3 if "79" in text else 1} for i in range(1, n + 1)]}
            )
        if task == "entities":
            n = len(re.findall(r"^\[\d+\]", text, re.M))
            return json.dumps({"items": [{"id": i, "entities": ["Halcyon Grid"]} for i in range(1, n + 1)]})
        if task == "judge":
            return json.dumps({"faithfulness": 0.9, "relevance": 0.8, "correctness": 0.7, "notes": "ok"})
        if task == "eval_questions":
            n = len(re.findall(r"^\[\d+\]", text, re.M))
            return json.dumps(
                {
                    "items": [
                        {"id": i, "question": f"Question {i}?", "answer": f"Answer {i}"}
                        for i in range(1, n + 1)
                    ]
                }
            )
        return "ok"

    def stream(self, messages, *, system=None, max_tokens=8192) -> Iterator[str]:
        self.calls.append("answer")
        record_usage("claude-opus-5-5", 500, 40)
        yield from (self.answer[:10], self.answer[10:])


def fake_web(query: str, settings: Any, start_n: int, max_results: int = 4):
    from docsage.retrieval.types import Context

    return [
        Context(
            n=start_n,
            chunk_id=None,
            document_id=None,
            filename="",
            page=None,
            modality="web",
            text="Web result about segment revenue.",
            title="Example",
            url="https://example.com",
        )
    ]
