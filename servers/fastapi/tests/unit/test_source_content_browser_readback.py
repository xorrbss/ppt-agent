from __future__ import annotations

import hashlib

import pytest

from services.authored_vnext.capability_registry import CAPABILITY_REGISTRY_DIGEST
from utils.authored_dom_quality import ensure_authored_qids
from services.source_content.source_runs import source_run_binding_id
from services.source_content.browser_readback import (
    BrowserSourceReadbackError,
    deterministic_source_block_id,
    deterministic_source_image_block_id,
    deterministic_source_qid,
    source_block_data_attribute,
    stamp_source_block_ids,
    stamp_source_image_block_ids,
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


def test_deterministic_id_and_attribute_are_stable_and_escaped() -> None:
    assert deterministic_source_block_id("unit-1", 2, "A  B") == deterministic_source_block_id("unit-1", 2, "A B")
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


def test_image_occurrence_ids_are_stable_and_stamp_dom_order() -> None:
    first = deterministic_source_image_block_id("unit-1", 3, "sha256:abc", 2)
    assert first == deterministic_source_image_block_id("unit-1", 3, "sha256:abc", 2)
    assert first != deterministic_source_image_block_id("unit-1", 3, "sha256:abc", 3)
    stamped = stamp_source_image_block_ids('<div><img src="a"><img src="a"></div>', [first, "img-2"])
    assert stamped.count("data-source-block-id=") == 2
    assert stamp_source_image_block_ids(stamped, [first, "img-2"]) == stamped
    assert stamped.count("data-qid=") == 2


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
