"""Lease-fenced public API for authored-vNext generation and regeneration."""

from __future__ import annotations

from functools import lru_cache
import os
from typing import Annotated, Callable, TypeVar
import uuid

from fastapi import APIRouter, Depends, HTTPException, Path
from pydantic import BaseModel

from models.authored_vnext.deck import (
    GenerationResult,
    DeckMutationResult,
    MasterDigestPin,
    RegenerationResult,
    RollbackResult,
)
from services.authored_vnext.generation.core_bridge import DurableExportPlanBridge
from services.authored_vnext.generation.errors import GenerationPipelineError
from services.authored_vnext.generation.pipeline import AuthoredVNextGenerationPipeline
from services.authored_vnext.generation.mutations import reorder_deck_slides
from services.authored_vnext.generation.sql_repository import SqlGenerationRepository
from services.authored_vnext.integration.sql_repository import AuthoredVNextSqlRepository
from services.authored_vnext.master.digests import canonical_digest
from utils.owner_context import get_current_owner_id

from .models import (
    CandidateResponse,
    DeckResponse,
    GenerateOperationRequest,
    OperationAccepted,
    PublishCandidateRequest,
    RegenerateOperationRequest,
    ReorderOperationRequest,
    ResumeOperationRequest,
    RollbackOperationRequest,
)


def _enabled() -> bool:
    return os.getenv("AUTHORED_VNEXT_GENERATION_ENABLED", "false").lower() == "true"


def _require_enabled() -> None:
    if not _enabled():
        raise HTTPException(status_code=404, detail="authored_vnext_generation_disabled")


@lru_cache(maxsize=32)
def _repository_for_owner(owner_id: str) -> SqlGenerationRepository:
    owner_uuid = uuid.UUID(owner_id)
    base = AuthoredVNextSqlRepository.from_application_database(
        owner_scope=owner_id,
        tenant_id=os.getenv("AUTHORED_VNEXT_TENANT_ID", "default"),
        owner_id=owner_uuid,
    )
    return SqlGenerationRepository(base)


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
    command = request.command
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
