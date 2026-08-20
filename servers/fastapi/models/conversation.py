"""Public contracts for the canonical conversation runtime.

The durable rows remain the proven workspace tables.  These models deliberately
use domain names (Conversation, Message, AgentRun, RunEvent, Revision, Asset) at
the API boundary without introducing a second persistence path.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Literal, Optional
import uuid

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from models.workspace_operation_kinds import WORKSPACE_COMMAND_KINDS
from services.presentation_profiles import (
    PresentationCapabilities,
    PresentationDriverId,
    PresentationProfile,
)


AgentRunStatus = Literal[
    "queued",
    "waiting_for_assets",
    "blocked",
    "running",
    "approval_required",
    "awaiting_approval",
    "cancelling",
    "succeeded",
    "failed",
    "cancelled",
    "dead_letter",
]


class ConversationCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    presentation_id: uuid.UUID


class PresentationCapability(BaseModel):
    model_config = ConfigDict(extra="forbid")

    driver_id: PresentationDriverId
    profile: PresentationProfile
    capabilities: PresentationCapabilities
    generation_mode: Literal["standard"]
    presentation_mode: Literal["template", "authored"]
    tool_profile: Literal["authored", "template-v2"]
    can_edit: bool
    supports_assets: bool = True


class PresentationRevision(BaseModel):
    """A run's optimistic revision boundary and publication result."""

    model_config = ConfigDict(extra="forbid")

    presentation_id: uuid.UUID
    operation_id: Optional[uuid.UUID] = None
    source_revision: int = Field(ge=0)
    result_revision: Optional[int] = Field(default=None, ge=0)
    published: bool = False

    @model_validator(mode="after")
    def validate_publication(self) -> "PresentationRevision":
        if (
            self.result_revision is not None
            and self.result_revision < self.source_revision
        ):
            raise ValueError("result_revision must not precede source_revision")
        expected = (
            self.result_revision is not None
            and self.result_revision > self.source_revision
        )
        if self.published != expected:
            raise ValueError("published must describe an advancing revision")
        return self


class ConversationResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1] = 1
    id: uuid.UUID
    presentation_id: uuid.UUID
    revision: int = Field(ge=0)
    capability: PresentationCapability
    created_at: datetime
    updated_at: datetime


class ConversationViewport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    width: int = Field(ge=1, le=100_000)
    height: int = Field(ge=1, le=100_000)
    zoom: float = Field(default=1.0, gt=0, le=20)
    visible_slide_ids: list[uuid.UUID] = Field(
        default_factory=list,
        max_length=500,
        json_schema_extra={"uniqueItems": True},
    )

    @field_validator("visible_slide_ids")
    @classmethod
    def unique_visible_slides(
        cls, value: list[uuid.UUID]
    ) -> list[uuid.UUID]:
        if len(value) != len(set(value)):
            raise ValueError("visible slide references must be unique")
        return value


class ConversationSlideReference(BaseModel):
    model_config = ConfigDict(extra="forbid")

    slide_id: Optional[uuid.UUID] = None
    index: Optional[int] = Field(default=None, ge=0)

    @model_validator(mode="after")
    def require_slide_identity(self) -> "ConversationSlideReference":
        if self.slide_id is None and self.index is None:
            raise ValueError("slide_id or index is required")
        return self


class ConversationMessageContext(BaseModel):
    model_config = ConfigDict(extra="forbid")

    viewport: Optional[ConversationViewport] = None
    current_slide: Optional[ConversationSlideReference] = None


class ConversationMessageCreateRequest(BaseModel):
    """One canonical message admission.

    Generation mode and tool/capability flags are intentionally absent.  They
    are resolved from the presentation row by the server.
    """

    model_config = ConfigDict(extra="forbid")

    content: str = Field(min_length=1, max_length=100_000)
    idempotency_key: str = Field(min_length=1, max_length=255)
    command_type: str = Field(
        default="presentation.edit",
        min_length=1,
        max_length=128,
        json_schema_extra={"enum": list(WORKSPACE_COMMAND_KINDS)},
    )
    expected_revision: Optional[int] = Field(default=None, ge=0)
    context: ConversationMessageContext = Field(
        default_factory=ConversationMessageContext
    )
    asset_ids: list[uuid.UUID] = Field(
        default_factory=list,
        max_length=100,
        json_schema_extra={"uniqueItems": True},
    )
    operation_context: dict[str, Any] = Field(default_factory=dict)

    @field_validator("content", "idempotency_key", "command_type")
    @classmethod
    def strip_required_text(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("value must not be blank")
        return normalized

    @field_validator("command_type")
    @classmethod
    def allowlisted_command_type(cls, value: str) -> str:
        if value not in WORKSPACE_COMMAND_KINDS:
            raise ValueError("unsupported conversation command_type")
        return value

    @field_validator("asset_ids")
    @classmethod
    def unique_assets(cls, value: list[uuid.UUID]) -> list[uuid.UUID]:
        if len(value) != len(set(value)):
            raise ValueError("asset references must be unique")
        return value


class ConversationAsset(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1] = 1
    id: uuid.UUID
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
    created_at: datetime
    updated_at: datetime


class ConversationMessageItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1] = 1
    item_type: Literal["message"] = "message"
    id: uuid.UUID
    position: int = Field(ge=1)
    role: Literal["user", "assistant", "system", "tool"]
    content: str
    created_at: Optional[datetime] = None
    assets: list[dict[str, Any]] = Field(default_factory=list)


class AgentRunActionAvailability(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: Literal["approve", "cancel", "retry", "undo", "download"]
    label: str
    variant: Literal["primary", "secondary", "danger"]
    enabled: bool
    reason: Optional[str] = None


class AgentRunItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1] = 1
    item_type: Literal["run"] = "run"
    id: uuid.UUID
    position: int = Field(ge=1)
    run_type: str
    status: AgentRunStatus
    progress: Optional[float] = None
    error: Optional[str] = None
    safe_error_message: Optional[str] = None
    retryable: Optional[bool] = None
    result: Any = None
    version: int = Field(ge=0)
    revision: PresentationRevision
    approval_expires_at: Optional[datetime] = None
    retry_of_run_id: Optional[uuid.UUID] = None
    actions: list[AgentRunActionAvailability] = Field(default_factory=list)
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


ConversationTimelineItem = Annotated[
    ConversationMessageItem | AgentRunItem,
    Field(discriminator="item_type"),
]


class ConversationTimelineResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1] = 1
    conversation_id: uuid.UUID
    presentation_id: uuid.UUID
    revision: int = Field(ge=0)
    capability: PresentationCapability
    items: list[ConversationTimelineItem] = Field(default_factory=list)
    next_before_position: Optional[int] = None
    event_high_watermark: Optional[str] = None
    has_more: bool = False


class ConversationMessageReceipt(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1] = 1
    conversation_id: uuid.UUID
    message_id: uuid.UUID
    run_id: uuid.UUID
    status: AgentRunStatus
    duplicate: bool = False
    revision: PresentationRevision


class AgentRunActionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: Literal["retry", "cancel", "approve", "undo"]
    idempotency_key: str = Field(min_length=1, max_length=255)
    expected_run_version: Optional[int] = Field(default=None, ge=0)
    expected_revision: Optional[int] = Field(default=None, ge=0)

    @field_validator("idempotency_key")
    @classmethod
    def strip_key(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("idempotency_key must not be blank")
        return normalized


class AgentRunActionReceipt(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1] = 1
    conversation_id: uuid.UUID
    receipt_id: uuid.UUID
    run_id: uuid.UUID
    status: AgentRunStatus
    action: Literal["retry", "cancel", "approve", "undo"]
    duplicate: bool = False
    revision: PresentationRevision
