"""Immutable Authored semantic bundle graph and the single mutable CAS pointer."""

from __future__ import annotations

from datetime import datetime
from typing import Any
import uuid

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    Index,
    JSON,
    String,
    UniqueConstraint,
    Uuid,
    func,
)
from sqlmodel import Field, SQLModel

from utils.datetime_utils import get_current_utc_datetime


def _created_at() -> Column:
    return Column(
        DateTime(timezone=True),
        nullable=False,
        default=get_current_utc_datetime,
        server_default=func.now(),
    )


def _updated_at() -> Column:
    return Column(
        DateTime(timezone=True),
        nullable=False,
        default=get_current_utc_datetime,
        onupdate=get_current_utc_datetime,
        server_default=func.now(),
    )


class AuthoredDeckEmissionManifestRecord(SQLModel, table=True):
    __tablename__ = "authored_deck_emission_manifests"
    __table_args__ = (
        UniqueConstraint("owner_id", "tenant_id", "canonical_digest", name="uq_authored_deck_manifest_digest"),
        UniqueConstraint(
            "owner_id", "tenant_id", "presentation_id", "presentation_revision", "operation_id",
            name="uq_authored_deck_manifest_lineage",
        ),
        CheckConstraint("presentation_revision >= 1", name="ck_authored_deck_manifest_revision"),
        Index(
            "ix_authored_deck_manifest_presentation",
            "owner_id",
            "tenant_id",
            "presentation_id",
            "presentation_revision",
        ),
    )

    id: uuid.UUID = Field(primary_key=True, default_factory=uuid.uuid4)
    owner_id: uuid.UUID = Field(sa_column=Column(Uuid, nullable=False))
    tenant_id: str = Field(sa_column=Column(String(128), nullable=False))
    presentation_id: uuid.UUID = Field(sa_column=Column(Uuid, nullable=False))
    presentation_revision: int = Field(sa_column=Column(BigInteger, nullable=False))
    operation_id: uuid.UUID = Field(sa_column=Column(Uuid, nullable=False))
    generation_attempt_id: uuid.UUID | None = Field(default=None, sa_column=Column(Uuid, nullable=True))
    canonical_digest: str = Field(sa_column=Column(String(71), nullable=False))
    master_contract_digest: str = Field(sa_column=Column(String(71), nullable=False))
    composition_digest: str = Field(sa_column=Column(String(71), nullable=False))
    asset_manifest_digest: str = Field(sa_column=Column(String(71), nullable=False))
    font_manifest_digest: str = Field(sa_column=Column(String(71), nullable=False))
    quality_policy_version: str = Field(sa_column=Column(String(128), nullable=False))
    payload: dict[str, Any] = Field(sa_column=Column(JSON, nullable=False))
    created_at: datetime = Field(default_factory=get_current_utc_datetime, sa_column=_created_at())


class AuthoredDeckEmissionActivePointer(SQLModel, table=True):
    __tablename__ = "authored_deck_emission_active"
    __table_args__ = (
        UniqueConstraint("owner_id", "tenant_id", "presentation_id", name="uq_authored_deck_active_pointer"),
        CheckConstraint("canonical_mode IN ('legacyHtml', 'bundleGraph')", name="ck_authored_deck_active_mode"),
        CheckConstraint("presentation_revision >= 0 AND generation >= 1", name="ck_authored_deck_active_versions"),
        CheckConstraint(
            "(canonical_mode = 'legacyHtml' AND manifest_id IS NULL) OR "
            "(canonical_mode = 'bundleGraph' AND manifest_id IS NOT NULL)",
            name="ck_authored_deck_active_manifest_mode",
        ),
    )

    id: uuid.UUID = Field(primary_key=True, default_factory=uuid.uuid4)
    owner_id: uuid.UUID = Field(sa_column=Column(Uuid, nullable=False))
    tenant_id: str = Field(sa_column=Column(String(128), nullable=False))
    presentation_id: uuid.UUID = Field(sa_column=Column(Uuid, nullable=False))
    canonical_mode: str = Field(sa_column=Column(String(16), nullable=False))
    manifest_id: uuid.UUID | None = Field(
        default=None,
        sa_column=Column(
            Uuid,
            ForeignKey("authored_deck_emission_manifests.id", ondelete="RESTRICT"),
            nullable=True,
        ),
    )
    presentation_revision: int = Field(sa_column=Column(BigInteger, nullable=False))
    canonical_digest: str = Field(sa_column=Column(String(71), nullable=False))
    generation: int = Field(default=1, sa_column=Column(BigInteger, nullable=False, server_default="1"))
    operation_id: uuid.UUID = Field(sa_column=Column(Uuid, nullable=False))
    updated_at: datetime = Field(default_factory=get_current_utc_datetime, sa_column=_updated_at())


__all__ = [
    "AuthoredDeckEmissionActivePointer",
    "AuthoredDeckEmissionManifestRecord",
]
