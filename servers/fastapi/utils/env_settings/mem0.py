"""Typed accessor for the Mem0 memory knobs.

Numeric knobs use ``on_invalid="clamp"`` with ``minimum=1``: malformed text
falls back to the default and a value below 1 becomes 1, which is exactly what
the retired private ``_to_int`` copies did (``max(1, int(value))``).

String knobs are stripped and a blank value counts as unset. Paths that derive
from other paths (``MEM0_DIR`` -> qdrant / history db) and model/URL fallback
chains that consult the Ollama settings stay in the service: this accessor
returns the raw configured value (``None`` when unset) and the service composes.

``MEM0_ENABLED`` is a registered runtime setting and is read through
``is_mem0_enabled``; ``MEM0_TIMEOUT_SECONDS`` keeps its own accessor in
``utils.get_env``.
"""

from __future__ import annotations

from dataclasses import dataclass

from utils.get_env import env_bool, env_int, get_setting_env, is_mem0_enabled
from utils.runtime_settings import RuntimeSettings

ENV_NAMES: tuple[str, ...] = (
    "MEM0_ENABLED",
    "MEM0_TOP_K",
    "MEM0_MAX_CONTEXT_CHARS",
    "MEM0_PRESENTATION_NAMESPACE_PREFIX",
    "MEM0_CHAT_NAMESPACE_PREFIX",
    "CHAT_MAX_STORED_TURNS",
    "MEM0_SPACY_MODEL",
    "MEM0_REQUIRE_SPACY_MODEL",
    "MEM0_DIR",
    "MEM0_QDRANT_PATH",
    "MEM0_HISTORY_DB_PATH",
    "MEM0_COLLECTION_NAME",
    "MEM0_EMBEDDER_PROVIDER",
    "MEM0_EMBEDDER_MODEL",
    "MEM0_EMBEDDING_DIMS",
    "MEM0_LLM_MODEL",
    "MEM0_LLM_API_KEY",
    "MEM0_LLM_BASE_URL",
)

DEFAULT_MAX_STORED_TURNS = 20


@dataclass(frozen=True, slots=True)
class Mem0Settings:
    enabled: bool
    top_k: int
    max_context_chars: int
    presentation_namespace_prefix: str
    chat_namespace_prefix: str
    max_stored_turns: int
    spacy_model: str
    require_spacy_model: bool
    mem0_dir: str | None
    qdrant_path: str | None
    history_db_path: str | None
    collection_name: str
    embedder_provider: str
    embedder_model: str
    embedding_dims: int
    llm_model: str | None
    llm_api_key: str
    llm_base_url: str | None


def _text(name: str, settings: RuntimeSettings | None) -> str | None:
    value = (get_setting_env(name, settings) or "").strip()
    return value or None


def _int(name: str, default: int, settings: RuntimeSettings | None) -> int:
    return env_int(name, default, minimum=1, on_invalid="clamp", settings=settings)


def get_mem0_settings(settings: RuntimeSettings | None = None) -> Mem0Settings:
    presentation_prefix = (
        _text("MEM0_PRESENTATION_NAMESPACE_PREFIX", settings) or "presentation"
    )
    return Mem0Settings(
        enabled=is_mem0_enabled(settings),
        top_k=_int("MEM0_TOP_K", 8, settings),
        max_context_chars=_int("MEM0_MAX_CONTEXT_CHARS", 6000, settings),
        presentation_namespace_prefix=presentation_prefix,
        chat_namespace_prefix=(
            _text("MEM0_CHAT_NAMESPACE_PREFIX", settings) or presentation_prefix
        ),
        max_stored_turns=_int(
            "CHAT_MAX_STORED_TURNS", DEFAULT_MAX_STORED_TURNS, settings
        ),
        spacy_model=_text("MEM0_SPACY_MODEL", settings) or "en_core_web_sm",
        require_spacy_model=env_bool(
            "MEM0_REQUIRE_SPACY_MODEL", True, settings=settings
        ),
        mem0_dir=_text("MEM0_DIR", settings),
        qdrant_path=_text("MEM0_QDRANT_PATH", settings),
        history_db_path=_text("MEM0_HISTORY_DB_PATH", settings),
        collection_name=_text("MEM0_COLLECTION_NAME", settings) or "presenton_memories",
        embedder_provider=_text("MEM0_EMBEDDER_PROVIDER", settings) or "fastembed",
        embedder_model=_text("MEM0_EMBEDDER_MODEL", settings)
        or "BAAI/bge-small-en-v1.5",
        embedding_dims=_int("MEM0_EMBEDDING_DIMS", 384, settings),
        llm_model=_text("MEM0_LLM_MODEL", settings),
        llm_api_key=_text("MEM0_LLM_API_KEY", settings) or "ollama",
        llm_base_url=_text("MEM0_LLM_BASE_URL", settings),
    )
