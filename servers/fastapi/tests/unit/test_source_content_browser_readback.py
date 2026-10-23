from __future__ import annotations

import hashlib
import asyncio

import pytest

from models.authored_vnext.capability_registry import CAPABILITY_REGISTRY_DIGEST
from utils.authored_dom_quality import ensure_authored_qids
from services.source_content.source_runs import source_run_binding_id
from services.source_content.browser_readback import (
    BrowserSourceReadbackError,
    deterministic_source_qid,
    source_block_data_attribute,
    stamp_source_block_ids,
    stamp_source_image_block_ids,
    inspect_browser_source_readback,
    verify_browser_source_readback,
)


def _manifest() -> dict[str, object]:
    return {
        "units": [{
            "sourceUnitId": "unit-1", "sourceIndex": 0,
            "blocks": [
                {"sourceBlockId": "b1", "kind": "text", "order": 0, "payload": {"text": "원문 제목"}},
                {"sourceBlockId": "b2", "kind": "text", "order": 1, "payload": {"text": "중요한 본문"}},
            ],
        }],
    }


def _outline(*ids: str) -> dict[str, object]:
    return {"slides": [{"sourceUnitId": "unit-1", "sourceIndex": 0, "readingOrder": list(ids)}]}


def _metrics(*, ids=("b1", "b2"), text=("원문 제목", "중요한 본문"), visible=True, clipped=False, occluded=0.0):
    elements = []
    for index, block_id in enumerate(ids):
        elements.append({
            "sourceBlockId": block_id,
            "qid": deterministic_source_qid(block_id),
            "semanticRole": "title" if index == 0 else "body",
            "renderedText": text[index] if index < len(text) else "",
            "bbox": {"x": 20, "y": 20 + index * 40, "width": 200, "height": 24},
            "cssVisible": visible,
            "effectiveOpacity": 1,
            "fontLoaded": True,
            "clipped": clipped,
            "glyphClipped": False,
            "lineBoxClipped": False,
            "occludedFraction": occluded,
            "continuousOccludedFraction": occluded,
            "computedStyle": {"display": "block", "visibility": "visible"},
            "measurement": {"occlusion": True},
            "occlusionMeasurementEvidence": {"status": "measured"},
        })
    return {"width": 1280, "height": 720, "fontsReady": True, "elements": elements}


def test_browser_readback_binds_rendered_geometry_and_visibility() -> None:
    result = verify_browser_source_readback(manifest=_manifest(), outline=_outline("b1", "b2"), browser_metrics=[_metrics()])
    assert result.passed
    assert [item.source_block_id for item in result.evidence] == ["b1", "b2"]
    assert result.evidence[0].bbox["width"] == 200
    receipt = result.as_dict()
    assert receipt["verified"] is True
    assert receipt["capabilityRegistry"] == {
        "registrySchemaVersion": "authored-capability-registry/v1",
        "registryDigest": CAPABILITY_REGISTRY_DIGEST,
        "registryConsumer": "readback",
    }
    assert receipt["evidence"][0] == {
        "sourceBlockId": "b1",
        "qid": deterministic_source_qid("b1"),
        "renderedText": "원문 제목",
        "bbox": {"x": 20.0, "y": 20.0, "width": 200.0, "height": 24.0},
        "visible": True,
        "clipped": False,
        "occludedFraction": 0.0,
        "continuousOccludedFraction": 0.0,
        "kind": "text",
        "naturalWidth": None,
        "naturalHeight": None,
        "semanticRole": "title",
    }


def test_browser_readback_uses_authoritative_source_digest_not_admitted_nonce_bytes() -> None:
    html = '<h1>원문 제목</h1><p>중요한 본문</p>'
    metrics = _metrics()
    metrics.update(
        {
            "source_digest": "sha256:" + hashlib.sha256(html.encode("utf-8")).hexdigest(),
            # CSP/admission bytes legitimately have a different identity from
            # the authored render request and remain sealed as render_digest.
            "render_digest": "sha256:" + "2" * 64,
        }
    )

    result = verify_browser_source_readback(
        manifest=_manifest(),
        outline=_outline("b1", "b2"),
        browser_metrics=[metrics],
        htmls=[html],
    )

    assert result.passed
    metrics["source_digest"] = "sha256:" + "0" * 64
    with pytest.raises(BrowserSourceReadbackError):
        verify_browser_source_readback(
            manifest=_manifest(),
            outline=_outline("b1", "b2"),
            browser_metrics=[metrics],
            htmls=[html],
        )


@pytest.mark.parametrize("kwargs", [
    {"visible": False},
    {"clipped": True},
    {"occluded": 0.25},
    {"ids": ("b1",)},
])
def test_browser_readback_fails_closed_for_visibility_or_missing_binding(kwargs) -> None:
    with pytest.raises(BrowserSourceReadbackError):
        verify_browser_source_readback(manifest=_manifest(), outline=_outline("b1", "b2"), browser_metrics=[_metrics(**kwargs)])


def test_browser_readback_rejects_outline_order_drift() -> None:
    with pytest.raises(BrowserSourceReadbackError) as error:
        verify_browser_source_readback(manifest=_manifest(), outline=_outline("b2", "b1"), browser_metrics=[_metrics()])
    assert error.value.order_violations


def test_source_block_attribute_is_escaped() -> None:
    assert source_block_data_attribute('block"1') == 'data-source-block-id="block&quot;1"'


def test_stamp_source_block_ids_requires_unique_rendered_dom_match() -> None:
    stamped = stamp_source_block_ids(
        '<section><h1>제목</h1><p><span>본문</span></p><script>본문</script></section>',
        [("b1", "제목"), ("b2", "본문")],
    )
    assert 'data-source-block-id="b1"' in stamped
    assert 'data-source-block-id="b2"' in stamped
    assert 'data-semantic-role="title"' in stamped
    assert stamped.count('data-source-block-id="b1"') == 1
    assert stamp_source_block_ids(stamped, [("b1", "제목"), ("b2", "본문")]) == stamped
    # An exact repeat binds the first element in document order; the copy is
    # decorative.  Text already owned by another source ID still conflicts.
    repeated = stamp_source_block_ids('<p>같은 텍스트</p><div>같은 텍스트</div>', [("b1", "같은 텍스트")])
    assert repeated.startswith('<p data-source-block-id="b1"')
    assert repeated.endswith("<div>같은 텍스트</div>")
    with pytest.raises(BrowserSourceReadbackError) as conflict:
        stamp_source_block_ids('<p data-source-block-id="other">같은 텍스트</p>', [("b1", "같은 텍스트")])
    assert "conflicting" in conflict.value.detail
    with pytest.raises(BrowserSourceReadbackError) as absent:
        stamp_source_block_ids('<p>다른 텍스트</p>', [("b1", "같은 텍스트")])
    assert absent.value.block_ids == ("b1",)


def test_stamp_source_block_ids_closes_void_elements_without_corrupting_text_ownership() -> None:
    stamped = stamp_source_block_ids(
        """<!DOCTYPE html><html><head><meta charset="utf-8"><style></style></head>
        <body><h1><span>숏폼대행</span><br>1인사업자 홀로서기</h1>
        <p>후속 문구</p></body></html>""",
        [("title", "숏폼대행\n1인사업자 홀로서기"), ("body", "후속 문구")],
    )

    assert 'data-source-block-id="title"' in stamped
    assert 'data-source-block-id="body"' in stamped
    assert "</meta>" not in stamped
    assert "</br>" not in stamped
    assert stamp_source_block_ids(
        stamped,
        [("title", "숏폼대행\n1인사업자 홀로서기"), ("body", "후속 문구")],
    ) == stamped


def test_source_stamps_replace_one_authored_qid_and_reject_preexisting_duplicates() -> None:
    text = stamp_source_block_ids(
        '<p data-qid="cover.subtitle">원문</p>', [("b1", "원문")]
    )
    text = ensure_authored_qids(text)
    assert text.count("data-qid=") == 1
    assert f'data-qid="{deterministic_source_qid("b1")}"' in text
    assert 'data-source-block-id="b1"' in text

    image = stamp_source_image_block_ids(
        '<img src="asset" data-qid="image.existing">', ["image-1"]
    )
    assert image.count("data-qid=") == 1
    assert f'data-qid="{deterministic_source_qid("image-1")}"' in image
    assert 'data-source-block-id="image-1"' in image
    assert stamp_source_image_block_ids(image, ["image-1"]) == image

    with pytest.raises(BrowserSourceReadbackError):
        stamp_source_image_block_ids(
            '<img src="asset" data-qid="first" data-qid="second">', ["image-1"]
        )


def test_stamp_source_block_ids_repairs_only_duplicated_terminal_punctuation() -> None:
    stamped = stamp_source_block_ids(
        "<p>원문을 검증합니다..</p>",
        [("b1", "원문을 검증합니다.")],
    )
    assert ">원문을 검증합니다.</p>" in stamped
    assert 'data-source-block-id="b1"' in stamped
    assert stamp_source_block_ids(stamped, [("b1", "원문을 검증합니다.")]) == stamped

    with pytest.raises(BrowserSourceReadbackError):
        stamp_source_block_ids(
            "<p>원문을 검증했습니다.</p>",
            [("b1", "원문을 검증합니다.")],
        )


def test_image_occurrence_readback_requires_intrinsic_natural_size() -> None:
    manifest = {
        "units": [{
            "sourceUnitId": "unit-1", "sourceIndex": 0,
            "blocks": [{
                "sourceBlockId": "img-1", "kind": "image", "order": 0,
                "payload": {"assetId": "asset-1"},
            }],
        }]
    }
    outline = {"slides": [{"sourceUnitId": "unit-1", "sourceIndex": 0, "readingOrder": ["img-1"]}]}
    metrics = [{
        "width": 1280, "height": 720, "fontsReady": True,
        "elements": [{
            "sourceBlockId": "img-1", "qid": deterministic_source_qid("img-1"), "tag": "img",
            "semanticRole": "image",
            "renderedText": "", "bbox": {"x": 10, "y": 10, "width": 320, "height": 180},
            "cssVisible": True, "effectiveOpacity": 1, "fontLoaded": True,
            "clipped": False, "glyphClipped": False, "lineBoxClipped": False,
            "occludedFraction": 0, "continuousOccludedFraction": 0,
            "computedStyle": {"display": "block", "visibility": "visible"},
            "measurement": {"occlusion": True},
            "occlusionMeasurementEvidence": {"status": "measured"},
            "image": {"complete": True, "naturalWidth": 640, "naturalHeight": 360, "cssVisible": True},
        }],
    }]
    result = verify_browser_source_readback(manifest=manifest, outline=outline, browser_metrics=metrics)
    assert result.passed
    assert result.evidence[0].kind == "image"
    assert result.evidence[0].natural_width == 640
    assert result.as_dict()["evidence"][0]["naturalWidth"] == 640
    assert result.as_dict()["evidence"][0]["naturalHeight"] == 360
    bad = metrics[0]["elements"][0]["image"]
    bad["naturalWidth"] = 0
    with pytest.raises(BrowserSourceReadbackError):
        verify_browser_source_readback(manifest=manifest, outline=outline, browser_metrics=metrics)


def test_image_occurrence_ids_stamp_dom_order() -> None:
    first = "img-1"
    stamped = stamp_source_image_block_ids('<div><img src="a"><img src="a"></div>', [first, "img-2"])
    assert stamped.count("data-source-block-id=") == 2
    assert stamp_source_image_block_ids(stamped, [first, "img-2"]) == stamped
    assert stamped.count("data-qid=") == 2


def test_source_image_ancestor_clip_cannot_pass_using_clear_glyph_flags() -> None:
    manifest = {"units": [{
        "sourceUnitId": "unit-1", "sourceIndex": 0,
        "blocks": [{"sourceBlockId": "img-1", "kind": "image", "order": 0,
                    "payload": {"assetId": "asset-1"}}],
    }]}
    metrics = _metrics(ids=("img-1",), text=("",))
    image = metrics["elements"][0]
    image.update(tag="img", semanticRole="image", image={
        "complete": True, "naturalWidth": 400, "naturalHeight": 48, "cssVisible": True,
    })
    image["source_clip_bounds"] = {"left": 0, "top": 0, "right": 1280, "bottom": 720}
    assert verify_browser_source_readback(
        manifest=manifest, outline=_outline("img-1"), browser_metrics=[metrics]
    ).passed
    # The image bbox stays inside the slide; its ancestor only exposes 30px
    # of its 200px width. Images never produce glyph-clipping suspects.
    image["source_clip_bounds"]["right"] = 50
    diagnosed = inspect_browser_source_readback(
        manifest=manifest, outline=_outline("img-1"), browser_metrics=[metrics]
    )
    assert diagnosed.visibility_failures == ("img-1",)
    assert diagnosed.evidence[0].clipped is True
    assert not diagnosed.integrity_failures
    with pytest.raises(BrowserSourceReadbackError):
        verify_browser_source_readback(
            manifest=manifest, outline=_outline("img-1"), browser_metrics=[metrics]
        )
    image["source_clip_bounds"] = {"left": 0}
    diagnosed = inspect_browser_source_readback(
        manifest=manifest, outline=_outline("img-1"), browser_metrics=[metrics]
    )
    assert diagnosed.telemetry_failures == ("img-1",)


def test_decorative_images_are_never_consumed_as_source_occurrences() -> None:
    decorative = '<img data-decorative="true" src="template.png">'
    assert stamp_source_image_block_ids(decorative, []) == decorative
    with pytest.raises(BrowserSourceReadbackError):
        stamp_source_image_block_ids(decorative, ["source-1"])


def test_source_text_readback_requires_semantic_role_and_measured_occlusion() -> None:
    metrics = _metrics()
    metrics["elements"][0].pop("semanticRole")
    with pytest.raises(BrowserSourceReadbackError):
        verify_browser_source_readback(manifest=_manifest(), outline=_outline("b1", "b2"), browser_metrics=[metrics])
    metrics = _metrics()
    metrics["elements"][0].pop("occlusionMeasurementEvidence")
    with pytest.raises(BrowserSourceReadbackError):
        verify_browser_source_readback(manifest=_manifest(), outline=_outline("b1", "b2"), browser_metrics=[metrics])


def test_source_occurrence_readback_rejects_noncanonical_qid() -> None:
    metrics = _metrics()
    metrics["elements"][0]["qid"] = "arbitrary-qid"

    with pytest.raises(BrowserSourceReadbackError) as error:
        verify_browser_source_readback(
            manifest=_manifest(),
            outline=_outline("b1", "b2"),
            browser_metrics=[metrics],
        )

    assert error.value.block_ids == ("b1",)


def test_source_occurrence_readback_rejects_qid_owned_by_non_source_dom() -> None:
    metrics = _metrics()
    metrics["elements"].insert(0, {"qid": deterministic_source_qid("b1")})

    with pytest.raises(BrowserSourceReadbackError) as error:
        verify_browser_source_readback(
            manifest=_manifest(),
            outline=_outline("b1", "b2"),
            browser_metrics=[metrics],
        )

    assert error.value.block_ids == ("b1",)


def test_browser_readback_accepts_blocks_the_template_relocated() -> None:
    # DOM order across blocks is the new template's choice; every stronger
    # binding (one visible owner per block, no duplicates, none missing) holds.
    result = verify_browser_source_readback(
        manifest=_manifest(),
        outline=_outline("b1", "b2"),
        browser_metrics=[_metrics(ids=("b2", "b1"), text=("중요한 본문", "원문 제목"))],
    )
    assert result.passed
    assert result.order_violations == 0
    assert sorted(item.source_block_id for item in result.evidence) == ["b1", "b2"]


def _run_manifest(block_text: str) -> dict[str, object]:
    return {
        "units": [{
            "sourceUnitId": "unit-1", "sourceIndex": 0,
            "blocks": [
                {"sourceBlockId": "b1", "kind": "text", "order": 0, "payload": {"text": block_text}},
            ],
        }],
    }


def _run_metrics(*spans: tuple[int, int, str], run_count: int) -> dict[str, object]:
    elements = []
    for index, (start, end, rendered) in enumerate(spans):
        binding_id = source_run_binding_id("b1", start, end, run_count)
        elements.append({
            "sourceBlockId": binding_id,
            "qid": deterministic_source_qid(binding_id),
            "semanticRole": "body",
            "renderedText": rendered,
            "bbox": {"x": 20, "y": 20 + index * 40, "width": 200, "height": 24},
            "cssVisible": True,
            "effectiveOpacity": 1,
            "fontLoaded": True,
            "clipped": False,
            "glyphClipped": False,
            "lineBoxClipped": False,
            "occludedFraction": 0.0,
            "continuousOccludedFraction": 0.0,
            "computedStyle": {"display": "block", "visibility": "visible"},
            "measurement": {"occlusion": True},
            "occlusionMeasurementEvidence": {"status": "measured"},
        })
    return {"width": 1280, "height": 720, "fontsReady": True, "elements": elements}


def test_browser_readback_accepts_a_pipe_block_whose_halves_the_template_swapped() -> None:
    # A pipe joins independent labels and the new template owns which slot each
    # lands in.  Stamping grants that exemption, so the browser gate must too or
    # it fails the very decks stamping just bound.
    result = verify_browser_source_readback(
        manifest=_run_manifest("꿀벌쌤 | 숏폼대행 1인사업자 홀로서기"),
        outline=_outline("b1"),
        browser_metrics=[_run_metrics(
            (1, 1, "숏폼대행 1인사업자 홀로서기"),
            (0, 0, "꿀벌쌤"),
            run_count=2,
        )],
    )
    assert result.passed
    assert result.order_violations == 0
    assert [item.source_block_id for item in result.evidence] == ["b1"]


def test_browser_readback_records_a_block_whose_own_items_were_reordered() -> None:
    """Where a template places one block's items is layout, not lost text.

    Both items are measured and both are on the slide, so the deck passes and
    the receipt carries the deviation instead of rejecting the run.
    """

    result = verify_browser_source_readback(
        manifest=_run_manifest("• 첫째 항목\n• 둘째 항목"),
        outline=_outline("b1"),
        browser_metrics=[_run_metrics(
            (1, 1, "둘째 항목"),
            (0, 0, "첫째 항목"),
            run_count=2,
        )],
    )
    assert result.passed
    assert result.binding_deviations == 1
    assert result.order_violations == 0


def test_repair_readback_distinguishes_occlusion_from_source_and_telemetry_failure() -> None:
    metrics = _metrics(occluded=0.75)
    diagnosed = inspect_browser_source_readback(
        manifest=_manifest(), outline=_outline("b1", "b2"), browser_metrics=[metrics]
    )
    assert diagnosed.visibility_failures == ("b1", "b2")
    assert not diagnosed.integrity_failures
    assert not diagnosed.telemetry_failures
    assert diagnosed.evidence[0].occluded_fraction == 0.75
    assert diagnosed.as_dict()["verified"] is False
    with pytest.raises(BrowserSourceReadbackError):
        verify_browser_source_readback(
            manifest=_manifest(), outline=_outline("b1", "b2"), browser_metrics=[metrics]
        )

    metrics["elements"][0]["renderedText"] = "rewritten source"
    metrics["elements"][1].pop("occlusionMeasurementEvidence")
    diagnosed = inspect_browser_source_readback(
        manifest=_manifest(), outline=_outline("b1", "b2"), browser_metrics=[metrics]
    )
    assert diagnosed.integrity_failures == ("b1",)
    assert diagnosed.telemetry_failures == ("b2",)


def test_repair_readback_malformed_capture_is_diagnostic_and_strict_verifier_still_raises() -> None:
    metrics = _metrics()
    metrics["fontsReady"] = False
    diagnosed = inspect_browser_source_readback(
        manifest=_manifest(), outline=_outline("b1", "b2"), browser_metrics=[metrics]
    )
    assert diagnosed.diagnostic_error == "browser font/probe telemetry is incomplete"
    assert diagnosed.failed_slide_indices == (0,)
    assert diagnosed.passed is False
    with pytest.raises(BrowserSourceReadbackError) as error:
        verify_browser_source_readback(
            manifest=_manifest(), outline=_outline("b1", "b2"), browser_metrics=[metrics]
        )
    assert error.value.detail == diagnosed.diagnostic_error


def test_structural_repair_comparison_cannot_trade_one_source_block_for_another() -> None:
    from services.authored_structural_repair import _visibility, _visibility_comparison

    original = _visibility(_metrics(occluded=0.75), ("b1", "b2"))
    candidate_metrics = _metrics(occluded=0.25)
    candidate = _visibility(candidate_metrics, ("b1", "b2"))
    assert _visibility_comparison(original, candidate) == (True, True)
    candidate_metrics["elements"][1]["occludedFraction"] = 0.9
    candidate = _visibility(candidate_metrics, ("b1", "b2"))
    assert _visibility_comparison(original, candidate) == (False, False)


def test_structural_repair_clipping_needs_measured_bounds_for_partial_progress() -> None:
    from services.authored_structural_repair import _visibility, _visibility_comparison

    metrics = _metrics(clipped=True)
    unmeasured = _visibility(metrics, ("b1", "b2"))
    assert _visibility_comparison(unmeasured, unmeasured) == (False, False)
    for element in metrics["elements"]:
        element["source_clip_bounds"] = {"left": 0, "top": 0, "right": 120, "bottom": 720}
        element["text_fragments"] = [dict(element["bbox"])]
    original = _visibility(metrics, ("b1", "b2"))
    for element in metrics["elements"]:
        element["source_clip_bounds"]["right"] = 170
    candidate = _visibility(metrics, ("b1", "b2"))
    assert original["b1"]["clipping_fraction"] == pytest.approx(0.5)
    assert candidate["b1"]["clipping_fraction"] == pytest.approx(0.25)
    assert _visibility_comparison(original, candidate) == (True, True)


def test_structural_repair_rejects_source_structure_and_asset_replacement_before_render() -> None:
    from services.authored_structural_repair import source_repair_html_violations

    baseline = '<p data-qid="q1" data-source-block-id="b1" style="width:100px">원문</p><img data-qid="q2" src="original">'
    assert not source_repair_html_violations(baseline, baseline.replace("width:100px", "width:200px"))
    assert source_repair_html_violations(baseline, baseline.replace("원문", "요약"))
    assert source_repair_html_violations(baseline, baseline.replace('src="original"', 'src="replacement"'))
    assert source_repair_html_violations(baseline, baseline.replace('data-qid="q1"', 'data-qid="q3"'))


def test_structural_repair_checkpoint_requires_admission_and_exact_immutable_evidence() -> None:
    from dataclasses import replace
    from services.authored_structural_repair import (
        REPAIR_GATE_VERSION, RepairAssessment, SlideRepairResult, _digest, _json_digest,
        bind_repair_candidate, validate_repair_result,
        structural_checkpoint_metrics, validate_structural_checkpoint,
    )

    html, png, metrics = "<p>source</p>", b"rendered png", {"width": 1280}
    assessment = RepairAssessment(
        "admitted", source_integrity={"verified": True}, composition_report={"passed": True},
        style_report={"passed": True}, evidence={
            "gate_version": REPAIR_GATE_VERSION, "absolute_slide_index": 33,
            "baseline_html_digest": _digest("original"), "html_digest": _digest(html),
            "metrics_digest": _json_digest(metrics), "source_context_digest": _digest("manifest"),
        },
    )
    assessment = bind_repair_candidate(assessment, html, png, metrics)
    result = SlideRepairResult(3, 33, _digest("original"), html, png, metrics, assessment, True)
    validate_repair_result(result, expected_slide_index=33)
    checkpoint_metrics = structural_checkpoint_metrics(result)
    assert validate_structural_checkpoint(
        html, png, checkpoint_metrics, expected_slide_index=33,
        baseline_html_digest=_digest("original"),
    )
    assert not validate_structural_checkpoint(
        html, png, checkpoint_metrics, expected_slide_index=33,
        baseline_html_digest=_digest("different original"),
    )
    assert not validate_structural_checkpoint(
        html, b"changed png", checkpoint_metrics, expected_slide_index=33,
        baseline_html_digest=_digest("original"),
    )
    checkpoint_metrics["width"] = 900
    assert not validate_structural_checkpoint(
        html, png, checkpoint_metrics, expected_slide_index=33,
        baseline_html_digest=_digest("original"),
    )
    with pytest.raises(ValueError, match="not_admitted"):
        validate_repair_result(replace(result, assessment=replace(assessment, decision="provisional")))
    with pytest.raises(ValueError, match="evidence_invalid"):
        validate_repair_result(replace(result, slide_index=3))
    with pytest.raises(ValueError, match="evidence_invalid"):
        validate_repair_result(replace(result, png=b"different render"))
    metrics["width"] = 640
    with pytest.raises(ValueError, match="evidence_invalid"):
        validate_repair_result(result)


def test_structural_repair_evaluator_accumulates_only_monotonic_source_candidates() -> None:
    asyncio.run(_check_structural_repair_evaluator_accumulation())


def test_structural_repair_evaluator_accepts_nested_frozen_product_metrics() -> None:
    from types import MappingProxyType
    from services.authored_structural_repair import (
        _digest, _json_digest, bind_repair_candidate, make_source_repair_evaluator,
    )

    def freeze(value):
        if isinstance(value, dict):
            return MappingProxyType({key: freeze(item) for key, item in value.items()})
        if isinstance(value, list):
            return tuple(freeze(item) for item in value)
        return value

    async def check():
        baseline = (
            f'<h1 data-qid="{deterministic_source_qid("b1")}" style="width:200px">원문 제목</h1>'
            f'<p data-qid="{deterministic_source_qid("b2")}">중요한 본문</p>'
        )
        candidate = baseline.replace("200px", "220px")
        before, after = _metrics(occluded=0.75), _metrics(occluded=0.25)
        for html, metrics in ((baseline, before), (candidate, after)):
            metrics.update(source_digest=_digest(html), render_digest=_digest("render:" + html))
        frozen_before, frozen_after = freeze(before), freeze(after)
        assert _json_digest(frozen_before) == _json_digest(before)

        def composition(html, metrics):
            # The callback receives its own mutable snapshot, never aliases
            # into a frozen capture or the evaluator's selected state.
            metrics["elements"][0]["bbox"]["x"] = 999
            return freeze({"passed": True, "violations": []})

        evaluator = make_source_repair_evaluator(
            manifest=freeze(_manifest()), outline=freeze(_outline("b1", "b2")),
            baseline_html=baseline, baseline_metrics=frozen_before,
            composition_validator=composition, context=freeze({"protected_qids": []}),
        )
        result = await evaluator(33, candidate, frozen_after)
        assert result.decision == "provisional", result.reasons
        bound = bind_repair_candidate(result, candidate, b"png", frozen_after)
        evaluator.advance(_digest(baseline), bound, candidate, frozen_after)
        assert evaluator.selected_digest == _digest(candidate)
        assert frozen_before["elements"][0]["bbox"]["x"] == 20
        assert frozen_after["elements"][0]["bbox"]["x"] == 20
        assert (await evaluator(33, candidate, frozen_after)).decision == "reject"

    asyncio.run(check())


async def _check_structural_repair_evaluator_accumulation() -> None:
    from services.authored_structural_repair import _digest, make_source_repair_evaluator

    baseline = (
        f'<h1 data-qid="{deterministic_source_qid("b1")}" style="width:200px">원문 제목</h1>'
        f'<p data-qid="{deterministic_source_qid("b2")}">중요한 본문</p>'
    )

    def capture(html, occluded):
        metrics = _metrics(occluded=occluded)
        metrics.update(source_digest=_digest(html), render_digest=_digest("instrumented:" + html))
        return metrics

    evaluator = make_source_repair_evaluator(
        manifest=_manifest(), outline=_outline("b1", "b2"), baseline_html=baseline,
        baseline_metrics=capture(baseline, 0.75),
        composition_validator=lambda html, metrics: {"passed": True, "violations": []},
    )
    partial_html = baseline.replace("200px", "220px")
    partial_metrics = capture(partial_html, 0.25)
    partial = await evaluator(33, partial_html, partial_metrics)
    assert partial.decision == "provisional", partial.reasons
    assert (await evaluator(33, partial_html, partial_metrics)) == partial
    assert evaluator.selected_digest == _digest(baseline)

    other_html = baseline.replace("200px", "230px")
    other = await evaluator(33, other_html, capture(other_html, 0.5))
    assert other.decision == "provisional", other.reasons
    assert (await evaluator(33, partial_html, partial_metrics)) == partial
    evaluator.advance(_digest(baseline), partial, partial_html, partial_metrics)
    assert evaluator.selected_digest == _digest(partial_html)
    with pytest.raises(ValueError, match="selected_state_stale"):
        evaluator.advance(_digest(baseline), other, other_html, capture(other_html, 0.5))

    regressing = await evaluator(33, baseline.replace("200px", "230px"), capture(baseline.replace("200px", "230px"), 0.5))
    assert regressing.decision == "reject"
    missing_source = partial_html.replace("중요한 본문", "summary")
    assert (await evaluator(33, missing_source, capture(missing_source, 0.0))).decision == "reject"

    final_html = baseline.replace("200px", "240px")
    final = await evaluator(33, final_html, capture(final_html, 0.0))
    assert final.decision == "admitted", final.reasons
    assert final.source_integrity["verified"] is True
    assert final.evidence["absolute_slide_index"] == 33

    # Source visibility can already pass while two independent composition
    # defects remain. Solving one permits a selected intermediate candidate.
    first_defect = {"category": "overflow", "qids": ["card-1"], "severity": "high"}
    second_defect = {"category": "overflow", "qids": ["card-2"], "severity": "high"}
    composition_evaluator = make_source_repair_evaluator(
        manifest=_manifest(), outline=_outline("b1", "b2"), baseline_html=baseline,
        baseline_metrics=capture(baseline, 0.0),
        composition_validator=lambda html, metrics: {
            "passed": False,
            "violations": [first_defect, second_defect] if html == baseline else [second_defect],
        },
    )
    composition_partial = await composition_evaluator(33, partial_html, capture(partial_html, 0.0))
    assert composition_partial.decision == "provisional", composition_partial.reasons
    assert composition_partial.source_integrity["verified"] is True
    incomplete_metrics = capture(final_html, 0.0)
    incomplete_metrics["elements"][1].pop("continuousOccludedFraction")
    incomplete = await composition_evaluator(33, final_html, incomplete_metrics)
    assert incomplete.decision == "reject"
    assert "repair_visibility_measurement_incomplete" in incomplete.reasons


def test_structural_repair_style_guard_retains_9_5pt_floor_and_protected_geometry() -> None:
    from copy import deepcopy
    from services.authored_structural_repair import protected_scope_violations

    baseline = _metrics()
    for item in baseline["elements"]:
        item["font_size"] = 16
    candidate = deepcopy(baseline)
    candidate["elements"][1]["font_size"] = 12.4  # 9.3pt: below this repair contract.
    reasons = protected_scope_violations("<p>source</p>", "<p>source</p>", baseline, candidate, ())
    assert any(reason.startswith("repair_font_floor:") for reason in reasons)
    candidate = deepcopy(baseline)
    candidate["elements"][0]["bbox"]["x"] += 10
    reasons = protected_scope_violations("<p>source</p>", "<p>source</p>", baseline, candidate, (deterministic_source_qid("b1"),))
    assert any(reason.startswith("repair_protected_geometry_changed:") for reason in reasons)
    baseline = deepcopy(baseline)
    baseline["elements"][0]["bbox"]["height"] = 0
    candidate = deepcopy(baseline)
    reasons = protected_scope_violations(
        "<p>source</p>", "<p>source</p>", baseline, candidate,
        (deterministic_source_qid("b1"),),
    )
    assert not any(
        reason.startswith("repair_protected_geometry_changed:") for reason in reasons
    )


def test_structural_repair_evaluator_rejects_a_candidate_that_drops_source_text() -> None:
    """Validation run-14: a strict verbatim check killed the deck, not the candidate.

    The repair's composition validator runs the same fail-closed
    ``_require_source_verbatim_bindings`` the deck uses. Raised from inside the
    evaluator it escaped the repair round and ended a 35-slide deck at 34/35.
    The candidate must be REJECTED instead, leaving the original slide selected
    and the slide unresolved for the terminal fallback to flag.
    """

    from services.authored_presentation_generation import (
        _require_source_verbatim_bindings,
    )
    from services.authored_structural_repair import (
        SOURCE_VERBATIM_REJECTION,
        _digest,
        make_source_repair_evaluator,
        source_verbatim_rejection,
    )

    bindings = (("b1", "원문 제목"), ("b2", "중요한 본문"))
    baseline = stamp_source_block_ids(
        '<h1 data-qid="t" style="width:200px">원문 제목</h1>'
        '<p data-qid="b">중요한 본문</p>',
        bindings,
    )
    # The candidate summarised one bound block away -- exactly run-14's shape.
    candidate = stamp_source_block_ids(
        '<h1 data-qid="t" style="width:220px">원문 제목</h1><p data-qid="b">요약</p>',
        (("b1", "원문 제목"),),
    )

    def capture(html, occluded):
        metrics = _metrics(occluded=occluded)
        metrics.update(
            source_digest=_digest(html), render_digest=_digest("render:" + html)
        )
        return metrics

    seen: list[dict] = []

    async def validate_candidate(_index, html, _metrics):
        _require_source_verbatim_bindings([html], (bindings,))

    async def composition(html, metrics):
        try:
            await validate_candidate(2, html, metrics)
        except BrowserSourceReadbackError as error:
            rejected = source_verbatim_rejection(error)
            seen.append(rejected)
            return rejected
        return {"passed": True, "violations": []}

    # The check really does refuse this candidate on its own terms.
    with pytest.raises(BrowserSourceReadbackError):
        _require_source_verbatim_bindings([candidate], (bindings,))

    async def check():
        evaluator = make_source_repair_evaluator(
            manifest=_manifest(), outline=_outline("b1", "b2"),
            baseline_html=baseline, baseline_metrics=capture(baseline, 0.75),
            composition_validator=composition,
        )
        assessment = await evaluator(2, candidate, capture(candidate, 0.0))
        assert assessment.decision == "reject"
        assert "repair_composition_contract_failed" in assessment.reasons
        assert evaluator.selected_digest == _digest(baseline)

    asyncio.run(check())

    assert seen and seen[0]["contract_violations"] == [SOURCE_VERBATIM_REJECTION]
    assert seen[0]["passed"] is False


def test_generation_repair_validator_routes_the_strict_check_through_the_rejection() -> None:
    """The generation-side validator must not call the strict check bare again."""

    import inspect as inspect_module

    from services import authored_presentation_generation

    source = inspect_module.getsource(authored_presentation_generation)
    start = source.index("def composition_validator(")
    body = source[start : source.index("measured_dom =", start)]
    assert "await validate_source_repair_candidate(" in body
    assert "except BrowserSourceReadbackError as error:" in body
    assert "return source_verbatim_rejection(error)" in body
