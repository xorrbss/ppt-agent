from hashlib import sha256
from types import MappingProxyType, SimpleNamespace

import pytest

from models.generate_presentation_request import GeneratePresentationRequest
from services.authored_composition_policy import (
    CompositionProfile,
    SlideCapacity,
    _is_supporting_candidate,
    aggregate_owner_capacity_violations,
    build_authored_capacity_plan,
    canonicalize_authored_capacity_html,
    capacity_violations,
    count_content_images,
    ensure_authored_semantic_roles,
    repair_authored_layout_alignment,
    reduce_authored_closing_title_punctuation,
    reduce_authored_owner_text_capacity,
    resolve_composition_profile,
    semantic_repair_boundary_violations,
)
from utils.slide_capture_result import RenderCapture


def _capture(html, png, metrics):
    return RenderCapture.from_payload(
        html_digest="sha256:" + sha256(html.encode()).hexdigest(),
        png=png, browser_metrics=metrics, runtime_counters={}, prepared_html=html,
    )


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


def test_capacity_prompt_states_aggregate_owner_budgets() -> None:
    capacity = SlideCapacity(1, "PROBLEM", 64, 310, 400, 1, 28, 16)
    brief = capacity.prompt_brief(resolve_composition_profile(SimpleNamespace()))

    assert "combined text across all title owners <= 64 chars" in brief
    assert "combined text across all body owners <= 310 chars" in brief
    assert "do not repeat the same words in nested owners" in brief
    assert "title, body, and supporting owners <= 400 chars" in brief


def test_aggregate_owner_capacity_rejects_before_browser_without_double_counting() -> None:
    html = (
        "<html><head></head><body>"
        '<h1 data-qid="header.title" data-semantic-role="title">Title</h1>'
        '<div data-qid="body.wrapper" data-semantic-role="body">'
        '<p data-qid="body.item1" data-semantic-role="body">123456</p>'
        '<p data-qid="body.item2" data-semantic-role="body">789012</p>'
        "</div></body></html>"
    )
    capacity = SlideCapacity(0, "PROBLEM", 10, 10, 30, 0, 3)

    assert aggregate_owner_capacity_violations(html, capacity) == (
        "owner_body:12>10",
    )


def test_semantic_repair_boundary_preserves_qids_roles_and_source_owners() -> None:
    original = (
        "<html><head></head><body>"
        '<h1 data-qid="header.title" data-header-part="title" '
        'data-semantic-role="title">Protected title</h1>'
        '<p data-qid="body.item1" data-semantic-role="body">Long supporting detail</p>'
        '<p data-qid="source.copy" data-source-block-id="source:1" '
        'data-semantic-role="body">Verbatim source</p>'
        "</body></html>"
    )
    safe = original.replace("Long supporting detail", "Short detail")
    assert semantic_repair_boundary_violations(original, safe) == ()

    renamed = safe.replace('data-qid="body.item1"', 'data-qid="body.renamed"')
    assert "semantic_qid_added" in semantic_repair_boundary_violations(
        original, renamed
    )
    title_changed = safe.replace("Protected title", "Changed title")
    assert "semantic_text_rewrite_forbidden:header.title" in (
        semantic_repair_boundary_violations(original, title_changed)
    )
    source_changed = safe.replace("Verbatim source", "Changed source")
    assert "semantic_text_rewrite_forbidden:source.copy" in (
        semantic_repair_boundary_violations(original, source_changed)
    )


def test_semantic_repair_boundary_narrowly_allows_one_primary_title_shortening() -> None:
    original = (
        "<html><head></head><body>"
        '<h1 data-qid="slide.title" data-semantic-role="title">'
        "A deliberately long primary title</h1>"
        '<p data-qid="body.item1" data-semantic-role="body">Body</p>'
        "</body></html>"
    )
    shortened = original.replace(
        "A deliberately long primary title", "Short primary title"
    )
    assert "semantic_text_rewrite_forbidden:slide.title" in (
        semantic_repair_boundary_violations(original, shortened)
    )
    assert semantic_repair_boundary_violations(
        original,
        shortened,
        allow_primary_title_shortening=True,
    ) == ()

    header_original = original.replace(
        'data-qid="slide.title"',
        'data-qid="header.title" data-header-part="title"',
    )
    header_shortened = header_original.replace(
        "A deliberately long primary title", "Short primary title"
    )
    assert "semantic_text_rewrite_forbidden:header.title" in (
        semantic_repair_boundary_violations(
            header_original,
            header_shortened,
            allow_primary_title_shortening=True,
        )
    )

    source_original = original.replace(
        'data-qid="body.item1"',
        'data-qid="body.item1" data-source-block-id="source:1"',
    )
    source_shortened = source_original.replace(
        "A deliberately long primary title", "Short primary title"
    )
    assert "semantic_text_rewrite_forbidden:slide.title" in (
        semantic_repair_boundary_violations(
            source_original,
            source_shortened,
            allow_primary_title_shortening=True,
        )
    )

    # Two title owners cannot survive the stamper any more: the ``h2`` is filed
    # ``supporting``. It stays a heading, so its text is still immutable and the
    # allowance covers only the one remaining primary title.
    two_titles = original.replace(
        '<p data-qid="body.item1" data-semantic-role="body">Body</p>',
        '<h2 data-qid="slide.subtitle" data-semantic-role="title">Second long title</h2>',
    )
    two_shortened = two_titles.replace(
        "A deliberately long primary title", "Short title"
    ).replace("Second long title", "Second")
    assert "semantic_text_rewrite_forbidden:slide.subtitle" in (
        semantic_repair_boundary_violations(
            two_titles,
            two_shortened,
            allow_primary_title_shortening=True,
        )
    )


def test_dense_proposal_roles_admit_short_editable_label_systems(monkeypatch) -> None:
    monkeypatch.setenv("AUTHORED_COMPOSITION_ENVELOPE_ENABLED", "true")
    profile = CompositionProfile("ordinary", "authored", True, True, 1)
    plan = build_authored_capacity_plan(
        ["architecture", "pillars", "closing", "generic"],
        ["ARCHITECTURE", "PILLARS", "CLOSING", "CONTENT"],
        profile,
    )

    assert (
        plan.at(0).body_max_chars,
        plan.at(0).max_visible_text_chars,
        plan.at(0).max_qids,
    ) == (390, 460, 48)
    assert (
        plan.at(1).body_max_chars,
        plan.at(1).max_visible_text_chars,
        plan.at(1).max_qids,
    ) == (380, 460, 40)
    assert (
        plan.at(2).body_max_chars,
        plan.at(2).max_visible_text_chars,
        plan.at(2).max_qids,
    ) == (380, 460, 36)
    assert (
        plan.at(3).body_max_chars,
        plan.at(3).max_visible_text_chars,
        plan.at(3).max_qids,
    ) == (380, 460, 40)
    assert all(slide.min_font_px == 15 for slide in plan.slides)


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


def test_default_capacity_accepts_the_declared_15px_authored_floor():
    capacity = SlideCapacity(0, "PROBLEM", 20, 20, 20, 0, 1)
    html = '<p data-qid="body" data-semantic-role="body">Body</p>'

    def metrics(font_size):
        return {
            "elements": [
                {
                    "qid": "body",
                    "tag": "p",
                    "semantic_role": "body",
                    "rendered_text": "Body",
                    "has_owned_text": True,
                    "font_size": font_size,
                    "font_loaded": True,
                    "css_visible": True,
                    "bbox": {"x": 10, "y": 10, "width": 80, "height": 20},
                    "clipped": False,
                    "glyph_clipped": False,
                    "line_box_clipped": False,
                }
            ]
        }

    assert capacity_violations(html, metrics(15), capacity) == ()
    assert "font:14<15" in capacity_violations(html, metrics(14), capacity)


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


def test_layout_alignment_repair_removes_nested_duplicate_body_offset() -> None:
    html = """<!doctype html><html><head><style>
    .body-wrap{position:absolute;left:56px;right:56px;top:116px;height:518px}
    </style></head><body>
    <main data-header-part="body" style="position:absolute;top:116px;left:0;right:0;height:518px;">
      <div data-qid="body.layout" class="body-wrap">본문</div>
    </main></body></html>"""

    repaired = repair_authored_layout_alignment(html, role="ROADMAP")

    assert "authored-layout-alignment-repair-v1:nested-body-origin" in repaired
    assert (
        ".body-wrap{position:absolute;left:56px;right:56px;top:0px;height:518px}"
        in repaired
    )
    assert repair_authored_layout_alignment(repaired, role="ROADMAP") == repaired


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


def _library_icon_slot(node: int) -> str:
    """One archetype-library node icon exactly as the asset layer emits it."""
    return (
        f'<div class="ig-ic" data-qid="ig.s1.n{node}.ic">'
        '<img src="data:image/png;base64,AA" alt=""></div>'
    )


def test_library_node_icons_do_not_spend_the_slide_image_budget():
    """A four-node figure carries four icons and still claims no content image.

    The icons are decoration the model never wrote and never declares, so they
    are absent from the Chromium payload. Counting them would both spend the
    envelope and make the HTML total disagree with the measured one, which is
    itself a violation — that pairing failed a real five-slide deck with
    "images:4>1" and "browser_images:0!=4".
    """
    icons = "".join(_library_icon_slot(index) for index in range(4))
    html = f"<body><section class='ig ig-cycle n4'>{icons}</section></body>"

    assert count_content_images(html) == 0
    with_illustration = html.replace(
        "</body>", '<img data-illust-prompt="a press line"></body>'
    )
    assert count_content_images(with_illustration) == 1


def test_the_ordinary_deck_no_longer_stops_at_one_image_slot(monkeypatch):
    """The gate admits a figure's icons; the author is still offered one slot."""
    monkeypatch.setenv("AUTHORED_COMPOSITION_ENVELOPE_ENABLED", "true")
    profile = CompositionProfile("ordinary", "authored", True, True, 1)
    capacity = build_authored_capacity_plan(("# 슬라이드",), ("CONTENT",), profile).at(0)

    assert capacity.max_images == 8
    assert "Reserve up to 1 visible image slot(s)" in capacity.prompt_brief(profile)


def test_owner_text_reducer_preserves_nested_inline_markup() -> None:
    title = "Nested markup capacity"
    body_one = ("alpha " * 29) + "alpha"
    body_two = ("beta " * 29) + "beta"
    original = (
        "<html><head></head><body>"
        f'<h1 data-qid="slide.title" data-semantic-role="title">{title}</h1>'
        '<p data-qid="content.primary" data-semantic-role="body">'
        f'<span class="lead">{body_one}</span></p>'
        '<p data-qid="content.secondary" data-semantic-role="body">'
        f'<strong>{body_two}</strong></p>'
        "</body></html>"
    )
    capacity = SlideCapacity(2, "PILLARS", 64, 128, 150, 0, 30)

    reduced = reduce_authored_owner_text_capacity(
        original,
        capacity,
        profile_id="ordinary",
        allow_summarization=True,
    )

    assert reduced != original
    assert aggregate_owner_capacity_violations(reduced, capacity) == ()
    canonical_original = canonicalize_authored_capacity_html(original)
    assert semantic_repair_boundary_violations(
        canonical_original,
        reduced,
        allow_bounded_body_shortening=True,
    ) == ()
    assert '<span class="lead"' in reduced
    assert "<strong " in reduced
