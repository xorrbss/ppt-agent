"""Transaction-owned deletion for presentations and their local resources."""

from __future__ import annotations

import uuid

from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.ext.asyncio import AsyncSession

from models.sql.presentation import PresentationModel
from models.sql.template_v2 import TemplateV2
from models.sql.template_v2_local_state import TemplateV2LocalState


class PresentationDeleteConflictError(RuntimeError):
    """Raised when ownership is inconsistent or a concurrent child survives."""

    def __init__(self, presentation_id: uuid.UUID):
        super().__init__(str(presentation_id))
        self.presentation_id = presentation_id


class PresentationDeleteRetryableError(RuntimeError):
    """Raised when SQLite cannot acquire the delete transaction lock."""

    def __init__(self, presentation_id: uuid.UUID):
        super().__init__(str(presentation_id))
        self.presentation_id = presentation_id


def is_retryable_sqlite_lock(
    error: OperationalError,
    sql_session: AsyncSession,
) -> bool:
    """Return whether an OperationalError is SQLite BUSY/LOCKED.

    SQLite's driver already performs a bounded busy wait. Retrying the entire
    ownership transaction here would multiply that delay and could obscure a
    concurrent ownership change because SELECT FOR UPDATE is a no-op on
    SQLite. The API instead exposes a retryable 503 after rollback.
    """

    bind = sql_session.get_bind()
    if bind.dialect.name != "sqlite":
        return False

    original = error.orig
    sqlite_error_code = getattr(original, "sqlite_errorcode", None)
    if isinstance(sqlite_error_code, int):
        # Extended SQLite result codes retain the primary code in the low byte.
        if sqlite_error_code & 0xFF in {5, 6}:  # SQLITE_BUSY, SQLITE_LOCKED
            return True

    message = str(original).lower()
    return "database is locked" in message or "database table is locked" in message


class PresentationDeletionService:
    """Delete presentation-owned resources explicitly in one transaction.

    The local-state sidecar is the authoritative ownership graph. During the
    transitional period, the duplicate ``template_v2.presentation_id`` value
    must agree exactly with that graph. A mismatch fails closed so corruption
    cannot be hidden by a broad cascade.
    """

    def __init__(self, sql_session: AsyncSession):
        self._sql_session = sql_session

    async def delete(self, presentation: PresentationModel) -> None:
        presentation_id = presentation.id
        try:
            canonical_ids = set(
                await self._sql_session.scalars(
                    select(TemplateV2.id)
                    .where(TemplateV2.presentation_id == presentation_id)
                    .with_for_update()
                )
            )
            sidecar_ids = set(
                await self._sql_session.scalars(
                    select(TemplateV2LocalState.template_id)
                    .where(TemplateV2LocalState.presentation_id == presentation_id)
                    .with_for_update()
                )
            )
            if canonical_ids != sidecar_ids:
                await self._sql_session.rollback()
                raise PresentationDeleteConflictError(presentation_id)

            if canonical_ids:
                await self._sql_session.execute(
                    delete(TemplateV2).where(TemplateV2.id.in_(canonical_ids))
                )
                await self._sql_session.flush()
            await self._sql_session.delete(presentation)
            await self._sql_session.commit()
        except IntegrityError as error:
            await self._sql_session.rollback()
            raise PresentationDeleteConflictError(presentation_id) from error
        except OperationalError as error:
            await self._sql_session.rollback()
            if is_retryable_sqlite_lock(error, self._sql_session):
                raise PresentationDeleteRetryableError(presentation_id) from error
            raise
