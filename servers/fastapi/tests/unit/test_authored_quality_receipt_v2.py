from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest

from services.authored_vnext.master.digests import canonical_digest
from services.authored_quality_receipt_v2 import (
    QualityReceiptLedger,
    QualityReceiptV3Ledger,
    append_quality_receipt_v3_evidence,
    create_quality_receipt_v2,
    create_quality_receipt_v3,
    create_quality_receipt_v3_from_v2,
    evaluate_capacity_browser_metrics,
    evaluate_unified_gate,
    public_attempt_id,
    quality_receipt_manifest_path,
    quality_receipt_t1_triggers,
    release_profile_requirements,
    release_quality_policy,
)


def _v3_digest(value: str) -> str:
    return "sha256:" + value * 64


def _v3_evidence(*, tier: str = "T1", source_complete_claim: bool = False) -> dict[str, object]:
    registry_digest = _v3_digest("a")
    source_content = {
        "sourceMode": "text-image",
        "sourceCompleteClaim": source_complete_claim,
        "preserved": ["text", "image-occurrences"],
        "excluded": ["tables", "charts", "original-layout"],
        "excludedSourceObjectCounts": {"tables": 2, "charts": 1, "originalLayoutShapes": 7},
        "textOccurrences": [
            {"sourceBlockId": f"text-{index}", "contentDigest": _v3_digest("4")}
            for index in range(43)
        ],
        "imageOccurrences": [
            {"sourceBlockId": f"image-{index}", "contentDigest": _v3_digest("5")}
            for index in range(96)
        ],
        "textOccurrenceCount": 43,
        "imageOccurrenceCount": 96,
    }
    source_content["occurrenceInventoryDigest"] = canonical_digest(
        {
            "schemaVersion": "source-content-text-image-inventory/v1",
            **{
                key: value
                for key, value in source_content.items()
                if key not in {"textOccurrenceCount", "imageOccurrenceCount"}
            },
        }
    )
    return {
        "qualityTier": tier,
        "capabilityRegistry": {
            "digest": registry_digest,
            "consumers": {
                name: registry_digest
                for name in ("prompt", "validator", "font", "chromium", "scene", "ooxml", "readback")
            },
        },
        "deterministic": {
            name: {"status": "passed", "observed": True, "evidenceDigest": _v3_digest(value)}
            for name, value in (("geometry", "b"), ("font", "c"), ("content", "d"))
        },
        "providerCalls": {
            "slides": [
                {"slideIndex": 0, "initial": 1, "recovery": 0, "total": 1},
                {"slideIndex": 1, "initial": 1, "recovery": 1, "total": 2},
            ]
        },
        "sourceContent": source_content,
        "slides": [
            {"role": "cover"},
            {"layoutCapacity": 0.81, "hasTable": True, "repairApplied": True},
        ],
        "visualReview": {"status": "passed", "fullDeck": False},
    }


def test_generation_wires_job_scoped_durable_receipt_manifest() -> None:
    """Characterize the product call site, not only the receipt helper.

    Before the P0 wiring this AST check failed because generation relied on the
    in-memory ``DeckEvidenceLedger.receipt_store`` and omitted ``storage_path``.
    Keeping the check at the call site prevents a future refactor from silently
    regressing to process-local receipt idempotency.
    """

    source_path = Path(__file__).parents[2] / "services" / "authored_presentation_generation.py"
    tree = ast.parse(source_path.read_text(encoding="utf-8"))
    calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "create_quality_receipt_v2"
    ]
    assert calls, "generation must construct QualityReceipt v2"
    assert any(
        any(keyword.arg == "storage_path" for keyword in call.keywords)
        for call in calls
    ), "generation receipt construction must pass a durable storage_path"


def test_job_manifest_path_is_stable_and_does_not_embed_private_ids(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("AUTHORED_QUALITY_RECEIPT_DIRECTORY", str(tmp_path))
    private_job_id = "worker-lease-token-123"
    path = quality_receipt_manifest_path(private_job_id)
    assert path.parent == tmp_path.resolve()
    assert private_job_id not in path.name
    assert quality_receipt_manifest_path(private_job_id) == path


def _metrics() -> dict[str, object]:
    return {
        "elements": [
            {"qid": "title", "semantic_role": "title", "rendered_text": "Title", "font_size": 24, "font_loaded": True},
            {"qid": "body", "semantic_role": "body", "rendered_text": "Body text", "font_size": 18, "font_loaded": True},
        ]
    }


def _capacity() -> dict[str, object]:
    return {"maxQids": 8, "maxImages": 1, "maxVisibleTextChars": 100, "minFontPx": 16}


def test_capacity_checks_are_derived_from_browser_visibility() -> None:
    result = evaluate_capacity_browser_metrics(
        _metrics(), _capacity(), expected_title="Title", expected_body="Body text"
    )
    assert result["browserObserved"] is True
    assert result["checks"] == {
        "text": True,
        "title": True,
        "body": True,
        "font": True,
        "qid": True,
        "image": True,
    }


def test_capacity_ignores_layout_wrapper_text_but_keeps_all_qids_unique() -> None:
    metrics = _metrics()
    metrics["elements"] = [
        {
            "qid": "layout-wrapper",
            "semantic_role": "body",
            "rendered_text": "Title Body text" * 50,
            "has_owned_text": False,
            "font_size": 8,
            "font_loaded": False,
        },
        *metrics["elements"],
    ]

    result = evaluate_capacity_browser_metrics(
        metrics, _capacity(), expected_title="Title", expected_body="Body text"
    )

    assert result["checks"] == {
        "text": True,
        "title": True,
        "body": True,
        "font": True,
        "qid": True,
        "image": True,
    }


def test_supporting_text_uses_legibility_floor_without_consuming_content_qids() -> None:
    metrics = _metrics()
    metrics["elements"] = [
        *metrics["elements"],
        *[
            {
                "qid": f"label-{index}",
                "semantic_role": "supporting",
                "rendered_text": str(index),
                "has_owned_text": True,
                "font_size": 12,
                "font_loaded": True,
            }
            for index in range(12)
        ],
    ]

    result = evaluate_capacity_browser_metrics(metrics, _capacity())

    assert result["checks"]["qid"] is True
    assert result["checks"]["font"] is True


def test_expected_title_and_body_require_declared_semantic_roles() -> None:
    metrics = {
        "elements": [
            {"qid": "decor-1", "rendered_text": "Title", "font_size": 24, "font_loaded": True},
            {"qid": "decor-2", "rendered_text": "Body text", "font_size": 18, "font_loaded": True},
        ]
    }
    result = evaluate_capacity_browser_metrics(
        metrics, _capacity(), expected_title="Title", expected_body="Body text"
    )
    assert result["checks"]["title"] is False
    assert result["checks"]["body"] is False
    assert "title:semantic_role_missing" in result["violations"]
    assert "body:semantic_role_missing" in result["violations"]


def test_image_capacity_requires_image_semantic_role() -> None:
    image = {
        "qid": "image-1",
        "tag": "img",
        "semantic_role": "sourceImage",
        "complete": True,
        "naturalWidth": 640,
        "naturalHeight": 360,
    }
    valid = evaluate_capacity_browser_metrics(
        {"elements": [*_metrics()["elements"], image]}, _capacity()
    )
    assert valid["checks"]["image"] is True

    invalid_image = {**image, "semantic_role": "decoration"}
    invalid = evaluate_capacity_browser_metrics(
        {"elements": [*_metrics()["elements"], invalid_image]}, _capacity()
    )
    assert invalid["checks"]["image"] is False
    assert "image:semantic_role_missing:image-1" in invalid["violations"]


def test_unified_gate_keeps_safety_and_source_failures_as_blockers() -> None:
    capacity = evaluate_capacity_browser_metrics(_metrics(), _capacity())
    evaluation = evaluate_unified_gate(
        {
            "safety": {"status": "passed", "residuals": ["occluded:q2"]},
            "capacity": capacity,
            "sourceReadback": {"status": "passed", "verified": False},
            "visualReview": {"status": "pending"},
            "performance": {"status": "passed"},
        }
    )
    assert evaluation.passed is False
    assert evaluation.quality_status == "blocked"
    assert evaluation.publication_status == "pending"
    assert "safety.residual" in evaluation.blockers
    assert "sourceReadback.failed" in evaluation.blockers
    assert evaluation.visual_review_status == "not_assessed"


def test_receipt_is_created_once_and_digest_binding_is_immutable() -> None:
    capacity = evaluate_capacity_browser_metrics(_metrics(), _capacity())
    evidence = {
        "safety": {"status": "passed", "residuals": []},
        "capacity": capacity,
        "sourceReadback": {"status": "passed", "verified": True},
        "visualReview": {"status": "passed"},
        "performance": {"status": "passed"},
    }
    ledger = QualityReceiptLedger()
    first = create_quality_receipt_v2(
        ledger,
        attempt_id="attempt-1",
        revision=2,
        ledger_digest="sha256:" + "1" * 64,
        deck_html_digest="sha256:" + "2" * 64,
        evidence=evidence,
    )
    second = create_quality_receipt_v2(
        ledger,
        attempt_id="attempt-1",
        revision=2,
        ledger_digest="sha256:" + "1" * 64,
        deck_html_digest="sha256:" + "2" * 64,
        evidence={"capacity": {"status": "failed"}},
    )
    assert first is second
    assert first.schema_version == "quality-receipt/v2"
    assert first.gate_passed is True
    assert first.to_dict()["attemptId"] == public_attempt_id("attempt-1")
    with pytest.raises(ValueError, match="binding_changed"):
        create_quality_receipt_v2(
            ledger,
            attempt_id="attempt-1",
            revision=2,
            ledger_digest="sha256:" + "3" * 64,
            deck_html_digest="sha256:" + "2" * 64,
            evidence=evidence,
        )


def test_requested_release_and_pre_export_policy_are_separately_digest_bound() -> None:
    scene = {
        "sceneDigest": "sha256:" + "3" * 64,
        "sourceDigest": "sha256:" + "4" * 64,
        "qidDigest": "sha256:" + "5" * 64,
    }
    receipt = create_quality_receipt_v2(
        QualityReceiptLedger(),
        attempt_id="release-attempt",
        revision=3,
        ledger_digest="sha256:" + "1" * 64,
        deck_html_digest="sha256:" + "2" * 64,
        evidence={
            "safety": {"status": "passed"},
            "capacity": {
                "browserObserved": True,
                "checks": {
                    name: True
                    for name in ("text", "title", "body", "font", "qid", "image")
                },
            },
            "sourceReadback": {"required": False, "status": "not_applicable"},
        },
        requested_release_profile="release",
        pre_export_evaluation_profile="default",
        scene_envelope=scene,
    )

    payload = receipt.to_dict()
    assert payload["releaseProfile"] == "release"
    assert payload["requestedReleaseProfile"] == "release"
    assert payload["preExportEvaluationProfile"] == "default"
    assert payload["requestedReleasePolicyDigest"] != payload[
        "preExportEvaluationPolicyDigest"
    ]
    assert payload["sceneEnvelopeDigest"] == scene["sceneDigest"]
    assert receipt.gate_passed is True


def test_missing_browser_metrics_fails_capacity_closed() -> None:
    result = evaluate_unified_gate(
        {
            "safety": {"status": "passed", "residuals": []},
            "capacity": {"status": "passed"},
            "sourceReadback": {"status": "passed", "verified": True},
        }
    )
    assert result.capacity_status == "blocked"
    assert "capacity.browser_observation_missing" in result.blockers


def test_source_readback_status_requires_explicit_verified_true() -> None:
    result = evaluate_unified_gate(
        {
            "safety": {"status": "passed"},
            "capacity": {"browserObserved": True, "checks": {name: True for name in ("text", "title", "body", "font", "qid", "image")}},
            "sourceReadback": {"status": "passed"},
        }
    )
    assert result.source_readback_status == "blocked"
    assert "sourceReadback.failed" in result.blockers


def test_receipt_manifest_survives_restart_and_rejects_digest_drift(tmp_path) -> None:
    path = tmp_path / "quality-receipts.json"
    capacity = evaluate_capacity_browser_metrics(_metrics(), _capacity())
    evidence = {
        "safety": {"status": "passed"},
        "capacity": capacity,
        "sourceReadback": {"verified": True},
    }
    first = create_quality_receipt_v2(
        QualityReceiptLedger(storage_path=path),
        attempt_id="lease-secret-token",
        revision=1,
        ledger_digest="sha256:" + "a" * 64,
        deck_html_digest="sha256:" + "b" * 64,
        evidence=evidence,
    )
    restarted = QualityReceiptLedger(storage_path=path)
    second = create_quality_receipt_v2(
        restarted,
        attempt_id="lease-secret-token",
        revision=1,
        ledger_digest="sha256:" + "a" * 64,
        deck_html_digest="sha256:" + "b" * 64,
        evidence={"capacity": {"status": "failed"}},
    )
    assert second is not first
    assert second.to_dict() == first.to_dict()
    assert "lease-secret-token" not in path.read_text(encoding="utf-8")
    with pytest.raises(ValueError, match="binding_changed"):
        create_quality_receipt_v2(
            restarted,
            attempt_id="lease-secret-token",
            revision=1,
            ledger_digest="sha256:" + "c" * 64,
            deck_html_digest="sha256:" + "b" * 64,
            evidence=evidence,
        )


def test_optional_release_domains_are_not_assessed_unless_profile_requires_them() -> None:
    evaluation = evaluate_unified_gate(
        {
            "safety": {"status": "passed"},
            "capacity": {"browserObserved": True, "checks": {name: True for name in ("text", "title", "body", "font", "qid", "image")}},
            "sourceReadback": {"verified": True},
            "nativeCoverage": {"status": "not_assessed"},
        }
    )
    assert evaluation.passed is True
    assert evaluation.visual_review_status == "not_assessed"
    assert evaluation.performance_status == "not_assessed"
    assert evaluation.native_coverage_status == "not_assessed"
    assert not any(item.startswith("nativeCoverage.") for item in evaluation.blockers)
    strict = evaluate_unified_gate(
        {
            "safety": {"status": "passed"},
            "capacity": {"browserObserved": True, "checks": {name: True for name in ("text", "title", "body", "font", "qid", "image")}},
            "sourceReadback": {"verified": True},
            "nativeCoverage": {"status": "not_assessed"},
        },
        release_profile="strict",
    )
    assert strict.passed is False
    assert "visualReview.unverified" in strict.blockers
    assert "performance.unverified" in strict.blockers
    assert "nativeCoverage.denominator_invalid" in strict.blockers
    assert "nativeCoverage.fallback_audit_missing" in strict.blockers
    assert "nativeCoverage.unverified" in strict.blockers


def _release_evidence(*, native_count: int = 7) -> dict[str, object]:
    fallback_count = 10 - native_count
    return {
        "safety": {"status": "passed"},
        "capacity": {
            "browserObserved": True,
            "checks": {
                name: True
                for name in ("text", "title", "body", "font", "qid", "image")
            },
        },
        "sourceReadback": {"status": "passed", "verified": True},
        "visualReview": {"status": "passed"},
        "performance": {
            "status": "passed",
            "scope": "pre_publication",
            "stagesMs": {"outline": 10, "author": 20, "checkpoint": 5},
            "totalMs": 35,
        },
        "nativeCoverage": {
            "status": "passed",
            "eligibleElementCount": 10,
            "nativeElementCount": native_count,
            "ratio": native_count / 10,
            "rasterFallbacks": [
                {
                    "elementId": f"eligible-fallback-{index}",
                    "reason": "complex-css",
                    "eligible": True,
                    "explicit": True,
                }
                for index in range(fallback_count)
            ],
        },
    }


def test_release_profile_requires_source_visual_performance_and_native_evidence() -> None:
    evidence = _release_evidence()
    evidence["sourceReadback"] = {"status": "not_applicable", "required": False}
    evidence["visualReview"] = {"status": "not_applicable"}
    evidence["performance"] = {"status": "not_assessed"}

    evaluation = evaluate_unified_gate(evidence, release_profile="release")

    assert evaluation.passed is False
    assert evaluation.quality_status == "blocked"
    assert "sourceReadback.failed" in evaluation.blockers
    assert "visualReview.unverified" in evaluation.blockers
    assert "performance.unverified" in evaluation.blockers


def test_release_native_coverage_uses_every_audited_declared_owner() -> None:
    passing = evaluate_unified_gate(_release_evidence(), release_profile="release")

    assert passing.passed is True
    assert passing.quality_status == "passed"
    assert passing.native_coverage_status == "passed"
    assert passing.native_coverage_ratio == pytest.approx(0.70)
    assert passing.native_coverage_denominator == 10
    assert passing.native_element_count == 7
    assert len(passing.raster_fallbacks) == 3

    below_minimum = evaluate_unified_gate(
        _release_evidence(native_count=6),
        release_profile="release",
    )
    assert below_minimum.passed is False
    assert "nativeCoverage.below_minimum" in below_minimum.blockers

    unaudited = _release_evidence()
    unaudited["nativeCoverage"]["rasterFallbacks"][0].pop("reason")
    failed_audit = evaluate_unified_gate(unaudited, release_profile="release")
    assert "nativeCoverage.fallback_invalid" in failed_audit.blockers

    shrunk = _release_evidence()
    shrunk["nativeCoverage"]["rasterFallbacks"].append({
        "elementId": "unsupported-effect",
        "reason": "unsupported-filter",
        "eligible": False,
        "explicit": True,
    })
    failed_denominator = evaluate_unified_gate(shrunk, release_profile="release")
    assert "nativeCoverage.denominator_shrunk" in failed_denominator.blockers


def test_publication_completion_is_independent_from_quality_status() -> None:
    passed = evaluate_unified_gate(
        {**_release_evidence(), "publication": {"status": "completed"}},
        release_profile="release",
    )
    blocked_evidence = _release_evidence()
    blocked_evidence["visualReview"] = {"status": "failed"}
    blocked_evidence["publication"] = {"status": "completed"}
    blocked = evaluate_unified_gate(blocked_evidence, release_profile="release")

    assert passed.quality_status == "passed"
    assert passed.publication_status == "completed"
    assert blocked.quality_status == "blocked"
    assert blocked.publication_status == "completed"


def test_named_release_policy_cannot_be_weakened_by_mapping_flags() -> None:
    policy = release_quality_policy(
        {
            "id": "release",
            "requireSourceReadback": False,
            "requireVisualReview": False,
            "requirePerformance": False,
            "requireNativeCoverage": False,
            "minimumNativeCoverageRatio": 0.2,
        }
    )

    assert policy.require_source_readback is True
    assert policy.require_visual_review is True
    assert policy.require_performance is True
    assert policy.require_native_coverage is True
    assert policy.minimum_native_coverage_ratio == pytest.approx(0.70)


def test_durable_writers_refresh_under_lock_instead_of_losing_records(tmp_path) -> None:
    path = tmp_path / "quality-receipts.json"
    first = QualityReceiptLedger(storage_path=path)
    second = QualityReceiptLedger(storage_path=path)
    for ledger, attempt in ((first, "attempt-a"), (second, "attempt-b")):
        create_quality_receipt_v2(
            ledger,
            attempt_id=attempt,
            revision=1,
            ledger_digest="sha256:" + attempt[-1] * 64,
            deck_html_digest="sha256:" + "d" * 64,
            evidence={"safety": {"status": "passed"}, "capacity": {"browserObserved": True, "checks": {name: True for name in ("text", "title", "body", "font", "qid", "image")}}, "sourceReadback": {"verified": True}},
        )
    reloaded = QualityReceiptLedger(storage_path=path)
    assert len(reloaded._receipts) == 2


def test_policy_flags_are_immutable_across_idempotent_retries() -> None:
    ledger = QualityReceiptLedger()
    kwargs = {
        "attempt_id": "policy-attempt",
        "revision": 1,
        "ledger_digest": "sha256:" + "a" * 64,
        "deck_html_digest": "sha256:" + "b" * 64,
        "evidence": {"safety": {"status": "passed"}, "capacity": {"browserObserved": True, "checks": {name: True for name in ("text", "title", "body", "font", "qid", "image")}}, "sourceReadback": {"verified": True}, "visualReview": {"status": "passed"}},
    }
    create_quality_receipt_v2(ledger, require_visual_review=True, **kwargs)
    with pytest.raises(ValueError, match="policy_changed"):
        create_quality_receipt_v2(ledger, **kwargs)


def test_release_profile_rejects_truthy_false_strings_and_unknown_names() -> None:
    assert release_profile_requirements({"requireVisualReview": "false"})[:2] == (False, False)
    with pytest.raises(ValueError, match="boolean_invalid"):
        release_profile_requirements({"requireVisualReview": "maybe"})
    with pytest.raises(ValueError, match="profile_unknown"):
        release_profile_requirements("typo-production")


def test_manifest_digest_rejects_record_deletion_after_restart(tmp_path) -> None:
    path = tmp_path / "quality-receipts.json"
    create_quality_receipt_v2(
        QualityReceiptLedger(storage_path=path),
        attempt_id="manifest-attempt",
        revision=1,
        ledger_digest="sha256:" + "a" * 64,
        deck_html_digest="sha256:" + "b" * 64,
        evidence={"safety": {"status": "passed"}, "capacity": {"browserObserved": True, "checks": {name: True for name in ("text", "title", "body", "font", "qid", "image")}}, "sourceReadback": {"verified": True}},
    )
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["receipts"].clear()
    path.write_text(json.dumps(payload), encoding="utf-8")
    restarted = QualityReceiptLedger(storage_path=path)
    with pytest.raises(ValueError, match="manifest_corrupt"):
        create_quality_receipt_v2(
            restarted,
            attempt_id="manifest-attempt",
            revision=1,
            ledger_digest="sha256:" + "a" * 64,
            deck_html_digest="sha256:" + "b" * 64,
            evidence={},
        )


def test_v3_requires_shared_capability_digest_deterministic_evidence_and_t1() -> None:
    evidence = _v3_evidence()
    receipt = create_quality_receipt_v3(
        QualityReceiptV3Ledger(),
        attempt_id="private-v3-attempt",
        revision=4,
        evidence=evidence,
    )

    assert receipt.gate_passed is True
    assert receipt.quality_tier == "T1"
    assert receipt.t1_triggers == quality_receipt_t1_triggers(evidence)
    assert "provider:slide:1:recovery" in receipt.t1_triggers
    assert receipt.attempt_id == public_attempt_id("private-v3-attempt")


def test_v3_rejects_silent_t1_downgrade_unbounded_provider_and_source_complete_claim() -> None:
    evidence = _v3_evidence(tier="T0", source_complete_claim=True)
    evidence["providerCalls"] = {
        "slides": [
            {"slideIndex": 1, "initial": 1, "recovery": 2, "total": 3},
            {"slideIndex": 1, "initial": 1, "recovery": 0, "total": 1},
            {"slideIndex": 4, "initial": 1, "recovery": 0, "total": 1},
        ]
    }
    evidence["capabilityRegistry"]["consumers"]["chromium"] = _v3_digest("e")
    receipt = create_quality_receipt_v3(
        QualityReceiptV3Ledger(),
        attempt_id="v3-blocked",
        revision=1,
        evidence=evidence,
    )

    assert receipt.gate_passed is False
    assert "tier.t1_required" in receipt.blockers
    assert "providerCalls.slide_budget_invalid:1" in receipt.blockers
    assert "providerCalls.slide_duplicate:1" in receipt.blockers
    assert "providerCalls.slide_index_out_of_range:2" in receipt.blockers
    assert "providerCalls.slide_missing:0" in receipt.blockers
    assert "sourceContent.complete_claim_forbidden" in receipt.blockers
    assert "capabilityRegistry.consumer_mismatch:chromium" in receipt.blockers


def test_v3_distinguishes_unavailable_from_not_required_consumers() -> None:
    evidence = _v3_evidence()
    evidence["capabilityRegistry"]["consumers"]["ooxml"] = {
        "status": "not_required",
        "required": False,
    }
    evidence["capabilityRegistry"]["consumers"]["readback"] = {
        "status": "unavailable",
        "required": False,
        "reasonCode": "fidelity_path_has_no_ooxml_readback",
    }
    optional = create_quality_receipt_v3(
        QualityReceiptV3Ledger(),
        attempt_id="v3-optional-consumers",
        revision=1,
        evidence=evidence,
    )

    assert optional.gate_passed is True
    assert optional.to_dict()["capabilityConsumerStatuses"]["ooxml"] == "not_required"
    assert optional.to_dict()["capabilityConsumerStatuses"]["readback"] == "unavailable"

    evidence["capabilityRegistry"]["consumers"]["chromium"] = {
        "status": "unavailable",
        "required": True,
        "reasonCode": "chromium_worker_not_started",
    }
    evidence["capabilityRegistry"]["consumers"]["ooxml"]["required"] = True
    required = create_quality_receipt_v3(
        QualityReceiptV3Ledger(),
        attempt_id="v3-required-consumers",
        revision=1,
        evidence=evidence,
    )

    assert "capabilityRegistry.consumer_unavailable:chromium" in required.blockers
    assert "capabilityRegistry.consumer_not_required_invalid:ooxml" in required.blockers


def test_v3_source_inventory_fails_closed_on_list_count_and_digest_drift() -> None:
    cases = (
        ("textOccurrences", "not-a-list", "sourceContent.text_occurrences_invalid"),
        ("imageOccurrenceCount", 95, "sourceContent.image_occurrence_count_mismatch"),
        ("occurrenceInventoryDigest", _v3_digest("f"), "sourceContent.occurrence_inventory_digest_mismatch"),
    )
    for index, (field, value, expected_blocker) in enumerate(cases):
        evidence = json.loads(json.dumps(_v3_evidence()))
        evidence["sourceContent"][field] = value
        receipt = create_quality_receipt_v3(
            QualityReceiptV3Ledger(),
            attempt_id=f"v3-source-drift-{index}",
            revision=1,
            evidence=evidence,
        )
        assert expected_blocker in receipt.blockers


def test_v3_adapter_binds_the_immutable_v2_receipt_digest() -> None:
    capacity = evaluate_capacity_browser_metrics(_metrics(), _capacity())
    receipt_v2 = create_quality_receipt_v2(
        QualityReceiptLedger(),
        attempt_id="v2-transition-attempt",
        revision=3,
        ledger_digest=_v3_digest("8"),
        deck_html_digest=_v3_digest("9"),
        evidence={
            "safety": {"status": "passed", "residuals": []},
            "capacity": capacity,
            "sourceReadback": {"status": "passed", "verified": True},
        },
    )
    evidence = _v3_evidence()
    evidence["providerCalls"] = {"initial": 1, "recovery": 1, "total": 2}
    receipt_v3 = create_quality_receipt_v3_from_v2(
        QualityReceiptV3Ledger(),
        receipt_v2=receipt_v2,
        evidence=evidence,
    )

    assert receipt_v3.previous_schema_version == "quality-receipt/v2"
    assert receipt_v3.previous_version_receipt_digest == receipt_v2.receipt_digest
    assert receipt_v3.attempt_id == receipt_v2.attempt_id
    assert receipt_v3.revision == receipt_v2.revision


def test_v3_adapter_cannot_upgrade_a_failed_v2_gate() -> None:
    receipt_v2 = create_quality_receipt_v2(
        QualityReceiptLedger(),
        attempt_id="v2-failed-transition",
        revision=1,
        ledger_digest=_v3_digest("6"),
        deck_html_digest=_v3_digest("7"),
        evidence={
            "safety": {"status": "failed", "residuals": ["overlap:q1"]},
            "capacity": {
                "browserObserved": True,
                "checks": {
                    name: True
                    for name in ("text", "title", "body", "font", "qid", "image")
                },
            },
            "sourceReadback": {"status": "passed", "verified": True},
        },
    )

    receipt_v3 = create_quality_receipt_v3_from_v2(
        QualityReceiptV3Ledger(),
        receipt_v2=receipt_v2,
        evidence=_v3_evidence(),
    )

    assert receipt_v2.gate_passed is False
    assert receipt_v3.gate_passed is False
    assert "versionTransition.source_gate_failed" in receipt_v3.blockers


def test_v3_durable_evidence_events_are_append_only_and_chain_bound(tmp_path) -> None:
    path = tmp_path / "quality-receipt-v3.json"
    first = create_quality_receipt_v3(
        QualityReceiptV3Ledger(storage_path=path),
        attempt_id="v3-chain",
        revision=2,
        evidence=_v3_evidence(),
    )
    second_evidence = _v3_evidence()
    second_evidence["qualityTier"] = "T2"
    second_evidence["visualReview"] = {"status": "passed", "fullDeck": True}
    second = append_quality_receipt_v3_evidence(
        QualityReceiptV3Ledger(storage_path=path),
        attempt_id="v3-chain",
        revision=2,
        evidence=second_evidence,
        event_type="full-deck-review",
    )
    reloaded = QualityReceiptV3Ledger(storage_path=path)
    retry = append_quality_receipt_v3_evidence(
        reloaded,
        attempt_id="v3-chain",
        revision=2,
        evidence=second_evidence,
        event_type="full-deck-review",
    )

    assert second.sequence == 1
    assert second.previous_receipt_digest == first.receipt_digest
    assert retry.to_dict() == second.to_dict()
    assert len(reloaded._events) == 2
