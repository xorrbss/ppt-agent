from __future__ import annotations

from datetime import datetime
from typing import Optional
import uuid

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
    Text,
    UniqueConstraint,
    Uuid,
    func,
    text,
)
from sqlmodel import Field, SQLModel

from utils.datetime_utils import get_current_utc_datetime
from utils.owner_context import get_current_owner_id


WORKSPACE_ASSET_STATES = (
    "uploaded",
    "scanning",
    "processing",
    "ready",
    "failed",
    "quarantined",
    "expired",
    "deleted",
)
WORKSPACE_OPERATION_STATES = (
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
)
WORKSPACE_CLEANUP_STATES = (
    "pending",
    "claimed",
    "retry_wait",
    "succeeded",
    "dead_letter",
)

_EVENT_CURSOR_TYPE = BigInteger().with_variant(Integer, "sqlite")


def _state_check(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN ({', '.join(repr(value) for value in values)})"


def _owner_column(table_name: str, *, nullable: bool = True) -> Column:
    return Column(
        Uuid,
        ForeignKey(
            "user.id",
            name=f"fk_{table_name}_owner_id",
            ondelete="CASCADE",
        ),
        nullable=nullable,
        default=get_current_owner_id,
    )


def get_workspace_owner_scope() -> str:
    owner_id = get_current_owner_id()
    return str(owner_id) if owner_id is not None else "anonymous"


class WorkspaceConversation(SQLModel, table=True):
    __tablename__ = "workspace_conversations"
    __table_args__ = (
        UniqueConstraint("owner_scope", "presentation_id", name="uq_workspace_conversations_scope_presentation"),
        CheckConstraint("next_position >= 1", name="ck_workspace_conversations_next_position"),
        CheckConstraint("version >= 0", name="ck_workspace_conversations_version"),
        CheckConstraint(
            "event_retention_floor_cursor >= 0",
            name="ck_workspace_conversations_event_retention_floor_cursor",
        ),
        Index(
            "ix_workspace_conversations_owner_presentation_updated",
            "owner_id",
            "presentation_id",
            "updated_at",
        ),
    )

    id: uuid.UUID = Field(primary_key=True, default_factory=uuid.uuid4)
    owner_id: Optional[uuid.UUID] = Field(
        default_factory=get_current_owner_id,
        sa_column=_owner_column("workspace_conversations"),
    )
    owner_scope: str = Field(default_factory=get_workspace_owner_scope, sa_column=Column(String(36), nullable=False, default=get_workspace_owner_scope, server_default="anonymous"))
    presentation_id: uuid.UUID = Field(
        sa_column=Column(
            Uuid,
            ForeignKey(
                "presentations.id",
                name="fk_workspace_conversations_presentation_id",
                ondelete="CASCADE",
            ),
            nullable=False,
        )
    )
    next_position: int = Field(
        default=1,
        sa_column=Column(Integer, nullable=False, default=1, server_default=text("1")),
    )
    version: int = Field(
        default=0,
        sa_column=Column(Integer, nullable=False, default=0, server_default=text("0")),
    )
    event_retention_floor_cursor: int = Field(
        default=0,
        sa_column=Column(
            _EVENT_CURSOR_TYPE,
            nullable=False,
            default=0,
            server_default=text("0"),
        ),
    )
    created_at: datetime = Field(
        default_factory=get_current_utc_datetime,
        sa_column=Column(DateTime(timezone=True), nullable=False, default=get_current_utc_datetime, server_default=func.now()),
    )
    updated_at: datetime = Field(
        default_factory=get_current_utc_datetime,
        sa_column=Column(DateTime(timezone=True), nullable=False, default=get_current_utc_datetime, onupdate=get_current_utc_datetime, server_default=func.now()),
    )


class WorkspaceMessage(SQLModel, table=True):
    __tablename__ = "workspace_messages"
    __table_args__ = (
        UniqueConstraint("conversation_id", "position", name="uq_workspace_messages_conversation_position"),
        CheckConstraint("position >= 1", name="ck_workspace_messages_position"),
        CheckConstraint("role IN ('user', 'assistant', 'system', 'tool')", name="ck_workspace_messages_role"),
        Index("ix_workspace_messages_owner_created", "owner_id", "created_at"),
    )

    id: uuid.UUID = Field(primary_key=True, default_factory=uuid.uuid4)
    owner_id: Optional[uuid.UUID] = Field(
        default_factory=get_current_owner_id,
        sa_column=_owner_column("workspace_messages"),
    )
    conversation_id: uuid.UUID = Field(
        sa_column=Column(Uuid, ForeignKey("workspace_conversations.id", name="fk_workspace_messages_conversation_id", ondelete="CASCADE"), nullable=False, index=True)
    )
    position: int = Field(sa_column=Column(Integer, nullable=False))
    role: str = Field(sa_column=Column(String(16), nullable=False))
    content: str = Field(default="", sa_column=Column(Text, nullable=False, default="", server_default=""))
    schema_version: int = Field(default=1, sa_column=Column(Integer, nullable=False, default=1, server_default=text("1")))
    message_metadata: Optional[dict] = Field(default=None, sa_column=Column("metadata", JSON, nullable=True))
    created_at: datetime = Field(default_factory=get_current_utc_datetime, sa_column=Column(DateTime(timezone=True), nullable=False, default=get_current_utc_datetime, server_default=func.now()))


class WorkspaceAsset(SQLModel, table=True):
    __tablename__ = "workspace_assets"
    __table_args__ = (
        CheckConstraint(_state_check("state", WORKSPACE_ASSET_STATES), name="ck_workspace_assets_state"),
        CheckConstraint("size_bytes >= 0", name="ck_workspace_assets_size"),
        CheckConstraint("version >= 0", name="ck_workspace_assets_version"),
        UniqueConstraint("storage_key", name="uq_workspace_assets_storage_key"),
        Index("ix_workspace_assets_owner_state_updated", "owner_id", "state", "updated_at"),
        Index("ix_workspace_assets_expiry", "state", "expires_at"),
    )

    id: uuid.UUID = Field(primary_key=True, default_factory=uuid.uuid4)
    owner_id: Optional[uuid.UUID] = Field(
        default_factory=get_current_owner_id,
        sa_column=_owner_column("workspace_assets"),
    )
    conversation_id: Optional[uuid.UUID] = Field(default=None, sa_column=Column(Uuid, ForeignKey("workspace_conversations.id", name="fk_workspace_assets_conversation_id", ondelete="CASCADE"), nullable=True))
    state: str = Field(default="uploaded", sa_column=Column(String(24), nullable=False, default="uploaded", server_default="uploaded"))
    original_name: str = Field(sa_column=Column(String(512), nullable=False))
    media_type: Optional[str] = Field(default=None, sa_column=Column(String(255), nullable=True))
    size_bytes: int = Field(default=0, sa_column=Column(BigInteger, nullable=False, default=0, server_default=text("0")))
    storage_key: str = Field(sa_column=Column(String(1024), nullable=False))
    sha256: Optional[str] = Field(default=None, sa_column=Column(String(64), nullable=True))
    failure_code: Optional[str] = Field(default=None, sa_column=Column(String(128), nullable=True))
    failure_detail: Optional[dict] = Field(default=None, sa_column=Column(JSON, nullable=True))
    version: int = Field(default=0, sa_column=Column(Integer, nullable=False, default=0, server_default=text("0")))
    expires_at: Optional[datetime] = Field(default=None, sa_column=Column(DateTime(timezone=True), nullable=True))
    deleted_at: Optional[datetime] = Field(default=None, sa_column=Column(DateTime(timezone=True), nullable=True))
    created_at: datetime = Field(default_factory=get_current_utc_datetime, sa_column=Column(DateTime(timezone=True), nullable=False, default=get_current_utc_datetime, server_default=func.now()))
    updated_at: datetime = Field(default_factory=get_current_utc_datetime, sa_column=Column(DateTime(timezone=True), nullable=False, default=get_current_utc_datetime, onupdate=get_current_utc_datetime, server_default=func.now()))


class WorkspaceMessageAttachment(SQLModel, table=True):
    __tablename__ = "workspace_message_attachments"
    __table_args__ = (
        UniqueConstraint("message_id", "asset_id_snapshot", name="uq_workspace_message_attachments_asset"),
        UniqueConstraint("message_id", "ordinal", name="uq_workspace_message_attachments_ordinal"),
        CheckConstraint("ordinal >= 0", name="ck_workspace_message_attachments_ordinal"),
        CheckConstraint(_state_check("asset_state", WORKSPACE_ASSET_STATES), name="ck_workspace_message_attachments_asset_state"),
        Index("ix_workspace_message_attachments_asset", "asset_id"),
    )

    id: uuid.UUID = Field(primary_key=True, default_factory=uuid.uuid4)
    owner_id: Optional[uuid.UUID] = Field(
        default_factory=get_current_owner_id,
        sa_column=_owner_column("workspace_message_attachments"),
    )
    message_id: uuid.UUID = Field(sa_column=Column(Uuid, ForeignKey("workspace_messages.id", name="fk_workspace_message_attachments_message_id", ondelete="CASCADE"), nullable=False))
    # ``asset_id`` is only a live pointer.  The immutable snapshot columns are
    # the timeline source of truth and survive asset expiry/deletion.
    asset_id: Optional[uuid.UUID] = Field(
        default=None,
        sa_column=Column(
            Uuid,
            ForeignKey(
                "workspace_assets.id",
                name="fk_workspace_message_attachments_asset_id",
                ondelete="SET NULL",
            ),
            nullable=True,
        ),
    )
    asset_id_snapshot: uuid.UUID = Field(sa_column=Column(Uuid, nullable=False))
    ordinal: int = Field(default=0, sa_column=Column(Integer, nullable=False, default=0, server_default=text("0")))
    display_name: str = Field(sa_column=Column(String(512), nullable=False))
    media_type: Optional[str] = Field(default=None, sa_column=Column(String(255), nullable=True))
    size_bytes: int = Field(default=0, sa_column=Column(BigInteger, nullable=False, default=0, server_default=text("0")))
    asset_state: str = Field(sa_column=Column(String(24), nullable=False))
    asset_sha256: Optional[str] = Field(
        default=None,
        sa_column=Column(String(64), nullable=True),
    )
    asset_failure_code: Optional[str] = Field(
        default=None,
        sa_column=Column(String(128), nullable=True),
    )
    asset_failure_detail: Optional[dict] = Field(
        default=None,
        sa_column=Column(JSON, nullable=True),
    )
    created_at: datetime = Field(default_factory=get_current_utc_datetime, sa_column=Column(DateTime(timezone=True), nullable=False, default=get_current_utc_datetime, server_default=func.now()))


class WorkspaceOperation(SQLModel, table=True):
    __tablename__ = "workspace_operations"
    __table_args__ = (
        UniqueConstraint("conversation_id", "anchor_position", name="uq_workspace_operations_conversation_position"),
        UniqueConstraint(
            "owner_scope",
            "presentation_id",
            "kind",
            "idempotency_key_hash",
            name="uq_workspace_operations_command_idempotency",
        ),
        UniqueConstraint("retry_of_operation_id", name="uq_workspace_operations_retry_of"),
        CheckConstraint(_state_check("state", WORKSPACE_OPERATION_STATES), name="ck_workspace_operations_state"),
        CheckConstraint("anchor_position >= 1", name="ck_workspace_operations_anchor_position"),
        CheckConstraint("attempt_number >= 0", name="ck_workspace_operations_attempt_number"),
        CheckConstraint("fencing_token >= 0", name="ck_workspace_operations_fencing_token"),
        CheckConstraint("retry_count >= 0", name="ck_workspace_operations_retry_count"),
        CheckConstraint("max_attempts >= 1", name="ck_workspace_operations_max_attempts"),
        CheckConstraint("version >= 0", name="ck_workspace_operations_version"),
        CheckConstraint("next_event_sequence >= 1", name="ck_workspace_operations_next_event_sequence"),
        Index("ix_workspace_operations_dispatch", "state", "next_attempt_at", "lease_expires_at"),
        Index("ix_workspace_operations_owner_created", "owner_id", "created_at"),
    )

    id: uuid.UUID = Field(primary_key=True, default_factory=uuid.uuid4)
    owner_id: Optional[uuid.UUID] = Field(
        default_factory=get_current_owner_id,
        sa_column=_owner_column("workspace_operations"),
    )
    owner_scope: str = Field(default_factory=get_workspace_owner_scope, sa_column=Column(String(36), nullable=False, default=get_workspace_owner_scope, server_default="anonymous"))
    conversation_id: uuid.UUID = Field(sa_column=Column(Uuid, ForeignKey("workspace_conversations.id", name="fk_workspace_operations_conversation_id", ondelete="CASCADE"), nullable=False, index=True))
    presentation_id: uuid.UUID = Field(sa_column=Column(Uuid, ForeignKey("presentations.id", name="fk_workspace_operations_presentation_id", ondelete="CASCADE"), nullable=False, index=True))
    retry_of_operation_id: Optional[uuid.UUID] = Field(
        default=None,
        sa_column=Column(
            Uuid,
            ForeignKey(
                "workspace_operations.id",
                name="fk_workspace_operations_retry_of_operation_id",
                ondelete="SET NULL",
            ),
            nullable=True,
        ),
    )
    anchor_position: int = Field(sa_column=Column(Integer, nullable=False))
    kind: str = Field(sa_column=Column(String(128), nullable=False))
    state: str = Field(default="queued", sa_column=Column(String(24), nullable=False, default="queued", server_default="queued"))
    idempotency_key_hash: str = Field(sa_column=Column(String(64), nullable=False))
    request_hash: str = Field(sa_column=Column(String(64), nullable=False))
    request_payload: dict = Field(default_factory=dict, sa_column=Column(JSON, nullable=False))
    expected_revision: Optional[int] = Field(default=None, sa_column=Column(BigInteger, nullable=True))
    result_revision: Optional[int] = Field(default=None, sa_column=Column(BigInteger, nullable=True))
    approval_expires_at: Optional[datetime] = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), nullable=True),
    )
    attempt_number: int = Field(default=0, sa_column=Column(Integer, nullable=False, default=0, server_default=text("0")))
    attempt_token: Optional[str] = Field(default=None, sa_column=Column(String(128), nullable=True))
    fencing_token: int = Field(default=0, sa_column=Column(BigInteger, nullable=False, default=0, server_default=text("0")))
    lease_expires_at: Optional[datetime] = Field(default=None, sa_column=Column(DateTime(timezone=True), nullable=True))
    heartbeat_at: Optional[datetime] = Field(default=None, sa_column=Column(DateTime(timezone=True), nullable=True))
    next_attempt_at: Optional[datetime] = Field(default=None, sa_column=Column(DateTime(timezone=True), nullable=True))
    retry_count: int = Field(default=0, sa_column=Column(Integer, nullable=False, default=0, server_default=text("0")))
    max_attempts: int = Field(default=3, sa_column=Column(Integer, nullable=False, default=3, server_default=text("3")))
    backoff_seconds: int = Field(default=0, sa_column=Column(Integer, nullable=False, default=0, server_default=text("0")))
    cancel_requested_at: Optional[datetime] = Field(default=None, sa_column=Column(DateTime(timezone=True), nullable=True))
    cancelled_at: Optional[datetime] = Field(default=None, sa_column=Column(DateTime(timezone=True), nullable=True))
    progress: Optional[dict] = Field(default=None, sa_column=Column(JSON, nullable=True))
    result: Optional[dict] = Field(default=None, sa_column=Column(JSON, nullable=True))
    error_code: Optional[str] = Field(default=None, sa_column=Column(String(128), nullable=True))
    error_detail: Optional[dict] = Field(default=None, sa_column=Column(JSON, nullable=True))
    version: int = Field(default=0, sa_column=Column(Integer, nullable=False, default=0, server_default=text("0")))
    next_event_sequence: int = Field(default=1, sa_column=Column(Integer, nullable=False, default=1, server_default=text("1")))
    started_at: Optional[datetime] = Field(default=None, sa_column=Column(DateTime(timezone=True), nullable=True))
    completed_at: Optional[datetime] = Field(default=None, sa_column=Column(DateTime(timezone=True), nullable=True))
    created_at: datetime = Field(default_factory=get_current_utc_datetime, sa_column=Column(DateTime(timezone=True), nullable=False, default=get_current_utc_datetime, server_default=func.now()))
    updated_at: datetime = Field(default_factory=get_current_utc_datetime, sa_column=Column(DateTime(timezone=True), nullable=False, default=get_current_utc_datetime, onupdate=get_current_utc_datetime, server_default=func.now()))


class WorkspaceOperationEvent(SQLModel, table=True):
    __tablename__ = "workspace_operation_events"
    __table_args__ = (
        UniqueConstraint("operation_id", "sequence", name="uq_workspace_operation_events_operation_sequence"),
        CheckConstraint("sequence >= 1", name="ck_workspace_operation_events_sequence"),
        CheckConstraint("operation_version >= 0", name="ck_workspace_operation_events_operation_version"),
        Index("ix_workspace_operation_events_operation_cursor", "operation_id", "cursor"),
        Index("ix_workspace_operation_events_owner_cursor", "owner_id", "cursor"),
    )

    cursor: Optional[int] = Field(default=None, sa_column=Column(_EVENT_CURSOR_TYPE, primary_key=True, autoincrement=True))
    owner_id: Optional[uuid.UUID] = Field(
        default_factory=get_current_owner_id,
        sa_column=_owner_column("workspace_operation_events"),
    )
    operation_id: uuid.UUID = Field(sa_column=Column(Uuid, ForeignKey("workspace_operations.id", name="fk_workspace_operation_events_operation_id", ondelete="CASCADE"), nullable=False))
    sequence: int = Field(sa_column=Column(Integer, nullable=False))
    operation_version: int = Field(sa_column=Column(Integer, nullable=False))
    event_type: str = Field(sa_column=Column(String(128), nullable=False))
    schema_version: int = Field(default=1, sa_column=Column(Integer, nullable=False, default=1, server_default=text("1")))
    payload: dict = Field(default_factory=dict, sa_column=Column(JSON, nullable=False))
    created_at: datetime = Field(default_factory=get_current_utc_datetime, sa_column=Column(DateTime(timezone=True), nullable=False, default=get_current_utc_datetime, server_default=func.now()))


class WorkspaceOutbox(SQLModel, table=True):
    __tablename__ = "workspace_outbox"
    __table_args__ = (
        UniqueConstraint("owner_scope", "idempotency_key_hash", name="uq_workspace_outbox_scope_idempotency"),
        CheckConstraint("status IN ('pending', 'delivering', 'delivered', 'failed', 'dead_letter')", name="ck_workspace_outbox_status"),
        CheckConstraint("delivery_attempts >= 0", name="ck_workspace_outbox_delivery_attempts"),
        Index("ix_workspace_outbox_delivery", "status", "available_at", "lease_expires_at"),
    )

    id: uuid.UUID = Field(primary_key=True, default_factory=uuid.uuid4)
    owner_id: Optional[uuid.UUID] = Field(
        default_factory=get_current_owner_id,
        sa_column=_owner_column("workspace_outbox"),
    )
    owner_scope: str = Field(default_factory=get_workspace_owner_scope, sa_column=Column(String(36), nullable=False, default=get_workspace_owner_scope, server_default="anonymous"))
    operation_id: Optional[uuid.UUID] = Field(default=None, sa_column=Column(Uuid, ForeignKey("workspace_operations.id", name="fk_workspace_outbox_operation_id", ondelete="CASCADE"), nullable=True))
    event_cursor: Optional[int] = Field(default=None, sa_column=Column(_EVENT_CURSOR_TYPE, ForeignKey("workspace_operation_events.cursor", name="fk_workspace_outbox_event_cursor", ondelete="CASCADE"), nullable=True))
    topic: str = Field(sa_column=Column(String(128), nullable=False))
    status: str = Field(default="pending", sa_column=Column(String(24), nullable=False, default="pending", server_default="pending"))
    idempotency_key_hash: str = Field(sa_column=Column(String(64), nullable=False))
    payload_hash: str = Field(sa_column=Column(String(64), nullable=False))
    payload: dict = Field(default_factory=dict, sa_column=Column(JSON, nullable=False))
    delivery_attempts: int = Field(default=0, sa_column=Column(Integer, nullable=False, default=0, server_default=text("0")))
    available_at: datetime = Field(default_factory=get_current_utc_datetime, sa_column=Column(DateTime(timezone=True), nullable=False, default=get_current_utc_datetime, server_default=func.now()))
    lease_expires_at: Optional[datetime] = Field(default=None, sa_column=Column(DateTime(timezone=True), nullable=True))
    heartbeat_at: Optional[datetime] = Field(default=None, sa_column=Column(DateTime(timezone=True), nullable=True))
    delivered_at: Optional[datetime] = Field(default=None, sa_column=Column(DateTime(timezone=True), nullable=True))
    last_error: Optional[dict] = Field(default=None, sa_column=Column(JSON, nullable=True))
    created_at: datetime = Field(default_factory=get_current_utc_datetime, sa_column=Column(DateTime(timezone=True), nullable=False, default=get_current_utc_datetime, server_default=func.now()))
    updated_at: datetime = Field(default_factory=get_current_utc_datetime, sa_column=Column(DateTime(timezone=True), nullable=False, default=get_current_utc_datetime, onupdate=get_current_utc_datetime, server_default=func.now()))


class WorkspaceActionReceipt(SQLModel, table=True):
    __tablename__ = "workspace_action_receipts"
    __table_args__ = (
        UniqueConstraint(
            "owner_scope",
            "operation_id",
            "action_type",
            "idempotency_key_hash",
            name="uq_workspace_action_receipts_action_idempotency",
        ),
        UniqueConstraint(
            "operation_id",
            "action_type",
            "action_id",
            name="uq_workspace_action_receipts_operation_action",
        ),
        CheckConstraint("status IN ('accepted', 'applied', 'rejected', 'duplicate', 'failed')", name="ck_workspace_action_receipts_status"),
        Index("ix_workspace_action_receipts_operation_created", "operation_id", "created_at"),
    )

    id: uuid.UUID = Field(primary_key=True, default_factory=uuid.uuid4)
    owner_id: Optional[uuid.UUID] = Field(
        default_factory=get_current_owner_id,
        sa_column=_owner_column("workspace_action_receipts"),
    )
    owner_scope: str = Field(default_factory=get_workspace_owner_scope, sa_column=Column(String(36), nullable=False, default=get_workspace_owner_scope, server_default="anonymous"))
    operation_id: uuid.UUID = Field(sa_column=Column(Uuid, ForeignKey("workspace_operations.id", name="fk_workspace_action_receipts_operation_id", ondelete="CASCADE"), nullable=False))
    result_operation_id: Optional[uuid.UUID] = Field(
        default=None,
        sa_column=Column(
            Uuid,
            ForeignKey(
                "workspace_operations.id",
                name="fk_workspace_action_receipts_result_operation_id",
                ondelete="SET NULL",
            ),
            nullable=True,
        ),
    )
    action_id: str = Field(sa_column=Column(String(128), nullable=False))
    action_type: str = Field(sa_column=Column(String(128), nullable=False))
    status: str = Field(default="accepted", sa_column=Column(String(24), nullable=False, default="accepted", server_default="accepted"))
    idempotency_key_hash: str = Field(sa_column=Column(String(64), nullable=False))
    request_hash: str = Field(sa_column=Column(String(64), nullable=False))
    result_hash: Optional[str] = Field(default=None, sa_column=Column(String(64), nullable=True))
    request_payload: Optional[dict] = Field(default=None, sa_column=Column(JSON, nullable=True))
    result_payload: Optional[dict] = Field(default=None, sa_column=Column(JSON, nullable=True))
    source_revision: Optional[int] = Field(default=None, sa_column=Column(BigInteger, nullable=True))
    result_revision: Optional[int] = Field(default=None, sa_column=Column(BigInteger, nullable=True))
    created_at: datetime = Field(default_factory=get_current_utc_datetime, sa_column=Column(DateTime(timezone=True), nullable=False, default=get_current_utc_datetime, server_default=func.now()))
    updated_at: datetime = Field(default_factory=get_current_utc_datetime, sa_column=Column(DateTime(timezone=True), nullable=False, default=get_current_utc_datetime, onupdate=get_current_utc_datetime, server_default=func.now()))


class WorkspaceArtifact(SQLModel, table=True):
    __tablename__ = "workspace_artifacts"
    __table_args__ = (
        UniqueConstraint("storage_key", name="uq_workspace_artifacts_storage_key"),
        CheckConstraint("status IN ('staging', 'ready', 'cleanup_pending', 'failed', 'deleted')", name="ck_workspace_artifacts_status"),
        CheckConstraint("size_bytes >= 0", name="ck_workspace_artifacts_size"),
        CheckConstraint("cleanup_attempts >= 0", name="ck_workspace_artifacts_cleanup_attempts"),
        Index("ix_workspace_artifacts_cleanup", "status", "expires_at", "cleanup_lease_expires_at"),
        Index("ix_workspace_artifacts_operation", "operation_id"),
    )

    id: uuid.UUID = Field(primary_key=True, default_factory=uuid.uuid4)
    owner_id: Optional[uuid.UUID] = Field(
        default_factory=get_current_owner_id,
        sa_column=_owner_column("workspace_artifacts"),
    )
    operation_id: uuid.UUID = Field(sa_column=Column(Uuid, ForeignKey("workspace_operations.id", name="fk_workspace_artifacts_operation_id", ondelete="CASCADE"), nullable=False))
    presentation_id: Optional[uuid.UUID] = Field(default=None, sa_column=Column(Uuid, ForeignKey("presentations.id", name="fk_workspace_artifacts_presentation_id", ondelete="CASCADE"), nullable=True))
    kind: str = Field(sa_column=Column(String(128), nullable=False))
    status: str = Field(default="staging", sa_column=Column(String(24), nullable=False, default="staging", server_default="staging"))
    storage_key: str = Field(sa_column=Column(String(1024), nullable=False))
    sha256: str = Field(sa_column=Column(String(64), nullable=False))
    size_bytes: int = Field(default=0, sa_column=Column(BigInteger, nullable=False, default=0, server_default=text("0")))
    source_revision: Optional[int] = Field(default=None, sa_column=Column(BigInteger, nullable=True))
    expires_at: Optional[datetime] = Field(default=None, sa_column=Column(DateTime(timezone=True), nullable=True))
    cleanup_token: Optional[str] = Field(default=None, sa_column=Column(String(128), nullable=True))
    cleanup_lease_expires_at: Optional[datetime] = Field(default=None, sa_column=Column(DateTime(timezone=True), nullable=True))
    cleanup_attempts: int = Field(default=0, sa_column=Column(Integer, nullable=False, default=0, server_default=text("0")))
    cleanup_error: Optional[dict] = Field(default=None, sa_column=Column(JSON, nullable=True))
    deleted_at: Optional[datetime] = Field(default=None, sa_column=Column(DateTime(timezone=True), nullable=True))
    created_at: datetime = Field(default_factory=get_current_utc_datetime, sa_column=Column(DateTime(timezone=True), nullable=False, default=get_current_utc_datetime, server_default=func.now()))
    updated_at: datetime = Field(default_factory=get_current_utc_datetime, sa_column=Column(DateTime(timezone=True), nullable=False, default=get_current_utc_datetime, onupdate=get_current_utc_datetime, server_default=func.now()))


class WorkspaceCleanupJournal(SQLModel, table=True):
    """Durable, owner-independent intent to remove one private storage object.

    Snapshot identifiers deliberately have no foreign keys.  This table must
    survive presentation/account cascades and is claimed with a lease + fencing
    token so two maintenance workers cannot finalize one another's attempts.
    """

    __tablename__ = "workspace_cleanup_journal"
    __table_args__ = (
        UniqueConstraint(
            "idempotency_key_hash",
            name="uq_workspace_cleanup_journal_idempotency",
        ),
        CheckConstraint(
            _state_check("status", WORKSPACE_CLEANUP_STATES),
            name="ck_workspace_cleanup_journal_status",
        ),
        CheckConstraint(
            "size_bytes IS NULL OR size_bytes >= 0",
            name="ck_workspace_cleanup_journal_size",
        ),
        CheckConstraint(
            "cleanup_attempts >= 0",
            name="ck_workspace_cleanup_journal_attempts",
        ),
        CheckConstraint(
            "max_attempts >= 1",
            name="ck_workspace_cleanup_journal_max_attempts",
        ),
        CheckConstraint(
            "fencing_token >= 0",
            name="ck_workspace_cleanup_journal_fencing_token",
        ),
        CheckConstraint(
            "redrive_count >= 0",
            name="ck_workspace_cleanup_journal_redrive_count",
        ),
        Index(
            "ix_workspace_cleanup_journal_dispatch",
            "status",
            "next_attempt_at",
            "cleanup_lease_expires_at",
        ),
        Index(
            "ix_workspace_cleanup_journal_presentation",
            "presentation_id_snapshot",
            "created_at",
        ),
        Index(
            "ix_workspace_cleanup_journal_owner",
            "owner_id_snapshot",
            "created_at",
        ),
    )

    id: uuid.UUID = Field(primary_key=True, default_factory=uuid.uuid4)
    owner_id_snapshot: Optional[uuid.UUID] = Field(
        default=None,
        sa_column=Column(Uuid, nullable=True),
    )
    presentation_id_snapshot: Optional[uuid.UUID] = Field(
        default=None,
        sa_column=Column(Uuid, nullable=True),
    )
    operation_id_snapshot: Optional[uuid.UUID] = Field(
        default=None,
        sa_column=Column(Uuid, nullable=True),
    )
    storage_namespace: str = Field(sa_column=Column(String(64), nullable=False))
    resource_kind: str = Field(sa_column=Column(String(128), nullable=False))
    storage_key: str = Field(sa_column=Column(String(1024), nullable=False))
    sha256: Optional[str] = Field(
        default=None,
        sa_column=Column(String(64), nullable=True),
    )
    size_bytes: Optional[int] = Field(
        default=None,
        sa_column=Column(BigInteger, nullable=True),
    )
    source_revision: Optional[int] = Field(
        default=None,
        sa_column=Column(BigInteger, nullable=True),
    )
    resource_metadata: Optional[dict] = Field(
        default=None,
        sa_column=Column(JSON, nullable=True),
    )
    idempotency_key_hash: str = Field(sa_column=Column(String(64), nullable=False))
    status: str = Field(
        default="pending",
        sa_column=Column(
            String(24), nullable=False, default="pending", server_default="pending"
        ),
    )
    cleanup_token: Optional[str] = Field(
        default=None,
        sa_column=Column(String(128), nullable=True),
    )
    cleanup_lease_expires_at: Optional[datetime] = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), nullable=True),
    )
    next_attempt_at: Optional[datetime] = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), nullable=True),
    )
    cleanup_attempts: int = Field(
        default=0,
        sa_column=Column(Integer, nullable=False, default=0, server_default=text("0")),
    )
    max_attempts: int = Field(
        default=8,
        sa_column=Column(Integer, nullable=False, default=8, server_default=text("8")),
    )
    fencing_token: int = Field(
        default=0,
        sa_column=Column(BigInteger, nullable=False, default=0, server_default=text("0")),
    )
    redrive_count: int = Field(
        default=0,
        sa_column=Column(Integer, nullable=False, default=0, server_default=text("0")),
    )
    last_redriven_at: Optional[datetime] = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), nullable=True),
    )
    last_redriven_by_snapshot: Optional[uuid.UUID] = Field(
        default=None,
        sa_column=Column(Uuid, nullable=True),
    )
    last_error: Optional[dict] = Field(
        default=None,
        sa_column=Column(JSON, nullable=True),
    )
    deleted_at: Optional[datetime] = Field(
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
