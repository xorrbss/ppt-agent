"""Lease-fenced public API for authored-vNext generation and regeneration."""

from __future__ import annotations

from functools import lru_cache
import os
from typing import Annotated, Callable, TypeVar
import uuid

from fastapi import APIRouter, Depends, HTTPException, Path, Query
from pydantic import BaseModel

from models.authored_vnext.deck import (
    GenerationResult,
    DeckMutationResult,
    MasterDigestPin,
    RegenerationResult,
    RollbackResult,
    SceneMutationResult,
)
from models.authored_vnext.deck.quality import require_published_deck_size
from models.authored_vnext.semantic.editable_mvp_completion import (
    EditableMvpCompletionSealImportRequestV1,
    EditableMvpCompletionSealImportResponseV1,
)
from services.authored_vnext.generation.core_bridge import DurableExportPlanBridge
from services.authored_vnext.generation.errors import GenerationPipelineError
from services.authored_vnext.generation.pipeline import AuthoredVNextGenerationPipeline
from services.authored_vnext.generation.mutations import (
    mutate_scene_element,
    reorder_deck_slides,
)
from services.authored_vnext.generation.sql_repository import SqlGenerationRepository
from services.authored_vnext.integration.sql_repository import AuthoredVNextSqlRepository
from services.authored_vnext.master.digests import canonical_digest
from services.authored_vnext.semantic.editable_mvp_completion import (
    EditableMvpCompletionError,
    match_current_editable_mvp_completion,
    seal_import_editable_mvp_completion,
)
from services.authored_vnext.semantic.native_capabilities import (
    EDITABLE_MVP_CAPABILITY_IDS_V1,
    build_native_capability_projection_v1,
)
from services.authored_vnext.generation.gate0e_probes import (
    Gate0eProbeError,
    run_gate0e_structural_probes,
)
from utils.owner_context import get_current_owner_id

from .models import (
    CandidateResponse,
    DeckResponse,
    DeploymentStateV4,
    EditableHybridMvpStateV4,
    EditableHybridGate0eProbesV1,
    EditableHybridReadinessProjectionV4,
    ExternalPromotionStateV4,
    GenerateOperationRequest,
    OperationAccepted,
    PublishCandidateRequest,
    RegenerateOperationRequest,
    ReorderOperationRequest,
    ResumeOperationRequest,
    RollbackOperationRequest,
    SceneMutationOperationRequest,
    LocalRasterQualificationStateV4,
)


def _enabled() -> bool:
    return os.getenv("AUTHORED_VNEXT_GENERATION_ENABLED", "false").lower() == "true"


def _require_enabled() -> None:
    if not _enabled():
        raise HTTPException(status_code=404, detail="authored_vnext_generation_disabled")


def _require_internal_new_generation() -> None:
    """Keep independent vNext creation frozen behind an internal double gate.

    Existing deck reads, downloads and compatibility mutations continue to use
    this router.  A new vNext deck may only be minted by an explicitly enabled
    internal compatibility worker; the user product creates Authored documents
    through the canonical presentation endpoint instead.
    """

    internal_compat = (
        os.getenv("AUTHORED_VNEXT_INTERNAL_COMPATIBILITY_ENABLED", "false")
        .strip()
        .lower()
        == "true"
    )
    new_generation = (
        os.getenv("AUTHORED_VNEXT_NEW_GENERATION_ENABLED", "false")
        .strip()
        .lower()
        == "true"
    )
    if internal_compat and new_generation:
        return
    raise HTTPException(
        status_code=409,
        detail={
            "code": "authored_vnext_independent_generation_frozen",
            "requiredUserEngine": "authored",
            "compatibilityReadOpen": True,
            "promotionReady": False,
        },
    )


@lru_cache(maxsize=32)
def _base_repository_for_owner(owner_id: str) -> AuthoredVNextSqlRepository:
    owner_uuid = uuid.UUID(owner_id)
    return AuthoredVNextSqlRepository.from_application_database(
        owner_scope=owner_id,
        tenant_id=os.getenv("AUTHORED_VNEXT_TENANT_ID", "default"),
        owner_id=owner_uuid,
    )


def _repository_for_owner(owner_id: str) -> SqlGenerationRepository:
    """Return a request-scoped claim holder over the cached owner SQL base.

    ``SqlGenerationRepository`` carries the active generation lease claim.  A
    cached wrapper lets two concurrent requests for the same owner overwrite
    that mutable claim before either request publishes.  The engine-backed base
    is safe to share; the claim-bearing adapter is intentionally not.
    """

    return SqlGenerationRepository(_base_repository_for_owner(owner_id))


def get_generation_repository() -> SqlGenerationRepository:
    owner_id = get_current_owner_id()
    if owner_id is None:
        raise HTTPException(status_code=401, detail="authentication_required")
    return _repository_for_owner(str(owner_id))


Repository = Annotated[SqlGenerationRepository, Depends(get_generation_repository)]


AUTHORED_VNEXT_GENERATION_ROUTER = APIRouter(
    prefix="/authored-vnext/generation",
    tags=["Authored vNext Generation"],
    dependencies=[Depends(_require_enabled)],
)


def _pipeline(repository: SqlGenerationRepository) -> AuthoredVNextGenerationPipeline:
    return AuthoredVNextGenerationPipeline(repository, DurableExportPlanBridge())


def _error(error: Exception) -> HTTPException:
    if isinstance(error, GenerationPipelineError):
        code = error.code
        status_code = 404 if code == "authored_vnext_generation_not_found" else 409
        if code == "authored_vnext_candidate_rejected":
            status_code = 422
        return HTTPException(status_code=status_code, detail={"code": code})
    return HTTPException(status_code=409, detail={"code": "authored_vnext_operation_conflict"})


T = TypeVar("T", bound=BaseModel)


def _run(
    repository: SqlGenerationRepository,
    *,
    command: BaseModel,
    idempotency_key: str,
    kind: str,
    expected_revision: int | None,
    expected_digest: str | None,
    result_type: type[T],
    execute: Callable[[], T],
) -> T:
    row = repository.begin_operation(
        operation_id=str(command.operation_id),
        deck_id=str(command.deck_id),
        kind=kind,
        idempotency_key=idempotency_key,
        request=command,
        expected_revision=expected_revision,
        expected_digest=expected_digest,
    )
    if row.state == "succeeded" and row.result_payload is not None:
        return result_type.model_validate(row.result_payload)
    repository.claim_operation(
        str(command.operation_id),
        worker_id=f"api:{uuid.uuid4().hex}",
    )
    try:
        return execute()
    except Exception as error:
        repository.fail_operation(str(command.operation_id), error)
        raise


@AUTHORED_VNEXT_GENERATION_ROUTER.get(
    "/master-pins/{master_id}/{revision}",
    response_model=MasterDigestPin,
)
def get_master_pin(
    repository: Repository,
    master_id: str = Path(pattern=r"^master:[a-z0-9][a-z0-9._-]*$"),
    revision: int = Path(ge=1),
) -> MasterDigestPin:
    try:
        master = repository.get_master(master_id, revision)
        head = repository.get_master_head(master_id)
        if master.status != "published" or head.revision != revision or head.digest != master.digest:
            raise HTTPException(status_code=409, detail={"code": "authored_vnext_stale_master"})
        font_digest = master.contract.font_manifest.digest
        if font_digest is None:
            raise HTTPException(status_code=409, detail={"code": "authored_vnext_stale_master"})
        return MasterDigestPin(
            master_id=master_id,
            master_revision=revision,
            master_digest=master.digest,
            layout_registry_digest=canonical_digest(
                master.contract.layouts.model_dump(mode="json", by_alias=True)
            ),
            font_manifest_digest=font_digest,
        )
    except HTTPException:
        raise
    except Exception as error:
        raise _error(error) from error


@AUTHORED_VNEXT_GENERATION_ROUTER.post("/decks", response_model=GenerationResult)
def generate_deck(request: GenerateOperationRequest, repository: Repository) -> GenerationResult:
    _require_internal_new_generation()
    command = request.command
    try:
        require_published_deck_size(len(command.outline.items))
    except ValueError as error:
        raise HTTPException(
            status_code=422,
            detail={"code": "authored_vnext_generation_deck_size_invalid"},
        ) from error
    try:
        return _run(
            repository,
            command=command,
            idempotency_key=request.idempotency_key,
            kind="deck.generate",
            expected_revision=None,
            expected_digest=None,
            result_type=GenerationResult,
            execute=lambda: _pipeline(repository).generate(command),
        )
    except Exception as error:
        raise _error(error) from error


@AUTHORED_VNEXT_GENERATION_ROUTER.post("/resume", response_model=GenerationResult)
def resume_generation(request: ResumeOperationRequest, repository: Repository) -> GenerationResult:
    try:
        return _pipeline(repository).resume(request.command)
    except Exception as error:
        raise _error(error) from error


@AUTHORED_VNEXT_GENERATION_ROUTER.post(
    "/decks/{deck_id}/slides/{slide_id}/regeneration-candidates",
    response_model=CandidateResponse,
)
def prepare_regeneration(
    request: RegenerateOperationRequest,
    repository: Repository,
    deck_id: str = Path(pattern=r"^deck:[a-z0-9][a-z0-9._-]*$"),
    slide_id: str = Path(pattern=r"^slide:[a-z0-9][a-z0-9._-]*$"),
) -> CandidateResponse:
    command = request.command
    if command.deck_id != deck_id or command.slide_id != slide_id:
        raise HTTPException(status_code=422, detail={"code": "authored_vnext_path_mismatch"})
    try:
        row = repository.begin_operation(
            operation_id=command.operation_id,
            deck_id=deck_id,
            kind="slide.regenerate.prepare",
            idempotency_key=request.idempotency_key,
            request=command,
            expected_revision=command.publication_fence.expected_deck_revision,
            expected_digest=command.publication_fence.expected_active_revision_digest,
        )
        if row.state == "succeeded" and row.result_payload is not None:
            return CandidateResponse.model_validate(row.result_payload)
        repository.claim_operation(command.operation_id, worker_id=f"api:{uuid.uuid4().hex}")
        candidate = _pipeline(repository).prepare_regeneration(command)
        response = CandidateResponse(operation_id=command.operation_id, candidate=candidate)
        repository.complete_operation(command.operation_id, response.model_dump(mode="json", by_alias=True))
        return response
    except Exception as error:
        repository.fail_operation(command.operation_id, error)
        raise _error(error) from error


@AUTHORED_VNEXT_GENERATION_ROUTER.post(
    "/decks/{deck_id}/regeneration-candidates/{candidate_digest}/publish",
    response_model=RegenerationResult,
)
def publish_regeneration(
    request: PublishCandidateRequest,
    repository: Repository,
    deck_id: str = Path(pattern=r"^deck:[a-z0-9][a-z0-9._-]*$"),
    candidate_digest: str = Path(pattern=r"^sha256:[0-9a-f]{64}$"),
) -> RegenerationResult:
    stored = repository.get_candidate(candidate_digest)
    command = stored.get("command")
    if not isinstance(command, dict) or command.get("deckId") != deck_id:
        raise HTTPException(status_code=404, detail={"code": "authored_vnext_generation_not_found"})
    operation = _CandidatePublishCommand(
        operation_id=request.operation_id,
        deck_id=deck_id,
        candidate_digest=candidate_digest,
    )
    try:
        return _run(
            repository,
            command=operation,
            idempotency_key=request.idempotency_key,
            kind="slide.regenerate.publish",
            expected_revision=int(command["publicationFence"]["expectedDeckRevision"]),
            expected_digest=str(command["publicationFence"]["expectedActiveRevisionDigest"]),
            result_type=RegenerationResult,
            execute=lambda: _pipeline(repository).publish_regeneration(
                candidate_digest,
                publication_operation_id=request.operation_id,
            ),
        )
    except Exception as error:
        raise _error(error) from error


class _CandidatePublishCommand(BaseModel):
    operation_id: str
    deck_id: str
    candidate_digest: str


@AUTHORED_VNEXT_GENERATION_ROUTER.post("/decks/{deck_id}/rollback", response_model=RollbackResult)
def rollback_deck(
    request: RollbackOperationRequest,
    repository: Repository,
    deck_id: str = Path(pattern=r"^deck:[a-z0-9][a-z0-9._-]*$"),
) -> RollbackResult:
    command = request.command
    if command.deck_id != deck_id:
        raise HTTPException(status_code=422, detail={"code": "authored_vnext_path_mismatch"})
    try:
        return _run(
            repository,
            command=command,
            idempotency_key=request.idempotency_key,
            kind="deck.rollback",
            expected_revision=command.publication_fence.expected_deck_revision,
            expected_digest=command.publication_fence.expected_active_revision_digest,
            result_type=RollbackResult,
            execute=lambda: _pipeline(repository).rollback(command),
        )
    except Exception as error:
        raise _error(error) from error


@AUTHORED_VNEXT_GENERATION_ROUTER.post(
    "/decks/{deck_id}/reorder",
    response_model=DeckMutationResult,
)
def reorder_slides(
    request: ReorderOperationRequest,
    repository: Repository,
    deck_id: str = Path(pattern=r"^deck:[a-z0-9][a-z0-9._-]*$"),
) -> DeckMutationResult:
    command = request.command
    if command.deck_id != deck_id:
        raise HTTPException(status_code=422, detail={"code": "authored_vnext_path_mismatch"})
    bridge = DurableExportPlanBridge()
    try:
        return _run(
            repository,
            command=command,
            idempotency_key=request.idempotency_key,
            kind="deck.slides.reorder",
            expected_revision=command.publication_fence.expected_deck_revision,
            expected_digest=command.publication_fence.expected_active_revision_digest,
            result_type=DeckMutationResult,
            execute=lambda: reorder_deck_slides(repository, bridge, command),
        )
    except Exception as error:
        raise _error(error) from error


@AUTHORED_VNEXT_GENERATION_ROUTER.post(
    "/decks/{deck_id}/scene-mutations",
    response_model=SceneMutationResult,
)
def mutate_scene(
    request: SceneMutationOperationRequest,
    repository: Repository,
    deck_id: str = Path(pattern=r"^deck:[a-z0-9][a-z0-9._-]*$"),
) -> SceneMutationResult:
    command = request.command
    if command.deck_id != deck_id:
        raise HTTPException(
            status_code=422,
            detail={"code": "authored_vnext_path_mismatch"},
        )
    try:
        return _run(
            repository,
            command=command,
            idempotency_key=request.idempotency_key,
            kind=f"scene.{command.kind}",
            expected_revision=command.publication_fence.expected_deck_revision,
            expected_digest=command.publication_fence.expected_active_revision_digest,
            result_type=SceneMutationResult,
            execute=lambda: mutate_scene_element(
                repository,
                DurableExportPlanBridge(),
                command,
            ),
        )
    except Exception as error:
        raise _error(error) from error


@AUTHORED_VNEXT_GENERATION_ROUTER.get("/decks/{deck_id}", response_model=DeckResponse)
def get_deck(
    repository: Repository,
    deck_id: str = Path(pattern=r"^deck:[a-z0-9][a-z0-9._-]*$"),
) -> DeckResponse:
    try:
        return DeckResponse(deck=repository.get_deck(deck_id))
    except Exception as error:
        raise _error(error) from error


@AUTHORED_VNEXT_GENERATION_ROUTER.get(
    "/decks/{deck_id}/editable-hybrid-readiness-v4",
    response_model=EditableHybridReadinessProjectionV4,
)
def get_editable_hybrid_readiness_v4(
    repository: Repository,
    deck_id: str = Path(pattern=r"^deck:[a-z0-9][a-z0-9._-]*$"),
) -> EditableHybridReadinessProjectionV4:
    """Return the fail-closed editable MVP display projection.

    Scene/admission presence can advance this projection only to ``shadow``.
    The isolated sealed Gate 1E completion matcher owns the sole transition to
    ``ready``; this endpoint never infers it from element counts or export.
    """

    try:
        deck = repository.get_deck(deck_id)
    except Exception as error:
        raise _error(error) from error
    scene = deck.scene
    completion = None
    if scene is None or scene.hybrid_profile_id is None:
        editable = EditableHybridMvpStateV4(
            status="not-evaluated",
            editable_hybrid_mvp_ready=False,
            native_editable_ready=False,
            reason_code="editable_hybrid_mvp_not_evaluated",
        )
    elif any(
        decision.decision == "publicationBlocked"
        for slide in scene.slides
        for decision in slide.hybrid_export.owner_decisions  # type: ignore[union-attr]
    ):
        editable = EditableHybridMvpStateV4(
            status="blocked",
            editable_hybrid_mvp_ready=False,
            native_editable_ready=False,
            reason_code="editable_hybrid_mvp_publication_blocked",
        )
    else:
        completion, completion_reason = match_current_editable_mvp_completion(
            repository,
            deck=deck,
        )
        if completion is not None:
            editable = EditableHybridMvpStateV4(
                status="ready",
                editable_hybrid_mvp_ready=True,
                native_editable_ready=True,
                reason_code="editable_hybrid_mvp_completion_current",
            )
        else:
            status = (
                "expired"
                if completion_reason == "editable_hybrid_mvp_completion_expired"
                else (
                    "blocked"
                    if completion_reason
                    in {
                        "editable_hybrid_mvp_kill_switch_active",
                        "editable_hybrid_mvp_trust_pin_changed",
                        "editable_hybrid_mvp_completion_binding_stale",
                        "editable_hybrid_mvp_export_attempt_not_current",
                    }
                    else "shadow"
                )
            )
            editable = EditableHybridMvpStateV4(
                status=status,
                editable_hybrid_mvp_ready=False,
                native_editable_ready=False,
                reason_code=completion_reason,
            )
    return EditableHybridReadinessProjectionV4(
        editable_hybrid_mvp=editable,
        native_capabilities=build_native_capability_projection_v1(
            scene,
            ready_receipts=(
                {
                    capability_id: completion.receipt_digest
                    for capability_id in EDITABLE_MVP_CAPABILITY_IDS_V1
                }
                if completion is not None
                else {}
            ),
        ),
        local_raster_qualification=LocalRasterQualificationStateV4(
            status="not-evaluated",
            local_raster_release_ready=False,
        ),
        external_promotion=ExternalPromotionStateV4(
            status="not-evaluated",
            promotion_ready=False,
        ),
        deployment=DeploymentStateV4(deployment_authorized=False),
    )


@AUTHORED_VNEXT_GENERATION_ROUTER.get(
    "/decks/{deck_id}/editable-hybrid-gate0e-probes-v1",
    response_model=EditableHybridGate0eProbesV1,
)
def get_editable_hybrid_gate0e_probes_v1(
    repository: Repository,
    deck_id: str = Path(pattern=r"^deck:[a-z0-9][a-z0-9._-]*$"),
    gate_minus1e_result_set_digest: str | None = Query(
        default=None,
        alias="gateMinus1eResultSetDigest",
        pattern=r"^sha256:[0-9a-f]{64}$",
    ),
    source_head_commit: str | None = Query(
        default=None,
        alias="sourceHeadCommit",
        pattern=r"^[0-9a-f]{40}$",
    ),
) -> EditableHybridGate0eProbesV1:
    """Evaluate all structural mutations against immutable current baselines."""

    try:
        result = run_gate0e_structural_probes(
            repository,
            deck_id=deck_id,
            gate_minus1e_result_set_digest=gate_minus1e_result_set_digest,
            source_head_commit=source_head_commit,
        )
        return EditableHybridGate0eProbesV1.model_validate(result.payload())
    except Gate0eProbeError as error:
        raise HTTPException(
            status_code=error.status_code,
            detail={"code": error.code},
        ) from error


@AUTHORED_VNEXT_GENERATION_ROUTER.post(
    "/decks/{deck_id}/editable-hybrid-completions-v1/seal-import",
    response_model=EditableMvpCompletionSealImportResponseV1,
)
def seal_import_editable_hybrid_completion_v1(
    request: EditableMvpCompletionSealImportRequestV1,
    repository: Repository,
    deck_id: str = Path(pattern=r"^deck:[a-z0-9][a-z0-9._-]*$"),
) -> EditableMvpCompletionSealImportResponseV1:
    """Verify and append one externally sealed Gate 1E result by strict CAS."""

    try:
        return seal_import_editable_mvp_completion(
            repository,
            deck_id=deck_id,
            request=request,
        )
    except EditableMvpCompletionError as error:
        raise HTTPException(
            status_code=error.status_code,
            detail={"code": error.code},
        ) from error


@AUTHORED_VNEXT_GENERATION_ROUTER.get(
    "/operations/{operation_id}",
    response_model=OperationAccepted,
)
def get_operation(
    repository: Repository,
    operation_id: str = Path(pattern=r"^operation:[a-z0-9][a-z0-9._-]*$"),
) -> OperationAccepted:
    try:
        row = repository.get_operation_row(operation_id)
        return OperationAccepted(
            operation_id=row.operation_id,
            deck_id=row.deck_id,
            kind=row.kind,
            state=row.state,
            fencing_token=row.fencing_token,
            attempt_count=row.attempt_count,
            checkpoint=row.checkpoint_payload,
            result=row.result_payload,
            error_code=row.error_code,
        )
    except Exception as error:
        raise _error(error) from error
