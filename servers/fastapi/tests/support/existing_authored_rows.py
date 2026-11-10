"""Seed authored presentation rows that predate the authored driver removal.

Since 2026-11-10 (authored-removal P2) no registered driver claims an authored
identity, so the ORM write boundary
(``services.database_owner_scope._stamp_new_owned_rows``) refuses to insert a
new authored ``PresentationModel`` with ``unsupported_presentation_driver``.
An authored deck can therefore only exist as a row written before the
removal. These helpers write such a row with a Core statement, bypassing the
ORM insert hook exactly like a row already sitting in the database, and with
the identity the old write boundary used to stamp on it.
"""

from __future__ import annotations

import inspect
from typing import Any

from sqlalchemy import insert

from models.presentation_profiles import PROFILE_SCHEMA_VERSION
from models.sql.presentation import PresentationModel
from services.presentation_dashboard_metadata import presentation_dashboard_metadata
from utils.owner_context import get_current_owner_id


def existing_authored_row_values(presentation: PresentationModel) -> dict[str, Any]:
    """Column values the pre-removal write boundary stamped on an authored row."""

    values: dict[str, Any] = {}
    for column in PresentationModel.__table__.columns:
        value = getattr(presentation, column.name, None)
        if value is not None:
            values[column.name] = value
    theme = values.get("theme")
    theme = {**theme, "mode": "authored"} if isinstance(theme, dict) else {"mode": "authored"}
    values["theme"] = theme
    values["mode"] = "authored"
    values.setdefault("version", "v1-standard")
    values["generation_mode"] = "standard"
    values["profile_driver_id"] = "authored"
    values["profile_schema_version"] = PROFILE_SCHEMA_VERSION
    values["dashboard_metadata"] = presentation_dashboard_metadata(theme)
    if values.get("owner_id") is None and get_current_owner_id() is not None:
        values["owner_id"] = get_current_owner_id()
    return values


def _apply_to_object(presentation: PresentationModel, values: dict[str, Any]) -> None:
    for key in ("theme", "mode", "version", "generation_mode", "profile_driver_id",
                "profile_schema_version", "owner_id"):
        if key in values:
            setattr(presentation, key, values[key])


async def add_existing_authored_presentation(
    session: Any, presentation: PresentationModel
) -> PresentationModel:
    """Insert ``presentation`` as a pre-removal authored row (async or sync session)."""

    values = existing_authored_row_values(presentation)
    result = session.execute(insert(PresentationModel.__table__).values(**values))
    if inspect.isawaitable(result):
        await result
    _apply_to_object(presentation, values)
    return presentation


def add_existing_authored_presentation_sync(
    session: Any, presentation: PresentationModel
) -> PresentationModel:
    """Synchronous-session variant of :func:`add_existing_authored_presentation`."""

    values = existing_authored_row_values(presentation)
    session.execute(insert(PresentationModel.__table__).values(**values))
    _apply_to_object(presentation, values)
    return presentation


__all__ = [
    "add_existing_authored_presentation",
    "add_existing_authored_presentation_sync",
    "existing_authored_row_values",
]
