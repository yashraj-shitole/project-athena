"""Application services. Each submodule is a focused layer:

  - embedding: sentence-transformers singleton + encode helpers
  - text: tokenization, sentence split, cleaning (pure functions)
  - ingestion: extract → chunk → embed → keywords → store
  - retrieval: lexical / vector / hybrid (RRF) / rerank + cached search
  - llm: Ollama client, prompter (3000-token budget), streamer (SSE)
  - tools: registry, handlers (internal|http|mcp), selector, executor
  - orchestrator: agent loop assembling retrieval + LLM + tools
"""
from app.services import embedding, text

# Optional subsystems may not be importable in all envs (e.g. lightweight
# tasks that only need text utilities). We expose what's available.
try:
    from app.services import ingestion  # noqa: F401
except ImportError:  # pragma: no cover
    ingestion = None  # type: ignore[assignment]

try:
    from app.services import llm  # noqa: F401
except ImportError:  # pragma: no cover
    llm = None  # type: ignore[assignment]

try:
    from app.services import retrieval  # noqa: F401
except ImportError:  # pragma: no cover
    retrieval = None  # type: ignore[assignment]

try:
    from app.services import tools  # noqa: F401
except ImportError:  # pragma: no cover
    tools = None  # type: ignore[assignment]

try:
    from app.services import orchestrator  # noqa: F401
except ImportError:  # pragma: no cover
    orchestrator = None  # type: ignore[assignment]

try:
    from app.services import providers  # noqa: F401
except ImportError:  # pragma: no cover
    providers = None  # type: ignore[assignment]

__all__ = [
    "embedding",
    "text",
    "ingestion",
    "llm",
    "retrieval",
    "tools",
    "orchestrator",
    "providers",
]
