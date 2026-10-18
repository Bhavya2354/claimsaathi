"""Typed settings, read from the environment (and `.env` in local development).

Each component loads only the section it needs, so a process fails fast on the
settings it actually depends on. Environment-specific values (paths, URLs,
credentials, model names) have no defaults; only operational tunables do.
"""

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, HttpUrl, SecretStr, ValidationError, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.errors import ConfigurationError


class _Section(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        frozen=True,
    )


class RuntimeSettings(_Section):
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    log_format: Literal["json", "console"] = "json"


class CaseSettings(_Section):
    """Upload limits and retention. Raw files are never written to disk."""

    max_upload_mb: int = Field(default=15, ge=1)
    max_pdf_pages: int = Field(default=150, ge=1)
    max_documents_per_case: int = Field(default=12, ge=1)
    case_ttl_hours: int = Field(default=24, ge=1, description="Cases are purged after.")
    purge_interval_minutes: int = Field(default=30, ge=1)


class DatabaseSettings(_Section):
    database_url: SecretStr = Field(description="Async SQLAlchemy URL: aiosqlite or psycopg.")
    database_pool_size: int = Field(default=5, ge=1)
    database_echo: bool = False


class ProviderSettings(_Section):
    """Connection details per model provider. A provider is used only when its base URL is set.

    Every provider is reached through its OpenAI-compatible chat endpoint.
    """

    groq_base_url: HttpUrl | None = None
    groq_api_key: SecretStr | None = None
    gemini_base_url: HttpUrl | None = None
    gemini_api_key: SecretStr | None = None
    ollama_base_url: HttpUrl | None = None
    openrouter_base_url: HttpUrl | None = None
    openrouter_api_key: SecretStr | None = None
    cerebras_base_url: HttpUrl | None = None
    cerebras_api_key: SecretStr | None = None
    mistral_base_url: HttpUrl | None = None
    mistral_api_key: SecretStr | None = None
    github_models_base_url: HttpUrl | None = None
    github_models_api_key: SecretStr | None = None

    def endpoints(self) -> dict[str, tuple[str, SecretStr | None]]:
        """Enabled providers as name -> (base URL, key). Hosted providers need a key."""
        hosted = ("groq", "gemini", "openrouter", "cerebras", "mistral", "github_models")
        enabled: dict[str, tuple[str, SecretStr | None]] = {}
        for name in hosted:
            url = getattr(self, f"{name}_base_url")
            key = getattr(self, f"{name}_api_key")
            if url is not None and key is not None and key.get_secret_value():
                enabled[name] = (str(url).rstrip("/"), key)
        if self.ollama_base_url is not None:
            enabled["ollama"] = (str(self.ollama_base_url).rstrip("/"), None)
        return enabled


class RouteSettings(_Section):
    """Ordered fallback chains, written as `provider:model` entries separated by commas.

    reasoning: planning, letters and critique. fast: bulk labelling and extraction.
    vision: reading scanned pages and photos.
    """

    route_reasoning: str = Field(min_length=3)
    route_fast: str = Field(min_length=3)
    route_vision: str = Field(min_length=3)


class ModelCallSettings(_Section):
    llm_timeout_seconds: float = Field(default=60.0, gt=0)
    llm_temperature: float = Field(default=0.1, ge=0, le=2)
    llm_max_output_tokens: int = Field(default=4096, ge=64)
    llm_attempts_per_model: int = Field(default=2, ge=1)
    llm_backoff_seconds: float = Field(default=1.5, gt=0)
    llm_max_backoff_seconds: float = Field(default=20.0, gt=0)
    llm_breaker_failures: int = Field(default=3, ge=1, description="Failures before cooling down.")
    llm_breaker_cooldown_seconds: float = Field(default=90.0, gt=0)
    llm_provider_concurrency: int = Field(default=4, ge=1)
    llm_cache_ttl_seconds: int = Field(default=86_400, ge=0)
    llm_cache_max_entries: int = Field(default=5_000, ge=0)


class RetrievalSettings(_Section):
    qdrant_path: Path | None = Field(default=None, description="Embedded Qdrant storage folder.")
    qdrant_url: HttpUrl | None = Field(default=None, description="Remote Qdrant server.")
    qdrant_api_key: SecretStr | None = None
    qdrant_collection: str = Field(min_length=1)
    embedding_model: str = Field(min_length=1, description="FastEmbed dense model id.")
    sparse_model: str = Field(min_length=1, description="FastEmbed sparse (keyword) model id.")
    rerank_model: str = Field(min_length=1, description="FastEmbed cross-encoder model id.")
    model_cache_dir: Path = Field(description="Where downloaded embedding models are kept.")
    chunk_max_chars: int = Field(default=1_400, ge=200)
    chunk_overlap_chars: int = Field(default=200, ge=0)
    retrieval_candidates: int = Field(default=24, ge=1)
    retrieval_top_k: int = Field(default=6, ge=1)
    terms_clauses_per_query: int = Field(default=3, ge=1)
    # Free tiers cap tokens per minute; this bounds the clause text sent in one call.
    context_max_chars: int = Field(default=14_000, ge=2_000)

    @model_validator(mode="after")
    def _one_backend(self) -> "RetrievalSettings":
        if (self.qdrant_path is None) == (self.qdrant_url is None):
            raise ValueError("set exactly one of QDRANT_PATH or QDRANT_URL")
        return self


class PrivacySettings(_Section):
    pii_vault_key: SecretStr = Field(description="Fernet key that encrypts masked values.")
    pii_spacy_model: str = Field(min_length=1)
    pii_score_threshold: float = Field(default=0.5, ge=0, le=1)


class AgentSettings(_Section):
    checker_max_retries: int = Field(default=2, ge=0)
    audit_batch_size: int = Field(default=30, ge=1, description="Bill lines labelled per call.")
    chat_history_turns: int = Field(default=8, ge=1)
    ocr_min_text_chars: int = Field(default=80, ge=0, description="Below this a page is scanned.")
    ocr_render_dpi: int = Field(default=200, ge=72)
    bill_total_tolerance: float = Field(default=0.01, ge=0, description="Share of bill total.")


class ApiSettings(_Section):
    api_cors_origins: list[str] = Field(description="Exact origins allowed to call the API.")
    api_rate_limit_per_minute: int = Field(default=60, ge=1)
    api_rate_limit_burst: int = Field(default=20, ge=1)
    api_trusted_proxy_hops: int = Field(default=0, ge=0)


class TracingSettings(_Section):
    langfuse_host: HttpUrl | None = None
    langfuse_public_key: SecretStr | None = None
    langfuse_secret_key: SecretStr | None = None

    @property
    def enabled(self) -> bool:
        return all((self.langfuse_host, self.langfuse_public_key, self.langfuse_secret_key))


def _load[S: _Section](section: type[S]) -> S:
    try:
        return section()
    except ValidationError as exc:
        fields = sorted({str(err["loc"][0]).upper() for err in exc.errors() if err["loc"]})
        detail = ", ".join(fields) or "; ".join(err["msg"] for err in exc.errors())
        message = f"{section.__name__}: missing or invalid settings: {detail}"
        raise ConfigurationError(message) from None


@lru_cache
def runtime_settings() -> RuntimeSettings:
    return _load(RuntimeSettings)


@lru_cache
def case_settings() -> CaseSettings:
    return _load(CaseSettings)


@lru_cache
def database_settings() -> DatabaseSettings:
    return _load(DatabaseSettings)


@lru_cache
def provider_settings() -> ProviderSettings:
    return _load(ProviderSettings)


@lru_cache
def route_settings() -> RouteSettings:
    return _load(RouteSettings)


@lru_cache
def model_call_settings() -> ModelCallSettings:
    return _load(ModelCallSettings)


@lru_cache
def retrieval_settings() -> RetrievalSettings:
    return _load(RetrievalSettings)


@lru_cache
def privacy_settings() -> PrivacySettings:
    return _load(PrivacySettings)


@lru_cache
def agent_settings() -> AgentSettings:
    return _load(AgentSettings)


@lru_cache
def api_settings() -> ApiSettings:
    return _load(ApiSettings)


@lru_cache
def tracing_settings() -> TracingSettings:
    return _load(TracingSettings)
