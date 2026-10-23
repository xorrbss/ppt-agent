"""Producer of v1 generation-attempt timing receipts, for tests.

Production writes only v2 attempt receipts; the v1 loader still reads v1
records already on disk, and these tests need a writer to exercise it.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import replace

from services.authored_generation_timing_receipt import (
    GENERATION_ATTEMPT_OUTCOMES,
    GENERATION_ATTEMPT_TIMING_STAGES,
    GenerationAttemptTimingReceipt,
    QualityReceiptV2,
    _attempt_causes,
    _attempt_receipt_digest,
    _bounded_timing_ms,
    _checkpoint_signals,
    _load_attempt_records,
    _manifest_lock,
    _partial_stage_timings,
    _persist_attempt_records,
    _provider_usage,
    _quality_receipt_digest,
    generation_attempt_timing_receipt_path,
    public_attempt_id,
)


def create_generation_attempt_timing_receipt(
    *,
    job_id: object,
    attempt_id: object,
    attempt_number: int,
    revision: int,
    outcome: str,
    stage_timings_ms: Mapping[str, object],
    total_ms: object,
    failure_stage: str | None = None,
    quality_receipt: QualityReceiptV2 | None = None,
    provider_usage: Mapping[str, object] | None = None,
    checkpoint_signals: Sequence[Mapping[str, object]] = (),
    causes: Mapping[str, object] | None = None,
) -> GenerationAttemptTimingReceipt:
    """Atomically append one immutable success or failure attempt receipt."""

    if (
        isinstance(attempt_number, bool)
        or not isinstance(attempt_number, int)
        or attempt_number < 0
        or isinstance(revision, bool)
        or not isinstance(revision, int)
        or revision < 0
        or outcome not in GENERATION_ATTEMPT_OUTCOMES
        or (
            failure_stage is not None
            and failure_stage not in GENERATION_ATTEMPT_TIMING_STAGES
        )
        or (outcome == "succeeded" and failure_stage is not None)
    ):
        raise ValueError("generation_attempt_timing_receipt_invalid")
    opaque_attempt_id = public_attempt_id(attempt_id)
    quality_digest = None
    if quality_receipt is not None:
        if quality_receipt.receipt_digest != _quality_receipt_digest(quality_receipt):
            raise ValueError("generation_timing_quality_receipt_digest_mismatch")
        if quality_receipt.attempt_id != opaque_attempt_id:
            raise ValueError("generation_attempt_quality_receipt_attempt_mismatch")
        quality_digest = quality_receipt.receipt_digest
    receipt = GenerationAttemptTimingReceipt(
        schema_version="generation-attempt-timing-receipt/v1",
        attempt_id=opaque_attempt_id,
        attempt_number=attempt_number,
        revision=revision,
        outcome=outcome,
        failure_stage=failure_stage,
        stage_timings_ms=_partial_stage_timings(stage_timings_ms),
        total_ms=_bounded_timing_ms(total_ms, "attempt_total"),
        quality_receipt_digest=quality_digest,
        provider_usage=_provider_usage(provider_usage),
        checkpoint_signals=_checkpoint_signals(checkpoint_signals),
        causes=_attempt_causes(causes),
    )
    receipt = replace(receipt, receipt_digest=_attempt_receipt_digest(receipt))
    path = generation_attempt_timing_receipt_path(job_id)
    with _manifest_lock(path):
        records = _load_attempt_records(path)
        key = f"{receipt.attempt_id}#{receipt.attempt_number}#{receipt.revision}"
        existing = records.get(key)
        if existing is not None:
            if existing != receipt:
                raise ValueError("generation_attempt_timing_receipt_binding_changed")
            return existing
        records[key] = receipt
        _persist_attempt_records(path, records)
    return receipt
