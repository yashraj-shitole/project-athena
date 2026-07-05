"""Application configuration. All values come from env vars (prefixed ATHENA_)."""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import List

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# The shipped default. If this value is still in effect outside `dev` the
# app refuses to boot (see Settings.model_validator below) — anyone can
# forge JWTs with a known secret, so a quiet start in production is the
# worst possible failure mode.
_DEFAULT_JWT_SECRET = "change-me-in-prod"
_KNOWN_INSECURE_SECRETS = {_DEFAULT_JWT_SECRET, "", "secret", "changeme"}


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="ATHENA_",
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ---- App ----
    app_name: str = "Project Athena"
    environment: str = "dev"
    log_level: str = "INFO"
    debug: bool = False

    # ---- Database ----
    database_url: str = "postgresql+asyncpg://athena:athena@localhost:5432/athena"
    db_pool_size: int = 10
    db_echo: bool = False

    # ---- Redis ----
    redis_url: str = "redis://localhost:6379/0"
    cache_ttl_seconds: int = 300  # NFR-09 / FR-34

    # ---- LLM (Ollama-compatible) ----
    ollama_url: str = "http://localhost:11434"
    ollama_model: str = "qwen2.5:1.5b-instruct"
    ollama_timeout: float = 60.0

    # ---- Token budget (NFR-17) ----
    token_budget: int = 3000
    system_prompt_reserve: int = 350
    tool_def_reserve: int = 600
    history_reserve: int = 800
    chunk_reserve: int = 1000
    answer_reserve: int = 250

    # ---- Storage ----
    storage_dir: Path = Path("./storage")
    upload_max_bytes: int = 25 * 1024 * 1024  # 25MB default
    upload_allowed_types: List[str] = Field(
        default_factory=lambda: ["csv", "xlsx", "pdf", "doc", "docx"]
    )

    # ---- Ingestion ----
    chunk_size_tokens: int = 300
    chunk_overlap_tokens: int = 50
    embedding_dim: int = 384
    keyword_top_n: int = 8
    embedding_model_name: str = "sentence-transformers/all-MiniLM-L6-v2"
    keyword_min_sim: float = 0.15  # cosine threshold for keyword on-topic filter

    # ---- Retrieval ----
    retrieval_top_k: int = 4
    retrieval_hybrid_threshold: float = 0.05  # below this, also run vector
    retrieval_always_hybrid: bool = False  # FR-21: when True, always RRF lexical+vector

    # ---- Cache namespaces ----
    cache_prefix_retrieval: str = "search"
    cache_prefix_tool_def: str = "tools"

    # ---- Auth ----
    jwt_secret: str = _DEFAULT_JWT_SECRET
    jwt_algorithm: str = "HS256"
    access_token_ttl_min: int = 30
    refresh_token_ttl_days: int = 14
    # Email allowlist for tool-admin endpoints (POST/PATCH /api/tools,
    # /mcp/attach, /tools/{id}/invoke). Empty by default — in that case
    # the admin-only endpoints are disabled (403) so a fresh deploy does
    # not expose tool mutation to every authenticated user.
    admin_emails: List[str] = Field(default_factory=list)

    # ---- CORS ----
    cors_origins: List[str] = Field(default_factory=lambda: ["http://localhost:5173"])

    @field_validator("storage_dir", mode="before")
    @classmethod
    def _ensure_path(cls, v):
        return Path(v)

    def model_post_init(self, ____context) -> None:  # noqa: D401, ARG002
        """Fail-fast on insecure production configuration.

        - `jwt_secret` must not be a known placeholder when
          `environment != "dev"`.
        - `cors_origins` must not be the localhost dev default when
          `environment == "prod"`.
        """
        if self.environment.lower() not in {"dev", "development", "test", "local"}:
            if self.jwt_secret in _KNOWN_INSECURE_SECRETS:
                raise RuntimeError(
                    "ATHENA_JWT_SECRET is not set (or is a known placeholder). "
                    "Set a strong unique secret before running outside dev."
                )
        if self.environment.lower() == "prod":
            if any(o.startswith("http://localhost") for o in self.cors_origins):
                raise RuntimeError(
                    "ATHENA_CORS_ORIGINS still contains a localhost origin in "
                    "prod. Set explicit production origins."
                )

    def ensure_dirs(self) -> None:
        self.storage_dir.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    # UPPER_CASE aliases — keep the historical names used across the
    # codebase (EMBED_DIM, OLLAMA_BASE_URL, TOKEN_BUDGET_TOTAL, etc.).
    # ------------------------------------------------------------------
    @property
    def DEBUG(self) -> bool:  # noqa: N802
        return self.debug

    @property
    def EMBED_DIM(self) -> int:
        return self.embedding_dim

    @property
    def EMBED_MODEL_NAME(self) -> str:
        return self.embedding_model_name

    @property
    def OLLAMA_BASE_URL(self) -> str:
        return self.ollama_url

    @property
    def OLLAMA_MODEL(self) -> str:
        return self.ollama_model

    @property
    def OLLAMA_TIMEOUT_S(self) -> float:
        return self.ollama_timeout

    @property
    def TOKEN_BUDGET_TOTAL(self) -> int:
        return self.token_budget

    @property
    def TOKEN_BUDGET_SYSTEM(self) -> int:
        return self.system_prompt_reserve

    @property
    def TOKEN_BUDGET_TOOL_DEF(self) -> int:
        return self.tool_def_reserve

    @property
    def TOKEN_BUDGET_HISTORY(self) -> int:
        return self.history_reserve

    @property
    def TOKEN_BUDGET_CHUNK(self) -> int:
        return self.chunk_reserve

    @property
    def TOKEN_BUDGET_ANSWER(self) -> int:
        return self.answer_reserve

    @property
    def CHUNK_TARGET_TOKENS(self) -> int:
        return self.chunk_size_tokens

    @property
    def CHUNK_OVERLAP_TOKENS(self) -> int:
        return self.chunk_overlap_tokens

    @property
    def KEYWORD_MIN_SIM(self) -> float:
        return self.keyword_min_sim

    @property
    def KEYWORD_TOP_N(self) -> int:
        return self.keyword_top_n

    @property
    def RETRIEVAL_TOP_K(self) -> int:
        return self.retrieval_top_k

    @property
    def RETRIEVAL_HYBRID_THRESHOLD(self) -> float:
        return self.retrieval_hybrid_threshold

    @property
    def RETRIEVAL_ALWAYS_HYBRID(self) -> bool:
        return self.retrieval_always_hybrid

    @property
    def UPLOAD_MAX_BYTES(self) -> int:
        return self.upload_max_bytes

    @property
    def ALLOWED_UPLOAD_EXTS(self) -> List[str]:
        return self.upload_allowed_types

    @property
    def CACHE_PREFIX_RETRIEVAL(self) -> str:
        return self.cache_prefix_retrieval

    @property
    def CACHE_PREFIX_TOOL_DEF(self) -> str:
        return self.cache_prefix_tool_def


@lru_cache
def get_settings() -> Settings:
    s = Settings()
    s.ensure_dirs()
    return s


# Backwards-compat shim — a number of modules do `from app.core.config
# import settings`. The lru_cache ensures we instantiate Settings at most
# once per process, so this is just a friendly alias.
settings: Settings = get_settings()
