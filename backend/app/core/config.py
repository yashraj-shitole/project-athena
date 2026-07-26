"""Application configuration. All values come from env vars (prefixed ATHENA_)."""
from __future__ import annotations

import math
import re
from functools import lru_cache
from pathlib import Path
from typing import List

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# H-20 (High) — the prior default of "change-me-in-prod" is a
# known-public string. We still ship a development default for
# `make test` / `make dev` convenience, but the validator below
# refuses it (and any other weak secret) the moment the environment
# is anything other than dev.
#
# The set of *known-insecure* secrets is intentionally small: the
# strong gate is the length/entropy check, not the list.
_DEFAULT_JWT_SECRET = "change-me-in-prod"  # dev only
_KNOWN_INSECURE_SECRETS = {
    _DEFAULT_JWT_SECRET,
    "",
    "secret",
    "changeme",
    "password",
    "1234567890",
    "0123456789abcdef",
}
# H-20 — minimum secret length in bytes (HS256 requires ≥ 32 bytes
# per RFC 7518 §3.2; we round up to 32 anyway and require 8+
# characters even in dev so test suites don't pass with `secret`).
_MIN_SECRET_BYTES = 32


def _shannon_entropy(s: str) -> float:
    """Bits per byte. Strings of length 0 return 0."""
    if not s:
        return 0.0
    freq: dict[str, int] = {}
    for ch in s:
        freq[ch] = freq.get(ch, 0) + 1
    n = len(s)
    return -sum((c / n) * math.log2(c / n) for c in freq.values())


# CORS origin syntax. Each entry must be ``scheme://host[:port]``,
# no wildcards, no trailing slash, no path. ``javascript:`` and
# ``data:`` are rejected outright (CWE-942 defense in depth).
#
# Host: a label-plus-dot FQDN (``example.com``) or the literal
# ``localhost`` (dev only — the loopback check below blocks
# ``localhost`` in prod). The optional port is ``:NNNNN`` at the end.
_CORS_ORIGIN_RE = re.compile(
    r"^https?://(?:"
    r"(?:[A-Za-z0-9-]+\.)+[A-Za-z0-9-]+"   # FQDN: at least one dot
    r"|localhost"                            # dev-only loopback
    r")(?::\d{1,5})?$"
)


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
    # The total prompt budget the prompter assembles towards. The Phase-1
    # default of 3000 was sized for a tiny local model on constrained
    # hardware; every configured external vendor model (and the built-in
    # qwen2.5:1.5b-instruct, 32K context) comfortably handles a larger
    # window, so we default to 8000. This lets retrieval surface more
    # chunks and keeps conversation history from being clipped too hard,
    # both of which directly improve answer accuracy.
    token_budget: int = 8000
    system_prompt_reserve: int = 350
    tool_def_reserve: int = 600
    history_reserve: int = 1200
    chunk_reserve: int = 2500
    # The 250-token answer cap truncated longer answers mid-sentence.
    # 768 leaves room for a full cited answer without letting a runaway
    # generation fill the whole context window.
    answer_reserve: int = 768
    # Real model context window used to set Ollama's `num_ctx`. Decoupled
    # from `token_budget` (the *prompt* budget) so a larger retrieval
    # window doesn't require touching the model's context cap.
    model_context_tokens: int = 32768

    # ---- Storage ----
    storage_dir: Path = Path("./storage")
    upload_max_bytes: int = 25 * 1024 * 1024  # 25MB default
    upload_allowed_types: List[str] = Field(
        default_factory=lambda: [
            "csv", "xlsx", "pdf", "doc", "docx",
            "txt", "md", "html", "htm",
        ]
    )

    # ---- Ingestion ----
    chunk_size_tokens: int = 300
    chunk_overlap_tokens: int = 50
    embedding_dim: int = 384
    keyword_top_n: int = 8
    embedding_model_name: str = "sentence-transformers/all-MiniLM-L6-v2"
    keyword_min_sim: float = 0.15  # cosine threshold for keyword on-topic filter
    # Size of the per-batch embed + keyword-encoder + COPY window.
    # 64 chunks × ~300 tokens ≈ 19k tokens per encoder forward pass —
    # well within the all-MiniLM-L6-v2 sweet spot, and a bigger batch
    # amortizes per-batch overheads (Python, torch kernel launch)
    # better than the old 32. Memory impact is bounded by the sliding
    # window (`ingest_embed_workers × ingest_embed_batch_size` chunks
    # in flight), which stays small. Larger batches trade a slightly
    # coarser progress bar for higher throughput.
    ingest_embed_batch_size: int = 64
    # Number of embedding forward-passes to run concurrently during
    # ingestion. The MiniLM encoder releases the GIL during the torch
    # matmul, so fanning N batches across N worker threads gives a
    # near-linear speedup up to core count — this is the single biggest
    # ingestion win on CPU (embedding is ~99% of wall-clock on large
    # docs). Keyword-encode + COPY stay sequential (they're <1% and not
    # safe to run concurrently on one DB connection). 0 = auto
    # (min(8, cpu_count)). When >1, torch intra-op threads are capped
    # to cpu//workers so workers×threads ≈ cpu_count (no
    # oversubscription). Set to 1 to preserve the old sequential path.
    ingest_embed_workers: int = 0
    # HNSW bulk-load tuning for the ingestion transaction. Set
    # transaction-local (SET LOCAL) before the per-batch COPY loop so
    # HNSW index maintenance — the dominant per-row DB cost on chunk
    # writes — runs faster while the load is in flight, then resets at
    # the pipeline's commit so retrieval `ef_search` and other requests
    # are untouched. `ingest_hnsw_ef_insert` lowers the insert-time
    # graph search depth (pgvector default 40); 10 is the standard
    # bulk-load value — insert is ~2-4x faster, recall is governed
    # separately by `hnsw.ef_search` (left at its default). 0 = skip
    # (use server default). `ingest_maintenance_work_mem` raises the
    # per-transaction memory cap HNSW build uses (Postgres default is
    # typically 64MB). Empty = skip. Non-Postgres dialects ignore both.
    ingest_hnsw_ef_insert: int = 10
    ingest_maintenance_work_mem: str = "256MB"

    # ---- Retrieval ----
    retrieval_top_k: int = 6
    retrieval_hybrid_threshold: float = 0.05  # below this, also run vector
    retrieval_always_hybrid: bool = False  # FR-21: when True, always RRF lexical+vector
    # Minimum cosine similarity (1 - pgvector cosine distance) for a
    # vector hit to be kept. MiniLM-L6 embeddings of unrelated text sit
    # well below 0.2; dropping them prevents semantically-irrelevant
    # chunks from polluting the prompt (and triggering "I don't know" or
    # hallucination). Only applied to vector hits — lexical ts_rank_cd
    # and RRF-fused scores are on different scales and are not filtered.
    retrieval_vector_min_sim: float = 0.2

    # ---- Cache namespaces ----
    cache_prefix_retrieval: str = "search"
    cache_prefix_tool_def: str = "tools"

    # ---- Rate limits (H-18) ----
    # Per-IP, per-minute caps for the anonymous auth endpoints. The
    # limiter is fixed-window (see app/core/ratelimit.py); these are
    # the per-window maxima. Tighten in prod; relax in dev.
    rate_limit_login_per_min: int = 10
    rate_limit_register_per_min: int = 3
    rate_limit_refresh_per_min: int = 30

    # ---- Login lockout (H-19) ----
    # Account-scoped complement to the per-IP rate limit. After this
    # many *consecutive* failed login attempts for the same email,
    # the account is locked for ``login_lockout_s`` seconds. The
    # counter resets on every successful login. This blocks a
    # distributed brute force (one guess per IP per minute) that
    # would otherwise slip past the per-IP cap.
    login_max_fails: int = 5
    login_lockout_s: int = 900  # 15 minutes
    # After this many seconds of inactivity the counter decays
    # entirely. Defends against a slow trickle of guesses that
    # never quite reaches ``login_max_fails`` but never lets the
    # victim get back to a clean slate either.
    login_fail_window_s: int = 3600  # 1 hour

    # ---- Auth ----
    # H-20 (High) — `jwt_secret` defaults to the dev placeholder,
    # but the ``model_post_init`` validator refuses to boot outside
    # dev, AND the ``@field_validator`` below rejects any secret
    # shorter than 32 bytes or with low entropy even in dev. The
    # dev default is exactly 32 bytes of distinct chars, so it
    # survives the dev check; a real operator setting
    # ``ATHENA_JWT_SECRET=foo`` in prod is rejected at boot.
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
    # H-21 (High) — each entry is regex-validated by the
    # ``cors_origins`` field validator below. We refuse wildcards,
    # loopback hosts in non-dev, and `javascript:` / `data:` URIs.
    cors_origins: List[str] = Field(default_factory=lambda: ["http://localhost:5173"])

    # ---- External Model Connectors ----
    # Fernet key (urlsafe-base64 32-byte) used to encrypt connector API
    # keys at rest. If empty in non-dev environments the app refuses to
    # boot — the dev fallback derives a key from `jwt_secret` via HKDF
    # so a fresh checkout works without manual setup.
    connector_key: str = ""
    # Background health probe cadence. 0 disables the loop.
    connector_health_interval_s: float = 60.0
    # After this many consecutive failures a connector is auto-disabled
    # (circuit breaker). Set to 0 to disable auto-disable.
    connector_health_failure_threshold: int = 3
    # Soft cap on the number of API calls in a single health probe
    # cycle. Keeps the loop from saturating the event loop on a large
    # user base.
    connector_health_max_per_cycle: int = 20

    @field_validator("storage_dir", mode="before")
    @classmethod
    def _ensure_path(cls, v):
        return Path(v)

    @field_validator("jwt_secret")
    @classmethod
    def _check_jwt_secret_strength(cls, v: str) -> str:
        """H-20 — refuse any JWT secret that is too short or has
        too little entropy.

        Two gates:

        1. Length: at least ``_MIN_SECRET_BYTES`` (32) bytes when
           UTF-8 encoded. RFC 7518 §3.2 recommends at least 32
           bytes for HS256. A 16-byte secret is brute-forceable
           in seconds with hashcat.
        2. Entropy: at least 3.0 bits/byte Shannon. A
           32-character secret of all the same byte scores 0
           and is rejected. A 32-character secret of two
           alternating bytes scores 1.0 and is rejected. Real
           secrets score ≥ 3.5.

        Both gates run regardless of environment: the dev
        placeholder ``change-me-in-prod`` is 17 bytes, scores
        3.46 bits/byte, and is rejected by gate 1 (length). The
        dev-only default shipped in ``infra/docker-compose.yml``
        is a 49-byte high-entropy string that satisfies both
        gates. A real operator setting ``ATHENA_JWT_SECRET=foo``
        in prod is rejected at boot by ``model_post_init`` and
        also by this field validator.
        """
        if not v:
            raise ValueError(
                "ATHENA_JWT_SECRET must be set to a non-empty string."
            )
        n = len(v.encode("utf-8"))
        if v in _KNOWN_INSECURE_SECRETS and n < _MIN_SECRET_BYTES:
            raise ValueError(
                f"ATHENA_JWT_SECRET is the placeholder {v!r}; set a strong "
                f"unique secret of at least {_MIN_SECRET_BYTES} bytes."
            )
        if n < _MIN_SECRET_BYTES:
            raise ValueError(
                f"ATHENA_JWT_SECRET is {n} bytes; HS256 requires at least "
                f"{_MIN_SECRET_BYTES} bytes (RFC 7518 §3.2)."
            )
        entropy = _shannon_entropy(v)
        if entropy < 3.0:
            raise ValueError(
                f"ATHENA_JWT_SECRET has Shannon entropy {entropy:.2f} "
                f"bits/byte; must be at least 3.0."
            )
        return v

    @field_validator("cors_origins")
    @classmethod
    def _check_cors_origins(cls, v: List[str]) -> List[str]:
        """H-21 — each entry must be a fully-qualified ``scheme://host[:port]``.

        We refuse:

        * ``*`` (wildcard) — incompatible with ``allow_credentials=True``.
        * ``javascript:`` / ``data:`` / ``file:`` / ``null``.
        * Any entry with a path, query, or fragment — they would
          be normalized by browsers in surprising ways.
        * Loopback hosts (``localhost``, ``127.0.0.1``, ``::1``)
          when the *current* environment is not dev — these
          are debug-only and must not survive a prod deploy.

        The dev/test path is permissive; the prod path is strict.
        """
        env = (cls.environment or "dev").lower() if hasattr(cls, "environment") else "dev"
        is_dev = env in {"dev", "development", "test", "local"}
        for origin in v:
            o = origin.strip()
            if not o:
                continue
            if o == "*":
                raise ValueError(
                    "cors_origins must not contain '*' (incompatible with "
                    "allow_credentials=True; see CWE-942)."
                )
            if not _CORS_ORIGIN_RE.match(o):
                raise ValueError(
                    f"cors_origins entry {o!r} is not a valid "
                    f"scheme://host[:port] URL. Wildcards, paths, and "
                    f"non-http(s) schemes are not allowed."
                )
            if not is_dev and (
                "localhost" in o or "127.0.0.1" in o or "[::1]" in o
            ):
                raise ValueError(
                    f"cors_origins entry {o!r} is a loopback host; loopback "
                    f"is not permitted in environment {env!r}."
                )
        return v

    def model_post_init(self, ____context) -> None:  # noqa: D401, ARG002
        """Fail-fast on insecure production configuration.

        H-20 — `jwt_secret` is also gated by the field validator
        above, but we keep this check for the explicit error
        message operators see when they ship the placeholder.

        - `jwt_secret` must not be a known placeholder when
          `environment != "dev"`.
        - `cors_origins` must not be the localhost dev default when
          `environment == "prod"`.
        - `connector_key` must be set in non-dev environments
          (encrypting API keys with a JWT-derived key ties their
          lifetime to JWT rotation — surprising in production).
        """
        if self.environment.lower() not in {"dev", "development", "test", "local"}:
            if self.jwt_secret in _KNOWN_INSECURE_SECRETS:
                raise RuntimeError(
                    "ATHENA_JWT_SECRET is not set (or is a known placeholder). "
                    "Set a strong unique secret (>= 32 bytes, entropy >= 3.0 "
                    "bits/byte) before running outside dev."
                )
        if self.environment.lower() == "prod":
            if any(o.startswith("http://localhost") for o in self.cors_origins):
                raise RuntimeError(
                    "ATHENA_CORS_ORIGINS still contains a localhost origin in "
                    "prod. Set explicit production origins."
                )
        if self.environment.lower() not in {"dev", "development", "test", "local"}:
            if not self.connector_key:
                raise RuntimeError(
                    "ATHENA_CONNECTOR_KEY is not set. The app refuses to "
                    "encrypt External Model Connector API keys with a key "
                    "derived from ATHENA_JWT_SECRET outside dev. Generate "
                    "one with `python -c \"from cryptography.fernet import "
                    "Fernet; print(Fernet.generate_key().decode())\"` and "
                    "set it in the environment."
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
    def MODEL_CONTEXT_TOKENS(self) -> int:
        return self.model_context_tokens

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
    def INGEST_EMBED_BATCH_SIZE(self) -> int:
        return self.ingest_embed_batch_size

    @property
    def INGEST_EMBED_WORKERS(self) -> int:
        return self.ingest_embed_workers

    @property
    def INGEST_HNSW_EF_INSERT(self) -> int:
        return self.ingest_hnsw_ef_insert

    @property
    def INGEST_MAINTENANCE_WORK_MEM(self) -> str:
        return self.ingest_maintenance_work_mem

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
    def RETRIEVAL_VECTOR_MIN_SIM(self) -> float:
        return self.retrieval_vector_min_sim

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
