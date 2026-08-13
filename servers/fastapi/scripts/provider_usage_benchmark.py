#!/usr/bin/env python3
"""Analyze privacy-safe generation usage receipts without contacting providers.

The command accepts either a JSON receipt export or a declarative synthetic
fixture.  It deliberately does not import provider clients, read credentials, or
open network connections.  JSON input is fail-closed: only the receipt scalar
fields below are accepted, so a prompt, HTML, URL, request header/ID, exception
body, or API key cannot accidentally enter a benchmark report.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import uuid
from collections import Counter, defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterable

RECEIPT_FIELDS = frozenset(
    {
        "job_id", "operation", "stage", "provider", "model", "receipt_key_hash",
        "retry_class", "outcome", "latency_ms", "usage_state", "input_tokens", "output_tokens",
        "request_count", "reported_cost_microunits", "estimated_cost_microunits",
        "pricing_version",
    }
)
REQUIRED_FIELDS = frozenset(
    {
        "job_id", "operation", "stage", "provider", "model", "receipt_key_hash",
        "retry_class", "outcome", "latency_ms", "usage_state", "request_count",
    }
)
FORBIDDEN_KEY = re.compile(
    r"(?:prompt|html|ocr|source|url|path|header|request.?id|exception|error.?body|"
    r"api.?key|authorization|credential|secret|password|(?:access|auth)[_-]?token)", re.I
)
SENSITIVE_VALUE = re.compile(
    r"(?:\bsk-[A-Za-z0-9_-]{8,}|\bbearer\s+[A-Za-z0-9._~-]{8,}|"
    r"\b(?:api[_-]?key|secret|password)\s*[:=]\s*\S+)", re.I
)
USAGE_STATES = frozenset({"reported", "estimated", "unavailable"})
RETRY_CLASSES = frozenset({"initial", "retry", "resume", "recovery"})
OUTCOMES = frozenset({"succeeded", "failed", "cancelled"})
OPERATION_RE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}$")
HASH_RE = re.compile(r"^[a-f0-9]{64}$")
MAX_NUMBER = 1_000_000_000


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _safe_string(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value or len(value) > 200 or SENSITIVE_VALUE.search(value):
        raise ValueError(f"{field} must be a bounded, non-sensitive string")
    return value


def _number(value: Any, field: str, *, nullable: bool = False, whole: bool = False) -> int | float | None:
    if value is None and nullable:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f"{field} must be a finite number")
    if value < 0 or value > MAX_NUMBER or (whole and int(value) != value):
        raise ValueError(f"{field} must be a bounded nonnegative{' integer' if whole else ' number'}")
    return int(value) if whole else float(value)


def validate_receipt(raw: Any) -> dict[str, Any]:
    """Return a normalized receipt after fail-closed privacy/type validation."""
    if not isinstance(raw, dict):
        raise ValueError("each receipt must be an object")
    keys = set(raw)
    forbidden = sorted(key for key in keys if FORBIDDEN_KEY.search(key))
    unknown = sorted(keys - RECEIPT_FIELDS)
    missing = sorted(REQUIRED_FIELDS - keys)
    if forbidden:
        raise ValueError(f"receipt has privacy-forbidden fields: {', '.join(forbidden)}")
    if unknown:
        raise ValueError(f"receipt has unknown fields: {', '.join(unknown)}")
    if missing:
        raise ValueError(f"receipt is missing fields: {', '.join(missing)}")

    try:
        uuid.UUID(str(raw["job_id"]))
    except (TypeError, ValueError) as error:
        raise ValueError("job_id must be a UUID") from error
    normalized: dict[str, Any] = {"job_id": str(raw["job_id"])}
    for field in ("operation", "stage"):
        value = _safe_string(raw[field], field)
        if not OPERATION_RE.fullmatch(value):
            raise ValueError(f"{field} must be a safe operation identifier")
        normalized[field] = value
    for field in ("provider", "model"):
        value = _safe_string(raw[field], field)
        if not IDENTIFIER_RE.fullmatch(value):
            raise ValueError(f"{field} must be a safe identifier")
        normalized[field] = value
    receipt_key_hash = _safe_string(raw["receipt_key_hash"], "receipt_key_hash")
    if not HASH_RE.fullmatch(receipt_key_hash):
        raise ValueError("receipt_key_hash must be a SHA-256 hex digest")
    normalized["receipt_key_hash"] = receipt_key_hash
    retry_class = _safe_string(raw["retry_class"], "retry_class")
    if retry_class not in RETRY_CLASSES:
        raise ValueError("retry_class must be allowlisted")
    normalized["retry_class"] = retry_class
    outcome = _safe_string(raw["outcome"], "outcome")
    if outcome not in OUTCOMES:
        raise ValueError("outcome must be allowlisted")
    normalized["outcome"] = outcome
    normalized["latency_ms"] = _number(raw["latency_ms"], "latency_ms", whole=True)
    normalized["request_count"] = _number(raw["request_count"], "request_count", whole=True)
    if normalized["request_count"] < 1:
        raise ValueError("request_count must be a positive integer")
    state = raw["usage_state"]
    if state not in USAGE_STATES:
        raise ValueError("usage_state must be reported, estimated, or unavailable")
    normalized["usage_state"] = state
    for field in ("input_tokens", "output_tokens", "reported_cost_microunits", "estimated_cost_microunits"):
        normalized[field] = _number(raw.get(field), field, nullable=True, whole=True)
    pricing_version = raw.get("pricing_version")
    normalized["pricing_version"] = (
        None
        if pricing_version is None
        else _safe_string(pricing_version, "pricing_version")
    )
    if normalized["pricing_version"] is not None and not IDENTIFIER_RE.fullmatch(normalized["pricing_version"]):
        raise ValueError("pricing_version must be a safe identifier")
    if state == "unavailable" and any(
        normalized[field] is not None
        for field in ("input_tokens", "output_tokens", "reported_cost_microunits", "estimated_cost_microunits")
    ):
        raise ValueError("unavailable usage must use null token/cost fields, never zero")
    if state == "reported" and normalized["estimated_cost_microunits"] is not None:
        raise ValueError("reported usage cannot also contain estimated cost")
    if state == "estimated" and normalized["reported_cost_microunits"] is not None:
        raise ValueError("estimated usage cannot also contain reported cost")
    return normalized


def _percentile(values: Iterable[int | float], percentile: float) -> float | None:
    ordered = sorted(float(value) for value in values)
    if not ordered:
        return None
    # Nearest rank makes small offline fixture results deterministic and legible.
    index = max(0, math.ceil(percentile * len(ordered)) - 1)
    return round(ordered[index], 3)


def _safe_receipts(payload: Any) -> list[dict[str, Any]]:
    records = payload.get("receipts") if isinstance(payload, dict) else payload
    if not isinstance(records, list):
        raise ValueError("receipt input must be a list or an object with a receipts list")
    return [validate_receipt(record) for record in records]


def synthetic_receipts(fixture: dict[str, Any]) -> list[dict[str, Any]]:
    """Expand a non-sensitive fixture into deterministic receipt-shaped records."""
    allowed = {"schema_version", "fixture_kind", "slide_count", "runs", "timeout_policy", "author"}
    unknown = set(fixture) - allowed
    if unknown or any(FORBIDDEN_KEY.search(key) for key in fixture):
        raise ValueError("synthetic fixture has unknown or privacy-forbidden fields")
    slides = _number(fixture.get("slide_count"), "slide_count", whole=True)
    runs = _number(fixture.get("runs"), "runs", whole=True)
    if not slides or not runs:
        raise ValueError("synthetic fixture needs positive slide_count and runs")
    author = fixture.get("author")
    if not isinstance(author, dict):
        raise ValueError("synthetic fixture requires an author object")
    author_allowed = {
        "operation", "stage", "provider", "model", "latency_ms", "input_tokens",
        "output_tokens", "estimated_cost_microunits", "pricing_version", "retry_slide",
    }
    if set(author) - author_allowed or any(FORBIDDEN_KEY.search(key) for key in author):
        raise ValueError("synthetic author fields are not safe")
    retry_slide = _number(author.get("retry_slide", 0), "retry_slide", whole=True)
    if retry_slide and retry_slide > slides:
        raise ValueError("retry_slide must be within slide_count")
    records: list[dict[str, Any]] = []
    for run in range(1, runs + 1):
        job_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"presenton-offline-90x3-run-{run}"))
        for slide in range(1, slides + 1):
            key = hashlib.sha256(f"offline:{run}:{slide}".encode()).hexdigest()
            records.append(
                validate_receipt(
                    {
                        "job_id": job_id, "operation": author["operation"], "stage": author["stage"],
                        "provider": author["provider"], "model": author["model"], "receipt_key_hash": key,
                        "retry_class": "retry" if slide == retry_slide else "initial",
                        "outcome": "succeeded",
                        "latency_ms": author["latency_ms"], "usage_state": "estimated",
                        "input_tokens": author["input_tokens"], "output_tokens": author["output_tokens"],
                        "request_count": 2 if slide == retry_slide else 1,
                        "reported_cost_microunits": None,
                        "estimated_cost_microunits": author["estimated_cost_microunits"],
                        "pricing_version": author["pricing_version"],
                    }
                )
            )
    return records


def analyze_receipts(
    receipts: Iterable[dict[str, Any]], *, timeout_seconds: int, ui_poll_timeout_seconds: int | None = None
) -> dict[str, Any]:
    """Aggregate receipts once per idempotency key; duplicates never add cost."""
    if timeout_seconds <= 0 or (ui_poll_timeout_seconds is not None and ui_poll_timeout_seconds <= 0):
        raise ValueError("timeout values must be positive")
    deduped: dict[str, dict[str, Any]] = {}
    duplicates = 0
    for receipt in receipts:
        key = receipt["receipt_key_hash"]
        existing = deduped.get(key)
        if existing is None:
            deduped[key] = receipt
        elif existing != receipt:
            raise ValueError("same receipt_key_hash has conflicting receipt content")
        else:
            duplicates += 1
    rows = list(deduped.values())
    by_stage: dict[str, list[float]] = defaultdict(list)
    job_stages: dict[str, dict[str, float]] = defaultdict(dict)
    retry_classes: Counter[str] = Counter()
    states: Counter[str] = Counter()
    outcomes: Counter[str] = Counter()
    request_count = 0
    input_values: list[int] = []
    output_values: list[int] = []
    reported_costs: list[int] = []
    estimated_costs: list[int] = []
    cost_by_pricing_version: dict[str, dict[str, int]] = {}
    for row in rows:
        latency = float(row["latency_ms"])
        by_stage[row["stage"]].append(latency)
        job_stages[row["job_id"]][row["stage"]] = max(job_stages[row["job_id"]].get(row["stage"], 0.0), latency)
        retry_classes[row["retry_class"]] += 1
        states[row["usage_state"]] += 1
        outcomes[row["outcome"]] += 1
        request_count += int(row["request_count"])
        if row["input_tokens"] is not None:
            input_values.append(int(row["input_tokens"]))
        if row["output_tokens"] is not None:
            output_values.append(int(row["output_tokens"]))
        if row["reported_cost_microunits"] is not None:
            reported_costs.append(int(row["reported_cost_microunits"]))
        if row["estimated_cost_microunits"] is not None:
            estimated_costs.append(int(row["estimated_cost_microunits"]))
        if row["pricing_version"] is not None and (
            row["reported_cost_microunits"] is not None
            or row["estimated_cost_microunits"] is not None
        ):
            version = cost_by_pricing_version.setdefault(
                row["pricing_version"],
                {"reported_cost_microunits": 0, "estimated_cost_microunits": 0},
            )
            for field in version:
                if row[field] is not None:
                    version[field] += int(row[field])
    critical_paths = [sum(stages.values()) for stages in job_stages.values()]
    stage_latency = {
        stage: {
            "p50": _percentile(values, .50),
            "p95": _percentile(values, .95),
            "p99": _percentile(values, .99),
            "samples": len(values),
        }
        for stage, values in sorted(by_stage.items())
    }
    critical_path_p99 = _percentile(critical_paths, .99)
    # Receipts contain call durations, not stage-wall intervals or dependency
    # edges. Never turn this lower-bound proxy into an end-to-end timeout pass.
    server_timeout = (
        "insufficient_receipts" if not critical_paths
        else "fail" if max(critical_paths) > timeout_seconds * 1000
        else "inconclusive_receipt_proxy"
    )
    ui_timeout = (
        "not_configured" if ui_poll_timeout_seconds is None
        else "insufficient_receipts" if not critical_paths
        else "fail" if max(critical_paths) > ui_poll_timeout_seconds * 1000
        else "inconclusive_receipt_proxy"
    )
    return {
        "schema": "provider-usage-receipt-benchmark/1",
        "privacy": {"receipt_fields_allowlisted": True, "raw_payload_fields_accepted": False},
        "receipt_deduplication": {"unique_receipts": len(rows), "ignored_duplicate_receipts": duplicates},
        "jobs": len(job_stages),
        "stage_latency_ms": stage_latency,
        "critical_path_ms": {
            "method": "sum_of_per_job_stage_max_receipt_latency",
            "p50": _percentile(critical_paths, .50), "p95": _percentile(critical_paths, .95),
            "p99": _percentile(critical_paths, .99), "samples": len(critical_paths),
        },
        "retry_amplification": {
            "request_count": request_count,
            "logical_receipts": len(rows),
            "multiplier": round(request_count / len(rows), 6) if rows else None,
            "additional_requests": request_count - len(rows),
            "receipt_count_by_retry_class": dict(sorted(retry_classes.items())),
        },
        "usage": {
            "outcome_counts": dict(sorted(outcomes.items())),
            "usage_state_counts": dict(sorted(states.items())),
            "input_tokens": sum(input_values) if input_values else None,
            "output_tokens": sum(output_values) if output_values else None,
            "reported_cost_microunits": sum(reported_costs) if reported_costs else None,
            "estimated_cost_microunits": sum(estimated_costs) if estimated_costs else None,
            "cost_by_pricing_version": dict(sorted(cost_by_pricing_version.items())),
        },
        "timeout_assessment": {
            "server_deadline_seconds": timeout_seconds,
            "ui_poll_timeout_seconds": ui_poll_timeout_seconds,
            "critical_path_p99_seconds": (
                None if critical_path_p99 is None else round(critical_path_p99 / 1000, 3)
            ),
            "server_deadline": server_timeout,
            "ui_poll_timeout": ui_timeout,
        },
        "limitations": (
            "Receipt latency is provider-call duration. Critical path is a receipt-only "
            "lower-bound proxy; it does not replace end-to-end stage-wall telemetry."
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--receipts", type=Path, help="privacy-safe receipt export JSON")
    source.add_argument("--synthetic-fixture", type=Path, help="declarative offline fixture JSON")
    parser.add_argument("--timeout-seconds", type=int, default=1800)
    parser.add_argument("--ui-poll-timeout-seconds", type=int)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.synthetic_fixture:
        fixture = _read_json(args.synthetic_fixture)
        receipts = synthetic_receipts(fixture)
        timeout_policy = fixture.get("timeout_policy", {}) if isinstance(fixture, dict) else {}
        if args.ui_poll_timeout_seconds is None:
            args.ui_poll_timeout_seconds = timeout_policy.get("ui_poll_timeout_seconds")
        if args.timeout_seconds == 1800 and isinstance(timeout_policy, dict):
            args.timeout_seconds = timeout_policy.get("server_deadline_seconds", args.timeout_seconds)
        source_kind = "synthetic_offline_fixture"
    else:
        receipts = _safe_receipts(_read_json(args.receipts))
        source_kind = "receipt_export"
    report = analyze_receipts(
        receipts,
        timeout_seconds=args.timeout_seconds,
        ui_poll_timeout_seconds=args.ui_poll_timeout_seconds,
    )
    report.update(
        {
            "created_at_utc": datetime.now(UTC).isoformat(),
            "source_kind": source_kind,
            "network_calls": 0,
            "provider_calls": 0,
        }
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(args.output), "jobs": report["jobs"], "network_calls": 0, "provider_calls": 0}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
