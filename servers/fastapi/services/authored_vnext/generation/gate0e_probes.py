"""Read-only Gate 0E malformed-state probes for the current Hybrid deck."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from typing import Any
import uuid

from sqlalchemy import event, select

from models.sql import authored_editable_hybrid_mvp_completion as completion_model
from models.sql.authored_editable_hybrid_mvp_completion import (
    AuthoredEditableHybridMvpCompletion,
)
from models.sql.authored_vnext_repository import (
    AuthoredVNextExportArtifactReceipt,
    AuthoredVNextExportJob,
    AuthoredVNextPublicationPointer,
)
from models.sql.presentation_generation_job import PresentationGenerationJob
from services.authored_vnext.generation.sql_repository import SqlGenerationRepository
from services.authored_vnext.master.digests import canonical_digest


PROFILE_ID = "authored-editable-hybrid-ko-en-basic-v1"
MANIFEST_DIGEST = (
    "sha256:07c236cc2e54ba3a80f31087b63613e601e32f81b5334f64b803abb5ddd180db"
)
_CASES = (
    (1, "g0e.cas.stale-scene", 409, "editable_mvp_stale_scene_cas"),
    (2, "g0e.cas.stale-slide", 409, "editable_mvp_stale_slide_cas"),
    (3, "g0e.authority.cross-owner", 403, "editable_mvp_cross_owner"),
    (4, "g0e.authority.cross-tenant", 403, "editable_mvp_cross_tenant"),
    (5, "g0e.qid.missing", 422, "editable_mvp_qid_missing"),
    (6, "g0e.qid.duplicate", 422, "editable_mvp_qid_duplicate"),
    (7, "g0e.qid.ghost", 422, "editable_mvp_qid_ghost"),
    (8, "g0e.admission.unsupported-native", 422, "editable_mvp_unsupported_native"),
    (9, "g0e.ownership.native-raster-duplicate", 422, "editable_mvp_duplicate_owner"),
    (10, "g0e.seal.active-manifest-drift", 409, "editable_mvp_active_manifest_drift"),
    (11, "g0e.seal.artifact-byte-drift", 409, "editable_mvp_artifact_byte_drift"),
    (12, "g0e.seal.signer-nonce-replay", 409, "editable_mvp_signer_nonce_replay"),
    (13, "g0e.seal.stale-completion-cas", 409, "editable_mvp_stale_completion_cas"),
    (14, "g0e.seal.different-second-seal", 409, "editable_mvp_completion_already_sealed"),
    (15, "g0e.authority.local-receipt-external-validator", 403, "editable_mvp_receipt_not_external_authority"),
)


class Gate0eProbeError(ValueError):
    def __init__(self, code: str, *, status_code: int = 409) -> None:
        super().__init__(code)
        self.code = code
        self.status_code = status_code


@dataclass(frozen=True, slots=True)
class Gate0eCaseObservation:
    ordinal: int
    case_id: str
    mutation_payload: dict[str, Any]
    expected_http_status: int
    expected_error_code: str
    observed_http_status: int
    observed_error_code: str
    fail_closed: bool = True

    def payload(self) -> dict[str, Any]:
        return {
            "ordinal": self.ordinal,
            "caseId": self.case_id,
            "mutationPayload": self.mutation_payload,
            "expectedHttpStatus": self.expected_http_status,
            "expectedErrorCode": self.expected_error_code,
            "observedHttpStatus": self.observed_http_status,
            "observedErrorCode": self.observed_error_code,
            "failClosed": self.fail_closed,
        }


@dataclass(frozen=True, slots=True)
class Gate0eProbeResult:
    attempt: dict[str, Any]
    baseline: dict[str, Any]
    baseline_digest: str
    result_set_digest: str
    cases: tuple[Gate0eCaseObservation, ...]

    def payload(self) -> dict[str, Any]:
        return {
            "schemaVersion": "authored-editable-hybrid-gate0e-observations/v1",
            "authorityDomain": "structural_test",
            "profileId": PROFILE_ID,
            "manifestDigest": MANIFEST_DIGEST,
            "attempt": self.attempt,
            "baseline": self.baseline,
            "baselineDigest": self.baseline_digest,
            "resultSetDigest": self.result_set_digest,
            "cases": [item.payload() for item in self.cases],
            "allCasesPassed": True,
            "stateUnchanged": True,
            "promotionReady": False,
            "deploymentAuthorized": False,
        }


@dataclass(frozen=True, slots=True)
class Gate0eSlideBaseline:
    slide_id: str
    revision: int
    slide_digest: str
    owner_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class Gate0eBaseline:
    owner_scope: str
    tenant_id: str
    deck_id: str
    deck_revision: int
    deck_digest: str
    scene_digest: str
    slides: tuple[Gate0eSlideBaseline, ...]
    capabilities: tuple[tuple[str, str], ...]
    export_attempt_id: str
    publication_revision: int
    pointer_digest: str
    artifact_id: str
    artifact_digest: str
    artifact_size_bytes: int
    completion_count: int
    completion_id: str | None
    completion_result_digest: str | None
    completion_cas_version: int
    signer_nonce_digest: str | None
    completion_subject_unique: bool
    completion_nonce_unique: bool
    completion_append_only: bool
    attempt: dict[str, Any]

    def payload(self) -> dict[str, Any]:
        return {
            "ownerScope": self.owner_scope,
            "tenantId": self.tenant_id,
            "deckId": self.deck_id,
            "deckRevision": self.deck_revision,
            "deckDigest": self.deck_digest,
            "sceneDigest": self.scene_digest,
            "slides": [
                {
                    "slideId": slide.slide_id,
                    "revision": slide.revision,
                    "slideDigest": slide.slide_digest,
                    "ownerIds": list(slide.owner_ids),
                }
                for slide in self.slides
            ],
            "capabilities": dict(self.capabilities),
            "exportAttemptId": self.export_attempt_id,
            "publicationRevision": self.publication_revision,
            "pointerDigest": self.pointer_digest,
            "artifactId": self.artifact_id,
            "artifactDigest": self.artifact_digest,
            "artifactSizeBytes": self.artifact_size_bytes,
            "completionId": self.completion_id,
            "completionCount": self.completion_count,
            "completionResultDigest": self.completion_result_digest,
            "completionCasVersion": self.completion_cas_version,
            "signerNonceDigest": self.signer_nonce_digest,
            "completionSubjectUnique": self.completion_subject_unique,
            "completionNonceUnique": self.completion_nonce_unique,
            "completionAppendOnly": self.completion_append_only,
            "attempt": self.attempt,
        }

    def public_payload(self) -> dict[str, Any]:
        return {
            "deckId": self.deck_id,
            "deckRevision": self.deck_revision,
            "deckDigest": self.deck_digest,
            "sceneDigest": self.scene_digest,
            "ownerCount": sum(len(slide.owner_ids) for slide in self.slides),
            "exportAttemptId": self.export_attempt_id,
            "publicationRevision": self.publication_revision,
            "pointerDigest": self.pointer_digest,
            "artifactId": self.artifact_id,
            "artifactDigest": self.artifact_digest,
            "artifactSizeBytes": self.artifact_size_bytes,
            "completionCount": self.completion_count,
            "completionId": self.completion_id,
            "completionResultDigest": self.completion_result_digest,
            "completionCasVersion": self.completion_cas_version,
            "completionSubjectUnique": self.completion_subject_unique,
            "completionNonceUnique": self.completion_nonce_unique,
            "completionAppendOnly": self.completion_append_only,
        }


def _other_digest(value: str) -> str:
    replacement = "0" if value[-1] != "0" else "1"
    return value[:-1] + replacement


def _mutation_payloads(baseline: Gate0eBaseline) -> dict[str, dict[str, Any]]:
    slide = baseline.slides[0]
    owner_id = slide.owner_ids[0]
    unsupported_kind = next(
        (kind for kind, state in baseline.capabilities if state == "unsupported"),
        None,
    )
    if unsupported_kind is None:
        raise Gate0eProbeError("editable_mvp_gate0e_unsupported_capability_missing")
    hypothetical_first_digest = baseline.completion_result_digest or canonical_digest(
        {"deckDigest": baseline.deck_digest, "seal": "first"}
    )
    nonce_digest = baseline.signer_nonce_digest or canonical_digest(
        {"deckDigest": baseline.deck_digest, "nonce": "hypothetical"}
    )
    return {
        "g0e.cas.stale-scene": {
            "expectedSceneDigest": _other_digest(baseline.scene_digest)
        },
        "g0e.cas.stale-slide": {
            "slideId": slide.slide_id,
            "expectedSlideRevision": slide.revision + 1,
            "expectedSlideDigest": _other_digest(slide.slide_digest),
        },
        "g0e.authority.cross-owner": {
            "claimedOwnerScope": "owner:foreign"
        },
        "g0e.authority.cross-tenant": {
            "claimedTenantId": "tenant:foreign"
        },
        "g0e.qid.missing": {"ownerId": None},
        "g0e.qid.duplicate": {"ownerIds": [owner_id, owner_id]},
        "g0e.qid.ghost": {"ownerId": owner_id + ":ghost"},
        "g0e.admission.unsupported-native": {
            "ownerId": owner_id,
            "capabilityKind": unsupported_kind,
            "decision": "nativeEditable",
        },
        "g0e.ownership.native-raster-duplicate": {
            "nativeOwnerIds": [owner_id],
            "rasterOwnerIds": [owner_id],
        },
        "g0e.seal.active-manifest-drift": {
            "expectedActiveManifestDigest": _other_digest(baseline.deck_digest)
        },
        "g0e.seal.artifact-byte-drift": {
            "artifactDigest": _other_digest(baseline.artifact_digest),
            "artifactSizeBytes": baseline.artifact_size_bytes,
        },
        "g0e.seal.signer-nonce-replay": {
            "firstSignerNonceDigest": nonce_digest,
            "secondSignerNonceDigest": nonce_digest,
            "existingCompletionCount": baseline.completion_count,
        },
        "g0e.seal.stale-completion-cas": {
            "expectedCompletionCasVersion": baseline.completion_cas_version + 1
        },
        "g0e.seal.different-second-seal": {
            "existingCompletionCount": baseline.completion_count,
            "firstCompletionResultDigest": hypothetical_first_digest,
            "secondCompletionResultDigest": _other_digest(
                hypothetical_first_digest
            ),
            "subjectKey": [baseline.deck_id, baseline.deck_revision],
        },
        "g0e.authority.local-receipt-external-validator": {
            "receiptAuthorityDomain": "structural_test",
            "validatorAuthorityDomain": "external_promotion",
        },
    }


def _evaluate(
    case_id: str, payload: dict[str, Any], baseline: Gate0eBaseline
) -> tuple[int, str]:
    owners = {
        owner_id for slide in baseline.slides for owner_id in slide.owner_ids
    }
    if case_id == "g0e.cas.stale-scene" and payload.get(
        "expectedSceneDigest"
    ) != baseline.scene_digest:
        return 409, "editable_mvp_stale_scene_cas"
    if case_id == "g0e.cas.stale-slide":
        slide = next(
            (item for item in baseline.slides if item.slide_id == payload.get("slideId")),
            None,
        )
        if slide is None or (
            payload.get("expectedSlideRevision") != slide.revision
            or payload.get("expectedSlideDigest") != slide.slide_digest
        ):
            return 409, "editable_mvp_stale_slide_cas"
    if case_id == "g0e.authority.cross-owner" and payload.get(
        "claimedOwnerScope"
    ) != baseline.owner_scope:
        return 403, "editable_mvp_cross_owner"
    if case_id == "g0e.authority.cross-tenant" and payload.get(
        "claimedTenantId"
    ) != baseline.tenant_id:
        return 403, "editable_mvp_cross_tenant"
    if case_id == "g0e.qid.missing" and not payload.get("ownerId"):
        return 422, "editable_mvp_qid_missing"
    if case_id == "g0e.qid.duplicate":
        values = payload.get("ownerIds")
        if isinstance(values, list) and len(values) != len(set(values)):
            return 422, "editable_mvp_qid_duplicate"
    if case_id == "g0e.qid.ghost" and payload.get("ownerId") not in owners:
        return 422, "editable_mvp_qid_ghost"
    if case_id == "g0e.admission.unsupported-native":
        capability = dict(baseline.capabilities).get(payload.get("capabilityKind"))
        if payload.get("decision") == "nativeEditable" and capability != "supported":
            return 422, "editable_mvp_unsupported_native"
    if case_id == "g0e.ownership.native-raster-duplicate":
        native = set(payload.get("nativeOwnerIds") or [])
        raster = set(payload.get("rasterOwnerIds") or [])
        if native.intersection(raster):
            return 422, "editable_mvp_duplicate_owner"
    if case_id == "g0e.seal.active-manifest-drift" and payload.get(
        "expectedActiveManifestDigest"
    ) != baseline.deck_digest:
        return 409, "editable_mvp_active_manifest_drift"
    if case_id == "g0e.seal.artifact-byte-drift" and (
        payload.get("artifactDigest") != baseline.artifact_digest
        or payload.get("artifactSizeBytes") != baseline.artifact_size_bytes
    ):
        return 409, "editable_mvp_artifact_byte_drift"
    if case_id == "g0e.seal.signer-nonce-replay" and (
        baseline.completion_nonce_unique
        and payload.get("firstSignerNonceDigest")
        == payload.get("secondSignerNonceDigest")
    ):
        return 409, "editable_mvp_signer_nonce_replay"
    if case_id == "g0e.seal.stale-completion-cas" and payload.get(
        "expectedCompletionCasVersion"
    ) != baseline.completion_cas_version:
        return 409, "editable_mvp_stale_completion_cas"
    if case_id == "g0e.seal.different-second-seal" and (
        baseline.completion_subject_unique
        and baseline.completion_append_only
        and payload.get("firstCompletionResultDigest")
        != payload.get("secondCompletionResultDigest")
        and payload.get("subjectKey")
        == [baseline.deck_id, baseline.deck_revision]
    ):
        return 409, "editable_mvp_completion_already_sealed"
    if case_id == "g0e.authority.local-receipt-external-validator" and (
        payload.get("receiptAuthorityDomain") == "structural_test"
        and payload.get("validatorAuthorityDomain") == "external_promotion"
    ):
        return 403, "editable_mvp_receipt_not_external_authority"
    raise Gate0eProbeError("editable_mvp_gate0e_probe_did_not_fail_closed")


def evaluate_gate0e_cases(
    baseline: Gate0eBaseline,
) -> tuple[list[Gate0eCaseObservation], str]:
    mutations = _mutation_payloads(baseline)
    observations: list[Gate0eCaseObservation] = []
    for ordinal, case_id, expected_status, expected_code in _CASES:
        mutation = mutations[case_id]
        observed_status, observed_code = _evaluate(case_id, mutation, baseline)
        if (observed_status, observed_code) != (expected_status, expected_code):
            raise Gate0eProbeError("editable_mvp_gate0e_probe_result_mismatch")
        observations.append(
            Gate0eCaseObservation(
                ordinal=ordinal,
                case_id=case_id,
                mutation_payload=mutation,
                expected_http_status=expected_status,
                expected_error_code=expected_code,
                observed_http_status=observed_status,
                observed_error_code=observed_code,
            )
        )
    result_digest = canonical_digest(
        {
            "manifestDigest": MANIFEST_DIGEST,
            "baselineDigest": canonical_digest(baseline.payload()),
            "cases": [
                item.payload() for item in observations
            ],
        }
    )
    return observations, result_digest


def _load_current_baseline(
    repository: SqlGenerationRepository, deck_id: str
) -> Gate0eBaseline:
    deck = repository.get_deck(deck_id)
    scene = deck.scene
    if scene is None or scene.hybrid_profile_id != PROFILE_ID:
        raise Gate0eProbeError("editable_mvp_gate0e_scene_baseline_missing")
    if not scene.slides or any(not slide.elements for slide in scene.slides):
        raise Gate0eProbeError("editable_mvp_gate0e_owner_baseline_missing")
    base = repository._base
    with repository._sessions() as session:
        completions = list(session.scalars(
            select(AuthoredEditableHybridMvpCompletion).where(
                AuthoredEditableHybridMvpCompletion.owner_id == base.owner_id,
                AuthoredEditableHybridMvpCompletion.owner_scope == base.owner_scope,
                AuthoredEditableHybridMvpCompletion.tenant_id == base.tenant_id,
                AuthoredEditableHybridMvpCompletion.deck_id == deck.deck_id,
                AuthoredEditableHybridMvpCompletion.deck_revision == deck.revision,
            )
        ).all())
        if len(completions) > 1:
            raise Gate0eProbeError("editable_mvp_gate0e_completion_uniqueness_broken")
        completion = completions[0] if completions else None
        if completion is not None and (
            completion.deck_digest != deck.digest
            or completion.scene_digest != scene.scene_digest
            or completion.profile_id != PROFILE_ID
        ):
            raise Gate0eProbeError("editable_mvp_gate0e_completion_baseline_stale")
        pointer = session.scalar(
            select(AuthoredVNextPublicationPointer).where(
                AuthoredVNextPublicationPointer.owner_id == base.owner_id,
                AuthoredVNextPublicationPointer.owner_scope == base.owner_scope,
                AuthoredVNextPublicationPointer.tenant_id == base.tenant_id,
                AuthoredVNextPublicationPointer.publication_kind == "deck_artifact",
                AuthoredVNextPublicationPointer.entity_id == deck.deck_id,
            )
        )
        if pointer is None or pointer.artifact_id is None:
            raise Gate0eProbeError("editable_mvp_gate0e_active_publication_missing")
        artifact = session.scalar(
            select(AuthoredVNextExportArtifactReceipt).where(
                AuthoredVNextExportArtifactReceipt.id == pointer.artifact_id,
                AuthoredVNextExportArtifactReceipt.owner_id == base.owner_id,
                AuthoredVNextExportArtifactReceipt.owner_scope == base.owner_scope,
                AuthoredVNextExportArtifactReceipt.tenant_id == base.tenant_id,
                AuthoredVNextExportArtifactReceipt.deck_id == deck.deck_id,
                AuthoredVNextExportArtifactReceipt.deck_revision == deck.revision,
                AuthoredVNextExportArtifactReceipt.readback_verified.is_(True),
            )
        )
        if artifact is None or artifact.artifact_digest != pointer.digest:
            raise Gate0eProbeError("editable_mvp_gate0e_active_publication_drift")
        export_job = session.scalar(
            select(AuthoredVNextExportJob).where(
                AuthoredVNextExportJob.id == artifact.job_id,
                AuthoredVNextExportJob.owner_id == base.owner_id,
                AuthoredVNextExportJob.owner_scope == base.owner_scope,
                AuthoredVNextExportJob.tenant_id == base.tenant_id,
                AuthoredVNextExportJob.deck_id == deck.deck_id,
                AuthoredVNextExportJob.deck_revision == deck.revision,
                AuthoredVNextExportJob.deck_digest == deck.digest,
                AuthoredVNextExportJob.state == "succeeded",
            )
        )
        if export_job is None:
            raise Gate0eProbeError("editable_mvp_gate0e_export_baseline_missing")
        export_attempt_id = export_job.id
        expected_pointer_revision = (
            1
            if export_job.expected_publication_revision is None
            else export_job.expected_publication_revision + 1
        )
        if pointer.revision != expected_pointer_revision:
            raise Gate0eProbeError("editable_mvp_gate0e_active_publication_drift")
        if completion is not None and completion.hybrid_export_attempt_id != str(
            export_attempt_id
        ):
            raise Gate0eProbeError("editable_mvp_gate0e_completion_baseline_stale")
        expected_qids = sorted(
            element.element_id
            for slide in scene.slides
            for element in slide.elements
        )
        if completion is not None:
            if (
                "sha256:"
                + hashlib.sha256(completion.sealed_result_bytes).hexdigest()
                != completion.sealed_result_sha256
            ):
                raise Gate0eProbeError("editable_mvp_gate0e_completion_bytes_changed")
            try:
                result = json.loads(completion.sealed_result_bytes.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as error:
                raise Gate0eProbeError(
                    "editable_mvp_gate0e_completion_bytes_invalid"
                ) from error
            attempt = result.get("attempt") if isinstance(result, dict) else None
            if not isinstance(attempt, dict) or (
                attempt.get("presentationRevision") != deck.revision
                or attempt.get("sceneDigest") != scene.scene_digest
                or attempt.get("activeManifestDigest") != deck.digest
                or attempt.get("hybridExportAttemptId") != str(export_attempt_id)
                or attempt.get("expectedQids") != expected_qids
            ):
                raise Gate0eProbeError("editable_mvp_gate0e_attempt_baseline_stale")
        else:
            request_payload = (
                export_job.request_payload
                if isinstance(export_job.request_payload, dict)
                else {}
            )
            try:
                presentation_uuid = uuid.UUID(
                    hex=deck.deck_id.removeprefix("deck:semantic-")
                )
            except ValueError as error:
                raise Gate0eProbeError(
                    "editable_mvp_gate0e_deck_subject_invalid"
                ) from error
            presentation_id = str(presentation_uuid)
            generation_job = session.scalar(
                select(PresentationGenerationJob).where(
                    PresentationGenerationJob.presentation_id == presentation_uuid,
                    PresentationGenerationJob.state == "published",
                )
            )
            if generation_job is None or not generation_job.task_id:
                raise Gate0eProbeError(
                    "editable_mvp_gate0e_generation_baseline_missing"
                )
            gate_minus1_digest = request_payload.get("gateMinus1eResultSetDigest")
            if not isinstance(gate_minus1_digest, str) or not gate_minus1_digest.startswith("sha256:"):
                gate_minus1_digest = canonical_digest(
                    {"deckDigest": deck.digest, "gateMinus1e": "not-bound"}
                )
            source_head = request_payload.get("sourceHeadCommit")
            if not isinstance(source_head, str) or len(source_head) != 40:
                source_head = "0" * 40
            generation_attempt_id = request_payload.get("generationAttemptId")
            if generation_attempt_id is not None and generation_attempt_id != generation_job.task_id:
                raise Gate0eProbeError(
                    "editable_mvp_gate0e_generation_attempt_drift"
                )
            generation_attempt_id = generation_job.task_id
            attempt = {
                "presentationId": presentation_id,
                "presentationRevision": deck.revision,
                "generationAttemptId": generation_attempt_id,
                "hybridExportAttemptId": str(export_attempt_id),
                "sceneDigest": scene.scene_digest,
                "activeManifestDigest": deck.digest,
                "gateMinus1eResultSetDigest": gate_minus1_digest,
                "gate0eResultSetDigest": canonical_digest(
                    {"deckDigest": deck.digest, "gate0e": "pending"}
                ),
                "sourceHeadCommit": source_head,
                "expectedQids": expected_qids,
            }
        constraint_names = {
            constraint.name
            for constraint in AuthoredEditableHybridMvpCompletion.__table__.constraints
            if constraint.name is not None
        }
        subject_unique = "uq_authored_editable_mvp_completion_v1_subject" in constraint_names
        nonce_unique = "uq_authored_editable_mvp_completion_v1_nonce" in constraint_names
        append_only = event.contains(
            AuthoredEditableHybridMvpCompletion,
            "before_update",
            completion_model._reject_mutation,
        ) and event.contains(
            AuthoredEditableHybridMvpCompletion,
            "before_delete",
            completion_model._reject_mutation,
        )
        if not (subject_unique and nonce_unique and append_only):
            raise Gate0eProbeError("editable_mvp_gate0e_completion_structure_invalid")
        return Gate0eBaseline(
            owner_scope=base.owner_scope,
            tenant_id=base.tenant_id,
            deck_id=deck.deck_id,
            deck_revision=deck.revision,
            deck_digest=deck.digest,
            scene_digest=scene.scene_digest,
            slides=tuple(
                Gate0eSlideBaseline(
                    slide_id=slide.slide_id,
                    revision=slide.revision,
                    slide_digest=slide.slide_digest,
                    owner_ids=tuple(element.element_id for element in slide.elements),
                )
                for slide in scene.slides
            ),
            capabilities=tuple(
                (item.kind, item.native_pptx_export) for item in scene.capabilities
            ),
            export_attempt_id=str(export_attempt_id),
            publication_revision=pointer.revision,
            pointer_digest=pointer.digest,
            artifact_id=str(artifact.id),
            artifact_digest=artifact.artifact_digest,
            artifact_size_bytes=artifact.size_bytes,
            completion_count=len(completions),
            completion_id=str(completion.id) if completion is not None else None,
            completion_result_digest=(
                completion.result_set_digest if completion is not None else None
            ),
            completion_cas_version=(completion.cas_version if completion is not None else 0),
            signer_nonce_digest=(
                "sha256:" + hashlib.sha256(completion.receipt_nonce.encode("utf-8")).hexdigest()
                if completion is not None
                else None
            ),
            completion_subject_unique=subject_unique,
            completion_nonce_unique=nonce_unique,
            completion_append_only=append_only,
            attempt=dict(attempt),
        )


def run_gate0e_structural_probes(
    repository: SqlGenerationRepository,
    *,
    deck_id: str,
    gate_minus1e_result_set_digest: str | None = None,
    source_head_commit: str | None = None,
) -> Gate0eProbeResult:
    before = _load_current_baseline(repository, deck_id)
    observations, result_digest = evaluate_gate0e_cases(before)
    after = _load_current_baseline(repository, deck_id)
    if before.payload() != after.payload():
        raise Gate0eProbeError("editable_mvp_gate0e_baseline_changed")
    attempt = dict(before.attempt)
    if gate_minus1e_result_set_digest is not None:
        if len(gate_minus1e_result_set_digest) != 71 or not gate_minus1e_result_set_digest.startswith("sha256:"):
            raise Gate0eProbeError("editable_mvp_gate0e_gate_minus1_digest_invalid", status_code=422)
        attempt["gateMinus1eResultSetDigest"] = gate_minus1e_result_set_digest
    if source_head_commit is not None:
        if len(source_head_commit) != 40 or any(character not in "0123456789abcdefABCDEF" for character in source_head_commit):
            raise Gate0eProbeError("editable_mvp_gate0e_source_head_invalid", status_code=422)
        attempt["sourceHeadCommit"] = source_head_commit.lower()
    attempt["gate0eResultSetDigest"] = result_digest
    return Gate0eProbeResult(
        attempt=attempt,
        baseline=before.public_payload(),
        baseline_digest=canonical_digest(before.payload()),
        result_set_digest=result_digest,
        cases=tuple(observations),
    )


__all__ = [
    "Gate0eBaseline",
    "Gate0eCaseObservation",
    "Gate0eProbeError",
    "Gate0eProbeResult",
    "Gate0eSlideBaseline",
    "evaluate_gate0e_cases",
    "run_gate0e_structural_probes",
]
