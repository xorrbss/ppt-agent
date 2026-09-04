"""Ownership edges and declared run style of the Composer's declared semantic draft.

``browser-source.ts`` reports each observed element's ``parentElementId`` as
``element.parentElement.closest("[data-qid]")``.  The authored-hybrid exporter
rejects the whole slide with ``authored_semantic_ownership_mismatch`` when the
declared graph disagrees, so the draft must project the same nesting.

Run style (size, weight, colour) must equally never disagree with what
``render_css.py`` and ``render_text.py`` actually painted: the exporter's
identity check compares the declared run against what Chromium measures.
"""

from __future__ import annotations

import uuid

import pytest

from models.authored_vnext.semantic.contracts import AuthoredSemanticSourceV1
from services.composer.plan import ComposerTokens, Paragraph, SourceRunRef, TextRun
from services.composer.publish_semantic import (
    collect_qid_elements,
    semantic_draft_from_html,
)


TOKENS = ComposerTokens(
    background="#FFFFFF",
    surface="#F4F6F8",
    text_primary="#0F172A",
    text_secondary="#475569",
    accent="#1473E6",
    accent_text="#0B4FA8",
    accent_soft="#E8F0FE",
    rule="#D9E2EF",
)

NESTED_HTML = """
<div data-qid="cx-band" data-semantic-role="furniture">
  <div data-qid="cx-stack" data-semantic-role="group">
    <h1 data-qid="cx-title" data-semantic-role="title">
      <span data-qid="source-block-a" data-semantic-role="body">분기 실적</span>
    </h1>
    <p data-qid="cx-body" data-semantic-role="body">본문 문장</p>
  </div>
  <img data-qid="cx-image" data-semantic-role="image" alt="">
</div>
<footer data-qid="cx-footer" data-semantic-role="footer">1 / 35</footer>
"""


def _draft(html: str, *, tokens: ComposerTokens = TOKENS) -> list[dict]:
    draft = semantic_draft_from_html(
        html, language="ko", font_family="Pretendard", font_face="Regular", tokens=tokens
    )
    return draft["semanticSource"]["elements"]


def test_collect_projects_the_nesting_the_browser_walks() -> None:
    collected = {item["qid"]: item for item in collect_qid_elements(NESTED_HTML)}

    assert collected["cx-band"]["parent"] is None
    assert collected["cx-band"]["children"] == ["cx-stack", "cx-image"]
    assert collected["cx-stack"]["parent"] == "cx-band"
    assert collected["cx-title"]["children"] == ["source-block-a"]
    assert collected["source-block-a"]["parent"] == "cx-title"
    # A void element closes itself: the footer is a sibling of the band, and the
    # image never becomes an ancestor of what follows it.
    assert collected["cx-image"]["children"] == []
    assert collected["cx-footer"]["parent"] is None


def test_draft_declares_both_directions_of_every_edge() -> None:
    elements = {item["elementId"]: item for item in _draft(NESTED_HTML)}

    for element in elements.values():
        parent_id = element["parentId"]
        if parent_id is not None:
            assert element["elementId"] in elements[parent_id]["childIds"]
        for child_id in element["childIds"]:
            assert elements[child_id]["parentId"] == element["elementId"]


def test_draft_passes_the_published_contract_validator() -> None:
    """The publication contract runs the same ownership check as the exporter."""

    elements = _draft(NESTED_HTML)
    source = AuthoredSemanticSourceV1.model_validate(
        {
            "slideId": uuid.uuid4(),
            "presentationId": uuid.uuid4(),
            "slideRevision": 1,
            "operationId": uuid.uuid4(),
            "authoredHtmlDigest": "sha256:" + "0" * 64,
            "elements": elements,
            "identityRegistry": {
                "activeQids": sorted(item["elementId"] for item in elements),
                "retiredQids": [],
            },
        }
    )
    by_id = {element.element_id: element for element in source.elements}
    assert by_id["cx-stack"].parent_id == "cx-band"
    assert by_id["cx-band"].child_ids == ["cx-stack", "cx-image"]


def test_text_belongs_to_the_innermost_owner_only() -> None:
    elements = {item["elementId"]: item for item in _draft(NESTED_HTML)}

    assert elements["source-block-a"]["kind"] == "text"
    assert (
        elements["source-block-a"]["payload"]["paragraphs"][0]["runs"][0]["text"]
        == "분기 실적"
    )
    # The title only wraps the run-bearing block, so it declares no text of its own.
    assert elements["cx-title"]["kind"] == "shape"
    assert elements["cx-body"]["kind"] == "text"


@pytest.mark.parametrize("qid", ["cx-band", "cx-stack", "cx-title"])
def test_container_elements_stay_declared(qid: str) -> None:
    """Containers carry qids in the DOM, so they must stay in the declared set."""

    assert qid in {item["elementId"] for item in _draft(NESTED_HTML)}


# --- Declared run style must equal what the renderer actually painted ----------


def _rendered_paragraph_html(*, font_px: float, css: str, bold_source: bool = False) -> str:
    """One ``render_text.render_bound_text`` element, exactly as the renderer emits it."""

    from services.composer.render_text import BindingRegistry, render_bound_text

    paragraph = Paragraph(
        text="본문 문장",
        runs=[TextRun(text="본문 문장", bold=bold_source)] if bold_source else [],
        source=SourceRunRef(block_id="b1", binding_id="b1"),
    )
    element, _glyph = render_bound_text(
        paragraph, tag="p", role="body", css=css, font_px=font_px, registry=BindingRegistry()
    )
    return f"<html><body>{element}</body></html>"


@pytest.mark.parametrize(
    ("font_px", "expected_pt"),
    [
        (40.0, 30.0),  # title rung: 40 * 0.75
        (26.0, 19.5),  # subtitle rung
        (20.0, 15.0),  # body rung
        (15.0, 11.25),  # supporting floor
        (12.0, 9.0),  # exactly at the clamp
        (10.0, 9.0),  # below the clamp: max(9, 7.5) == 9
        (17.85, 13.3875),  # a fit-ladder scaled, non-round size
    ],
)
def test_declared_size_matches_the_renderers_own_px_0_75_clamp(font_px: float, expected_pt: float) -> None:
    """``fontSizePt = max(9, round(px * 0.75))``, mirroring ``browser-source.ts:367``."""

    html = _rendered_paragraph_html(font_px=font_px, css="cx-text cx-body")
    elements = _draft(html)
    assert elements[0]["payload"]["paragraphs"][0]["runs"][0]["fontSizePt"] == expected_pt


def test_declared_weight_reads_the_inline_bold_render_text_writes() -> None:
    """A body paragraph whose source runs are all bold gets the inline echo."""

    plain = _draft(_rendered_paragraph_html(font_px=20.0, css="cx-text cx-body", bold_source=False))
    bold = _draft(_rendered_paragraph_html(font_px=20.0, css="cx-text cx-body", bold_source=True))
    assert plain[0]["payload"]["paragraphs"][0]["runs"][0]["weight"] == 400
    assert bold[0]["payload"]["paragraphs"][0]["runs"][0]["weight"] == 700


def test_declared_weight_is_bold_for_a_title_with_no_inline_bold() -> None:
    """A title is bold from its CSS class alone; render_text never echoes it inline.

    Reading only the inline style would still declare 400 for a title whose
    source text was never itself bold-formatted, even though the browser
    computes 700 for it -- the exact divergence this module exists to close.
    """

    html = _rendered_paragraph_html(font_px=40.0, css="cx-text cx-title", bold_source=False)
    assert "font-weight" not in html  # the renderer really did not echo it inline
    elements = _draft(html)
    assert elements[0]["payload"]["paragraphs"][0]["runs"][0]["weight"] == 700


def test_marker_class_resolves_accent_text_despite_its_decoration_role() -> None:
    """``.cx-marker`` paints in ``accent_text`` while its ``data-semantic-role`` is ``decoration``.

    A role-keyed colour table would resolve ``decoration`` to the body
    colour; only the class-keyed table gets this right.
    """

    from services.composer.render_text import QidFactory, render_marker

    marker_html = render_marker("•", qids=QidFactory(), font_px=20.0)
    assert 'data-semantic-role="decoration"' in marker_html
    html = f"<html><body>{marker_html}</body></html>"
    elements = _draft(html)
    run = elements[0]["payload"]["paragraphs"][0]["runs"][0]
    assert run["color"] == TOKENS.accent_text
    assert run["color"] != TOKENS.text_primary
    assert run["weight"] == 700


def test_class_without_a_colour_bearing_class_falls_back_to_text_primary() -> None:
    html = '<html><body><p data-qid="q1" data-semantic-role="body" class="cx-text cx-body">본문</p></body></html>'
    elements = _draft(html)
    assert elements[0]["payload"]["paragraphs"][0]["runs"][0]["color"] == TOKENS.text_primary


def test_last_colour_bearing_class_wins_when_several_are_present() -> None:
    """Mirrors the CSS cascade: the later-declared rule of two equal-specificity classes wins.

    ``TEXT_COLOR_CLASS_TOKENS`` is ordered like ``render_css.stylesheet``, so
    an element carrying both ``cx-marker`` (earlier) and ``cx-muted`` (later)
    must resolve to ``cx-muted``'s token, not ``cx-marker``'s.
    """

    html = (
        '<html><body><p data-qid="q1" data-semantic-role="body" '
        'class="cx-marker cx-muted">본문</p></body></html>'
    )
    elements = _draft(html)
    assert elements[0]["payload"]["paragraphs"][0]["runs"][0]["color"] == TOKENS.text_secondary
