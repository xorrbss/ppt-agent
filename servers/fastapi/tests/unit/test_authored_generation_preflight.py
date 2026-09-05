from services.authored_composition_policy import SlideCapacity
from services.authored_generation_preflight import (
    AuthoredSlideArtifact,
    preflight_authored_provider_repair,
)


def _metric(qid, role, text, *, y, font=18, visible=True):
    return {
        "qid": qid,
        "tag": "h1" if role == "title" else "p",
        "semantic_role": role,
        "rendered_text": text,
        "has_owned_text": bool(text),
        "font_size": font,
        "font_loaded": True,
        "css_visible": visible,
        "bbox": {"x": 40, "y": y, "width": 500, "height": 28},
        "clipped": False,
        "glyph_clipped": False,
        "line_box_clipped": False,
    }


def _artifact(body="Body", *, cue=None):
    cue_html = (
        f'<p data-qid="closing.cue" data-semantic-role="body">{cue}</p>'
        if cue is not None
        else ""
    )
    html = (
        "<html><head></head><body>"
        '<h1 data-qid="title" data-semantic-role="title">Title</h1>'
        f'<p data-qid="body" data-semantic-role="body">{body}</p>'
        f"{cue_html}</body></html>"
    )
    elements = [
        _metric("title", "title", "Title", y=30),
        _metric("body", "body", body, y=120),
    ]
    if cue is not None:
        elements.append(_metric("closing.cue", "body", cue, y=200))
    return AuthoredSlideArtifact(
        html=html,
        png=(body + str(cue or "")).encode(),
        browser_metrics={
            "viewport": {"width": 1280, "height": 720},
            "elements": elements,
        },
    )


def test_preflight_blocks_unmeasured_font_qid_and_visibility_failures():
    bad = _artifact("Body")
    bad_metrics = dict(bad.browser_metrics)
    bad_elements = [dict(item) for item in bad_metrics["elements"]]
    bad_elements[1]["font_loaded"] = False
    bad_elements[1]["css_visible"] = False
    bad_metrics["elements"] = bad_elements
    terminal = preflight_authored_provider_repair(
        slide_index=4,
        artifact=AuthoredSlideArtifact(bad.html, bad.png, bad_metrics),
        capacity=SlideCapacity(4, "CONTENT", 20, 20, 40, 0, 4),
        profile_id="ordinary",
        reference_mode="style_reference",
    )
    assert terminal.terminal is True
    assert terminal.retryable is False
    assert terminal.provider_call_allowed is False
    # Both violations name the "body" element rather than carrying a
    # measurement, and an unloaded font / invisible element is not a DOM
    # safety category, so this falls through to the deterministic gate rather
    # than the capacity code.
    assert terminal.code == "authored_deterministic_preflight_failed"
    assert "browser_font_unverified:body" in terminal.violations
    assert "browser_text_not_visible:body" in terminal.violations

    missing_qid_metrics = dict(bad.browser_metrics)
    missing_qid_metrics["elements"] = list(missing_qid_metrics["elements"][:-1])
    qid_terminal = preflight_authored_provider_repair(
        slide_index=4,
        artifact=AuthoredSlideArtifact(bad.html, bad.png, missing_qid_metrics),
        capacity=SlideCapacity(4, "CONTENT", 20, 20, 40, 0, 4),
        profile_id="ordinary",
        reference_mode="style_reference",
    )
    assert "browser_qid_mismatch" in qid_terminal.violations


def test_preflight_is_the_provider_call_authority() -> None:
    clean = _artifact("Body")
    allowed = preflight_authored_provider_repair(
        slide_index=0,
        artifact=clean,
        capacity=SlideCapacity(0, "CONTENT", 20, 20, 40, 0, 4),
        profile_id="ordinary",
        reference_mode="style_reference",
    )
    assert allowed.provider_call_allowed is True

    closing = _artifact("B", cue="EXCESS")
    deterministic = preflight_authored_provider_repair(
        slide_index=1,
        artifact=closing,
        capacity=SlideCapacity(1, "CLOSING", 20, 1, 40, 0, 4),
        profile_id="ordinary",
        reference_mode="style_reference",
    )
    assert deterministic.classification == "deterministic_repair"
    assert deterministic.provider_call_allowed is False


def _restyle_artifact(*, decor_visible: bool):
    """A restyled slide whose ornament lands on top of a source block.

    Slide 16 of operation 3d0b9e72 rendered a "+" hero mark that the browser
    could not see, that was clipped, and that overlapped the source text beside
    it.  The mark is the model's own decoration; the block under it is source.
    """

    html = (
        "<html><head></head><body>"
        '<h1 data-qid="title" data-semantic-role="title">Title</h1>'
        '<div data-qid="s15.hero.decor" class="hero-mark" data-semantic-role="body">+</div>'
        '<div data-qid="source-block-4c1e" data-semantic-role="body"'
        ' data-source-block-id="source-slide:16:text:8">대행사는 촬영 가이드까지 제공할 수 있다</div>'
        "</body></html>"
    )
    decor = _metric("s15.hero.decor", "body", "+", y=120, visible=decor_visible)
    decor["clipped"] = True
    decor["glyph_clipped"] = True
    decor["bbox"] = {"x": 40, "y": 120, "width": 500, "height": 60}
    source = _metric(
        "source-block-4c1e", "body", "대행사는 촬영 가이드까지 제공할 수 있다", y=140
    )
    source["bbox"] = {"x": 60, "y": 140, "width": 460, "height": 28}
    return AuthoredSlideArtifact(
        html=html,
        png=b"png",
        browser_metrics={
            "viewport": {"width": 1280, "height": 720},
            "elements": [_metric("title", "title", "Title", y=30), decor, source],
        },
    )


def test_an_ornament_over_source_text_still_reaches_the_repair_call():
    """Capacity is terminal because source text cannot be shortened to fit.

    That reasoning covers the elements that own source text, not the decoration
    the model drew around them.  Classifying an invisible "+" ornament as a
    capacity impossibility ended a 35-slide deck without one repair call.
    """

    preflight = preflight_authored_provider_repair(
        slide_index=15,
        artifact=_restyle_artifact(decor_visible=False),
        capacity=SlideCapacity(15, "CONTENT", 40, 120, 200, 0, 8),
        profile_id="source-verbatim",
        reference_mode="style_reference",
    )
    assert "browser_text_not_visible:s15.hero.decor" in preflight.violations
    # The caller lets a safety-gate classification continue into repair and
    # raises on a capacity one, so the code is the whole decision here.
    assert preflight.code == "authored_composition_safety_gate_failed"


def test_a_clipped_source_block_is_a_layout_defect_the_repair_can_fix():
    """Which element ended up invisible is layout, not an impossibility.

    Operation 069c620d was ended by one clipped source block on a page whose
    whole source is 168 characters. Nothing there is over budget; the slide was
    simply laid out badly, and no repair call was made.
    """

    artifact = _restyle_artifact(decor_visible=True)
    metrics = dict(artifact.browser_metrics)
    elements = [dict(item) for item in metrics["elements"]]
    elements[2]["css_visible"] = False
    metrics["elements"] = elements
    preflight = preflight_authored_provider_repair(
        slide_index=15,
        artifact=AuthoredSlideArtifact(artifact.html, artifact.png, metrics),
        capacity=SlideCapacity(15, "CONTENT", 40, 120, 200, 0, 8),
        profile_id="source-verbatim",
        reference_mode="style_reference",
    )
    assert "browser_text_not_visible:source-block-4c1e" in preflight.violations
    assert preflight.code == "authored_composition_safety_gate_failed"


def test_a_restyled_slide_over_its_text_budget_is_still_terminal():
    """Verbatim source text cannot be shortened, so an over-budget page ends.

    This is the measurement the terminal classification exists for, and it
    stays terminal however the slide is laid out.
    """

    artifact = _restyle_artifact(decor_visible=True)
    preflight = preflight_authored_provider_repair(
        slide_index=15,
        artifact=artifact,
        capacity=SlideCapacity(15, "CONTENT", 4, 4, 8, 0, 8),
        profile_id="source-verbatim",
        reference_mode="style_reference",
    )
    assert any(">" in item for item in preflight.violations)
    assert preflight.code == "authored_slide_capacity_exceeded"
    assert preflight.terminal is True


def _ordinary_artifact_with_offcanvas_figure_node():
    """An ordinary (non-restyled) slide whose figure caption is off canvas.

    ``ig.s14.n6.desc`` is a node of a server-generated infographic figure (the
    component library in ``utils/authored_infographic/``, whose text is held
    verbatim to the plan by ``utils/authored_infographic/checker.py``), pushed
    off the bottom of the canvas. This slide carries no
    ``data-source-block-id`` anywhere: the model owns every other node.
    """

    html = (
        "<html><head></head><body>"
        '<h1 data-qid="title" data-semantic-role="title">Title</h1>'
        '<p data-qid="body" data-semantic-role="body">Body</p>'
        '<p data-qid="ig.s14.n6.desc" data-semantic-role="body">'
        "Figure caption text</p>"
        "</body></html>"
    )
    figure = _metric(
        "ig.s14.n6.desc",
        "body",
        "Figure caption text",
        y=700,
        visible=False,
    )
    figure["clipped"] = True
    figure["bbox"] = {"x": 40, "y": 700, "width": 400, "height": 80}
    elements = [
        _metric("title", "title", "Title", y=30),
        _metric("body", "body", "Body", y=120),
        figure,
    ]
    return AuthoredSlideArtifact(
        html=html,
        png=b"png",
        browser_metrics={
            "viewport": {"width": 1280, "height": 720},
            "elements": elements,
        },
    )


def test_an_offcanvas_infographic_figure_node_still_reaches_the_repair_call():
    """A library figure's own text is not the model's to shorten.

    Slide 14 of a 24-of-24-slide authored operation was ended by this exact
    violation set:

        violations=['browser_text_not_visible:ig.s14.n6.desc',
                     'safety:clipping:ig.s14.n6.desc',
                     'safety:off_canvas:ig.s14.n6']

    ``ig.s14.n6.desc`` is a figure caption, not model prose: shrinking it is
    not a legal repair, and it was simply pushed off the bottom of the
    canvas. That is a layout defect regardless of whether the deck is
    restyled, so it must reach the repair call rather than end the deck.
    """

    preflight = preflight_authored_provider_repair(
        slide_index=13,
        artifact=_ordinary_artifact_with_offcanvas_figure_node(),
        capacity=SlideCapacity(13, "CONTENT", 20, 30, 400, 0, 8),
        profile_id="ordinary",
        reference_mode="style_reference",
    )
    assert "browser_text_not_visible:ig.s14.n6.desc" in preflight.violations
    assert preflight.code == "authored_composition_safety_gate_failed"


def test_an_ordinary_slide_over_its_text_budget_is_still_terminal():
    """An ordinary deck owns its own text, so an over-budget page still ends.

    This is the measurement the terminal classification exists for, and it
    stays terminal whether or not the deck is a restyle.
    """

    artifact = _artifact("Body")
    preflight = preflight_authored_provider_repair(
        slide_index=2,
        artifact=artifact,
        capacity=SlideCapacity(2, "CONTENT", 4, 4, 8, 0, 8),
        profile_id="ordinary",
        reference_mode="style_reference",
    )
    assert any(">" in item for item in preflight.violations)
    assert preflight.code == "authored_slide_capacity_exceeded"
    assert preflight.terminal is True
