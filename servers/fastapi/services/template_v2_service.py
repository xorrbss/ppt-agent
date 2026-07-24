"""Canonical persistence facade for Template V2 CRUD operations.

HTTP routes own request/response compatibility and rollout policy. This service
owns shared transactions and error classification so API facades cannot drift
into independent storage implementations.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from typing import Any, Collection, Protocol
import uuid

from sqlalchemy import update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from models.sql.presentation import PresentationModel
from models.sql.template_v2 import TemplateV2
from models.sql.template_v2_local_state import TemplateV2LocalState
from services.template_v2_revision_service import append_revision
from templates.v2.constants import TEMPLATE_V2_VERSION
from templates.v2.wire_codec import decode_wire_layouts


class TemplateV2ServiceError(RuntimeError):
    """Base error translated by the owning HTTP facade."""


class TemplateV2NotFoundError(TemplateV2ServiceError):
    pass


class TemplateV2AlreadyExistsError(TemplateV2ServiceError):
    pass


class TemplateV2SourceNotFoundError(TemplateV2ServiceError):
    pass


class TemplateV2SourceIdentityError(TemplateV2ServiceError):
    pass


class TemplateV2PersistenceConflictError(TemplateV2ServiceError):
    pass


class TemplateV2RevisionConflictError(TemplateV2ServiceError):
    def __init__(self, *, expected_revision: int, current_revision: int):
        self.expected_revision = expected_revision
        self.current_revision = current_revision
        super().__init__(
            "Template V2 revision conflict: "
            f"expected {expected_revision}, current {current_revision}"
        )


@dataclass(frozen=True)
class TemplateV2Record:
    """Canonical template together with local provenance/edit state."""

    template: TemplateV2
    presentation_id: uuid.UUID
    revision: int

    def __getattr__(self, name: str) -> Any:
        return getattr(self.template, name)


@dataclass(frozen=True)
class TemplateV2LocalStateSnapshot:
    presentation_id: uuid.UUID
    revision: int


class TemplateV2LocalStatePort(Protocol):
    """Storage boundary for local fields kept outside canonical Template V2."""

    def add(
        self,
        *,
        template: TemplateV2,
        presentation_id: uuid.UUID,
    ) -> TemplateV2LocalStateSnapshot:
        ...

    async def get(
        self,
        template: TemplateV2,
    ) -> TemplateV2LocalStateSnapshot:
        ...

    async def advance_revision(
        self,
        *,
        template: TemplateV2,
        expected_revision: int | None,
    ) -> bool:
        ...


class SidecarTemplateV2LocalStatePort:
    """Production local-state adapter backed by ``TemplateV2LocalState``.

    DEPRECATED TWO-STAGE BOUNDARY: the transitional read/update fallback is
    deliberately contained here so deployments can roll through the backfill
    safely. It can be removed when the legacy columns disappear without
    changing the service or either HTTP facade.
    """

    def __init__(self, sql_session: AsyncSession):
        self._sql_session = sql_session
        self._new_states: dict[str, TemplateV2LocalState] = {}

    def add(
        self,
        *,
        template: TemplateV2,
        presentation_id: uuid.UUID,
    ) -> TemplateV2LocalStateSnapshot:
        state = TemplateV2LocalState(
            template_id=template.id,
            presentation_id=presentation_id,
            revision=1,
        )
        self._new_states[template.id] = state
        self._sql_session.add(state)
        return self._snapshot(state)

    async def get(
        self,
        template: TemplateV2,
    ) -> TemplateV2LocalStateSnapshot:
        state = self._new_states.get(template.id)
        if state is None and hasattr(self._sql_session, "get"):
            candidate = await self._sql_session.get(
                TemplateV2LocalState,
                template.id,
            )
            if isinstance(candidate, TemplateV2LocalState):
                state = candidate
        if state is not None:
            return self._snapshot(state)
        return self._transitional_snapshot(template)

    async def advance_revision(
        self,
        *,
        template: TemplateV2,
        expected_revision: int | None,
    ) -> bool:
        state = self._new_states.get(template.id)
        if state is None and hasattr(self._sql_session, "get"):
            candidate = await self._sql_session.get(
                TemplateV2LocalState,
                template.id,
            )
            if isinstance(candidate, TemplateV2LocalState):
                state = candidate
        if state is None:
            return await self._advance_transitional_revision(
                template=template,
                expected_revision=expected_revision,
            )

        statement = update(TemplateV2LocalState).where(
            TemplateV2LocalState.template_id == template.id
        )
        if expected_revision is not None:
            statement = statement.where(
                TemplateV2LocalState.revision == expected_revision
            )
        statement = statement.values(
            revision=TemplateV2LocalState.revision + 1
        )
        result = await self._sql_session.execute(statement)
        if result.rowcount != 1:
            return False

        # DEPRECATED TWO-STAGE BOUNDARY: while the original revision column
        # remains, keep it as an exact shadow of the authoritative sidecar.
        # The schema validator and downgrade guard rely on this invariant.
        revision_column = getattr(TemplateV2, "revision", None)
        if revision_column is None:
            return True
        sidecar_revision = (
            select(TemplateV2LocalState.revision)
            .where(TemplateV2LocalState.template_id == template.id)
            .scalar_subquery()
        )
        mirror_result = await self._sql_session.execute(
            update(TemplateV2)
            .where(TemplateV2.id == template.id)
            .values(revision=sidecar_revision)
        )
        return mirror_result.rowcount == 1

    @staticmethod
    def _snapshot(
        state: TemplateV2LocalState,
    ) -> TemplateV2LocalStateSnapshot:
        return TemplateV2LocalStateSnapshot(
            presentation_id=state.presentation_id,
            revision=state.revision,
        )

    @staticmethod
    def _transitional_snapshot(
        template: TemplateV2,
    ) -> TemplateV2LocalStateSnapshot:
        """Deprecated read fallback for rows not yet backfilled to sidecar."""

        try:
            return TemplateV2LocalStateSnapshot(
                presentation_id=template.presentation_id,
                revision=template.revision,
            )
        except AttributeError as error:
            raise TemplateV2PersistenceConflictError(template.id) from error

    async def _advance_transitional_revision(
        self,
        *,
        template: TemplateV2,
        expected_revision: int | None,
    ) -> bool:
        """Deprecated update fallback for rows not yet backfilled to sidecar."""

        revision_column = getattr(TemplateV2, "revision", None)
        if revision_column is None:
            raise TemplateV2PersistenceConflictError(template.id)
        statement = update(TemplateV2).where(TemplateV2.id == template.id)
        if expected_revision is not None:
            statement = statement.where(revision_column == expected_revision)
        statement = statement.values(revision=revision_column + 1)
        result = await self._sql_session.execute(statement)
        return result.rowcount == 1


_MUTABLE_FIELDS = frozenset(
    {
        "name",
        "description",
        "merged_components",
        "layouts",
        "assets",
        "is_default",
    }
)
_JSON_FIELDS = frozenset({"merged_components", "layouts", "assets"})


def _lossless_layouts(value: dict[str, Any]) -> dict[str, Any]:
    """Validate known Template V2 fields while retaining wire extensions."""

    wire_layouts = decode_wire_layouts(value)
    wire_layouts.validate_strict()
    return wire_layouts.to_storage_value()


class TemplateV2Service:
    """Persistence facade shared by structured and compatibility APIs."""

    def __init__(
        self,
        sql_session: AsyncSession,
        *,
        local_state: TemplateV2LocalStatePort | None = None,
    ):
        self._sql_session = sql_session
        self._local_state = local_state or SidecarTemplateV2LocalStatePort(
            sql_session
        )

    async def list(
        self,
        *,
        template_ids: Collection[str],
        offset: int,
        limit: int,
    ) -> list[TemplateV2Record]:
        statement = (
            select(TemplateV2)
            .where(TemplateV2.id.in_(tuple(template_ids)))
            .order_by(TemplateV2.created_at, TemplateV2.id)
            .offset(offset)
            .limit(limit)
        )
        result = await self._sql_session.execute(statement)
        return [
            await self._record(template)
            for template in result.scalars().all()
        ]

    async def get(self, template_id: str) -> TemplateV2Record:
        template = await self._get_template(template_id)
        return await self._record(template)

    async def create(
        self,
        *,
        presentation_id: uuid.UUID,
        template_id: str,
        name: str,
        description: str | None,
        layouts: dict[str, Any],
        assets: dict[str, Any] | None,
        is_default: bool,
    ) -> TemplateV2Record:
        source_presentation = await self._sql_session.get(
            PresentationModel,
            presentation_id,
        )
        if source_presentation is None:
            raise TemplateV2SourceNotFoundError(str(presentation_id))
        if (
            source_presentation.version != TEMPLATE_V2_VERSION
            or source_presentation.mode != "template"
        ):
            raise TemplateV2SourceIdentityError(str(presentation_id))
        if await self._sql_session.get(TemplateV2, template_id) is not None:
            raise TemplateV2AlreadyExistsError(template_id)

        canonical_values: dict[str, Any] = {
            "id": template_id,
            "name": name,
            "description": description,
            "layouts": _lossless_layouts(layouts),
            "assets": deepcopy(assets),
            "is_default": is_default,
        }
        # DEPRECATED TWO-STAGE BOUNDARY: required only while the old non-null
        # column still exists. New local state is written to the sidecar below.
        if "presentation_id" in TemplateV2.model_fields:
            canonical_values["presentation_id"] = presentation_id
        template = TemplateV2(**canonical_values)
        self._sql_session.add(template)
        state = self._local_state.add(
            template=template,
            presentation_id=presentation_id,
        )
        await append_revision(
            self._sql_session,
            template=template,
            revision=state.revision,
            reason="create",
        )
        try:
            await self._sql_session.commit()
        except IntegrityError as error:
            await self._classify_create_integrity_error(
                template_id=template_id,
                presentation_id=presentation_id,
                error=error,
            )
        await self._sql_session.refresh(template)
        return TemplateV2Record(
            template=template,
            presentation_id=state.presentation_id,
            revision=state.revision,
        )

    async def update(
        self,
        template_id: str,
        *,
        changes: dict[str, Any],
        expected_revision: int | None = None,
        journal_reason: str = "autosave",
    ) -> TemplateV2Record:
        unsupported = set(changes) - _MUTABLE_FIELDS
        if unsupported:
            raise ValueError(
                "unsupported Template V2 fields: "
                + ", ".join(sorted(unsupported))
            )

        template = await self._get_template(template_id)
        current_state = await self._local_state.get(template)
        if not changes:
            return self._combine(template, current_state)

        revision_advanced = await self._local_state.advance_revision(
            template=template,
            expected_revision=expected_revision,
        )
        if not revision_advanced:
            await self._sql_session.rollback()
            current = await self._get_template(template_id)
            current_state = await self._local_state.get(current)
            if expected_revision is not None:
                raise TemplateV2RevisionConflictError(
                    expected_revision=expected_revision,
                    current_revision=current_state.revision,
                )
            raise TemplateV2PersistenceConflictError(template_id)

        persisted_changes = {}
        for key, value in changes.items():
            if key == "layouts":
                persisted_changes[key] = _lossless_layouts(value)
            elif key in _JSON_FIELDS:
                persisted_changes[key] = deepcopy(value)
            else:
                persisted_changes[key] = value
        result = await self._sql_session.execute(
            update(TemplateV2)
            .where(TemplateV2.id == template_id)
            .values(**persisted_changes)
        )
        if result.rowcount != 1:
            await self._sql_session.rollback()
            raise TemplateV2PersistenceConflictError(template_id)

        await append_revision(
            self._sql_session,
            template=template,
            revision=current_state.revision + 1,
            reason=journal_reason,
            changes=persisted_changes,
        )
        try:
            await self._sql_session.commit()
        except IntegrityError as error:
            await self._rollback_persistence_conflict(error)
        await self._sql_session.refresh(template)
        refreshed_state = await self._local_state.get(template)
        return self._combine(template, refreshed_state)

    async def delete(self, template_id: str) -> TemplateV2Record:
        template = await self._get_template(template_id)
        record = await self._record(template)
        await self._sql_session.delete(template)
        try:
            await self._sql_session.commit()
        except IntegrityError as error:
            await self._rollback_persistence_conflict(error)
        return record

    async def _get_template(self, template_id: str) -> TemplateV2:
        template = await self._sql_session.get(TemplateV2, template_id)
        if template is None:
            raise TemplateV2NotFoundError(template_id)
        return template

    async def _record(self, template: TemplateV2) -> TemplateV2Record:
        return self._combine(
            template,
            await self._local_state.get(template),
        )

    @staticmethod
    def _combine(
        template: TemplateV2,
        state: TemplateV2LocalStateSnapshot,
    ) -> TemplateV2Record:
        return TemplateV2Record(
            template=template,
            presentation_id=state.presentation_id,
            revision=state.revision,
        )

    async def _classify_create_integrity_error(
        self,
        *,
        template_id: str,
        presentation_id: uuid.UUID,
        error: IntegrityError,
    ) -> None:
        await self._sql_session.rollback()
        if await self._sql_session.get(TemplateV2, template_id) is not None:
            raise TemplateV2AlreadyExistsError(template_id) from error
        if (
            await self._sql_session.get(PresentationModel, presentation_id)
            is None
        ):
            raise TemplateV2SourceNotFoundError(str(presentation_id)) from error
        raise TemplateV2PersistenceConflictError(template_id) from error

    async def _rollback_persistence_conflict(
        self,
        error: IntegrityError,
    ) -> None:
        await self._sql_session.rollback()
        raise TemplateV2PersistenceConflictError() from error
