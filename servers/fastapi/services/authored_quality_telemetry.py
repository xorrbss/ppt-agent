"""Content-free observations for the authored quality-review pipeline."""

import math
import re
from collections import Counter
from collections.abc import Iterable, Mapping
from typing import Any

from utils.llm_calls import author_vision_qa
from utils.llm_calls.author_slide import STYLE_REFERENCE

_SAFE_OBSERVATION_TOKEN = re.compile(r"^[A-Za-z0-9._:/@+-]{1,128}$")
_WINDOWS_ABSOLUTE_PATH = re.compile(r"^[A-Za-z]:[\\/]")
_SAFE_REFERENCE_MODES = {"exact_reconstruction", STYLE_REFERENCE}
_SAFE_STAGES = {
    "initial_review",
    "correction",
    "final_review",
    "acceptance",
    "completed",
}
_SAFE_REJECTION_REASONS = {
    "no_candidate",
    "unchanged",
    "worsened",
    "initial_unavailable",
    "initial_inconsistent",
    "initial_indeterminate",
    "final_unavailable",
    "final_inconsistent",
    "final_indeterminate",
    "regression",
    "local_regression",
    "local_baseline_unavailable",
    "local_candidate_unavailable",
    "local_high_remaining",
    "local_not_improved",
    "fallback_failed",
    "full_reauthor_failed",
    "ocr_unavailable",
    "ocr_timeout",
    "ocr_not_improved",
    "ocr_regression",
}


def safe_review_model() -> str:
    """Return a bounded configuration token, never a prompt or provider response."""
    try:
        model = str(author_vision_qa.get_model() or "unknown")
    except Exception:  # noqa: BLE001 - telemetry is best-effort only
        return "unknown"
    return _safe_configuration_token(model)


def _safe_configuration_token(value: Any) -> str:
    """Return one log-safe identifier, rejecting paths and free-form content."""
    token = str(value or "unknown")
    token_lower = token.casefold()
    if (
        not _SAFE_OBSERVATION_TOKEN.fullmatch(token)
        or _WINDOWS_ABSOLUTE_PATH.match(token)
        or token.startswith(("/", "\\", "file:"))
        or "\\" in token
        or token_lower.startswith(("sk-", "api_key", "apikey", "bearer", "basic"))
    ):
        return "unknown"
    return token


def _nonnegative_int(value: Any) -> int:
    try:
        return max(0, int(value))
    except (TypeError, ValueError, OverflowError):
        return 0


def _nonnegative_ms(value: Any) -> float:
    try:
        number = float(value)
        return round(max(0.0, number), 3) if math.isfinite(number) else 0.0
    except (TypeError, ValueError, OverflowError):
        return 0.0


def review_observation_metadata() -> dict[str, str]:
    """Read static reviewer configuration without initiating any VQA request."""
    return {
        "model": safe_review_model(),
        "prompt_version": _safe_configuration_token(
            getattr(author_vision_qa, "CRITIQUE_PROMPT_VERSION", "unknown")
        ),
        "cache_version": _safe_configuration_token(
            getattr(author_vision_qa, "_VQA_PROMPT_VERSION", "unknown")
        ),
    }


def style_comparison_observation_metadata() -> dict[str, str]:
    """Read comparison-review configuration without exposing cache-key material."""
    return {
        "model": safe_review_model(),
        "prompt_version": _safe_configuration_token(
            getattr(
                author_vision_qa,
                "STYLE_COMPARISON_VQA_PROMPT_VERSION",
                "unknown",
            )
        ),
        "cache_version": _safe_configuration_token(
            getattr(
                author_vision_qa,
                "STYLE_COMPARISON_VQA_PROMPT_VERSION",
                "unknown",
            )
        ),
    }


def observe_critique_cache(
    images: Iterable[bytes],
    contexts: Iterable[str | None],
    *,
    source_images: Iterable[bytes] | None = None,
    reference_mode: str = STYLE_REFERENCE,
) -> tuple[int, int]:
    """Count completed cache hits immediately before a critique batch."""
    image_list = list(images)
    context_list = list(contexts)
    source_list = (
        list(source_images) if source_images is not None else [None] * len(image_list)
    )
    if len(context_list) != len(image_list) or len(source_list) != len(image_list):
        return 0, len(image_list)
    try:
        cache_enabled = int(getattr(author_vision_qa, "VQA_CACHE_SIZE", 0)) > 0
    except (TypeError, ValueError):
        cache_enabled = False
    if not cache_enabled:
        return 0, len(image_list)

    key_builder = getattr(author_vision_qa, "_critique_cache_key", None)
    cache = getattr(author_vision_qa, "_vqa_cache", None)
    if not callable(key_builder) or cache is None:
        return 0, len(image_list)

    hits = 0
    for image, context, source_image in zip(image_list, context_list, source_list):
        try:
            key = key_builder(
                image,
                context,
                source_image=source_image,
                reference_mode=reference_mode,
            )
            hits += int(key in cache)
        except Exception:  # noqa: BLE001, S112 - telemetry must not affect review
            continue
    return hits, len(image_list) - hits


def observe_style_comparison_cache(
    baseline_images: Iterable[bytes],
    candidate_images: Iterable[bytes],
    contexts: Iterable[str | None],
) -> tuple[int, int]:
    """Count completed cache hits for one STYLE_REFERENCE comparison batch."""
    baseline_list = list(baseline_images)
    candidate_list = list(candidate_images)
    context_list = list(contexts)
    if not (len(baseline_list) == len(candidate_list) == len(context_list)):
        return 0, len(candidate_list)
    try:
        cache_enabled = int(getattr(author_vision_qa, "VQA_CACHE_SIZE", 0)) > 0
    except (TypeError, ValueError):
        cache_enabled = False
    if not cache_enabled:
        return 0, len(candidate_list)

    key_builder = getattr(author_vision_qa, "_style_comparison_cache_key", None)
    cache = getattr(author_vision_qa, "_style_comparison_vqa_cache", None)
    if not callable(key_builder) or cache is None:
        return 0, len(candidate_list)

    hits = 0
    for baseline, candidate, context in zip(
        baseline_list, candidate_list, context_list
    ):
        try:
            key = key_builder(
                baseline,
                candidate,
                context,
                reference_mode=STYLE_REFERENCE,
            )
            hits += int(key in cache)
        except Exception:  # noqa: BLE001, S112 - telemetry must not affect review
            continue
    return hits, len(candidate_list) - hits


def build_review_event(
    *,
    reference_mode: str,
    stage: str,
    slide_count: int,
    attempted: int = 0,
    accepted: int = 0,
    rejection_reasons: Mapping[str, int] | None = None,
    model: str | None = None,
    prompt_version: str | None = None,
    cache_version: str | None = None,
    critique_batch_count: int = 0,
    critique_slide_request_count: int = 0,
    cache_hits: int = 0,
    cache_misses: int = 0,
    render_ms: float = 0.0,
    critique_ms: float = 0.0,
    revision_ms: float = 0.0,
    indeterminate_count: int = 0,
    ocr_executed: bool = False,
    ocr_requests: int = 0,
    ocr_calls: int = 0,
    ocr_cache_hits: int = 0,
    ocr_total_ms: float = 0.0,
    local_inspection_ms: float = 0.0,
    montage_ms: float = 0.0,
    persist_ms: float = 0.0,
    total_ms: float = 0.0,
    patch_attempted: int = 0,
    patch_accepted: int = 0,
    patch_rejected: int = 0,
    fallback_attempted: int = 0,
    fallback_succeeded: int = 0,
    fallback_failed: int = 0,
    local_high_remaining: int = 0,
    vqa_cost_usd: float | None = None,
) -> dict[str, Any]:
    """Build the closed, content-free observation emitted by the service."""
    raw_reasons = rejection_reasons or {}
    reasons: Counter[str] = Counter()
    for reason, count in raw_reasons.items():
        safe_reason = reason if reason in _SAFE_REJECTION_REASONS else "other"
        reasons[safe_reason] += _nonnegative_int(count)
    rejected = sum(reasons.values())
    safe_cost: float | None = None
    if vqa_cost_usd is not None:
        try:
            raw_cost = float(vqa_cost_usd)
            if math.isfinite(raw_cost):
                safe_cost = round(max(0.0, raw_cost), 6)
        except (TypeError, ValueError, OverflowError):
            pass
    vqa_calls = _nonnegative_int(critique_slide_request_count)
    return {
        "schema_version": 2,
        "pipeline": "authored_quality_review",
        "reference_mode": (
            reference_mode if reference_mode in _SAFE_REFERENCE_MODES else "unknown"
        ),
        "stage": stage if stage in _SAFE_STAGES else "unknown",
        "slides": _nonnegative_int(slide_count),
        "attempted": _nonnegative_int(attempted),
        "accepted": _nonnegative_int(accepted),
        "rejected": rejected,
        "rejection_reasons": dict(sorted(reasons.items())),
        "model": _safe_configuration_token(model),
        "prompt_version": _safe_configuration_token(prompt_version),
        "cache_version": _safe_configuration_token(cache_version),
        "critique_batch_count": _nonnegative_int(critique_batch_count),
        "critique_slide_request_count": vqa_calls,
        "vqa_calls": vqa_calls,
        "vqa_cost_usd": safe_cost,
        "vqa_cost_status": (
            "reported"
            if safe_cost is not None
            else "provider_usage_unavailable"
            if vqa_calls
            else "not_applicable"
        ),
        "cache_hits": _nonnegative_int(cache_hits),
        "cache_misses": _nonnegative_int(cache_misses),
        "render_ms": _nonnegative_ms(render_ms),
        "local_inspection_ms": _nonnegative_ms(local_inspection_ms),
        "montage_ms": _nonnegative_ms(montage_ms),
        "persist_ms": _nonnegative_ms(persist_ms),
        "total_ms": _nonnegative_ms(total_ms),
        "ocr_ms": _nonnegative_ms(ocr_total_ms),
        "ocr_executed": bool(ocr_executed),
        "ocr_requests": _nonnegative_int(ocr_requests),
        "ocr_calls": _nonnegative_int(ocr_calls),
        "ocr_cache_hits": _nonnegative_int(ocr_cache_hits),
        "ocr_total_ms": _nonnegative_ms(ocr_total_ms),
        "critique_ms": _nonnegative_ms(critique_ms),
        "revision_ms": _nonnegative_ms(revision_ms),
        "patch_attempted": _nonnegative_int(patch_attempted),
        "patch_accepted": _nonnegative_int(patch_accepted),
        "patch_rejected": _nonnegative_int(patch_rejected),
        "fallback_attempted": _nonnegative_int(fallback_attempted),
        "fallback_succeeded": _nonnegative_int(fallback_succeeded),
        "fallback_failed": _nonnegative_int(fallback_failed),
        "local_high_remaining": _nonnegative_int(local_high_remaining),
        "indeterminate_count": _nonnegative_int(indeterminate_count),
    }
