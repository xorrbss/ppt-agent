"""Feature-gated DeckMasterContract vertical slice endpoints."""

from __future__ import annotations

import asyncio
from functools import lru_cache
import os
from pathlib import Path
from typing import Annotated
import uuid

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    Path as ApiPath,
    Query,
    UploadFile,
    status,
)

from models.authored_vnext.master.base import stable_slug
from models.authored_vnext.master.ai_import import (
    AiMasterAnalysis,
    SaveAiMasterAnalysisRequest,
    UpdateAiMasterAnalysisRequest,
)
from models.authored_vnext.master.operations import (
    CreateDeckRequest,
    CreateDraftRequest,
    ApplyMasterToPresentationRequest,
    ApplyMasterToPresentationResponse,
    CompanyStandardDefaultResponse,
    LegacyMasterApplyPin,
    MasterCatalogPage,
    MasterCasRequest,
    MasterRevisionResponse,
    PreviewRequest,
    PreviewResponse,
    RegenerateBodyRequest,
    RegenerateResponse,
    RegisterMasterRequest,
    StatusResponse,
    ValidationSummary,
)
from models.authored_vnext.master.slide import DeckSnapshot
from services.authored_vnext.master.errors import (
    DeckAlreadyExistsError,
    DeckDigestConflictError,
    DeckNotFoundError,
    DeckRevisionConflictError,
    MasterDigestConflictError,
    MasterNotFoundError,
    MasterNotDraftError,
    MasterNotPublishedError,
    MasterRevisionConflictError,
    MasterServiceError,
    MasterValidationError,
    SlideNotFoundError,
)
from services.authored_vnext.master.file_repository import (
    FileAuthoredVNextMasterRepository,
)
from services.authored_vnext.master.service import AuthoredVNextMasterService
from services.authored_vnext.master.ai_import import (
    AiImportError,
    AiMasterAnalysisStore,
    MAX_PPTX_BYTES,
    PptxMasterAnalyzer,
    apply_layout_actions,
    build_contract_draft,
    dedupe_layout_candidates,
    package_media,
    refresh_analysis_derived,
    validate_reapply,
)
from services.authored_vnext.master.assets import OwnerMasterAssetStore
from services.authored_vnext.generation.legacy_bridge import (
    apply_master_to_legacy_presentation,
)
from services.authored_vnext.generation.legacy_pin import resolve_legacy_master_pin
from services.authored_vnext.integration.sql_repository import (
    AuthoredVNextSqlRepository,
)
from utils.owner_context import get_current_owner_id
from .asset_router import (
    AUTHORED_VNEXT_MASTER_ASSET_ROUTER,
    get_master_asset_store,
)


def authored_vnext_master_enabled() -> bool:
    return os.getenv("AUTHORED_VNEXT_MASTER_ENABLED", "true").strip().lower() == "true"


def _require_enabled() -> None:
    if not authored_vnext_master_enabled():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="authored_vnext_master_disabled",
        )


@lru_cache(maxsize=32)
def _master_service_for_scope(
    owner_scope: str, owner_id: str | None
) -> AuthoredVNextMasterService:
    mode = os.getenv("AUTHORED_VNEXT_REPOSITORY_MODE", "sql").strip().lower()
    tenant_id = os.getenv("AUTHORED_VNEXT_TENANT_ID", "default")
    if mode == "file":
        environment = os.getenv("ENVIRONMENT", "production").strip().lower()
        if environment not in {"dev", "development", "local", "test"}:
            raise RuntimeError("authored_vnext_file_repository_forbidden_in_production")
        app_data = Path(os.getenv("APP_DATA_DIRECTORY", "app_data"))
        repository = FileAuthoredVNextMasterRepository(
            app_data
            / "authored_vnext"
            / "master"
            / stable_slug(tenant_id)
            / stable_slug(owner_scope)
        )
    elif mode == "sql":
        repository = AuthoredVNextSqlRepository.from_application_database(
            owner_scope=owner_scope,
            tenant_id=tenant_id,
            owner_id=(uuid.UUID(owner_id) if owner_id else None),
        )
    else:
        raise RuntimeError("authored_vnext_repository_mode_invalid")
    return AuthoredVNextMasterService(repository)


def get_authored_vnext_master_service() -> AuthoredVNextMasterService:
    owner_id = get_current_owner_id()
    if owner_id is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="authored_vnext_owner_required",
        )
    scope = str(owner_id)
    return _master_service_for_scope(scope, scope)


ServiceDependency = Annotated[
    AuthoredVNextMasterService,
    Depends(get_authored_vnext_master_service),
]


@lru_cache(maxsize=64)
def _analysis_store_for_scope(root: str, owner_scope: str) -> AiMasterAnalysisStore:
    return AiMasterAnalysisStore(
        Path(root)
        / "authored_vnext"
        / "ai-master-import"
        / "users"
        / stable_slug(owner_scope)
    )


def get_ai_master_analysis_store() -> AiMasterAnalysisStore:
    owner_id = get_current_owner_id()
    if owner_id is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="authored_vnext_owner_required",
        )
    root = str(Path(os.getenv("APP_DATA_DIRECTORY", "app_data")).resolve())
    return _analysis_store_for_scope(root, str(owner_id))


AnalysisStoreDependency = Annotated[
    AiMasterAnalysisStore,
    Depends(get_ai_master_analysis_store),
]
MasterAssetStoreDependency = Annotated[
    OwnerMasterAssetStore,
    Depends(get_master_asset_store),
]


AUTHORED_VNEXT_MASTER_ROUTER = APIRouter(
    prefix="/authored-vnext/master",
    tags=["Authored vNext Master"],
    dependencies=[Depends(_require_enabled)],
)
AUTHORED_VNEXT_MASTER_ROUTER.include_router(AUTHORED_VNEXT_MASTER_ASSET_ROUTER)


def _http_error(error: MasterServiceError) -> HTTPException:
    if isinstance(error, (MasterNotFoundError, DeckNotFoundError, SlideNotFoundError)):
        status_code = status.HTTP_404_NOT_FOUND
    elif isinstance(
        error,
        (
            MasterRevisionConflictError,
            MasterDigestConflictError,
            DeckRevisionConflictError,
            DeckDigestConflictError,
            DeckAlreadyExistsError,
            MasterNotPublishedError,
            MasterNotDraftError,
        ),
    ):
        status_code = status.HTTP_409_CONFLICT
    elif isinstance(error, MasterValidationError):
        status_code = status.HTTP_422_UNPROCESSABLE_CONTENT
    else:
        status_code = status.HTTP_409_CONFLICT
    return HTTPException(
        status_code=status_code,
        detail={"code": error.code},
    )


@AUTHORED_VNEXT_MASTER_ROUTER.get("/status", response_model=StatusResponse)
def master_status() -> StatusResponse:
    return StatusResponse(
        enabled=True,
        contract_version="deckmaster-contract/v1",
        slide_spec_version="slidespec/v2",
        compiler_port="OoxmlMasterCompilerPort",
    )


@AUTHORED_VNEXT_MASTER_ROUTER.get(
    "/company-standard/default",
    response_model=CompanyStandardDefaultResponse,
)
def company_standard_default(
    service: ServiceDependency,
) -> CompanyStandardDefaultResponse:
    """Return the owner's published company standard for first-use auto apply."""

    master_id = os.getenv(
        "AUTHORED_VNEXT_DEFAULT_COMPANY_STANDARD_ID",
        "master:ibiz-proposal-blue",
    ).strip()
    if not master_id:
        return CompanyStandardDefaultResponse(company_standard=None)
    try:
        published = service.get_published_revision(master_id)
    except MasterNotFoundError:
        return CompanyStandardDefaultResponse(company_standard=None)
    return CompanyStandardDefaultResponse(
        company_standard=LegacyMasterApplyPin(
            master_id=published.master_id,
            revision=published.revision,
            digest=published.digest,
        ),
        name=published.contract.name,
    )


def _ai_import_http_error(error: AiImportError) -> HTTPException:
    if error.code == "authored_vnext_ai_master_analysis_not_found":
        status_code = status.HTTP_404_NOT_FOUND
    elif error.code in {
        "authored_vnext_ai_master_legacy_ppt_unsupported",
        "authored_vnext_ai_master_pptx_required",
    }:
        status_code = status.HTTP_415_UNSUPPORTED_MEDIA_TYPE
    elif error.code.endswith("_corrupt") or error.code.endswith("_digest_mismatch"):
        status_code = status.HTTP_409_CONFLICT
    else:
        status_code = status.HTTP_422_UNPROCESSABLE_CONTENT
    return HTTPException(status_code=status_code, detail={"code": error.code})


def _finalize_ai_analysis_layouts(
    current: AiMasterAnalysis,
    store: AiMasterAnalysisStore,
) -> tuple[AiMasterAnalysis, bool]:
    """Apply the idempotent pre-registration body-layout dedupe boundary."""

    before_ids = [item.id for item in current.layout_candidates]
    feature_sidecar, sidecar_reason = store.ensure_feature_sidecar(
        current.analysis_id
    )
    finalized = dedupe_layout_candidates(current, feature_sidecar)
    warnings = [
        item
        for item in finalized.warnings
        if not item.startswith("featureSidecarReextracted:")
    ]
    if sidecar_reason is not None:
        warning = f"featureSidecarReextracted:{sidecar_reason}"
        warnings.append(warning)
    finalized = refresh_analysis_derived(
        finalized.model_copy(
            update={
                "algorithm_version": feature_sidecar.algorithm_version,
                "feature_sidecar_status": (
                    "reextracted" if sidecar_reason is not None else "ready"
                ),
                "warnings": warnings,
            }
        )
    )
    changed = before_ids != [item.id for item in finalized.layout_candidates]
    if changed:
        contract = build_contract_draft(finalized)
        if finalized.master_revision is not None:
            contract = contract.model_copy(
                update={"master_id": finalized.master_revision.master_id}
            )
        finalized = finalized.model_copy(update={"contract_draft": contract})
    return finalized, changed


@AUTHORED_VNEXT_MASTER_ROUTER.post(
    "/ai-import/analyses",
    response_model=AiMasterAnalysis,
    status_code=status.HTTP_201_CREATED,
)
async def analyze_pptx_for_master(
    store: AnalysisStoreDependency,
    asset_store: MasterAssetStoreDependency,
    file: Annotated[UploadFile, File()],
    purpose: Annotated[str, Form()],
) -> AiMasterAnalysis:
    """Analyze one PPTX locally and persist source/evidence for WYSIWYG review."""

    filename = Path(file.filename or "source.pptx").name
    suffix = Path(filename).suffix.lower()
    if suffix == ".ppt":
        raise _ai_import_http_error(
            AiImportError("authored_vnext_ai_master_legacy_ppt_unsupported")
        )
    if suffix != ".pptx":
        raise _ai_import_http_error(
            AiImportError("authored_vnext_ai_master_pptx_required")
        )
    value = await file.read(MAX_PPTX_BYTES + 1)
    if len(value) > MAX_PPTX_BYTES or await file.read(1):
        raise _ai_import_http_error(
            AiImportError("authored_vnext_ai_master_file_size_invalid")
        )
    try:
        analysis = await asyncio.to_thread(
            PptxMasterAnalyzer().analyze,
            value,
            filename,
            purpose,
            None,
            asset_store.publish_png,
        )
        await asyncio.to_thread(
            store.save,
            analysis,
            value,
            package_media(value),
        )
        return analysis
    except AiImportError as error:
        raise _ai_import_http_error(error) from error


@AUTHORED_VNEXT_MASTER_ROUTER.get(
    "/ai-import/analyses/{analysis_id}",
    response_model=AiMasterAnalysis,
)
def get_ai_master_analysis(
    store: AnalysisStoreDependency,
    analysis_id: str = ApiPath(pattern=r"^[0-9a-f]{32}$"),
) -> AiMasterAnalysis:
    try:
        return store.get(analysis_id)
    except AiImportError as error:
        raise _ai_import_http_error(error) from error


@AUTHORED_VNEXT_MASTER_ROUTER.patch(
    "/ai-import/analyses/{analysis_id}",
    response_model=AiMasterAnalysis,
)
def update_ai_master_analysis(
    request: UpdateAiMasterAnalysisRequest,
    store: AnalysisStoreDependency,
    asset_store: MasterAssetStoreDependency,
    analysis_id: str = ApiPath(pattern=r"^[0-9a-f]{32}$"),
) -> AiMasterAnalysis:
    try:
        current = store.get(analysis_id)
        actions = request.layout_actions or []
        cumulative_actions = (
            len(actions) >= len(current.layout_actions)
            and actions[: len(current.layout_actions)] == current.layout_actions
        )
        incoming_actions = (
            actions[len(current.layout_actions) :]
            if cumulative_actions
            else actions
        )
        reanalyzing = any(
            action.action == "reanalyze" for action in incoming_actions
        )
        if reanalyzing:
            previous_revision = current.master_revision
            previous_actions = list(current.layout_actions)
            reanalyzed = PptxMasterAnalyzer().analyze(
                store.source(analysis_id),
                current.source.filename,
                current.purpose,
                analysis_id,
                asset_store.publish_png,
            )
            previous = {item.candidate_id: item for item in current.decisions}
            decisions = [
                previous.get(item.candidate_id, item) for item in reanalyzed.decisions
            ]
            current = reanalyzed.model_copy(
                update={
                    "decisions": decisions,
                    "master_revision": previous_revision,
                }
            )
            if cumulative_actions:
                history_actions = actions
                replay_actions = [
                    action for action in actions if action.action != "reanalyze"
                ]
            else:
                history_actions = [*previous_actions, *actions]
                replay_actions = [
                    action
                    for action in history_actions
                    if action.action != "reanalyze"
                ]
        else:
            replay_actions = incoming_actions
        if request.decisions is not None:
            known = {item.id for item in current.repeated_elements}
            if any(item.candidate_id not in known for item in request.decisions):
                raise AiImportError("authored_vnext_ai_master_candidate_unknown")
            current = current.model_copy(update={"decisions": request.decisions})
        source_aware = reanalyzing or any(
            action.action in {"merge", "split"} for action in replay_actions
        )
        feature_sidecar = None
        sidecar_reason = None
        if source_aware:
            feature_sidecar, sidecar_reason = store.ensure_feature_sidecar(
                analysis_id
            )
            if sidecar_reason is not None:
                warning = f"featureSidecarReextracted:{sidecar_reason}"
                current = current.model_copy(
                    update={
                        "algorithm_version": feature_sidecar.algorithm_version,
                        "feature_sidecar_status": "reextracted",
                        "warnings": [
                            *[item for item in current.warnings if item != warning],
                            warning,
                        ],
                    }
                )
            else:
                current = current.model_copy(
                    update={
                        "algorithm_version": feature_sidecar.algorithm_version,
                        "feature_sidecar_status": "ready",
                    }
                )
        if replay_actions:
            current = apply_layout_actions(
                current, replay_actions, feature_sidecar=feature_sidecar
            )
        if reanalyzing:
            current = current.model_copy(update={"layout_actions": history_actions})
        low_confidence = [
            f"layout:{item.id}"
            for item in current.layout_candidates
            if item.confidence < 0.65
        ] + [
            f"element:{item.id}"
            for item in current.repeated_elements
            if item.confidence < 0.65
        ]
        current = current.model_copy(
            update={
                "summary": current.summary.model_copy(
                    update={
                        "layout_candidates": len(current.layout_candidates),
                        "low_confidence_count": len(low_confidence),
                    }
                ),
                "low_confidence": low_confidence,
                "status": (
                    "partial"
                    if current.warnings or low_confidence
                    else "completed"
                ),
            }
        )
        contract = build_contract_draft(current)
        if current.master_revision is not None:
            contract = contract.model_copy(
                update={"master_id": current.master_revision.master_id}
            )
        current = current.model_copy(update={"contract_draft": contract})
        store.save(current)
        return current
    except AiImportError as error:
        raise _ai_import_http_error(error) from error


@AUTHORED_VNEXT_MASTER_ROUTER.post(
    "/ai-import/analyses/{analysis_id}/validate",
    response_model=AiMasterAnalysis,
)
def validate_ai_master_analysis(
    store: AnalysisStoreDependency,
    analysis_id: str = ApiPath(pattern=r"^[0-9a-f]{32}$"),
) -> AiMasterAnalysis:
    try:
        current = store.get(analysis_id)
        current, _ = _finalize_ai_analysis_layouts(current, store)
        validation = validate_reapply(current)
        updated = current.model_copy(update={"validation": validation})
        store.save(updated)
        return updated
    except AiImportError as error:
        raise _ai_import_http_error(error) from error


@AUTHORED_VNEXT_MASTER_ROUTER.post(
    "/ai-import/analyses/{analysis_id}/save",
    response_model=AiMasterAnalysis,
)
def save_ai_master_analysis(
    request: SaveAiMasterAnalysisRequest,
    service: ServiceDependency,
    store: AnalysisStoreDependency,
    analysis_id: str = ApiPath(pattern=r"^[0-9a-f]{32}$"),
) -> AiMasterAnalysis:
    """Seal the reviewed semantic draft through the existing revision/CAS service."""

    try:
        current = store.get(analysis_id)
        current, dedupe_changed = _finalize_ai_analysis_layouts(current, store)
        if dedupe_changed and request.contract is not None:
            store.save(current)
            raise AiImportError(
                "authored_vnext_ai_master_dedupe_review_required"
            )
        contract = request.contract or build_contract_draft(current)
        expected_revision = request.expected_revision
        expected_digest = request.expected_digest
        master_id = request.master_id
        if current.master_revision is not None:
            persisted_master_id = current.master_revision.master_id
            if master_id is not None and master_id != persisted_master_id:
                raise AiImportError(
                    "authored_vnext_ai_master_master_id_mismatch"
                )
            master_id = persisted_master_id
            if expected_revision is None:
                expected_revision = current.master_revision.revision
                expected_digest = current.master_revision.digest
        if master_id is not None:
            if contract.master_id != master_id:
                raise AiImportError(
                    "authored_vnext_ai_master_master_id_mismatch"
                )
            revision = service.create_draft(
                CreateDraftRequest(
                    contract=contract,
                    expected_revision=expected_revision,
                    expected_digest=expected_digest,
                )
            )
        elif expected_revision is not None:
            raise AiImportError("authored_vnext_ai_master_master_id_required")
        else:
            revision = service.register_master(
                RegisterMasterRequest(contract=contract)
            )
        saved = current.model_copy(
            update={
                "contract_draft": revision.contract,
                "master_revision": revision,
            }
        )
        store.save(saved)
        return saved
    except AiImportError as error:
        raise _ai_import_http_error(error) from error
    except MasterServiceError as error:
        raise _http_error(error) from error


@AUTHORED_VNEXT_MASTER_ROUTER.get(
    "/masters",
    response_model=MasterCatalogPage,
)
def list_masters(
    service: ServiceDependency,
    cursor: Annotated[str | None, Query(max_length=1024)] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> MasterCatalogPage:
    try:
        return service.list_masters(cursor=cursor, limit=limit)
    except ValueError as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={"code": "authored_vnext_master_catalog_cursor_invalid"},
        ) from error


@AUTHORED_VNEXT_MASTER_ROUTER.post(
    "/masters/register",
    response_model=MasterRevisionResponse,
    status_code=status.HTTP_201_CREATED,
)
def register_master(
    request: RegisterMasterRequest,
    service: ServiceDependency,
) -> MasterRevisionResponse:
    try:
        return service.register_master(request)
    except MasterServiceError as error:
        raise _http_error(error) from error


@AUTHORED_VNEXT_MASTER_ROUTER.post(
    "/drafts",
    response_model=MasterRevisionResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_draft(
    request: CreateDraftRequest,
    service: ServiceDependency,
) -> MasterRevisionResponse:
    try:
        return service.create_draft(request)
    except MasterServiceError as error:
        raise _http_error(error) from error


@AUTHORED_VNEXT_MASTER_ROUTER.get(
    "/masters/{master_id}/revisions/{revision}",
    response_model=MasterRevisionResponse,
)
def get_master_revision(
    service: ServiceDependency,
    master_id: str = ApiPath(pattern=r"^master:[a-z0-9][a-z0-9._-]*$"),
    revision: int = ApiPath(ge=1),
) -> MasterRevisionResponse:
    try:
        return service.get_revision(master_id, revision)
    except MasterServiceError as error:
        raise _http_error(error) from error


@AUTHORED_VNEXT_MASTER_ROUTER.get(
    "/masters/{master_id}/head",
    response_model=MasterRevisionResponse,
)
def get_master_head(
    service: ServiceDependency,
    master_id: str = ApiPath(pattern=r"^master:[a-z0-9][a-z0-9._-]*$"),
) -> MasterRevisionResponse:
    """Return the owner's current head so the existing UI can reopen it safely."""

    try:
        return service.get_revision(master_id)
    except MasterServiceError as error:
        raise _http_error(error) from error


@AUTHORED_VNEXT_MASTER_ROUTER.get(
    "/masters/{master_id}/published",
    response_model=MasterRevisionResponse,
)
def get_published_master(
    service: ServiceDependency,
    master_id: str = ApiPath(pattern=r"^master:[a-z0-9][a-z0-9._-]*$"),
) -> MasterRevisionResponse:
    try:
        return service.get_published_revision(master_id)
    except MasterServiceError as error:
        raise _http_error(error) from error


@AUTHORED_VNEXT_MASTER_ROUTER.post(
    "/presentations/{presentation_id}/apply",
    response_model=ApplyMasterToPresentationResponse,
)
async def apply_master_to_presentation(
    request: ApplyMasterToPresentationRequest,
    presentation_id: uuid.UUID,
) -> ApplyMasterToPresentationResponse:
    owner_id = get_current_owner_id()
    if owner_id is None:
        raise HTTPException(status_code=401, detail="authored_vnext_owner_required")
    try:
        resolved = await asyncio.to_thread(
            resolve_legacy_master_pin,
            owner_id=owner_id,
            requested=request.master_pin,
        )
        applied = await apply_master_to_legacy_presentation(
            presentation_id=presentation_id,
            expected_presentation_revision=request.expected_presentation_revision,
            expected_deck_revision=request.expected_deck_revision,
            expected_deck_digest=request.expected_deck_digest,
            requested_pin=request.master_pin,
            idempotency_key=request.idempotency_key,
            resolved=resolved,
        )
        return ApplyMasterToPresentationResponse(
            presentation_id=str(presentation_id),
            presentation_revision=applied.presentation_revision,
            deck_id=applied.deck.deck_id,
            deck_revision=applied.deck.revision,
            deck_digest=applied.deck.digest,
            master=request.master_pin,
        )
    except Exception as error:
        code = getattr(error, "code", str(error))
        if not isinstance(code, str) or not code.startswith("authored_vnext_"):
            code = "authored_vnext_master_apply_conflict"
        raise HTTPException(status_code=409, detail={"code": code}) from error


@AUTHORED_VNEXT_MASTER_ROUTER.post(
    "/drafts/{master_id}/validate",
    response_model=ValidationSummary,
)
def validate_draft(
    request: MasterCasRequest,
    service: ServiceDependency,
    master_id: str = ApiPath(pattern=r"^master:[a-z0-9][a-z0-9._-]*$"),
) -> ValidationSummary:
    try:
        return service.validate_draft(master_id, request)
    except MasterServiceError as error:
        raise _http_error(error) from error


@AUTHORED_VNEXT_MASTER_ROUTER.post(
    "/drafts/{master_id}/publish",
    response_model=MasterRevisionResponse,
)
def publish_draft(
    request: MasterCasRequest,
    service: ServiceDependency,
    master_id: str = ApiPath(pattern=r"^master:[a-z0-9][a-z0-9._-]*$"),
) -> MasterRevisionResponse:
    try:
        return service.publish(master_id, request)
    except MasterServiceError as error:
        raise _http_error(error) from error


@AUTHORED_VNEXT_MASTER_ROUTER.post("/preview", response_model=PreviewResponse)
def preview(
    request: PreviewRequest,
    service: ServiceDependency,
) -> PreviewResponse:
    try:
        return service.preview(request)
    except (MasterServiceError, ValueError) as error:
        if isinstance(error, MasterServiceError):
            raise _http_error(error) from error
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={"code": "authored_vnext_master_preview_invalid"},
        ) from error


@AUTHORED_VNEXT_MASTER_ROUTER.post(
    "/decks",
    response_model=DeckSnapshot,
    status_code=status.HTTP_201_CREATED,
)
def create_deck(
    request: CreateDeckRequest,
    service: ServiceDependency,
) -> DeckSnapshot:
    try:
        return service.create_deck(request)
    except (MasterServiceError, ValueError) as error:
        if isinstance(error, MasterServiceError):
            raise _http_error(error) from error
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={"code": "authored_vnext_deck_invalid"},
        ) from error


@AUTHORED_VNEXT_MASTER_ROUTER.post(
    "/decks/{deck_id}/slides/{slide_id}/regenerate-body",
    response_model=RegenerateResponse,
)
def regenerate_body(
    request: RegenerateBodyRequest,
    service: ServiceDependency,
    deck_id: str = ApiPath(pattern=r"^deck:[a-z0-9][a-z0-9._-]*$"),
    slide_id: str = ApiPath(pattern=r"^slide:[a-z0-9][a-z0-9._-]*$"),
) -> RegenerateResponse:
    try:
        return service.regenerate_body(deck_id, slide_id, request)
    except MasterServiceError as error:
        raise _http_error(error) from error
