"""Provider-neutral interfaces for chat and embedding models, plus usage metering."""

from __future__ import annotations

import contextlib
import contextvars
import json
import re
import threading
from abc import ABC, abstractmethod
from collections.abc import Iterator, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal, TypeVar

import numpy as np
from pydantic import BaseModel, ValidationError

from ..errors import BudgetExceeded, ProviderError
from .pricing import cost_of, price_for

T = TypeVar("T", bound=BaseModel)


# ----------------------------------------------------------------------------------------------
# Messages
# ----------------------------------------------------------------------------------------------
@dataclass(frozen=True)
class ImagePart:
    data: bytes
    mime_type: str = "image/png"


@dataclass
class Message:
    role: Literal["user", "assistant"]
    content: str | list[str | ImagePart]

    def parts(self) -> list[str | ImagePart]:
        return [self.content] if isinstance(self.content, str) else list(self.content)

    def text(self) -> str:
        return "\n".join(p for p in self.parts() if isinstance(p, str))


def user(*parts: str | ImagePart) -> Message:
    return Message("user", parts[0] if len(parts) == 1 and isinstance(parts[0], str) else list(parts))


# ----------------------------------------------------------------------------------------------
# Usage metering
# ----------------------------------------------------------------------------------------------
@dataclass
class UsageMeter:
    """Thread-safe token and cost accumulator with an optional budget."""

    budget_usd: float | None = None
    label: str = "operation"
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    calls: int = 0
    unpriced_models: set[str] = field(default_factory=set)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def check(self) -> None:
        if self.budget_usd is not None and self.budget_usd > 0 and self.cost_usd > self.budget_usd:
            raise BudgetExceeded(
                f"{self.label.capitalize()} stopped at ${self.cost_usd:.4f}, over the ${self.budget_usd:.2f} budget.",
                hint="Raise the budget in Settings or choose a cheaper chunking strategy or model.",
            )

    def record(self, model: str, input_tokens: int, output_tokens: int) -> None:
        with self._lock:
            self.calls += 1
            self.input_tokens += int(input_tokens or 0)
            self.output_tokens += int(output_tokens or 0)
            self.cost_usd += cost_of(model, input_tokens or 0, output_tokens or 0)
            if price_for(model) is None:
                self.unpriced_models.add(model)
        self.check()

    def snapshot(self) -> dict[str, Any]:
        return {
            "calls": self.calls,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "cost_usd": round(self.cost_usd, 6),
            "unpriced_models": sorted(self.unpriced_models),
        }


_current_meter: contextvars.ContextVar[UsageMeter | None] = contextvars.ContextVar("meter", default=None)


@contextlib.contextmanager
def metering(meter: UsageMeter) -> Iterator[UsageMeter]:
    token = _current_meter.set(meter)
    try:
        yield meter
    finally:
        _current_meter.reset(token)


def current_meter() -> UsageMeter | None:
    return _current_meter.get()


def record_usage(model: str, input_tokens: int, output_tokens: int) -> None:
    meter = current_meter()
    if meter is not None:
        meter.record(model, input_tokens, output_tokens)


def check_budget() -> None:
    meter = current_meter()
    if meter is not None:
        meter.check()


# ----------------------------------------------------------------------------------------------
# Chat models
# ----------------------------------------------------------------------------------------------
_JSON_BLOCK = re.compile(r"\{.*\}|\[.*\]", re.S)


def parse_json_text(text: str) -> Any:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\n?|```$", "", text).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        match = _JSON_BLOCK.search(text)
        if not match:
            raise
        return json.loads(match.group(0))


def strict_schema(model: type[BaseModel]) -> dict[str, Any]:
    """JSON schema with ``additionalProperties: false`` and every property required."""

    schema = model.model_json_schema()

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            if node.get("type") == "object" and "properties" in node:
                node["additionalProperties"] = False
                node["required"] = list(node["properties"].keys())
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    walk(schema)
    return schema


class ChatModel(ABC):
    provider: str = "base"
    #: False for the offline provider, which cannot follow open-ended instructions.
    generative: bool = True
    supports_vision: bool = True

    def __init__(self, model: str):
        self.model = model

    @property
    def label(self) -> str:
        return f"{self.provider}:{self.model}"

    @abstractmethod
    def generate(
        self,
        messages: Sequence[Message],
        *,
        system: str | None = None,
        max_tokens: int = 4096,
        json_schema: dict[str, Any] | None = None,
    ) -> str:
        """Return the model's text reply. Usage is recorded on the current meter."""

    def stream(
        self, messages: Sequence[Message], *, system: str | None = None, max_tokens: int = 8192
    ) -> Iterator[str]:
        yield self.generate(messages, system=system, max_tokens=max_tokens)

    def generate_json(
        self,
        messages: Sequence[Message],
        schema: type[T],
        *,
        system: str | None = None,
        max_tokens: int = 4096,
    ) -> T:
        js = strict_schema(schema)
        text = self.generate(messages, system=system, max_tokens=max_tokens, json_schema=js)
        try:
            return schema.model_validate(parse_json_text(text))
        except (json.JSONDecodeError, ValidationError) as first_error:
            # One repair attempt: show the model its own output and the validation error.
            repair = [
                *messages,
                Message("assistant", text),
                Message(
                    "user",
                    f"That was not valid JSON for the schema ({first_error}). Reply with only the corrected JSON.",
                ),
            ]
            text = self.generate(repair, system=system, max_tokens=max_tokens, json_schema=js)
            try:
                return schema.model_validate(parse_json_text(text))
            except (json.JSONDecodeError, ValidationError) as exc:
                raise ProviderError(f"{self.label} returned malformed structured output.") from exc


# ----------------------------------------------------------------------------------------------
# Embedding models
# ----------------------------------------------------------------------------------------------
class EmbeddingModel(ABC):
    provider: str = "base"
    batch_size: int = 64

    def __init__(self, model: str, dim: int):
        self.model = model
        self.dim = dim

    @property
    def label(self) -> str:
        return f"{self.provider}:{self.model}"

    @abstractmethod
    def _embed(self, texts: list[str], *, query: bool) -> list[list[float]]: ...

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        out: list[list[float]] = []
        items = [t if t.strip() else " " for t in texts]
        for i in range(0, len(items), self.batch_size):
            check_budget()
            out.extend(self._embed(items[i : i + self.batch_size], query=False))
        return [normalize(v) for v in out]

    def embed_query(self, text: str) -> list[float]:
        return normalize(self._embed([text or " "], query=True)[0])


def normalize(vec: Sequence[float]) -> list[float]:
    arr = np.asarray(vec, dtype=np.float32)
    norm = float(np.linalg.norm(arr))
    if norm == 0:
        return arr.tolist()
    return (arr / norm).tolist()


def approx_tokens(text: str) -> int:
    return max(1, len(text) // 4)
