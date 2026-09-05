"""Provider rate profile contract (workstream B1)."""

from __future__ import annotations

import pytest

from services.authored_generation_performance import authored_provider_concurrency
from services.authored_provider_rate_profile import (
    AuthoredRateProfile,
    resolve_authored_rate_profile,
    resolve_authored_rate_profile_name,
)


_KNOBS = (
    "AUTHORED_PROVIDER_RATE_PROFILE",
    "AUTHORED_API_PROVIDER_CONCURRENCY_MAX",
    "AUTHORED_AUTHOR_CONCURRENCY",
    "AUTHORED_BATCH_SLIDES_PER_CALL",
    "AUTHORED_BATCH_WAVE_CONCURRENCY",
    "AUTHORED_BATCH_RECOVERY_SUCCESSES",
    "AUTHORED_PROVIDER_CONCURRENCY",
    "AUTHORED_PROVIDER_OPENAI_CONCURRENCY",
    "AUTHORED_PROVIDER_CODEX_CONCURRENCY",
    "LLM",
)


@pytest.fixture(autouse=True)
def _clean_knobs(monkeypatch):
    for key in _KNOBS:
        monkeypatch.delenv(key, raising=False)


def test_token_profile_authors_one_slide_per_call_on_a_small_wave(monkeypatch) -> None:
    """One slide per call is the shape the August decks were authored in; the
    token profile keeps only its wave and pool sizes from the batching era."""
    monkeypatch.setenv("AUTHORED_PROVIDER_RATE_PROFILE", "token")

    profile = resolve_authored_rate_profile()

    assert profile == AuthoredRateProfile(
        name="token",
        slides_per_call=1,
        initial_wave_concurrency=1,
        steady_wave_concurrency=2,
        recovery_successes=3,
        author_concurrency_default=5,
        author_concurrency_max=8,
    )


def test_api_profile_authors_one_slide_per_call_at_author_concurrency(monkeypatch) -> None:
    monkeypatch.setenv("AUTHORED_PROVIDER_RATE_PROFILE", "api")

    profile = resolve_authored_rate_profile()

    assert profile == AuthoredRateProfile(
        name="api",
        slides_per_call=1,
        initial_wave_concurrency=5,
        steady_wave_concurrency=5,
        recovery_successes=3,
        author_concurrency_default=5,
        author_concurrency_max=16,
    )


@pytest.mark.parametrize(
    ("provider", "expected"),
    [("codex", "token"), ("openai", "api"), ("anthropic", "api"), ("", "api"), (None, "api")],
)
def test_auto_derives_token_only_for_codex(monkeypatch, provider, expected) -> None:
    if provider is not None:
        monkeypatch.setenv("LLM", provider)
    monkeypatch.setenv("AUTHORED_PROVIDER_RATE_PROFILE", "auto")

    assert resolve_authored_rate_profile_name() == expected
    assert resolve_authored_rate_profile().name == expected


def test_unset_profile_knob_means_auto(monkeypatch) -> None:
    monkeypatch.setenv("LLM", "codex")
    assert resolve_authored_rate_profile().name == "token"
    monkeypatch.setenv("LLM", "openai")
    assert resolve_authored_rate_profile().name == "api"


def test_explicit_profile_wins_over_the_provider(monkeypatch) -> None:
    monkeypatch.setenv("LLM", "openai")
    monkeypatch.setenv("AUTHORED_PROVIDER_RATE_PROFILE", "token")
    assert resolve_authored_rate_profile().name == "token"

    monkeypatch.setenv("LLM", "codex")
    monkeypatch.setenv("AUTHORED_PROVIDER_RATE_PROFILE", "api")
    assert resolve_authored_rate_profile().name == "api"


def test_explicit_knobs_override_profile_values(monkeypatch) -> None:
    monkeypatch.setenv("AUTHORED_PROVIDER_RATE_PROFILE", "token")
    monkeypatch.setenv("AUTHORED_BATCH_SLIDES_PER_CALL", "2")
    monkeypatch.setenv("AUTHORED_BATCH_WAVE_CONCURRENCY", "4")
    monkeypatch.setenv("AUTHORED_BATCH_RECOVERY_SUCCESSES", "5")
    monkeypatch.setenv("AUTHORED_AUTHOR_CONCURRENCY", "7")

    profile = resolve_authored_rate_profile()

    assert profile.slides_per_call == 2
    assert profile.steady_wave_concurrency == 4
    assert profile.initial_wave_concurrency == 1  # the token warm-up stays serial
    assert profile.recovery_successes == 5
    assert profile.author_concurrency_default == 7
    assert profile.author_concurrency_max == 8


def test_api_ceiling_is_a_knob_and_clamps_author_concurrency(monkeypatch) -> None:
    monkeypatch.setenv("AUTHORED_PROVIDER_RATE_PROFILE", "api")
    monkeypatch.setenv("AUTHORED_API_PROVIDER_CONCURRENCY_MAX", "12")
    monkeypatch.setenv("AUTHORED_AUTHOR_CONCURRENCY", "40")

    profile = resolve_authored_rate_profile()

    assert profile.author_concurrency_max == 12
    assert profile.author_concurrency_default == 12
    assert profile.initial_wave_concurrency == profile.steady_wave_concurrency == 12
    assert authored_provider_concurrency("openai") == 12


def test_wave_concurrency_never_exceeds_the_author_pool(monkeypatch) -> None:
    monkeypatch.setenv("AUTHORED_PROVIDER_RATE_PROFILE", "api")
    monkeypatch.setenv("AUTHORED_AUTHOR_CONCURRENCY", "3")
    monkeypatch.setenv("AUTHORED_BATCH_WAVE_CONCURRENCY", "9")

    profile = resolve_authored_rate_profile()

    assert profile.steady_wave_concurrency == 3
    assert profile.initial_wave_concurrency == 3


@pytest.mark.parametrize("bad", ["0", "-2"])
def test_non_positive_explicit_knobs_fall_back_to_profile_values(monkeypatch, bad) -> None:
    """Non-integer text is rejected by the registry snapshot itself; only the
    sign is the accessor's concern."""

    monkeypatch.setenv("AUTHORED_PROVIDER_RATE_PROFILE", "token")
    monkeypatch.setenv("AUTHORED_BATCH_SLIDES_PER_CALL", bad)
    monkeypatch.setenv("AUTHORED_AUTHOR_CONCURRENCY", bad)

    profile = resolve_authored_rate_profile()

    assert profile.slides_per_call == 1
    assert profile.author_concurrency_default == 5


def test_author_concurrency_defaults_and_ceilings_follow_the_profile(monkeypatch) -> None:
    monkeypatch.setenv("AUTHORED_PROVIDER_RATE_PROFILE", "token")
    assert authored_provider_concurrency("codex") == 5
    monkeypatch.setenv("AUTHORED_PROVIDER_CONCURRENCY", "20")
    assert authored_provider_concurrency("codex") == 8

    monkeypatch.setenv("AUTHORED_PROVIDER_RATE_PROFILE", "api")
    assert authored_provider_concurrency("openai") == 16
    monkeypatch.delenv("AUTHORED_PROVIDER_CONCURRENCY")
    assert authored_provider_concurrency("openai") == 5
    monkeypatch.setenv("AUTHORED_PROVIDER_OPENAI_CONCURRENCY", "6")
    assert authored_provider_concurrency("openai") == 6
    assert authored_provider_concurrency("anthropic") == 5


def test_profile_rejects_impossible_values() -> None:
    with pytest.raises(ValueError, match="unknown authored rate profile"):
        AuthoredRateProfile("auto", 1, 1, 1, 1, 1, 1)
    with pytest.raises(ValueError, match="positive integer"):
        AuthoredRateProfile("api", 0, 1, 1, 1, 1, 1)
    with pytest.raises(ValueError, match="must not exceed"):
        AuthoredRateProfile("api", 1, 1, 1, 1, 9, 8)
