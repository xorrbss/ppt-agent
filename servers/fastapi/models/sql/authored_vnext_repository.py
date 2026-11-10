"""Durable authored-vNext master and deck revisions and their publications.

Revision and candidate rows are append-only.  Small head and
publication rows are the only mutable records and are advanced with SQL CAS.
"""

from __future__ import annotations

from datetime import datetime
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
    event,
    func,
)
from sqlmodel import Field, SQLModel

from utils.datetime_utils import get_current_utc_datetime


def _utc_column(*, update: bool = False) -> Column:
    return Column(
        DateTime(timezone=True),
        nullable=False,
        default=get_current_utc_datetime,
        onupdate=get_current_utc_datetime if update else None,
        server_default=func.now(),
    )


def _owner_column() -> Column:
    return Column(
        Uuid,
        ForeignKey(
            "user.id",
            name=None,
            ondelete="CASCADE",
        ),
        nullable=False,
        index=True,
    )


class AuthoredVNextMasterRevision(SQLModel, table=True):
    __tablename__ = "authored_vnext_master_revisions"
    __table_args__ = (
        UniqueConstraint("owner_scope", "tenant_id", "master_id", "revision", name="uq_avnext_master_revision"),
        CheckConstraint("revision >= 1", name="ck_avnext_master_revision_positive"),
        CheckConstraint("status IN ('draft', 'published')", name="ck_avnext_master_status"),
        Index("ix_avnext_master_scope_id_created", "owner_scope", "tenant_id", "master_id", "created_at"),
    )

    id: uuid.UUID = Field(primary_key=True, default_factory=uuid.uuid4)
    owner_id: uuid.UUID = Field(sa_column=_owner_column())
    owner_scope: str = Field(sa_column=Column(String(128), nullable=False))
    tenant_id: str = Field(sa_column=Column(String(128), nullable=False))
    master_id: str = Field(sa_column=Column(String(256), nullable=False))
    revision: int = Field(sa_column=Column(BigInteger, nullable=False))
    digest: str = Field(sa_column=Column(String(71), nullable=False))
    status: str = Field(sa_column=Column(String(16), nullable=False))
    payload: dict = Field(sa_column=Column(JSON, nullable=False))
    created_at: datetime = Field(default_factory=get_current_utc_datetime, sa_column=_utc_column())


class AuthoredVNextSlideRevision(SQLModel, table=True):
    __tablename__ = "authored_vnext_slide_revisions"
    __table_args__ = (
        UniqueConstraint(
            "owner_scope", "tenant_id", "deck_id", "slide_id", "revision", name="uq_avnext_slide_revision"
        ),
        CheckConstraint("revision >= 1", name="ck_avnext_slide_revision_positive"),
        Index("ix_avnext_slide_scope_deck", "owner_scope", "tenant_id", "deck_id", "slide_id"),
    )

    id: uuid.UUID = Field(primary_key=True, default_factory=uuid.uuid4)
    owner_id: uuid.UUID = Field(sa_column=_owner_column())
    owner_scope: str = Field(sa_column=Column(String(128), nullable=False))
    tenant_id: str = Field(sa_column=Column(String(128), nullable=False))
    deck_id: str = Field(sa_column=Column(String(256), nullable=False))
    slide_id: str = Field(sa_column=Column(String(256), nullable=False))
    revision: int = Field(sa_column=Column(BigInteger, nullable=False))
    spec_digest: str = Field(sa_column=Column(String(71), nullable=False))
    master_digest: str = Field(sa_column=Column(String(71), nullable=False))
    payload: dict = Field(sa_column=Column(JSON, nullable=False))
    created_at: datetime = Field(default_factory=get_current_utc_datetime, sa_column=_utc_column())


class AuthoredVNextDeckRevision(SQLModel, table=True):
    __tablename__ = "authored_vnext_deck_revisions"
    __table_args__ = (
        UniqueConstraint("owner_scope", "tenant_id", "deck_id", "revision", name="uq_avnext_deck_revision"),
        CheckConstraint("revision >= 1", name="ck_avnext_deck_revision_positive"),
        Index("ix_avnext_deck_scope_id_created", "owner_scope", "tenant_id", "deck_id", "created_at"),
    )

    id: uuid.UUID = Field(primary_key=True, default_factory=uuid.uuid4)
    owner_id: uuid.UUID = Field(sa_column=_owner_column())
    owner_scope: str = Field(sa_column=Column(String(128), nullable=False))
    tenant_id: str = Field(sa_column=Column(String(128), nullable=False))
    deck_id: str = Field(sa_column=Column(String(256), nullable=False))
    revision: int = Field(sa_column=Column(BigInteger, nullable=False))
    digest: str = Field(sa_column=Column(String(71), nullable=False))
    master_id: str = Field(sa_column=Column(String(256), nullable=False))
    master_revision: int = Field(sa_column=Column(BigInteger, nullable=False))
    master_digest: str = Field(sa_column=Column(String(71), nullable=False))
    payload: dict = Field(sa_column=Column(JSON, nullable=False))
    created_at: datetime = Field(default_factory=get_current_utc_datetime, sa_column=_utc_column())


class AuthoredVNextEntityHead(SQLModel, table=True):
    __tablename__ = "authored_vnext_entity_heads"
    __table_args__ = (
        UniqueConstraint("owner_scope", "tenant_id", "entity_kind", "entity_id", name="uq_avnext_entity_head"),
        CheckConstraint("entity_kind IN ('master', 'deck')", name="ck_avnext_head_kind"),
        CheckConstraint("revision >= 1 AND generation >= 1", name="ck_avnext_head_versions"),
    )

    id: uuid.UUID = Field(primary_key=True, default_factory=uuid.uuid4)
    owner_id: uuid.UUID = Field(sa_column=_owner_column())
    owner_scope: str = Field(sa_column=Column(String(128), nullable=False))
    tenant_id: str = Field(sa_column=Column(String(128), nullable=False))
    entity_kind: str = Field(sa_column=Column(String(16), nullable=False))
    entity_id: str = Field(sa_column=Column(String(256), nullable=False))
    revision: int = Field(sa_column=Column(BigInteger, nullable=False))
    digest: str = Field(sa_column=Column(String(71), nullable=False))
    generation: int = Field(default=1, sa_column=Column(BigInteger, nullable=False, server_default="1"))
    updated_at: datetime = Field(default_factory=get_current_utc_datetime, sa_column=_utc_column(update=True))


class AuthoredVNextCandidate(SQLModel, table=True):
    __tablename__ = "authored_vnext_candidates"
    __table_args__ = (
        UniqueConstraint("owner_scope", "tenant_id", "candidate_digest", name="uq_avnext_candidate_digest"),
    )

    id: uuid.UUID = Field(primary_key=True, default_factory=uuid.uuid4)
    owner_id: uuid.UUID = Field(sa_column=_owner_column())
    owner_scope: str = Field(sa_column=Column(String(128), nullable=False))
    tenant_id: str = Field(sa_column=Column(String(128), nullable=False))
    candidate_digest: str = Field(sa_column=Column(String(71), nullable=False))
    payload: dict = Field(sa_column=Column(JSON, nullable=False))
    created_at: datetime = Field(default_factory=get_current_utc_datetime, sa_column=_utc_column())


class AuthoredVNextPublicationPointer(SQLModel, table=True):
    __tablename__ = "authored_vnext_publication_pointers"
    __table_args__ = (
        UniqueConstraint(
            "owner_scope", "tenant_id", "publication_kind", "entity_id", name="uq_avnext_publication_pointer"
        ),
        CheckConstraint("publication_kind IN ('master', 'deck_artifact')", name="ck_avnext_publication_kind"),
        CheckConstraint("revision >= 1 AND generation >= 1", name="ck_avnext_publication_versions"),
    )

    id: uuid.UUID = Field(primary_key=True, default_factory=uuid.uuid4)
    owner_id: uuid.UUID = Field(sa_column=_owner_column())
    owner_scope: str = Field(sa_column=Column(String(128), nullable=False))
    tenant_id: str = Field(sa_column=Column(String(128), nullable=False))
    publication_kind: str = Field(sa_column=Column(String(32), nullable=False))
    entity_id: str = Field(sa_column=Column(String(256), nullable=False))
    revision: int = Field(sa_column=Column(BigInteger, nullable=False))
    digest: str = Field(sa_column=Column(String(71), nullable=False))
    artifact_id: uuid.UUID | None = Field(default=None, sa_column=Column(Uuid, nullable=True))
    generation: int = Field(default=1, sa_column=Column(BigInteger, nullable=False, server_default="1"))
    operation_id: uuid.UUID = Field(default_factory=uuid.uuid4, sa_column=Column(Uuid, nullable=False))
    updated_at: datetime = Field(default_factory=get_current_utc_datetime, sa_column=_utc_column(update=True))


_IMMUTABLE_MODELS = (
    AuthoredVNextMasterRevision,
    AuthoredVNextSlideRevision,
    AuthoredVNextDeckRevision,
    AuthoredVNextCandidate,
)


def _reject_immutable_change(*_args: object) -> None:
    raise ValueError("authored_vnext_record_is_append_only")


for _model in _IMMUTABLE_MODELS:
    event.listen(_model, "before_update", _reject_immutable_change)
    event.listen(_model, "before_delete", _reject_immutable_change)


AUTHORED_VNEXT_SQL_TABLES = tuple(
    model.__table__
    for model in (
        AuthoredVNextMasterRevision,
        AuthoredVNextSlideRevision,
        AuthoredVNextDeckRevision,
        AuthoredVNextEntityHead,
        AuthoredVNextCandidate,
        AuthoredVNextPublicationPointer,
    )
)
