from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest


MODULE_PATH = Path(__file__).resolve().parents[2] / "scripts" / "provider_usage_benchmark.py"
SPEC = importlib.util.spec_from_file_location("provider_usage_benchmark", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
benchmark = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(benchmark)


def receipt(**changes):
    value = {
        "job_id": "2f6272ea-52fe-5336-94e6-0d37b6c0c1a6", "operation": "authored_author_slide", "stage": "author",
        "provider": "offline-local", "model": "fixture-author-v1", "receipt_key_hash": "a" * 64,
        "retry_class": "initial", "outcome": "succeeded", "latency_ms": 100, "usage_state": "estimated",
        "input_tokens": 2, "output_tokens": 3, "request_count": 1,
        "reported_cost_microunits": None, "estimated_cost_microunits": 4, "pricing_version": "fixture-v1",
    }
    value.update(changes)
    return value


def test_duplicate_receipts_are_not_counted_twice():
    safe = benchmark.validate_receipt(receipt())
    report = benchmark.analyze_receipts([safe, safe], timeout_seconds=1_800, ui_poll_timeout_seconds=1_800)
    assert report["receipt_deduplication"] == {"unique_receipts": 1, "ignored_duplicate_receipts": 1}
    assert report["usage"]["estimated_cost_microunits"] == 4
    assert report["retry_amplification"]["multiplier"] == 1.0


def test_unavailable_usage_stays_null_not_zero():
    safe = benchmark.validate_receipt(receipt(
        usage_state="unavailable", input_tokens=None, output_tokens=None,
        estimated_cost_microunits=None, pricing_version=None,
    ))
    report = benchmark.analyze_receipts([safe], timeout_seconds=1_800)
    assert report["usage"]["input_tokens"] is None
    assert report["usage"]["estimated_cost_microunits"] is None
    with pytest.raises(ValueError, match="null token/cost"):
        benchmark.validate_receipt(receipt(usage_state="unavailable", input_tokens=0))


@pytest.mark.parametrize("field", ["prompt", "source_url", "request_id", "exception_body", "api_key"])
def test_payload_and_secret_fields_are_rejected(field):
    with pytest.raises(ValueError, match="privacy-forbidden"):
        benchmark.validate_receipt(receipt(**{field: "not allowed"}))


def test_synthetic_90x3_is_offline_and_has_retry_amplification():
    fixture = {
        "schema_version": 1, "fixture_kind": "synthetic", "slide_count": 90, "runs": 3,
        "timeout_policy": {"ui_poll_timeout_seconds": 1_800, "server_deadline_seconds": 1_800},
        "author": {"operation": "authored_author_slide", "stage": "author", "provider": "offline-local",
                   "model": "fixture-author-v1", "latency_ms": 75_000, "input_tokens": 2400,
                   "output_tokens": 5000, "estimated_cost_microunits": 15_000, "pricing_version": "fixture-v1",
                   "retry_slide": 45},
    }
    report = benchmark.analyze_receipts(
        benchmark.synthetic_receipts(fixture),
        timeout_seconds=1_800,
        ui_poll_timeout_seconds=1_800,
    )
    assert report["jobs"] == 3
    assert report["receipt_deduplication"]["unique_receipts"] == 270
    assert report["retry_amplification"]["request_count"] == 273
    assert report["stage_latency_ms"]["author"]["p99"] == 75_000
    assert report["timeout_assessment"]["server_deadline"] == "inconclusive_receipt_proxy"
