"""Request-scoped Codex model presets for durable presentation generation."""

from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator

from fastapi import HTTPException

from enums.llm_provider import LLMProvider
from utils.llm_provider import get_llm_provider
from utils.runtime_settings import (
    RuntimeSettings,
    resolve_runtime_settings,
    runtime_settings_context,
)


CODEX_GENERATION_PRESETS: dict[str, tuple[str, str]] = {
    "luna-medium": ("gpt-6-luna", "medium"),
    "luna-high": ("gpt-6-luna", "high"),
    "sol-low": ("gpt-6-sol", "low"),
    "sol-medium": ("gpt-6-sol", "medium"),
    "sol-high": ("gpt-6-sol", "high"),
    # sol6-* are a legacy alias of sol-* (same gpt-6-sol target): kept so
    # stored configs/requests carrying the old sol6-* id still resolve
    # identically.
    "sol6-low": ("gpt-6-sol", "low"),
    "sol6-medium": ("gpt-6-sol", "medium"),
    "sol6-high": ("gpt-6-sol", "high"),
    "astra-low": ("gpt-6-astra", "low"),
    "astra-medium": ("gpt-6-astra", "medium"),
}


def generation_model_settings(
    preset: str | None,
    settings: RuntimeSettings | None = None,
) -> RuntimeSettings:
    snapshot = resolve_runtime_settings(settings)
    if not preset:
        return snapshot
    selection = CODEX_GENERATION_PRESETS.get(preset)
    if selection is None:
        raise HTTPException(status_code=422, detail="codex_generation_preset_invalid")
    if get_llm_provider(snapshot) != LLMProvider.CODEX:
        raise HTTPException(
            status_code=422,
            detail="codex_generation_preset_requires_codex_provider",
        )
    model, effort = selection
    return snapshot.with_overrides(
        {"CODEX_MODEL": model, "CODEX_REASONING_EFFORT": effort}
    )


@contextmanager
def generation_model_context(preset: str | None) -> Iterator[RuntimeSettings]:
    settings = generation_model_settings(preset)
    with runtime_settings_context(settings):
        yield settings


__all__ = [
    "CODEX_GENERATION_PRESETS",
    "generation_model_context",
    "generation_model_settings",
]
