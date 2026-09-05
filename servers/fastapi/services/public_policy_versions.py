"""Default public policy version and its deployment-ordering fallback.

Admission pins exactly one ``public_policy_profiles`` row per operation and
reads its columns as immutable ceilings.  Flipping the default version in code
and migrating the row that carries it are two separate deploy steps and they
are not ordered: a rolling deploy, a worker replica started before the API, or
an operator who forgot ``MIGRATE_DATABASE_ON_STARTUP=true`` all put code that
asks for the new version in front of a database that does not have it yet.
Admission fails closed with ``public_policy_unavailable`` when the requested
row is missing, so without this seam that window rejects every deck outright.

The fallback is deliberately one step wide and applies only to the *current
default* version.  An operation pinned to an explicitly older version keeps
that version's limits; nothing here redirects it.

Falling back is a degraded mode, not a resting state: the previous default
carries the previous generation-deadline ceiling, so large decks are capped
again until the migration runs.  Every fallback logs a warning naming both
versions.
"""

from __future__ import annotations

import logging

from sqlalchemy.ext.asyncio import AsyncSession

from models.sql.public_admission import PublicPolicyProfile


logger = logging.getLogger(__name__)


# The policy row admission pins by default.  ``public-default/v4`` is a clone
# of v3 that only raises ``generation_deadline_seconds`` to the code ceiling in
# ``services/public_generation_deadline_policy.py``; see Alembic revision
# ``a2c4e6f8b1d3``.
PUBLIC_DEFAULT_POLICY_VERSION = "public-default/v4"

# The one version the default may fall back to while a deployment still has
# code in front of the migration.
PUBLIC_PREVIOUS_DEFAULT_POLICY_VERSION = "public-default/v3"


async def resolve_public_policy_row(
    session: AsyncSession, version: str
) -> PublicPolicyProfile | None:
    """Return the enabled policy row for ``version``, or the previous default.

    ``None`` means no usable row exists; the caller raises its own admission
    rejection so this module never decides how a request fails.
    """

    row = await session.get(PublicPolicyProfile, version)
    if row is not None and row.enabled:
        return row
    if version != PUBLIC_DEFAULT_POLICY_VERSION:
        return None
    previous = await session.get(
        PublicPolicyProfile, PUBLIC_PREVIOUS_DEFAULT_POLICY_VERSION
    )
    if previous is None or not previous.enabled:
        return None
    logger.warning(
        "public_policy_version_fallback requested=%s applied=%s: the database "
        "is behind the code. Run the Alembic migrations; until then the "
        "previous generation deadline ceiling caps large decks.",
        version,
        PUBLIC_PREVIOUS_DEFAULT_POLICY_VERSION,
    )
    return previous


__all__ = [
    "PUBLIC_DEFAULT_POLICY_VERSION",
    "PUBLIC_PREVIOUS_DEFAULT_POLICY_VERSION",
    "resolve_public_policy_row",
]
