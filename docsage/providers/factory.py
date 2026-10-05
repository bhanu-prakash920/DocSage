"""Resolve configured providers into model instances."""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from typing import Any, Literal

from ..config import Settings, get_settings
from ..errors import ConfigurationError, ProviderError
from .base import ChatModel, EmbeddingModel
from .offline_provider import HashEmbeddings, OfflineChat, local_embeddings_available

log = logging.getLogger(__name__)

Tier = Literal["main", "fast"]

DEFAULT_CHAT_MODELS: dict[str, tuple[str, str]] = {
    # provider: (main model for answers, fast model for bulk ingestion, grading and rewriting)
    "anthropic": ("claude-opus-5-5", "claude-haiku-4-5"),
    # Rolling aliases avoid hard shutdowns of pinned model versions.
    "google": ("gemini-flash-latest", "gemini-flash-lite-latest"),
    "openai": ("gpt-5.4", "gpt-5.4-mini"),
    "ollama": ("llama3.2-vision", "llama3.2-vision"),
    "offline": ("extractive", "extractive"),
}

DEFAULT_EMBEDDINGS: dict[str, tuple[str, int]] = {
    "google": ("gemini-embedding-001", 768),
    "openai": ("text-embedding-3-small", 1536),
    "ollama": ("nomic-embed-text", 768),
    "local": ("BAAI/bge-base-en-v1.5", 768),
    "hash": ("feature-hash-v1", 512),
}


def resolve_llm_provider(s: Settings) -> str:
    if s.llm_provider != "auto":
        return s.llm_provider
    for name in ("anthropic", "google", "openai"):
        if s.has_key(name):
            return name
    return "offline"


def resolve_embedding_provider(s: Settings) -> str:
    if s.embedding_provider != "auto":
        return s.embedding_provider
    for name in ("google", "openai"):
        if s.has_key(name):
            return name
    return "local" if local_embeddings_available() else "hash"


@dataclass(frozen=True)
class EmbeddingSpec:
    provider: str
    model: str
    dim: int

    def as_dict(self) -> dict[str, Any]:
        return {"provider": self.provider, "model": self.model, "dim": self.dim}


def default_embedding_spec(s: Settings | None = None) -> EmbeddingSpec:
    s = s or get_settings()
    provider = resolve_embedding_provider(s)
    model, dim = DEFAULT_EMBEDDINGS[provider]
    if s.embedding_provider != "auto" or s.embedding_model:
        model = s.embedding_model or model
    if provider == "local" and not s.embedding_model:
        model = s.local_embedding_model
    return EmbeddingSpec(provider, model, s.embedding_dim or dim)


_cache: dict[tuple[Any, ...], Any] = {}
_cache_lock = threading.Lock()


def _require_key(s: Settings, name: str) -> str:
    key = s.key(name)
    if not key:
        raise ConfigurationError(
            f"The {name} provider is selected but {name.upper()}_API_KEY is not set.",
            hint="Add the key to .env or pick another provider in Settings.",
        )
    return key


_chat_override: ChatModel | None = None


def set_chat_override(model: ChatModel | None) -> None:
    """Force every chat lookup to return ``model`` (used by tests and evaluation harnesses)."""
    global _chat_override
    _chat_override = model


def get_chat_model(tier: Tier = "main", s: Settings | None = None) -> ChatModel:
    s = s or get_settings()
    if _chat_override is not None and s.llm_provider != "offline":
        return _chat_override
    provider = resolve_llm_provider(s)
    main_default, fast_default = DEFAULT_CHAT_MODELS[provider]
    if s.llm_provider == "auto" and provider != "offline":
        # Explicit model names only apply when the provider was chosen explicitly or matches.
        model = (s.llm_model if tier == "main" else s.llm_fast_model) or None
        model = model if model and _model_matches(provider, model) else None
    else:
        model = s.llm_model if tier == "main" else s.llm_fast_model
    model = model or (main_default if tier == "main" else fast_default)
    key = (
        provider,
        model,
        s.anthropic_effort,
        s.anthropic_fallbacks,
        s.key(provider) if provider in ("anthropic", "google", "openai") else s.ollama_base_url,
    )
    with _cache_lock:
        if key in _cache:
            return _cache[key]
        instance = _build_chat(provider, model, s)
        _cache[key] = instance
        return instance


def _model_matches(provider: str, model: str) -> bool:
    prefixes = {"anthropic": ("claude",), "google": ("gemini", "models/"), "openai": ("gpt", "o")}
    return model.startswith(prefixes.get(provider, ("",)))


def _build_chat(provider: str, model: str, s: Settings) -> ChatModel:
    if provider == "anthropic":
        from .anthropic_provider import AnthropicChat

        return AnthropicChat(
            model, _require_key(s, "anthropic"), effort=s.anthropic_effort, fallbacks=s.anthropic_fallbacks
        )
    if provider == "google":
        from .google_provider import GoogleChat

        return GoogleChat(model, _require_key(s, "google"))
    if provider == "openai":
        from .openai_provider import OpenAIChat

        return OpenAIChat(model, _require_key(s, "openai"))
    if provider == "ollama":
        from .ollama_provider import OllamaChat

        return OllamaChat(model, s.ollama_base_url)
    if provider == "offline":
        return OfflineChat(model)
    raise ConfigurationError(f"Unknown LLM provider '{provider}'.")


def get_embedding_model(
    spec: EmbeddingSpec | dict[str, Any] | None = None, s: Settings | None = None
) -> EmbeddingModel:
    s = s or get_settings()
    if spec is None:
        spec = default_embedding_spec(s)
    elif isinstance(spec, dict):
        spec = EmbeddingSpec(spec["provider"], spec["model"], int(spec["dim"]))
    key = ("emb", spec, s.key(spec.provider) if spec.provider in ("google", "openai") else None)
    with _cache_lock:
        if key in _cache:
            return _cache[key]
        instance = _build_embeddings(spec, s)
        if instance.dim != spec.dim:
            raise ProviderError(
                f"Embedding model {spec.model} returns {instance.dim} dimensions, "
                f"but this collection expects {spec.dim}."
            )
        _cache[key] = instance
        return instance


def _build_embeddings(spec: EmbeddingSpec, s: Settings) -> EmbeddingModel:
    if spec.provider == "google":
        from .google_provider import GoogleEmbeddings

        return GoogleEmbeddings(spec.model, spec.dim, _require_key(s, "google"))
    if spec.provider == "openai":
        from .openai_provider import OpenAIEmbeddings

        return OpenAIEmbeddings(spec.model, spec.dim, _require_key(s, "openai"))
    if spec.provider == "ollama":
        from .ollama_provider import OllamaEmbeddings

        return OllamaEmbeddings(spec.model, spec.dim, s.ollama_base_url)
    if spec.provider == "local":
        from .offline_provider import LocalEmbeddings

        return LocalEmbeddings(spec.model, spec.dim)
    if spec.provider == "hash":
        return HashEmbeddings(spec.model, spec.dim)
    raise ConfigurationError(f"Unknown embedding provider '{spec.provider}'.")


def clear_cache() -> None:
    with _cache_lock:
        _cache.clear()


def provider_status(s: Settings | None = None) -> dict[str, Any]:
    s = s or get_settings()
    llm = resolve_llm_provider(s)
    status: dict[str, Any] = {
        "llm_provider": llm,
        "llm_model": None,
        "llm_fast_model": None,
        "generative": llm != "offline",
        "embedding": default_embedding_spec(s).as_dict(),
        "keys": {
            name: s.has_key(name) for name in ("anthropic", "google", "openai", "tavily", "llama_cloud")
        },
        "web_search_available": s.has_key("tavily"),
        "local_embeddings_available": local_embeddings_available(),
        "error": None,
    }
    try:
        status["llm_model"] = get_chat_model("main", s).model
        status["llm_fast_model"] = get_chat_model("fast", s).model
    except (ConfigurationError, ProviderError) as exc:
        status["error"] = exc.message
    return status
