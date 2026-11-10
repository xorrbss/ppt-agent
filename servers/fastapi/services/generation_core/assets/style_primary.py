"""The deck's primary colour as the page author sees it.

Moved out of ``authoring/author_slide_design_system.py`` unchanged: svg asset
offers paint icons with it, and that module builds the HTML author's design
system. ``author_slide_design_system`` re-exports it.
"""

from __future__ import annotations

from typing import Any


def resolve_style_primary(brand: Any, style: Any | None) -> Any:
    """The primary colour the author uses: an explicit brand colour, else the style's, else the brand's."""
    primary = getattr(brand, "primary", None)
    if style is None or getattr(style, "id", None) == "default":
        return primary
    if getattr(brand, "primary_is_explicit", False):
        return primary
    return (
        getattr(style, "primary_color", None)
        or getattr(style, "preview_accent", None)
        or primary
    )


__all__ = ["resolve_style_primary"]
