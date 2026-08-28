from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest

from services.authored_quality_receipt_v2 import (
    QualityReceiptLedger,
    create_quality_receipt_v2,
    evaluate_capacity_browser_metrics,
    evaluate_unified_gate,
    public_attempt_id,
    quality_receipt_manifest_path,
    release_profile_requirements,
)


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
    assert evaluation.publication_status == "blocked"
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
        }
    )
    assert evaluation.visual_review_status == "not_assessed"
    assert evaluation.performance_status == "not_assessed"
    strict = evaluate_unified_gate(
        {
            "safety": {"status": "passed"},
            "capacity": {"browserObserved": True, "checks": {name: True for name in ("text", "title", "body", "font", "qid", "image")}},
            "sourceReadback": {"verified": True},
        },
        release_profile="strict",
    )
    assert strict.passed is False
    assert "visualReview.unverified" in strict.blockers
    assert "performance.unverified" in strict.blockers


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
