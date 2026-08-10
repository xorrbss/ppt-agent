"""Stable contracts for asset storage admission.

The public binding intentionally contains identifiers and verified metadata
only. Filesystem locations remain private to :class:`AssetGateway`.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
import uuid


class AssetKind(StrEnum):
    FILE = "file"
    IMAGE = "image"
    FONT = "font"
    CONVERSATION_ATTACHMENT = "conversation_attachment"
    TEMPLATE_PACKAGE = "template_package"
    GENERATED = "generated"


class AssetGatewayError(RuntimeError):
    def __init__(
        self,
        code: str,
        *,
        status_code: int = 400,
        retryable: bool = False,
    ) -> None:
        super().__init__(code)
        self.code = code
        self.status_code = status_code
        self.retryable = retryable


@dataclass(frozen=True, slots=True)
class AssetPolicy:
    kind: AssetKind
    allowed_media_types: frozenset[str]
    allowed_extensions: frozenset[str]
    max_bytes: int
    malware_scan_required: bool
    extraction_allowed: bool
    retention_seconds: int | None
    owner_scoped: bool = True
    publication_states: frozenset[str] = frozenset({"ready"})


@dataclass(frozen=True, slots=True)
class AssetBinding:
    """Immutable metadata that is safe to persist or give to generation."""

    asset_id: uuid.UUID
    kind: AssetKind
    media_type: str
    size_bytes: int
    sha256: str
    version: int
    conversation_id: uuid.UUID | None
    scan_state: str = "passed"
    lifecycle_state: str = "ready"

    def as_json(self) -> dict[str, object]:
        return {
            "assetId": str(self.asset_id),
            "kind": self.kind.value,
            "mediaType": self.media_type,
            "sizeBytes": self.size_bytes,
            "sha256": self.sha256,
            "version": self.version,
            "conversationId": (
                str(self.conversation_id) if self.conversation_id else None
            ),
            "scanState": self.scan_state,
            "lifecycleState": self.lifecycle_state,
        }
