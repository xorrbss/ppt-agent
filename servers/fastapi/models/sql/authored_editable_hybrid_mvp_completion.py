"""Append-only, scope-bound editable Hybrid MVP Gate 1E completions."""

from __future__ import annotations

from datetime import datetime
import uuid

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Column,
    DateTime,
    Index,
    Integer,
    LargeBinary,
    String,
    UniqueConstraint,
    Uuid,
    event,
    func,
)
from sqlmodel import Field, SQLModel

from utils.datetime_utils import get_current_utc_datetime


class AuthoredEditableHybridMvpCompletion(SQLModel, table=True):
    __tablename__ = "authored_editable_hybrid_mvp_completions_v1"
    __table_args__ = (
        UniqueConstraint(
            "owner_scope",
            "tenant_id",
            "deck_id",
            "deck_revision",
            name="uq_authored_editable_mvp_completion_v1_subject",
        ),
        UniqueConstraint(
            "issuer_id",
            "signer_key_id",
            "receipt_nonce",
            name="uq_authored_editable_mvp_completion_v1_nonce",
        ),
        UniqueConstraint(
            "owner_scope",
            "tenant_id",
            "sealed_result_sha256",
            name="uq_authored_editable_mvp_completion_v1_bytes",
        ),
        CheckConstraint("cas_version = 1", name="ck_authored_editable_mvp_completion_v1_cas"),
        CheckConstraint("artifact_count = 35", name="ck_authored_editable_mvp_completion_v1_artifacts"),
        CheckConstraint("skipped_count = 0", name="ck_authored_editable_mvp_completion_v1_no_skip"),
        CheckConstraint("authority_domain = 'actual_product'", name="ck_authored_editable_mvp_completion_v1_domain"),
        CheckConstraint(
            "profile_id = 'authored-editable-hybrid-ko-en-basic-v1'",
            name="ck_authored_editable_mvp_completion_v1_profile",
        ),
        Index(
            "ix_authored_editable_mvp_completion_v1_current",
            "owner_scope",
            "tenant_id",
            "deck_id",
            "deck_revision",
            "expires_at",
        ),
    )

    id: uuid.UUID = Field(primary_key=True, default_factory=uuid.uuid4)
    owner_id: uuid.UUID = Field(sa_column=Column(Uuid, nullable=False))
    owner_scope: str = Field(sa_column=Column(String(128), nullable=False))
    tenant_id: str = Field(sa_column=Column(String(128), nullable=False))
    deck_id: str = Field(sa_column=Column(String(256), nullable=False))
    deck_revision: int = Field(sa_column=Column(BigInteger, nullable=False))
    deck_digest: str = Field(sa_column=Column(String(71), nullable=False))
    scene_digest: str = Field(sa_column=Column(String(71), nullable=False))
    authority_domain: str = Field(default="actual_product", sa_column=Column(String(32), nullable=False))
    profile_id: str = Field(sa_column=Column(String(128), nullable=False))
    profile_digest: str = Field(sa_column=Column(String(71), nullable=False))
    gate_manifest_digest: str = Field(sa_column=Column(String(71), nullable=False))
    generation_attempt_id: str = Field(sa_column=Column(String(256), nullable=False))
    hybrid_export_attempt_id: str = Field(sa_column=Column(String(256), nullable=False))
    artifact_inventory_digest: str = Field(sa_column=Column(String(71), nullable=False))
    artifact_count: int = Field(default=35, sa_column=Column(Integer, nullable=False))
    skipped_count: int = Field(default=0, sa_column=Column(Integer, nullable=False))
    result_core_digest: str = Field(sa_column=Column(String(71), nullable=False))
    result_set_digest: str = Field(sa_column=Column(String(71), nullable=False))
    sealed_result_sha256: str = Field(sa_column=Column(String(71), nullable=False))
    sealed_result_bytes: bytes = Field(sa_column=Column(LargeBinary, nullable=False))
    validation_receipt_bytes: bytes = Field(sa_column=Column(LargeBinary, nullable=False))
    verifier_trust_bytes: bytes = Field(sa_column=Column(LargeBinary, nullable=False))
    verifier_trust_sha256: str = Field(sa_column=Column(String(71), nullable=False))
    receipt_digest: str = Field(sa_column=Column(String(71), nullable=False))
    issuer_id: str = Field(sa_column=Column(String(128), nullable=False))
    signer_key_id: str = Field(sa_column=Column(String(128), nullable=False))
    receipt_nonce: str = Field(sa_column=Column(String(256), nullable=False))
    issued_at: datetime = Field(sa_column=Column(DateTime(timezone=True), nullable=False))
    expires_at: datetime = Field(sa_column=Column(DateTime(timezone=True), nullable=False))
    cas_version: int = Field(default=1, sa_column=Column(Integer, nullable=False, server_default="1"))
    created_at: datetime = Field(
        default_factory=get_current_utc_datetime,
        sa_column=Column(DateTime(timezone=True), nullable=False, server_default=func.now()),
    )


def _reject_mutation(*_args: object) -> None:
    raise ValueError("authored_editable_mvp_completion_is_append_only")


event.listen(AuthoredEditableHybridMvpCompletion, "before_update", _reject_mutation)
event.listen(AuthoredEditableHybridMvpCompletion, "before_delete", _reject_mutation)
