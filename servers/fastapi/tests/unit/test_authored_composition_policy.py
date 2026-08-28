from services.authored_composition_policy import (
    SlideCapacity,
    capacity_violations,
    ensure_authored_semantic_roles,
)


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
            {"qid": "a", "tag": "div", "rendered_text": "12345678901", "font_size": 12},
            {"qid": "b", "tag": "div", "rendered_text": "x", "font_size": 20},
        ],
    }

    violations = capacity_violations(html, metrics, capacity)

    assert "qids:2>1" in violations
    assert "images:2>1" in violations
    assert "visible_text:13>10" in violations
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


def test_semantic_roles_are_stamped_before_owned_text_browser_capacity_gate():
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

    elements = [
        metric("slide.body", "body", "body", "Title Body", False),
        metric("header.title", "h1", "title", "Title", True),
        {**metric("meta.label", "span", "supporting", "Label", True), "font_size": 15},
        {**metric("chart.scale.zero", "span", "supporting", "0", True), "font_size": 13},
        metric("copy", "p", "body", "Body", True),
        {"qid": "line.break", "tag": "br", "semantic_role": "body", "has_owned_text": False},
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
