"""Isolated Gate 0 execution over real product publication, binding, CAS, and projection."""

from __future__ import annotations

import base64
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import os
from typing import Any
import uuid

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from models.authored_vnext.semantic.contracts import ExportAttemptDigestBindingV1
from models.authored_vnext.semantic.release_authority_v3 import (
    DurableArtifactPutRequestV3,
    EvidenceIssuerEnvelopeV3,
    Gate0StructuralCaseExecutionV1,
)
from models.sql.authored_capability_receipt import AuthoredCapabilityReceiptRecordV2
from models.sql.authored_release_evidence_v3 import (
    AuthoredEvidenceBundleCompletionV3,
    AuthoredGate0StructuralCompletionV3,
    AuthoredGate0StructuralEventV3,
    AuthoredGate0StructuralSubjectV3,
    AuthoredGate0StructuralTraceV3,
    AuthoredReleaseArtifactV3,
    AuthoredReleaseNonceV3,
)
from models.sql.authored_semantic_bundle import (
    AuthoredDeckEmissionManifestRecord,
    AuthoredEmissionBundleRecord,
    AuthoredSemanticExportBinding,
)
from models.sql.presentation import PresentationModel
from models.sql.presentation_export_attempt import PresentationExportAttempt
from models.sql.slide import SlideModel
from services.authored_vnext.semantic.canonical import (
    SemanticContractError,
    bytes_digest,
    canonical_digest,
    canonical_json,
)
from services.authored_vnext.semantic.export_binding import bind_existing_export_attempt
from services.authored_vnext.semantic.completion_v3 import (
    CompletionSealCasConflictV3,
    seal_completion_row_cas_v3,
)
from services.authored_vnext.semantic.publication import publish_authored_semantic_bundle_graph
from services.authored_vnext.semantic.qualification_v3 import (
    bind_durable_artifact_v3,
    put_durable_artifact_v3,
)
from services.authored_vnext.semantic.readiness_v3 import (
    get_workbench_capability_projection_v3,
    load_release_profile_definition_v3,
)
from services.authored_vnext.semantic.release_authority_v3 import (
    BoundEvidenceSubjectRejectionV3,
    validate_bound_evidence_subject_v3,
)
from utils.owner_context import get_current_owner_id


_CASE_ORDINALS: dict[str, int] = {
    case_id: ordinal
    for ordinal, case_id in enumerate(
        (
            "g0.positive.structural-projection",
            "g0.mutate.authority-domain",
            "g0.mutate.evidence-class",
            "g0.mutate.evidence-schema-version",
            "g0.mutate.evidence-policy-id",
            "g0.mutate.evidence-policy-digest",
            "g0.mutate.issuer-id",
            "g0.mutate.signer-key-id",
            "g0.mutate.issued-at",
            "g0.mutate.expires-at",
            "g0.mutate.nonce",
            "g0.mutate.tenant-id",
            "g0.mutate.owner-id",
            "g0.mutate.presentation-id",
            "g0.mutate.presentation-revision",
            "g0.mutate.operation-id",
            "g0.mutate.generation-attempt-id",
            "g0.mutate.manifest-digest",
            "g0.mutate.export-attempt-id",
            "g0.mutate.binding-digest",
            "g0.mutate.renderer-source-digest",
            "g0.mutate.input-pptx-digest",
            "g0.mutate.saved-copy-pptx-digest",
            "g0.mutate.reopen-input-pptx-digest",
            "g0.mutate.release-profile-id",
            "g0.mutate.release-profile-digest",
            "g0.mutate.feature-set-digest",
            "g0.mutate.environment-attestation-digest",
            "g0.mutate.worker-attestation-digest",
            "g0.mutate.synthetic-font-bytes",
            "g0.mutate.partial-capability-set",
            "g0.mutate.active-manifest-after-issue",
            "g0.mutate.artifact-bytes-after-issue",
            "g0.mutate.completion-second-seal",
        ),
        start=1,
    )
}

_SUBJECT_FIELDS = (
    "authorityDomain",
    "evidenceClass",
    "schemaVersion",
    "evidencePolicyId",
    "evidencePolicyDigest",
    "issuerId",
    "signerKeyId",
    "issuedAt",
    "expiresAt",
    "nonce",
    "tenantId",
    "ownerId",
    "presentationId",
    "presentationRevision",
    "operationId",
    "generationAttemptId",
    "manifestDigest",
    "exportAttemptId",
    "bindingDigest",
    "rendererSourceDigest",
    "inputPptxDigest",
    "savedCopyPptxDigest",
    "reopenInputPptxDigest",
    "releaseProfileId",
    "releaseProfileDigest",
    "featureSetDigest",
    "environmentAttestationDigest",
    "workerAttestationDigest",
    "fontInventory.faces[0].sourceContainerSha256",
    "allRequiredCapabilityIds",
    "artifactReference.versionId",
    "completionSeal",
)


class _ObservedRejection(Exception):
    def __init__(self, status_code: int, code: str) -> None:
        super().__init__(code)
        self.status_code = status_code
        self.code = code


def _pins(request: Gate0StructuralCaseExecutionV1) -> tuple[str, str]:
    expected_manifest = os.getenv("AUTHORED_GATE0_STRUCTURAL_MANIFEST_DIGEST", "").strip()
    policy_digest = os.getenv("AUTHORED_RELEASE_EVIDENCE_POLICY_DIGEST", "").strip()
    source_digest = os.getenv("AUTHORED_RELEASE_SOURCE_TREE_CENSUS_DIGEST", "").strip()
    if (
        not expected_manifest
        or request.manifest_digest != expected_manifest
        or not policy_digest.startswith("sha256:")
        or not source_digest.startswith("sha256:")
    ):
        raise SemanticContractError("authored_gate0_structural_runtime_pins_invalid")
    return policy_digest, source_digest


def _sign(trace: dict[str, Any]) -> tuple[str, str, str]:
    key_value = os.getenv("AUTHORED_GATE0_STRUCTURAL_ED25519_PRIVATE_KEY_BASE64", "")
    signer_key_id = os.getenv("AUTHORED_GATE0_STRUCTURAL_SIGNER_KEY_ID", "").strip()
    try:
        key_bytes = base64.b64decode(key_value, validate=True)
        private_key = Ed25519PrivateKey.from_private_bytes(key_bytes)
    except (ValueError, TypeError) as error:
        raise SemanticContractError("authored_gate0_structural_signer_unconfigured") from error
    if not signer_key_id:
        raise SemanticContractError("authored_gate0_structural_signer_unconfigured")
    trace["signerKeyId"] = signer_key_id
    trace_digest = canonical_digest(trace)
    signature = base64.b64encode(private_key.sign(trace_digest.encode("ascii"))).decode("ascii")
    return signer_key_id, trace_digest, signature


def _uuid(run_id: uuid.UUID, case_id: str, label: str) -> uuid.UUID:
    return uuid.uuid5(run_id, f"{case_id}:{label}")


def _flip_digest(value: str) -> str:
    return value[:-1] + ("0" if value[-1] != "0" else "1")


def _set_path(payload: dict[str, Any], path: str, value: Any) -> None:
    if path == "$.evidenceSchemaVersion":
        payload["schemaVersion"] = value
    elif path == "$.fontInventory.faces[0].sourceContainerSha256":
        payload["fontInventory"]["faces"][0]["sourceContainerSha256"] = value
    elif path == "$.artifactReference.versionId":
        payload["artifactReference"]["versionId"] = value
    else:
        payload[path.removeprefix("$.")] = value


def _apply_mutation(
    baseline: dict[str, Any], request: Gate0StructuralCaseExecutionV1, *, now: datetime
) -> dict[str, Any]:
    mutation = request.mutation
    if mutation is None:
        return deepcopy(baseline)
    mutated = deepcopy(baseline)
    replacements: dict[str, Any] = {
        "replace-structural-with-actual-product": "actual_product",
        "replace-with-different-enum": "actual_product_capability",
        "increment-version": "authored-evidence/v4",
        "replace-valid-id": "alternate-policy-or-profile",
        "replace-valid-uuid": str(_uuid(request.run_id, request.case_id, "replacement")),
        "replace-active-key-from-wrong-purpose": "structural.wrong-purpose.key",
        "set-future-beyond-skew": (now + timedelta(minutes=5)).isoformat().replace("+00:00", "Z"),
        "set-before-validation-clock": (now - timedelta(minutes=1)).isoformat().replace("+00:00", "Z"),
        "reuse-consumed-nonce": f"replayed_{request.run_id.hex}_{request.ordinal}",
        "replace-cross-tenant": f"other-{request.run_id.hex}",
        "replace-cross-owner": str(_uuid(request.run_id, request.case_id, "other-owner")),
        "decrement-revision": max(0, int(baseline["presentationRevision"]) - 1),
        "replace-wrong-environment": _flip_digest(baseline["environmentAttestationDigest"]),
        "replace-wrong-worker": _flip_digest(baseline["workerAttestationDigest"]),
        "substitute-synthetic-font-bytes": _flip_digest(baseline["fontInventory"]["faces"][0]["sourceContainerSha256"]),
        "remove-one-required-capability": baseline["allRequiredCapabilityIds"][:-1],
        "change-active-manifest-after-receipt-issue": _flip_digest(baseline["manifestDigest"]),
        "replace-bound-object-version": str(_uuid(request.run_id, request.case_id, "other-artifact")),
        "issue-different-second-seal": {
            "casVersion": 2,
            "digest": canonical_digest({"second": str(request.run_id), "case": request.case_id}),
        },
    }
    for path in mutation.json_paths:
        if mutation.operator == "flip-one-hex-nibble":
            replacement = _flip_digest(mutated[path.removeprefix("$.")])
        else:
            try:
                replacement = replacements[mutation.operator]
            except KeyError as error:
                raise SemanticContractError("authored_gate0_structural_mutation_operator_invalid") from error
        _set_path(mutated, path, replacement)
    return mutated


def _changed_field(baseline: dict[str, Any], mutated: dict[str, Any]) -> str | None:
    for field in _SUBJECT_FIELDS:
        if field == "fontInventory.faces[0].sourceContainerSha256":
            before = baseline["fontInventory"]["faces"][0]["sourceContainerSha256"]
            after = mutated["fontInventory"]["faces"][0]["sourceContainerSha256"]
        elif field == "artifactReference.versionId":
            before = baseline["artifactReference"]["versionId"]
            after = mutated["artifactReference"]["versionId"]
        else:
            before, after = baseline.get(field), mutated.get(field)
        if before != after:
            return field
    return None


def _observe_product_subject_validation(
    baseline: dict[str, Any], mutated: dict[str, Any], *, phase: str, now: datetime
) -> None:
    try:
        validate_bound_evidence_subject_v3(mutated, expected=baseline, phase=phase, now=now)
    except BoundEvidenceSubjectRejectionV3 as error:
        raise _ObservedRejection(error.status_code, error.code) from error


async def _consume_structural_nonce(
    session: AsyncSession,
    *,
    payload: dict[str, Any],
    subject_digest: str,
) -> None:
    existing = await session.scalar(
        select(AuthoredReleaseNonceV3).where(
            AuthoredReleaseNonceV3.issuer_id == payload["issuerId"],
            AuthoredReleaseNonceV3.signer_key_id == payload["signerKeyId"],
            AuthoredReleaseNonceV3.nonce == payload["nonce"],
        )
    )
    if existing is not None:
        if existing.subject_digest == subject_digest:
            return
        raise _ObservedRejection(409, "evidence-nonce-replay")
    session.add(
        AuthoredReleaseNonceV3(
            authority_domain="structural_test",
            evidence_class="synthetic_structural_only",
            issuer_id=payload["issuerId"],
            signer_key_id=payload["signerKeyId"],
            nonce=payload["nonce"],
            subject_digest=subject_digest,
        )
    )
    await session.flush()


def _product_authority_accepts(payload: dict[str, Any]) -> bool:
    candidate = {
        "schemaVersion": "authored-evidence-issuer-envelope/v3",
        "authorityDomain": payload["authorityDomain"],
        "evidenceClass": payload["evidenceClass"],
        "issuerId": payload["issuerId"],
        "signerKeyId": payload["signerKeyId"],
        "environmentId": "structural-isolated",
        "keyPurpose": "actual-product-capability",
        "rotationEpoch": 1,
        "issuedAt": payload["issuedAt"],
        "expiresAt": payload["expiresAt"],
        "nonce": payload["nonce"],
        "signatureAlgorithm": "Ed25519",
        "signatureBase64": base64.b64encode(b"0" * 64).decode("ascii"),
    }
    try:
        EvidenceIssuerEnvelopeV3.model_validate(candidate, strict=False)
    except (ValidationError, ValueError):
        return False
    return False


async def _event(
    session: AsyncSession,
    *,
    subject_id: uuid.UUID,
    seam: str,
    input_value: Any,
    output_value: Any,
    payload: dict[str, Any],
    durable_record_id: uuid.UUID | None = None,
) -> dict[str, Any]:
    record = AuthoredGate0StructuralEventV3(
        subject_id=subject_id,
        authority_domain="structural_test",
        seam=seam,
        operation_id=uuid.uuid4(),
        input_digest=canonical_digest(input_value),
        output_digest=canonical_digest(output_value),
        payload=payload,
    )
    session.add(record)
    await session.flush()
    span: dict[str, Any] = {
        "seam": seam,
        "serverObserved": True,
        "operationId": str(record.operation_id),
        "inputDigest": record.input_digest,
        "outputDigest": record.output_digest,
        "evidenceRef": (f"/api/v1/ppt/authored-emission/gate0-structural-v3/evidence/{record.id}"),
    }
    if durable_record_id is not None:
        span["durableRecordId"] = str(durable_record_id)
    return span


async def execute_gate0_structural_case_v3(
    session: AsyncSession,
    *,
    request: Gate0StructuralCaseExecutionV1,
) -> tuple[int, dict[str, Any]]:
    if _CASE_ORDINALS.get(request.case_id) != request.ordinal:
        raise SemanticContractError("authored_gate0_structural_case_unknown")
    if (request.ordinal == 1) != (request.mutation is None):
        raise SemanticContractError("authored_gate0_structural_mutation_shape_invalid")
    policy_digest, source_digest = _pins(request)
    request_wire = request.model_dump(mode="json", by_alias=True)
    request_digest = canonical_digest(request_wire)
    existing = await session.scalar(
        select(AuthoredGate0StructuralTraceV3).where(
            AuthoredGate0StructuralTraceV3.run_id == request.run_id,
            AuthoredGate0StructuralTraceV3.case_id == request.case_id,
        )
    )
    if existing is not None:
        if existing.request_digest == request_digest:
            return existing.http_status, dict(existing.payload)
        raise SemanticContractError("authored_gate0_structural_case_replay_conflict")

    now = datetime.now(timezone.utc).replace(microsecond=0)
    # Request-scoped writes are stamped with the authenticated owner during
    # flush. Use that same identity for the structural graph and its immediate
    # readback; direct service tests retain a deterministic isolated owner.
    owner_id = get_current_owner_id() or _uuid(request.run_id, request.case_id, "owner")
    presentation_id = _uuid(request.run_id, request.case_id, "presentation")
    operation_id = _uuid(request.run_id, request.case_id, "operation")
    generation_attempt_id = _uuid(request.run_id, request.case_id, "generation")
    export_attempt_id = _uuid(request.run_id, request.case_id, "export")
    tenant_id = f"g0-{request.run_id.hex[:16]}-{request.ordinal:02d}"
    session.add(
        PresentationModel(
            id=presentation_id,
            owner_id=owner_id,
            content="isolated Gate 0 structural subject",
            n_slides=1,
            language="ko",
            mode="authored",
        )
    )
    await session.flush()
    html = '<div data-qid="g0.subject"></div>'
    slide = SlideModel(
        id=_uuid(request.run_id, request.case_id, "slide"),
        owner_id=owner_id,
        presentation=presentation_id,
        layout_group="authored",
        layout="authored:content",
        index=0,
        content={"__authored__": True},
        html_content=html,
    )
    manifest = await publish_authored_semantic_bundle_graph(
        session,
        presentation_id=presentation_id,
        owner_id=owner_id,
        expected_presentation_revision=0,
        operation_id=operation_id,
        generation_attempt_id=generation_attempt_id,
        slides=[slide],
        htmls=[html],
        semantic_drafts=[
            {
                "semanticUnavailableReceipt": {
                    "htmlDigest": bytes_digest(html.encode("utf-8")),
                    "failureCode": "authored_semantic_retry_exhausted",
                }
            }
        ],
        master_contract_digest=canonical_digest({"gate0": "master"}),
        composition_digest=canonical_digest({"gate0": "composition"}),
        tenant_id=tenant_id,
    )
    assert manifest.canonical_digest is not None
    manifest_record = await session.scalar(
        select(AuthoredDeckEmissionManifestRecord).where(
            AuthoredDeckEmissionManifestRecord.canonical_digest == manifest.canonical_digest,
            AuthoredDeckEmissionManifestRecord.tenant_id == tenant_id,
        )
    )
    assert manifest_record is not None
    bundle_records = list(
        await session.scalars(
            select(AuthoredEmissionBundleRecord).where(
                AuthoredEmissionBundleRecord.tenant_id == tenant_id,
                AuthoredEmissionBundleRecord.presentation_id == presentation_id,
            )
        )
    )
    by_digest = {item.bundle_digest: item for item in bundle_records}
    ordered_records = [by_digest[item.bundle_digest] for item in manifest.ordered_slides]
    session.add(
        PresentationExportAttempt(
            id=export_attempt_id,
            owner_id=owner_id,
            owner_scope=str(owner_id),
            presentation_id=presentation_id,
            format="pptx",
            state="pending",
            source_revision=manifest.presentation_revision,
            source_snapshot_sha256="1" * 64,
            request_hash="2" * 64,
            idempotency_key_hash=canonical_digest({"run": str(request.run_id), "case": request.case_id}).removeprefix(
                "sha256:"
            ),
            requested_adapter="general",
            provenance_schema_version=1,
            provenance={"schemaVersion": 1, "authorityDomain": "structural_test"},
        )
    )
    await session.flush()
    renderer_digest = canonical_digest({"gate0": "renderer"})
    binding = ExportAttemptDigestBindingV1(
        export_attempt_id=export_attempt_id,
        presentation_id=presentation_id,
        presentation_revision=manifest.presentation_revision,
        operation_id=manifest.operation_id,
        generation_attempt_id=manifest.generation_attempt_id,
        slide_origin_generation_attempt_ids=[item.origin_generation_attempt_id for item in ordered_records],
        canonical_digest=manifest.canonical_digest,
        manifest_digest=manifest.canonical_digest,
        semantic_digests=[item.semantic_component_digest for item in ordered_records],
        master_contract_digest=manifest.master_contract_digest,
        composition_digest=manifest.composition_digest,
        font_manifest_digest=manifest.font_manifest_digest,
        asset_manifest_digest=manifest.asset_manifest_digest,
        quality_policy_digest=canonical_digest({"qualityPolicyVersion": manifest.quality_policy_version}),
        mode="hybrid",
        renderer_source_digest=renderer_digest,
        serializer_id="pptxgenjs-powerpoint",
        serializer_version="gate0-structural-v3",
        receipt_digests=[],
    )
    sealed_binding = await bind_existing_export_attempt(
        session, owner_id=owner_id, binding=binding, tenant_id=tenant_id
    )
    assert sealed_binding.binding_digest is not None
    binding_record = await session.scalar(
        select(AuthoredSemanticExportBinding).where(
            AuthoredSemanticExportBinding.export_attempt_id == export_attempt_id
        )
    )
    assert binding_record is not None

    seed_bytes = b"gate0-real-bound-source-v3"
    seed_artifact = await put_durable_artifact_v3(
        session,
        request=DurableArtifactPutRequestV3(
            export_attempt_id=export_attempt_id,
            artifact_id=f"gate0/{request.run_id}/{request.case_id}/bound-source",
            sha256=bytes_digest(seed_bytes),
            media_type="application/octet-stream",
            encryption_key_ref="kms:gate0/isolated",
            storage_generation=1,
            content_base64=base64.b64encode(seed_bytes).decode("ascii"),
        ),
        tenant_id=tenant_id,
    )
    seed_artifact = await bind_durable_artifact_v3(
        session,
        artifact_id=seed_artifact.artifact_id,
        export_attempt_id=export_attempt_id,
        expected_version_id=seed_artifact.version_id,
        expected_sha256=seed_artifact.sha256,
        tenant_id=tenant_id,
    )
    definition, profile_digest = load_release_profile_definition_v3()
    baseline = {
        "authorityDomain": "structural_test",
        "evidenceClass": "synthetic_structural_only",
        "schemaVersion": "authored-capability-receipt-bound-subject/v3",
        "evidencePolicyId": "authored-evidence-policy/v2-candidate-20260823-build20326",
        "evidencePolicyDigest": policy_digest,
        "issuerId": "gate0.structural.issuer",
        "signerKeyId": os.getenv("AUTHORED_GATE0_STRUCTURAL_SIGNER_KEY_ID", ""),
        "issuedAt": now.isoformat().replace("+00:00", "Z"),
        "expiresAt": (now + timedelta(minutes=15)).isoformat().replace("+00:00", "Z"),
        "nonce": f"gate0_{request.run_id.hex}_{request.ordinal:02d}",
        "tenantId": tenant_id,
        "ownerId": str(owner_id),
        "presentationId": str(presentation_id),
        "presentationRevision": manifest.presentation_revision,
        "operationId": str(operation_id),
        "generationAttemptId": str(generation_attempt_id),
        "manifestDigest": manifest.canonical_digest,
        "exportAttemptId": str(export_attempt_id),
        "bindingDigest": sealed_binding.binding_digest,
        "rendererSourceDigest": renderer_digest,
        "inputPptxDigest": canonical_digest({"gate0": "input-pptx"}),
        "savedCopyPptxDigest": canonical_digest({"gate0": "saved-copy-pptx"}),
        "reopenInputPptxDigest": canonical_digest({"gate0": "reopen-input-pptx"}),
        "releaseProfileId": definition.profile_id,
        "releaseProfileDigest": profile_digest,
        "featureSetDigest": os.getenv(
            "AUTHORED_RELEASE_FEATURE_SET_DIGEST", canonical_digest({"gate0": "feature-set"})
        ),
        "environmentAttestationDigest": os.getenv(
            "AUTHORED_RELEASE_ENVIRONMENT_ATTESTATION_DIGEST",
            canonical_digest({"gate0": "environment"}),
        ),
        "workerAttestationDigest": os.getenv(
            "AUTHORED_RELEASE_WORKER_ATTESTATION_DIGEST", canonical_digest({"gate0": "worker"})
        ),
        "fontInventory": {"faces": [{"sourceContainerSha256": bytes_digest(seed_bytes)}]},
        "allRequiredCapabilityIds": list(definition.all_required_capability_ids),
        "artifactReference": {
            "artifactId": seed_artifact.artifact_id,
            "versionId": str(seed_artifact.version_id),
            "sha256": seed_artifact.sha256,
        },
        "completionSeal": None,
        "receipt": {"schemaVersion": "authored-capability-receipt-issue/v3"},
    }
    mutated = _apply_mutation(baseline, request, now=now)
    baseline_digest, mutated_digest = canonical_digest(baseline), canonical_digest(mutated)
    if request.mutation is not None and baseline_digest == mutated_digest:
        raise SemanticContractError("authored_gate0_structural_mutation_no_effect")
    subject_bytes = canonical_json(mutated).encode("utf-8")
    subject_artifact = await put_durable_artifact_v3(
        session,
        request=DurableArtifactPutRequestV3(
            export_attempt_id=export_attempt_id,
            artifact_id=f"gate0/{request.run_id}/{request.case_id}/mutated-subject-json",
            sha256=bytes_digest(subject_bytes),
            media_type="application/json",
            encryption_key_ref="kms:gate0/isolated",
            storage_generation=1,
            content_base64=base64.b64encode(subject_bytes).decode("ascii"),
        ),
        tenant_id=tenant_id,
    )
    subject_artifact = await bind_durable_artifact_v3(
        session,
        artifact_id=subject_artifact.artifact_id,
        export_attempt_id=export_attempt_id,
        expected_version_id=subject_artifact.version_id,
        expected_sha256=subject_artifact.sha256,
        tenant_id=tenant_id,
    )
    subject = AuthoredGate0StructuralSubjectV3(
        run_id=request.run_id,
        case_id=request.case_id,
        tenant_id=tenant_id,
        authority_domain="structural_test",
        presentation_id=presentation_id,
        baseline_subject_digest=baseline_digest,
        mutated_subject_digest=mutated_digest,
        subject_artifact_id=subject_artifact.artifact_id,
        subject_artifact_version_id=subject_artifact.version_id,
        baseline_payload=baseline,
        mutated_payload=mutated,
        post_state={},
    )
    session.add(subject)
    await session.flush()

    spans = [
        await _event(
            session,
            subject_id=subject.id,
            seam="router",
            input_value=request_wire,
            output_value={"validated": True, "caseId": request.case_id},
            payload={"request": request_wire, "validated": True},
        ),
        await _event(
            session,
            subject_id=subject.id,
            seam="database",
            input_value={"runId": str(request.run_id), "caseId": request.case_id},
            output_value={"subjectId": str(subject.id), "persisted": True},
            payload={"subjectId": str(subject.id), "persisted": True},
            durable_record_id=subject.id,
        ),
        await _event(
            session,
            subject_id=subject.id,
            seam="publicationService",
            input_value={"presentationId": str(presentation_id), "revision": 0},
            output_value=manifest.model_dump(mode="json", by_alias=True),
            payload={
                "service": "publish_authored_semantic_bundle_graph",
                "manifestRecordId": str(manifest_record.id),
                "manifestDigest": manifest.canonical_digest,
            },
            durable_record_id=manifest_record.id,
        ),
        await _event(
            session,
            subject_id=subject.id,
            seam="productionBindingService",
            input_value=binding.model_dump(mode="json", by_alias=True),
            output_value=sealed_binding.model_dump(mode="json", by_alias=True),
            payload={
                "service": "bind_existing_export_attempt",
                "bindingRecordId": str(binding_record.id),
                "bindingDigest": sealed_binding.binding_digest,
            },
            durable_record_id=binding_record.id,
        ),
        await _event(
            session,
            subject_id=subject.id,
            seam="artifactEndpoint",
            input_value={"artifactId": subject_artifact.artifact_id, "sha256": subject_artifact.sha256},
            output_value=subject_artifact.model_dump(mode="json", by_alias=True),
            payload={
                "service": "put_durable_artifact_v3+bind_durable_artifact_v3",
                "artifact": subject_artifact.model_dump(mode="json", by_alias=True),
                "contentBase64": base64.b64encode(subject_bytes).decode("ascii"),
            },
            durable_record_id=subject_artifact.version_id,
        ),
    ]

    phase = request.mutation.phase if request.mutation and request.mutation.phase else "payload-before-issue"
    observed_error: _ObservedRejection | None = None
    changed_field = _changed_field(baseline, mutated)
    if changed_field == "nonce":
        session.add(
            AuthoredReleaseNonceV3(
                authority_domain="structural_test",
                evidence_class="synthetic_structural_only",
                issuer_id=mutated["issuerId"],
                signer_key_id=mutated["signerKeyId"],
                nonce=mutated["nonce"],
                subject_digest="sha256:" + "0" * 64,
            )
        )
        await session.flush()
    if not phase.startswith("post-"):
        try:
            if changed_field != "nonce":
                _observe_product_subject_validation(
                    dict(subject.baseline_payload),
                    dict(subject.mutated_payload),
                    phase="pre-issue",
                    now=now,
                )
        except _ObservedRejection as error:
            observed_error = error
    structural_receipt: dict[str, Any] | None = None
    if observed_error is None:
        receipt_subject_digest = baseline_digest if phase.startswith("post-") else mutated_digest
        try:
            await _consume_structural_nonce(session, payload=mutated, subject_digest=receipt_subject_digest)
            structural_receipt = {
                "schemaVersion": "authored-gate0-structural-receipt/v1",
                "authorityDomain": "structural_test",
                "evidenceClass": "synthetic_structural_only",
                "subjectDigest": receipt_subject_digest,
                "artifactVersionId": str(subject_artifact.version_id),
                "manifestDigest": manifest.canonical_digest,
                "bindingDigest": sealed_binding.binding_digest,
                "productionAuthorityGranted": False,
            }
            structural_receipt["receiptDigest"] = canonical_digest(structural_receipt)
            subject.structural_receipt_digest = structural_receipt["receiptDigest"]
        except _ObservedRejection as error:
            observed_error = error
    receipt_output = structural_receipt or {
        "issued": False,
        "rejectionCode": observed_error.code if observed_error else None,
    }
    receipt_span = await _event(
        session,
        subject_id=subject.id,
        seam="receiptEndpoint",
        input_value={"subjectDigest": mutated_digest, "authorityDomain": "structural_test"},
        output_value=receipt_output,
        payload={
            "issuer": "structural_test",
            "productionValidatorAccepted": _product_authority_accepts(mutated),
            "result": receipt_output,
        },
    )
    receipt_span["durableRecordId"] = receipt_span["evidenceRef"].rsplit("/", 1)[-1]
    spans.append(receipt_span)

    completion_cas_attempts: list[dict[str, Any]] = []
    if changed_field == "completionSeal" and phase.startswith("post-"):
        if structural_receipt is None:
            raise SemanticContractError("authored_gate0_structural_completion_receipt_missing")
        structural_completion = AuthoredGate0StructuralCompletionV3(
            subject_id=subject.id,
            authority_domain="structural_test",
            state="pending",
            cas_version=0,
            receipt_digests=[],
            completion_digest=canonical_digest({"subjectDigest": baseline_digest, "state": "pending"}),
            authority_payload=None,
        )
        session.add(structural_completion)
        await session.flush()
        first_digest = canonical_digest(
            {
                "subjectDigest": baseline_digest,
                "receiptDigest": structural_receipt["receiptDigest"],
                "state": "sealed",
            }
        )
        first_input = {
            "attempt": 1,
            "expectedCasVersion": 0,
            "completionDigest": first_digest,
        }
        await seal_completion_row_cas_v3(
            session,
            record=structural_completion,
            expected_cas_version=0,
            completion_digest=first_digest,
            receipt_digests=[structural_receipt["receiptDigest"]],
            authority_payload={
                "authorityDomain": "structural_test",
                "evidenceClass": "synthetic_structural_only",
            },
            sealed_at=now,
        )
        completion_cas_attempts.append(
            {
                **first_input,
                "operationId": str(uuid.uuid4()),
                "inputDigest": canonical_digest(first_input),
                "outcome": "sealed",
                "outputDigest": canonical_digest(
                    {
                        "state": structural_completion.state,
                        "casVersion": structural_completion.cas_version,
                        "completionDigest": structural_completion.completion_digest,
                    }
                ),
                "durableRecordId": str(structural_completion.id),
            }
        )
        second_digest = mutated["completionSeal"]["digest"]
        second_input = {
            "attempt": 2,
            "expectedCasVersion": 1,
            "completionDigest": second_digest,
        }
        try:
            await seal_completion_row_cas_v3(
                session,
                record=structural_completion,
                expected_cas_version=1,
                completion_digest=second_digest,
                receipt_digests=[structural_receipt["receiptDigest"]],
                authority_payload={
                    "authorityDomain": "structural_test",
                    "evidenceClass": "synthetic_structural_only",
                },
                sealed_at=now,
            )
        except CompletionSealCasConflictV3 as error:
            observed_error = _ObservedRejection(409, error.structural_code)
            completion_cas_attempts.append(
                {
                    **second_input,
                    "operationId": str(uuid.uuid4()),
                    "inputDigest": canonical_digest(second_input),
                    "outcome": "conflict",
                    "errorCode": error.structural_code,
                    "outputDigest": canonical_digest({"outcome": "conflict", "reason": error.reason}),
                    "durableRecordId": str(structural_completion.id),
                }
            )
        else:
            raise SemanticContractError("authored_gate0_structural_second_seal_not_rejected")

    actual_projection = await get_workbench_capability_projection_v3(
        session,
        presentation_id=presentation_id,
        owner_id=owner_id,
        tenant_id=tenant_id,
        now=now,
    )
    if actual_projection.release_profile.promotion_ready:
        raise SemanticContractError("authored_gate0_structural_product_projection_escaped")
    if observed_error is None and phase.startswith("post-") and changed_field == "artifactReference.versionId":
        try:
            await bind_durable_artifact_v3(
                session,
                artifact_id=seed_artifact.artifact_id,
                export_attempt_id=export_attempt_id,
                expected_version_id=uuid.UUID(mutated["artifactReference"]["versionId"]),
                expected_sha256=seed_artifact.sha256,
                tenant_id=tenant_id,
            )
        except SemanticContractError as error:
            if error.code != "authored_release_artifact_binding_mismatch":
                raise
            observed_error = _ObservedRejection(409, "durable-artifact-version-mismatch")
    if observed_error is None and phase.startswith("post-"):
        try:
            _observe_product_subject_validation(
                dict(subject.baseline_payload),
                dict(subject.mutated_payload),
                phase=phase,
                now=now,
            )
        except _ObservedRejection as error:
            observed_error = error
    isolated_ready = structural_receipt is not None and observed_error is None
    projection_output = {
        "isolatedProjectionPromotionReady": isolated_ready,
        "actualProductPromotionReady": actual_projection.release_profile.promotion_ready,
        "actualProductProjectionDigest": actual_projection.projection_digest,
        "rejectionCode": observed_error.code if observed_error else None,
        "completionCasAttemptsDigest": (canonical_digest(completion_cas_attempts) if completion_cas_attempts else None),
    }
    subject.projection_digest = canonical_digest(projection_output)
    spans.append(
        await _event(
            session,
            subject_id=subject.id,
            seam="projectionEndpoint",
            input_value={
                "structuralReceiptDigest": subject.structural_receipt_digest,
                "presentationId": str(presentation_id),
            },
            output_value=projection_output,
            payload={"service": "get_workbench_capability_projection_v3", "projection": projection_output},
        )
    )

    completion_count = int(
        await session.scalar(
            select(func.count())
            .select_from(AuthoredEvidenceBundleCompletionV3)
            .where(AuthoredEvidenceBundleCompletionV3.tenant_id == tenant_id)
        )
        or 0
    )
    actual_receipt_count = int(
        await session.scalar(
            select(func.count())
            .select_from(AuthoredCapabilityReceiptRecordV2)
            .where(
                AuthoredCapabilityReceiptRecordV2.tenant_id == tenant_id,
                AuthoredCapabilityReceiptRecordV2.authority_domain == "actual_product",
            )
        )
        or 0
    )
    artifact_count = int(
        await session.scalar(
            select(func.count())
            .select_from(AuthoredReleaseArtifactV3)
            .where(AuthoredReleaseArtifactV3.tenant_id == tenant_id, AuthoredReleaseArtifactV3.state == "bound")
        )
        or 0
    )
    post_state = {
        "boundArtifactCount": artifact_count,
        "structuralReceiptCount": 1 if structural_receipt else 0,
        "actualProductReceiptCount": actual_receipt_count,
        "sealedCompletionCount": completion_count,
        "actualProductPromotionReady": False,
        "structuralCompletionState": ("sealed" if completion_cas_attempts else None),
    }
    subject.post_state = post_state
    await session.flush()

    mutation_evidence = None
    if request.mutation is not None:
        mutation_evidence = {
            "jsonPaths": list(request.mutation.json_paths),
            "operator": request.mutation.operator,
            "phase": phase,
            "subjectArtifactId": subject_artifact.artifact_id,
            "baselineSubjectDigest": baseline_digest,
            "mutatedSubjectDigest": mutated_digest,
        }
        mutation_evidence["mutationDigest"] = canonical_digest(mutation_evidence)
    trace = {
        "schemaVersion": "authored-gate0-server-execution-trace/v1",
        "source": "fastapi-server-issued",
        "traceId": str(uuid.uuid4()),
        "databaseTransactionId": str(subject.id),
        "caseId": request.case_id,
        "manifestDigest": request.manifest_digest,
        "evidencePolicyDigest": policy_digest,
        "authorityDomain": "structural_test",
        "spans": spans,
        "mutationEvidence": mutation_evidence,
        "postState": post_state,
        "completionCasAttempts": completion_cas_attempts,
    }
    signer_key_id, trace_digest, signature = _sign(trace)
    trace.update({"signerKeyId": signer_key_id, "traceDigest": trace_digest, "signatureBase64": signature})
    http_status, error_code = (observed_error.status_code, observed_error.code) if observed_error else (200, None)
    payload = {
        "schemaVersion": "authored-gate0-structural-case-result/v1",
        "authorityDomain": "structural_test",
        "evidenceClass": "synthetic_structural_only",
        "productionAuthorityGranted": False,
        "productionValidatorAccepted": False,
        "errorCode": error_code,
        "isolatedProjectionPromotionReady": isolated_ready,
        "releaseReportStatus": "not-verified",
        "manifestDigest": request.manifest_digest,
        "evidencePolicyDigest": policy_digest,
        "sourceTreeCensusDigest": source_digest,
        "mutationDigest": mutation_evidence["mutationDigest"] if mutation_evidence else None,
        "mutationEvidence": mutation_evidence,
        "postState": post_state,
        "executionTrace": trace,
    }
    session.add(
        AuthoredGate0StructuralTraceV3(
            id=subject.id,
            run_id=request.run_id,
            case_id=request.case_id,
            ordinal=request.ordinal,
            authority_domain="structural_test",
            request_digest=request_digest,
            trace_digest=trace_digest,
            http_status=http_status,
            error_code=error_code,
            sealed_completion_count=completion_count,
            payload=payload,
        )
    )
    await session.flush()
    return http_status, payload


async def gate0_structural_sealed_completion_count_v3(session: AsyncSession, *, run_id: uuid.UUID, case_id: str) -> int:
    record = await session.scalar(
        select(AuthoredGate0StructuralTraceV3).where(
            AuthoredGate0StructuralTraceV3.run_id == run_id,
            AuthoredGate0StructuralTraceV3.case_id == case_id,
        )
    )
    return 0 if record is None else record.sealed_completion_count


async def read_gate0_structural_evidence_v3(session: AsyncSession, *, evidence_id: uuid.UUID) -> dict[str, Any] | None:
    record = await session.get(AuthoredGate0StructuralEventV3, evidence_id)
    if record is None:
        return None
    return {
        "schemaVersion": "authored-gate0-structural-evidence/v1",
        "evidenceId": str(record.id),
        "subjectId": str(record.subject_id),
        "authorityDomain": record.authority_domain,
        "seam": record.seam,
        "operationId": str(record.operation_id),
        "inputDigest": record.input_digest,
        "outputDigest": record.output_digest,
        "payload": record.payload,
    }


__all__ = [
    "execute_gate0_structural_case_v3",
    "gate0_structural_sealed_completion_count_v3",
    "read_gate0_structural_evidence_v3",
]
