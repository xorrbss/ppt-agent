"""Compare-and-swap predicate for JSON columns, shared by slide publishers.

Moved unchanged out of ``services.slidespec_artifact_store`` when SlideSpec was
removed; the svg page remake publishes through it.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import cast, literal
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncSession


def _json_cas_predicate(session: AsyncSession, column: Any, expected: Any) -> Any:
    """Keep SQL NULL distinct while comparing PostgreSQL JSON structurally."""

    if expected is None:
        return column.is_(None)
    if session.get_bind().dialect.name == "postgresql":
        # PostgreSQL JSON has no equality operator.  Cast both operands to
        # JSONB without dropping either immutable snapshot predicate.
        return cast(column, JSONB) == literal(expected, type_=JSONB)
    return column == expected
