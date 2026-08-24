"""Public authored-vNext operation envelopes."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import Field, model_validator

from models.authored_vnext.semantic.native_capabilities import (
    NativeCapabilityProjectionV1,
)

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


class EditableHybridMvpStateV4(StrictCamelModel):
    profile_id: Literal["authored-editable-hybrid-ko-en-basic-v1"] = (
        "authored-editable-hybrid-ko-en-basic-v1"
    )
    release_scope: Literal["ko-en-text-solid-shape-image-crop-basic-table"] = (
        "ko-en-text-solid-shape-image-crop-basic-table"
    )
    status: Literal["not-evaluated", "shadow", "ready", "blocked", "expired"]
    editable_hybrid_mvp_ready: bool
    native_editable_ready: bool
    reason_code: str = Field(min_length=1, max_length=128)


class LocalRasterQualificationStateV4(StrictCamelModel):
    status: Literal["not-evaluated", "shadow", "ready", "blocked", "expired"]
    local_raster_release_ready: bool


class ExternalPromotionStateV4(StrictCamelModel):
    status: Literal["not-evaluated", "shadow", "ready", "blocked", "expired"]
    promotion_ready: bool


class DeploymentStateV4(StrictCamelModel):
    deployment_authorized: bool


class EditableHybridReadinessProjectionV4(StrictCamelModel):
    editable_hybrid_mvp: EditableHybridMvpStateV4
    native_capabilities: NativeCapabilityProjectionV1
    local_raster_qualification: LocalRasterQualificationStateV4
    external_promotion: ExternalPromotionStateV4
    deployment: DeploymentStateV4


class EditableHybridGate0eProbeCaseV1(StrictCamelModel):
    ordinal: int = Field(ge=1, le=15)
    case_id: str = Field(min_length=1, max_length=128)
    mutation_payload: dict[str, Any]
    expected_http_status: Literal[403, 409, 422]
    expected_error_code: str = Field(min_length=1, max_length=128)
    observed_http_status: Literal[403, 409, 422]
    observed_error_code: str = Field(min_length=1, max_length=128)
    fail_closed: Literal[True] = True


class EditableHybridGate0eProbesV1(StrictCamelModel):
    schema_version: Literal[
        "authored-editable-hybrid-gate0e-observations/v1"
    ] = "authored-editable-hybrid-gate0e-observations/v1"
    authority_domain: Literal["structural_test"] = "structural_test"
    profile_id: Literal["authored-editable-hybrid-ko-en-basic-v1"] = (
        "authored-editable-hybrid-ko-en-basic-v1"
    )
    manifest_digest: Literal[
        "sha256:07c236cc2e54ba3a80f31087b63613e601e32f81b5334f64b803abb5ddd180db"
    ] = "sha256:07c236cc2e54ba3a80f31087b63613e601e32f81b5334f64b803abb5ddd180db"
    attempt: dict[str, Any]
    baseline: dict[str, Any]
    baseline_digest: Digest
    result_set_digest: Digest
    cases: list[EditableHybridGate0eProbeCaseV1] = Field(
        min_length=15, max_length=15
    )
    all_cases_passed: Literal[True] = True
    state_unchanged: Literal[True] = True
    promotion_ready: Literal[False] = False
    deployment_authorized: Literal[False] = False

    @model_validator(mode="after")
    def _exact_manifest_order(self) -> "EditableHybridGate0eProbesV1":
        if [item.ordinal for item in self.cases] != list(range(1, 16)):
            raise ValueError("Gate 0E observations must retain manifest order")
        if len({item.case_id for item in self.cases}) != 15:
            raise ValueError("Gate 0E observations must contain every case once")
        return self
