"""OpenAI (or any OpenAI-compatible endpoint) via the official openai SDK."""

from __future__ import annotations

import base64
from collections.abc import Iterator, Sequence
from typing import Any, cast

import openai

from ..errors import ProviderError
from .base import ChatModel, EmbeddingModel, ImagePart, Message, record_usage


def _map_error(exc: Exception, model: str) -> ProviderError:
    if isinstance(exc, openai.AuthenticationError):
        return ProviderError("OpenAI rejected the API key.", hint="Check OPENAI_API_KEY in .env.")
    if isinstance(exc, openai.NotFoundError):
        return ProviderError(f"OpenAI model '{model}' was not found.", hint="Change the model in Settings.")
    if isinstance(exc, openai.RateLimitError):
        return ProviderError("OpenAI rate limit or quota reached after retries.")
    if isinstance(exc, openai.APIConnectionError):
        return ProviderError("Could not reach the OpenAI API.")
    return ProviderError(f"OpenAI API error: {exc}")


def _messages(messages: Sequence[Message], system: str | None) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = [{"role": "system", "content": system}] if system else []
    for m in messages:
        if isinstance(m.content, str):
            out.append({"role": m.role, "content": m.content})
            continue
        parts: list[dict[str, Any]] = []
        for p in m.parts():
            if isinstance(p, ImagePart):
                url = f"data:{p.mime_type};base64,{base64.b64encode(p.data).decode('ascii')}"
                parts.append({"type": "image_url", "image_url": {"url": url}})
            elif p:
                parts.append({"type": "text", "text": p})
        out.append({"role": m.role, "content": parts})
    return out


class OpenAIChat(ChatModel):
    provider = "openai"

    def __init__(self, model: str, api_key: str, base_url: str | None = None):
        super().__init__(model)
        self.client = openai.OpenAI(api_key=api_key, base_url=base_url, max_retries=4, timeout=180.0)

    def generate(self, messages, *, system=None, max_tokens=8192, json_schema=None) -> str:
        kwargs: dict[str, Any] = {}
        if json_schema is not None:
            kwargs["response_format"] = {
                "type": "json_schema",
                "json_schema": {"name": "output", "schema": json_schema, "strict": True},
            }
        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=cast(Any, _messages(messages, system)),
                max_completion_tokens=max_tokens,
                **kwargs,
            )
        except openai.OpenAIError as exc:
            raise _map_error(exc, self.model) from exc
        if response.usage:
            record_usage(self.model, response.usage.prompt_tokens, response.usage.completion_tokens)
        return response.choices[0].message.content or ""

    def stream(self, messages, *, system=None, max_tokens=8192) -> Iterator[str]:
        try:
            stream: Any = self.client.chat.completions.create(
                model=self.model,
                messages=cast(Any, _messages(messages, system)),
                max_completion_tokens=max_tokens,
                stream=True,
                stream_options={"include_usage": True},
            )
            for chunk in stream:
                if chunk.usage:
                    record_usage(self.model, chunk.usage.prompt_tokens, chunk.usage.completion_tokens)
                if chunk.choices and chunk.choices[0].delta.content:
                    yield chunk.choices[0].delta.content
        except openai.OpenAIError as exc:
            raise _map_error(exc, self.model) from exc


class OpenAIEmbeddings(EmbeddingModel):
    provider = "openai"
    batch_size = 128

    def __init__(self, model: str, dim: int, api_key: str):
        super().__init__(model, dim)
        self.client = openai.OpenAI(api_key=api_key, max_retries=4)

    def _embed(self, texts: list[str], *, query: bool) -> list[list[float]]:
        try:
            response = self.client.embeddings.create(model=self.model, input=texts, dimensions=self.dim)
        except openai.OpenAIError as exc:
            raise _map_error(exc, self.model) from exc
        record_usage(self.model, response.usage.prompt_tokens, 0)
        return [d.embedding for d in sorted(response.data, key=lambda d: d.index)]
