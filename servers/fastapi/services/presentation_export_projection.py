"""Bounded wire projection for persisted presentation export reads."""

from __future__ import annotations

from typing import Any


PRESENTATION_EXPORT_PROJECTION = "authored-composition-dedup-v1"
PRESENTATION_EXPORT_PROJECTION_HEADER = "X-Presenton-Presentation-Projection"
COMPOSITION_PIN_REFERENCE = {
    "$presentonRef": "theme.authored_vnext.compositionPin",
    "schemaVersion": "presenton.export-composition-ref/v1",
}


def project_presentation_for_export(payload: dict[str, Any]) -> dict[str, Any]:
    """Replace only byte-identical repeated composition pins with a wire ref.

    The persisted payload and ordinary presentation read remain unchanged. A
    consumer must see ``PRESENTATION_EXPORT_PROJECTION_HEADER`` and rehydrate
    every exact marker before calculating the renderer-source digest.
    """

    # ``PresentationWithSlides.model_dump`` gives this boundary a detached JSON
    # tree. Mutate that tree in place so a ~95 MiB response is not copied once
    # more before its repeated pins are released.
    projected = payload
    theme = projected.get("theme")
    if not isinstance(theme, dict):
        return projected
    authored_vnext = theme.get("authored_vnext")
    if not isinstance(authored_vnext, dict):
        return projected
    shared_pin = authored_vnext.get("compositionPin")
    if not isinstance(shared_pin, dict):
        return projected

    scene = authored_vnext.get("scene")
    if isinstance(scene, dict) and scene.get("compositionPin") == shared_pin:
        scene["compositionPin"] = dict(COMPOSITION_PIN_REFERENCE)

    slides = projected.get("slides")
    if isinstance(slides, list):
        for slide in slides:
            if not isinstance(slide, dict):
                continue
            content = slide.get("content")
            if (
                isinstance(content, dict)
                and content.get("compositionPin") == shared_pin
            ):
                content["compositionPin"] = dict(COMPOSITION_PIN_REFERENCE)
    return projected


__all__ = [
    "COMPOSITION_PIN_REFERENCE",
    "PRESENTATION_EXPORT_PROJECTION",
    "PRESENTATION_EXPORT_PROJECTION_HEADER",
    "project_presentation_for_export",
]
