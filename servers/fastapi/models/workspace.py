"""Public request/response contracts for the private slides workspace.

The SQL rows intentionally stay private.  These small Pydantic models are the
allow-list at the API boundary and keep internal storage keys, leases, fencing
tokens, raw provider failures, and owner identifiers out of responses.
"""

from __future__ import annotations

from datetime import datetime
import re
from typing import Any, Literal, Optional
import uuid

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    PrivateAttr,
    field_validator,
    model_validator,
)

from models.sql.workspace import WORKSPACE_ASSET_STATES


_SENSITIVE_WORKSPACE_KEYS = {
    "access_key",
    "access_token",
    "api_key",
    "auth_token",
    "authorization",
    "bearer_token",
    "cookie",
    "cookies",
    "credential",
    "credentials",
    "password",
    "passwd",
    "private_key",
    "refresh_token",
    "secret",
    "secret_key",
    "session_cookie",
    "token",
}


def canonical_workspace_key(value: object) -> str:
    """Normalize separators and CamelCase before applying security policies."""

    raw = str(value).strip()
    # Both acronym boundaries (``APIKey``) and ordinary camel case
    # (``accessToken``) must normalize to the same tokens as snake/kebab case.
    snake = re.sub(r"(.)([A-Z][a-z]+)", r"\1_\2", raw)
    snake = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", snake)
    return re.sub(r"[^a-zA-Z0-9]+", "_", snake).strip("_").lower()


def is_sensitive_workspace_key(value: object) -> bool:
    """Return whether a request/result key may contain authentication material."""

    normalized = canonical_workspace_key(value)
    if normalized in _SENSITIVE_WORKSPACE_KEYS:
        return True
    return any(
        normalized.endswith(f"_{suffix}")
        for suffix in _SENSITIVE_WORKSPACE_KEYS
    )


def _reject_sensitive_workspace_keys(value: Any) -> Any:
    if isinstance(value, dict):
        for key, child in value.items():
            if is_sensitive_workspace_key(key):
                raise ValueError("workspace metadata must not contain credentials")
            _reject_sensitive_workspace_keys(child)
    elif isinstance(value, (list, tuple)):
        for child in value:
            _reject_sensitive_workspace_keys(child)
    return value


class WorkspaceAttachmentInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    asset_id: Optional[uuid.UUID] = None
    file_name: str = Field(min_length=1, max_length=512)
    media_type: Optional[str] = Field(default=None, max_length=255)
    size_bytes: int = Field(default=0, ge=0)
    sha256: Optional[str] = Field(default=None, min_length=64, max_length=64)
    status: str = "ready"

    @field_validator("file_name")
    @classmethod
    def validate_file_name(cls, value: str) -> str:
        # The service applies basename normalization as a second boundary.
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("file_name is required")
        return cleaned

    @field_validator("status")
    @classmethod
    def validate_status(cls, value: str) -> str:
        if value not in WORKSPACE_ASSET_STATES:
            raise ValueError("invalid workspace asset status")
        return value

    @field_validator("sha256")
    @classmethod
    def validate_sha256(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        normalized = value.lower()
        if any(character not in "0123456789abcdef" for character in normalized):
            raise ValueError("sha256 must be hexadecimal")
        return normalized


class WorkspaceBootstrapRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # Metadata-only attachments predate durable workspace assets.  Keep the
    # compatibility switch out of the serialized/public request contract so a
    # caller cannot opt itself into the trusted path over HTTP.
    _allow_internal_legacy_metadata: bool = PrivateAttr(default=False)

    idempotency_key: str = Field(min_length=1, max_length=255)
    prompt: str = Field(min_length=1, max_length=100_000)
    generation_settings: dict[str, Any] = Field(default_factory=dict)
    asset_ids: list[uuid.UUID] = Field(default_factory=list, max_length=100)
    attachments: list[WorkspaceAttachmentInput] = Field(
        default_factory=list,
        max_length=100,
    )
    operation_type: str = Field(
        default="presentation.generate",
        min_length=1,
        max_length=128,
    )
    expected_revision: Optional[int] = Field(default=None, ge=0)

    @field_validator("idempotency_key", "prompt", "operation_type")
    @classmethod
    def strip_required_text(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("value must not be blank")
        return normalized

    @field_validator("generation_settings")
    @classmethod
    def reject_credentials_in_generation_settings(
        cls, value: dict[str, Any]
    ) -> dict[str, Any]:
        return _reject_sensitive_workspace_keys(value)

    @model_validator(mode="after")
    def validate_asset_references(self) -> "WorkspaceBootstrapRequest":
        referenced = list(self.asset_ids) + [
            attachment.asset_id
            for attachment in self.attachments
            if attachment.asset_id is not None
        ]
        if len(self.asset_ids) + len(self.attachments) > 100:
            raise ValueError("at most 100 workspace assets may be attached")
        if len(referenced) != len(set(referenced)):
            raise ValueError("workspace asset references must be unique")
        return self

    @classmethod
    def from_internal_legacy_metadata(
        cls, **values: Any
    ) -> "WorkspaceBootstrapRequest":
        """Build a trusted in-process request for pre-asset metadata fixtures.

        New product/API paths must upload an asset first and use its server
        issued id.  This constructor is intentionally not represented in the
        JSON schema or accepted as a request field.
        """

        request = cls(**values)
        request._allow_internal_legacy_metadata = True
        return request


class WorkspaceAssetTransitionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    state: Literal[
        "uploaded",
        "scanning",
        "processing",
        "ready",
        "failed",
        "quarantined",
        "expired",
        "deleted",
    ]
    expected_version: int = Field(ge=0)
    failure_code: Optional[str] = Field(default=None, min_length=1, max_length=128)

    @field_validator("failure_code")
    @classmethod
    def normalize_failure_code(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        normalized = value.strip().lower().replace(" ", "_")
        if not normalized or any(
            character not in "abcdefghijklmnopqrstuvwxyz0123456789_.-"
            for character in normalized
        ):
            raise ValueError("failure_code must be a stable machine-readable code")
        return normalized


class WorkspaceAssetResponse(BaseModel):
    """Allow-listed asset metadata; storage and owner fields stay private."""

    model_config = ConfigDict(extra="forbid")

    schema_version: int = 1
    asset_id: uuid.UUID
    file_name: str
    media_type: Optional[str] = None
    size_bytes: int = Field(ge=0)
    sha256: Optional[str] = None
    status: Literal[
        "uploaded",
        "scanning",
        "processing",
        "ready",
        "failed",
        "quarantined",
        "expired",
        "deleted",
    ]
    version: int = Field(ge=0)
    failure_code: Optional[str] = None
    expires_at: Optional[datetime] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


class WorkspaceCommandRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    command: str = Field(min_length=1, max_length=100_000)
    command_type: Literal[
        "presentation.edit",
        "slide.edit",
        "slide.regenerate",
        "slide.delete",
        "version.restore",
        "quality.review",
        "export.pptx",
        "export.pdf",
    ] = "presentation.edit"
    idempotency_key: str = Field(min_length=1, max_length=255)
    expected_revision: Optional[int] = Field(default=None, ge=0)
    context: dict[str, Any] = Field(default_factory=dict)

    @field_validator("command", "idempotency_key")
    @classmethod
    def strip_command_text(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("value must not be blank")
        return normalized

    @field_validator("context")
    @classmethod
    def reject_credentials_in_context(cls, value: dict[str, Any]) -> dict[str, Any]:
        return _reject_sensitive_workspace_keys(value)


class WorkspaceOperationActionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: Literal["retry", "cancel", "approve"]
    idempotency_key: str = Field(min_length=1, max_length=255)
    action_id: Optional[str] = Field(default=None, min_length=1, max_length=128)
    expected_operation_version: Optional[int] = Field(default=None, ge=0)
    # Kept for the shipped frontend v1 wire shape.  It is a presentation
    # revision precondition and never substitutes for operation version.
    expected_revision: Optional[int] = Field(default=None, ge=0)

    @field_validator("idempotency_key")
    @classmethod
    def strip_idempotency_key(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("value must not be blank")
        return normalized


class WorkspaceCapabilitiesResponse(BaseModel):
    schema_version: int = 1
    supported: bool
    enabled: bool
    timeline: bool
    commands: bool
    events: bool
    runtime_profile: Optional[str] = None
    reason: Optional[str] = None


class WorkspaceTimelineResponse(BaseModel):
    schema_version: int = 1
    conversation_id: Optional[uuid.UUID] = None
    items: list[dict[str, Any]] = Field(default_factory=list)
    next_before_position: Optional[int] = None
    event_high_watermark: Optional[str] = None
    has_more: bool = False
    revision: int = 0


class WorkspaceOperationEventResponse(BaseModel):
    """A durable event projection with the post-transition CAS version."""

    model_config = ConfigDict(extra="forbid")

    id: str
    operation_id: uuid.UUID
    operation_version: int = Field(ge=0)
    sequence: int = Field(ge=1)
    cursor: Optional[str] = None
    type: str
    status: Optional[str] = None
    message: Optional[str] = None
    progress: Optional[float] = None
    error: Any = None
    retryable: Optional[bool] = None
    result: Any = None
    created_at: Optional[datetime] = None


class WorkspaceEventPageResponse(BaseModel):
    schema_version: int = 1
    events: list[WorkspaceOperationEventResponse] = Field(default_factory=list)
    event_high_watermark: Optional[str] = None


class WorkspaceBootstrapResponse(BaseModel):
    schema_version: int = 1
    conversation_id: uuid.UUID
    first_user_message_id: uuid.UUID
    operation_id: uuid.UUID
    operation_state: str
    duplicate: bool = False
    timeline: WorkspaceTimelineResponse


class WorkspaceActionResponse(BaseModel):
    schema_version: int = 1
    receipt_id: uuid.UUID
    operation_id: uuid.UUID
    status: str
    duplicate: bool = False
    timeline: WorkspaceTimelineResponse
