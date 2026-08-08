"""Request-local OCR gating for authored quality-review candidates.

Raw OCR text is intentionally retained only inside :class:`RequestOcrRuntime`.
Every payload returned by this module is safe for persistence and UI delivery.
"""

import asyncio
import hashlib
import logging
import re
import time
from collections.abc import Awaitable, Callable, Iterable, Mapping
from typing import Any

from utils.authored_ocr_quality import (
    AuthoredOcrCache,
    OcrQualityResult,
    compare_authored_ocr,
    compare_expected_candidate,
    expected_text_from_browser_metrics,
    inspect_tesseract,
)
from utils.llm_calls.author_slide import EXACT_RECONSTRUCTION
from utils.ocr_language import presentation_language_to_ocr_code

InspectOcr = Callable[..., Awaitable[OcrQualityResult]]
_SAFE_LANGUAGE = re.compile(r"^[A-Za-z0-9_]{1,32}$")


def _safe_languages(values: Iterable[str]) -> list[str]:
    return sorted({value for value in values if _SAFE_LANGUAGE.fullmatch(value)})


def _build_ocr_readiness() -> dict[str, Any]:
    """Inspect the production OCR contract without exposing commands or paths."""
    started_at = time.perf_counter()
    requested = "kor+eng"
    try:
        runtime = inspect_tesseract(
            explicit_command=None,
            tessdata_dir=None,
            language=requested,
            timeout_seconds=5.0,
        )
        requested_languages = _safe_languages(runtime.requested_languages)
        available_languages = _safe_languages(runtime.available_languages)
        missing_languages = sorted(set(requested_languages) - set(available_languages))
        if runtime.status == "ready":
            code = "ocr_ready"
        elif runtime.status == "language_missing":
            code = "ocr_language_missing"
        elif runtime.failure_reason == "tesseract_executable_not_found":
            code = "ocr_executable_missing"
        else:
            code = "ocr_runtime_check_failed"
        ready = runtime.status == "ready" and not missing_languages
        tessdata_configured = runtime.tessdata_dir is not None
    except Exception:  # noqa: BLE001 - readiness must fail closed
        requested_languages = _safe_languages(requested.split("+"))
        available_languages = []
        missing_languages = requested_languages
        code = "ocr_runtime_check_failed"
        ready = False
        tessdata_configured = False
    return {
        "schema_version": 1,
        "component": "authored_quality_ocr",
        "ready": ready,
        "code": code,
        "requested_languages": requested_languages,
        "available_languages": available_languages,
        "missing_languages": missing_languages,
        "tessdata_configured": tessdata_configured,
        "inspection_ms": round((time.perf_counter() - started_at) * 1000, 3),
    }


async def get_ocr_readiness() -> dict[str, Any]:
    """Run the bounded CLI probe off the event loop for readiness callers."""
    return await asyncio.to_thread(_build_ocr_readiness)


def ocr_language(language: str | None) -> str:
    normalized = str(language or "").strip().casefold()
    if normalized in {"ko", "kor", "kr", "korean", "한국어"} or normalized.startswith(
        "korean ("
    ):
        code = "kor"
    else:
        code = presentation_language_to_ocr_code(language)
    requested = [part for part in code.replace(",", "+").split("+") if part]
    if "eng" not in requested:
        requested.append("eng")
    return "+".join(dict.fromkeys(requested))


def safe_ocr_payload(
    result: OcrQualityResult | None,
    *,
    role: str,
    cache_hit: bool,
    failure_reason: str | None = None,
) -> dict[str, Any]:
    if result is None:
        payload: dict[str, Any] = {
            "available": False,
            "status": "error",
            "failure_reason": failure_reason or "ocr_runtime_error",
            "text_digest": None,
            "metrics": {},
            "calls": 0,
            "cache_hits": 0,
            "elapsed_ms": 0.0,
            "returncode": None,
        }
    else:
        payload = dict(result.to_dict())
    for key in ("text", "raw_text", "ocr_text"):
        payload.pop(key, None)
    payload.update(
        {
            "role": role,
            "availability": bool(payload.get("available")),
            "request_cache_hit": cache_hit,
        }
    )
    return payload


class RequestOcrRuntime:
    """Request-local image cache and privacy-safe OCR telemetry accumulator."""

    def __init__(
        self,
        language: str,
        *,
        inspect_ocr: InspectOcr,
        logger: logging.Logger,
    ) -> None:
        self.language = language
        self._inspect_ocr = inspect_ocr
        self._logger = logger
        self.utility_cache = AuthoredOcrCache()
        self._images: dict[str, OcrQualityResult] = {}
        self.requests = 0
        self.calls = 0
        self.cache_hits = 0
        self.total_ms = 0.0

    @staticmethod
    def _with_expected_metrics(
        result: OcrQualityResult, expected_text: str | None
    ) -> OcrQualityResult:
        if expected_text is None:
            return result
        metrics = compare_authored_ocr(expected_text, result, mode="expected").metrics
        return OcrQualityResult(
            **{
                **result.__dict__,
                "metrics": metrics,
                "elapsed_ms": 0.0,
            }
        )

    async def inspect(
        self,
        image: bytes,
        *,
        expected_text: str | None,
        role: str,
    ) -> tuple[OcrQualityResult | None, dict[str, Any]]:
        self.requests += 1
        fingerprint = hashlib.sha256(image).hexdigest()
        cached = self._images.get(fingerprint)
        if cached is not None:
            self.cache_hits += 1
            result = self._with_expected_metrics(cached, expected_text)
            return result, safe_ocr_payload(result, role=role, cache_hit=True)

        started_at = time.perf_counter()
        result: OcrQualityResult | None = None
        failure_reason: str | None = None
        utility_calls_before = self.utility_cache.calls
        utility_hits_before = self.utility_cache.cache_hits
        try:
            result = await self._inspect_ocr(
                image,
                expected_text=expected_text,
                language=self.language,
                cache=self.utility_cache,
            )
        except Exception as error:  # noqa: BLE001 - optional OCR fails closed
            failure_reason = f"ocr_runtime_error:{type(error).__name__}"
            self._logger.warning(
                "Authored OCR inspection failed role=%s error_type=%s",
                role,
                type(error).__name__,
            )
        self.total_ms += (time.perf_counter() - started_at) * 1000
        self.calls += max(0, self.utility_cache.calls - utility_calls_before)
        self.cache_hits += max(0, self.utility_cache.cache_hits - utility_hits_before)
        if result is not None and result.available:
            self._images[fingerprint] = OcrQualityResult(
                **{**result.__dict__, "metrics": {}}
            )
        return result, safe_ocr_payload(
            result,
            role=role,
            cache_hit=False,
            failure_reason=failure_reason,
        )

    def summary(self) -> dict[str, Any]:
        return {
            "ocr_executed": self.requests > 0,
            "ocr_requests": self.requests,
            "ocr_calls": self.calls,
            "ocr_cache_hits": self.cache_hits,
            "ocr_total_ms": round(self.total_ms, 3),
        }


def ocr_unavailable_reason(
    results: Iterable[OcrQualityResult | None],
) -> str | None:
    unavailable = [
        result for result in results if result is None or not result.available
    ]
    if not unavailable:
        return None
    for result in unavailable:
        status = "" if result is None else str(result.status or "")
        detail = "" if result is None else str(result.failure_reason or "")
        if "timeout" in f"{status} {detail}".casefold():
            return "ocr_timeout"
    return "ocr_unavailable"


def ocr_omissions(metrics: Mapping[str, Any] | None) -> dict[str, int] | None:
    if not metrics or not metrics.get("available", True):
        return None
    classes = metrics.get("character_classes")
    if not isinstance(classes, Mapping):
        return None

    def missing(category: str) -> int:
        item = classes.get(category)
        return (
            max(0, int(item.get("missing_or_substituted", 0)))
            if isinstance(item, Mapping)
            else 0
        )

    return {
        "text": max(
            0,
            int(metrics.get("deletions", 0)) + int(metrics.get("substitutions", 0)),
        ),
        "numbers": missing("digits"),
        "symbols": missing("symbols"),
        "korean": missing("korean"),
        "english": missing("english"),
    }


def normalize_ocr_check(entry: dict[str, Any]) -> None:
    """Expose the compact top-level contract consumed by telemetry and the UI."""
    if not entry.get("required"):
        entry.update(
            {
                "status": "routed_skip",
                "executed": False,
                "available": False,
                "comparison": None,
                "baseline_omissions": None,
                "candidate_omissions": None,
            }
        )
        return

    payloads = [
        payload
        for payload in (
            entry.get("reference"),
            entry.get("baseline"),
            entry.get("candidate"),
        )
        if isinstance(payload, Mapping)
    ]
    executed = bool(payloads)
    unavailable = [payload for payload in payloads if not payload.get("available")]
    if unavailable:
        timed_out = any(
            "timeout"
            in f"{payload.get('status', '')} {payload.get('failure_reason', '')}".casefold()
            for payload in unavailable
        )
        status = "timeout" if timed_out else "unavailable"
    else:
        status = "executed" if executed else "routed_skip"

    candidate = entry.get("candidate")
    comparison = (
        candidate.get("comparison") if isinstance(candidate, Mapping) else None
    ) or (entry.get("initial_decision") or {}).get("comparison")
    comparison_metrics = (
        comparison.get("metrics") if isinstance(comparison, Mapping) else None
    )
    baseline_metrics = (
        (entry.get("baseline") or {}).get("metrics")
        if isinstance(entry.get("baseline"), Mapping)
        else None
    )
    candidate_metrics = (
        candidate.get("metrics") if isinstance(candidate, Mapping) else None
    )
    if isinstance(comparison_metrics, Mapping):
        if isinstance(comparison_metrics.get("baseline"), Mapping):
            baseline_metrics = comparison_metrics["baseline"]
        if isinstance(comparison_metrics.get("candidate"), Mapping):
            candidate_metrics = comparison_metrics["candidate"]
    entry.update(
        {
            "status": status,
            "executed": executed,
            "available": executed and not unavailable,
            "comparison": comparison,
            "baseline_omissions": ocr_omissions(baseline_metrics),
            "candidate_omissions": ocr_omissions(candidate_metrics),
        }
    )


async def evaluate_candidate_ocr(
    *,
    runtime: RequestOcrRuntime,
    entry: dict[str, Any],
    reference_mode: str,
    source_image: bytes | None,
    baseline_image: bytes,
    candidate_image: bytes,
    baseline_metrics: Mapping[str, Any] | None,
    candidate_metrics: Mapping[str, Any] | None,
    signals: Iterable[str],
    path: str,
) -> tuple[bool, bool, str | None]:
    combined_signals = sorted(set(entry.get("signals") or []) | set(signals))
    required = bool(combined_signals)
    entry["signals"] = combined_signals
    entry["required"] = required
    if not required:
        entry["skip_reason"] = "no_text_fidelity_signal"
        entry["decision"] = {
            "accepted": None,
            "reason": "ocr_not_required",
            "path": path,
        }
        normalize_ocr_check(entry)
        return False, True, None

    entry["skip_reason"] = None
    exact = reference_mode == EXACT_RECONSTRUCTION
    baseline_expected = None
    candidate_expected = None
    if not exact:
        baseline_expected = (
            expected_text_from_browser_metrics(dict(baseline_metrics or {})) or None
        )
        candidate_expected = (
            expected_text_from_browser_metrics(dict(candidate_metrics or {})) or None
        )

    baseline_result, baseline_payload = await runtime.inspect(
        baseline_image,
        expected_text=baseline_expected,
        role="baseline_render",
    )
    entry["baseline"] = baseline_payload
    source_result: OcrQualityResult | None = None
    if exact:
        if source_image is None:
            source_payload = safe_ocr_payload(
                None,
                role="source_reference",
                cache_hit=False,
                failure_reason="exact_source_missing",
            )
        else:
            source_result, source_payload = await runtime.inspect(
                source_image,
                expected_text=None,
                role="source_reference",
            )
        entry["reference"] = source_payload

    candidate_result, candidate_payload = await runtime.inspect(
        candidate_image,
        expected_text=candidate_expected,
        role="candidate_render",
    )
    failure = ocr_unavailable_reason(
        [source_result, baseline_result, candidate_result]
        if exact
        else [baseline_result, candidate_result]
    )
    comparison_payload: dict[str, Any] | None = None
    if failure is not None:
        accepted = False
        reason = failure
    elif exact:
        comparison = compare_expected_candidate(
            source_result, baseline_result, candidate_result
        )
        comparison_payload = comparison.to_dict()
        initial_comparison = (entry.get("initial_decision") or {}).get(
            "comparison"
        ) or {}
        initial_loss = not bool(initial_comparison.get("accepted", False))
        accepted = bool(
            comparison.accepted
            and comparison.no_regression
            and (comparison.strict_improvement if initial_loss else True)
        )
        comparison_payload["strict_improvement_required"] = initial_loss
        reason = (
            "ocr_improved"
            if accepted and comparison.strict_improvement
            else (
                "ocr_content_preserved"
                if accepted
                else (
                    "ocr_regression"
                    if not comparison.no_regression
                    else "ocr_not_improved"
                )
            )
        )
    else:
        comparison = compare_authored_ocr(
            baseline_result, candidate_result, mode="baseline"
        )
        comparison_payload = comparison.to_dict()
        accepted = bool(comparison.accepted and comparison.no_regression)
        reason = "ocr_content_preserved" if accepted else "ocr_regression"

    candidate_payload.update({"path": path, "comparison": comparison_payload})
    decision = {"accepted": accepted, "reason": reason, "path": path}
    attempt = {"candidate": candidate_payload, "decision": decision}
    entry.setdefault("candidate_attempts", []).append(attempt)
    entry["candidate"] = candidate_payload
    entry["decision"] = decision
    normalize_ocr_check(entry)
    return True, accepted, None if accepted else reason


async def capture_initial_ocr(
    *,
    runtime: RequestOcrRuntime,
    slide_index: int,
    reference_mode: str,
    source_image: bytes | None,
    baseline_image: bytes,
    baseline_metrics: Mapping[str, Any] | None,
    signals: Iterable[str],
) -> dict[str, Any]:
    routed_signals = sorted(set(signals))
    exact = reference_mode == EXACT_RECONSTRUCTION
    entry: dict[str, Any] = {
        "slide_index": slide_index,
        "mode": "expected" if exact else "baseline",
        "required": bool(routed_signals),
        "signals": routed_signals,
        "skip_reason": None if routed_signals else "no_text_fidelity_signal",
        "reference": None,
        "baseline": None,
        "candidate": None,
        "candidate_attempts": [],
        "initial_decision": {
            "accepted": None,
            "reason": "awaiting_candidate" if routed_signals else "ocr_not_required",
        },
        "decision": {
            "accepted": None,
            "reason": "candidate_not_run" if routed_signals else "ocr_not_required",
        },
    }
    if not routed_signals:
        normalize_ocr_check(entry)
        return entry

    expected_text = None
    if not exact:
        expected_text = (
            expected_text_from_browser_metrics(dict(baseline_metrics or {})) or None
        )
    baseline_result, baseline_payload = await runtime.inspect(
        baseline_image,
        expected_text=expected_text,
        role="baseline_render",
    )
    entry["baseline"] = baseline_payload
    if exact:
        source_result: OcrQualityResult | None = None
        if source_image is None:
            source_payload = safe_ocr_payload(
                None,
                role="source_reference",
                cache_hit=False,
                failure_reason="exact_source_missing",
            )
        else:
            source_result, source_payload = await runtime.inspect(
                source_image,
                expected_text=None,
                role="source_reference",
            )
        entry["reference"] = source_payload
        unavailable = ocr_unavailable_reason([source_result, baseline_result])
        if unavailable is not None:
            entry["initial_decision"] = {
                "accepted": False,
                "reason": unavailable,
                "comparison": None,
            }
        else:
            comparison = compare_authored_ocr(
                source_result, baseline_result, mode="expected"
            )
            entry["initial_decision"] = {
                "accepted": comparison.accepted,
                "reason": comparison.reason,
                "comparison": comparison.to_dict(),
            }
    elif baseline_result is None or not baseline_result.available:
        entry["initial_decision"] = {
            "accepted": False,
            "reason": ocr_unavailable_reason([baseline_result]),
            "comparison": None,
        }
    else:
        entry["initial_decision"] = {
            "accepted": True,
            "reason": "baseline_captured",
            "comparison": None,
        }
    normalize_ocr_check(entry)
    return entry
