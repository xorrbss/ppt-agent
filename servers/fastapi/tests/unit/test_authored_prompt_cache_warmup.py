
from __future__ import annotations

from dataclasses import dataclass

from services.authored_provider_rate_profile import AuthoredRateProfile
from utils.get_env_programme import is_authored_prompt_cache_warmup_enabled
from services.generation_core.authoring.author_slide import Brand


def _brand() -> Brand:
    return Brand(topic="cache warmup", language="Korean", primary="#2563EB", fonts="Inter")


_DESIGN = "DECK DESIGN SYSTEM: palette, type scale, spacing."


# --- the splice-order fix (prefix byte-identity) -------------------------------


def _common_prefix(left: str, right: str) -> str:
    length = min(len(left), len(right))
    for index in range(length):
        if left[index] != right[index]:
            return left[:index]
    return left[:length]


# --- the warm-up ordering -------------------------------------------------------


def _profile(*, initial: int = 4, steady: int = 4, per_call: int = 1) -> AuthoredRateProfile:
    return AuthoredRateProfile(
        name="api",
        slides_per_call=per_call,
        initial_wave_concurrency=initial,
        steady_wave_concurrency=steady,
        recovery_successes=2,
        author_concurrency_default=steady,
        author_concurrency_max=steady,
    )


@dataclass
class _Result:
    htmls: dict


# --- knob defaults ---------------------------------------------------------------


def test_knob_defaults_to_false(monkeypatch):
    monkeypatch.delenv("AUTHORED_PROMPT_CACHE_WARMUP_ENABLED", raising=False)
    assert is_authored_prompt_cache_warmup_enabled() is False


def test_knob_reads_the_env_override(monkeypatch):
    monkeypatch.setenv("AUTHORED_PROMPT_CACHE_WARMUP_ENABLED", "true")
    assert is_authored_prompt_cache_warmup_enabled() is True
    monkeypatch.setenv("AUTHORED_PROMPT_CACHE_WARMUP_ENABLED", "false")
    assert is_authored_prompt_cache_warmup_enabled() is False


# --- the per-deck summary line ---------------------------------------------------
