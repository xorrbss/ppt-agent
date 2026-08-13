"""Fail-closed runtime policy for frozen non-authored presentations."""

from __future__ import annotations

import uuid
from typing import Final

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from models.compatibility_migration import (
    FrozenCompatibilityOperation,
    FrozenOperationDecision,
    FrozenRuntimeDescriptor,
)
from models.sql.presentation import (
    PRESENTATION_LIFECYCLE_DELETED,
    PresentationModel,
)
from models.sql.slide import SlideModel
from services.compatibility_migration.census import classify_presentation
from services.presentation_profiles import (
    PresentationProfile,
    PresentationProfileError,
    resolve_presentation_profile,
)
from utils.owner_context import get_current_owner_id

FROZEN_ALLOWED_OPERATIONS: Final = tuple(FrozenCompatibilityOperation)


class FrozenCompatibilityError(ValueError):
    def __init__(self, code: str, *, status_code: int = 409):
        self.code = code
        self.status_code = status_code
        super().__init__(code)


def require_authored_mutation_target(
    presentation: PresentationModel,
) -> PresentationProfile:
    """Allow mutable workflows only for canonical authored identities."""

    try:
        profile = resolve_presentation_profile(presentation)
    except PresentationProfileError as error:
        raise FrozenCompatibilityError(
            "compatibility_presentation_quarantined"
        ) from error
    if profile.driver_id != "authored":
        raise FrozenCompatibilityError("legacy_presentation_frozen")
    return profile


async def require_authored_mutation_evidence(
    sql_session: AsyncSession,
    presentation: PresentationModel,
) -> PresentationProfile:
    """Require authored profile and matching, non-malformed slide evidence."""

    profile = require_authored_mutation_target(presentation)
    slides = list(
        (
            await sql_session.scalars(
                select(SlideModel)
                .where(SlideModel.presentation == presentation.id)
                .order_by(SlideModel.index, SlideModel.id)
            )
        ).all()
    )
    if any(slide.owner_id != presentation.owner_id for slide in slides):
        raise FrozenCompatibilityError("compatibility_presentation_quarantined")
    census_entry = classify_presentation(presentation, slides)
    if census_entry.quarantine:
        raise FrozenCompatibilityError("compatibility_presentation_quarantined")
    return profile


async def _require_owned_presentation(
    sql_session: AsyncSession,
    presentation_id: uuid.UUID,
) -> PresentationModel:
    presentation = await sql_session.get(PresentationModel, presentation_id)
    if (
        presentation is None
        or presentation.lifecycle_status == PRESENTATION_LIFECYCLE_DELETED
        or presentation.owner_id != get_current_owner_id()
    ):
        raise FrozenCompatibilityError(
            "compatibility_presentation_not_found", status_code=404
        )
    return presentation


async def get_frozen_runtime_descriptor(
    sql_session: AsyncSession,
    presentation_id: uuid.UUID,
) -> FrozenRuntimeDescriptor:
    presentation = await _require_owned_presentation(sql_session, presentation_id)
    slides = list(
        (
            await sql_session.scalars(
                select(SlideModel)
                .where(SlideModel.presentation == presentation_id)
                .order_by(SlideModel.index, SlideModel.id)
            )
        ).all()
    )
    census_entry = classify_presentation(presentation, slides)
    if census_entry.quarantine:
        raise FrozenCompatibilityError(
            "compatibility_presentation_quarantined", status_code=409
        )
    try:
        profile = resolve_presentation_profile(presentation)
    except PresentationProfileError as error:
        raise FrozenCompatibilityError(
            "compatibility_presentation_quarantined", status_code=409
        ) from error
    if profile.driver_id == "authored":
        raise FrozenCompatibilityError("compatibility_runtime_not_applicable")
    return FrozenRuntimeDescriptor(
        presentation_id=presentation.id,
        source_driver_id=profile.driver_id,
        allowed_operations=FROZEN_ALLOWED_OPERATIONS,
    )


async def authorize_frozen_operation(
    sql_session: AsyncSession,
    presentation_id: uuid.UUID,
    operation: str,
) -> FrozenOperationDecision:
    await get_frozen_runtime_descriptor(sql_session, presentation_id)
    try:
        allowed_operation = FrozenCompatibilityOperation(operation)
    except ValueError as error:
        raise FrozenCompatibilityError("legacy_presentation_frozen") from error
    return FrozenOperationDecision(
        presentation_id=presentation_id,
        operation=allowed_operation,
    )


__all__ = [
    "FROZEN_ALLOWED_OPERATIONS",
    "FrozenCompatibilityError",
    "authorize_frozen_operation",
    "get_frozen_runtime_descriptor",
    "require_authored_mutation_evidence",
    "require_authored_mutation_target",
]
