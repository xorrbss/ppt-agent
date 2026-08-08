"""Public request/response contracts for the private slides workspace.

The SQL rows intentionally stay private.  These small Pydantic models are the
allow-list at the API boundary and keep internal storage keys, leases, fencing
tokens, raw provider failures, and owner identifiers out of responses.
"""

from __future__ import annotations

from datetime import datetime
import re
from typing import Annotated, Any, Literal, Optional
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
from models.workspace_operation_kinds import (
    WORKSPACE_BOOTSTRAP_KINDS,
    WORKSPACE_COMMAND_KINDS,
)


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
        json_schema_extra={"enum": list(WORKSPACE_BOOTSTRAP_KINDS)},
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

    schema_version: Literal[1] = 1
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
    # The service performs the semantic allow-list check so unsupported kinds
    # receive the stable public error code instead of a generic Pydantic error.
    command_type: str = Field(
        default="presentation.edit",
        min_length=1,
        max_length=128,
        json_schema_extra={"enum": list(WORKSPACE_COMMAND_KINDS)},
    )
    idempotency_key: str = Field(min_length=1, max_length=255)
    expected_revision: Optional[int] = Field(default=None, ge=0)
    asset_ids: list[uuid.UUID] = Field(
        default_factory=list,
        max_length=100,
        json_schema_extra={"uniqueItems": True},
    )
    context: dict[str, Any] = Field(default_factory=dict)

    @field_validator("command", "command_type", "idempotency_key")
    @classmethod
    def strip_command_text(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("value must not be blank")
        return normalized

    @field_validator("asset_ids")
    @classmethod
    def validate_unique_asset_ids(
        cls, value: list[uuid.UUID]
    ) -> list[uuid.UUID]:
        if len(value) != len(set(value)):
            raise ValueError("workspace asset references must be unique")
        return value

    @field_validator("context")
    @classmethod
    def reject_credentials_in_context(cls, value: dict[str, Any]) -> dict[str, Any]:
        return _reject_sensitive_workspace_keys(value)


class WorkspaceTemplateApplicationRequest(BaseModel):
    """Explicit confirmation contract for an AI-authored template change."""

    model_config = ConfigDict(extra="forbid")

    source_revision: int = Field(ge=0)
    prompt: str = Field(min_length=1, max_length=100_000)
    authored_style: str = Field(min_length=1, max_length=255)
    vision_qa: bool = False

    @field_validator("prompt", "authored_style")
    @classmethod
    def strip_template_application_text(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("value must not be blank")
        return normalized


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
    """Read and write capabilities are intentionally reported independently."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1] = 1
    # Compatibility names consumed by the shipped client.
    supported: bool
    enabled: bool
    timeline: bool
    commands: bool
    events: bool
    # Explicit v1 names used by new clients and rollout tooling.
    reads_supported: bool
    writes_supported: bool
    commands_enabled: bool
    runtime_profile: Optional[str] = None
    reason: Optional[str] = None
    cohort_eligible: bool = True
    cohort_bucket: Optional[int] = Field(default=None, ge=0, le=9_999)
    kill_switch_active: bool = False
    shadow_mode: bool = False


class WorkspaceSLOResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1] = 1
    healthy: bool
    silent_loss_total: int = Field(ge=0)
    shadow_parity_ratio: float = Field(ge=0, le=1)
    error_ratio: float = Field(ge=0, le=1)
    dead_letter_total: int = Field(ge=0)
    dead_letter_ratio: float = Field(ge=0, le=1)
    lease_lost_total: int = Field(ge=0)
    lease_lost_ratio: float = Field(ge=0, le=1)
    revision_conflict_total: int = Field(ge=0)
    revision_conflict_ratio: float = Field(ge=0, le=1)
    cleanup_dead_letter_total: int = Field(ge=0)
    cleanup_dead_letter_ratio: float = Field(ge=0, le=1)
    outbox_dead_letter_total: int = Field(ge=0)
    outbox_dead_letter_ratio: float = Field(ge=0, le=1)
    outbox_lease_lost_total: int = Field(ge=0)
    outbox_lease_lost_ratio: float = Field(ge=0, le=1)
    event_lag_max_ms: int = Field(ge=0)
    violations: list[str] = Field(default_factory=list)


class WorkspaceErrorDetailResponse(BaseModel):
    """Stable workspace-only error fields documented in OpenAPI."""

    model_config = ConfigDict(extra="forbid")

    status_code: int
    detail: str
    code: str
    message: str
    request_id: str
    safe_message: str
    retryable: bool


class WorkspaceErrorEnvelopeResponse(BaseModel):
    """Compatibility envelope plus the typed public workspace error."""

    model_config = ConfigDict(extra="forbid")

    detail: str
    error: WorkspaceErrorDetailResponse
    request_id: str


class WorkspaceTimelineAttachmentFailureResponse(BaseModel):
    """Safe, registry-backed attachment failure details for the timeline."""

    model_config = ConfigDict(extra="forbid")

    failure_code: str = Field(min_length=1, max_length=128)
    safe_message: str = Field(min_length=1, max_length=512)
    retryable: bool


class WorkspaceTimelineAttachmentResponse(BaseModel):
    """Immutable attachment snapshot plus optional live, safe capabilities."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1] = 1
    id: uuid.UUID
    name: str
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
    current_state: Optional[
        Literal[
            "uploaded",
            "scanning",
            "processing",
            "ready",
            "failed",
            "quarantined",
            "expired",
            "deleted",
        ]
    ] = None
    mime_type: Optional[str] = None
    size_bytes: int = Field(ge=0)
    sha256: Optional[str] = None
    # Retained for v1 compatibility, but it may contain only the same stable
    # failure code represented by ``safe_failure`` (never a message or detail).
    error: Optional[str] = None
    safe_failure: Optional[WorkspaceTimelineAttachmentFailureResponse] = None
    download_url: Optional[str] = Field(default=None, max_length=1024)
    download_action: Optional[Literal["download"]] = None
    preview_url: Optional[str] = Field(default=None, max_length=1024)
    preview_action: Optional[Literal["preview"]] = None
    unavailable_reason: Optional[str] = Field(default=None, max_length=128)

    @field_validator("download_url")
    @classmethod
    def validate_download_url(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        if re.fullmatch(
            r"/api/v1/ppt/workspace/assets/"
            r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
            r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}/download",
            value,
        ) is None:
            raise ValueError("download_url must be a workspace asset route")
        return value

    @field_validator("preview_url")
    @classmethod
    def validate_preview_url(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        if re.fullmatch(
            r"/api/v1/ppt/workspace/assets/"
            r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
            r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}/preview",
            value,
        ) is None:
            raise ValueError("preview_url must be a workspace asset route")
        return value

    @model_validator(mode="after")
    def validate_capability_pairs(self) -> "WorkspaceTimelineAttachmentResponse":
        if (self.download_url is None) != (self.download_action is None):
            raise ValueError("download_url and download_action must be paired")
        if (self.preview_url is None) != (self.preview_action is None):
            raise ValueError("preview_url and preview_action must be paired")
        if self.safe_failure is not None:
            if self.error not in {None, self.safe_failure.failure_code}:
                raise ValueError("error must match safe_failure.failure_code")
        return self


class WorkspaceOperationEventResponse(BaseModel):
    """A durable event projection with the post-transition CAS version."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1] = 1
    id: str
    operation_id: uuid.UUID
    operation_version: int = Field(ge=0)
    sequence: int = Field(ge=1)
    cursor: Optional[str] = None
    operation_anchor_position: Optional[int] = Field(default=None, ge=1)
    type: str
    status: Optional[str] = None
    message: Optional[str] = None
    progress: Optional[float] = None
    error: Optional[str] = None
    safe_message: Optional[str] = Field(default=None, min_length=1, max_length=512)
    retryable: Optional[bool] = None
    result: Any = None
    created_at: Optional[datetime] = None


class WorkspaceMessageTimelineItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1] = 1
    anchor_type: Literal["message"]
    id: uuid.UUID
    position: int = Field(ge=1)
    role: str
    content: str
    created_at: Optional[datetime] = None
    attachments: list[WorkspaceTimelineAttachmentResponse] = Field(
        default_factory=list
    )
    generation_settings: Optional[dict[str, Any]] = None


class WorkspaceOperationActorResponse(BaseModel):
    """Anonymous public actor projection; owner identifiers never leave storage."""

    model_config = ConfigDict(extra="forbid")

    type: Literal["user"] = "user"
    label: str


class WorkspaceOperationTargetResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    presentation_id: Optional[uuid.UUID] = None
    summary: str
    context: dict[str, Any] = Field(default_factory=dict)


class WorkspaceAllowedActionResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: Literal[
        "approve",
        "reject",
        "cancel",
        "retry",
        "undo",
        "download",
        "focus",
    ]
    label: str
    variant: Literal["primary", "secondary", "danger"]
    enabled: bool
    reason: Optional[str] = None


class WorkspaceOperationTimelineItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1] = 1
    anchor_type: Literal["operation"]
    id: uuid.UUID
    operation_id: uuid.UUID
    position: int = Field(ge=1)
    operation_type: str
    title: str
    status: str
    progress: Optional[float] = None
    error: Optional[str] = None
    error_message: Optional[str] = None
    retryable: Optional[bool] = None
    result: Any = None
    # ``version`` is retained for v1 clients; ``operation_version`` makes the
    # optimistic-concurrency precondition explicit for action controls.
    version: int = Field(ge=0)
    operation_version: int = Field(ge=0)
    expected_revision: Optional[int] = Field(default=None, ge=0)
    result_revision: Optional[int] = Field(default=None, ge=0)
    approval_expires_at: Optional[datetime] = None
    retry_of_operation_id: Optional[uuid.UUID] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    actor: WorkspaceOperationActorResponse
    target: WorkspaceOperationTargetResponse
    activity_summary: str
    allowed_actions: list[WorkspaceAllowedActionResponse] = Field(
        default_factory=list
    )
    events: list[WorkspaceOperationEventResponse] = Field(default_factory=list)


WorkspaceTimelineItem = Annotated[
    WorkspaceMessageTimelineItem | WorkspaceOperationTimelineItem,
    Field(discriminator="anchor_type"),
]


class WorkspaceTimelineResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1] = 1
    conversation_id: Optional[uuid.UUID] = None
    items: list[WorkspaceTimelineItem] = Field(default_factory=list)
    next_before_position: Optional[int] = None
    event_high_watermark: Optional[str] = None
    has_more: bool = False
    revision: int = Field(default=0, ge=0)


class WorkspaceEventPageResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1] = 1
    events: list[WorkspaceOperationEventResponse] = Field(default_factory=list)
    event_high_watermark: Optional[str] = None


class WorkspaceBootstrapResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1] = 1
    receipt_id: uuid.UUID
    conversation_id: uuid.UUID
    first_user_message_id: uuid.UUID
    operation_id: uuid.UUID
    status: str
    operation_state: str
    duplicate: bool = False
    timeline: WorkspaceTimelineResponse


class WorkspaceTemplateApplicationResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1] = 1
    source_presentation_id: uuid.UUID
    source_revision: int = Field(ge=0)
    target_presentation_id: uuid.UUID
    conversation_id: uuid.UUID
    first_user_message_id: uuid.UUID
    operation_id: uuid.UUID
    status: str
    operation_state: str
    duplicate: bool = False
    timeline: WorkspaceTimelineResponse


class WorkspaceActionResponse(BaseModel):
    """Versioned receipt plus the legacy flattened timeline projection."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1] = 1
    receipt_id: uuid.UUID
    operation_id: uuid.UUID
    status: str
    duplicate: bool = False
    timeline: WorkspaceTimelineResponse
    # Compatibility fields: command/action endpoints historically returned a
    # bare timeline.  Keeping these fields avoids a flag-day client rollout.
    conversation_id: Optional[uuid.UUID] = None
    items: list[WorkspaceTimelineItem] = Field(default_factory=list)
    next_before_position: Optional[int] = None
    event_high_watermark: Optional[str] = None
    has_more: bool = False
    revision: int = Field(default=0, ge=0)
