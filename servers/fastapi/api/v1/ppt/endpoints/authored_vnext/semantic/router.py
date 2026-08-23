"""Versioned read and export-binding API for Authored semantic emission."""

from __future__ import annotations

import os
import uuid

from fastapi import APIRouter, Depends, HTTPException, Path, Query, status
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from models.authored_vnext.semantic.contracts import (
    ActiveAuthoredEmissionResponseV1,
    ExportAttemptDigestBindingV1,
    WorkbenchCapabilityProjectionV1,
)
from models.authored_vnext.semantic.lifecycle import (
    AuthoredEmissionLifecycleReceiptV1,
    AuthoredLegacyCutoverRequestV1,
    AuthoredManifestRollbackRequestV1,
    AuthoredOrderedRemakeRequestV1,
)
from models.authored_vnext.semantic.readiness import (
    CapabilityReceiptIssueRequestV2,
    CapabilityReceiptRevokeRequestV2,
    CapabilityReceiptV2,
    ReleasePinnedExportAttemptDigestBindingV2,
    VisualEvidenceArtifactPublishRequestV2,
    VisualEvidenceArtifactV2,
    WorkbenchCapabilityProjectionV2,
)
from models.authored_vnext.semantic.release_profile_v3 import (
    WorkbenchCapabilityProjectionV3,
)
from models.authored_vnext.semantic.release_authority_v3 import (
    CapabilityReceiptIssueRequestV3,
    ComQualificationClaimRequestV3,
    ComQualificationCreateRequestV3,
    ComQualificationJobV3,
    ComQualificationTransitionRequestV3,
    DurableArtifactBindRequestV3,
    DurableArtifactPutRequestV3,
    DurableArtifactRefV3,
    EvidenceBundleCompletionCreateRequestV3,
    EvidenceBundleCompletionSealRequestV3,
    EvidenceBundleCompletionV3,
    EvidenceGraphRootCreateRequestV3,
    EvidenceGraphRootV3,
    Gate0StructuralCaseExecutionV1,
)
from models.authored_vnext.semantic.release_evidence_v3 import (
    CiEvidenceArtifactPublishRequestV3,
    CiEvidenceArtifactV3,
)
from services.authored_vnext.semantic.canonical import SemanticContractError
from services.authored_vnext.semantic.export_binding import bind_existing_export_attempt
from services.authored_vnext.semantic.query import (
    get_active_authored_emission,
    get_workbench_capability_projection,
)
from services.authored_vnext.semantic.lifecycle import (
    cutover_authored_emission_to_legacy,
    remake_ordered_authored_emission,
    rollback_authored_emission_manifest,
)
from services.authored_vnext.semantic.readiness import (
    get_workbench_capability_projection_v2,
    issue_capability_receipt,
    publish_visual_evidence_artifact,
    revoke_capability_receipt,
)
from services.authored_vnext.semantic.readiness_v3 import (
    get_workbench_capability_projection_v3,
    issue_capability_receipt_v3,
)
from services.authored_vnext.semantic.release_evidence_v3 import (
    publish_ci_evidence_artifact_v3,
)
from services.authored_vnext.semantic.release_authority_v3 import (
    issue_actual_product_capability_receipt_v3,
    release_trust_readback_v3,
    sign_server_readback_v3,
)
from services.authored_vnext.semantic.qualification_v3 import (
    bind_durable_artifact_v3,
    claim_com_qualification_job_v3,
    create_com_qualification_job_v3,
    get_com_qualification_job_v3,
    put_durable_artifact_v3,
    transition_com_qualification_job_v3,
)
from services.authored_vnext.semantic.completion_v3 import (
    create_evidence_bundle_completion_v3,
    create_evidence_graph_root_v3,
    get_evidence_bundle_completion_v3,
    get_evidence_graph_root_v3,
    seal_evidence_bundle_completion_v3,
)
from services.authored_vnext.semantic.gate0_structural_v3 import (
    execute_gate0_structural_case_v3,
    gate0_structural_sealed_completion_count_v3,
    read_gate0_structural_evidence_v3,
)
from services.database import get_async_session
from utils.owner_context import get_current_owner_id, get_current_owner_is_admin


AUTHORED_SEMANTIC_ROUTER = APIRouter(tags=["Authored Semantic Emission"])


def _owner() -> uuid.UUID:
    owner_id = get_current_owner_id()
    if owner_id is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"code": "authored_semantic_owner_required"},
        )
    return owner_id


def _error(error: SemanticContractError) -> HTTPException:
    if error.code in {
        "authored_semantic_presentation_not_found",
        "authored_semantic_export_attempt_not_found",
    }:
        return HTTPException(status_code=404, detail={"code": error.code})
    if error.code in {
        "authored_semantic_cas_conflict",
        "authored_semantic_export_stale_manifest",
        "authored_semantic_export_attempt_already_bound",
        "authored_semantic_lifecycle_stale_revision",
        "authored_semantic_lifecycle_stale_generation",
        "authored_semantic_lifecycle_stale_manifest",
        "authored_semantic_lifecycle_attempt_reused",
        "authored_semantic_lifecycle_cas_conflict",
        "authored_semantic_rollback_slide_cas_conflict",
        "authored_evidence_nonce_replay",
        "authored_release_artifact_cas_conflict",
        "authored_release_artifact_binding_mismatch",
        "authored_com_qualification_claim_conflict",
        "authored_com_qualification_stale_cas",
        "authored_evidence_graph_cas_conflict",
        "authored_evidence_completion_cas_conflict",
        "authored_evidence_completion_second_seal_conflict",
        "authored_evidence_completion_stale_cas",
        "authored_gate0_structural_case_replay_conflict",
    }:
        return HTTPException(status_code=409, detail={"code": error.code})
    if error.code == "authored_capability_receipt_admin_required":
        return HTTPException(status_code=403, detail={"code": error.code})
    if error.code in {
        "authored_evidence_authority_scope_invalid",
        "authored_evidence_signer_not_allowed",
        "authored_evidence_signer_trust_unconfigured",
        "authored_evidence_signature_invalid",
    }:
        return HTTPException(status_code=403, detail={"code": error.code})
    return HTTPException(status_code=422, detail={"code": error.code})


def _admin_owner() -> uuid.UUID:
    owner_id = _owner()
    if not get_current_owner_is_admin():
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"code": "authored_capability_receipt_admin_required"},
        )
    return owner_id


@AUTHORED_SEMANTIC_ROUTER.get(
    "/presentation/{presentation_id}/authored-emission/active",
    response_model=ActiveAuthoredEmissionResponseV1,
)
async def read_active_authored_emission(
    presentation_id: uuid.UUID = Path(),
    session: AsyncSession = Depends(get_async_session),
) -> ActiveAuthoredEmissionResponseV1:
    try:
        return await get_active_authored_emission(
            session,
            presentation_id=presentation_id,
            owner_id=_owner(),
            tenant_id=os.getenv("AUTHORED_VNEXT_TENANT_ID", "default"),
        )
    except SemanticContractError as error:
        raise _error(error) from error


@AUTHORED_SEMANTIC_ROUTER.get(
    "/presentation/{presentation_id}/authored-emission/capability-projection",
    response_model=WorkbenchCapabilityProjectionV1,
)
async def read_workbench_capability_projection(
    presentation_id: uuid.UUID = Path(),
    session: AsyncSession = Depends(get_async_session),
) -> WorkbenchCapabilityProjectionV1:
    try:
        return await get_workbench_capability_projection(
            session,
            presentation_id=presentation_id,
            owner_id=_owner(),
            tenant_id=os.getenv("AUTHORED_VNEXT_TENANT_ID", "default"),
        )
    except SemanticContractError as error:
        raise _error(error) from error


@AUTHORED_SEMANTIC_ROUTER.get(
    "/presentation/{presentation_id}/authored-emission/capability-projection-v2",
    response_model=WorkbenchCapabilityProjectionV2,
)
async def read_workbench_capability_projection_v2(
    presentation_id: uuid.UUID = Path(),
    session: AsyncSession = Depends(get_async_session),
) -> WorkbenchCapabilityProjectionV2:
    try:
        return await get_workbench_capability_projection_v2(
            session,
            presentation_id=presentation_id,
            owner_id=_owner(),
            tenant_id=os.getenv("AUTHORED_VNEXT_TENANT_ID", "default"),
        )
    except SemanticContractError as error:
        raise _error(error) from error


@AUTHORED_SEMANTIC_ROUTER.get(
    "/presentation/{presentation_id}/authored-emission/capability-projection-v3",
    response_model=WorkbenchCapabilityProjectionV3,
)
async def read_workbench_capability_projection_v3(
    presentation_id: uuid.UUID = Path(),
    session: AsyncSession = Depends(get_async_session),
) -> WorkbenchCapabilityProjectionV3:
    try:
        return await get_workbench_capability_projection_v3(
            session,
            presentation_id=presentation_id,
            owner_id=_owner(),
            tenant_id=os.getenv("AUTHORED_VNEXT_TENANT_ID", "default"),
        )
    except SemanticContractError as error:
        raise _error(error) from error


@AUTHORED_SEMANTIC_ROUTER.post(
    "/authored-emission/capability-receipts-v2",
    response_model=CapabilityReceiptV2,
    status_code=status.HTTP_201_CREATED,
)
async def create_capability_receipt_v2(
    request: CapabilityReceiptIssueRequestV2,
    session: AsyncSession = Depends(get_async_session),
) -> CapabilityReceiptV2:
    issuer_id = _admin_owner()
    try:
        receipt = await issue_capability_receipt(
            session,
            request=request,
            issuer_principal_id=issuer_id,
            issuer_is_admin=True,
            tenant_id=os.getenv("AUTHORED_VNEXT_TENANT_ID", "default"),
        )
        await session.commit()
        return receipt
    except SemanticContractError as error:
        await session.rollback()
        raise _error(error) from error


@AUTHORED_SEMANTIC_ROUTER.post(
    "/authored-emission/capability-receipts-v3",
    response_model=CapabilityReceiptV2,
    status_code=status.HTTP_201_CREATED,
)
async def create_capability_receipt_v3(
    request: CapabilityReceiptIssueRequestV3,
    session: AsyncSession = Depends(get_async_session),
) -> CapabilityReceiptV2:
    issuer_id = _admin_owner()
    try:
        receipt = await issue_actual_product_capability_receipt_v3(
            session,
            request=request,
            issuer_principal_id=issuer_id,
            issuer_is_admin=True,
            tenant_id=os.getenv("AUTHORED_VNEXT_TENANT_ID", "default"),
        )
        await session.commit()
        return receipt
    except SemanticContractError as error:
        await session.rollback()
        raise _error(error) from error


@AUTHORED_SEMANTIC_ROUTER.post(
    "/authored-emission/capability-receipts-structural-v3",
    response_model=CapabilityReceiptV2,
    status_code=status.HTTP_201_CREATED,
)
async def create_structural_capability_receipt_v3(
    request: CapabilityReceiptIssueRequestV2,
    session: AsyncSession = Depends(get_async_session),
) -> CapabilityReceiptV2:
    """Exercise the real validators without creating product authority."""

    issuer_id = _admin_owner()
    try:
        receipt = await issue_capability_receipt_v3(
            session,
            request=request,
            issuer_principal_id=issuer_id,
            issuer_is_admin=True,
            tenant_id=os.getenv("AUTHORED_VNEXT_TENANT_ID", "default"),
        )
        await session.commit()
        return receipt
    except SemanticContractError as error:
        await session.rollback()
        raise _error(error) from error


@AUTHORED_SEMANTIC_ROUTER.post(
    "/presentation/{presentation_id}/authored-emission/visual-evidence-artifacts-v2",
    response_model=VisualEvidenceArtifactV2,
    status_code=status.HTTP_201_CREATED,
)
async def create_visual_evidence_artifact_v2(
    request: VisualEvidenceArtifactPublishRequestV2,
    presentation_id: uuid.UUID = Path(),
    session: AsyncSession = Depends(get_async_session),
) -> VisualEvidenceArtifactV2:
    publisher_id = _admin_owner()
    try:
        artifact = await publish_visual_evidence_artifact(
            session,
            presentation_id=presentation_id,
            request=request,
            publisher_principal_id=publisher_id,
            publisher_is_admin=True,
            tenant_id=os.getenv("AUTHORED_VNEXT_TENANT_ID", "default"),
        )
        await session.commit()
        return artifact
    except SemanticContractError as error:
        await session.rollback()
        raise _error(error) from error


@AUTHORED_SEMANTIC_ROUTER.post(
    "/authored-emission/ci-evidence-artifacts-v3",
    response_model=CiEvidenceArtifactV3,
    status_code=status.HTTP_201_CREATED,
)
async def create_ci_evidence_artifact_v3(
    request: CiEvidenceArtifactPublishRequestV3,
    session: AsyncSession = Depends(get_async_session),
) -> CiEvidenceArtifactV3:
    publisher_id = _admin_owner()
    try:
        artifact = await publish_ci_evidence_artifact_v3(
            session,
            request=request,
            publisher_principal_id=publisher_id,
            publisher_is_admin=True,
            tenant_id=os.getenv("AUTHORED_VNEXT_TENANT_ID", "default"),
        )
        await session.commit()
        return artifact
    except SemanticContractError as error:
        await session.rollback()
        raise _error(error) from error


@AUTHORED_SEMANTIC_ROUTER.post(
    "/authored-emission/release-artifacts-v3",
    response_model=DurableArtifactRefV3,
    status_code=status.HTTP_201_CREATED,
)
async def create_release_artifact_v3(
    request: DurableArtifactPutRequestV3,
    session: AsyncSession = Depends(get_async_session),
) -> DurableArtifactRefV3:
    _admin_owner()
    try:
        artifact = await put_durable_artifact_v3(
            session,
            request=request,
            tenant_id=os.getenv("AUTHORED_VNEXT_TENANT_ID", "default"),
        )
        await session.commit()
        return artifact
    except SemanticContractError as error:
        await session.rollback()
        raise _error(error) from error


@AUTHORED_SEMANTIC_ROUTER.post(
    "/authored-emission/release-artifacts-v3/{artifact_id:path}/bind",
    response_model=DurableArtifactRefV3,
)
async def bind_release_artifact_v3(
    request: DurableArtifactBindRequestV3,
    artifact_id: str = Path(min_length=3, max_length=256),
    session: AsyncSession = Depends(get_async_session),
) -> DurableArtifactRefV3:
    _admin_owner()
    try:
        artifact = await bind_durable_artifact_v3(
            session,
            artifact_id=artifact_id,
            export_attempt_id=request.export_attempt_id,
            expected_version_id=request.expected_version_id,
            expected_sha256=request.expected_sha256,
            tenant_id=os.getenv("AUTHORED_VNEXT_TENANT_ID", "default"),
        )
        await session.commit()
        return artifact
    except SemanticContractError as error:
        await session.rollback()
        raise _error(error) from error


@AUTHORED_SEMANTIC_ROUTER.post(
    "/authored-emission/com-qualification-jobs-v3",
    response_model=ComQualificationJobV3,
    status_code=status.HTTP_201_CREATED,
)
async def create_com_qualification_v3(
    request: ComQualificationCreateRequestV3,
    session: AsyncSession = Depends(get_async_session),
) -> ComQualificationJobV3:
    _admin_owner()
    try:
        job = await create_com_qualification_job_v3(
            session,
            request=request,
            tenant_id=os.getenv("AUTHORED_VNEXT_TENANT_ID", "default"),
        )
        await session.commit()
        return job
    except SemanticContractError as error:
        await session.rollback()
        raise _error(error) from error


@AUTHORED_SEMANTIC_ROUTER.get(
    "/authored-emission/com-qualification-jobs-v3/{job_id}",
    response_model=ComQualificationJobV3,
)
async def read_com_qualification_v3(
    job_id: uuid.UUID = Path(),
    execution_nonce: str = Query(alias="executionNonce", min_length=16, max_length=128),
    session: AsyncSession = Depends(get_async_session),
) -> ComQualificationJobV3:
    _admin_owner()
    try:
        job = await get_com_qualification_job_v3(
            session,
            job_id=job_id,
            tenant_id=os.getenv("AUTHORED_VNEXT_TENANT_ID", "default"),
        )
        return ComQualificationJobV3.model_validate(
            sign_server_readback_v3(job.model_dump(mode="json", by_alias=True), execution_nonce=execution_nonce),
            strict=False,
        )
    except SemanticContractError as error:
        raise _error(error) from error


@AUTHORED_SEMANTIC_ROUTER.get("/authored-emission/release-trust-readback-v3")
async def read_release_trust_v3(
    execution_nonce: str = Query(alias="executionNonce", min_length=16, max_length=128),
) -> dict[str, object]:
    _admin_owner()
    try:
        return release_trust_readback_v3(execution_nonce=execution_nonce)
    except SemanticContractError as error:
        raise _error(error) from error


@AUTHORED_SEMANTIC_ROUTER.post(
    "/authored-emission/com-qualification-jobs-v3/{job_id}/claim",
    response_model=ComQualificationJobV3,
)
async def claim_com_qualification_v3(
    request: ComQualificationClaimRequestV3,
    job_id: uuid.UUID = Path(),
    session: AsyncSession = Depends(get_async_session),
) -> ComQualificationJobV3:
    _admin_owner()
    try:
        job = await claim_com_qualification_job_v3(
            session,
            job_id=job_id,
            worker_id=request.worker_id,
            tenant_id=os.getenv("AUTHORED_VNEXT_TENANT_ID", "default"),
        )
        await session.commit()
        return job
    except SemanticContractError as error:
        await session.rollback()
        raise _error(error) from error


@AUTHORED_SEMANTIC_ROUTER.post(
    "/authored-emission/com-qualification-jobs-v3/{job_id}/transition",
    response_model=ComQualificationJobV3,
)
async def transition_com_qualification_v3(
    request: ComQualificationTransitionRequestV3,
    job_id: uuid.UUID = Path(),
    session: AsyncSession = Depends(get_async_session),
) -> ComQualificationJobV3:
    _admin_owner()
    try:
        job = await transition_com_qualification_job_v3(
            session,
            job_id=job_id,
            request=request,
            tenant_id=os.getenv("AUTHORED_VNEXT_TENANT_ID", "default"),
        )
        await session.commit()
        return job
    except SemanticContractError as error:
        await session.rollback()
        raise _error(error) from error


@AUTHORED_SEMANTIC_ROUTER.post(
    "/authored-emission/evidence-graph-roots-v3",
    response_model=EvidenceGraphRootV3,
    status_code=status.HTTP_201_CREATED,
)
async def create_evidence_graph_root_api_v3(
    request: EvidenceGraphRootCreateRequestV3,
    session: AsyncSession = Depends(get_async_session),
) -> EvidenceGraphRootV3:
    owner_id = _admin_owner()
    try:
        root = await create_evidence_graph_root_v3(
            session,
            request=request,
            owner_id=owner_id,
            tenant_id=os.getenv("AUTHORED_VNEXT_TENANT_ID", "default"),
        )
        await session.commit()
        return root
    except SemanticContractError as error:
        await session.rollback()
        raise _error(error) from error


@AUTHORED_SEMANTIC_ROUTER.get(
    "/authored-emission/evidence-graph-roots-v3/{graph_root_id}",
    response_model=EvidenceGraphRootV3,
)
async def read_evidence_graph_root_api_v3(
    graph_root_id: uuid.UUID = Path(),
    execution_nonce: str = Query(alias="executionNonce", min_length=16, max_length=128),
    session: AsyncSession = Depends(get_async_session),
) -> EvidenceGraphRootV3:
    _admin_owner()
    try:
        graph = await get_evidence_graph_root_v3(
            session,
            graph_root_id=graph_root_id,
            tenant_id=os.getenv("AUTHORED_VNEXT_TENANT_ID", "default"),
        )
        return EvidenceGraphRootV3.model_validate(
            sign_server_readback_v3(graph.model_dump(mode="json", by_alias=True), execution_nonce=execution_nonce),
            strict=False,
        )
    except SemanticContractError as error:
        raise _error(error) from error


@AUTHORED_SEMANTIC_ROUTER.post(
    "/authored-emission/evidence-bundle-completions-v3",
    response_model=EvidenceBundleCompletionV3,
    status_code=status.HTTP_201_CREATED,
)
async def create_evidence_bundle_completion_api_v3(
    request: EvidenceBundleCompletionCreateRequestV3,
    session: AsyncSession = Depends(get_async_session),
) -> EvidenceBundleCompletionV3:
    _admin_owner()
    try:
        completion = await create_evidence_bundle_completion_v3(
            session,
            request=request,
            tenant_id=os.getenv("AUTHORED_VNEXT_TENANT_ID", "default"),
        )
        await session.commit()
        return completion
    except SemanticContractError as error:
        await session.rollback()
        raise _error(error) from error


@AUTHORED_SEMANTIC_ROUTER.get(
    "/authored-emission/evidence-bundle-completions-v3/{completion_id}",
    response_model=EvidenceBundleCompletionV3,
)
async def read_evidence_bundle_completion_api_v3(
    completion_id: uuid.UUID = Path(),
    execution_nonce: str = Query(alias="executionNonce", min_length=16, max_length=128),
    session: AsyncSession = Depends(get_async_session),
) -> EvidenceBundleCompletionV3:
    _admin_owner()
    try:
        completion = await get_evidence_bundle_completion_v3(
            session,
            completion_id=completion_id,
            tenant_id=os.getenv("AUTHORED_VNEXT_TENANT_ID", "default"),
        )
        return EvidenceBundleCompletionV3.model_validate(
            sign_server_readback_v3(
                completion.model_dump(mode="json", by_alias=True),
                execution_nonce=execution_nonce,
            ),
            strict=False,
        )
    except SemanticContractError as error:
        raise _error(error) from error


@AUTHORED_SEMANTIC_ROUTER.post(
    "/authored-emission/evidence-bundle-completions-v3/{completion_id}/seal",
    response_model=EvidenceBundleCompletionV3,
)
async def seal_evidence_bundle_completion_api_v3(
    request: EvidenceBundleCompletionSealRequestV3,
    completion_id: uuid.UUID = Path(),
    session: AsyncSession = Depends(get_async_session),
) -> EvidenceBundleCompletionV3:
    _admin_owner()
    try:
        completion = await seal_evidence_bundle_completion_v3(
            session,
            completion_id=completion_id,
            request=request,
            tenant_id=os.getenv("AUTHORED_VNEXT_TENANT_ID", "default"),
        )
        await session.commit()
        return completion
    except SemanticContractError as error:
        await session.rollback()
        raise _error(error) from error


@AUTHORED_SEMANTIC_ROUTER.post("/authored-emission/gate0-structural-v3/cases/{case_id}")
async def execute_gate0_structural_case_api_v3(
    request: Gate0StructuralCaseExecutionV1,
    case_id: str = Path(pattern=r"^g0\.[a-z0-9.-]+$"),
    session: AsyncSession = Depends(get_async_session),
) -> JSONResponse:
    _admin_owner()
    if request.case_id != case_id:
        raise HTTPException(
            status_code=422,
            detail={"code": "authored_gate0_structural_case_path_mismatch"},
        )
    try:
        http_status, payload = await execute_gate0_structural_case_v3(session, request=request)
        await session.commit()
        return JSONResponse(status_code=http_status, content=payload)
    except SemanticContractError as error:
        await session.rollback()
        raise _error(error) from error


@AUTHORED_SEMANTIC_ROUTER.get("/authored-emission/gate0-structural-v3/runs/{run_id}/cases/{case_id}/sealed-completions")
async def read_gate0_structural_completion_count_api_v3(
    run_id: uuid.UUID = Path(),
    case_id: str = Path(pattern=r"^g0\.[a-z0-9.-]+$"),
    session: AsyncSession = Depends(get_async_session),
) -> dict[str, int]:
    _admin_owner()
    count = await gate0_structural_sealed_completion_count_v3(session, run_id=run_id, case_id=case_id)
    return {"sealedCompletionCount": count}


@AUTHORED_SEMANTIC_ROUTER.get("/authored-emission/gate0-structural-v3/evidence/{evidence_id}")
async def read_gate0_structural_evidence_api_v3(
    evidence_id: uuid.UUID = Path(),
    session: AsyncSession = Depends(get_async_session),
) -> dict[str, object]:
    _admin_owner()
    evidence = await read_gate0_structural_evidence_v3(session, evidence_id=evidence_id)
    if evidence is None:
        raise HTTPException(
            status_code=404,
            detail={"code": "authored_gate0_structural_evidence_not_found"},
        )
    return evidence


@AUTHORED_SEMANTIC_ROUTER.post(
    "/authored-emission/capability-receipts-v2/{receipt_digest}/revoke",
    response_model=CapabilityReceiptV2,
    status_code=status.HTTP_201_CREATED,
)
async def revoke_capability_receipt_v2(
    request: CapabilityReceiptRevokeRequestV2,
    receipt_digest: str = Path(pattern=r"^sha256:[0-9a-f]{64}$"),
    session: AsyncSession = Depends(get_async_session),
) -> CapabilityReceiptV2:
    issuer_id = _admin_owner()
    try:
        receipt = await revoke_capability_receipt(
            session,
            receipt_digest=receipt_digest,
            request=request,
            issuer_principal_id=issuer_id,
            issuer_is_admin=True,
            tenant_id=os.getenv("AUTHORED_VNEXT_TENANT_ID", "default"),
        )
        await session.commit()
        return receipt
    except SemanticContractError as error:
        await session.rollback()
        raise _error(error) from error


@AUTHORED_SEMANTIC_ROUTER.post(
    "/presentation/{presentation_id}/authored-emission/export-attempt-bindings",
    response_model=ExportAttemptDigestBindingV1,
    status_code=status.HTTP_201_CREATED,
)
async def create_export_attempt_binding(
    binding: ExportAttemptDigestBindingV1,
    presentation_id: uuid.UUID = Path(),
    session: AsyncSession = Depends(get_async_session),
) -> ExportAttemptDigestBindingV1:
    if os.getenv("AUTHORED_SEMANTIC_BUNDLE_WRITES_ENABLED", "false").lower() != "true":
        raise HTTPException(
            status_code=404,
            detail={"code": "authored_semantic_bundle_writes_disabled"},
        )
    if binding.presentation_id != presentation_id:
        raise HTTPException(
            status_code=422,
            detail={"code": "authored_semantic_path_mismatch"},
        )
    try:
        sealed = await bind_existing_export_attempt(
            session,
            owner_id=_owner(),
            binding=binding,
            tenant_id=os.getenv("AUTHORED_VNEXT_TENANT_ID", "default"),
        )
        await session.commit()
        return sealed
    except SemanticContractError as error:
        await session.rollback()
        raise _error(error) from error


@AUTHORED_SEMANTIC_ROUTER.post(
    "/presentation/{presentation_id}/authored-emission/export-attempt-bindings-v2",
    response_model=ReleasePinnedExportAttemptDigestBindingV2,
    status_code=status.HTTP_201_CREATED,
)
async def create_export_attempt_binding_v2(
    binding: ReleasePinnedExportAttemptDigestBindingV2,
    presentation_id: uuid.UUID = Path(),
    session: AsyncSession = Depends(get_async_session),
) -> ReleasePinnedExportAttemptDigestBindingV2:
    """Bind evidence receipts and independently validated release-authority pins."""

    if os.getenv("AUTHORED_SEMANTIC_BUNDLE_WRITES_ENABLED", "false").lower() != "true":
        raise HTTPException(
            status_code=404,
            detail={"code": "authored_semantic_bundle_writes_disabled"},
        )
    if binding.presentation_id != presentation_id:
        raise HTTPException(
            status_code=422,
            detail={"code": "authored_semantic_path_mismatch"},
        )
    try:
        sealed = await bind_existing_export_attempt(
            session,
            owner_id=_owner(),
            binding=binding,
            tenant_id=os.getenv("AUTHORED_VNEXT_TENANT_ID", "default"),
        )
        if not isinstance(sealed, ReleasePinnedExportAttemptDigestBindingV2):
            raise SemanticContractError("authored_semantic_export_binding_version_mismatch")
        await session.commit()
        return sealed
    except SemanticContractError as error:
        await session.rollback()
        raise _error(error) from error


def _writes_enabled() -> None:
    if os.getenv("AUTHORED_SEMANTIC_BUNDLE_WRITES_ENABLED", "false").lower() != "true":
        raise HTTPException(
            status_code=404,
            detail={"code": "authored_semantic_bundle_writes_disabled"},
        )


@AUTHORED_SEMANTIC_ROUTER.post(
    "/presentation/{presentation_id}/authored-emission/remake-ordered-set",
    response_model=AuthoredEmissionLifecycleReceiptV1,
)
async def remake_ordered_set(
    request: AuthoredOrderedRemakeRequestV1,
    presentation_id: uuid.UUID = Path(),
    session: AsyncSession = Depends(get_async_session),
) -> AuthoredEmissionLifecycleReceiptV1:
    _writes_enabled()
    try:
        receipt = await remake_ordered_authored_emission(
            session,
            presentation_id=presentation_id,
            owner_id=_owner(),
            request=request,
            tenant_id=os.getenv("AUTHORED_VNEXT_TENANT_ID", "default"),
        )
        await session.commit()
        return receipt
    except SemanticContractError as error:
        await session.rollback()
        raise _error(error) from error


@AUTHORED_SEMANTIC_ROUTER.post(
    "/presentation/{presentation_id}/authored-emission/rollback-manifest",
    response_model=AuthoredEmissionLifecycleReceiptV1,
)
async def rollback_manifest(
    request: AuthoredManifestRollbackRequestV1,
    presentation_id: uuid.UUID = Path(),
    session: AsyncSession = Depends(get_async_session),
) -> AuthoredEmissionLifecycleReceiptV1:
    _writes_enabled()
    try:
        receipt = await rollback_authored_emission_manifest(
            session,
            presentation_id=presentation_id,
            owner_id=_owner(),
            request=request,
            tenant_id=os.getenv("AUTHORED_VNEXT_TENANT_ID", "default"),
        )
        await session.commit()
        return receipt
    except SemanticContractError as error:
        await session.rollback()
        raise _error(error) from error


@AUTHORED_SEMANTIC_ROUTER.post(
    "/presentation/{presentation_id}/authored-emission/cutover-legacy-html",
    response_model=AuthoredEmissionLifecycleReceiptV1,
)
async def cutover_legacy_html(
    request: AuthoredLegacyCutoverRequestV1,
    presentation_id: uuid.UUID = Path(),
    session: AsyncSession = Depends(get_async_session),
) -> AuthoredEmissionLifecycleReceiptV1:
    _writes_enabled()
    try:
        receipt = await cutover_authored_emission_to_legacy(
            session,
            presentation_id=presentation_id,
            owner_id=_owner(),
            request=request,
            tenant_id=os.getenv("AUTHORED_VNEXT_TENANT_ID", "default"),
        )
        await session.commit()
        return receipt
    except SemanticContractError as error:
        await session.rollback()
        raise _error(error) from error


__all__ = ["AUTHORED_SEMANTIC_ROUTER"]
