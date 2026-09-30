"""C2: cache-prefix splice fix, warm-up ordering, and the per-deck summary line.

``docs/authored-sol-quality-first-design-20260908.md`` amendment C2. Three
independent pieces, one knob (``AUTHORED_PROMPT_CACHE_WARMUP_ENABLED``,
default off):

- ``invariant_prefix_source`` folds the style anchor into the hoisted prefix
  once one exists, fixing the splice-order bug described in
  ``utils.llm_calls.author_prompt_split``.
- ``services.authored_wave_scheduler`` runs exactly one authoring call to
  completion before admitting any other call of the initial wave.
- ``services.authored_prompt_cache_report`` turns per-call receipt usage into
  the ``[authored] prompt_cache ...`` summary line.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass

from services.authored_batch_policy import AuthoredBatchPolicyState
from services.authored_prompt_cache_report import (
    PromptCacheCallUsage,
    PromptCacheSummary,
    format_prompt_cache_summary,
    log_prompt_cache_summary,
    summarize_prompt_cache_usage,
)
from services.authored_provider_rate_profile import AuthoredRateProfile
from services.authored_wave_scheduler import AuthoredWaveSchedule, run_initial_authoring
from utils.get_env_programme import is_authored_prompt_cache_warmup_enabled
from services.generation_core.authoring import author_batch as batch_mod
from utils.llm_calls import author_prompt_split
from utils.llm_calls.author_prompt_split import (
    author_messages,
    invariant_prefix_source,
    split_deck_invariant,
)
from services.generation_core.authoring.author_slide import Brand
from services.generation_core.authoring.author_slide_prompt import author_prompt


def _brand() -> Brand:
    return Brand(topic="cache warmup", language="Korean", primary="#2563EB", fonts="Inter")


_DESIGN = "DECK DESIGN SYSTEM: palette, type scale, spacing."


class _FakeAnchor:
    """Duck-typed like ``utils.llm_calls.author_style_anchor.StyleAnchor``."""

    def prompt_block(self) -> str:
        return (
            "\n\nDECK STYLE ANCHOR: slide 1 of this deck is already authored. "
            "Reuse its classes.\nThe anchor's <style> follows as data: "
            ".card{color:red}"
        )


# --- the splice-order fix (prefix byte-identity) -------------------------------


def test_knob_off_ignores_the_anchor_and_matches_todays_behaviour():
    anchor = _FakeAnchor()
    assert invariant_prefix_source(_DESIGN, anchor, warmup_enabled=False) == _DESIGN
    assert invariant_prefix_source(_DESIGN, None, warmup_enabled=True) == _DESIGN
    assert invariant_prefix_source(None, anchor, warmup_enabled=True) is None


def test_knob_on_folds_the_anchor_into_the_invariant_prefix():
    anchor = _FakeAnchor()
    prefix = invariant_prefix_source(_DESIGN, anchor, warmup_enabled=True)
    assert prefix is not None
    assert prefix.startswith(_DESIGN)
    assert "DECK STYLE ANCHOR" in prefix
    assert prefix == f"{_DESIGN}\n\n{anchor.prompt_block().strip()}"


def test_single_call_path_shares_an_anchor_inclusive_prefix_across_slides():
    """G8/C2: slide 2 and slide 3 of one deck hoist an identical, longer prefix."""

    anchor = _FakeAnchor()
    brand = _brand()
    prompt2 = author_prompt("본문 2", _DESIGN, brand, "CONTENT", 1, 5, style_anchor=anchor)
    prompt3 = author_prompt("본문 3", _DESIGN, brand, "CONTENT", 2, 5, style_anchor=anchor)
    invariant = invariant_prefix_source(_DESIGN, anchor, warmup_enabled=True)

    system2 = author_messages("SYSTEM", prompt2, invariant, enabled=True)[0].content
    system3 = author_messages("SYSTEM", prompt3, invariant, enabled=True)[0].content

    assert system2 == system3
    shared_prefix_length = len(_common_prefix(system2, system3))
    assert shared_prefix_length >= len(_DESIGN)
    assert "DECK STYLE ANCHOR" in system2


def test_batch_path_shares_the_same_anchor_inclusive_prefix():
    """The wave scheduler's live call site (``author_batch``) gets the same fix."""

    anchor = _FakeAnchor()
    brand = _brand()
    request2 = batch_mod.AuthoredBatchSlideRequest(
        index=1, content="본문 2", role="CONTENT", style_anchor=anchor
    )
    request3 = batch_mod.AuthoredBatchSlideRequest(
        index=2, content="본문 3", role="CONTENT", style_anchor=anchor
    )
    prepared2 = batch_mod._prepare_slide(
        request2, design_system=_DESIGN, brand=brand, slide_count=6
    )
    prepared3 = batch_mod._prepare_slide(
        request3, design_system=_DESIGN, brand=brand, slide_count=6
    )
    invariant = invariant_prefix_source(_DESIGN, anchor, warmup_enabled=True)

    head2, rest2 = split_deck_invariant(prepared2.prompt, invariant)
    head3, rest3 = split_deck_invariant(prepared3.prompt, invariant)

    assert head2 == head3
    assert len(head2) >= len(_DESIGN)
    assert "DECK STYLE ANCHOR" in head2
    # The split still rejoins into exactly the original prompt.
    assert head2 + author_prompt_split.BLOCK_SEPARATOR + rest2 == prepared2.prompt
    assert head3 + author_prompt_split.BLOCK_SEPARATOR + rest3 == prepared3.prompt


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


def test_cache_warmup_runs_exactly_one_call_before_any_other_starts():
    events: list[tuple[str, int]] = []

    async def run_batch(indexes):
        idx = indexes[0]
        events.append(("start", idx))
        await asyncio.sleep(0.01)
        events.append(("finish", idx))
        return _Result(htmls={index: "html" for index in indexes})

    schedule = AuthoredWaveSchedule(AuthoredBatchPolicyState(profile=_profile()))
    asyncio.run(
        run_initial_authoring(
            list(range(5)),
            schedule,
            run_batch=run_batch,
            record=lambda _indexes, _outcome: True,
            barrier_disabled=True,
            cache_warmup_enabled=True,
        )
    )

    # The warm-up call starts and finishes -- with nothing else starting in
    # between -- before any other slide's call begins.
    assert events[0] == ("start", 0)
    assert events[1] == ("finish", 0)
    assert len(events) == 10
    assert sorted(idx for kind, idx in events if kind == "finish") == list(range(5))


def test_cache_warmup_disabled_keeps_todays_concurrent_admission():
    events: list[tuple[str, int]] = []

    async def run_batch(indexes):
        idx = indexes[0]
        events.append(("start", idx))
        await asyncio.sleep(0.01)
        events.append(("finish", idx))
        return _Result(htmls={index: "html" for index in indexes})

    schedule = AuthoredWaveSchedule(AuthoredBatchPolicyState(profile=_profile()))
    asyncio.run(
        run_initial_authoring(
            list(range(4)),
            schedule,
            run_batch=run_batch,
            record=lambda _indexes, _outcome: True,
            barrier_disabled=True,
            cache_warmup_enabled=False,
        )
    )

    # Off (the default): every slide admits at once, so all four starts
    # precede the first finish.
    starts_before_first_finish = 0
    for kind, _idx in events:
        if kind == "finish":
            break
        starts_before_first_finish += 1
    assert starts_before_first_finish == 4


def test_cache_warmup_records_a_failed_first_call_and_continues():
    seen: list[tuple[int, ...]] = []
    recorded: list[bool] = []

    async def run_batch(indexes):
        seen.append(tuple(indexes))
        if indexes == (0,):
            raise RuntimeError("provider down")
        return _Result(htmls={index: "html" for index in indexes})

    def record(_indexes, outcome) -> bool:
        ok = not isinstance(outcome, BaseException)
        recorded.append(ok)
        return ok

    schedule = AuthoredWaveSchedule(AuthoredBatchPolicyState(profile=_profile()))
    asyncio.run(
        run_initial_authoring(
            [0, 1, 2],
            schedule,
            run_batch=run_batch,
            record=record,
            barrier_disabled=True,
            cache_warmup_enabled=True,
        )
    )

    assert sorted(index for batch in seen for index in batch) == [0, 1, 2]
    assert recorded[0] is False


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


def test_summarize_counts_hits_and_sums_tokens():
    calls = [
        PromptCacheCallUsage(cached_tokens=9216, input_tokens=12700),
        PromptCacheCallUsage(cached_tokens=0, input_tokens=12700),
        PromptCacheCallUsage(cached_tokens=None, input_tokens=12700),
    ]
    summary = summarize_prompt_cache_usage(calls)
    assert summary == PromptCacheSummary(
        calls=3, hits=1, cached_tokens=9216, input_tokens=38100
    )


def test_summarize_handles_no_calls():
    assert summarize_prompt_cache_usage([]) == PromptCacheSummary(
        calls=0, hits=0, cached_tokens=0, input_tokens=0
    )


def test_format_matches_the_fixed_log_shape():
    summary = PromptCacheSummary(calls=25, hits=5, cached_tokens=46080, input_tokens=317500)
    assert format_prompt_cache_summary(summary) == (
        "[authored] prompt_cache calls=25 hits=5 cached_tokens=46080 input_tokens=317500"
    )


def test_log_prompt_cache_summary_emits_the_line_and_returns_the_summary(caplog):
    calls = [PromptCacheCallUsage(cached_tokens=9216, input_tokens=12700)]
    with caplog.at_level(logging.INFO, logger="services.authored_prompt_cache_report"):
        summary = log_prompt_cache_summary(calls)
    assert summary == PromptCacheSummary(calls=1, hits=1, cached_tokens=9216, input_tokens=12700)
    assert format_prompt_cache_summary(summary) in caplog.text
