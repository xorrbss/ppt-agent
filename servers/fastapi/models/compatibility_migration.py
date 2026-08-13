"""Public contracts for the authored-only compatibility migration boundary."""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import Enum
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class FrozenCompatibilityOperation(str, Enum):
    OPEN = "open"
    PREVIEW = "preview"
    EXPORT = "export"
    DOWNLOAD = "download"
    DELETE = "delete"
    UPGRADE_FORK = "upgrade-fork"


class FrozenRuntimeDescriptor(BaseModel):
    model_config = ConfigDict(frozen=True)

    schema_version: Literal[1] = 1
    presentation_id: uuid.UUID
    source_driver_id: Literal["standard", "smart", "template-v2"]
    state: Literal["frozen-compatibility"] = "frozen-compatibility"
    allowed_operations: tuple[FrozenCompatibilityOperation, ...]


class FrozenOperationDecision(BaseModel):
    model_config = ConfigDict(frozen=True)

    presentation_id: uuid.UUID
    operation: FrozenCompatibilityOperation
    allowed: Literal[True] = True
    runtime: Literal["frozen-compatibility"] = "frozen-compatibility"


class CompatibilityErrorDetail(BaseModel):
    model_config = ConfigDict(frozen=True)

    code: str
    message: str


class ArchiveDigest(BaseModel):
    model_config = ConfigDict(frozen=True)

    role: Literal["source", "runtime", "artifact"]
    algorithm: Literal["sha256"] = "sha256"
    value: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    byte_length: Annotated[int, Field(ge=0)]


class ArchiveManifestPayload(BaseModel):
    """Content-safe archive inventory; payload bytes and paths are never embedded."""

    model_config = ConfigDict(frozen=True)

    schema_version: Literal[1] = 1
    archive_id: uuid.UUID
    source_presentation_id: uuid.UUID
    target_presentation_id: uuid.UUID
    owner_scope_sha256: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    created_at: datetime
    digests: tuple[ArchiveDigest, ArchiveDigest, ArchiveDigest]

    @model_validator(mode="after")
    def require_each_digest_role_once(self) -> "ArchiveManifestPayload":
        roles = [digest.role for digest in self.digests]
        if roles != ["source", "runtime", "artifact"]:
            raise ValueError("archive_digest_roles_invalid")
        if self.source_presentation_id == self.target_presentation_id:
            raise ValueError("archive_target_must_be_new")
        return self


class SignedArchiveManifest(BaseModel):
    model_config = ConfigDict(frozen=True)

    payload: ArchiveManifestPayload
    signature_algorithm: Literal["hmac-sha256"] = "hmac-sha256"
    signing_key_id: Annotated[
        str, Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9._-]+$")
    ]
    signature: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]


class AuthoredForkSlide(BaseModel):
    """Server-owned authored slide input; IDs, owner and profile are not accepted."""

    model_config = ConfigDict(extra="forbid")

    index: Annotated[int, Field(ge=0)]
    html_content: Annotated[str, Field(min_length=1)]
    speaker_note: str | None = None
    properties: dict[str, Any] | None = None


class UpgradeForkRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_source_revision: Annotated[int, Field(ge=0)]
    slides: Annotated[list[AuthoredForkSlide], Field(min_length=1)]
    title: Annotated[str, Field(min_length=1, max_length=512)] | None = None

    @model_validator(mode="after")
    def require_contiguous_unique_slide_indexes(self) -> "UpgradeForkRequest":
        indexes = [slide.index for slide in self.slides]
        if sorted(indexes) != list(range(len(indexes))):
            raise ValueError("authored_fork_slide_indexes_must_be_contiguous")
        return self


class UpgradeForkResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    schema_version: Literal[1] = 1
    source_presentation_id: uuid.UUID
    presentation_id: uuid.UUID
    driver_id: Literal["authored"] = "authored"
    source_revision: int
    archive_manifest: SignedArchiveManifest

