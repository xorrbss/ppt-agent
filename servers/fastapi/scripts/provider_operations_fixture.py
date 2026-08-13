"""Fail-closed validation for the provider operations fixture."""

from __future__ import annotations

import math
import re
from typing import Any, Iterable
from urllib.parse import urlsplit


FORBIDDEN_KEY = re.compile(
    r"(?:prompt|content|html|ocr|document|source|url|header|request.?id|exception|error.?body|"
    r"api.?key|authorization|credential|secret|password|(?:access|auth)[_-]?token)",
    re.I,
)
SENSITIVE_VALUE = re.compile(
    r"(?:\bsk-[A-Za-z0-9_-]{8,}|\bbearer\s+[A-Za-z0-9._~-]{8,}|"
    r"\b(?:api[_-]?key|secret|password)\s*[:=]\s*\S+)",
    re.I,
)
SAFE_ID = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
ALLOWED_ROOT_KEYS = frozenset(
    {
        "schema_version",
        "corpus_id",
        "fixture_kind",
        "slides",
        "runs",
        "pricing",
        "capacity",
        "chaos",
        "kill_conditions",
        "staging",
    }
)
ALLOWED_SLIDE_KEYS = frozenset(
    {"slide_id", "archetype", "complexity_units", "export_units"}
)
ALLOWED_PRICING_KEYS = frozenset(
    {
        "version",
        "input_microunits_per_1k",
        "output_microunits_per_1k",
        "image_microunits_each",
    }
)
ALLOWED_CAPACITY_KEYS = frozenset(
    {
        "input_tokens_per_complexity",
        "output_tokens_per_complexity",
        "images_per_slide",
        "max_attempts_per_slide",
        "provider_rpm",
        "concurrency",
    }
)
ALLOWED_CHAOS_KEYS = frozenset({"retry", "cancel_resume"})
ALLOWED_FAULT_KEYS = frozenset({"run", "slide_id", "attempt"})
ALLOWED_KILL_KEYS = frozenset(
    {
        "estimated_cost_microunits",
        "retry_amplification",
        "critical_path_p99_ms",
        "ram_peak_mib",
        "export_p99_ms",
    }
)
ALLOWED_STAGING_KEYS = frozenset({"hostname_allowlist", "endpoint", "currency"})


def _number(value: Any, field: str, *, minimum: int = 0) -> int:
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or value < minimum
        or value > 1_000_000_000
    ):
        raise ValueError(f"{field} must be an integer from {minimum} to 1000000000")
    return value


def _positive_limit(value: Any, field: str) -> int | float:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
        or value <= 0
    ):
        raise ValueError(f"{field} must be a positive finite number")
    return value


def _safe_id(value: Any, field: str) -> str:
    if (
        not isinstance(value, str)
        or not SAFE_ID.fullmatch(value)
        or SENSITIVE_VALUE.search(value)
    ):
        raise ValueError(f"{field} must be a non-sensitive lowercase identifier")
    return value


def _reject_sensitive_tree(value: Any, prefix: str = "fixture") -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            if not isinstance(key, str) or FORBIDDEN_KEY.search(key):
                raise ValueError(f"privacy-forbidden field at {prefix}.{key}")
            _reject_sensitive_tree(child, f"{prefix}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _reject_sensitive_tree(child, f"{prefix}[{index}]")
    elif isinstance(value, str) and SENSITIVE_VALUE.search(value):
        raise ValueError(f"sensitive-looking value at {prefix}")


def _exact_keys(
    value: Any, allowed: frozenset[str], field: str
) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{field} must be an object")
    unknown = sorted(set(value) - allowed)
    if unknown:
        raise ValueError(f"{field} has unknown fields: {', '.join(unknown)}")
    return value


def _fault(
    value: Any, name: str, slide_ids: set[str], runs: int
) -> dict[str, Any]:
    item = _exact_keys(value, ALLOWED_FAULT_KEYS, name)
    run = _number(item.get("run"), f"{name}.run", minimum=1)
    attempt = _number(item.get("attempt"), f"{name}.attempt", minimum=1)
    slide_id = _safe_id(item.get("slide_id"), f"{name}.slide_id")
    if run > runs or slide_id not in slide_ids:
        raise ValueError(f"{name} must target a fixture run and slide")
    return {"run": run, "slide_id": slide_id, "attempt": attempt}


def validate_fixture(raw: Any) -> dict[str, Any]:
    """Fail closed on corpus shape, privacy, pricing, and operational policy."""
    root = _exact_keys(raw, ALLOWED_ROOT_KEYS, "fixture")
    _reject_sensitive_tree(root)
    if (
        root.get("schema_version") != 1
        or root.get("fixture_kind") != "fixed_non_sensitive_90"
    ):
        raise ValueError(
            "fixture must use schema_version 1 and fixed_non_sensitive_90"
        )
    corpus_id = _safe_id(root.get("corpus_id"), "corpus_id")
    runs = _number(root.get("runs"), "runs", minimum=1)
    if runs != 3:
        raise ValueError("the operational fixture must run exactly 3 times")
    raw_slides = root.get("slides")
    if not isinstance(raw_slides, list) or len(raw_slides) != 90:
        raise ValueError(
            "the fixed operational corpus must contain exactly 90 slides"
        )
    slides = []
    seen: set[str] = set()
    for index, raw_slide in enumerate(raw_slides):
        slide = _exact_keys(raw_slide, ALLOWED_SLIDE_KEYS, f"slides[{index}]")
        slide_id = _safe_id(slide.get("slide_id"), f"slides[{index}].slide_id")
        if slide_id in seen:
            raise ValueError(f"duplicate slide_id: {slide_id}")
        seen.add(slide_id)
        slides.append(
            {
                "slide_id": slide_id,
                "archetype": _safe_id(
                    slide.get("archetype"), f"slides[{index}].archetype"
                ),
                "complexity_units": _number(
                    slide.get("complexity_units"),
                    f"slides[{index}].complexity_units",
                    minimum=1,
                ),
                "export_units": _number(
                    slide.get("export_units"),
                    f"slides[{index}].export_units",
                    minimum=1,
                ),
            }
        )
    pricing = _exact_keys(root.get("pricing"), ALLOWED_PRICING_KEYS, "pricing")
    pricing = {
        "version": _safe_id(pricing.get("version"), "pricing.version"),
        **{
            key: _number(pricing.get(key), f"pricing.{key}")
            for key in sorted(ALLOWED_PRICING_KEYS)
            if key != "version"
        },
    }
    capacity = _exact_keys(root.get("capacity"), ALLOWED_CAPACITY_KEYS, "capacity")
    capacity = {
        key: _number(capacity.get(key), f"capacity.{key}", minimum=1)
        for key in sorted(ALLOWED_CAPACITY_KEYS)
    }
    chaos = _exact_keys(root.get("chaos"), ALLOWED_CHAOS_KEYS, "chaos")
    chaos = {
        "retry": _fault(chaos.get("retry"), "chaos.retry", seen, runs),
        "cancel_resume": _fault(
            chaos.get("cancel_resume"), "chaos.cancel_resume", seen, runs
        ),
    }
    if capacity["max_attempts_per_slide"] < 2:
        raise ValueError(
            "capacity.max_attempts_per_slide must permit one bounded recovery attempt"
        )
    if any(
        item["attempt"] >= capacity["max_attempts_per_slide"]
        for item in chaos.values()
    ):
        raise ValueError(
            "chaos failure attempts must leave room for one bounded recovery attempt"
        )
    if (chaos["retry"]["run"], chaos["retry"]["slide_id"]) == (
        chaos["cancel_resume"]["run"],
        chaos["cancel_resume"]["slide_id"],
    ):
        raise ValueError("retry and cancel/resume chaos targets must be distinct")
    kill = _exact_keys(
        root.get("kill_conditions"), ALLOWED_KILL_KEYS, "kill_conditions"
    )
    kill = {
        key: _positive_limit(kill.get(key), f"kill_conditions.{key}")
        for key in sorted(ALLOWED_KILL_KEYS)
    }
    staging = _exact_keys(root.get("staging"), ALLOWED_STAGING_KEYS, "staging")
    allowlist = staging.get("hostname_allowlist")
    if not isinstance(allowlist, list) or not allowlist:
        raise ValueError("staging.hostname_allowlist must be a non-empty list")
    safe_hosts = [
        _safe_id(host, "staging.hostname_allowlist") for host in allowlist
    ]
    if len(safe_hosts) != len(set(safe_hosts)):
        raise ValueError("staging.hostname_allowlist must not contain duplicates")
    endpoint = validate_endpoint(staging.get("endpoint"), safe_hosts)
    currency = staging.get("currency")
    if currency != "USD":
        raise ValueError("staging.currency must be USD")
    return {
        "schema_version": 1,
        "corpus_id": corpus_id,
        "fixture_kind": "fixed_non_sensitive_90",
        "slides": slides,
        "runs": runs,
        "pricing": pricing,
        "capacity": capacity,
        "chaos": chaos,
        "kill_conditions": kill,
        "staging": {
            "hostname_allowlist": safe_hosts,
            "endpoint": endpoint,
            "currency": currency,
        },
    }


def validate_endpoint(endpoint: Any, hostname_allowlist: Iterable[str]) -> str:
    """Accept only a literal HTTPS origin whose hostname is explicitly allowlisted."""
    if (
        not isinstance(endpoint, str)
        or len(endpoint) > 200
        or SENSITIVE_VALUE.search(endpoint)
    ):
        raise ValueError("staging endpoint must be a bounded, non-sensitive string")
    parsed = urlsplit(endpoint)
    host = (parsed.hostname or "").lower().rstrip(".")
    allowed = {item.lower().rstrip(".") for item in hostname_allowlist}
    if (
        parsed.scheme != "https"
        or not host
        or host not in allowed
        or parsed.username is not None
        or parsed.password is not None
        or parsed.port not in (None, 443)
        or parsed.path not in ("", "/")
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError(
            "staging endpoint must be an allowlisted HTTPS origin on port 443"
        )
    if (
        host == "localhost"
        or host.endswith(".localhost")
        or re.fullmatch(r"[0-9a-f:.]+", host)
    ):
        raise ValueError("localhost and IP-literal staging endpoints are forbidden")
    return f"https://{host}"


__all__ = ["validate_endpoint", "validate_fixture"]
