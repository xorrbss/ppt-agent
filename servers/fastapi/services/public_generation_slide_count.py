"""Resolve the slide count a public generation admission must charge.

A page-locked source-content restyle authors exactly one slide per admitted
source page (``prepare_source_content_authored_reflow`` rewrites ``n_slides``
to that count on the worker).  Admission sizes the immutable deadline and the
provider budgets from its count, so it must charge the same number instead of
the caller's ``n_slides``/``slideCount`` hint.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from services.source_content.pipeline import (
    RASTER_SOURCE_MODE,
    SOURCE_MODE as SOURCE_CONTENT_RESTYLE_MODE,
    admit_source_content_request,
)


def source_outline_slide_count(request: Any) -> int | None:
    """Return the admitted outline's locked slide count, if one is frozen."""

    outline = getattr(request, "_source_document_outline", None)
    raw_count = getattr(outline, "source_slide_count", None)
    if raw_count is None:
        return None
    count = int(raw_count)
    return count if count >= 1 else None


def source_content_admission_carrier(
    *,
    source_mode: str | None,
    source_content_manifest_pin: Any,
    instructions: str | None,
    reference_snapshot_ids: list[str],
    raster_approval_pin: Any = None,
    strategy: str | None = None,
    orientation: str | None = None,
    generation_request: Any = None,
) -> SimpleNamespace:
    """Build the minimal request shape ``admit_source_content_request`` reads.

    The public v2 route resolves its count before the internal request exists,
    so it admits the manifest through this carrier instead of a partial model.
    The worker re-admits the durable pin itself; nothing here is persisted.
    """

    carrier = SimpleNamespace(
        source_mode=source_mode,
        source_content_manifest_pin=source_content_manifest_pin,
        raster_approval_pin=raster_approval_pin,
        strategy=strategy,
        orientation=orientation,
        instructions=instructions,
        web_search=False,
        vision_qa=False,
        export_as="pptx",
        reference_snapshot_ids=list(reference_snapshot_ids),
    )
    if generation_request is not None:
        # Preserve validated admission fields and selector presence. Copying
        # the DTO's implicit 16:9 value alone would override source geometry.
        for name in (
            "page_size", "orientation", "aspect_ratio", "svg_canvas",
            "content_policy", "authored_style", "generation_mode",
            "generation_engine", "design_pack_pin", "master_pin",
            "svg_design_reference_pin", "svg_design_reference_snapshot_id",
            "svg_template_pin", "svg_template_mode", "svg_template_frame_map",
        ):
            setattr(carrier, name, getattr(generation_request, name, None))
        carrier.model_fields_set = set(generation_request.model_fields_set)
    return carrier


async def resolve_source_content_admission_slide_count(
    request: Any,
    *,
    sql_session: Any,
    owner_id: Any,
    tenant_id: str | None = None,
) -> int | None:
    """Return the page-locked count for a restyle request, else ``None``.

    Raises ``SourceContentError`` when the manifest pin cannot be admitted.
    ``request`` gains the frozen manifest/outline as a side effect exactly as
    request validation would set them.
    """

    if getattr(request, "source_mode", None) not in {SOURCE_CONTENT_RESTYLE_MODE, RASTER_SOURCE_MODE}:
        return None
    if getattr(request, "_source_document_outline", None) is None:
        await admit_source_content_request(
            request,
            sql_session=sql_session,
            owner_id=owner_id,
            tenant_id=tenant_id,
        )
    return source_outline_slide_count(request)
