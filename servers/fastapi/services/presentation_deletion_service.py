"""Transaction-owned deletion for presentations and their local resources."""

from __future__ import annotations

import uuid

from sqlalchemy import delete, or_, select
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.ext.asyncio import AsyncSession

from models.sql.authored_exact_provenance import AuthoredExactProvenance
from models.sql.chat_history_message import ChatHistoryMessageModel
from models.sql.presentation import PresentationModel
from models.sql.presentation import PRESENTATION_LIFECYCLE_DELETED
from models.sql.presentation_generation_job import PresentationGenerationJob
from models.sql.presentation_layout_code import PresentationLayoutCodeModel
from models.sql.presentation_version import PresentationVersionModel
from models.sql.slide import SlideModel
from models.sql.template import TemplateModel
from models.sql.template_v2 import TemplateV2
from models.sql.template_v2_local_state import TemplateV2LocalState
from models.sql.workspace import (
    WorkspaceArtifact,
    WorkspaceAsset,
    WorkspaceConversation,
    WorkspaceOperation,
)
from services.workspace_cleanup_journal_service import (
    snapshot_presentation_workspace_cleanup_targets,
)
from utils.datetime_utils import get_current_utc_datetime


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

    async def _locked_template_ids(
        self,
        presentation_id: uuid.UUID,
    ) -> set[str]:
        canonical_ids = set(
            await self._sql_session.scalars(
                select(TemplateV2.id)
                .where(TemplateV2.presentation_id == presentation_id)
                .with_for_update()
                .execution_options(skip_owner_scope=True)
            )
        )
        sidecar_ids = set(
            await self._sql_session.scalars(
                select(TemplateV2LocalState.template_id)
                .where(TemplateV2LocalState.presentation_id == presentation_id)
                .with_for_update()
                .execution_options(skip_owner_scope=True)
            )
        )
        if canonical_ids != sidecar_ids:
            raise PresentationDeleteConflictError(presentation_id)
        return canonical_ids

    async def tombstone(
        self,
        presentation: PresentationModel,
        *,
        operation_id: uuid.UUID,
    ) -> int:
        """Redact a deck and journal its immutable files in one transaction.

        The caller owns commit/rollback.  The presentation, conversation and
        workspace operation graph intentionally survive so an operation
        handler can append its succeeded Event in the same transaction.
        """

        presentation_id = presentation.id
        if presentation.lifecycle_status == PRESENTATION_LIFECYCLE_DELETED:
            if presentation.deletion_operation_id == operation_id:
                return 0
            raise PresentationDeleteConflictError(presentation_id)

        template_ids = await self._locked_template_ids(presentation_id)
        cleanup_targets = await snapshot_presentation_workspace_cleanup_targets(
            self._sql_session,
            presentation=presentation,
        )

        conversation_ids = set(
            (
                await self._sql_session.scalars(
                    select(WorkspaceConversation.id)
                    .where(WorkspaceConversation.presentation_id == presentation_id)
                    .execution_options(skip_owner_scope=True)
                )
            ).all()
        )
        operation_ids = set(
            (
                await self._sql_session.scalars(
                    select(WorkspaceOperation.id)
                    .where(WorkspaceOperation.presentation_id == presentation_id)
                    .execution_options(skip_owner_scope=True)
                )
            ).all()
        )

        artifact_predicates = [WorkspaceArtifact.presentation_id == presentation_id]
        if operation_ids:
            artifact_predicates.append(
                WorkspaceArtifact.operation_id.in_(operation_ids)
            )
        await self._sql_session.execute(
            delete(WorkspaceArtifact)
            .where(or_(*artifact_predicates))
            .execution_options(skip_owner_scope=True)
        )
        if conversation_ids:
            await self._sql_session.execute(
                delete(WorkspaceAsset)
                .where(WorkspaceAsset.conversation_id.in_(conversation_ids))
                .execution_options(skip_owner_scope=True)
            )

        await self._sql_session.execute(
            delete(PresentationGenerationJob)
            .where(PresentationGenerationJob.presentation_id == presentation_id)
            .execution_options(skip_owner_scope=True)
        )
        await self._sql_session.execute(
            delete(ChatHistoryMessageModel)
            .where(ChatHistoryMessageModel.presentation_id == presentation_id)
            .execution_options(skip_owner_scope=True)
        )
        await self._sql_session.execute(
            delete(PresentationLayoutCodeModel)
            .where(PresentationLayoutCodeModel.presentation == presentation_id)
            .execution_options(skip_owner_scope=True)
        )
        await self._sql_session.execute(
            delete(PresentationVersionModel).where(
                PresentationVersionModel.presentation_id == presentation_id
            )
        )
        await self._sql_session.execute(
            delete(AuthoredExactProvenance).where(
                AuthoredExactProvenance.presentation_id == presentation_id
            )
        )
        await self._sql_session.execute(
            delete(SlideModel)
            .where(SlideModel.presentation == presentation_id)
            .execution_options(skip_owner_scope=True)
        )
        await self._sql_session.execute(
            delete(TemplateModel)
            .where(TemplateModel.id == presentation_id)
            .execution_options(skip_owner_scope=True)
        )
        if template_ids:
            await self._sql_session.execute(
                delete(TemplateV2)
                .where(TemplateV2.id.in_(template_ids))
                .execution_options(skip_owner_scope=True)
            )

        deleted_at = get_current_utc_datetime()
        presentation.content = ""
        presentation.n_slides = 0
        presentation.title = None
        presentation.file_paths = None
        presentation.outlines = None
        presentation.layout = None
        presentation.structure = None
        presentation.instructions = None
        presentation.tone = None
        presentation.verbosity = None
        presentation.include_table_of_contents = False
        presentation.include_title_slide = False
        presentation.web_search = False
        presentation.theme = None
        presentation.deck_plan = None
        presentation.mode = None
        presentation.share_token = None
        presentation.lifecycle_status = PRESENTATION_LIFECYCLE_DELETED
        presentation.deleted_at = deleted_at
        presentation.deletion_operation_id = operation_id
        presentation.updated_at = deleted_at
        self._sql_session.add(presentation)
        await self._sql_session.flush()
        return cleanup_targets

    async def delete(self, presentation: PresentationModel) -> None:
        presentation_id = presentation.id
        try:
            canonical_ids = await self._locked_template_ids(presentation_id)

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
