"""Focused replay checks for coupled maximum-quality candidate preflight."""

from __future__ import annotations

import asyncio
import json
from copy import deepcopy
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image

from services import slidespec_authored_executor as executor
from services.slidespec_coupled_repair import build_coupled_repair_candidates
from services.slidespec_repair import build_deterministic_cascade_report
from utils.authored_ocr_quality import OcrComparison, OcrQualityResult


_FIXTURE = (
    Path(__file__).parents[1]
    / "fixtures"
    / "maximum_quality_coupled_repair_cases.json"
)
_HASH = "sha256:" + "a" * 64
_CONTENT_HASH = "sha256:" + "c" * 64


def _cases() -> list[dict]:
    return json.loads(_FIXTURE.read_text(encoding="utf-8"))["cases"]


def _context(candidate: dict) -> dict:
    return {
        "slideId": f"slide-{candidate['qid'].split('-')[0]}",
        "baseSpecRevision": 2,
        "baseSpecHash": _HASH,
        "expectedContentDigest": _CONTENT_HASH,
        "coupledRepairs": [candidate],
        "limits": {"maxOperations": 16, "maxAffectedBlocks": 8},
    }


@pytest.mark.parametrize("slide_index", [11, 73, 77, 14, 24, 45])
def test_reported_coupled_and_ocr_replays_are_deterministic_and_preflight_clean(
    slide_index: int,
) -> None:
    candidate = next(item["candidate"] for item in _cases() if item["slide_index"] == slide_index)

    first = build_coupled_repair_candidates(_context(candidate))
    second = build_coupled_repair_candidates(_context(candidate))

    assert first == second
    assert len(first.candidates) == 1
    assert first.rejections == ()
    plan = build_deterministic_cascade_report(_context(candidate)).plans[0]
    assert len(plan.scope) >= 2
    assert "coupled-proof:chrome" in plan.diagnostic_refs
    assert "hard-gates:preserved" in plan.diagnostic_refs


def test_predicted_clearance_failure_is_removed_before_lazy_validation() -> None:
    candidate = deepcopy(_cases()[0]["candidate"])
    candidate["proof"]["predictedClearancePx"] = 4

    report = build_deterministic_cascade_report(_context(candidate))

    assert report.plans == ()
    assert [item.reason_code for item in report.coupled_rejections] == [
        "repair.coupled.predicted_clearance_insufficient"
    ]


def test_predicted_text_fit_failure_is_removed_before_lazy_validation() -> None:
    candidate = deepcopy(next(item["candidate"] for item in _cases() if item["slide_index"] == 77))
    candidate["proof"]["predictedTextFitPx"] = 220

    result = build_coupled_repair_candidates(_context(candidate))

    assert result.candidates == ()
    assert result.rejections[0].reason_code == (
        "repair.coupled.predicted_text_fit_insufficient"
    )


def test_target_only_candidate_cannot_claim_a_coupled_repair() -> None:
    candidate = deepcopy(_cases()[0]["candidate"])
    candidate["operations"] = [
        {
            "op": "translateWithinParent",
            "target": candidate["target"],
            "value": {"x": 0, "y": -0.02},
            "precondition": {
                "expectedBounds": {"x": 0.1, "y": 0.2, "width": 0.3, "height": 0.2}
            },
        }
    ]
    candidate["operationProofs"] = [
        {
            "target": candidate["target"],
            "op": "translateWithinParent",
            "mutationAllowed": True,
            "roundTripSupported": True,
            "baselineMatched": True,
        }
    ]

    result = build_coupled_repair_candidates(_context(candidate))

    assert result.candidates == ()
    assert result.rejections[0].reason_code == "repair.coupled.target_only_disallowed"


@pytest.mark.parametrize(
    ("mutation", "reason"),
    [
        (
            lambda value: value["operationProofs"][0].update(
                roundTripSupported=False
            ),
            "repair.coupled.operation_projection_unproven",
        ),
        (
            lambda value: value["proof"].update(hardGatePolicy="relaxed"),
            "repair.coupled.hard_gate_proof_missing",
        ),
        (
            lambda value: value["constraintFootprint"].update(blockingCore=[]),
            "repair.coupled.constraint_footprint_incomplete",
        ),
    ],
)
def test_projection_hard_gate_and_unsat_footprint_proofs_fail_closed(
    mutation, reason: str
) -> None:
    candidate = deepcopy(_cases()[0]["candidate"])
    mutation(candidate)

    result = build_coupled_repair_candidates(_context(candidate))

    assert result.candidates == ()
    assert result.rejections[0].reason_code == reason


def test_font_shrink_requires_parent_reclaim_text_token_mapping_and_bounded_retry() -> None:
    candidate = deepcopy(next(item["candidate"] for item in _cases() if item["slide_index"] == 45))
    candidate["operations"] = [candidate["operations"][1]]
    candidate["operationProofs"] = [candidate["operationProofs"][1]]

    target_only = build_coupled_repair_candidates(_context(candidate))
    assert target_only.rejections[0].reason_code == "repair.coupled.target_only_disallowed"

    candidate = deepcopy(next(item["candidate"] for item in _cases() if item["slide_index"] == 45))
    candidate["ocrSafety"]["tokenMappingComplete"] = False
    missing_mapping = build_coupled_repair_candidates(_context(candidate))
    assert missing_mapping.rejections[0].reason_code == "repair.coupled.ocr_mapping_incomplete"

    candidate = deepcopy(next(item["candidate"] for item in _cases() if item["slide_index"] == 45))
    candidate["ocrSafety"]["retryScales"] = [1, 1.25, 1.5, 2]
    unbounded_retry = build_coupled_repair_candidates(_context(candidate))
    assert unbounded_retry.rejections[0].reason_code == "repair.coupled.ocr_retry_budget_invalid"

    candidate = deepcopy(next(item["candidate"] for item in _cases() if item["slide_index"] == 45))
    candidate["operations"][1]["scale"] = 0.9
    unsafe_shrink = build_coupled_repair_candidates(_context(candidate))
    assert unsafe_shrink.rejections[0].reason_code == "repair.coupled.font_shrink_out_of_bounds"


def test_fixture_is_read_only_sanitized_replay_not_a_publication_recipe() -> None:
    payload = json.loads(_FIXTURE.read_text(encoding="utf-8"))

    assert payload["revision"] == 2
    assert {item["slide_index"] for item in payload["cases"]} == {11, 14, 24, 45, 73, 77}
    assert {
        (item["slide_index"], item["page_number"])
        for item in payload["cases"]
        if "page_number" in item
    } == {(14, 15), (24, 25), (45, 46)}
    encoded = json.dumps(payload, ensure_ascii=False).lower()
    assert "html" not in encoded
    assert "ocr_text" not in encoded
    assert "publish" not in encoded


def test_borderline_ocr_uses_at_most_two_multiscale_retries_and_selects_best(
    monkeypatch,
) -> None:
    image = Image.new("RGB", (100, 50), "white")
    payload = BytesIO()
    image.save(payload, format="PNG")
    widths: list[int] = []

    async def fake_inspect(raw: bytes, *, expected_text: str, **_kwargs):
        assert expected_text == "important 42%"
        width = Image.open(BytesIO(raw)).width
        widths.append(width)
        cer = {100: 0.08, 150: 0.02}.get(width, 0.04)
        return OcrQualityResult(
            available=True,
            status="success",
            failure_reason=None,
            text="important 42%",
            text_digest="sha256:" + "1" * 64,
            metrics={"cer": cer, "missing_rate": cer},
            calls=len(widths),
            cache_hits=0,
            elapsed_ms=1.0,
            returncode=0,
        )

    monkeypatch.setattr(executor, "inspect_authored_ocr", fake_inspect)

    selected = asyncio.run(
        executor._inspect_ocr_with_bounded_multiscale(
            payload.getvalue(), expected_text="important 42%"
        )
    )

    assert widths == [100, 150]
    assert selected.metrics["cer"] == 0.02


def test_ocr_rejection_diagnostics_expose_mapping_without_authored_text() -> None:
    spec = SimpleNamespace(
        tokens=(SimpleNamespace(name="critical", value="Revenue 42%", important=True),),
        content=(
            SimpleNamespace(
                semanticId="sem:metric",
                importantTokens=("critical",),
            ),
        ),
    )
    request = SimpleNamespace(
        source=SimpleNamespace(
            bindings=(SimpleNamespace(semanticId="sem:metric", qid="q-metric"),)
        )
    )
    baseline = OcrQualityResult(
        True, "success", None, "Revenue 42%", "sha256:" + "1" * 64, {}, 1, 0, 1.0
    )
    candidate = OcrQualityResult(
        True, "success", None, "Revenue", "sha256:" + "2" * 64, {}, 1, 0, 1.0
    )
    comparison = OcrComparison(
        False, "text_regression", False, False, {}, {}, {"text": 1.0}, {}
    )

    diagnostics = executor._ocr_gate_diagnostics(
        spec=spec,
        request=request,
        baseline=baseline,
        candidate=candidate,
        comparison=comparison,
    )

    token = diagnostics["important_tokens"][0]
    assert diagnostics["important_token_count"] == 1
    assert diagnostics["token_mapping_complete"] is True
    assert diagnostics["token_mapping_status"] == "complete"
    assert diagnostics["crop_evidence_available"] is False
    assert token["semantic_ids"] == ["sem:metric"]
    assert token["qids"] == ["q-metric"]
    assert token["baseline_present"] is True
    assert token["candidate_present"] is False
    assert "Revenue 42%" not in json.dumps(diagnostics)
