"""Tenant/user-scoped pointers to credentials held outside the database."""

from __future__ import annotations

from datetime import datetime
import uuid

from sqlalchemy import CheckConstraint, Column, DateTime, ForeignKey, Index, String, Uuid, func
from sqlmodel import Field, SQLModel

from utils.datetime_utils import get_current_utc_datetime


class ProviderCredentialReference(SQLModel, table=True):
    """An opaque vault/KMS reference; this table never stores secret material."""

    __tablename__ = "provider_credential_references"
    __table_args__ = (
        CheckConstraint(
            "backend IN ('desktop_vault', 'server_kms')",
            name="ck_provider_credential_references_backend",
        ),
        Index(
            "uq_provider_credential_references_scope_provider",
            "tenant_id",
            "user_id",
            "provider",
            unique=True,
        ),
    )

    id: uuid.UUID = Field(primary_key=True, default_factory=uuid.uuid4)
    tenant_id: str = Field(sa_column=Column(String(128), nullable=False))
    user_id: uuid.UUID = Field(
        sa_column=Column(
            Uuid,
            ForeignKey(
                "user.id",
                name="fk_provider_credential_references_user_id",
                ondelete="CASCADE",
            ),
            nullable=False,
            index=True,
        )
    )
    provider: str = Field(sa_column=Column(String(64), nullable=False))
    backend: str = Field(sa_column=Column(String(32), nullable=False))
    credential_ref: str = Field(sa_column=Column(String(512), nullable=False, unique=True))
    created_at: datetime = Field(
        default_factory=get_current_utc_datetime,
        sa_column=Column(
            DateTime(timezone=True),
            nullable=False,
            default=get_current_utc_datetime,
            server_default=func.now(),
        ),
    )
    updated_at: datetime = Field(
        default_factory=get_current_utc_datetime,
        sa_column=Column(
            DateTime(timezone=True),
            nullable=False,
            default=get_current_utc_datetime,
            onupdate=get_current_utc_datetime,
            server_default=func.now(),
        ),
    )
