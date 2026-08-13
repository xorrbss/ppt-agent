"""Scalar-only receipt persistence for provider usage and stage timing.

Do not add arbitrary metadata to this API.  Request content, URLs, headers,
provider request IDs, exceptions, and credentials are intentionally not part of
the input type or persistence model.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
import re
import uuid

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.exc import IntegrityError
from sqlmodel import select

from models.sql.provider_usage_receipt import ProviderUsageReceipt


_HASH_RE = re.compile(r"^[a-f0-9]{64}$")
_IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}$")
_SENSITIVE_VALUE_RE = re.compile(
    r"(?:\bsk-[A-Za-z0-9_-]{8,}|\bbearer\s+[A-Za-z0-9._~-]{8,}|"
    r"\b(?:api[_-]?key|secret|password)\s*[:=]\s*\S+)", re.IGNORECASE
)
_OPERATION_RE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_RETRY_CLASSES = frozenset(("initial", "retry", "resume", "recovery"))
_USAGE_STATES = frozenset(("reported", "estimated", "unavailable"))
_OUTCOMES = frozenset(("succeeded", "failed", "cancelled"))


@dataclass(frozen=True, slots=True)
class ProviderUsageReceiptInput:
    """The complete public allowlist for persisted receipt data."""

    job_id: uuid.UUID
    operation: str
    stage: str
    provider: str
    model: str
    receipt_key_hash: str
    retry_class: str
    outcome: str
    latency_ms: int
    usage_state: str
    input_tokens: int | None = None
    output_tokens: int | None = None
    request_count: int = 1
    reported_cost_microunits: int | None = None
    estimated_cost_microunits: int | None = None
    pricing_version: str | None = None


def _validate_identifier(
    value: str, name: str, *, operation: bool = False, max_length: int = 128
) -> None:
    pattern = _OPERATION_RE if operation else _IDENTIFIER_RE
    if (
        not isinstance(value, str)
        or len(value) > max_length
        or not pattern.fullmatch(value)
        or _SENSITIVE_VALUE_RE.search(value)
    ):
        raise ValueError(f"{name} must be a bounded safe identifier")


def _validate_nonnegative(value: int | None, name: str, *, positive: bool = False) -> None:
    if value is None:
        return
    if isinstance(value, bool) or not isinstance(value, int) or value < (1 if positive else 0):
        qualifier = "positive" if positive else "non-negative"
        raise ValueError(f"{name} must be a {qualifier} integer")


def validate_provider_usage_receipt(receipt: ProviderUsageReceiptInput) -> None:
    """Reject secret-bearing/free-text values before they can reach storage."""

    _validate_identifier(receipt.operation, "operation", operation=True, max_length=64)
    _validate_identifier(receipt.stage, "stage", operation=True, max_length=64)
    _validate_identifier(receipt.provider, "provider", max_length=64)
    _validate_identifier(receipt.model, "model", max_length=128)
    if receipt.pricing_version is not None:
        _validate_identifier(receipt.pricing_version, "pricing_version", max_length=64)
    if not isinstance(receipt.receipt_key_hash, str) or not _HASH_RE.fullmatch(
        receipt.receipt_key_hash
    ):
        raise ValueError("receipt_key_hash must be a SHA-256 hex digest")
    if receipt.retry_class not in _RETRY_CLASSES:
        raise ValueError("retry_class is not allowlisted")
    if receipt.outcome not in _OUTCOMES:
        raise ValueError("outcome is not allowlisted")
    if receipt.usage_state not in _USAGE_STATES:
        raise ValueError("usage_state is not allowlisted")
    _validate_nonnegative(receipt.latency_ms, "latency_ms")
    _validate_nonnegative(receipt.request_count, "request_count", positive=True)
    for name in (
        "input_tokens",
        "output_tokens",
        "reported_cost_microunits",
        "estimated_cost_microunits",
    ):
        _validate_nonnegative(getattr(receipt, name), name)
    if receipt.usage_state == "unavailable" and any(
        getattr(receipt, name) is not None
        for name in (
            "input_tokens",
            "output_tokens",
            "reported_cost_microunits",
            "estimated_cost_microunits",
        )
    ):
        raise ValueError("unavailable usage cannot be represented as zero")
    if receipt.usage_state == "reported" and receipt.estimated_cost_microunits is not None:
        raise ValueError("reported receipts cannot contain estimated cost")
    if receipt.usage_state == "estimated" and receipt.reported_cost_microunits is not None:
        raise ValueError("estimated receipts cannot contain reported cost")
    if (
        receipt.reported_cost_microunits is not None
        or receipt.estimated_cost_microunits is not None
    ) and receipt.pricing_version is None:
        raise ValueError("priced receipts require pricing_version")


def _same_receipt(stored: ProviderUsageReceipt, incoming: ProviderUsageReceiptInput) -> bool:
    expected = asdict(incoming)
    return all(getattr(stored, key) == value for key, value in expected.items())


async def record_provider_usage_receipt(
    session: AsyncSession, receipt: ProviderUsageReceiptInput
) -> ProviderUsageReceipt:
    """Persist a receipt once; retry/resume replays with its key are free."""

    validate_provider_usage_receipt(receipt)
    existing = await session.scalar(
        select(ProviderUsageReceipt).where(
            ProviderUsageReceipt.receipt_key_hash == receipt.receipt_key_hash
        )
    )
    if existing is not None:
        if not _same_receipt(existing, receipt):
            raise ValueError("receipt_key_hash conflicts with a different receipt")
        return existing
    stored = ProviderUsageReceipt(**asdict(receipt))
    try:
        # The savepoint keeps the caller transaction usable when two workers race
        # on the same idempotency digest. The unique constraint is authoritative.
        async with session.begin_nested():
            session.add(stored)
            await session.flush()
    except IntegrityError:
        existing = await session.scalar(
            select(ProviderUsageReceipt).where(
                ProviderUsageReceipt.receipt_key_hash == receipt.receipt_key_hash
            )
        )
        if existing is None:
            raise
        if not _same_receipt(existing, receipt):
            raise ValueError(
                "receipt_key_hash conflicts with a different receipt"
            ) from None
        return existing
    return stored


async def record_provider_usage_receipt_detached(
    receipt: ProviderUsageReceiptInput,
) -> ProviderUsageReceipt:
    """Persist from instrumentation without sharing a pipeline transaction."""

    # Imported lazily to avoid the database module importing this service at startup.
    from services.database import async_session_maker

    async with async_session_maker() as session:
        stored = await record_provider_usage_receipt(session, receipt)
        await session.commit()
        return stored


async def aggregate_provider_usage_for_job(
    session: AsyncSession, job_id: uuid.UUID
) -> dict[str, object]:
    """Aggregate safe scalars without converting unavailable usage to zero."""

    receipts = list(
        (
            await session.execute(
                select(ProviderUsageReceipt).where(ProviderUsageReceipt.job_id == job_id)
            )
        ).scalars()
    )
    def total(field: str) -> int | None:
        values = [getattr(receipt, field) for receipt in receipts]
        known = [value for value in values if value is not None]
        return sum(known) if known else None

    pricing_versions: dict[str, dict[str, int]] = {}
    for receipt in receipts:
        if receipt.pricing_version is None or (
            receipt.reported_cost_microunits is None
            and receipt.estimated_cost_microunits is None
        ):
            continue
        bucket = pricing_versions.setdefault(
            receipt.pricing_version,
            {"reported_cost_microunits": 0, "estimated_cost_microunits": 0},
        )
        for field in bucket:
            value = getattr(receipt, field)
            if value is not None:
                bucket[field] += value

    provider_receipts = [
        receipt
        for receipt in receipts
        if not (receipt.provider == "local" and receipt.model == "none")
    ]

    return {
        "receipt_count": len(receipts),
        "request_count": sum(receipt.request_count for receipt in receipts),
        "provider_receipt_count": len(provider_receipts),
        "provider_request_count": sum(
            receipt.request_count for receipt in provider_receipts
        ),
        "provider_retry_receipt_count": sum(
            receipt.retry_class != "initial" for receipt in provider_receipts
        ),
        "retry_receipt_count": sum(
            receipt.retry_class != "initial" for receipt in receipts
        ),
        "resume_receipt_count": sum(
            receipt.retry_class == "resume" for receipt in receipts
        ),
        "recovery_receipt_count": sum(
            receipt.retry_class == "recovery" for receipt in receipts
        ),
        "cancelled_receipt_count": sum(
            receipt.outcome == "cancelled" for receipt in receipts
        ),
        "failed_receipt_count": sum(
            receipt.outcome == "failed" for receipt in receipts
        ),
        "unavailable_receipt_count": sum(
            receipt.usage_state == "unavailable" for receipt in receipts
        ),
        "latency_ms": total("latency_ms"),
        "input_tokens": total("input_tokens"),
        "output_tokens": total("output_tokens"),
        "reported_cost_microunits": total("reported_cost_microunits"),
        "estimated_cost_microunits": total("estimated_cost_microunits"),
        "cost_by_pricing_version": pricing_versions,
    }
