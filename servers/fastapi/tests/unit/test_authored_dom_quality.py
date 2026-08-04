from utils.authored_dom_quality import (
    EXACT_RECONSTRUCTION,
    STYLE_REFERENCE,
    DomQualityIssue,
    DomQualityReport,
    build_compact_dom_map,
    ensure_authored_qids,
    has_dom_quality_regression,
    inspect_authored_dom,
    is_valid_authored_qid,
    normalize_browser_metrics,
)

HTML = """<!doctype html><html><body><h1 id="title">Results 42%</h1><p data-qid="body-copy">Revenue → ₩10</p><img id="logo" src="logo.png"></body></html>"""


def test_qid_migration_is_deterministic_idempotent_and_preserves_existing_identifiers():
    first = ensure_authored_qids(HTML)
    second = ensure_authored_qids(first)

    assert first == second
    assert 'id="title"' in first
    assert 'id="logo"' in first
    assert 'data-qid="body-copy"' in first
    assert first.count("data-qid=") == 2


def test_qid_migration_makes_duplicate_existing_qids_unique_without_touching_html_id():
    enriched = ensure_authored_qids(
        '<html><body><p id="one" data-qid="same">one</p><p id="two" data-qid="same">two</p></body></html>'
    )

    assert 'id="one" data-qid="same"' in enriched
    assert 'id="two"' in enriched
    assert enriched.count('data-qid="same"') == 1
    assert enriched.count("data-qid=") == 2


def test_qid_migration_replaces_invalid_duplicate_and_missing_qids_with_patchable_tokens():
    source = '<div data-qid="bad qid">one</div><p data-qid="same">two</p><p data-qid="same">three</p><img>'

    migrated = ensure_authored_qids(source)
    qids = [node.qid for node in build_compact_dom_map(migrated)]

    assert migrated == ensure_authored_qids(migrated)
    assert len(qids) == len(set(qids)) == 3
    assert all(is_valid_authored_qid(qid) for qid in qids)


def test_qid_migration_replaces_blank_attribute_without_creating_a_duplicate():
    source = (
        '<div data-qid="   ">one</div><p data-qid="">two</p><span data-qid>three</span>'
    )

    migrated = ensure_authored_qids(source)

    assert migrated == ensure_authored_qids(migrated)
    assert migrated.count("data-qid=") == 3
    assert len(build_compact_dom_map(migrated)) == 3


def test_compact_dom_map_is_bounded_and_uses_browser_bbox_and_selected_style_only():
    html = ensure_authored_qids(HTML)
    title_qid = build_compact_dom_map(html)[0].qid
    nodes = build_compact_dom_map(
        html,
        {
            "elements": [
                {
                    "qid": title_qid,
                    "bbox": {"x": 10, "y": 20, "width": 300, "height": 40},
                    "text": "Results 42%",
                    "computed_style": {
                        "fontSize": "30px",
                        "color": "#111",
                        "unsafe": "not exposed",
                    },
                }
            ]
        },
    )

    title = next(node for node in nodes if node.qid == title_qid)
    assert title.bbox and title.bbox.width == 300
    assert title.style == {"fontSize": "30px", "color": "#111"}
    assert "unsafe" not in title.style


def test_normalize_browser_metrics_accepts_browser_camel_case_contract():
    normalized = normalize_browser_metrics(
        {
            "width": 1280,
            "height": 720,
            "document_fonts_ready": False,
            "elements": [
                {
                    "qid": "q-one",
                    "rect": {"left": 1, "top": 2, "width": 3, "height": 4},
                    "scrollWidth": 11,
                    "clientWidth": 10,
                }
            ],
        }
    )

    assert normalized["fonts_ready"] is False
    assert normalized["elements"][0]["bbox"] == {
        "x": 1.0,
        "y": 2.0,
        "width": 3.0,
        "height": 4.0,
    }
    assert normalized["elements"][0]["scroll_width"] == 11


def test_unavailable_or_incomplete_telemetry_is_not_a_clean_dom_pass():
    html = ensure_authored_qids(
        "<html><body><p>Needs browser coverage</p></body></html>"
    )

    unavailable = inspect_authored_dom(html, None)
    incomplete = inspect_authored_dom(html, {"elements": []})

    assert unavailable.telemetry_status == "unavailable"
    assert incomplete.telemetry_status == "incomplete"
    assert unavailable.needs_attention
    assert incomplete.needs_attention


def test_present_empty_collector_payload_is_available_for_a_truly_empty_slide():
    report = inspect_authored_dom("<html><body></body></html>", {"elements": []})

    assert report.telemetry_status == "available"
    assert not report.needs_attention


def test_non_editable_qid_telemetry_does_not_hide_complete_editable_coverage():
    html = (
        '<html><body><p data-qid="body">Covered</p>'
        '<br data-qid="legacy-break"></body></html>'
    )
    report = inspect_authored_dom(
        html,
        {
            "elements": [
                {
                    "qid": "body",
                    "bbox": {"x": 10, "y": 10, "width": 100, "height": 20},
                    "client_width": 100,
                    "client_height": 20,
                    "scroll_width": 100,
                    "scroll_height": 20,
                    "text": "Covered",
                },
                {
                    "qid": "legacy-break",
                    "bbox": {"x": 0, "y": 0, "width": 0, "height": 0},
                },
            ]
        },
    )

    assert report.telemetry_status == "available"


def test_local_inspector_classifies_fast_browser_signals_and_content_tokens():
    html = ensure_authored_qids(HTML)
    nodes = build_compact_dom_map(html)
    title, body = nodes
    report = inspect_authored_dom(
        html,
        {
            "width": 1280,
            "height": 720,
            "fonts_ready": False,
            "elements": [
                {
                    "qid": title.qid,
                    "tag": "h1",
                    "bbox": {"x": -2, "y": 1, "width": 300, "height": 30},
                    "scrollWidth": 150,
                    "clientWidth": 100,
                    "rendered_text": "Results",
                    "computed_style": {"color": "#777", "backgroundColor": "#888"},
                },
                {
                    "qid": body.qid,
                    "tag": "p",
                    "bbox": {"x": 20, "y": 20, "width": 100, "height": 30},
                    "clipped": True,
                    "font_loaded": False,
                    "rendered_text": "Revenue",
                    "computed_style": {"color": "#111", "backgroundColor": "#fff"},
                },
            ],
        },
    )

    categories = {issue.category for issue in report.issues}
    assert {
        "font",
        "overflow",
        "off_canvas",
        "content",
        "contrast",
        "clipping",
        "overlap",
    } <= categories
    assert report.needs_attention


def test_visible_static_text_scroll_overhang_is_not_treated_as_clipping():
    html = '<h1 data-qid="title">Rendered title</h1>'
    base = {
        "qid": "title",
        "tag": "h1",
        "bbox": {"x": 40, "y": 40, "width": 400, "height": 56},
        "clientWidth": 400,
        "clientHeight": 56,
        "scrollWidth": 400,
        "scrollHeight": 62,
        "rendered_text": "Rendered title",
        "computed_style": {
            "position": "static",
            "overflow": "visible",
            "overflowX": "visible",
            "overflowY": "visible",
        },
    }

    visible_flow = inspect_authored_dom(html, {"elements": [base]})
    positioned_box = inspect_authored_dom(
        html,
        {
            "elements": [
                {
                    **base,
                    "computed_style": {**base["computed_style"], "position": "absolute"},
                }
            ]
        },
    )

    assert not any(issue.category == "overflow" for issue in visible_flow.issues)
    assert any(issue.category == "overflow" for issue in positioned_box.issues)


def test_reference_mode_keeps_explicitly_intentional_geometry_out_of_local_defects():
    html = ensure_authored_qids(
        "<html><body><div>art</div><div>label</div></body></html>"
    )
    first, second = build_compact_dom_map(html)
    metrics = {
        "elements": [
            {
                "qid": first.qid,
                "bbox": {"x": -20, "y": 0, "width": 100, "height": 100},
                "intentional": True,
            },
            {
                "qid": second.qid,
                "bbox": {"x": 0, "y": 0, "width": 100, "height": 100},
                "intentional": True,
            },
        ]
    }

    exact = inspect_authored_dom(html, metrics, reference_mode=EXACT_RECONSTRUCTION)
    style = inspect_authored_dom(html, metrics, reference_mode=STYLE_REFERENCE)

    assert not {issue.category for issue in exact.issues} & {
        "off_canvas",
        "overlap",
        "clipping",
    }
    assert not {issue.category for issue in style.issues} & {
        "off_canvas",
        "overlap",
        "clipping",
    }
    assert exact.reference_mode == EXACT_RECONSTRUCTION


def test_nested_parent_and_child_boxes_are_not_reported_as_colliding():
    html = ensure_authored_qids(
        "<html><body><section><h1>Nested title</h1></section></body></html>"
    )
    section, title = build_compact_dom_map(html)
    report = inspect_authored_dom(
        html,
        {
            "elements": [
                {
                    "qid": section.qid,
                    "bbox": {"x": 0, "y": 0, "width": 500, "height": 300},
                },
                {
                    "qid": title.qid,
                    "parentQid": section.qid,
                    "bbox": {"x": 20, "y": 20, "width": 300, "height": 60},
                },
            ]
        },
    )

    assert not any(issue.category == "overlap" for issue in report.issues)


def test_nested_collision_is_coalesced_to_visible_sibling_layout_branches():
    html = (
        '<main data-qid="platform"><div data-qid="flow">'
        '<div data-qid="card">Card 4</div></div>'
        '<footer data-qid="footer"><span data-qid="ready">READY</span></footer>'
        '</main>'
    )
    report = inspect_authored_dom(
        html,
        {
            "elements": [
                {"qid": "platform", "bbox": {"x": 0, "y": 0, "width": 340, "height": 530}, "rendered_text": "Card 4 READY"},
                {"qid": "flow", "parent_qid": "platform", "bbox": {"x": 20, "y": 300, "width": 280, "height": 240}, "rendered_text": "Card 4"},
                {"qid": "card", "parent_qid": "flow", "bbox": {"x": 20, "y": 450, "width": 280, "height": 90}, "rendered_text": "Card 4"},
                {"qid": "footer", "parent_qid": "platform", "bbox": {"x": 20, "y": 480, "width": 280, "height": 45}, "rendered_text": "READY"},
                {"qid": "ready", "parent_qid": "footer", "bbox": {"x": 240, "y": 490, "width": 50, "height": 20}, "rendered_text": "READY"},
            ]
        },
    )

    overlaps = [issue for issue in report.issues if issue.category == "overlap"]
    assert [issue.qids for issue in overlaps] == [("flow", "footer")]


def test_empty_absolute_decoration_overlap_and_parent_clipped_offcanvas_are_ignored():
    html = (
        '<aside data-qid="aside"><div data-qid="glow"></div>'
        '<section data-qid="content">Visible content</section></aside>'
    )
    report = inspect_authored_dom(
        html,
        {
            "width": 1280,
            "height": 720,
            "elements": [
                {
                    "qid": "aside",
                    "bbox": {"x": 650, "y": 0, "width": 630, "height": 720},
                    "rendered_text": "Visible content",
                    "computed_style": {"position": "relative", "overflow": "hidden"},
                },
                {
                    "qid": "glow",
                    "parent_qid": "aside",
                    "bbox": {"x": 900, "y": 500, "width": 500, "height": 500},
                    "rendered_text": "",
                    "computed_style": {"position": "absolute"},
                },
                {
                    "qid": "content",
                    "parent_qid": "aside",
                    "bbox": {"x": 880, "y": 100, "width": 340, "height": 530},
                    "rendered_text": "Visible content",
                    "computed_style": {"position": "absolute"},
                },
            ],
        },
    )

    assert not any(
        issue.category in {"overlap", "off_canvas"} and "glow" in issue.qids
        for issue in report.issues
    )


def test_exact_geometry_is_deferred_to_source_vqa_unless_explicitly_unintentional():
    html = ensure_authored_qids("<html><body><div>source geometry</div></body></html>")
    node = build_compact_dom_map(html)[0]
    metric = {
        "qid": node.qid,
        "bbox": {"x": -20, "y": 0, "width": 200, "height": 100},
        "clipped": True,
    }

    exact = inspect_authored_dom(
        html, {"elements": [metric]}, reference_mode=EXACT_RECONSTRUCTION
    )
    style = inspect_authored_dom(
        html, {"elements": [metric]}, reference_mode=STYLE_REFERENCE
    )
    explicit = inspect_authored_dom(
        html,
        {"elements": [{**metric, "unintentional": True}]},
        reference_mode=EXACT_RECONSTRUCTION,
    )

    assert not {issue.category for issue in exact.issues} & {
        "off_canvas",
        "clipping",
    }
    assert {"off_canvas", "clipping"} <= {issue.category for issue in style.issues}
    assert {"off_canvas", "clipping"} <= {issue.category for issue in explicit.issues}


def test_transparent_background_does_not_create_false_contrast_failure():
    html = ensure_authored_qids("<html><body><p>Readable</p></body></html>")
    node = build_compact_dom_map(html)[0]
    report = inspect_authored_dom(
        html,
        {
            "elements": [
                {
                    "qid": node.qid,
                    "bbox": {"x": 0, "y": 0, "width": 100, "height": 20},
                    "computed_style": {
                        "color": "rgb(0, 0, 0)",
                        "backgroundColor": "rgba(0, 0, 0, 0)",
                    },
                }
            ]
        },
    )

    assert not any(issue.category == "contrast" for issue in report.issues)


def test_regression_check_rejects_new_issue_even_when_a_high_issue_was_resolved():
    baseline = DomQualityReport(
        STYLE_REFERENCE,
        (DomQualityIssue("image", "high", "broken", ("hero",)),),
        (),
        1,
    )
    candidate = DomQualityReport(
        STYLE_REFERENCE,
        (DomQualityIssue("contrast", "medium", "low contrast", ("caption",)),),
        (),
        1,
    )
    improved = DomQualityReport(STYLE_REFERENCE, (), (), 1)

    assert has_dom_quality_regression(baseline, candidate)
    assert not has_dom_quality_regression(baseline, improved)


def test_regression_check_rejects_candidate_without_complete_telemetry():
    baseline = DomQualityReport(
        STYLE_REFERENCE, (), (), 0, telemetry_status="unavailable"
    )
    unavailable_candidate = DomQualityReport(
        STYLE_REFERENCE, (), (), 0, telemetry_status="unavailable"
    )
    available_candidate = DomQualityReport(
        STYLE_REFERENCE, (), (), 0, telemetry_status="available"
    )

    assert has_dom_quality_regression(baseline, unavailable_candidate)
    assert not has_dom_quality_regression(baseline, available_candidate)
