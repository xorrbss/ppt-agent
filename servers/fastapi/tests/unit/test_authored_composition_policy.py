import asyncio
from types import MappingProxyType, SimpleNamespace

import pytest

from models.generate_presentation_request import GeneratePresentationRequest
from services.authored_checkpoint_errors import AuthoredStageError
from services.authored_composition_policy import (
    AuthoredCapacityPlan,
    CompositionProfile,
    SlideCapacity,
    _is_supporting_candidate,
    capacity_violations,
    ensure_authored_semantic_roles,
    repair_authored_layout_alignment,
    reduce_authored_closing_capacity,
    reduce_authored_closing_title_punctuation,
    resolve_composition_profile,
)
from services.authored_presentation_generation import (
    _admit_gate_repair_capacity_candidate,
    _admit_vqa_capacity_candidate,
    _apply_bounded_closing_capacity_reducer,
    _apply_bounded_header_eyebrow_fallback,
    _apply_final_checkpoint_closing_capacity_reducer,
    _apply_final_checkpoint_header_eyebrow_fallback,
    _bind_bounded_repair_evidence,
    _callable_accepts_keyword,
    _capacity_gate_initial_critiques,
    _evaluate_capacity_role_evidence,
    _final_authored_quality_gate,
    _require_source_verbatim_bindings,
)
from services.source_content.browser_readback import BrowserSourceReadbackError
from utils.renderer_isolation import admit_authored_html


def test_capacity_treats_only_exact_chart_week_ticks_as_supporting() -> None:
    decorations = [f"roadmap.decoration.{index:02d}" for index in range(1, 26)]
    content = ["roadmap.title", *[f"roadmap.item.{index:02d}" for index in range(1, 20)]]
    ticks = [f"roadmap.chart.week{index:02d}" for index in range(1, 17)]
    supporting = [f"roadmap.supporting.{index:02d}" for index in range(1, 5)]

    html_parts = ["<section>"]
    html_parts.extend(
        f'<div data-qid="{qid}" data-semantic-role="decoration"></div>'
        for qid in decorations
    )
    html_parts.append(
        '<h1 data-qid="roadmap.title" data-semantic-role="title">Roadmap</h1>'
    )
    html_parts.extend(
        f'<span data-qid="{qid}" data-semantic-role="body">item</span>'
        for qid in content[1:]
    )
    html_parts.extend(
        f'<span data-qid="{qid}" data-semantic-role="body">{index}</span>'
        for index, qid in enumerate(ticks, start=1)
    )
    html_parts.extend(
        f'<span data-qid="{qid}" data-semantic-role="supporting">note</span>'
        for qid in supporting
    )
    html_parts.append("</section>")
    html = "".join(html_parts)

    def metric(qid: str, role: str, text: str, tag: str = "span") -> dict[str, object]:
        return {
            "qid": qid,
            "tag": tag,
            "semantic_role": role,
            "rendered_text": text,
            "has_owned_text": bool(text),
            "font_size": 18,
            "font_loaded": True,
            "css_visible": True,
            "clipped": False,
            "glyph_clipped": False,
            "line_box_clipped": False,
            "bbox": {"x": 10, "y": 10, "width": 80, "height": 20},
        }

    elements = [metric(qid, "decoration", "", "div") for qid in decorations]
    elements.append(metric("roadmap.title", "title", "Roadmap", "h1"))
    elements.extend(metric(qid, "body", "item") for qid in content[1:])
    elements.extend(
        metric(qid, "body", str(index))
        for index, qid in enumerate(ticks, start=1)
    )
    elements.extend(metric(qid, "supporting", "note") for qid in supporting)
    assert len(elements) == 65

    normalized_html = ensure_authored_semantic_roles(html)
    assert normalized_html == html
    assert normalized_html.count('data-qid="') == 65
    assert all(
        f'data-qid="{qid}" data-semantic-role="body">{index}</span>'
        in normalized_html
        for index, qid in enumerate(ticks, start=1)
    )

    capacity_only_ticks = [
        qid
        for index, qid in enumerate(ticks, start=1)
        if _is_supporting_candidate(
            {"data-qid": qid}, owned_text=str(index), capacity_only=True
        )
    ]
    assert capacity_only_ticks == ticks
    assert all(
        not _is_supporting_candidate({"data-qid": qid}, owned_text=str(index))
        for index, qid in enumerate(ticks, start=1)
    )
    assert all(
        not _is_supporting_candidate(attrs, owned_text=text, capacity_only=True)
        for attrs, text in (
            ({"data-qid": "roadmap.chart.week01.copy"}, "1"),
            ({"data-qid": "roadmap.week01"}, "1"),
            ({"data-qid": "roadmap.chart.week1"}, "1"),
            ({"data-qid": "roadmap.chart.week01"}, "Week 1"),
            ({"data-qid": "roadmap.chart.week01"}, "100"),
            ({"data-qid": "roadmap.chart.week01"}, "2"),
        )
    )

    metrics = {"elements": elements}
    admitted = SlideCapacity(5, "ROADMAP", 100, 500, 500, 0, 20, 16)
    rejected = SlideCapacity(5, "ROADMAP", 100, 500, 500, 0, 19, 16)
    assert capacity_violations(normalized_html, metrics, admitted) == ()
    assert capacity_violations(normalized_html, metrics, rejected) == ("qids:20>19",)


def test_composition_profile_defaults_to_development_quality_gates() -> None:
    profile = resolve_composition_profile(SimpleNamespace())

    assert profile.quality_profile_id == "development"
    assert profile.require_source_readback is False
    assert profile.require_visual_review is False
    assert profile.require_performance is False
    assert profile.require_native_coverage is False
    assert profile.minimum_native_coverage_ratio == 0.0


def test_composition_release_profile_seals_required_quality_gates() -> None:
    profile = resolve_composition_profile(
        SimpleNamespace(_authored_quality_profile="release")
    )

    assert profile.quality_profile_id == "release"
    assert profile.require_source_readback is True
    assert profile.require_visual_review is True
    assert profile.require_performance is True
    assert profile.require_native_coverage is True
    assert profile.minimum_native_coverage_ratio == pytest.approx(0.70)


def test_http_request_preserves_release_quality_profile_at_admission() -> None:
    request = GeneratePresentationRequest.model_validate(
        {"content": "Release deck", "qualityProfile": "release"}
    )

    assert request.quality_profile == "release"
    assert request.model_dump(mode="json", by_alias=True)["qualityProfile"] == "release"
    assert resolve_composition_profile(request).quality_profile_id == "release"
    assert GeneratePresentationRequest(content="Development deck").quality_profile == (
        "development"
    )


def test_composition_profile_rejects_unknown_quality_profile() -> None:
    with pytest.raises(ValueError, match="authored_quality_profile_unknown"):
        resolve_composition_profile(SimpleNamespace(quality_profile="release-ish"))


def test_post_vqa_qid_overflow_restores_frozen_gate_valid_candidate() -> None:
    capacity = SlideCapacity(4, "PILLARS", 58, 290, 380, 0, 32)

    def candidate(count: int) -> tuple[str, dict[str, object]]:
        qids = [f"s05-item-{index}" for index in range(count)]
        html = "<section>" + "".join(
            f'<span data-qid="{qid}" data-semantic-role="body">x</span>'
            for qid in qids
        ) + "</section>"
        metrics = {
            "elements": [
                {
                    "qid": qid,
                    "tag": "span",
                    "semantic_role": "body",
                    "rendered_text": "x",
                    "has_owned_text": True,
                    "css_visible": True,
                    "clipped": False,
                    "glyph_clipped": False,
                    "line_box_clipped": False,
                    "bbox": {"x": 1, "y": 1, "width": 10, "height": 10},
                    "font_size": 18,
                    "font_loaded": True,
                }
                for qid in qids
            ]
        }
        return html, metrics

    original_html, original_metrics = candidate(30)
    revised_html, revised_metrics = candidate(34)
    selected_html, selected_png, selected_metrics, admitted = (
        _admit_vqa_capacity_candidate(
            slide_index=4,
            original_html=original_html,
            original_png=b"original",
            original_metrics=original_metrics,
            candidate_html=revised_html,
            candidate_png=b"revised",
            candidate_metrics=revised_metrics,
            capacity=capacity,
        )
    )

    assert capacity_violations(original_html, original_metrics, capacity) == ()
    assert "qids:34>32" in capacity_violations(
        revised_html, revised_metrics, capacity
    )
    assert revised_html.count("data-qid=") == 34
    assert admitted is False
    assert selected_html == original_html
    assert selected_png == b"original"
    assert selected_metrics is original_metrics


def test_vision_qa_false_gate_repair_normalizes_short_labels_without_qid_loss(
    monkeypatch,
) -> None:
    request = GeneratePresentationRequest(content="Gate repair", vision_qa=False)
    capacity = SlideCapacity(4, "PILLARS", 58, 290, 380, 0, 32)
    body_qids = [f"content.{index}" for index in range(25)]
    number_qids = [f"step.{index}.no" for index in range(7)]
    marker_qids = ["controls.refresh.mark", "controls.alert.mark"]
    labels = {
        **{qid: "x" for qid in body_qids},
        **{qid: f"{index + 1:02d}" for index, qid in enumerate(number_qids)},
        marker_qids[0]: "↻",
        marker_qids[1]: "!",
    }
    original_html = (
        '<section data-qid="slide.root" data-semantic-role="decoration">'
        + "".join(
            f'<span data-qid="{qid}" data-semantic-role="body">{labels[qid]}</span>'
            for qid in labels
        )
        + "</section>"
    )

    def metrics(*, normalized: bool) -> dict[str, object]:
        return {
            "elements": [
                {
                    "qid": "slide.root",
                    "tag": "section",
                    "semantic_role": "decoration",
                    "rendered_text": "",
                    "has_owned_text": False,
                },
                *[
                    {
                        "qid": qid,
                        "tag": "span",
                        "semantic_role": (
                            "supporting"
                            if normalized and qid in {*number_qids, *marker_qids}
                            else "body"
                        ),
                        "rendered_text": labels[qid],
                        "has_owned_text": True,
                        "css_visible": True,
                        "clipped": False,
                        "glyph_clipped": False,
                        "line_box_clipped": False,
                        "bbox": {"x": 1, "y": 1, "width": 10, "height": 10},
                        "font_size": 18,
                        "font_loaded": True,
                    }
                    for qid in labels
                ],
            ]
        }

    original_metrics = metrics(normalized=False)
    normalized_html = ensure_authored_semantic_roles(original_html)
    normalized_metrics = metrics(normalized=True)
    captures: list[str] = []

    async def fake_capture(candidate_html):
        captures.append(candidate_html)
        assert candidate_html == normalized_html
        return SimpleNamespace(png=b"fresh-repair", browser_metrics=normalized_metrics)

    monkeypatch.setattr(
        "services.authored_presentation_generation.render_html_to_png_with_metrics",
        fake_capture,
    )
    rebound_htmls, rebound_pngs, rebound_metrics = asyncio.run(
        _bind_bounded_repair_evidence([normalized_html])
    )
    selected_html, selected_png, selected_metrics, admitted = (
        _admit_gate_repair_capacity_candidate(
            slide_index=4,
            original_html=original_html,
            original_png=b"failed-original",
            original_metrics=original_metrics,
            candidate_html=rebound_htmls[0],
            candidate_png=rebound_pngs[0],
            candidate_metrics=rebound_metrics[0],
            capacity=capacity,
        )
    )

    assert request.vision_qa is False
    assert captures == [normalized_html]
    assert len(labels) == 34
    assert original_html.count("data-qid=") == normalized_html.count("data-qid=") == 35
    assert normalized_html.count('data-semantic-role="supporting"') == 9
    assert "qids:34>32" in capacity_violations(
        original_html, original_metrics, capacity
    )
    assert capacity_violations(selected_html, selected_metrics, capacity) == ()
    assert (selected_html, selected_png, admitted) == (
        normalized_html,
        b"fresh-repair",
        True,
    )

    with pytest.raises(AuthoredStageError) as failed:
        _admit_gate_repair_capacity_candidate(
            slide_index=4,
            original_html=original_html,
            original_png=b"failed-original",
            original_metrics=original_metrics,
            candidate_html=original_html,
            candidate_png=b"still-over-capacity",
            candidate_metrics=original_metrics,
            capacity=capacity,
        )
    assert failed.value.code == "authored_slide_capacity_exceeded"
    assert failed.value.retryable is False


def test_capacity_violations_enforce_qids_images_text_and_font_floor():
    capacity = SlideCapacity(
        slide_index=0,
        role="PROBLEM",
        title_max_chars=20,
        body_max_chars=30,
        max_visible_text_chars=10,
        max_images=1,
        max_qids=1,
        min_font_px=16,
    )
    html = (
        '<div data-qid="a">12345678901</div>'
        '<div data-qid="b">x</div><img src="a"><img src="b">'
    )
    metrics = {
        "viewport": {"width": 1280, "height": 720},
        "elements": [
            {
                "qid": "a",
                "tag": "div",
                "semantic_role": "body",
                "rendered_text": "12345678901",
                "font_size": 12,
                "font_loaded": True,
            },
            {
                "qid": "b",
                "tag": "div",
                "semantic_role": "body",
                "rendered_text": "x",
                "font_size": 20,
                "font_loaded": True,
            },
        ],
    }

    violations = capacity_violations(html, metrics, capacity)

    assert "qids:2>1" in violations
    assert "images:2>1" in violations
    assert "visible_text:12>10" in violations
    assert "font:12<16" in violations


def test_variadic_pipeline_adapter_accepts_composition_profile_keyword():
    async def facade_adapter(*args, **kwargs):
        return args, kwargs

    async def legacy_double(outline, brand, style):
        return outline, brand, style

    assert _callable_accepts_keyword(
        facade_adapter, "composition_profile"
    ) is True
    assert _callable_accepts_keyword(
        legacy_double, "composition_profile"
    ) is False


def test_capacity_violations_ignore_non_visible_script_text():
    capacity = SlideCapacity(0, "COVER", 20, 20, 5, 1, 2)
    html = '<script>this text must not count</script><div data-qid="a">short</div>'
    metrics = {
        "elements": [
            {
                "qid": "a",
                "tag": "div",
                "semantic_role": "body",
                "rendered_text": "short",
                "font_size": 16,
                "font_loaded": True,
                "css_visible": True,
                "bbox": {"x": 10, "y": 10, "width": 120, "height": 24},
                "clipped": False,
                "glyph_clipped": False,
                "line_box_clipped": False,
            }
        ]
    }
    assert capacity_violations(html, metrics, capacity) == ()


def test_capacity_violations_require_browser_title_and_body_roles():
    """Characterization: HTML title/body strings must not satisfy the gate alone."""
    capacity = SlideCapacity(0, "PROBLEM", 20, 20, 40, 1, 4, min_font_px=16)
    html = '<h1 data-qid="q-title">Title</h1><p data-qid="q-body">Body copy</p>'
    # The browser saw both text nodes, but did not report semantic roles.
    metrics = {
        "elements": [
            {"qid": "q-title", "tag": "h1", "rendered_text": "Title", "font_size": 24},
            {"qid": "q-body", "tag": "p", "rendered_text": "Body copy", "font_size": 18},
        ]
    }
    violations = capacity_violations(html, metrics, capacity)
    assert "browser_title_role_missing" in violations
    assert "browser_body_role_missing" in violations


def test_capacity_violations_require_image_semantic_role():
    capacity = SlideCapacity(0, "PILLARS", 20, 20, 40, 1, 2, min_font_px=16)
    html = '<img data-qid="q-img" src="asset.png">'
    base_image = {
        "qid": "q-img",
        "tag": "img",
        "rendered_text": "",
        "font_size": 16,
        "font_loaded": True,
        "css_visible": True,
        "bbox": {"x": 10, "y": 10, "width": 320, "height": 180},
        "clipped": False,
        "glyph_clipped": False,
        "glyphClipped": False,
        "line_box_clipped": False,
        "lineBoxClipped": False,
        "image": {
            "complete": True,
            "naturalWidth": 640,
            "naturalHeight": 360,
            "cssVisible": True,
        },
    }
    missing_role = capacity_violations(html, {"elements": [base_image]}, capacity)
    assert "browser_image_role_missing:q-img" in missing_role

    valid_image = dict(base_image, semantic_role="bodyImage")
    assert capacity_violations(html, {"elements": [valid_image]}, capacity) == ()
    ledger_frozen = MappingProxyType(
        {
            "elements": (
                MappingProxyType(
                    {
                        **valid_image,
                        "image": MappingProxyType(dict(base_image["image"])),
                    }
                ),
            )
        }
    )
    assert capacity_violations(html, ledger_frozen, capacity) == ()
    malformed = dict(valid_image, image={**base_image["image"], "naturalWidth": "not-a-number"})
    assert "browser_image_unverified" in capacity_violations(html, {"elements": [malformed]}, capacity)


def test_capacity_violations_use_browser_rendered_role_text_lengths():
    capacity = SlideCapacity(0, "PROBLEM", 5, 8, 40, 0, 2, min_font_px=16)
    html = '<h1 data-qid="q-title">Short</h1><p data-qid="q-body">Body</p>'
    metrics = {
        "elements": [
            {
                "qid": "q-title", "tag": "h1", "semantic_role": "title",
                "rendered_text": "Rendered title is too long", "font_size": 24,
                "font_loaded": True, "css_visible": True,
                "bbox": {"x": 0, "y": 0, "width": 200, "height": 30},
                "clipped": False, "glyph_clipped": False, "line_box_clipped": False,
            },
            {
                "qid": "q-body", "tag": "p", "semantic_role": "body",
                "rendered_text": "Body", "font_size": 18,
                "font_loaded": True, "css_visible": True,
                "bbox": {"x": 0, "y": 40, "width": 200, "height": 30},
                "clipped": False, "glyph_clipped": False, "line_box_clipped": False,
            },
        ]
    }
    violations = capacity_violations(html, metrics, capacity)
    assert "browser_title:26>5" in violations


def test_source_capacity_evidence_accepts_exact_text_promoted_to_title_owner():
    first_source_text = "AI 검증 프레임워크"
    second_source_text = "입력과 출력의 근거를 추적합니다"
    metrics = {
        "elements": [
            {
                "qid": "source.title",
                "tag": "h1",
                "semantic_role": "title",
                "rendered_text": first_source_text,
                "has_owned_text": True,
                "font_size": 32,
                "font_loaded": True,
                "css_visible": True,
                "clipped": False,
                "glyph_clipped": False,
                "line_box_clipped": False,
                "bbox": {"x": 72, "y": 32, "width": 600, "height": 28},
                "viewport_intersection_ratio": 1,
            },
            {
                "qid": "source.body",
                "tag": "p",
                "semantic_role": "body",
                "rendered_text": second_source_text,
                "has_owned_text": True,
                "font_size": 18,
                "font_loaded": True,
                "css_visible": True,
                "clipped": False,
                "glyph_clipped": False,
                "line_box_clipped": False,
                "bbox": {"x": 72, "y": 100, "width": 900, "height": 48},
                "viewport_intersection_ratio": 1,
            },
        ]
    }
    capacity = SlideCapacity(0, "COVER", 58, 220, 280, 0, 2)

    evidence = _evaluate_capacity_role_evidence(metrics, capacity)

    assert evidence["checks"]["title"] is True
    assert evidence["checks"]["body"] is True
    assert evidence["checks"]["qid"] is True
    assert evidence["checks"]["font"] is True


def test_semantic_roles_are_stamped_before_owned_text_browser_capacity_gate(monkeypatch):
    html = (
        '<body data-qid="slide.body">'
        '<h1 data-qid="header.title">Title</h1>'
        '<span data-qid="meta.label">Label</span>'
        '<span data-qid="chart.scale.zero" data-semantic-role="body">0</span>'
        '<p data-qid="copy">Body</p>'
        '<br data-qid="line.break">'
        '<img data-qid="hero.image" src="asset.png">'
        "</body>"
    )
    stamped = ensure_authored_semantic_roles(html)

    assert 'data-qid="header.title" data-semantic-role="title"' in stamped
    assert 'data-qid="copy" data-semantic-role="body"' in stamped
    assert 'data-qid="meta.label" data-semantic-role="supporting"' in stamped
    assert 'data-qid="chart.scale.zero" data-semantic-role="supporting"' in stamped
    assert 'data-qid="slide.body" data-semantic-role="decoration"' in stamped
    assert 'data-qid="line.break" data-semantic-role="decoration"' in stamped
    assert '<img data-qid="hero.image" src="asset.png" data-semantic-role="image">' in stamped
    assert ensure_authored_semantic_roles(stamped) == stamped

    def metric(qid, tag, role, text="", owns_text=False):
        return {
            "qid": qid,
            "tag": tag,
            "semantic_role": role,
            "rendered_text": text,
            "has_owned_text": owns_text,
            "font_size": 18,
            "font_loaded": True,
            "css_visible": True,
            "bbox": {"x": 10, "y": 10, "width": 100, "height": 24},
            "clipped": False,
            "glyph_clipped": False,
            "line_box_clipped": False,
        }

    legacy_checkpoint_html = (
        '<main data-qid="slide.root" data-semantic-role="body">'
        '<div data-qid="shape.rule" data-semantic-role="body"></div>'
        '<h1 data-qid="header.title" data-semantic-role="title">Title</h1>'
        '<p data-qid="copy" data-semantic-role="body">Body</p></main>'
    )
    migrated = ensure_authored_semantic_roles(legacy_checkpoint_html)
    assert '<main data-qid="slide.root" data-semantic-role="decoration">' in migrated
    assert '<div data-qid="shape.rule" data-semantic-role="decoration">' in migrated
    assert '<p data-qid="copy" data-semantic-role="body">Body</p>' in migrated

    async def fake_capture(html):
        assert html == migrated
        return SimpleNamespace(
            png=b"repair-png",
            browser_metrics={
                "elements": [
                    metric("slide.root", "main", "decoration", "Title Body", False),
                    metric("shape.rule", "div", "decoration", "", False),
                    metric("header.title", "h1", "title", "Title", True),
                    metric("copy", "p", "body", "Body", True),
                ]
            },
        )

    monkeypatch.setattr(
        "services.authored_presentation_generation.render_html_to_png_with_metrics",
        fake_capture,
    )
    repaired_htmls, repaired_pngs, repaired_metrics = asyncio.run(
        _bind_bounded_repair_evidence([legacy_checkpoint_html])
    )
    assert repaired_htmls == [migrated]
    assert repaired_pngs == [b"repair-png"]
    assert capacity_violations(
        repaired_htmls[0],
        repaired_metrics[0],
        SlideCapacity(0, "COVER", 10, 10, 20, 0, 2, min_font_px=16),
    ) == ()

    elements = [
        {
            key: value
            for key, value in metric(
                "slide.body", "body", "decoration", "Title Label 0 Body", False
            ).items()
            if key != "has_owned_text"
        },
        metric("header.title", "h1", "title", "Title", True),
        {**metric("meta.label", "span", "supporting", "Label", True), "font_size": 15},
        {**metric("chart.scale.zero", "span", "supporting", "0", True), "font_size": 13},
        metric("copy", "p", "body", "Body", True),
        {"qid": "line.break", "tag": "br", "semantic_role": "decoration", "has_owned_text": False},
        {
            **metric("hero.image", "img", "image"),
            "image": {
                "complete": True,
                "naturalWidth": 640,
                "naturalHeight": 360,
                "cssVisible": True,
            },
        },
    ]
    capacity = SlideCapacity(0, "COVER", 10, 10, 20, 1, 3, min_font_px=16)
    assert capacity_violations(stamped, {"elements": elements}, capacity) == ()

    sealed = (
        '<p data-qid="copy" data-source-block-id="source:block:1" '
        'data-semantic-role="body">Body</p>'
    )
    bindings = ((('source:block:1', 'Body'),),)
    _require_source_verbatim_bindings([sealed], bindings)
    with pytest.raises(BrowserSourceReadbackError):
        _require_source_verbatim_bindings([sealed.replace("Body", "Body..")], bindings)


def test_capacity_gate_builds_role_aware_initial_critiques_for_bounded_repair():
    failures = [
        {
            "slide_index": 1,
            "issues": [
                {
                    "category": "capacity",
                    "message": "browser_text_not_visible:header.eyebrow",
                }
            ],
        },
        {
            "slide_index": 2,
            "issues": [
                {"category": "capacity", "message": "qids:21>20"},
                {"category": "capacity", "message": "browser_body:299>270"},
            ],
        },
    ]

    critiques = _capacity_gate_initial_critiques(
        failures, [1, 2], ["COVER", "PROBLEM", "CLOSING"]
    )

    problem_details = [issue.detail for issue in critiques[0].issues]
    closing_details = [issue.detail for issue in critiques[1].issues]
    assert any(
        "header.eyebrow" in detail and "retain its semantic role" in detail
        for detail in problem_details
    )
    assert any(
        "1 excess content target" in detail and "never relabel" in detail
        for detail in closing_details
    )
    assert any(
        "29 chars" in detail and "semantic role meanings" in detail
        for detail in closing_details
    )


def test_layout_alignment_repair_restores_header_inset_and_symmetric_body_margin():
    problem = """<!doctype html><html><head><style>
    *{box-sizing:border-box}.header{height:127px}
    .eyebrow{height:22px;padding-top:0;font-size:16px}
    </style></head><body><div data-header-part="eyebrow">검증 기준</div></body></html>"""
    repaired_problem = repair_authored_layout_alignment(problem, role="PROBLEM")
    assert "authored-layout-alignment-repair-v1:header-inset" in repaired_problem
    assert "padding-top:34px" in repaired_problem
    assert "height:22px" not in repaired_problem
    assert repair_authored_layout_alignment(
        repaired_problem, role="PROBLEM"
    ) == repaired_problem

    closing = """<!doctype html><html><head><style>
    .body{margin:28px 72px 0;height:533px}
    </style></head><body><div data-header-part="body" data-qid="body.root"></div></body></html>"""
    repaired_closing = repair_authored_layout_alignment(closing, role="CLOSING")
    assert "authored-layout-alignment-repair-v1:body-class" in repaired_closing
    assert '<div class="body" data-header-part="body"' in repaired_closing
    assert repair_authored_layout_alignment(
        repaired_closing, role="CLOSING"
    ) == repaired_closing
    assert repair_authored_layout_alignment(
        closing.replace("<body>", '<body data-source-block-id="source:1">'),
        role="CLOSING",
    ) == closing.replace("<body>", '<body data-source-block-id="source:1">')


def test_layout_alignment_repair_keeps_rotated_arrow_tip_inside_connector_gutter():
    problem = '''<!doctype html><html><head><style>
    .evidence{display:grid;grid-template-columns:1fr 72px 1fr;align-items:center}
    .bridge{height:2px;background:#2563EB;position:relative}
    .bridge-tip{position:absolute;right:-1px;top:-6px;width:14px;height:14px;
      border-top:2px solid #2563EB;border-right:2px solid #2563EB;
      transform:rotate(45deg)}
    </style></head><body>
    <div data-qid="evidence.bridge"><div data-qid="evidence.bridge.tip"></div></div>
    <div data-qid="evidence.measure.main">5종 KPI로<br>효과 측정</div>
    </body></html>'''

    repaired = repair_authored_layout_alignment(problem, role="PROBLEM")

    assert "authored-connector-clearance-repair-v1" in repaired
    assert 'data-qid="evidence.bridge"]{width:44px!important;' in repaired
    assert "justify-self:start!important" in repaired
    assert repair_authored_layout_alignment(repaired, role="PROBLEM") == repaired


def test_semantic_roles_bind_header_title_to_the_text_owner():
    html = (
        '<div data-qid="header.title.group" data-header-part="title" '
        'data-semantic-role="title">'
        '<span data-qid="hdr.title.text" data-semantic-role="body">'
        '신뢰의 기준은 수치와 추적성으로 증명한다</span></div>'
    )

    stamped = ensure_authored_semantic_roles(html)

    assert (
        '<div data-qid="header.title.group" data-header-part="title" '
        'data-semantic-role="decoration">' in stamped
    )
    assert (
        '<span data-qid="hdr.title.text" data-semantic-role="title">' in stamped
    )


def test_final_checkpoint_closing_capacity_reducer_uses_exact_ledger_metrics(
    monkeypatch,
):
    html = '<p data-qid="closing.cue" data-semantic-role="body">정리 문구</p>'
    metrics = {"elements": []}
    capacity = SlideCapacity(0, "CLOSING", 62, 270, 350, 0, 20)
    pipeline = SimpleNamespace(
        evidence_attempt_id="attempt",
        evidence_ledger=SimpleNamespace(
            latest=lambda _attempt, _index: SimpleNamespace(
                html=html, browser_metrics=metrics
            )
        ),
    )
    calls = []

    async def fake_reducer(**kwargs):
        calls.append(kwargs)
        return "<p>reduced</p>", b"reduced", {"elements": []}

    monkeypatch.setattr(
        "services.authored_presentation_generation._apply_bounded_closing_capacity_reducer",
        fake_reducer,
    )
    htmls = [html]
    pngs = [b"before"]
    overrides = {}

    repaired = asyncio.run(
        _apply_final_checkpoint_closing_capacity_reducer(
            pipeline=pipeline,
            htmls=htmls,
            pngs=pngs,
            capacity_plan=SimpleNamespace(at=lambda _index: capacity),
            profile_id="ordinary",
            metrics_override=overrides,
        )
    )

    assert repaired == [0]
    assert calls[0]["browser_metrics"] is metrics
    assert htmls == ["<p>reduced</p>"]
    assert pngs == [b"reduced"]
    assert overrides == {0: {"elements": []}}


def test_closing_title_punctuation_reducer_keeps_gate_and_meaning_intact():
    html = (
        '<html><head></head><body>'
        '<div data-qid="closing.header.title" data-semantic-role="title">'
        '검증 가능한 관문이 신뢰할 수 있는 배포를 만듭니다</div>'
        '<h1 data-qid="closing.statement.title" data-semantic-role="title">'
        'AI가 생성해도, 배포 결정은 증거와 사람의 승인으로 완성합니다.</h1>'
        '</body></html>'
    )
    metrics = {
        "elements": [
            {
                "qid": "closing.header.title",
                "semantic_role": "title",
                "has_owned_text": True,
                "rendered_text": "검증 가능한 관문이 신뢰할 수 있는 배포를 만듭니다",
                "font_size": 20,
                "font_loaded": True,
                "css_visible": True,
                "clipped": False,
                "glyph_clipped": False,
                "line_box_clipped": False,
                "bbox": {"x": 72, "y": 32, "width": 600, "height": 28},
                "viewport_intersection_ratio": 1,
            },
            {
                "qid": "closing.statement.title",
                "semantic_role": "title",
                "has_owned_text": True,
                "rendered_text": "AI가 생성해도, 배포 결정은 증거와 사람의 승인으로 완성합니다.",
                "font_size": 20,
                "font_loaded": True,
                "css_visible": True,
                "clipped": False,
                "glyph_clipped": False,
                "line_box_clipped": False,
                "bbox": {"x": 72, "y": 100, "width": 900, "height": 48},
                "viewport_intersection_ratio": 1,
            },
        ]
    }
    capacity = SlideCapacity(0, "CLOSING", 62, 270, 350, 0, 20)

    reduced = reduce_authored_closing_title_punctuation(
        html, metrics, capacity, profile_id="ordinary"
    )

    assert "authored-closing-title-punctuation-reducer-v1" in reduced
    assert "AI가 생성해도 배포 결정은 증거와 사람의 승인으로 완성합니다</h1>" in reduced
    assert "검증 가능한 관문이 신뢰할 수 있는 배포를 만듭니다" in reduced


def test_bounded_closing_reducer_recaptures_21_targets_and_299_body_chars(
    monkeypatch,
):
    titles = [(f"title.{index}", "제목텍스트") for index in range(4)]
    groups = {
        "conclusion": [18, 18, 18, 18, 18],
        "criteria": [18, 18, 18, 17],
        "artifacts": [18, 18, 18, 18, 10],
    }
    body_nodes = [
        (f"{group}.item{index}", "가" * length)
        for group, lengths in groups.items()
        for index, length in enumerate(lengths, start=1)
    ]
    body_nodes.extend(
        [
            ("closing.claim", "나" * 18),
            ("closing.emphasis", "다" * 18),
            ("closing.cue", "라" * 20),
        ]
    )
    assert len(titles) + len(body_nodes) == 21
    assert sum(len(text) for _qid, text in body_nodes) == 299

    html = "<html><head></head><body>"
    html += "".join(
        f'<h2 data-qid="{qid}" data-semantic-role="title">{text}</h2>'
        for qid, text in titles
    )
    for group, lengths in groups.items():
        html += f'<ul data-qid="{group}.list" data-semantic-role="decoration">'
        html += "".join(
            f'<li data-qid="{group}.item{index}" data-semantic-role="body">'
            f'{"가" * length}</li>'
            for index, length in enumerate(lengths, start=1)
        )
        html += "</ul>"
    html += (
        '<p data-qid="closing.claim" data-semantic-role="body">'
        f'{"나" * 18}</p>'
        '<strong data-qid="closing.emphasis" data-semantic-role="body">'
        f'{"다" * 18}</strong>'
        '<div data-qid="closing.cue" data-semantic-role="body">'
        f'{"라" * 20}</div></body></html>'
    )

    def metric(qid, tag, role, text):
        return {
            "qid": qid,
            "tag": tag,
            "semantic_role": role,
            "rendered_text": text,
            "has_owned_text": bool(text),
            "font_size": 18,
            "font_loaded": True,
            "css_visible": True,
            "bbox": {"x": 10, "y": 10, "width": 120, "height": 24},
            "clipped": False,
            "glyph_clipped": False,
            "line_box_clipped": False,
        }

    before_elements = [
        *(metric(qid, "h2", "title", text) for qid, text in titles),
        *(
            metric(f"{group}.list", "ul", "decoration", "")
            for group in groups
        ),
        *(metric(qid, "li", "body", text) for qid, text in body_nodes[:-3]),
        metric("closing.claim", "p", "body", "나" * 18),
        metric("closing.emphasis", "strong", "body", "다" * 18),
        metric("closing.cue", "div", "body", "라" * 20),
    ]
    before_metrics = {"elements": before_elements}
    capacity = SlideCapacity(2, "CLOSING", 62, 270, 350, 0, 20)
    assert capacity_violations(html, before_metrics, capacity) == (
        "qids:21>20",
        "browser_body:299>270",
    )

    reduced = reduce_authored_closing_capacity(
        html, before_metrics, capacity, profile_id="ordinary"
    )
    assert "authored-closing-capacity-reducer-v1" in reduced
    assert 'data-qid="closing.cue"' not in reduced
    assert 'data-qid="artifacts.item5"' not in reduced
    assert 'data-qid="closing.claim" data-semantic-role="body"' in reduced
    assert 'data-qid="closing.emphasis" data-semantic-role="body"' in reduced
    assert reduce_authored_closing_capacity(
        reduced, before_metrics, capacity, profile_id="ordinary"
    ) == reduced
    assert reduce_authored_closing_capacity(
        html.replace("<body>", '<body data-source-block-id="source:1">'),
        before_metrics,
        capacity,
        profile_id="ordinary",
    ).find('data-qid="closing.cue"') >= 0

    removed = {"closing.cue", "artifacts.item5"}
    after_metrics = {
        "elements": [
            item for item in before_elements if item["qid"] not in removed
        ]
    }
    captures = []

    async def fake_capture(candidate_html):
        captures.append(candidate_html)
        assert candidate_html == reduced
        return SimpleNamespace(png=b"reduced-closing", browser_metrics=after_metrics)

    monkeypatch.setattr(
        "services.authored_presentation_generation.render_html_to_png_with_metrics",
        fake_capture,
    )
    repaired = asyncio.run(
        _apply_bounded_closing_capacity_reducer(
            html=html,
            browser_metrics=before_metrics,
            capacity=capacity,
            profile_id="ordinary",
        )
    )

    assert repaired is not None
    repaired_html, repaired_png, repaired_metrics = repaired
    assert captures == [repaired_html]
    assert repaired_png == b"reduced-closing"
    assert len(
        [
            item
            for item in repaired_metrics["elements"]
            if item["semantic_role"] in {"title", "body", "image"}
        ]
    ) == 19
    assert sum(
        len(item["rendered_text"])
        for item in repaired_metrics["elements"]
        if item["semantic_role"] == "body"
    ) == 269
    assert capacity_violations(repaired_html, repaired_metrics, capacity) == ()


def test_bounded_header_eyebrow_fallback_recaptures_exact_qid(monkeypatch):
    html = (
        "<html><head></head><body>"
        '<div data-header-part="band" data-header-band="127" '
        'data-qid="header.band" data-semantic-role="decoration">'
        '<div data-header-part="eyebrow" data-qid="header.eyebrow" '
        'data-semantic-role="supporting">검증 기준</div>'
        '<h1 data-header-part="title" data-qid="header.title" '
        'data-semantic-role="title">Title</h1></div></body></html>'
    )

    def element(qid, role, text, *, y, height, clipped=False, tag="div"):
        return {
            "qid": qid,
            "tag": tag,
            "semantic_role": role,
            "rendered_text": text,
            "has_owned_text": bool(text),
            "font_size": 16 if role == "supporting" else 38,
            "font_loaded": True,
            "css_visible": True,
            "bbox": {"x": 72, "y": y, "width": 1136, "height": height},
            "clipped": clipped,
            "glyph_clipped": clipped,
            "line_box_clipped": clipped,
        }

    band = element("header.band", "decoration", "", y=0, height=127)
    before_eyebrow = {
        **element(
            "header.eyebrow", "supporting", "검증 기준", y=0, height=22, clipped=True
        ),
        "glyph_clip_cause": "ancestor",
        "offset_parent_qid": "header.band",
        "computed_style": {"transform": "none"},
        "text_fragments": [{"x": 72, "y": -1, "width": 64, "height": 24}],
        "static_translation_probe": {
            "layoutIsolation": True,
            "stackingStable": True,
            "transformComposable": True,
            "safeTranslationIntervalPx": {"minY": 0, "maxY": 698},
            "siblingBoundsPx": [
                {"x": 72, "y": 30, "width": 976, "height": 45},
                {"x": 72, "y": 126, "width": 1136, "height": 1},
            ],
        },
    }
    title = element("header.title", "title", "Title", y=30, height=45, tag="h1")
    before_metrics = {"elements": [band, before_eyebrow, title]}
    after_eyebrow = {
        **before_eyebrow,
        "bbox": {"x": 72, "y": 1, "width": 1136, "height": 22},
        "clipped": False,
        "glyph_clipped": False,
        "line_box_clipped": False,
        "text_fragments": [{"x": 72, "y": 0, "width": 64, "height": 24}],
    }
    after_metrics = {"elements": [band, after_eyebrow, title]}
    captures = []

    async def fake_capture(patched_html):
        admit_authored_html(patched_html)
        captures.append(patched_html)
        return SimpleNamespace(png=b"eyebrow-repair", browser_metrics=after_metrics)

    monkeypatch.setattr(
        "services.authored_presentation_generation.render_html_to_png_with_metrics",
        fake_capture,
    )
    capacity = SlideCapacity(1, "PROBLEM", 64, 310, 400, 0, 2)
    repaired = asyncio.run(
        _apply_bounded_header_eyebrow_fallback(
            html=html,
            browser_metrics=before_metrics,
            capacity=capacity,
            # Recovery does not persist the original generated contract. The
            # immutable HTML band and Chromium geometry are its durable proof.
            header_contract=None,
            role="PROBLEM",
            allow_immutable_checkpoint_geometry=True,
        )
    )

    assert repaired is not None
    repaired_html, repaired_png, repaired_metrics = repaired
    assert captures == [repaired_html]
    assert "authored-header-eyebrow-clip-repair-v1" in repaired_html
    assert "translateY(1px)" in repaired_html
    assert '>검증 기준</div>' in repaired_html
    assert 'data-semantic-role="supporting"' in repaired_html
    assert repaired_png == b"eyebrow-repair"
    assert capacity_violations(repaired_html, repaired_metrics, capacity) == ()


def test_final_checkpoint_header_fallback_attempts_only_once(monkeypatch):
    html = (
        "<html><head></head><body>"
        '<div data-header-part="band" data-header-band="127" '
        'data-qid="header.band" data-semantic-role="decoration">'
        '<div data-header-part="eyebrow" data-qid="header.eyebrow" '
        'data-semantic-role="supporting">검증 기준</div></div>'
        '<h1 data-qid="title" data-semantic-role="title">Title</h1>'
        '<p data-qid="body" data-semantic-role="body">Body</p>'
        "</body></html>"
    )

    def metric(qid, role, text, *, clipped=False):
        return {
            "qid": qid,
            "tag": "div",
            "semantic_role": role,
            "rendered_text": text,
            "has_owned_text": bool(text),
            "font_size": 16,
            "font_loaded": True,
            "css_visible": True,
            "bbox": {"x": 72, "y": 20, "width": 300, "height": 24},
            "clipped": clipped,
            "glyph_clipped": clipped,
            "line_box_clipped": clipped,
        }

    metrics = {
        "elements": [
            metric("header.band", "decoration", ""),
            metric("header.eyebrow", "supporting", "검증 기준", clipped=True),
            metric("title", "title", "Title"),
            metric("body", "body", "Body"),
        ]
    }
    capacities = AuthoredCapacityPlan(
        profile=CompositionProfile("ordinary", "authored", True, True, 1),
        slides=tuple(
            SlideCapacity(index, "PROBLEM", 64, 310, 400, 0, 10, 12)
            for index in range(2)
        ),
    )
    records = tuple(
        SimpleNamespace(
            html=html,
            browser_metrics=metrics,
            capture_policy="hydrated",
        )
        for _ in range(2)
    )
    pipeline = SimpleNamespace(
        evidence_attempt_id="attempt",
        evidence_ledger=SimpleNamespace(
            latest=lambda _attempt, index: records[index]
        ),
    )
    calls = []

    async def fake_fallback(**kwargs):
        calls.append(kwargs)
        return kwargs["html"].replace(
            "</head>", "<style>/* repaired */</style></head>"
        ), b"repaired", metrics

    monkeypatch.setattr(
        "services.authored_presentation_generation._apply_bounded_header_eyebrow_fallback",
        fake_fallback,
    )
    htmls = [html, html]
    pngs = [b"original-0", b"original-1"]
    overrides = {}
    attempted, repaired_index = asyncio.run(
        _apply_final_checkpoint_header_eyebrow_fallback(
            pipeline=pipeline,
            htmls=htmls,
            pngs=pngs,
            capacity_plan=capacities,
            header_contract=None,
            roles=["PROBLEM", "PROBLEM"],
            metrics_override=overrides,
            already_attempted=False,
        )
    )
    repeated = asyncio.run(
        _apply_final_checkpoint_header_eyebrow_fallback(
            pipeline=pipeline,
            htmls=htmls,
            pngs=pngs,
            capacity_plan=capacities,
            header_contract=None,
            roles=["PROBLEM", "PROBLEM"],
            metrics_override=overrides,
            already_attempted=attempted,
        )
    )

    assert (attempted, repaired_index) == (True, 0)
    assert repeated == (True, None)
    assert len(calls) == 1
    assert calls[0]["allow_immutable_checkpoint_geometry"] is True
    assert "/* repaired */" in htmls[0]
    assert htmls[1] == html
    assert pngs == [b"repaired", b"original-1"]
    assert overrides == {0: metrics}


def test_final_capacity_violation_is_terminal_after_bounded_repair():
    html = (
        '<h1 data-qid="title" data-semantic-role="title">Title</h1>'
        '<p data-qid="body" data-semantic-role="body">Body</p>'
    )

    def metric(qid, role, text):
        return {
            "qid": qid,
            "tag": "h1" if role == "title" else "p",
            "semantic_role": role,
            "rendered_text": text,
            "has_owned_text": True,
            "font_size": 18,
            "font_loaded": True,
            "css_visible": True,
            "bbox": {"x": 10, "y": 10, "width": 100, "height": 24},
            "clipped": False,
            "glyph_clipped": False,
            "line_box_clipped": False,
        }

    profile = CompositionProfile("ordinary", "authored", True, True, 1)
    plan = AuthoredCapacityPlan(
        profile=profile,
        slides=(SlideCapacity(0, "PROBLEM", 20, 3, 20, 0, 2),),
    )

    with pytest.raises(AuthoredStageError) as caught:
        asyncio.run(
            _final_authored_quality_gate(
                htmls=[html],
                capacity_plan=plan,
                profile=profile,
                reference_mode="style-reference",
                browser_metrics_by_slide=[
                    {
                        "elements": [
                            metric("title", "title", "Title"),
                            metric("body", "body", "Body"),
                        ]
                    }
                ],
            )
        )

    assert caught.value.code == "authored_slide_capacity_exceeded"
    assert caught.value.retryable is False
