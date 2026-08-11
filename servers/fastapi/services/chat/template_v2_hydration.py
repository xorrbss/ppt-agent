"""Fail-closed hydration of Chat content into native Template V2 slide UI."""

from __future__ import annotations

import copy
from dataclasses import dataclass
from typing import Any

from templates.v2.content import hydrate_layout_content
from templates.v2.persistence import canonicalize_slide_ui


class TemplateV2HydrationError(ValueError):
    """Raised when a layout cannot be resolved or hydrated canonically."""


@dataclass(frozen=True)
class HydratedTemplateV2State:
    """A fully validated state that is safe to apply to a SlideModel."""

    content: dict[str, Any]
    ui: dict[str, Any]


def resolve_template_v2_layout(
    layout_payload: dict[str, Any], layout_id: str
) -> dict[str, Any]:
    """Return a detached layout by id from a Template V2 layouts payload."""

    layouts = layout_payload.get("layouts")
    if isinstance(layouts, dict):
        layouts = layouts.get("layouts")
    if not isinstance(layouts, list):
        raise TemplateV2HydrationError("Template V2 payload has no layouts list")
    for layout in layouts:
        if isinstance(layout, dict) and str(layout.get("id")) == str(layout_id):
            return copy.deepcopy(layout)
    raise TemplateV2HydrationError(f"Template V2 layout not found: {layout_id}")


def hydrate_template_v2_slide(
    source_layout: dict[str, Any],
    content: dict[str, Any],
) -> HydratedTemplateV2State:
    """Clone, hydrate and strictly canonicalize a layout without mutating inputs."""

    if not isinstance(content, dict):
        raise TemplateV2HydrationError("Template V2 content must be an object")
    try:
        ui = hydrate_layout_content(source_layout, content)
        canonical = canonicalize_slide_ui(ui)
    except Exception as exc:
        raise TemplateV2HydrationError(
            f"Template V2 hydration produced invalid native UI: {exc}"
        ) from exc
    if canonical is None:
        raise TemplateV2HydrationError("Template V2 hydration produced empty UI")
    return HydratedTemplateV2State(
        content=copy.deepcopy(content),
        ui=canonical,
    )


def apply_hydrated_template_v2_state(
    slide: Any, state: HydratedTemplateV2State
) -> None:
    """Apply validated state, restoring the object on assignment failure."""

    previous = (
        copy.deepcopy(getattr(slide, "content", None)),
        copy.deepcopy(getattr(slide, "ui", None)),
        getattr(slide, "html_content", None),
    )
    try:
        slide.content = copy.deepcopy(state.content)
        slide.html_content = None
        slide.ui = copy.deepcopy(state.ui)
    except Exception:
        slide.content, slide.ui, slide.html_content = previous
        raise


__all__ = [
    "HydratedTemplateV2State",
    "TemplateV2HydrationError",
    "apply_hydrated_template_v2_state",
    "hydrate_template_v2_slide",
    "resolve_template_v2_layout",
]
