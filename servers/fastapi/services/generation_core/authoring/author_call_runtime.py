"""Provider-call runtime shared by every page author (svg and the HTML author).

The authoring thread pool, the optional request-rate gate and the per-call
reasoning/output settings. Moved out of ``author_slide`` unchanged so svg
authoring does not import the HTML author; ``author_slide`` re-exports every
name for its historical callers.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from typing import Optional
from weakref import WeakKeyDictionary

from services.authored_generation_performance import authored_provider_concurrency
from utils.llm_calls import author_slide_effort
from utils.llm_calls.author_slide_admission import (
    AuthorRateStates,
    register_rate_limit,
    wait_for_rate_slot,
)
from utils.llm_calls.author_slide_options import _env_nonnegative_float
from utils.llm_provider import get_llm_provider, is_codex_selected

# Per-slide authoring makes a blocking provider call (~75s of model reasoning). Run it
# in a DEDICATED thread pool — NOT asyncio's shared default executor — so a deck's
# concurrent authoring threads can't starve unrelated blocking work (other LLM calls,
# exports) that also relies on the default executor. The pool size IS the process-wide
# authoring concurrency cap: every page author funnels its provider call through it,
# so excess calls queue here instead of fanning out to hundreds of provider requests
# at once. The deck pipeline uses this same provider-specific cap; do not introduce
# an author-only override.
try:
    _selected_provider = str(get_llm_provider().value)
except Exception:
    _selected_provider = None
AUTHOR_CONCURRENCY = authored_provider_concurrency(_selected_provider)
_AUTHOR_EXECUTOR = ThreadPoolExecutor(
    max_workers=AUTHOR_CONCURRENCY, thread_name_prefix="author-slide"
)

# Concurrency bounds in-flight calls; this optional gate also bounds how quickly
# calls begin. It is useful for providers that publish an RPM quota lower than
# AUTHOR_CONCURRENCY. Zero keeps the existing behavior until an operator opts in.
AUTHOR_REQUESTS_PER_MINUTE = _env_nonnegative_float(
    "AUTHORED_AUTHOR_REQUESTS_PER_MINUTE", 0.0
)
AUTHOR_RATE_LIMIT_COOLDOWN_SECONDS = _env_nonnegative_float(
    "AUTHORED_AUTHOR_RATE_LIMIT_COOLDOWN_SECONDS", 10.0
)
AUTHOR_RATE_LIMIT_MAX_COOLDOWN_SECONDS = _env_nonnegative_float(
    "AUTHORED_AUTHOR_RATE_LIMIT_MAX_COOLDOWN_SECONDS", 60.0
)


_AUTHOR_RATE_STATES: AuthorRateStates = WeakKeyDictionary()


async def _wait_for_author_rate_slot() -> None:
    await wait_for_rate_slot(_AUTHOR_RATE_STATES, AUTHOR_REQUESTS_PER_MINUTE)


def _register_author_rate_limit(error: BaseException) -> None:
    register_rate_limit(
        error,
        _AUTHOR_RATE_STATES,
        AUTHOR_RATE_LIMIT_COOLDOWN_SECONDS,
        AUTHOR_RATE_LIMIT_MAX_COOLDOWN_SECONDS,
    )


def _authoring_extra_body(
    effort: Optional[str] = None, *, model: object = None
) -> Optional[dict]:
    """Per-slide codex reasoning-effort override (codex-only -- the param 400s on
    providers that don't support it).

    Thin wrapper over ``author_slide_effort.resolve_authoring_reasoning_level``;
    the codex check stays here because callers monkeypatch this module's
    ``is_codex_selected`` to exercise the codex egress path.
    """
    if not is_codex_selected():
        return None
    level = author_slide_effort.resolve_authoring_reasoning_level(effort, model)
    return None if level is None else {"reasoning": {"effort": level}}


_ANTHROPIC_EFFORTS = {"minimal": "low", "low": "low", "medium": "medium", "high": "high", "xhigh": "max"}


def _apply_anthropic_authoring_reasoning(
    kwargs: dict, effort: Optional[str] = None, *, model: object = None
) -> dict:
    """Pass the authored effort to Anthropic as ``output_config.effort`` only.

    Claude models differ in their default. claude-sonnet-5 thinks when
    ``thinking`` is omitted and spent whole 16k authoring budgets on hidden
    reasoning (pages returned no text: ``public_provider_empty_output``);
    claude-sonnet-4-6 does not think by default, and *enabling* adaptive
    thinking there consumed the budget the same way. Measured on one SVG
    prompt (16k budget): ``output_config.effort`` alone bounded sonnet-5
    (omitted: max_tokens with truncated text; medium: 5.8k tokens, complete)
    and left sonnet-4-6 / opus-4-8 without thinking, whereas adaptive thinking
    at medium made sonnet-4-6 return 16k thinking tokens and no text. So
    ``thinking`` is never enabled here; ``none`` disables it.

    An operator who sets AUTHORED_REASONING_EFFORT (or CODEX_REASONING_EFFORT)
    to a disable word turns the override off, whatever effort the call site
    passes. Other providers are left unchanged.
    """
    from enums.llm_provider import LLMProvider

    try:
        provider = get_llm_provider()
    except Exception:
        # Offline and unit contexts may have no resolvable provider.
        return kwargs
    if provider != LLMProvider.ANTHROPIC:
        return kwargs
    if author_slide_effort.resolve_authoring_reasoning_level(None, model) is None:
        return kwargs
    level = author_slide_effort.resolve_authoring_reasoning_level(effort, model)
    if level is None:
        return kwargs
    extra = kwargs.get("extra_body")
    extra = dict(extra) if isinstance(extra, dict) else {}
    if level == "none":
        extra["thinking"] = {"type": "disabled"}
    else:
        extra["output_config"] = {"effort": _ANTHROPIC_EFFORTS.get(level, "medium")}
    kwargs["extra_body"] = extra
    return kwargs


def _authoring_output_profile(
    *,
    role: str,
    reference_mode: str,
    structured_context: dict | None,
    correction_hint: str | None,
    model: object = None,
) -> tuple[int, str]:
    """Select a fast role profile without weakening strict authoring modes."""

    return author_slide_effort.authoring_output_profile(
        role=role,
        reference_mode=reference_mode,
        structured_context=structured_context,
        correction_hint=correction_hint,
        model=model,
    )
