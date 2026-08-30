"""Durable digest checkpoints for deck-wide authored planning.

The checkpoint contains only canonical plan output and content digests. It
never stores prompts, provider responses, credentials, cookies, or lease
tokens. A completed row is reusable by a later operation attempt only when its
input and stored output digests both verify.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from hashlib import sha256
import json
from typing import TypeVar
import uuid

from sqlalchemy import update
from sqlalchemy.exc import IntegrityError
from sqlmodel import select

from models.sql.presentation_generation_job import PresentationGenerationJob
from models.sql.public_admission import AuthoredDeckPlanCheckpoint
from models.sql.workspace import WorkspaceOperation
from utils.datetime_utils import get_current_utc_datetime


_T = TypeVar("_T")
_STAGES = frozenset({"deck_plan", "chapter_plan", "header", "illustration"})


class AuthoredDeckPlanCheckpointError(RuntimeError):
    """Fail-closed checkpoint conflict or corruption."""

    retryable = False

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def canonical_checkpoint_digest(value: object) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return sha256(encoded).hexdigest()


def _validate_stage(stage: str) -> str:
    if stage not in _STAGES:
        raise ValueError("authored_deck_checkpoint_stage_invalid")
    return stage


def _validate_payload(payload: object) -> dict:
    if not isinstance(payload, dict):
        raise AuthoredDeckPlanCheckpointError(
            "authored_deck_checkpoint_payload_invalid"
        )
    # Round-trip now so no non-JSON object reaches a durable JSON column.
    return json.loads(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    )


async def load_authored_deck_plan_checkpoint(
    *, generation_job_id: uuid.UUID, stage: str, input_digest: str
) -> dict | None:
    """Load one verified completed checkpoint from the durable database."""

    _validate_stage(stage)
    from services.database import async_session_maker

    async with async_session_maker() as session:
        row = await session.scalar(
            select(AuthoredDeckPlanCheckpoint).where(
                AuthoredDeckPlanCheckpoint.generation_job_id == generation_job_id,
                AuthoredDeckPlanCheckpoint.stage == stage,
            )
        )
        if row is None:
            return None
        if row.input_digest != input_digest:
            raise AuthoredDeckPlanCheckpointError(
                "authored_deck_checkpoint_input_conflict"
            )
        payload = _validate_payload(row.payload)
        if canonical_checkpoint_digest(payload) != row.output_digest:
            raise AuthoredDeckPlanCheckpointError(
                "authored_deck_checkpoint_output_corrupt"
            )
        return payload


async def commit_authored_deck_plan_checkpoint(
    *,
    generation_job_id: uuid.UUID,
    attempt_token: str,
    attempt_number: int,
    stage: str,
    input_digest: str,
    payload: Mapping[str, object],
) -> dict:
    """Commit one output only while the producing generation lease is current."""

    _validate_stage(stage)
    if not attempt_token:
        raise ValueError("authored_deck_checkpoint_attempt_token_required")
    if attempt_number < 1:
        raise ValueError("authored_deck_checkpoint_attempt_number_invalid")
    normalized = _validate_payload(dict(payload))
    output_digest = canonical_checkpoint_digest(normalized)

    from services.database import async_session_maker

    async with async_session_maker() as session:
        # This conditional no-op UPDATE is the fencing write. It is atomic on
        # SQLite and PostgreSQL and does not rely on FOR UPDATE semantics.
        fenced = await session.execute(
            update(PresentationGenerationJob)
            .where(
                PresentationGenerationJob.id == generation_job_id,
                PresentationGenerationJob.state == "running",
                PresentationGenerationJob.attempt_token == attempt_token,
                PresentationGenerationJob.attempt_number == attempt_number,
            )
            .values(updated_at=get_current_utc_datetime())
        )
        if fenced.rowcount != 1:
            await session.rollback()
            raise AuthoredDeckPlanCheckpointError(
                "authored_deck_checkpoint_stale_attempt"
            )
        workspace_operation_id = await session.scalar(
            select(PresentationGenerationJob.workspace_operation_id).where(
                PresentationGenerationJob.id == generation_job_id
            )
        )
        if workspace_operation_id is not None:
            operation_fence = await session.execute(
                update(WorkspaceOperation)
                .where(
                    WorkspaceOperation.id == workspace_operation_id,
                    WorkspaceOperation.state == "running",
                    WorkspaceOperation.attempt_token == attempt_token,
                    WorkspaceOperation.attempt_number == attempt_number,
                )
                .values(version=WorkspaceOperation.version)
            )
            if operation_fence.rowcount != 1:
                await session.rollback()
                raise AuthoredDeckPlanCheckpointError(
                    "authored_deck_checkpoint_workspace_fence_lost"
                )
        existing = await session.scalar(
            select(AuthoredDeckPlanCheckpoint).where(
                AuthoredDeckPlanCheckpoint.generation_job_id == generation_job_id,
                AuthoredDeckPlanCheckpoint.stage == stage,
            )
        )
        if existing is not None:
            if (
                existing.input_digest != input_digest
                or existing.output_digest != output_digest
                or _validate_payload(existing.payload) != normalized
            ):
                await session.rollback()
                raise AuthoredDeckPlanCheckpointError(
                    "authored_deck_checkpoint_commit_conflict"
                )
            await session.commit()
            return normalized
        session.add(
            AuthoredDeckPlanCheckpoint(
                generation_job_id=generation_job_id,
                stage=stage,
                input_digest=input_digest,
                output_digest=output_digest,
                payload=normalized,
                produced_attempt_number=attempt_number,
            )
        )
        try:
            await session.commit()
        except IntegrityError:
            await session.rollback()
            replay = await load_authored_deck_plan_checkpoint(
                generation_job_id=generation_job_id,
                stage=stage,
                input_digest=input_digest,
            )
            if replay != normalized:
                raise AuthoredDeckPlanCheckpointError(
                    "authored_deck_checkpoint_commit_conflict"
                )
        return normalized


async def resolve_with_authored_deck_plan_checkpoint(
    *,
    generation_job: PresentationGenerationJob | None,
    stage: str,
    input_contract: Mapping[str, object],
    resolve: Callable[[], Awaitable[_T]],
    encode: Callable[[_T], Mapping[str, object]],
    decode: Callable[[Mapping[str, object]], _T],
) -> tuple[_T, bool]:
    """Return `(value, reused)` around one deck-wide planning call."""

    _validate_stage(stage)
    if generation_job is None:
        return await resolve(), False
    input_digest = canonical_checkpoint_digest(dict(input_contract))
    existing = await load_authored_deck_plan_checkpoint(
        generation_job_id=generation_job.id,
        stage=stage,
        input_digest=input_digest,
    )
    if existing is not None:
        return decode(existing), True
    value = await resolve()
    payload = await commit_authored_deck_plan_checkpoint(
        generation_job_id=generation_job.id,
        attempt_token=generation_job.attempt_token or "",
        attempt_number=generation_job.attempt_number,
        stage=stage,
        input_digest=input_digest,
        payload=encode(value),
    )
    return decode(payload), False


__all__ = [
    "AuthoredDeckPlanCheckpointError",
    "canonical_checkpoint_digest",
    "commit_authored_deck_plan_checkpoint",
    "load_authored_deck_plan_checkpoint",
    "resolve_with_authored_deck_plan_checkpoint",
]
