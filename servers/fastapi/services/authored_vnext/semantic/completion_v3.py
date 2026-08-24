"""Immutable evidence graph root and single-CAS bundle completion authority."""

from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
import os
from typing import Any
import uuid

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from models.authored_vnext.semantic.release_authority_v3 import (
    EvidenceBundleCompletionCreateRequestV3,
    EvidenceBundleCompletionSealRequestV3,
    EvidenceBundleCompletionV3,
    EvidenceGraphRootCreateRequestV3,
    EvidenceGraphRootV3,
    GATE1_GRAPH_BASE_ARTIFACT_IDS_DIGEST_V3,
    GATE1_GRAPH_BASE_ARTIFACT_IDS_V3,
    GATE1_REQUIRED_ARTIFACT_IDS_DIGEST_V3,
)
from models.sql.authored_release_evidence_v3 import (
    AuthoredComQualificationJobV3,
    AuthoredEvidenceBundleCompletionV3,
    AuthoredEvidenceGraphRootV3,
    AuthoredReleaseArtifactV3,
)
from models.sql.authored_semantic_bundle import AuthoredDeckEmissionActivePointer
from models.sql.authored_semantic_bundle import AuthoredSemanticExportBinding
from models.sql.presentation import PresentationModel
from models.sql.presentation_export_attempt import PresentationExportAttempt
from services.authored_vnext.semantic.canonical import SemanticContractError
from services.authored_vnext.semantic.readiness_v3 import load_release_profile_definition_v3
from services.authored_vnext.semantic.readiness import _active_records, _records
from services.authored_vnext.semantic.release_authority_v3 import (
    actual_product_canonical_digest_v2,
    consume_authority_nonce_v3,
    validate_actual_product_authority_v3,
    validate_actual_product_record_v3,
)
from utils.datetime_utils import get_current_utc_datetime


def _utc(value: datetime) -> datetime:
    return value.astimezone(timezone.utc) if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _required_runtime_pins() -> dict[str, str]:
    mapping = {
        "source_tree_census_digest": "AUTHORED_RELEASE_SOURCE_TREE_CENSUS_DIGEST",
        "source_head_commit": "AUTHORED_RELEASE_SOURCE_HEAD_COMMIT",
        "evidence_policy_digest": "AUTHORED_RELEASE_EVIDENCE_POLICY_DIGEST",
        "feature_set_digest": "AUTHORED_RELEASE_FEATURE_SET_DIGEST",
        "gate_minus1_manifest_digest": "AUTHORED_RELEASE_GATE_MINUS1_MANIFEST_DIGEST",
        "gate_minus1_result_set_digest": "AUTHORED_RELEASE_GATE_MINUS1_RESULT_SET_DIGEST",
        "gate_minus1_receipt_artifact_digest": "AUTHORED_RELEASE_GATE_MINUS1_RECEIPT_ARTIFACT_DIGEST",
        "gate_minus1_validator_contract_digest": "AUTHORED_RELEASE_GATE_MINUS1_VALIDATOR_CONTRACT_DIGEST",
        "environment_attestation_digest": "AUTHORED_RELEASE_ENVIRONMENT_ATTESTATION_DIGEST",
        "worker_attestation_digest": "AUTHORED_RELEASE_WORKER_ATTESTATION_DIGEST",
    }
    pins = {field: os.getenv(name, "").strip().lower() for field, name in mapping.items()}
    if any(not value for value in pins.values()):
        raise SemanticContractError("authored_evidence_completion_runtime_pins_unconfigured")
    return pins


def _graph_projection(request: EvidenceGraphRootCreateRequestV3) -> dict[str, Any]:
    value = request.model_dump(mode="json", by_alias=True)
    value.pop("authority", None)
    value.pop("rootDigest", None)
    return value


def _graph_response(record: AuthoredEvidenceGraphRootV3) -> EvidenceGraphRootV3:
    pin_digest = actual_product_canonical_digest_v2(
        {
            "sourceTreeCensusDigest": record.source_tree_census_digest,
            "sourceHeadCommit": record.source_head_commit,
            "gateMinus1ManifestDigest": record.gate_minus1_manifest_digest,
            "gateMinus1ResultSetDigest": record.gate_minus1_result_set_digest,
            "gateMinus1ReceiptArtifactDigest": record.gate_minus1_receipt_artifact_digest,
            "gateMinus1ValidatorContractDigest": record.gate_minus1_validator_contract_digest,
        }
    )
    return EvidenceGraphRootV3(
        graph_root_id=record.id,
        persisted_record_id=record.id,
        presentation_id=record.presentation_id,
        presentation_revision=record.presentation_revision,
        export_attempt_id=record.export_attempt_id,
        request_digest=record.request_digest,
        evidence_policy_digest=record.evidence_policy_digest,
        active_manifest_digest=record.active_manifest_digest,
        required_pin_digest=pin_digest,
        base_artifact_ids=list(record.base_artifact_ids),
        base_artifact_ids_digest=record.base_artifact_ids_digest,
        final_required_artifact_ids_digest=record.final_required_artifact_ids_digest,
        artifact_inventory_digest=record.artifact_inventory_digest,
        root_digest=record.root_digest,
        com_qualification_job_id=record.com_qualification_job_id,
        com_qualification_state="sealed",
        com_qualification_result_digest=record.com_qualification_result_digest,
    )


def _completion_response(
    record: AuthoredEvidenceBundleCompletionV3,
    graph: AuthoredEvidenceGraphRootV3,
) -> EvidenceBundleCompletionV3:
    return EvidenceBundleCompletionV3(
        completion_id=record.id,
        persisted_record_id=record.id,
        graph_root_id=record.graph_root_id,
        graph_root_digest=graph.root_digest,
        export_attempt_id=graph.export_attempt_id,
        state=record.state,
        cas_version=record.cas_version,
        required_capability_ids=list(record.required_capability_ids),
        receipt_digests=list(record.receipt_digests),
        completion_digest=record.completion_digest,
        create_request_digest=record.create_request_digest,
        seal_request_digest=record.seal_request_digest,
        receipt_identities=list(record.receipt_identities),
        sealed_at=record.sealed_at,
    )


class CompletionSealCasConflictV3(Exception):
    """Shared DB-CAS outcome; callers retain their authority-specific API code."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason
        self.structural_code = (
            "completion-seal-conflict" if reason == "second_seal_conflict" else "completion-seal-stale-cas"
        )


async def seal_completion_row_cas_v3(
    session: AsyncSession,
    *,
    record: Any,
    expected_cas_version: int,
    completion_digest: str,
    receipt_digests: list[str],
    authority_payload: dict[str, Any],
    sealed_at: datetime,
) -> Any:
    """Apply the one shared pending->sealed CAS for product and structural rows."""

    if record.state == "sealed":
        if record.completion_digest == completion_digest and record.receipt_digests == receipt_digests:
            return record
        raise CompletionSealCasConflictV3("second_seal_conflict")
    if record.cas_version != expected_cas_version:
        raise CompletionSealCasConflictV3("stale_cas")
    result = await session.execute(
        update(type(record))
        .where(
            type(record).id == record.id,
            type(record).state == "pending",
            type(record).cas_version == expected_cas_version,
        )
        .values(
            state="sealed",
            cas_version=expected_cas_version + 1,
            receipt_digests=receipt_digests,
            completion_digest=completion_digest,
            authority_payload=authority_payload,
            sealed_at=sealed_at,
        )
    )
    if result.rowcount != 1:
        raise CompletionSealCasConflictV3("stale_cas")
    await session.flush()
    await session.refresh(record)
    return record


def _actual_product_cas_error(error: CompletionSealCasConflictV3) -> SemanticContractError:
    return SemanticContractError(
        "authored_evidence_completion_second_seal_conflict"
        if error.reason == "second_seal_conflict"
        else "authored_evidence_completion_stale_cas"
    )


async def _revalidate_subject(session: AsyncSession, graph: AuthoredEvidenceGraphRootV3) -> None:
    pins = _required_runtime_pins()
    definition, definition_digest = load_release_profile_definition_v3()
    if graph.release_profile_digest != definition_digest:
        raise SemanticContractError("authored_evidence_completion_profile_changed")
    base_artifact_ids = list(GATE1_GRAPH_BASE_ARTIFACT_IDS_V3)
    artifact_inventory_ids = [item.get("artifactId") for item in graph.artifact_inventory if isinstance(item, dict)]
    if (
        graph.base_artifact_ids != base_artifact_ids
        or graph.base_artifact_ids_digest != GATE1_GRAPH_BASE_ARTIFACT_IDS_DIGEST_V3
        or actual_product_canonical_digest_v2(graph.base_artifact_ids) != graph.base_artifact_ids_digest
        or graph.final_required_artifact_ids_digest != GATE1_REQUIRED_ARTIFACT_IDS_DIGEST_V3
        or len(artifact_inventory_ids) != len(graph.artifact_inventory)
        or len(set(artifact_inventory_ids)) != len(artifact_inventory_ids)
        or artifact_inventory_ids != base_artifact_ids
        or actual_product_canonical_digest_v2(graph.artifact_inventory) != graph.artifact_inventory_digest
    ):
        raise SemanticContractError("authored_evidence_graph_required_artifacts_invalid")
    for field, expected in pins.items():
        if getattr(graph, field) != expected:
            raise SemanticContractError("authored_evidence_completion_runtime_pin_changed")
    presentation = await session.get(PresentationModel, graph.presentation_id)
    pointer = await session.scalar(
        select(AuthoredDeckEmissionActivePointer).where(
            AuthoredDeckEmissionActivePointer.owner_id == graph.owner_id,
            AuthoredDeckEmissionActivePointer.tenant_id == graph.tenant_id,
            AuthoredDeckEmissionActivePointer.presentation_id == graph.presentation_id,
        )
    )
    if (
        presentation is None
        or presentation.owner_id != graph.owner_id
        or presentation.revision != graph.presentation_revision
        or pointer is None
        or pointer.canonical_mode != "bundleGraph"
        or pointer.presentation_revision != graph.presentation_revision
        or pointer.canonical_digest != graph.active_manifest_digest
    ):
        raise SemanticContractError("authored_evidence_completion_subject_changed")
    export_attempt = await session.get(PresentationExportAttempt, graph.export_attempt_id)
    com_job = await session.get(AuthoredComQualificationJobV3, graph.com_qualification_job_id)
    export_binding = await session.scalar(
        select(AuthoredSemanticExportBinding).where(
            AuthoredSemanticExportBinding.export_attempt_id == graph.export_attempt_id,
            AuthoredSemanticExportBinding.owner_id == graph.owner_id,
            AuthoredSemanticExportBinding.tenant_id == graph.tenant_id,
        )
    )
    binding_payload = export_binding.payload if export_binding is not None else {}
    authority_claim = binding_payload.get("exportAuthorityClaim")
    release_receipt_digests = binding_payload.get("releaseAuthorityReceiptDigests")
    release_projection_digest = binding_payload.get("releaseProjectionDigest")
    release_pin_present = any(
        value is not None for value in (authority_claim, release_receipt_digests, release_projection_digest)
    )
    release_pin_valid = not release_pin_present or (
        isinstance(authority_claim, dict)
        and authority_claim.get("kind") in {"boundedNative", "promotion"}
        and authority_claim.get("profileDefinitionDigest") == graph.release_profile_digest
        and isinstance(release_receipt_digests, list)
        and bool(release_receipt_digests)
        and authority_claim.get("receiptDigests") == release_receipt_digests
        and isinstance(release_projection_digest, str)
        and authority_claim.get("projectionDigest") == release_projection_digest
    )
    if (
        export_attempt is None
        or com_job is None
        or com_job.tenant_id != graph.tenant_id
        or com_job.export_attempt_id != graph.export_attempt_id
        or com_job.evidence_policy_digest != graph.evidence_policy_digest
        or com_job.authority_domain != "actual_product"
        or com_job.state != "sealed"
        or actual_product_canonical_digest_v2(com_job.evidence) != graph.com_qualification_result_digest
        or export_attempt.owner_scope != str(graph.owner_id)
        or export_attempt.presentation_id != graph.presentation_id
        or export_attempt.source_revision != graph.presentation_revision
        or export_attempt.state != "succeeded"
        or export_attempt.artifact_sha256 is None
        or export_binding is None
        or export_binding.presentation_id != graph.presentation_id
        or export_binding.presentation_revision != graph.presentation_revision
        or export_binding.canonical_digest != graph.active_manifest_digest
        or export_binding.pptx_digest != "sha256:" + export_attempt.artifact_sha256
        or binding_payload.get("exportAttemptId") != str(graph.export_attempt_id)
        or binding_payload.get("manifestDigest") != graph.active_manifest_digest
        or binding_payload.get("bindingDigest") != export_binding.binding_digest
        or not isinstance(binding_payload.get("receiptDigests"), list)
        or not binding_payload["receiptDigests"]
        or not release_pin_valid
    ):
        raise SemanticContractError("authored_evidence_completion_export_binding_changed")


async def create_evidence_graph_root_v3(
    session: AsyncSession,
    *,
    request: EvidenceGraphRootCreateRequestV3,
    owner_id: uuid.UUID,
    tenant_id: str,
    now: datetime | None = None,
) -> EvidenceGraphRootV3:
    observed_at = _utc(now or get_current_utc_datetime())
    signed_payload = request.model_dump(mode="json", by_alias=True)
    signed_payload.pop("authority", None)
    signed_payload.pop("completionDigest", None)
    authority_digest = validate_actual_product_authority_v3(signed_payload, request.authority, now=observed_at)
    await consume_authority_nonce_v3(session, authority=request.authority, subject_digest=authority_digest)
    expected_inventory_digest = actual_product_canonical_digest_v2(
        [item.model_dump(mode="json", by_alias=True) for item in request.artifact_inventory]
    )
    expected_root_digest = actual_product_canonical_digest_v2(_graph_projection(request))
    request_digest = actual_product_canonical_digest_v2(signed_payload)
    if request.artifact_inventory_digest != expected_inventory_digest or request.root_digest != expected_root_digest:
        raise SemanticContractError("authored_evidence_graph_digest_mismatch")
    for item in request.artifact_inventory:
        artifact = await session.get(AuthoredReleaseArtifactV3, item.version_id)
        if (
            artifact is None
            or artifact.tenant_id != tenant_id
            or artifact.export_attempt_id != request.export_attempt_id
            or item.export_attempt_id != request.export_attempt_id
            or artifact.artifact_id != item.artifact_id
            or artifact.state != "bound"
            or artifact.sha256 != item.sha256
            or artifact.byte_length != item.byte_length
            or artifact.media_type != item.media_type
            or artifact.encryption_key_ref != item.encryption_key_ref
            or artifact.storage_generation != item.storage_generation
            or "sha256:" + sha256(bytes(artifact.content_bytes)).hexdigest() != item.sha256
        ):
            raise SemanticContractError("authored_evidence_graph_artifact_mismatch")
    existing = await session.scalar(
        select(AuthoredEvidenceGraphRootV3).where(
            AuthoredEvidenceGraphRootV3.tenant_id == tenant_id,
            AuthoredEvidenceGraphRootV3.export_attempt_id == request.export_attempt_id,
            AuthoredEvidenceGraphRootV3.evidence_policy_digest == request.evidence_policy_digest,
            AuthoredEvidenceGraphRootV3.authority_domain == "actual_product",
        )
    )
    if existing is not None:
        if existing.root_digest == request.root_digest:
            await _revalidate_subject(session, existing)
            return _graph_response(existing)
        raise SemanticContractError("authored_evidence_graph_cas_conflict")
    record = AuthoredEvidenceGraphRootV3(
        tenant_id=tenant_id,
        owner_id=owner_id,
        presentation_id=request.presentation_id,
        presentation_revision=request.presentation_revision,
        export_attempt_id=request.export_attempt_id,
        authority_domain="actual_product",
        evidence_policy_digest=request.evidence_policy_digest,
        active_manifest_digest=request.active_manifest_digest,
        source_tree_census_digest=request.source_tree_census_digest,
        source_head_commit=request.source_head_commit,
        release_profile_digest=request.release_profile_digest,
        feature_set_digest=request.feature_set_digest,
        gate_minus1_manifest_digest=request.gate_minus1_manifest_digest,
        gate_minus1_result_set_digest=request.gate_minus1_result_set_digest,
        gate_minus1_receipt_artifact_digest=request.gate_minus1_receipt_artifact_digest,
        gate_minus1_validator_contract_digest=request.gate_minus1_validator_contract_digest,
        environment_attestation_digest=request.environment_attestation_digest,
        worker_attestation_digest=request.worker_attestation_digest,
        com_qualification_job_id=request.com_qualification_job_id,
        com_qualification_result_digest=request.com_qualification_result_digest,
        base_artifact_ids=list(request.base_artifact_ids),
        base_artifact_ids_digest=request.base_artifact_ids_digest,
        final_required_artifact_ids_digest=request.final_required_artifact_ids_digest,
        artifact_inventory=[item.model_dump(mode="json", by_alias=True) for item in request.artifact_inventory],
        artifact_inventory_digest=request.artifact_inventory_digest,
        root_digest=request.root_digest,
        request_digest=request_digest,
    )
    session.add(record)
    await session.flush()
    await _revalidate_subject(session, record)
    return _graph_response(record)


async def create_evidence_bundle_completion_v3(
    session: AsyncSession,
    *,
    request: EvidenceBundleCompletionCreateRequestV3,
    tenant_id: str,
) -> EvidenceBundleCompletionV3:
    definition, _ = load_release_profile_definition_v3()
    if request.required_capability_ids != definition.all_required_capability_ids:
        raise SemanticContractError("authored_evidence_completion_capability_set_invalid")
    graph = await session.get(AuthoredEvidenceGraphRootV3, request.graph_root_id)
    if graph is None or graph.tenant_id != tenant_id:
        raise SemanticContractError("authored_evidence_graph_not_found")
    await _revalidate_subject(session, graph)
    existing = await session.scalar(
        select(AuthoredEvidenceBundleCompletionV3).where(AuthoredEvidenceBundleCompletionV3.graph_root_id == graph.id)
    )
    pending_digest = actual_product_canonical_digest_v2(
        {
            "graphRootDigest": graph.root_digest,
            "requiredCapabilityIds": request.required_capability_ids,
            "state": "pending",
        }
    )
    if existing is not None:
        if existing.required_capability_ids == request.required_capability_ids:
            return _completion_response(existing, graph)
        raise SemanticContractError("authored_evidence_completion_cas_conflict")
    record = AuthoredEvidenceBundleCompletionV3(
        tenant_id=tenant_id,
        graph_root_id=graph.id,
        authority_domain="actual_product",
        state="pending",
        cas_version=0,
        required_capability_ids=request.required_capability_ids,
        receipt_digests=[],
        completion_digest=pending_digest,
        create_request_digest=actual_product_canonical_digest_v2(
            request.model_dump(mode="json", by_alias=True)
        ),
        receipt_identities=[],
    )
    session.add(record)
    await session.flush()
    return _completion_response(record, graph)


async def seal_evidence_bundle_completion_v3(
    session: AsyncSession,
    *,
    completion_id: uuid.UUID,
    request: EvidenceBundleCompletionSealRequestV3,
    tenant_id: str,
    now: datetime | None = None,
) -> EvidenceBundleCompletionV3:
    observed_at = _utc(now or get_current_utc_datetime())
    completion = await session.get(AuthoredEvidenceBundleCompletionV3, completion_id)
    if completion is None or completion.tenant_id != tenant_id:
        raise SemanticContractError("authored_evidence_completion_not_found")
    graph = await session.get(AuthoredEvidenceGraphRootV3, completion.graph_root_id)
    assert graph is not None
    signed_payload = request.model_dump(mode="json", by_alias=True)
    signed_payload.pop("authority", None)
    signed_payload.pop("completionDigest", None)
    authority_digest = validate_actual_product_authority_v3(signed_payload, request.authority, now=observed_at)
    await consume_authority_nonce_v3(session, authority=request.authority, subject_digest=authority_digest)
    expected_completion_digest = actual_product_canonical_digest_v2(
        {
            "graphRootDigest": graph.root_digest,
            "requiredCapabilityIds": completion.required_capability_ids,
            "receiptDigests": request.receipt_digests,
            "authorityDigest": authority_digest,
            "state": "sealed",
        }
    )
    if request.completion_digest != expected_completion_digest:
        raise SemanticContractError("authored_evidence_completion_digest_mismatch")
    if completion.state == "sealed":
        try:
            await seal_completion_row_cas_v3(
                session,
                record=completion,
                expected_cas_version=request.expected_cas_version,
                completion_digest=request.completion_digest,
                receipt_digests=request.receipt_digests,
                authority_payload=completion.authority_payload or {},
                sealed_at=completion.sealed_at or observed_at,
            )
        except CompletionSealCasConflictV3 as error:
            raise _actual_product_cas_error(error) from error
        return _completion_response(completion, graph)
    await _revalidate_subject(session, graph)
    records = [
        record
        for record in _active_records(await _records(session, tenant_id=tenant_id))
        if record.receipt_digest in request.receipt_digests
    ]
    by_capability = {record.capability_id: record for record in records}
    expected_receipts = []
    receipt_identities = []
    export_binding = await session.scalar(
        select(AuthoredSemanticExportBinding).where(
            AuthoredSemanticExportBinding.tenant_id == tenant_id,
            AuthoredSemanticExportBinding.export_attempt_id == graph.export_attempt_id,
        )
    )
    if export_binding is None:
        raise SemanticContractError("authored_evidence_completion_export_binding_changed")
    for capability_id in completion.required_capability_ids:
        record = by_capability.get(capability_id)
        if (
            record is None
            or record.export_attempt_id != graph.export_attempt_id
            or record.binding_digest != export_binding.binding_digest
            or not validate_actual_product_record_v3(record, now=observed_at)
        ):
            raise SemanticContractError("authored_evidence_completion_receipt_set_invalid")
        expected_receipts.append(record.receipt_digest)
        receipt_identities.append(
            {
                "recordId": str(record.id),
                "capabilityId": record.capability_id,
                "receiptDigest": record.receipt_digest,
                "exportAttemptId": str(record.export_attempt_id),
                "bindingDigest": record.binding_digest,
                "evidenceBundleId": str(record.payload.get("evidenceBundleId", "")),
                "evidenceDigest": record.payload.get("evidenceDigest"),
            }
        )
    if request.receipt_digests != expected_receipts or len(records) != len(expected_receipts):
        raise SemanticContractError("authored_evidence_completion_receipt_set_invalid")
    try:
        await seal_completion_row_cas_v3(
            session,
            record=completion,
            expected_cas_version=request.expected_cas_version,
            receipt_digests=request.receipt_digests,
            completion_digest=request.completion_digest,
            authority_payload={
                "envelope": request.authority.model_dump(mode="json", by_alias=True),
                "signedPayload": signed_payload,
                "authorityDigest": authority_digest,
            },
            sealed_at=observed_at,
        )
    except CompletionSealCasConflictV3 as error:
        raise _actual_product_cas_error(error) from error
    completion.seal_request_digest = actual_product_canonical_digest_v2(
        request.model_dump(mode="json", by_alias=True)
    )
    completion.receipt_identities = receipt_identities
    await session.flush()
    return _completion_response(completion, graph)


async def sealed_completion_matches_projection_v3(
    session: AsyncSession,
    *,
    tenant_id: str,
    presentation_id: uuid.UUID,
    presentation_revision: int,
    active_manifest_digest: str,
    receipt_by_capability: dict[str, str],
) -> bool:
    definition, _ = load_release_profile_definition_v3()
    completion = await session.scalar(
        select(AuthoredEvidenceBundleCompletionV3)
        .join(
            AuthoredEvidenceGraphRootV3,
            AuthoredEvidenceGraphRootV3.id == AuthoredEvidenceBundleCompletionV3.graph_root_id,
        )
        .where(
            AuthoredEvidenceBundleCompletionV3.tenant_id == tenant_id,
            AuthoredEvidenceBundleCompletionV3.state == "sealed",
            AuthoredEvidenceGraphRootV3.presentation_id == presentation_id,
            AuthoredEvidenceGraphRootV3.presentation_revision == presentation_revision,
            AuthoredEvidenceGraphRootV3.active_manifest_digest == active_manifest_digest,
        )
    )
    if completion is None:
        return False
    graph = await session.get(AuthoredEvidenceGraphRootV3, completion.graph_root_id)
    if graph is None:
        return False
    try:
        await _revalidate_subject(session, graph)
        authority_payload = completion.authority_payload or {}
        envelope_value = authority_payload.get("envelope")
        signed_payload = authority_payload.get("signedPayload")
        authority_digest = authority_payload.get("authorityDigest")
        if not isinstance(envelope_value, dict) or not isinstance(signed_payload, dict):
            return False
        from models.authored_vnext.semantic.release_authority_v3 import (
            EvidenceIssuerEnvelopeV3,
        )

        envelope = EvidenceIssuerEnvelopeV3.model_validate(envelope_value, strict=False)
        if validate_actual_product_authority_v3(signed_payload, envelope) != authority_digest:
            return False
    except SemanticContractError:
        return False
    expected = [receipt_by_capability.get(item) for item in definition.all_required_capability_ids]
    identity_pairs = [
        (item.get("capabilityId"), item.get("receiptDigest"), item.get("exportAttemptId"))
        for item in completion.receipt_identities
        if isinstance(item, dict)
    ]
    expected_identity_pairs = [
        (capability_id, receipt_by_capability.get(capability_id), str(graph.export_attempt_id))
        for capability_id in definition.all_required_capability_ids
    ]
    return (
        completion.required_capability_ids == definition.all_required_capability_ids
        and None not in expected
        and completion.receipt_digests == expected
        and identity_pairs == expected_identity_pairs
    )


async def get_evidence_graph_root_v3(
    session: AsyncSession, *, graph_root_id: uuid.UUID, tenant_id: str
) -> EvidenceGraphRootV3:
    graph = await session.get(AuthoredEvidenceGraphRootV3, graph_root_id)
    if graph is None or graph.tenant_id != tenant_id:
        raise SemanticContractError("authored_evidence_graph_not_found")
    await _revalidate_subject(session, graph)
    return _graph_response(graph)


async def get_evidence_bundle_completion_v3(
    session: AsyncSession, *, completion_id: uuid.UUID, tenant_id: str
) -> EvidenceBundleCompletionV3:
    completion = await session.get(AuthoredEvidenceBundleCompletionV3, completion_id)
    if completion is None or completion.tenant_id != tenant_id:
        raise SemanticContractError("authored_evidence_completion_not_found")
    graph = await session.get(AuthoredEvidenceGraphRootV3, completion.graph_root_id)
    assert graph is not None
    await _revalidate_subject(session, graph)
    return _completion_response(completion, graph)


__all__ = [
    "CompletionSealCasConflictV3",
    "create_evidence_bundle_completion_v3",
    "create_evidence_graph_root_v3",
    "get_evidence_bundle_completion_v3",
    "get_evidence_graph_root_v3",
    "seal_evidence_bundle_completion_v3",
    "sealed_completion_matches_projection_v3",
    "seal_completion_row_cas_v3",
]
