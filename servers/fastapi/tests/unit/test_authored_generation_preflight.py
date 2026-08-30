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
    assert terminal.code == "authored_slide_capacity_exceeded"
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
