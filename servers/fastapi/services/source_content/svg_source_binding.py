"""Bind an SVG source-content request to its admitted manifest and output canvas."""

from __future__ import annotations

from typing import Any

from services.source_content.staging_projection import _source_error


def bind_svg_source_input(request: Any, *, manifest: Any, pin: Any, owner_id: Any) -> None:
    """Freeze the document canvas and the source-input binding on ``request``.

    Replay re-enters with the previously frozen canvas and binding; any
    difference is a 409 rather than a silent re-binding.
    """
    from services.document_canvas import (
        canvas_digest,
        document_canvas_for_orientation,
        document_canvas_from_source_slide_size,
    )
    from services.svg_source_layout import source_pages

    if manifest.slide_size is None:
        raise _source_error("svg_source_canvas_not_supported")
    try:
        selected_a4 = getattr(request, "page_size", None) == "A4"
        # Source geometry remains immutable in the manifest. Explicit A4
        # selectors govern layout, publication and export independently.
        # Requests saved before selectors were supported keep their exact
        # source canvas and v1 binding on durable replay.
        document_canvas = (
            document_canvas_for_orientation(request.orientation)
            if selected_a4
            else document_canvas_from_source_slide_size(
                width_emu=manifest.slide_size.cx,
                height_emu=manifest.slide_size.cy,
            )
        )
        source_pages(manifest)
    except ValueError as error:
        raise _source_error(str(error)) from error
    frozen_canvas = getattr(request, "_document_canvas", None)
    if frozen_canvas is not None and frozen_canvas != document_canvas:
        raise _source_error("svg_source_input_binding_mismatch", status_code=409)
    request._document_canvas = document_canvas
    request._authored_canvas = document_canvas.canvas_contract()
    canvas_payload = document_canvas.model_dump(mode="json", by_alias=True)
    binding = {
        "version": "svg-source-input/v2" if selected_a4 else "svg-source-input/v1",
        "ownerId": str(owner_id),
        "manifestId": pin.manifest_id,
        "manifestDigest": pin.digest,
        "referenceSnapshotId": str(manifest.reference_snapshot_id),
        "sourceDigest": manifest.source_digest,
        "canvasDigest": canvas_digest(canvas_payload),
        "documentCanvas": canvas_payload,
        "pageIds": [unit.source_unit_id for unit in sorted(manifest.units, key=lambda unit: unit.source_index)],
    }
    if selected_a4:
        binding["sourceSlideSize"] = manifest.slide_size.model_dump(mode="json", by_alias=True)
    frozen_binding = getattr(request, "_svg_source_binding", None)
    if frozen_binding is not None and frozen_binding != binding:
        raise _source_error("svg_source_input_binding_mismatch", status_code=409)
    request._svg_source_binding = binding
