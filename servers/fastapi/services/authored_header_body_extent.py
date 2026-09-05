"""Guarantee the header body wrapper has a horizontal extent.

The header contract prompt (``utils/authored_header_prompt.py``) tells the
authoring model to wrap all slide content in one element marked
``data-header-part="body"`` and to pin it with ``position:absolute`` when the
band above it is also pinned. An absolutely positioned element with none of
``left``, ``right``, or ``width`` set has no width to shrink-wrap to — it
collapses to the width of its content, and any child anchored inside it with
``left:0;right:0`` collapses with it (measured width 0, blank slide). The
prompt now asks the model to spell out ``left:0;right:0`` on the pinned form,
but the server owns this contract and must not depend on the model obeying
it, so this module normalises the wrapper deterministically before renderer
admission.

``ensure_body_root_horizontal_extent`` is a pure string transform: it finds
the single ``data-header-part="body"`` wrapper, and when its inline style
pins it with ``position:absolute`` but declares none of ``left``, ``right``,
or ``width``, inserts ``left:0;right:0;`` immediately after the
``position:absolute`` declaration. Every other case — including a wrapper
that already has an explicit extent, a flowed (``margin-top``) wrapper, or a
slide with no body wrapper at all — is returned byte-identical. The function
does not special-case ``data-source-block-id`` slides: the collapse this
guards against is a rendering-geometry defect, not a content rewrite, so it
applies the same fix regardless of where the slide's content came from.
"""

from __future__ import annotations

import re

__all__ = ["ensure_body_root_horizontal_extent"]

_BODY_WRAPPER = re.compile(
    r"<(?P<tag>div|main|section)\b"
    r"(?P<attrs>[^>]*\bdata-header-part\s*=\s*(?P<name_quote>['\"])body(?P=name_quote)[^>]*)>",
    re.IGNORECASE | re.DOTALL,
)

_STYLE_ATTR = re.compile(
    r"\bstyle\s*=\s*(?P<quote>['\"])(?P<style>.*?)(?P=quote)",
    re.IGNORECASE | re.DOTALL,
)

_POSITION_ABSOLUTE_DECL = re.compile(
    r"(?P<lead>^|;)(?P<ws>\s*)position\s*:\s*absolute\s*;?",
    re.IGNORECASE,
)

_HAS_HORIZONTAL_EXTENT = re.compile(
    r"(?:^|;)\s*(?:left|right|width)\s*:",
    re.IGNORECASE,
)


def ensure_body_root_horizontal_extent(html: str) -> str:
    """Insert ``left:0;right:0`` into a pinned body wrapper that lacks one.

    Returns ``html`` unchanged unless a single ``data-header-part="body"``
    wrapper is found whose inline style pins it with
    ``position:absolute`` and declares none of ``left``, ``right``, or
    ``width``. Idempotent: a wrapper this function has already fixed
    already declares ``left``, so a second call is a no-op.
    """
    wrapper = _BODY_WRAPPER.search(html)
    if wrapper is None:
        return html

    attrs = wrapper.group("attrs")
    style_match = _STYLE_ATTR.search(attrs)
    if style_match is None:
        return html

    style = style_match.group("style")
    if _POSITION_ABSOLUTE_DECL.search(style) is None:
        return html
    if _HAS_HORIZONTAL_EXTENT.search(style) is not None:
        return html

    new_style = _POSITION_ABSOLUTE_DECL.sub(
        lambda m: f"{m.group('lead')}{m.group('ws')}position:absolute;left:0;right:0;",
        style,
        count=1,
    )

    attrs_start = wrapper.start("attrs")
    style_start = attrs_start + style_match.start("style")
    style_end = attrs_start + style_match.end("style")
    return html[:style_start] + new_style + html[style_end:]
