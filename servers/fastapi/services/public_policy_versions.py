"""Default public policy version, and the deployment-ordering gap it closed.

Admission pins exactly one ``public_policy_profiles`` row per operation and
reads its columns as immutable ceilings.  Flipping the default version in code
and migrating the row that carries it are two separate deploy steps and they
are not ordered: a rolling deploy, a worker replica started before the API, or
an operator who forgot ``MIGRATE_DATABASE_ON_STARTUP=true`` all put code that
asks for the new version in front of a database that does not have it yet.
This module used to answer that window with the previous default's row and a
warning, so decks kept generating under silently older ceilings.

That window no longer exists.  ``require_database_schema_revision_authority``
refuses startup when ``alembic_version`` does not match the code's head, so a
process that asks for the current default version cannot be running against a
database that lacks its row.  The one-step fallback is therefore unreachable,
and what used to be a warning is now an error: reaching it means the schema
contract was bypassed, and continuing on stale ceilings would hide that.

An operation pinned to an explicitly older version is still never redirected;
a missing row for it returns ``None`` and the caller rejects, as before.
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from models.sql.public_admission import PublicPolicyProfile


# The policy row admission pins by default.  ``public-default/v5`` is a clone
# of v4 that only raises ``export_deadline_seconds`` to the export ceiling in
# ``services/public_export_deadline_policy.py``; see Alembic revision
# ``b4e6f8a0c2d5``.  (v4 itself raised ``generation_deadline_seconds`` to the
# generation ceiling; revision ``a2c4e6f8b1d3``.)
PUBLIC_DEFAULT_POLICY_VERSION = "public-default/v5"

# The version the default used to fall back to.  Kept because the refusal
# below names it, and because operations admitted under it are still pinned to
# it in ``public_operation_admissions`` and ``public_quota_counters``.
PUBLIC_PREVIOUS_DEFAULT_POLICY_VERSION = "public-default/v4"


async def resolve_public_policy_row(
    session: AsyncSession, version: str
) -> PublicPolicyProfile | None:
    """Return the enabled policy row for ``version``.

    ``None`` means no usable row exists; the caller raises its own admission
    rejection so this module never decides how a *request* fails.

    Raises ``RuntimeError('public_policy_version_fallback')`` when the current
    default's row is missing while the previous default's row is still present.
    That is a database behind the code, which startup already refuses, so it is
    an invariant violation rather than a request-level rejection -- and it must
    not be answered by quietly generating under the older ceilings.
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
    raise RuntimeError(
        "public_policy_version_fallback: the database has no enabled "
        f"'{version}' policy row but still has "
        f"'{PUBLIC_PREVIOUS_DEFAULT_POLICY_VERSION}', so it is behind this "
        "code. Startup refuses that schema mismatch; reaching this point means "
        "the check was bypassed. Run `uv run alembic upgrade head` in "
        "servers/fastapi instead of generating under stale ceilings."
    )


__all__ = [
    "PUBLIC_DEFAULT_POLICY_VERSION",
    "PUBLIC_PREVIOUS_DEFAULT_POLICY_VERSION",
    "resolve_public_policy_row",
]
