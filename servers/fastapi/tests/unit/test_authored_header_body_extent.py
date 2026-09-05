"""``ensure_body_root_horizontal_extent`` — the deterministic normalisation
that keeps a pinned ``data-header-part="body"`` wrapper from collapsing to
zero width.

An absolutely positioned element with none of ``left``, ``right``, or
``width`` has no horizontal extent: it shrink-wraps to its content, and any
child anchored inside it with ``left:0;right:0`` collapses down to zero width
with it. This is the exact defect measured in job 18306a40 slide 10
(CLOSING): every text element measured width 0 and the slide rendered blank.
"""

from services.authored_header_body_extent import ensure_body_root_horizontal_extent

# The exact wrapper from the measured defect (job 18306a40, slide 10, CLOSING).
_CLOSING_WRAPPER = (
    '<div data-header-part="body" style="position:absolute;top:112px" '
    'data-qid="body.root" data-semantic-role="decoration">'
)


def test_pinned_wrapper_without_extent_gets_left_right():
    html = f"<html><body>{_CLOSING_WRAPPER}content</div></body></html>"

    result = ensure_body_root_horizontal_extent(html)

    assert (
        'data-header-part="body" style="position:absolute;left:0;right:0;top:112px" '
        'data-qid="body.root" data-semantic-role="decoration"' in result
    )
    # Nothing else in the document changes.
    assert result.replace("left:0;right:0;", "") == html


def test_pinned_wrapper_with_width_is_untouched():
    html = (
        '<div data-header-part="body" style="position:absolute;top:112px;width:1280px">'
        "content</div>"
    )

    assert ensure_body_root_horizontal_extent(html) == html


def test_pinned_wrapper_with_left_is_untouched():
    html = (
        '<div data-header-part="body" style="position:absolute;top:112px;left:64px">'
        "content</div>"
    )

    assert ensure_body_root_horizontal_extent(html) == html


def test_pinned_wrapper_with_right_is_untouched():
    html = (
        '<div data-header-part="body" style="right:0;position:absolute;top:112px">'
        "content</div>"
    )

    assert ensure_body_root_horizontal_extent(html) == html


def test_flowed_wrapper_is_untouched():
    html = '<div data-header-part="body" style="margin-top:20px">content</div>'

    assert ensure_body_root_horizontal_extent(html) == html


def test_no_wrapper_is_untouched():
    html = "<html><body><div>plain content, no header parts</div></body></html>"

    assert ensure_body_root_horizontal_extent(html) == html


def test_no_style_attribute_is_untouched():
    html = '<div data-header-part="body" data-qid="body.root">content</div>'

    assert ensure_body_root_horizontal_extent(html) == html


def test_result_is_idempotent():
    html = f"<html><body>{_CLOSING_WRAPPER}content</div></body></html>"

    once = ensure_body_root_horizontal_extent(html)
    twice = ensure_body_root_horizontal_extent(once)

    assert once == twice


def test_single_quoted_attribute_spelling():
    html = (
        "<main data-header-part='body' style='position:absolute;top:200px'>"
        "content</main>"
    )

    result = ensure_body_root_horizontal_extent(html)

    assert "style='position:absolute;left:0;right:0;top:200px'" in result


def test_source_bound_slide_is_normalised_the_same_as_any_other():
    """Unlike the alignment repair in ``authored_composition_policy``, this
    guard is a rendering-geometry fix, not a content rewrite — it must not
    skip a slide just because it carries ``data-source-block-id`` elsewhere.
    """
    html = (
        '<div data-source-block-id="para-1">source text</div>'
        f"{_CLOSING_WRAPPER}content</div>"
    )

    result = ensure_body_root_horizontal_extent(html)

    assert "position:absolute;left:0;right:0;top:112px" in result
