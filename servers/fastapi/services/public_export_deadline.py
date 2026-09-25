"""Isolated worker deadline grants derived from the pinned public admission.

Admission pins one absolute ``deadline_at`` per export operation (scaled by
slide count, ``services/public_export_deadline_policy.py``), the lease fences
on it and completion checks it.  The isolated render and export workers used
to be granted from *other* numbers -- a flat 600 s, a render batch 120 s and a
hybrid constant of 1740 s -- so a worker could legally outlive the admission
that owned it and be failed at completion with its package already built.

This module makes the admission the only source: every worker grant is what
is left of ``deadline_at`` at the moment the worker is launched, bounded above
by the role's executor budget.  The grant is re-read from the clock at each
launch (render batches run before the export worker), never from a constant.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from models.sql.public_admission import PublicOperationAdmission
from services.public_export_deadline_policy import PUBLIC_EXPORT_DEADLINE_MAX_SECONDS
from services.public_export_errors import PublicExportOperationError
from utils.datetime_utils import as_utc, get_current_utc_datetime


# A render batch converts at most ``MAX_PUBLIC_RENDER_BATCH_SLIDES`` slides to
# PNG; the trusted executor gives non-export roles a 600 s budget and the batch
# has always been bounded well below it.
PUBLIC_RENDER_BATCH_DEADLINE_SECONDS = 120

# Grants for a job built without an admission row (tests, ad-hoc callers):
# ``build_isolated_operation_job`` has no session by design, so it cannot read
# the admission itself.  Product paths always pass a derived grant instead.
_FALLBACK_EXPORT_DEADLINE_SECONDS = 600


def worker_deadline_bounds(role: str) -> tuple[int, int]:
    """Return ``(fallback, maximum)`` seconds for a worker role.

    An editable-hybrid export measures every slide in the isolated worker's
    own browser before assembling OOXML, so the export role's ceiling is the
    browser budget (the executor's export cap) rather than the raster
    converter's.
    """

    if role == "export":
        return _FALLBACK_EXPORT_DEADLINE_SECONDS, PUBLIC_EXPORT_DEADLINE_MAX_SECONDS
    return PUBLIC_RENDER_BATCH_DEADLINE_SECONDS, PUBLIC_RENDER_BATCH_DEADLINE_SECONDS


@dataclass(frozen=True)
class PublicExportDeadlineGrant:
    """What the pinned admission still allows, per worker role."""

    deadline_at: datetime

    def remaining_seconds(self, *, now: datetime | None = None) -> int:
        current = as_utc(now if now is not None else get_current_utc_datetime())
        return int((as_utc(self.deadline_at) - current).total_seconds())

    def _grant(self, role: str, *, now: datetime | None) -> int:
        remaining = self.remaining_seconds(now=now)
        if remaining < 1:
            raise PublicExportOperationError("public_export_admission_deadline_exhausted")
        return min(worker_deadline_bounds(role)[1], remaining)

    def export_worker_seconds(self, *, now: datetime | None = None) -> int:
        """Grant for the export worker, hybrid or raster alike."""

        return self._grant("export", now=now)

    def render_batch_seconds(self, *, now: datetime | None = None) -> int:
        """Grant for one network-none render batch."""

        return self._grant("render", now=now)


async def load_public_export_deadline_grant(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    operation_id: uuid.UUID,
) -> PublicExportDeadlineGrant:
    """Read the admission the operation runs under; fail closed without one."""

    async with session_factory() as session:
        admission = await session.scalar(
            select(PublicOperationAdmission).where(
                PublicOperationAdmission.operation_id == operation_id
            )
        )
    if admission is None or admission.deadline_at is None:
        raise PublicExportOperationError("public_export_admission_required")
    return PublicExportDeadlineGrant(deadline_at=as_utc(admission.deadline_at))


__all__ = [
    "PUBLIC_RENDER_BATCH_DEADLINE_SECONDS",
    "PublicExportDeadlineGrant",
    "load_public_export_deadline_grant",
    "worker_deadline_bounds",
]
