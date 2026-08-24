"""Validated HTTP models for the authored vNext durable export API."""

from __future__ import annotations

from typing import Literal

from pydantic import Field, model_validator

from models.authored_vnext.master.base import DeckId, Digest, StrictCamelModel


class CreateExportJobRequest(StrictCamelModel):
    deck_id: DeckId
    expected_deck_revision: int = Field(ge=1)
    expected_deck_digest: Digest
    expected_master_revision: int = Field(ge=1)
    expected_master_digest: Digest
    expected_active_artifact_revision: int = Field(default=0, ge=0)
    expected_active_artifact_digest: Digest | None = None
    expected_source_package_digest: Digest | None = None
    expected_asset_digests: list[Digest] = Field(default_factory=list, max_length=128)
    expected_font_digest: Digest
    editable_hybrid_profile_id: Literal[
        "authored-editable-hybrid-ko-en-basic-v1"
    ] | None = None
    idempotency_key: str = Field(min_length=1, max_length=200)
    operation_fence: int = Field(default=1, ge=1)
    timeout_ms: int = Field(default=120_000, ge=1_000, le=600_000)
    max_attempts: int = Field(default=3, ge=1, le=10)

    @model_validator(mode="after")
    def _publication_cas_is_coherent(self) -> "CreateExportJobRequest":
        if self.expected_active_artifact_revision == 0:
            if self.expected_active_artifact_digest is not None:
                raise ValueError("initial publication cannot expect an artifact digest")
        elif self.expected_active_artifact_digest is None:
            raise ValueError("existing publication requires its artifact digest")
        if len(set(self.expected_asset_digests)) != len(self.expected_asset_digests):
            raise ValueError("asset digests must be unique")
        return self


class ExportArtifactReceipt(StrictCamelModel):
    artifact_id: str
    publication_revision: int = Field(ge=1)
    digest: Digest
    byte_length: int = Field(gt=0)
    package_digest: Digest
    master_digest: Digest
    deck_digest: Digest
    read_back_verified: Literal[True]


class ExportJobResponse(StrictCamelModel):
    schema_version: Literal["authored-vnext-export-job/v1"] = (
        "authored-vnext-export-job/v1"
    )
    job_id: str
    deck_id: DeckId
    state: Literal[
        "queued",
        "running",
        "retry_wait",
        "succeeded",
        "failed",
        "cancelled",
    ]
    attempt_count: int = Field(ge=0)
    max_attempts: int = Field(ge=1)
    operation_fence: int = Field(ge=1)
    cancel_requested: bool
    resumable: bool
    error_code: str | None = None
    artifact: ExportArtifactReceipt | None = None


class PublicationCasRequest(StrictCamelModel):
    expected_publication_revision: int = Field(ge=1)
    expected_artifact_digest: Digest


class CancelResponse(StrictCamelModel):
    job_id: str
    state: str
    cancel_requested: bool


class OperationFenceRequest(StrictCamelModel):
    operation_fence: int = Field(ge=1)
