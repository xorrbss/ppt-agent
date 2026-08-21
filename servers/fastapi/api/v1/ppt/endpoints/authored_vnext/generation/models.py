"""Public authored-vNext operation envelopes."""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from models.authored_vnext.deck import (
    CandidateBundle,
    DeckGenerationSnapshot,
    GenerateDeckRequest,
    GenerationResult,
    RegenerateBodyCommand,
    RegenerationResult,
    ResumeGenerationRequest,
    RollbackDeckRequest,
    RollbackResult,
    ReorderSlidesCommand,
    SceneMutationCommand,
    SceneMutationResult,
)
from models.authored_vnext.master.base import Digest, StrictCamelModel


class OperationAccepted(StrictCamelModel):
    schema_version: Literal["authored-vnext-operation/v1"] = "authored-vnext-operation/v1"
    operation_id: str
    deck_id: str
    kind: str
    state: Literal["queued", "running", "succeeded", "failed", "cancelled"]
    fencing_token: int = Field(ge=0)
    attempt_count: int = Field(ge=0)
    checkpoint: dict | None = None
    result: dict | None = None
    error_code: str | None = None


class GenerateOperationRequest(StrictCamelModel):
    idempotency_key: str = Field(min_length=8, max_length=256)
    command: GenerateDeckRequest


class ResumeOperationRequest(StrictCamelModel):
    idempotency_key: str = Field(min_length=8, max_length=256)
    command: ResumeGenerationRequest


class RegenerateOperationRequest(StrictCamelModel):
    idempotency_key: str = Field(min_length=8, max_length=256)
    command: RegenerateBodyCommand


class CandidateResponse(StrictCamelModel):
    schema_version: Literal["regeneration-candidate-response/v1"] = (
        "regeneration-candidate-response/v1"
    )
    operation_id: str
    candidate: CandidateBundle


class PublishCandidateRequest(StrictCamelModel):
    idempotency_key: str = Field(min_length=8, max_length=256)
    operation_id: str


class RollbackOperationRequest(StrictCamelModel):
    idempotency_key: str = Field(min_length=8, max_length=256)
    command: RollbackDeckRequest


class ReorderOperationRequest(StrictCamelModel):
    idempotency_key: str = Field(min_length=8, max_length=256)
    command: ReorderSlidesCommand


class SceneMutationOperationRequest(StrictCamelModel):
    idempotency_key: str = Field(min_length=8, max_length=256)
    command: SceneMutationCommand


class DeckResponse(StrictCamelModel):
    schema_version: Literal["authored-vnext-deck-response/v1"] = (
        "authored-vnext-deck-response/v1"
    )
    deck: DeckGenerationSnapshot


GenerationPublicResult = (
    GenerationResult | RegenerationResult | RollbackResult | SceneMutationResult
)


class CandidateReference(StrictCamelModel):
    candidate_digest: Digest
    operation_id: str
