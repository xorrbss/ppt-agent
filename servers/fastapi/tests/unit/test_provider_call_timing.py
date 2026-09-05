"""SPEED-12: per-stage aggregation of provider-call spans and its receipt field."""

from __future__ import annotations

import pytest

from services.provider_call_timing import (
    PROVIDER_CALL_TIMING_SCHEMA,
    ProviderCallTimingLedger,
    provider_call_timing_field,
)


def _record(admission: int, executor: int, provider: int, rate: float | None = None) -> dict[str, object]:
    record: dict[str, object] = {
        "totalMillis": admission + executor + provider,
        "admissionWaitMillis": admission,
        "executorWaitMillis": executor,
        "providerMillis": provider,
    }
    if rate is not None:
        record["outputTokensPerSecond"] = rate
    return record


def test_ledger_aggregates_sum_and_percentiles_per_stage() -> None:
    ledger = ProviderCallTimingLedger()
    for admission, executor, provider, rate in (
        (100, 10, 1000, 50.0),
        (200, 20, 2000, 40.0),
        (300, 30, 3000, None),
        (400, 40, 4000, 20.0),
        (500, 50, 5000, 10.0),
    ):
        ledger.record("author", _record(admission, executor, provider, rate))
    ledger.record("vqa", _record(1, 2, 3))
    snapshot = ledger.snapshot()
    assert snapshot is not None
    assert snapshot["schemaVersion"] == PROVIDER_CALL_TIMING_SCHEMA
    author = snapshot["stages"]["author"]
    assert author["calls"] == 5
    assert author["admissionWaitMillis"] == {"sum": 1500, "p50": 300, "p90": 500}
    assert author["executorWaitMillis"] == {"sum": 150, "p50": 30, "p90": 50}
    assert author["providerMillis"] == {"sum": 15000, "p50": 3000, "p90": 5000}
    assert author["totalMillis"]["sum"] == 16650
    # Only the four calls that reported output tokens contribute a rate.
    assert author["outputTokensPerSecond"] == {"calls": 4, "p50": 20.0, "p90": 50.0}
    vqa = snapshot["stages"]["vqa"]
    assert vqa["calls"] == 1 and "outputTokensPerSecond" not in vqa
    assert vqa["providerMillis"] == {"sum": 3, "p50": 3, "p90": 3}


def test_empty_ledger_has_no_snapshot_so_the_receipt_key_stays_absent() -> None:
    assert ProviderCallTimingLedger().snapshot() is None


def test_ledger_rejects_a_record_missing_a_span_or_with_a_negative_span() -> None:
    ledger = ProviderCallTimingLedger()
    with pytest.raises(ValueError, match="provider_call_timing_record_invalid"):
        ledger.record("author", {"totalMillis": 1})
    with pytest.raises(ValueError, match="provider_call_timing_record_invalid"):
        ledger.record("author", _record(-1, 0, 0))


def test_field_validator_round_trips_a_snapshot_and_rejects_bad_shapes() -> None:
    ledger = ProviderCallTimingLedger()
    ledger.record("author", _record(1, 2, 3, 4.5))
    snapshot = ledger.snapshot()
    assert provider_call_timing_field(snapshot) == snapshot
    assert provider_call_timing_field(None) is None
    for bad in (
        {"schemaVersion": "other/v1", "stages": snapshot["stages"]},
        {"schemaVersion": PROVIDER_CALL_TIMING_SCHEMA, "stages": {}},
        {"schemaVersion": PROVIDER_CALL_TIMING_SCHEMA, "stages": {"author": {"calls": 0}}},
        {
            "schemaVersion": PROVIDER_CALL_TIMING_SCHEMA,
            "stages": {"author": {**snapshot["stages"]["author"], "providerMillis": {"sum": -1, "p50": 0, "p90": 0}}},
        },
        {
            "schemaVersion": PROVIDER_CALL_TIMING_SCHEMA,
            "stages": {"author": {**snapshot["stages"]["author"], "unknown": 1}},
        },
    ):
        with pytest.raises(ValueError, match="generation_attempt_v2_provider_call_timing_invalid"):
            provider_call_timing_field(bad)
