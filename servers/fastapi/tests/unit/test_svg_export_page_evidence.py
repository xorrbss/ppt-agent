"""The export receipt's authoring / geometry_font / visual_review for a generated SVG deck.

Pages are built the way generation persists them: a renderer-shaped quality
dict (``svg_renderer.render_svg_with_metrics``), classified by the driver's
rule (``drivers/svg/quality.page_outcome`` + ``classify_page_outcomes``), then
stored through ``drivers/svg/publish.svg_slide_content``.
"""
from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
import pytest

from models.public_v2_contracts import QualityReceiptV3
from services.generation_core.drivers.svg.publish import svg_slide_content
from services.generation_core.drivers.svg.quality import page_outcome
from services.generation_core.drivers.svg.terminal import FALLBACK_OUTCOME, FALLBACK_REASON
from services.generation_core.quality.advisory_record import advisory_record
from services.generation_core.quality.page_outcome import classify_page_outcomes, review_page_indices
from services.generation_core.quality.terminal_slide_flag_only import flagged_entry
from services.svg_contract import SVG_CONTRACT_VERSION, source_digest
from services.svg_export_page_evidence import svg_authoring, svg_geometry_font
from services.svg_export_quality import svg_public_quality_receipt
from services.svg_export_release_gate import require_release_v1_receipt
from services.svg_export_visual_review import slide_page_digests, split_page_advisories
from services.svg_renderer import SVG_RENDERER_VERSION


def _measured_quality(svg: str, *, failures=(), font_loaded=True) -> dict:
    failures = [dict(item) for item in failures]
    return {"contract_version": SVG_CONTRACT_VERSION, "renderer_version": SVG_RENDERER_VERSION,
            "source_digest": source_digest(svg), "png_digest": "sha256:" + "a" * 64, "wrapper_digest": "w",
            "metrics_digest": "m", "measurement_complete": True,
            "geometry_passed": not failures, "passed": not failures, "verified": False,
            "semantic_review": "required", "failures": failures, "body_font_floor_px": 12,
            "browser_metrics": {}, "trusted_font_materialization": {
                "profile": "svg-default-fonts/v1", "worker_materialized": font_loaded}}


def _publish(pages: list[dict]) -> list[SimpleNamespace]:
    """``pages``: payloads (svg_content, quality, optional terminal) in deck order."""

    outcomes = classify_page_outcomes([page_outcome(i, payload) for i, payload in enumerate(pages)])
    slides = []
    for outcome, payload in zip(outcomes, pages, strict=True):
        payload["quality"] = {**payload["quality"], "classification": outcome.classification,
                              "verified": outcome.verified}
        content = svg_slide_content(payload, role="content", source="text", pt_per_px=0.75)
        slides.append(SimpleNamespace(svg_content=payload["svg_content"], content=content))
    return slides


def _page(svg: str, **quality_args) -> dict:
    return {"svg_content": svg, "quality": _measured_quality(svg, **quality_args), "png_sha256": "a" * 64}


def _advisory(slides, issues_on=()):
    vision = {"slides": [{"index": i, "issues": ["dead_band"] if i in issues_on else []}
                         for i in range(len(slides))], "promptVersion": "v1", "model": "vision-test"}
    record = advisory_record(vision, slide_page_digests(slides))
    record["pageAdvisories"] = split_page_advisories(vision, slides, range(len(slides)))
    return record


def _receipt(slides, advisory):
    return svg_public_quality_receipt(
        slides=slides, advisory=advisory, source_preservation=None, worker_metrics={},
        exporter_version="converter/v1", quality_receipt_id="quality-t", operation_id="operation-t",
        presentation_id="presentation-t", artifact_digest="sha256:" + "a" * 64, source_revision=1,
        scene_digest="sha256:" + "b" * 64, evaluated_at=datetime.now(timezone.utc))


def test_clean_generated_deck_passes_every_required_domain_and_is_release_ready():
    slides = _publish([_page("<svg>1</svg>"), _page("<svg>2</svg>")])

    receipt = _receipt(slides, _advisory(slides))

    QualityReceiptV3.model_validate(receipt)
    with pytest.raises(ValueError):
        QualityReceiptV3.model_validate({**receipt, "evidenceDigests": {}})
    assert receipt["authoring"]["status"] == "passed"
    assert receipt["geometryFont"]["status"] == "passed"
    assert receipt["geometryFont"]["evidence"]["checkerVersion"] == SVG_RENDERER_VERSION
    assert receipt["authoring"]["evidence"]["measurements"]["verifiedPages"] == 2
    assert receipt["visualReview"]["status"] == "passed"
    assert receipt["releaseReadiness"] == "ready"
    require_release_v1_receipt(receipt)  # must not raise
    legacy = _advisory(slides)
    legacy.pop("pageAdvisories")
    legacy_receipt = _receipt(slides, legacy)
    assert legacy_receipt["visualReview"]["reasonCode"] == "svg_visual_advisory_png_evidence_missing"
    assert legacy_receipt["releaseReadiness"] == "blocked"


def test_deck_with_zero_flagged_pages_is_not_visual_review_flagged_despite_advisory_issues():
    # The live A4 pilot deck: generation reported 0 flagged pages, but the
    # advisory named an issue; export review must agree with generation.
    slides = _publish([_page("<svg>1</svg>"), _page("<svg>2</svg>")])
    assert review_page_indices([s.content["svgReceipt"]["quality"] for s in slides]) == []

    receipt = _receipt(slides, _advisory(slides, issues_on={1}))

    assert receipt["visualReview"]["status"] == "passed"
    assert receipt["visualReview"]["evidence"]["measurements"]["advisoryFlaggedPages"] == 1
    assert receipt["releaseReadiness"] == "ready"


def test_deck_with_a_flagged_page_is_still_visual_review_flagged():
    failure = {"kind": "text_overlap", "pairs": [["a", "b"]]}
    slides = _publish([_page("<svg>1</svg>"), _page("<svg>2</svg>", failures=[failure])])

    receipt = _receipt(slides, _advisory(slides))

    assert receipt["visualReview"]["reasonCode"] == "svg_visual_review_flagged"
    assert receipt["visualReview"]["evidence"]["measurements"]["reviewPages"] == 1
    assert receipt["geometryFont"]["reasonCode"] == "svg_geometry_font_failed"
    assert receipt["geometryFont"]["evidence"]["measurements"]["failedPages"] == "1"
    assert receipt["authoring"]["reasonCode"] == "svg_authoring_failed"
    assert receipt["releaseReadiness"] == "blocked"


def test_font_not_materialized_fails_geometry_font_only():
    slides = _publish([_page("<svg>1</svg>", font_loaded=False)])

    assert svg_geometry_font(slides)["reasonCode"] == "svg_geometry_font_failed"
    assert svg_authoring(slides)["status"] == "passed"


def test_fallback_substitute_page_fails_authoring_even_when_measured_clean():
    page = _page("<svg>fallback</svg>")
    page["terminal"] = flagged_entry(index=0, reason=FALLBACK_REASON, attempted=False,
                                     outcome=FALLBACK_OUTCOME)
    slides = _publish([page])

    authoring = svg_authoring(slides)

    assert authoring["status"] == "failed"
    assert authoring["reasonCode"] == "svg_authoring_failed"
    assert authoring["evidence"]["measurements"]["failedPages"] == "0"
    assert svg_geometry_font(slides)["status"] == "passed"


def test_missing_page_evidence_stays_fail_closed():
    bare = SimpleNamespace(svg_content="<svg>1</svg>",
                           content={"svgReceipt": {"quality": {"passed": True, "verified": True}}})
    no_receipt = SimpleNamespace(svg_content="<svg>2</svg>", content=None)

    for slides in ([bare], [no_receipt], []):
        assert svg_authoring(slides) == {"required": True, "status": "failed",
                                         "reasonCode": "svg_authoring_evidence_missing"}
        assert svg_geometry_font(slides) == {"required": True, "status": "failed",
                                             "reasonCode": "svg_geometry_font_evidence_missing"}


def test_evidence_from_an_unknown_checker_version_counts_as_missing():
    slides = _publish([_page("<svg>1</svg>")])
    slides[0].content["svgReceipt"]["quality"]["renderer_version"] = "svg-chromium/v0"

    assert svg_geometry_font(slides)["reasonCode"] == "svg_geometry_font_evidence_missing"


def test_page_changed_after_measurement_is_stale():
    slides = _publish([_page("<svg>1</svg>")])
    slides[0].svg_content = "<svg>edited</svg>"

    assert svg_authoring(slides)["reasonCode"] == "svg_authoring_evidence_stale"
    assert svg_geometry_font(slides)["reasonCode"] == "svg_geometry_font_evidence_stale"


def test_saved_common_header_candidates_use_allocated_fields_and_measured_collisions():
    """Offline replay of captured browser evidence, without rendering or providers."""
    from copy import deepcopy
    import json
    import os
    from pathlib import Path
    from xml.etree import ElementTree as ET

    from services.svg_common_header_export_evidence import apply_common_header_v2_quality

    directory = os.environ.get("SVG_HEADER_OVERFLOW_REPLAY_DIRECTORY")
    if not directory:
        pytest.skip("Set SVG_HEADER_OVERFLOW_REPLAY_DIRECTORY to the saved candidate evidence directory")

    def _case_number(number):
        page = json.loads((Path(directory) / f"candidate-page-{number}.json").read_text(encoding="utf-8-sig"))
        furniture = page["system_furniture"]
        header = furniture["generated_common_header"]

        def measure(mutate=None, svg=None):
            # Re-evaluate this helper alone; captured page/global failures stay
            # untouched in the source artifact and are not release evidence.
            quality = deepcopy(page["quality"])
            quality["failures"] = []
            quality["passed"] = quality["geometry_passed"] = True
            if mutate:
                mutate(quality)
            apply_common_header_v2_quality(quality, svg or page["svg_content"],
                header["contract"], header["page"], furniture)
            return quality

        measured = measure()
        assert measured["common_header"]["passed"] is True
        assert measured["failures"] == []
        elements = measured["browser_metrics"]["elements"]
        header_text = [item for item in elements if item.get("text_ink_fragments")
                       and item["qid"] in {"svg-23", "svg-25", "svg-26"}]
        assert len(header_text) == 2
        qids = [item["qid"] for item in header_text]

        def items(quality):
            return [next(item for item in quality["browser_metrics"]["elements"] if item["qid"] == qid)
                    for qid in qids]

        def conservative_cells(quality):
            for item in items(quality):
                for fragment in item["text_ink_fragments"]:
                    fragment["cellClusters"] = 1

        assert measure(conservative_cells)["common_header"]["passed"] is True

        def outside_field(quality):
            items(quality)[0]["text_ink_fragments"][0]["x"] = -10

        assert any(item["kind"] == "svg_common_header_overflow" for item in measure(outside_field)["failures"])

        def overlapping_text(quality):
            first, second = items(quality)
            second["text_ink_fragments"] = deepcopy(first["text_ink_fragments"])

        assert any(item["kind"] == "svg_common_header_text_overlap"
                   for item in measure(overlapping_text)["failures"])

        def missing_ink(quality):
            items(quality)[0]["text_ink_fragments"] = []

        assert any(item["kind"] == "svg_common_header_overflow" for item in measure(missing_ink)["failures"])

        for property_name, value in (("glyph_clipped", True), ("font_loaded", False),
                                     ("resolved_font_family", "untrusted-font"), ("effective_opacity", .5)):
            def mismatch(quality, property_name=property_name, value=value):
                items(quality)[0][property_name] = value

            assert any(item["kind"] == "svg_common_header_font_mismatch"
                       for item in measure(mismatch)["failures"])

        changed = ET.fromstring(page["svg_content"])
        next(node for node in changed.iter() if node.get("id", "").endswith("-document-line-1")).text += "changed"
        assert any(item["kind"] == "svg_common_header_digest_mismatch"
                   for item in measure(svg=ET.tostring(changed, encoding="unicode"))["failures"])

    for number in (1, 2):
        _case_number(number)
