"""Durable export-attempt identity and its schema-versioned provenance."""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any, Literal, Optional
import uuid

from pydantic import (
    BaseModel,
    ConfigDict,
    Field as PydanticField,
    field_validator,
    model_validator,
)
from sqlalchemy import (
    JSON,
    BigInteger,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    Uuid,
    func,
    text,
)
from sqlmodel import Field, SQLModel

from models.sql.workspace import get_workspace_owner_scope
from utils.datetime_utils import get_current_utc_datetime
from utils.owner_context import get_current_owner_id


PRESENTATION_EXPORT_FORMATS = ("pptx", "pdf")
PRESENTATION_EXPORT_ATTEMPT_STATES = (
    "pending",
    "running",
    "succeeded",
    "failed",
)
PRESENTATION_EXPORT_ADAPTERS = (
    "native_clone",
    "native_reconstructed",
    "general",
)
PRESENTATION_EXPORT_REQUESTED_ADAPTERS = ("auto", *PRESENTATION_EXPORT_ADAPTERS)
_SENSITIVE_PROVENANCE_KEYS = {
    "access_key",
    "access_token",
    "api_key",
    "authorization",
    "cookie",
    "credentials",
    "password",
    "private_key",
    "refresh_token",
    "secret",
    "secret_key",
    "session_cookie",
    "token",
}
_SENSITIVE_PROVENANCE_KEYS_COMPACT = {
    re.sub(r"[^a-z0-9]", "", key) for key in _SENSITIVE_PROVENANCE_KEYS
}


def _is_sha256(value: str) -> bool:
    return len(value) == 64 and all(character in "0123456789abcdef" for character in value)


def _sha256_sql(column: str) -> str:
    remainder = column
    for character in "0123456789abcdef":
        remainder = f"replace({remainder}, '{character}', '')"
    return (
        f"length({column}) = 64 AND lower({column}) = {column} "
        f"AND length({remainder}) = 0"
    )


def _reject_sensitive_provenance_keys(value: Any) -> Any:
    if isinstance(value, dict):
        for key, child in value.items():
            raw_key = str(key).strip()
            camel_split = re.sub(r"(.)([A-Z][a-z]+)", r"\1_\2", raw_key)
            camel_split = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", camel_split)
            normalized = re.sub(r"[^a-z0-9]+", "_", camel_split.lower()).strip(
                "_"
            )
            compact = re.sub(r"[^a-z0-9]", "", raw_key.lower())
            if normalized in _SENSITIVE_PROVENANCE_KEYS or any(
                normalized.endswith(f"_{suffix}")
                for suffix in _SENSITIVE_PROVENANCE_KEYS
            ) or compact in _SENSITIVE_PROVENANCE_KEYS_COMPACT or any(
                compact.endswith(suffix)
                for suffix in _SENSITIVE_PROVENANCE_KEYS_COMPACT
            ):
                raise ValueError("export provenance must not contain credentials")
            _reject_sensitive_provenance_keys(child)
    elif isinstance(value, (list, tuple)):
        for child in value:
            _reject_sensitive_provenance_keys(child)
    return value


class ExportCompilerRuntimeProvenance(BaseModel):
    model_config = ConfigDict(extra="forbid")

    compiler: str = PydanticField(min_length=1, max_length=128)
    compiler_version: str = PydanticField(min_length=1, max_length=128)
    runtime: str = PydanticField(min_length=1, max_length=128)
    runtime_version: str = PydanticField(min_length=1, max_length=128)


class ExportFreshnessProvenance(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_revision: int = PydanticField(ge=0)
    source_snapshot_sha256: str
    package_sha256: str | None = None
    asset_digests_match: bool
    package_digest_match: bool

    @field_validator("source_snapshot_sha256", "package_sha256")
    @classmethod
    def validate_digest(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.lower()
        if not _is_sha256(normalized):
            raise ValueError("export freshness digests must be SHA-256 values")
        return normalized


class PresentationExportAttemptProvenance(BaseModel):
    """Closed JSON contract stored with one deterministic adapter decision."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1] = 1
    adapter_chain: tuple[
        Literal["native_clone", "native_reconstructed", "general"], ...
    ] = PydanticField(min_length=1, max_length=3)
    capability_evidence: dict[str, Any] = PydanticField(min_length=1)
    fallback_reason: str | None = PydanticField(default=None, max_length=256)
    asset_digests: dict[str, str] = PydanticField(default_factory=dict)
    compiler_runtime: ExportCompilerRuntimeProvenance
    freshness: ExportFreshnessProvenance

    @field_validator("adapter_chain")
    @classmethod
    def validate_adapter_chain(
        cls,
        value: tuple[
            Literal["native_clone", "native_reconstructed", "general"], ...
        ],
    ) -> tuple[Literal["native_clone", "native_reconstructed", "general"], ...]:
        if len(value) != len(set(value)):
            raise ValueError("export adapter chain entries must be unique")
        return value

    @field_validator("capability_evidence")
    @classmethod
    def reject_sensitive_capability_evidence(
        cls,
        value: dict[str, Any],
    ) -> dict[str, Any]:
        return _reject_sensitive_provenance_keys(value)

    @field_validator("asset_digests")
    @classmethod
    def validate_asset_digests(cls, value: dict[str, str]) -> dict[str, str]:
        normalized: dict[str, str] = {}
        for asset_id, digest in value.items():
            key = asset_id.strip()
            candidate = digest.lower()
            if not key or not _is_sha256(candidate):
                raise ValueError("export asset digests must use nonblank keys and SHA-256 values")
            normalized[key] = candidate
        return normalized

    @model_validator(mode="after")
    def validate_fallback_evidence(self) -> "PresentationExportAttemptProvenance":
        if len(self.adapter_chain) > 1 and not (self.fallback_reason or "").strip():
            raise ValueError("export adapter fallback requires a stable reason")
        if self.fallback_reason is not None:
            self.fallback_reason = self.fallback_reason.strip()
        return self


def validate_export_attempt_provenance(value: dict[str, Any]) -> dict[str, Any]:
    """Return the canonical JSON object accepted by the persistence boundary."""

    return PresentationExportAttemptProvenance.model_validate(value).model_dump(
        mode="json"
    )


class PresentationExportAttempt(SQLModel, table=True):
    __tablename__ = "presentation_export_attempts"
    __table_args__ = (
        UniqueConstraint(
            "owner_scope",
            "presentation_id",
            "format",
            "idempotency_key_hash",
            name="uq_export_attempts_scope_presentation_format_key",
        ),
        CheckConstraint(
            "format IN ('pptx', 'pdf')",
            name="ck_export_attempts_format",
        ),
        CheckConstraint(
            "state IN ('pending', 'running', 'succeeded', 'failed')",
            name="ck_export_attempts_state",
        ),
        CheckConstraint(
            "source_revision >= 0",
            name="ck_export_attempts_source_revision",
        ),
        CheckConstraint(
            f"({_sha256_sql('source_snapshot_sha256')}) AND "
            f"({_sha256_sql('request_hash')}) AND "
            f"({_sha256_sql('idempotency_key_hash')})",
            name="ck_export_attempts_hashes",
        ),
        CheckConstraint(
            "artifact_sha256 IS NULL OR "
            f"({_sha256_sql('artifact_sha256')})",
            name="ck_export_attempts_artifact_digest",
        ),
        CheckConstraint(
            "artifact_size_bytes IS NULL OR artifact_size_bytes >= 0",
            name="ck_export_attempts_artifact_size",
        ),
        CheckConstraint(
            "provenance_schema_version >= 1",
            name="ck_export_attempts_provenance_schema",
        ),
        CheckConstraint(
            "requested_adapter IN "
            "('auto', 'native_clone', 'native_reconstructed', 'general')",
            name="ck_export_attempts_requested_adapter",
        ),
        CheckConstraint(
            "selected_adapter IS NULL OR selected_adapter IN "
            "('native_clone', 'native_reconstructed', 'general')",
            name="ck_export_attempts_selected_adapter",
        ),
        CheckConstraint(
            "(state = 'pending' AND started_at IS NULL AND completed_at IS NULL "
            "AND artifact_sha256 IS NULL AND artifact_size_bytes IS NULL "
            "AND failure_code IS NULL) OR "
            "(state = 'running' AND started_at IS NOT NULL AND completed_at IS NULL "
            "AND artifact_sha256 IS NULL AND artifact_size_bytes IS NULL "
            "AND failure_code IS NULL) OR "
            "(state = 'succeeded' AND started_at IS NOT NULL "
            "AND completed_at IS NOT NULL AND selected_adapter IS NOT NULL "
            "AND artifact_sha256 IS NOT NULL AND artifact_size_bytes IS NOT NULL "
            "AND failure_code IS NULL AND failure_path IS NULL) OR "
            "(state = 'failed' AND started_at IS NOT NULL "
            "AND completed_at IS NOT NULL AND artifact_sha256 IS NULL "
            "AND artifact_size_bytes IS NULL AND failure_code IS NOT NULL)",
            name="ck_export_attempts_terminal_state",
        ),
        Index(
            "ix_export_attempts_presentation_created",
            "presentation_id",
            "created_at",
        ),
        Index("ix_export_attempts_state_created", "state", "created_at"),
        Index("ix_export_attempts_operation", "workspace_operation_id"),
    )

    id: uuid.UUID = Field(primary_key=True, default_factory=uuid.uuid4)
    owner_id: Optional[uuid.UUID] = Field(
        default_factory=get_current_owner_id,
        exclude=True,
        sa_column=Column(
            Uuid,
            ForeignKey(
                "user.id",
                name="fk_export_attempts_owner_id",
                ondelete="CASCADE",
            ),
            nullable=True,
            default=get_current_owner_id,
        ),
    )
    owner_scope: str = Field(
        default_factory=get_workspace_owner_scope,
        sa_column=Column(
            String(36),
            nullable=False,
            default=get_workspace_owner_scope,
            server_default="anonymous",
        ),
    )
    presentation_id: uuid.UUID = Field(
        sa_column=Column(
            Uuid,
            ForeignKey(
                "presentations.id",
                name="fk_export_attempts_presentation_id",
                ondelete="CASCADE",
            ),
            nullable=False,
        )
    )
    workspace_operation_id: Optional[uuid.UUID] = Field(
        default=None,
        sa_column=Column(
            Uuid,
            ForeignKey(
                "workspace_operations.id",
                name="fk_export_attempts_workspace_operation_id",
                ondelete="SET NULL",
            ),
            nullable=True,
        ),
    )
    format: str = Field(sa_column=Column(String(8), nullable=False))
    state: str = Field(
        default="pending",
        sa_column=Column(
            String(16),
            nullable=False,
            default="pending",
            server_default="pending",
        ),
    )
    source_revision: int = Field(sa_column=Column(BigInteger, nullable=False))
    source_snapshot_sha256: str = Field(
        sa_column=Column(String(64), nullable=False)
    )
    request_hash: str = Field(sa_column=Column(String(64), nullable=False))
    idempotency_key_hash: str = Field(
        sa_column=Column(String(64), nullable=False)
    )
    requested_adapter: str = Field(sa_column=Column(String(64), nullable=False))
    selected_adapter: Optional[str] = Field(
        default=None,
        sa_column=Column(String(64), nullable=True),
    )
    artifact_sha256: Optional[str] = Field(
        default=None,
        sa_column=Column(String(64), nullable=True),
    )
    artifact_size_bytes: Optional[int] = Field(
        default=None,
        sa_column=Column(BigInteger, nullable=True),
    )
    failure_code: Optional[str] = Field(
        default=None,
        sa_column=Column(String(128), nullable=True),
    )
    failure_path: Optional[str] = Field(
        default=None,
        sa_column=Column(String(512), nullable=True),
    )
    provenance_schema_version: int = Field(
        default=1,
        sa_column=Column(
            Integer,
            nullable=False,
            default=1,
            server_default=text("1"),
        ),
    )
    provenance: dict[str, Any] = Field(sa_column=Column(JSON, nullable=False))
    started_at: Optional[datetime] = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), nullable=True),
    )
    completed_at: Optional[datetime] = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), nullable=True),
    )
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
