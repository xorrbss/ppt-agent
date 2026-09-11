"""Durable content ordinals and physical overload-retry reservations."""

from __future__ import annotations

from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any, Mapping
import uuid

from services.authored_checkpoint_errors import AuthoredStageError
from services.authored_checkpoint_fence import (
    checkpoint_mutation_session as _checkpoint_mutation_session,
    load_checkpoint as _checkpoint,
    owned_job as _owned_job,
)
from utils.datetime_utils import get_current_utc_datetime


@dataclass(frozen=True, slots=True)
class ProviderAttemptReservation:
    """Authority needed to reserve one overload replay of a physical call."""

    job_id: uuid.UUID
    slide_index: int
    attempt_token: str
    ordinal: int
    fencing_token: int | None


_PENDING: ContextVar[tuple[ProviderAttemptReservation, ...]] = ContextVar(
    "authored_pending_provider_attempt_reservations", default=()
)


def _publish(reservation: ProviderAttemptReservation) -> None:
    pending = _PENDING.get()
    group = (
        reservation.job_id,
        reservation.attempt_token,
        reservation.ordinal,
        reservation.fencing_token,
    )
    if pending:
        first = pending[0]
        current_group = (
            first.job_id,
            first.attempt_token,
            first.ordinal,
            first.fencing_token,
        )
        if current_group != group:
            pending = ()
    if all(existing.slide_index != reservation.slide_index for existing in pending):
        _PENDING.set((*pending, reservation))


def take_provider_attempt_reservations() -> tuple[ProviderAttemptReservation, ...]:
    pending = _PENDING.get()
    _PENDING.set(())
    return pending


def current_provider_attempt_reservations() -> tuple[ProviderAttemptReservation, ...]:
    return _PENDING.get()


def provider_attempt_epoch(metadata: Mapping[str, Any] | None) -> int:
    if not isinstance(metadata, Mapping):
        return 0
    raw = metadata.get("provider_attempt_epoch")
    if isinstance(raw, int) and not isinstance(raw, bool) and raw >= 0:
        return raw
    return 0


def provider_attempt_count_in_epoch(
    metadata: Mapping[str, Any] | None, epoch: int | None
) -> int:
    """Content ordinals spent in an operation attempt, capped at three."""

    if not isinstance(metadata, Mapping):
        return 0
    raw = metadata.get("provider_attempt_count", 0)
    current = raw if isinstance(raw, int) and not isinstance(raw, bool) else 0
    current = min(3, max(0, current))
    if epoch is None:
        return current
    return current if provider_attempt_epoch(metadata) >= int(epoch) else 0


def _positive_mapping(value: object) -> dict[str, int]:
    if not isinstance(value, Mapping):
        return {}
    return {
        str(key): raw
        for key, raw in value.items()
        if isinstance(raw, int) and not isinstance(raw, bool) and raw > 0
    }


async def reserve_provider_attempt(
    *,
    job_id: uuid.UUID,
    slide_index: int,
    attempt_token: str,
    ordinal: int,
    fencing_token: int | None = None,
    session=None,
):
    """Reserve one of three content ordinals and its first physical request."""

    if isinstance(ordinal, bool) or ordinal not in {1, 2, 3}:
        raise ValueError("authored_provider_attempt_ordinal_invalid")
    reservation = ProviderAttemptReservation(
        job_id, slide_index, attempt_token, ordinal, fencing_token
    )
    async with _checkpoint_mutation_session(session) as (mutation_session, owns_session):
        job = await _owned_job(
            mutation_session, job_id, attempt_token, fencing_token=fencing_token
        )
        checkpoint = await _checkpoint(mutation_session, job_id, slide_index)
        metadata = dict(checkpoint.error) if isinstance(checkpoint.error, Mapping) else {}
        raw_current = metadata.get("provider_attempt_count", 0)
        current = (
            raw_current
            if isinstance(raw_current, int) and not isinstance(raw_current, bool)
            else 0
        )
        epoch = None if fencing_token is None else int(fencing_token)
        physical_by_ordinal = _positive_mapping(
            metadata.get("provider_physical_attempts")
        )
        if epoch is not None and provider_attempt_epoch(metadata) < epoch:
            raw_total = metadata.get("provider_attempt_total", 0)
            total = raw_total if isinstance(raw_total, int) and not isinstance(raw_total, bool) else 0
            metadata["provider_attempt_total"] = max(0, total) + max(0, current)
            raw_physical_total = metadata.get("provider_physical_attempt_total", 0)
            physical_total = (
                raw_physical_total
                if isinstance(raw_physical_total, int)
                and not isinstance(raw_physical_total, bool)
                else 0
            )
            metadata["provider_physical_attempt_total"] = max(
                0, physical_total
            ) + sum(physical_by_ordinal.values())
            physical_by_ordinal = {}
            metadata["provider_attempt_epoch"] = epoch
            current = 0
        if current not in {0, 1, 2, 3}:
            await mutation_session.rollback()
            raise AuthoredStageError(
                slide_index=slide_index,
                stage="author",
                code="authored_provider_attempt_checkpoint_invalid",
                retryable=False,
            )
        if ordinal < current or ordinal > current + 1:
            await mutation_session.rollback()
            raise AuthoredStageError(
                slide_index=slide_index,
                stage="author",
                code="authored_provider_attempt_checkpoint_conflict",
                retryable=False,
                provider_attempt_count=current,
            )
        if ordinal == current:
            _publish(reservation)
            return checkpoint
        metadata["provider_attempt_count"] = ordinal
        metadata["provider_attempt_state"] = "reserved"
        physical_by_ordinal[str(ordinal)] = max(
            1, physical_by_ordinal.get(str(ordinal), 0)
        )
        metadata["provider_physical_attempts"] = physical_by_ordinal
        if epoch is not None:
            metadata["provider_attempt_epoch"] = epoch
        checkpoint.error = metadata
        checkpoint.attempt_token = attempt_token
        checkpoint.updated_at = get_current_utc_datetime()
        job.current_stage = "author"
        job.updated_at = checkpoint.updated_at
        mutation_session.add_all((checkpoint, job))
        if owns_session:
            await mutation_session.commit()
        _publish(reservation)
        return checkpoint


async def reserve_provider_overload_retry(
    reservations: tuple[ProviderAttemptReservation, ...],
    *,
    physical_ordinal: int,
) -> None:
    """Reserve physical replay two without advancing the content ordinal."""

    if physical_ordinal != 2:
        raise ValueError("authored_provider_physical_attempt_ordinal_invalid")
    for reservation in reservations:
        async with _checkpoint_mutation_session(None) as (mutation_session, owns_session):
            job = await _owned_job(
                mutation_session,
                reservation.job_id,
                reservation.attempt_token,
                fencing_token=reservation.fencing_token,
            )
            checkpoint = await _checkpoint(
                mutation_session, reservation.job_id, reservation.slide_index
            )
            metadata = dict(checkpoint.error) if isinstance(checkpoint.error, Mapping) else {}
            epoch_matches = (
                reservation.fencing_token is None
                or provider_attempt_epoch(metadata) == reservation.fencing_token
            )
            current_content = provider_attempt_count_in_epoch(
                metadata, reservation.fencing_token
            )
            if not epoch_matches or current_content != reservation.ordinal:
                await mutation_session.rollback()
                raise AuthoredStageError(
                    slide_index=reservation.slide_index,
                    stage="author",
                    code="authored_provider_attempt_checkpoint_conflict",
                    retryable=False,
                    provider_attempt_count=current_content,
                )
            physical_by_ordinal = _positive_mapping(
                metadata.get("provider_physical_attempts")
            )
            current = physical_by_ordinal.get(str(reservation.ordinal), 1)
            if current not in {1, physical_ordinal}:
                await mutation_session.rollback()
                raise AuthoredStageError(
                    slide_index=reservation.slide_index,
                    stage="author",
                    code="authored_provider_physical_attempt_checkpoint_conflict",
                    retryable=False,
                    provider_attempt_count=reservation.ordinal,
                )
            if current == physical_ordinal:
                continue
            physical_by_ordinal[str(reservation.ordinal)] = physical_ordinal
            metadata["provider_physical_attempts"] = physical_by_ordinal
            metadata["provider_attempt_state"] = "overload_retry_reserved"
            checkpoint.error = metadata
            checkpoint.attempt_token = reservation.attempt_token
            checkpoint.updated_at = get_current_utc_datetime()
            job.current_stage = "author"
            job.updated_at = checkpoint.updated_at
            mutation_session.add_all((checkpoint, job))
            if owns_session:
                await mutation_session.commit()


__all__ = [
    "ProviderAttemptReservation",
    "current_provider_attempt_reservations",
    "provider_attempt_count_in_epoch",
    "provider_attempt_epoch",
    "reserve_provider_attempt",
    "reserve_provider_overload_retry",
    "take_provider_attempt_reservations",
]
