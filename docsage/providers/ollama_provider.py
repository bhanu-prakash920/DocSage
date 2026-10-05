"""Local models through an Ollama server's REST API."""

from __future__ import annotations

import base64
import json
from collections.abc import Iterator, Sequence
from typing import Any

import httpx
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential_jitter

from ..errors import ProviderError
from .base import ChatModel, EmbeddingModel, ImagePart, Message, record_usage

_retry = retry(
    retry=retry_if_exception_type((httpx.ConnectError, httpx.ReadTimeout)),
    stop=stop_after_attempt(3),
    wait=wait_exponential_jitter(initial=1, max=10),
    reraise=True,
)


def _messages(messages: Sequence[Message], system: str | None) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = [{"role": "system", "content": system}] if system else []
    for m in messages:
        images = [base64.b64encode(p.data).decode("ascii") for p in m.parts() if isinstance(p, ImagePart)]
        entry: dict[str, Any] = {"role": m.role, "content": m.text()}
        if images:
            entry["images"] = images
        out.append(entry)
    return out


def _wrap(exc: Exception, base_url: str, model: str) -> ProviderError:
    if isinstance(exc, httpx.ConnectError):
        return ProviderError(f"Could not reach Ollama at {base_url}.", hint="Start it with `ollama serve`.")
    if isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code == 404:
        return ProviderError(f"Ollama model '{model}' is not pulled.", hint=f"Run `ollama pull {model}`.")
    return ProviderError(f"Ollama request failed: {exc}")


class OllamaChat(ChatModel):
    provider = "ollama"

    def __init__(self, model: str, base_url: str):
        super().__init__(model)
        self.base_url = base_url.rstrip("/")
        self.http = httpx.Client(base_url=self.base_url, timeout=300.0)

    def generate(self, messages, *, system=None, max_tokens=8192, json_schema=None) -> str:
        body: dict[str, Any] = {
            "model": self.model,
            "messages": _messages(messages, system),
            "stream": False,
            "options": {"num_predict": max_tokens},
        }
        if json_schema is not None:
            body["format"] = json_schema

        @_retry
        def call() -> dict[str, Any]:
            r = self.http.post("/api/chat", json=body)
            r.raise_for_status()
            return r.json()

        try:
            data = call()
        except httpx.HTTPError as exc:
            raise _wrap(exc, self.base_url, self.model) from exc
        record_usage(self.model, data.get("prompt_eval_count", 0), data.get("eval_count", 0))
        return data.get("message", {}).get("content", "")

    def stream(self, messages, *, system=None, max_tokens=8192) -> Iterator[str]:
        body = {
            "model": self.model,
            "messages": _messages(messages, system),
            "stream": True,
            "options": {"num_predict": max_tokens},
        }
        try:
            with self.http.stream("POST", "/api/chat", json=body) as r:
                r.raise_for_status()
                for line in r.iter_lines():
                    if not line:
                        continue
                    data = json.loads(line)
                    if data.get("done"):
                        record_usage(self.model, data.get("prompt_eval_count", 0), data.get("eval_count", 0))
                    text = data.get("message", {}).get("content")
                    if text:
                        yield text
        except httpx.HTTPError as exc:
            raise _wrap(exc, self.base_url, self.model) from exc


class OllamaEmbeddings(EmbeddingModel):
    provider = "ollama"
    batch_size = 32

    def __init__(self, model: str, dim: int, base_url: str):
        super().__init__(model, dim)
        self.base_url = base_url.rstrip("/")
        self.http = httpx.Client(base_url=self.base_url, timeout=300.0)

    def _embed(self, texts: list[str], *, query: bool) -> list[list[float]]:
        try:
            r = self.http.post("/api/embed", json={"model": self.model, "input": texts})
            r.raise_for_status()
        except httpx.HTTPError as exc:
            raise _wrap(exc, self.base_url, self.model) from exc
        return r.json()["embeddings"]
