from __future__ import annotations

import asyncio
from dataclasses import asdict, replace
from pathlib import Path
import uuid

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from models.sql.presentation_generation_job import PresentationGenerationJob
from models.sql.provider_usage_receipt import ProviderUsageReceipt
from services.provider_usage_receipt_service import (
    ProviderUsageReceiptInput,
    aggregate_provider_usage_for_job,
    record_provider_usage_receipt,
    validate_provider_usage_receipt,
)


def _receipt(job_id: uuid.UUID, **changes) -> ProviderUsageReceiptInput:
    values = {
        "job_id": job_id,
        "operation": "authored_generation",
        "stage": "author",
        "provider": "openai",
        "model": "gpt-5.2-mini",
        "receipt_key_hash": "a" * 64,
        "retry_class": "initial",
        "outcome": "succeeded",
        "latency_ms": 321,
        "usage_state": "estimated",
        "input_tokens": 123,
        "output_tokens": 45,
        "estimated_cost_microunits": 678,
        "pricing_version": "2026-08-13",
    }
    values.update(changes)
    return ProviderUsageReceiptInput(**values)


async def _database(path: Path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{path}")
    # Loading the job model resolves the FK target. SQLite keeps FK checks
    # disabled for this isolated receipt-only test database.
    assert PresentationGenerationJob.__tablename__ == "presentation_generation_jobs"
    async with engine.begin() as connection:
        await connection.run_sync(
            lambda sync_connection: ProviderUsageReceipt.__table__.create(
                sync_connection, checkfirst=True
            )
        )
    return engine, async_sessionmaker(engine, expire_on_commit=False)


def test_receipt_input_is_scalar_allowlist_and_rejects_private_content() -> None:
    receipt = _receipt(uuid.uuid4())
    validate_provider_usage_receipt(receipt)

    for field, private_value in (
        ("operation", "prompt=customer private input"),
        ("stage", "https://customer.example/source"),
        ("provider", "Authorization: Bearer secret"),
        ("model", "api_key=never-store"),
        ("receipt_key_hash", "request-id-123"),
        ("pricing_version", "exception body: private"),
    ):
        with pytest.raises(ValueError):
            validate_provider_usage_receipt(replace(receipt, **{field: private_value}))

    with pytest.raises(TypeError):
        ProviderUsageReceiptInput(  # type: ignore[call-arg]
            **asdict(receipt), prompt="must not have an entry point"
        )


def test_unavailable_usage_is_null_not_zero_and_source_is_exclusive() -> None:
    receipt = _receipt(
        uuid.uuid4(),
        usage_state="unavailable",
        input_tokens=None,
        output_tokens=None,
        estimated_cost_microunits=None,
        pricing_version=None,
    )
    validate_provider_usage_receipt(receipt)

    with pytest.raises(ValueError, match="cannot be represented as zero"):
        validate_provider_usage_receipt(replace(receipt, input_tokens=0))
    with pytest.raises(ValueError, match="cannot contain reported cost"):
        validate_provider_usage_receipt(
            replace(_receipt(uuid.uuid4()), reported_cost_microunits=1)
        )
    with pytest.raises(ValueError, match="cannot contain estimated cost"):
        validate_provider_usage_receipt(
            replace(
                _receipt(uuid.uuid4(), usage_state="reported", estimated_cost_microunits=None),
                estimated_cost_microunits=1,
            )
        )


def test_receipts_dedupe_retries_and_keep_cancel_resume_aggregate_exact(tmp_path: Path) -> None:
    async def scenario() -> None:
        engine, sessions = await _database(tmp_path / "provider-usage.db")
        job_id = uuid.uuid4()
        try:
            initial = _receipt(job_id)
            resumed = replace(
                _receipt(job_id, receipt_key_hash="b" * 64),
                retry_class="resume",
                outcome="cancelled",
                stage="render",
                provider="local",
                model="none",
                usage_state="unavailable",
                input_tokens=None,
                output_tokens=None,
                estimated_cost_microunits=None,
                pricing_version=None,
            )
            async with sessions() as session:
                first = await record_provider_usage_receipt(session, initial)
                replay = await record_provider_usage_receipt(session, initial)
                assert first.id == replay.id
                await record_provider_usage_receipt(session, resumed)
                await session.commit()

            async with sessions() as session:
                aggregate = await aggregate_provider_usage_for_job(session, job_id)
                assert aggregate == {
                    "receipt_count": 2,
                    "request_count": 2,
                    "provider_receipt_count": 1,
                    "provider_request_count": 1,
                    "provider_retry_receipt_count": 0,
                    "retry_receipt_count": 1,
                    "resume_receipt_count": 1,
                    "recovery_receipt_count": 0,
                    "cancelled_receipt_count": 1,
                    "failed_receipt_count": 0,
                    "unavailable_receipt_count": 1,
                    "latency_ms": 642,
                    "input_tokens": 123,
                    "output_tokens": 45,
                    "reported_cost_microunits": None,
                    "estimated_cost_microunits": 678,
                    "cost_by_pricing_version": {
                        "2026-08-13": {
                            "reported_cost_microunits": 0,
                            "estimated_cost_microunits": 678,
                        }
                    },
                }
                with pytest.raises(ValueError, match="conflicts"):
                    await record_provider_usage_receipt(
                        session, replace(initial, latency_ms=322)
                    )
        finally:
            await engine.dispose()

    asyncio.run(scenario())
