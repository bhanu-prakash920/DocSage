"""Runtime configuration.

Values resolve in this order (later wins): field defaults, ``.env`` / environment variables,
then ``<data_dir>/settings.json`` written by the Settings page. API keys are only ever read from
the environment and are never written to disk by DocSage.
"""

from __future__ import annotations

import json
import re
import threading
from pathlib import Path
from typing import Any, Literal

from pydantic import AliasChoices, Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

LLMProvider = Literal["auto", "anthropic", "google", "openai", "ollama", "offline"]
EmbeddingProvider = Literal["auto", "google", "openai", "ollama", "local", "hash"]
ParserName = Literal["pymupdf", "llamaparse", "docling"]
ChunkingStrategy = Literal["recursive", "semantic", "agentic", "proposition"]
RerankMode = Literal["none", "llm", "cross-encoder"]
QueryMode = Literal["simple", "agentic"]

# Settings that the web UI may change at runtime. Everything else is deploy-time configuration.
EDITABLE_FIELDS: tuple[str, ...] = (
    "llm_provider",
    "llm_model",
    "llm_fast_model",
    "embedding_provider",
    "embedding_model",
    "embedding_dim",
    "parser",
    "chunking",
    "chunk_size",
    "chunk_overlap",
    "summarize_images",
    "summarize_tables",
    "ocr_tables",
    "graph_rag",
    "query_mode",
    "top_k",
    "hybrid",
    "rerank",
    "parent_expansion",
    "web_search",
    "agent_max_retries",
    "anthropic_effort",
    "max_ingest_cost_usd",
    "max_query_cost_usd",
)

_PLACEHOLDER = re.compile(r"^(\.{2,}|<.*>|x+|changeme|replace.*|todo|none|null|your[_-].*|.*[_-]here)$", re.I)


def is_placeholder(value: str) -> bool:
    """True for empty values and template text such as 'your_api_key_here' or '...'."""
    value = value.strip().strip("'\"")
    return not value or bool(_PLACEHOLDER.match(value))


PARSER_ALIASES = {"pymupdf4llm": "pymupdf", "LlamaParse": "llamaparse", "llama_parse": "llamaparse"}


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="DOCSAGE_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        populate_by_name=True,
    )

    # --- storage -----------------------------------------------------------------------------
    data_dir: Path = Path("data")

    # --- providers -----------------------------------------------------------------------------
    llm_provider: LLMProvider = "auto"
    llm_model: str | None = None
    llm_fast_model: str | None = None
    embedding_provider: EmbeddingProvider = "auto"
    embedding_model: str | None = None
    embedding_dim: int | None = None
    anthropic_effort: Literal["low", "medium", "high", "xhigh", "max"] = "medium"
    anthropic_fallbacks: bool = True
    ollama_base_url: str = "http://localhost:11434"
    local_embedding_model: str = "BAAI/bge-base-en-v1.5"
    cross_encoder_model: str = "BAAI/bge-reranker-base"

    anthropic_api_key: SecretStr | None = Field(
        default=None, validation_alias=AliasChoices("ANTHROPIC_API_KEY", "DOCSAGE_ANTHROPIC_API_KEY")
    )
    google_api_key: SecretStr | None = Field(
        default=None,
        validation_alias=AliasChoices("GOOGLE_API_KEY", "GEMINI_API_KEY", "DOCSAGE_GOOGLE_API_KEY"),
    )
    openai_api_key: SecretStr | None = Field(
        default=None, validation_alias=AliasChoices("OPENAI_API_KEY", "DOCSAGE_OPENAI_API_KEY")
    )
    tavily_api_key: SecretStr | None = Field(
        default=None, validation_alias=AliasChoices("TAVILY_API_KEY", "DOCSAGE_TAVILY_API_KEY")
    )
    llama_cloud_api_key: SecretStr | None = Field(
        default=None, validation_alias=AliasChoices("LLAMA_CLOUD_API_KEY", "DOCSAGE_LLAMA_CLOUD_API_KEY")
    )

    # --- ingestion ----------------------------------------------------------------------------
    parser: ParserName = "pymupdf"
    chunking: ChunkingStrategy = "semantic"
    chunk_size: int = Field(default=1200, ge=200, le=8000)
    chunk_overlap: int = Field(default=150, ge=0, le=2000)
    summarize_images: bool = True
    summarize_tables: bool = True
    ocr_tables: bool = False
    min_image_px: int = 96
    graph_rag: bool = False
    max_upload_mb: int = 100
    max_ingest_cost_usd: float = Field(default=2.0, ge=0)
    llm_concurrency: int = Field(default=4, ge=1, le=32)

    # --- retrieval / answering -----------------------------------------------------------------
    query_mode: QueryMode = "agentic"
    top_k: int = Field(default=6, ge=1, le=30)
    hybrid: bool = True
    rerank: RerankMode = "none"
    parent_expansion: bool = True
    web_search: bool = True
    agent_max_retries: int = Field(default=1, ge=0, le=4)
    max_query_cost_usd: float = Field(default=0.25, ge=0)
    history_turns: int = 6

    # --- server ---------------------------------------------------------------------------------
    host: str = "127.0.0.1"
    port: int = 8000
    api_token: SecretStr | None = None
    cors_origins: list[str] = ["http://localhost:5173", "http://127.0.0.1:5173"]
    log_level: str = "INFO"
    log_json: bool = False

    @field_validator("parser", mode="before")
    @classmethod
    def _parser_alias(cls, v: Any) -> Any:
        return PARSER_ALIASES.get(v, v)

    @field_validator(
        "anthropic_api_key",
        "google_api_key",
        "openai_api_key",
        "tavily_api_key",
        "llama_cloud_api_key",
        "api_token",
        mode="before",
    )
    @classmethod
    def _blank_is_none(cls, v: Any) -> Any:
        if v is None:
            return None
        raw = v.get_secret_value() if isinstance(v, SecretStr) else v
        if isinstance(raw, str) and is_placeholder(raw):
            return None
        return v

    # --- helpers --------------------------------------------------------------------------------
    @property
    def settings_file(self) -> Path:
        return self.data_dir / "settings.json"

    def key(self, name: str) -> str | None:
        secret: SecretStr | None = getattr(self, f"{name}_api_key", None)
        return secret.get_secret_value() if secret else None

    def has_key(self, name: str) -> bool:
        return bool(self.key(name))

    def editable_dict(self) -> dict[str, Any]:
        return {name: getattr(self, name) for name in EDITABLE_FIELDS}


_lock = threading.RLock()
_settings: Settings | None = None


def _load_overrides(base: Settings) -> dict[str, Any]:
    path = base.settings_file
    if not path.exists():
        return {}
    try:
        raw = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return {}
    return {k: v for k, v in raw.items() if k in EDITABLE_FIELDS}


def get_settings() -> Settings:
    global _settings
    with _lock:
        if _settings is None:
            base = Settings()
            overrides = _load_overrides(base)
            _settings = base.model_copy(update=overrides) if overrides else base
            if overrides:
                # Re-validate overrides so a hand-edited settings.json cannot inject bad values.
                _settings = Settings.model_validate({**base.model_dump(), **overrides})
        return _settings


def configure(**kwargs: Any) -> Settings:
    """Replace the process-wide settings (used by the CLI and tests)."""
    global _settings
    with _lock:
        base = Settings(**kwargs)
        overrides = _load_overrides(base)
        _settings = (
            Settings.model_validate({**base.model_dump(), **overrides, **kwargs}) if overrides else base
        )
        return _settings


def update_settings(changes: dict[str, Any]) -> Settings:
    """Validate and persist runtime-editable settings."""
    global _settings
    unknown = set(changes) - set(EDITABLE_FIELDS)
    if unknown:
        raise ValueError(f"Not editable at runtime: {', '.join(sorted(unknown))}")
    with _lock:
        current = get_settings()
        merged = Settings.model_validate({**current.model_dump(), **changes})
        persisted = _load_overrides(current)
        persisted.update({k: getattr(merged, k) for k in changes})
        merged.data_dir.mkdir(parents=True, exist_ok=True)
        merged.settings_file.write_text(json.dumps(persisted, indent=2, default=str))
        _settings = merged
        return merged


def reset_settings_cache() -> None:
    global _settings
    with _lock:
        _settings = None
