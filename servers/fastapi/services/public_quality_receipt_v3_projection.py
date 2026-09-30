"""Verified, content-free public projections of persisted quality checkers."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from typing import Any

from models.public_v2_contracts import QualityReceiptV3 as PublicQualityReceiptV3
from services.authored_quality_receipt_v2 import validate_current_quality_receipt_payload, validate_quality_receipt_v3_payload
from services.authored_quality_receipt_v3_pipeline import _mapping, _capacity_checks_pass, _prefixed_sha256
from services.authored_vnext.master.digests import canonical_digest
from services.authored_vnext.slide_quality_acceptance import (
    SCHEMA_VERSION as SLIDE_QUALITY_SCHEMA_VERSION,
    validate_slide_quality_acceptance_receipt,
)
from services.public_quality_receipt_v3_store import quality_evidence_digest


def project_persisted_authored_quality_receipt_v3(
    *,
    authored_quality_gate: object,
    quality_receipt_id: str,
    operation_id: str,
    presentation_id: str,
    artifact_digest: str,
    source_revision: int,
    scene_digest: str,
    exporter_version: str,
    evaluated_at: datetime,
    generation_evidence_record: Mapping[str, Any] | None = None,
    review_evidence_record: Mapping[str, Any] | None = None,
    render_runtime_binding_digest: str | None = None,
    result_render_identity_digest: str | None = None,
    checker_policy_digest: str | None = None,
    _review_chain_operations: tuple[str, ...] = (),
) -> PublicQualityReceiptV3:
    """Project a persisted internal receipt to the public V3 wire contract.

    The persisted theme payload is untrusted input at this boundary. Both its
    V2 envelope and nested V3 receipt must verify their immutable digests and
    their version-transition binding before a public status can expose them.
    This adapter deliberately does not invent checker evidence: domains that
    lack a persisted public checker receipt remain failed, unavailable, pending,
    or not assessed. Only verified required domains can make quality ready;
    this is not a production promotion or deployment authorization.
    """

    gate = _mapping(authored_quality_gate)
    if gate is None:
        raise ValueError("authored_quality_gate_payload_invalid")
    internal_v2 = validate_current_quality_receipt_payload(gate)
    raw_v3 = _mapping(gate.get("qualityReceiptV3"))
    if raw_v3 is None:
        raise ValueError("authored_quality_gate_v3_missing")
    internal_v3 = validate_quality_receipt_v3_payload(raw_v3)
    if (
        internal_v3.previous_schema_version != "quality-receipt/v2"
        or internal_v3.previous_version_receipt_digest != internal_v2.receipt_digest
    ):
        raise ValueError("authored_quality_gate_version_transition_mismatch")
    if not internal_v3.capability_registry_digest:
        raise ValueError("authored_quality_gate_capability_registry_missing")
    domains: dict[str, dict[str, Any]] = {
        "content_brief": {"required": False, "status": "not_required"},
        "authoring": {"required": True, "status": "failed", "reasonCode": "authoring_checker_evidence_not_persisted"},
        "geometry_font": {"required": True, "status": "failed", "reasonCode": "geometry_font_checker_evidence_not_persisted"},
        "image_content_integrity": (
            {"required": True, "status": "failed", "reasonCode": "source_checker_evidence_not_persisted"}
            if internal_v2.require_source_readback else {"required": False, "status": "not_required"}
        ),
        "visual_review": {"required": bool(internal_v3.t1_triggers),
            "status": "failed" if internal_v3.t1_triggers else "unavailable",
            "reasonCode": "visual_checker_evidence_not_persisted"},
        "performance": {"required": internal_v2.require_performance,
            "status": "failed" if internal_v2.require_performance else "not_assessed",
            "reasonCode": "performance_checker_evidence_not_persisted"},
        "native_coverage_ooxml": {"required": False, "status": "pending", "reasonCode": "ooxml_checker_evidence_not_persisted"},
    }
    binding = {
        "operationId": operation_id, "presentationId": presentation_id,
        "artifactDigest": artifact_digest, "sourceRevision": source_revision,
        "sceneDigest": scene_digest, "exporterVersion": exporter_version,
        "renderRuntimeBindingDigest": render_runtime_binding_digest,
        "resultRenderIdentityDigest": result_render_identity_digest,
    }

    def passed(name: str, *, version: str, input_digest: str,
               output_digest: str, measurements: dict[str, Any],
               thresholds: dict[str, Any]) -> None:
        domains[name] = {
            "required": domains[name]["required"], "status": "passed",
            "evidence": {
                "checkerVersion": version,
                "inputDigest": quality_evidence_digest({**binding, "checkerInputDigest": input_digest}),
                "outputDigest": output_digest,
                "measurements": measurements, "thresholds": thresholds,
            },
        }

    generation = _mapping(generation_evidence_record)
    if generation is not None:
        evidence = _mapping(generation.get("evidence"))
        stored_receipt = _mapping(generation.get("receipt"))
        if (generation.get("schemaVersion") != "quality-receipt-event/v3"
            or generation.get("presentationId") != presentation_id
            or stored_receipt is None or evidence is None
            or validate_quality_receipt_v3_payload(stored_receipt) != internal_v3
            or quality_evidence_digest(evidence) != internal_v3.evidence_digest):
            raise ValueError("public_quality_generation_evidence_invalid")
        payloads = _mapping(evidence.get("checkerPayloads")) or {}
        capacity = _mapping(payloads.get("capacity")) or {}
        safety = _mapping(payloads.get("safety")) or {}
        visual = _mapping(payloads.get("visual")) or {}
        source_readback = _mapping(payloads.get("sourceReadback")) or {}
        slide_quality = _mapping(payloads.get("slideDocumentQualityReceipt"))
        source_content = _mapping(payloads.get("sourceContent"))
        deterministic = _mapping(evidence.get("deterministic")) or {}
        statuses = dict(internal_v3.capability_consumer_statuses)
        current_generation = (
            generation.get("resultRevision") == source_revision
            and internal_v2.deck_html_digest == scene_digest
            and payloads.get("deckHtmlDigest") == scene_digest
            and internal_v2.gate_passed
            and _prefixed_sha256(render_runtime_binding_digest)
            and _prefixed_sha256(result_render_identity_digest)
        )
        for name, check_names, payload, consumer_names in (
            ("authoring", ("content",), safety, ("prompt", "scene", "validator")),
            ("geometry_font", ("geometry", "font"), capacity, ("chromium", "font", "scene", "validator")),
        ):
            check_records = [_mapping(deterministic.get(name)) or {} for name in check_names]
            if (current_generation and all(statuses.get(name) == "passed" for name in consumer_names)
                and all(item.get("status") == "passed" and item.get("observed") is True
                        and item.get("evidenceDigest") == canonical_digest(dict(payload)) for item in check_records)
                and (name != "geometry_font" or _capacity_checks_pass(capacity, "text", "title", "body", "font", "qid", "image"))
                and (name != "authoring" or (safety.get("status") == "passed" and not safety.get("residuals", ())))):
                passed(name, version="authored-generation-checkers/v1",
                       input_digest=internal_v2.deck_html_digest,
                       output_digest=internal_v3.evidence_digest,
                       measurements={"blockingIssueCount": 0, "checkerCount": len(check_names)},
                       thresholds={"maximumBlockingIssueCount": 0, "allChecksObserved": True})
        readback_check = _mapping(deterministic.get("readback")) or {}
        source_inventory_current = (
            source_content is not None
            and source_content == _mapping(evidence.get("sourceContent"))
            and source_content.get("status") == "passed"
            and source_content.get("required") is True
            and source_content.get("sourceMode") == "text-image"
            and source_content.get("sourceCompleteClaim") is False
            and isinstance(source_content.get("occurrenceInventoryDigest"), str)
        )
        if (current_generation and internal_v2.require_source_readback
            and internal_v2.source_readback_status == "passed"
            and statuses.get("readback") == "passed"
            and source_readback.get("status") == "passed"
            and source_readback.get("required") is True
            and source_readback.get("requiredCoverage") == 1.0
            and readback_check.get("status") == "passed"
            and readback_check.get("observed") is True
            and readback_check.get("evidenceDigest") == canonical_digest(dict(source_readback))
            and source_inventory_current):
            passed("image_content_integrity", version="source-content-readback/v1",
                   input_digest=scene_digest, output_digest=internal_v3.evidence_digest,
                   measurements={
                       "sourceReadbackVerified": True,
                       "textOccurrenceCount": source_content.get("textOccurrenceCount"),
                       "imageOccurrenceCount": source_content.get("imageOccurrenceCount"),
                   }, thresholds={"sourceReadbackRequired": True, "requiredCoverage": 1.0})

        visual_check = _mapping(deterministic.get("visual")) or {}
        slide_results = slide_quality.get("slides") if slide_quality is not None else None
        visual_generation_current = (
            slide_quality is not None
            and slide_quality.get("schemaVersion") == SLIDE_QUALITY_SCHEMA_VERSION
            and validate_slide_quality_acceptance_receipt(slide_quality)
            and slide_quality.get("documentQualityPassed") is True
            and slide_quality.get("failingSlideIndexes") == []
            and slide_quality.get("blockerCodes") == []
            and isinstance(slide_results, list)
            and len(slide_results) > 0
            and slide_quality.get("observedSlideCount") == len(slide_results)
            and slide_quality.get("expectedSlideCount") == len(slide_results)
            and all(
                isinstance(item, Mapping)
                and item.get("passed") is True
                and isinstance(item.get("score"), (int, float))
                and not isinstance(item.get("score"), bool)
                and float(item["score"]) >= 90.0
                for item in slide_results
            )
            and visual.get("status") == "passed"
            and visual.get("fullDeck") is True
            and visual.get("reviewedCount") == len(slide_results)
            and visual.get("threshold") == slide_quality.get("slideScoreThreshold")
            and visual.get("minimumSlideScore") == slide_quality.get("minimumSlideScore")
            and visual.get("receiptDigest") == slide_quality.get("receiptDigest")
            and visual_check.get("status") == "passed"
            and visual_check.get("observed") is True
            and visual_check.get("evidenceDigest") == canonical_digest(dict(visual))
        )
        if (
            current_generation
            and internal_v2.visual_review_status == "passed"
            and visual_generation_current
        ):
            passed(
                "visual_review",
                version="slide-document-quality-acceptance/v1",
                input_digest=scene_digest,
                output_digest=internal_v3.evidence_digest,
                measurements={
                    "reviewedCount": len(slide_results),
                    "minimumSlideScore": slide_quality.get("minimumSlideScore"),
                    "failingSlideCount": 0,
                },
                thresholds={
                    "requiredSlideCount": len(slide_results),
                    "minimumSlideScore": 90.0,
                    "everySlideMustPass": True,
                },
            )

    review = _mapping(review_evidence_record)
    if review is not None:
        review_operation_id = str(review.get("operationId", ""))
        if review_operation_id in _review_chain_operations:
            raise ValueError("quality_review_evidence_chain_cycle")
        if len(_review_chain_operations) >= 32:
            raise ValueError("quality_review_evidence_chain_too_deep")
        receipt = _mapping(review.get("receipt")) or {}
        snapshot = _mapping(review.get("evidence")) or {}
        checker = _mapping(snapshot.get("checkerEvidence")) or {}
        post_gate = _mapping(snapshot.get("postReviewGate")) or {}
        slide_results = snapshot.get("slideResults")
        from services.workspace_quality_operation_handler import validate_export_release_review_receipt
        import uuid
        try:
            validate_export_release_review_receipt(
                receipt, operation_id=uuid.UUID(str(review.get("operationId"))),
                source_revision=review.get("sourceRevision"), result_revision=source_revision,
            )
        except (ValueError, RuntimeError) as error:
            raise ValueError("public_quality_review_evidence_invalid") from error
        slide_count = checker.get("slideCount")
        if (review.get("schemaVersion") != "export-review-evidence/v1"
            or review.get("presentationId") != presentation_id
            or review.get("resultRevision") != source_revision
            or review.get("snapshotDigest") != quality_evidence_digest(snapshot)
            or receipt.get("evidenceDigest") != quality_evidence_digest(checker)
            or receipt.get("postReviewGateDigest") != post_gate.get("evidenceDigest")
            or post_gate.get("evidenceDigest") != quality_evidence_digest({key: value for key, value in post_gate.items() if key != "evidenceDigest"})
            or checker.get("slideResultsDigest") != quality_evidence_digest({"slideResults": slide_results})
            or not isinstance(slide_count, int) or isinstance(slide_count, bool) or slide_count <= 0
            or not isinstance(slide_results, list) or len(slide_results) != slide_count
            or sorted(item.get("slide_index", -1) for item in slide_results if isinstance(item, Mapping)) != list(range(slide_count))
            or checker.get("reviewedCount") != slide_count
            or any(checker.get(key) != 0 for key in ("remainingIssueCount", "incompleteMeasurementCount", "unresolvedConfirmedIssueCount"))
            or post_gate.get("gatePassed") is not True or post_gate.get("issues") != []
            or post_gate.get("slideCount") != slide_count):
            raise ValueError("public_quality_review_checker_digest_mismatch")
        for key, expected in (
            ("resultDeckHtmlDigest", scene_digest),
            ("renderRuntimeBindingDigest", render_runtime_binding_digest),
            ("resultRenderIdentityDigest", result_render_identity_digest),
            ("checkerPolicyDigest", checker_policy_digest),
        ):
            if not _prefixed_sha256(expected) or receipt.get(key) != expected or post_gate.get(key) != expected or (key != "resultDeckHtmlDigest" and checker.get(key) != expected):
                raise ValueError("public_quality_review_runtime_or_source_mismatch")
        if (checker.get("fixedCount") != post_gate.get("fixedCount")
            or receipt.get("baseDeckHtmlDigest") != post_gate.get("baseDeckHtmlDigest")
            or (receipt.get("baseEvidenceKind") == "generation-quality-v3" and (
                generation is None or receipt.get("baseDeckHtmlDigest") != internal_v2.deck_html_digest
                or receipt.get("baseQualityReceiptDigest") != internal_v3.receipt_digest))):
            raise ValueError("public_quality_review_transition_mismatch")
        # A mutating review has new H1 capacity/DOM observations. An unchanged
        # review instead requires the original persisted generation checker.
        if isinstance(checker.get("fixedCount"), int) and checker["fixedCount"] > 0 and post_gate.get("profileId") == "ordinary":
            for name in ("authoring", "geometry_font"):
                passed(name, version="release-review-h0-h1/v2", input_digest=scene_digest,
                       output_digest=post_gate["evidenceDigest"],
                       measurements={"slideCount": slide_count, "blockingIssueCount": 0},
                       thresholds={"maximumBlockingIssueCount": 0, "fullDeckRequired": True})
        elif receipt.get("baseEvidenceKind") in {"release-review-transition", "release-recheck-transition"}:
            for name in ("authoring", "geometry_font"):
                domains[name] = {"required": True, "status": "failed",
                                 "reasonCode": "prior_checker_evidence_not_verified"}
            parent = _mapping(review.get("baseEvidenceRecord"))
            if receipt.get("baseEvidenceKind") == "release-review-transition" and parent is not None:
                parent_receipt = _mapping(parent.get("receipt")) or {}
                if (checker.get("fixedCount") != 0
                    or review.get("sourceRevision") != source_revision
                    or receipt.get("baseDeckHtmlDigest") != scene_digest
                    or receipt.get("sourceRenderIdentityDigest") != result_render_identity_digest
                    or parent.get("schemaVersion") != "export-review-evidence/v1"
                    or any(parent.get(key) != review.get(key) for key in ("ownerId", "ownerScope", "presentationId"))
                    or parent.get("operationId") != receipt.get("baseEvidenceOperationId")
                    or parent_receipt.get("receiptDigest") != receipt.get("baseQualityReceiptDigest")
                    or parent.get("resultRevision") != review.get("sourceRevision")
                    or parent_receipt.get("resultDeckHtmlDigest") != receipt.get("baseDeckHtmlDigest")
                    or parent_receipt.get("resultRenderIdentityDigest") != receipt.get("sourceRenderIdentityDigest")):
                    raise ValueError("quality_review_evidence_chain_binding_mismatch")
                # Re-run the same private checker verifier to the actual H1 or
                # generation root. A receipt header's PASS is never sufficient.
                prior_quality = project_persisted_authored_quality_receipt_v3(
                    authored_quality_gate=authored_quality_gate,
                    quality_receipt_id=quality_receipt_id, operation_id=operation_id,
                    presentation_id=presentation_id, artifact_digest=artifact_digest,
                    source_revision=source_revision, scene_digest=scene_digest,
                    exporter_version=exporter_version, evaluated_at=evaluated_at,
                    generation_evidence_record=generation_evidence_record,
                    review_evidence_record=parent,
                    render_runtime_binding_digest=render_runtime_binding_digest,
                    result_render_identity_digest=result_render_identity_digest,
                    checker_policy_digest=checker_policy_digest,
                    _review_chain_operations=(*_review_chain_operations, review_operation_id),
                )
                for name in ("authoring", "geometry_font"):
                    prior_domain = getattr(prior_quality, name)
                    if prior_domain.status == "passed" and prior_domain.evidence is not None:
                        passed(name, version=prior_domain.evidence.checker_version,
                               input_digest=scene_digest,
                               output_digest=prior_domain.evidence.output_digest,
                               measurements={**prior_domain.evidence.measurements,
                                             "sourceReviewReceiptDigest": parent_receipt["receiptDigest"]},
                               thresholds=prior_domain.evidence.thresholds)
        passed("visual_review", version="release-review-h0-h1/v2", input_digest=scene_digest,
               output_digest=receipt["evidenceDigest"],
               measurements={"reviewedCount": slide_count, "remainingIssueCount": 0, "incompleteMeasurementCount": 0},
               thresholds={"requiredSlideCount": slide_count, "maximumRemainingIssueCount": 0, "maximumIncompleteMeasurementCount": 0})
    ready = all(not domain["required"] or domain["status"] == "passed" for domain in domains.values())
    return PublicQualityReceiptV3(
        quality_receipt_id=quality_receipt_id,
        operation_id=operation_id,
        presentation_id=presentation_id,
        artifact_digest=artifact_digest,
        source_revision=source_revision,
        scene_digest=scene_digest,
        capability_registry_digest=internal_v3.capability_registry_digest,
        exporter_version=exporter_version,
        evaluated_at=evaluated_at,
        **domains,
        release_readiness="ready" if ready else "blocked",
    )
