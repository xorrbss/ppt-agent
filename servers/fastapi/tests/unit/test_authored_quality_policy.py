"""Focused regression tests for exact-reconstruction review policy."""


from utils.llm_calls.critique_slide import (
    EXACT_RECONSTRUCTION,
    STYLE_REFERENCE,
    CritiqueIssue,
    ImageContentPart,
    SlideCritique,
    _build_critique_messages,
)


def _critique(severity: str, detail: str = "visible mismatch") -> SlideCritique:
    return SlideCritique(
        needs_fix=True,
        issues=[CritiqueIssue(type="other", severity=severity, detail=detail)],
    )


def test_exact_prompt_avoids_overlap_crop_photo_logo_and_font_false_positives():
    source = b"\x89PNG\r\n\x1a\nsource"
    render = b"render"

    messages = _build_critique_messages(
        render,
        source_image=source,
        reference_mode=EXACT_RECONSTRUCTION,
    )
    system_prompt = messages[0].content
    parts = messages[1].content
    image_parts = [part for part in parts if isinstance(part, ImageContentPart)]

    assert "already exists in the source is intentional and is not a defect" in system_prompt
    assert "photos, logos, and complex raster artwork" in system_prompt
    assert "harmless compression, color-profile, or subpixel antialiasing" in system_prompt
    assert "You do not need to identify the font by name" in system_prompt
    assert "INDETERMINATE:" in system_prompt
    assert [part.data for part in image_parts] == [source, render]


def test_style_reference_prompt_and_single_render_contract_are_unchanged():
    render = b"render-only"

    messages = _build_critique_messages(
        render,
        slide_context="expected context",
        reference_mode=STYLE_REFERENCE,
    )
    parts = messages[1].content
    image_parts = [part for part in parts if isinstance(part, ImageContentPart)]

    assert messages[0].content == (
        "You are a meticulous presentation design reviewer. Inspect the rendered slide "
        "IMAGE and report only REAL, visible problems: text overflow or clipping, element "
        "overlap, low contrast, leftover placeholder text, misalignment, or large/unbalanced "
        "empty space. Do not invent issues. If the slide looks clean and readable, return "
        "needs_fix=false with an empty issues list. Be specific and concise; ignore the small "
        "navigation badge that may appear in a corner of the capture."
    )
    assert parts[0].text == (
        "Review this slide image. Intended content: expected context"
    )
    assert [(part.data, part.mime_type) for part in image_parts] == [
        (render, "image/png")
    ]
