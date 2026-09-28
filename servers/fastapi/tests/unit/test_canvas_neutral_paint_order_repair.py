from hashlib import sha256

from services.authored_composition_policy import repair_authored_owned_text_paint_order


def _paint_order_fixture(width: int, height: int):
    """Same eligible paint-order candidate as the 1280x720 coverage in
    test_authored_composition_policy.py, parameterised only by declared canvas
    size (the geometry itself is canvas-agnostic for this repair)."""

    source = '''<!doctype html><html><head><style>
    .panel{position:relative;display:flex;flex-direction:column;width:400px;height:300px}
    .note{margin-top:200px;height:80px}.shape{position:absolute;width:200px;height:80px;
      right:-20px;bottom:0;border:2px solid blue;transform:rotate(-8deg)}
    </style></head><body><section class="panel" data-qid="panel" data-semantic-role="supporting">
    <div class="note" data-qid="note" data-semantic-role="supporting">Preserve every exported element.</div>
    <div class="shape" data-qid="shape" data-semantic-role="supporting"></div>
    </section></body></html>'''
    parent_box = {"x": 0, "y": 0, "width": 400, "height": 300}
    text_box = {"x": 20, "y": 200, "width": 360, "height": 80}
    render_digest = "sha256:" + "1" * 64

    def metric(qid, tag, bounds, *, position, display="block", owned_text=""):
        return {
            "qid": qid, "tag": tag, "bbox": bounds,
            "parent_qid": "panel" if qid != "panel" else None,
            "offset_parent_qid": "panel", "source_block_id": None,
            "semantic_role": "supporting", "owned_text": owned_text,
            "rendered_text": owned_text, "has_owned_text": bool(owned_text),
            "has_owned_paint": True, "css_visible": True, "effective_opacity": 1,
            "intentional": False, "effective_z_index": 1 if qid == "note" else 2,
            "clipped": False, "glyph_clipped": False, "line_box_clipped": False,
            "measurement": {
                key: True for key in
                ("geometry", "stacking", "transform", "visual_effects", "occlusion", "clipping")
            },
            "computed_style": {
                "position": position, "display": display, "zIndex": "auto",
                "opacity": "1", "visibility": "visible", "backgroundBlendMode": "normal",
                "backgroundImage": "none", "transform": "none",
                **{edge: "auto" for edge in ("top", "right", "bottom", "left")},
            },
            "visual_effects": {
                "filter": "none", "backdropFilter": "none", "clipPath": "none",
                "maskImage": "none", "mixBlendMode": "normal", "transform": "none",
            },
            "containing_block_proof": {
                "source": "chrome", "telemetryComplete": True, "exactBoundOffsetParent": True,
                "offsetParentQid": "panel", "offsetParentTag": "section",
                "offsetParentBoundsPx": parent_box,
            },
            "text_fragments": [],
        }

    parent = metric("panel", "section", parent_box, position="relative", display="flex")
    target = metric("note", "div", text_box, position="static", owned_text="Preserve every exported element.")
    target.update({
        "continuous_occluded_fraction": 0.14, "occluded_fraction": 0.15,
        "occlusion_unknown_fraction": 0, "occlusion_unbound_count": 0,
        "occlusion_measurement_error": None, "occlusion_occluder_qids": ["shape"],
        "occlusion_known_sample_count": 100, "occlusion_sample_count": 100,
        "occlusion_confirmed_covered_sample_count": 15,
        "occlusion_probe_version": "owned-paint-v3", "occlusion_sensor_version": "owned-paint-v5-inset-retry",
        "occlusion_measurement_evidence": {
            "status": "measured", "stage": "chrome.owned_paint_sampling", "metric": "occlusion", "qid": "note",
            "reason": None, "render_digest": render_digest, "source_digest": render_digest,
            "known": 100, "covered": 15, "sampled": 100, "known_occluded_fraction": 0.15,
            "sensor_version": "owned-paint-v5-inset-retry",
        },
        "static_translation_probe": {
            "source": "chrome", "telemetryComplete": True, "layoutIsolation": True,
            "stackingStable": True, "transformComposable": True, "unsupportedReason": None,
            "flowImpactPx": 0, "maxSiblingDeltaPx": 0, "translationErrorPx": 0,
            "targetBoundsPx": text_box, "parentBoundsPx": parent_box,
        },
        "text_fragments": [{"x": 20, "y": 230, "width": 330, "height": 20}],
    })
    shape = metric("shape", "div", {"x": 220, "y": 220, "width": 200, "height": 80}, position="absolute")
    shape["computed_style"]["transform"] = "matrix(0.99, -0.14, 0.14, 0.99, 0, 0)"
    shape["measurement"]["occlusion"] = False
    shape["occlusion_measurement_evidence"] = {"status": "unknown"}
    metrics = {
        "dom_metrics_version": "authored-dom-metrics-v16-owned-text", "width": width, "height": height,
        "fonts_ready": True, "source_digest": "sha256:" + sha256(source.encode()).hexdigest(),
        "render_digest": render_digest, "elements": [parent, target, shape],
    }
    rule = (
        '<style>/* authored-owned-text-paint-order-v1 */'
        '[data-qid="panel"]>[data-qid="note"]{z-index:1!important}</style>'
    )
    return source, metrics, rule


def test_default_allow_list_unchanged_for_1280x720_and_720x1280():
    for width, height in ((1280, 720), (720, 1280)):
        source, metrics, rule = _paint_order_fixture(width, height)
        candidate = repair_authored_owned_text_paint_order(source, metrics)
        assert candidate.replace(rule, "", 1) == source
        assert candidate != source


def test_default_allow_list_still_rejects_non_16_9_geometry():
    source, metrics, _rule = _paint_order_fixture(1188, 840)
    assert repair_authored_owned_text_paint_order(source, metrics) == source


def test_canvas_size_none_matches_today_exactly():
    for width, height in ((1280, 720), (720, 1280), (1188, 840)):
        source, metrics, _rule = _paint_order_fixture(width, height)
        assert (
            repair_authored_owned_text_paint_order(source, metrics)
            == repair_authored_owned_text_paint_order(source, metrics, canvas_size=None)
        )


def test_explicit_canvas_size_applies_where_default_would_reject():
    source, metrics, rule = _paint_order_fixture(1188, 840)
    assert repair_authored_owned_text_paint_order(source, metrics) == source
    candidate = repair_authored_owned_text_paint_order(source, metrics, canvas_size=(1188, 840))
    assert candidate.replace(rule, "", 1) == source
    assert candidate != source


def test_explicit_canvas_size_mismatch_leaves_html_unchanged():
    source, metrics, _rule = _paint_order_fixture(1280, 720)
    assert repair_authored_owned_text_paint_order(source, metrics, canvas_size=(1188, 840)) == source
    source_a4, metrics_a4, _rule_a4 = _paint_order_fixture(1188, 840)
    assert repair_authored_owned_text_paint_order(source_a4, metrics_a4, canvas_size=(1280, 720)) == source_a4
