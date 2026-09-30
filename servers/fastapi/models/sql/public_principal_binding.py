"""Provisioned external-principal to internal-owner bindings.

Raw OIDC subjects are deliberately not persisted.  A binding is scoped by the
verified issuer and tenant and resolves to one existing Presenton user.
"""

from __future__ import annotations

from datetime import datetime
import uuid

from sqlalchemy import Column, DateTime, ForeignKey, Index, String, UniqueConstraint, Uuid, func
from sqlmodel import Field, SQLModel

from utils.datetime_utils import get_current_utc_datetime
from utils.hashing import sha256_hex


def external_principal_component_digest(value: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise ValueError("external_principal_component_required")
    return sha256_hex(normalized.encode("utf-8"))


def external_principal_binding_digest(
    *, issuer: str, tenant_id: str, subject: str
) -> str:
    components = (issuer.strip(), tenant_id.strip(), subject.strip())
    if not all(components):
        raise ValueError("external_principal_binding_identity_required")
    return sha256_hex("\0".join(components).encode("utf-8"))


class PublicPrincipalBinding(SQLModel, table=True):
    """Fail-closed provisioning record for one external identity."""

    __tablename__ = "public_principal_bindings"
    __table_args__ = (
        UniqueConstraint(
            "issuer_digest",
            "tenant_id",
            "subject_digest",
            name="uq_public_principal_binding_identity",
        ),
        Index(
            "ix_public_principal_binding_owner",
            "tenant_id",
            "user_id",
        ),
    )

    binding_digest: str = Field(
        sa_column=Column(String(64), primary_key=True)
    )
    issuer_digest: str = Field(sa_column=Column(String(64), nullable=False))
    tenant_id: str = Field(sa_column=Column(String(128), nullable=False))
    subject_digest: str = Field(sa_column=Column(String(64), nullable=False))
    user_id: uuid.UUID = Field(
        sa_column=Column(
            Uuid,
            ForeignKey("user.id", ondelete="CASCADE"),
            nullable=False,
        )
    )
    created_at: datetime = Field(
        default_factory=get_current_utc_datetime,
        sa_column=Column(
            DateTime(timezone=True),
            nullable=False,
            server_default=func.now(),
        ),
    )


def build_public_principal_binding(
    *, issuer: str, tenant_id: str, subject: str, user_id: uuid.UUID
) -> PublicPrincipalBinding:
    normalized_tenant = tenant_id.strip()
    return PublicPrincipalBinding(
        binding_digest=external_principal_binding_digest(
            issuer=issuer,
            tenant_id=normalized_tenant,
            subject=subject,
        ),
        issuer_digest=external_principal_component_digest(issuer),
        tenant_id=normalized_tenant,
        subject_digest=external_principal_component_digest(subject),
        user_id=user_id,
    )


__all__ = [
    "PublicPrincipalBinding",
    "build_public_principal_binding",
    "external_principal_binding_digest",
    "external_principal_component_digest",
]
