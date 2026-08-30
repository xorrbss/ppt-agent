"""Explicit provisioning contract for externally issued public principals."""

from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from models.sql.public_principal_binding import (
    PublicPrincipalBinding,
    build_public_principal_binding,
)
from models.sql.user import User


class PublicPrincipalProvisioningError(RuntimeError):
    pass


async def provision_public_principal_binding(
    session: AsyncSession,
    *,
    issuer: str,
    tenant_id: str,
    subject: str,
    user_id: uuid.UUID,
) -> PublicPrincipalBinding:
    """Idempotently bind one verified issuer/tenant/subject to an active user.

    Provisioning is intentionally an internal control-plane operation.  Public
    bearer-token requests can consume an existing binding but cannot create or
    change one.
    """

    user = await session.get(User, user_id)
    if user is None or not user.is_active:
        raise PublicPrincipalProvisioningError(
            "public_principal_binding_user_unavailable"
        )
    candidate = build_public_principal_binding(
        issuer=issuer,
        tenant_id=tenant_id,
        subject=subject,
        user_id=user_id,
    )
    existing = await session.get(
        PublicPrincipalBinding,
        candidate.binding_digest,
    )
    if existing is not None:
        if (
            existing.user_id != candidate.user_id
            or existing.issuer_digest != candidate.issuer_digest
            or existing.tenant_id != candidate.tenant_id
            or existing.subject_digest != candidate.subject_digest
        ):
            raise PublicPrincipalProvisioningError(
                "public_principal_binding_conflict"
            )
        return existing
    session.add(candidate)
    await session.flush()
    return candidate


__all__ = [
    "PublicPrincipalProvisioningError",
    "provision_public_principal_binding",
]
