"""Gemini via the google-genai SDK."""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from typing import Any, cast

from google import genai
from google.genai import errors, types
from tenacity import retry, retry_if_exception, stop_after_attempt, wait_exponential_jitter

from ..errors import ProviderError
from .base import ChatModel, EmbeddingModel, ImagePart, Message, approx_tokens, record_usage


def _transient(exc: BaseException) -> bool:
    if isinstance(exc, errors.APIError):
        return exc.code in (408, 409, 429) or (exc.code or 0) >= 500
    return isinstance(exc, (ConnectionError, TimeoutError))


_retry = retry(
    retry=retry_if_exception(_transient),
    stop=stop_after_attempt(5),
    wait=wait_exponential_jitter(initial=1, max=30),
    reraise=True,
)


def _map_error(exc: Exception, model: str) -> ProviderError:
    if isinstance(exc, errors.APIError):
        if exc.code in (401, 403):
            return ProviderError("Google rejected the API key.", hint="Check GOOGLE_API_KEY in .env.")
        if exc.code == 404:
            return ProviderError(
                f"Gemini model '{model}' was not found or has been retired.",
                hint="Change the model in Settings.",
            )
        if exc.code == 429:
            return ProviderError("Gemini rate limit or quota reached after retries.")
        return ProviderError(f"Gemini API error {exc.code}: {exc.message}")
    return ProviderError(f"Gemini request failed: {exc}")


def _contents(messages: Sequence[Message]) -> list[types.Content]:
    out = []
    for m in messages:
        parts = []
        for p in m.parts():
            if isinstance(p, ImagePart):
                parts.append(types.Part.from_bytes(data=p.data, mime_type=p.mime_type))
            elif p:
                parts.append(types.Part.from_text(text=p))
        out.append(types.Content(role="model" if m.role == "assistant" else "user", parts=parts))
    return out


class GoogleChat(ChatModel):
    provider = "google"

    def __init__(self, model: str, api_key: str):
        super().__init__(model)
        self.client = genai.Client(api_key=api_key)

    def _config(self, system: str | None, max_tokens: int, json_schema: dict[str, Any] | None):
        kwargs: dict[str, Any] = {"max_output_tokens": max_tokens}
        if system:
            kwargs["system_instruction"] = system
        if json_schema is not None:
            kwargs["response_mime_type"] = "application/json"
            kwargs["response_json_schema"] = json_schema
        return types.GenerateContentConfig(**kwargs)

    def _record(self, usage: Any) -> None:
        if usage is None:
            return
        output = (usage.candidates_token_count or 0) + (getattr(usage, "thoughts_token_count", 0) or 0)
        record_usage(self.model, usage.prompt_token_count or 0, output)

    def generate(self, messages, *, system=None, max_tokens=8192, json_schema=None) -> str:
        @_retry
        def call():
            return self.client.models.generate_content(
                model=self.model,
                contents=cast(Any, _contents(messages)),
                config=self._config(system, max_tokens, json_schema),
            )

        try:
            response = call()
        except Exception as exc:
            raise _map_error(exc, self.model) from exc
        self._record(response.usage_metadata)
        return response.text or ""

    def stream(self, messages, *, system=None, max_tokens=8192) -> Iterator[str]:
        usage = None
        try:
            for chunk in self.client.models.generate_content_stream(
                model=self.model,
                contents=cast(Any, _contents(messages)),
                config=self._config(system, max_tokens, None),
            ):
                usage = chunk.usage_metadata or usage
                if chunk.text:
                    yield chunk.text
        except Exception as exc:
            raise _map_error(exc, self.model) from exc
        self._record(usage)


class GoogleEmbeddings(EmbeddingModel):
    provider = "google"
    batch_size = 96

    def __init__(self, model: str, dim: int, api_key: str):
        super().__init__(model, dim)
        self.client = genai.Client(api_key=api_key)

    def _embed(self, texts: list[str], *, query: bool) -> list[list[float]]:
        @_retry
        def call():
            return self.client.models.embed_content(
                model=self.model,
                contents=cast(Any, texts),
                config=types.EmbedContentConfig(
                    output_dimensionality=self.dim,
                    task_type="RETRIEVAL_QUERY" if query else "RETRIEVAL_DOCUMENT",
                ),
            )

        try:
            response = call()
        except Exception as exc:
            raise _map_error(exc, self.model) from exc
        record_usage(self.model, sum(approx_tokens(t) for t in texts), 0)
        return [list(e.values or []) for e in (response.embeddings or [])]
