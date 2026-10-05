"""Claude via the official Anthropic SDK."""

from __future__ import annotations

import base64
from collections.abc import Iterator, Sequence
from typing import Any

import anthropic

from ..errors import ProviderError
from .base import ChatModel, ImagePart, Message, record_usage

# Models that accept the server-side refusal fallback ("default" routing).
_FALLBACK_MODELS = ("claude-fable-5-1", "claude-opus-5-5", "claude-opus-5", "claude-sonnet-5-5")
_FALLBACK_BETA = "server-side-fallback-2026-07-01"


def _supports_effort(model: str) -> bool:
    return not model.startswith("claude-haiku")


def _content(message: Message) -> list[dict[str, Any]]:
    blocks: list[dict[str, Any]] = []
    for part in message.parts():
        if isinstance(part, ImagePart):
            blocks.append(
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": part.mime_type,
                        "data": base64.standard_b64encode(part.data).decode("ascii"),
                    },
                }
            )
        elif part:
            blocks.append({"type": "text", "text": part})
    return blocks or [{"type": "text", "text": " "}]


class AnthropicChat(ChatModel):
    provider = "anthropic"

    def __init__(self, model: str, api_key: str, *, effort: str = "medium", fallbacks: bool = True):
        super().__init__(model)
        self.client = anthropic.Anthropic(api_key=api_key, max_retries=4, timeout=180.0)
        self.effort = effort
        self.use_fallbacks = fallbacks and model.startswith(_FALLBACK_MODELS)

    def _request(
        self,
        messages: Sequence[Message],
        system: str | None,
        max_tokens: int,
        json_schema: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        kwargs: dict[str, Any] = {
            "model": self.model,
            "max_tokens": max_tokens,
            "messages": [{"role": m.role, "content": _content(m)} for m in messages],
        }
        if system:
            kwargs["system"] = system
        output_config: dict[str, Any] = {}
        if _supports_effort(self.model):
            output_config["effort"] = self.effort
        if json_schema is not None:
            output_config["format"] = {"type": "json_schema", "schema": json_schema}
        if output_config:
            kwargs["output_config"] = output_config
        if self.use_fallbacks:
            kwargs["betas"] = [_FALLBACK_BETA]
            kwargs["fallbacks"] = "default"
        return kwargs

    def _messages_api(self) -> Any:
        return self.client.beta.messages if self.use_fallbacks else self.client.messages

    def _record(self, response: Any) -> None:
        usage = response.usage
        input_tokens = (
            (usage.input_tokens or 0)
            + (getattr(usage, "cache_creation_input_tokens", 0) or 0)
            + (getattr(usage, "cache_read_input_tokens", 0) or 0)
        )
        record_usage(getattr(response, "model", None) or self.model, input_tokens, usage.output_tokens or 0)

    @staticmethod
    def _check_refusal(response: Any) -> None:
        if response.stop_reason == "refusal":
            details = getattr(response, "stop_details", None)
            category = getattr(details, "category", None) if details else None
            raise ProviderError(
                "Claude declined to answer this request" + (f" ({category})." if category else "."),
            )

    def generate(
        self,
        messages: Sequence[Message],
        *,
        system: str | None = None,
        max_tokens: int = 16000,
        json_schema: dict[str, Any] | None = None,
    ) -> str:
        try:
            response = self._messages_api().create(**self._request(messages, system, max_tokens, json_schema))
        except anthropic.APIError as exc:
            raise _map_error(exc, self.model) from exc
        self._record(response)
        self._check_refusal(response)
        return "".join(b.text for b in response.content if getattr(b, "type", "") == "text")

    def stream(
        self, messages: Sequence[Message], *, system: str | None = None, max_tokens: int = 32000
    ) -> Iterator[str]:
        try:
            with self._messages_api().stream(**self._request(messages, system, max_tokens)) as stream:
                yield from stream.text_stream
                final = stream.get_final_message()
        except anthropic.APIError as exc:
            raise _map_error(exc, self.model) from exc
        self._record(final)
        self._check_refusal(final)


def _map_error(exc: anthropic.APIError, model: str) -> ProviderError:
    if isinstance(exc, anthropic.AuthenticationError):
        return ProviderError("Anthropic rejected the API key.", hint="Check ANTHROPIC_API_KEY in .env.")
    if isinstance(exc, anthropic.PermissionDeniedError):
        return ProviderError(f"This Anthropic key cannot use {model}.")
    if isinstance(exc, anthropic.NotFoundError):
        return ProviderError(
            f"Anthropic model '{model}' was not found.", hint="Change the model in Settings."
        )
    if isinstance(exc, anthropic.RateLimitError):
        return ProviderError("Anthropic rate limit reached after retries. Try again shortly.")
    if isinstance(exc, anthropic.BadRequestError):
        return ProviderError(f"Anthropic rejected the request: {exc.message}")
    if isinstance(exc, anthropic.APIConnectionError):
        return ProviderError("Could not reach the Anthropic API. Check the network connection.")
    return ProviderError(f"Anthropic API error: {exc}")
