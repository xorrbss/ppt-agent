"""Display page numbers of raster-redesign pages after reorder/insert/delete.

A redesign page keeps two numbers:

* the *source* page number (``svgReceipt.rasterSourcePageNumber`` and the
  asset envelope's ``sourcePageIndex``): provenance for crop slot ids, the
  source manifest and export -- never changed by a reorder;
* the *display* page number: the footer text drawn by raster_redesign_layout
  (element ``PAGE_NUMBER_ID``, right-aligned, zero-padded) -- follows the deck.

Renumbering replaces only that footer text. Geometry, crops, the source
manifest and the offered page are unchanged. A new screen placement manifest
retains the same derivatives, and Chromium remeasures the relabeled canonical
SVG using its authorized materialization before the shared setter writes it.
"""

from __future__ import annotations

import json
import re
from copy import deepcopy

from services.svg_contract import PAGE_NUMBER_ID, source_digest, validate_svg
from services.svg_page_publication import set_svg_page

_FOOTER = re.compile(rf'(<text id="{PAGE_NUMBER_ID}"[^>]*>)(\d+)(</text>)')


def display_page_number(svg: str) -> int | None:
    matches = _FOOTER.findall(svg or "")
    return int(matches[0][1]) if len(matches) == 1 else None


def relabel_page_number(svg: str, page_number: int) -> str:
    """Same element, same style and geometry; only the digits change."""
    if page_number < 1:
        raise ValueError("raster_display_page_number_invalid")
    result, count = _FOOTER.subn(lambda m: m.group(1) + str(page_number).zfill(2) + m.group(3), svg)
    if count != 1:
        raise ValueError("raster_redesign_footer_missing")
    return result


def _slot_contract(svg: str) -> dict:
    return {slot: {"required": True, "allowOmit": False}
            for slot in re.findall(r'href="(source-slot-[A-Za-z0-9_-]+)"', svg)}


async def _fenced_operation(session, presentation, *, operation_id, attempt_token, expected_revision):
    from sqlalchemy import select

    from models.sql.workspace import WorkspaceOperation
    from utils.owner_context import get_current_owner_id, get_current_tenant_id

    owner, tenant = get_current_owner_id(), get_current_tenant_id()
    if (not operation_id or not attempt_token or expected_revision is None or not tenant
            or owner != presentation.owner_id):
        raise ValueError("svg_asset_mutation_fence_required")
    operation = await session.scalar(select(WorkspaceOperation).where(
        WorkspaceOperation.id == operation_id, WorkspaceOperation.owner_id == owner,
        WorkspaceOperation.presentation_id == presentation.id,
        WorkspaceOperation.state == "running", WorkspaceOperation.attempt_token == attempt_token,
        WorkspaceOperation.expected_revision == expected_revision).with_for_update())
    if operation is None or presentation.revision != expected_revision:
        raise ValueError("svg_asset_mutation_fence_lost")
    return owner, tenant


async def renumber_redesign_page(session, presentation, slide, index, *, width, height,
                                 operation_id, attempt_token, expected_revision, service=None):
    """Make the footer show ``index + 1``; returns True when the slide changed."""
    display = index + 1
    current = display_page_number(slide.svg_content)
    if current is None:
        if slide.index == index:
            return False  # nothing to renumber at an unchanged position
        raise ValueError("raster_redesign_footer_missing")
    if current == display:
        return False
    svg = relabel_page_number(slide.svg_content, display)
    validate_svg(svg, width=width, height=height, source_manifest=_slot_contract(svg))
    content = deepcopy(slide.content or {})
    envelope = content.get("svgAssetManifest")
    materialized = None
    if envelope is not None:
        content["svgAssetManifest"], materialized = await _rebind_screen_placement(
            session, presentation, slide, svg, envelope, operation_id=operation_id,
            attempt_token=attempt_token, expected_revision=expected_revision, service=service,
            width=width, height=height)
    receipt = dict(content.get("svgReceipt") or {})
    previous_digest = receipt.get("source_digest")
    receipt.update(
        rasterDisplayPageNumber=display,
        pageNumberRelabel={"from": current, "to": display, "measuredSourceDigest": previous_digest},
    )
    content["svgReceipt"] = receipt
    from services.authored_presentation_artifacts import _save_slide_pngs
    from services.document_canvas import DocumentCanvasMetadata, parse_canvas_metadata
    from services.authored_canvas import presentation_canvas_metadata
    from services.svg_renderer import render_svg_with_metrics

    metadata = parse_canvas_metadata(presentation_canvas_metadata(getattr(presentation, "theme", None)))
    physical_kwargs = {"physical_canvas": metadata} if isinstance(metadata, DocumentCanvasMetadata) else {}
    capture = await render_svg_with_metrics(svg, width=width, height=height,
        materialized_svg=materialized, allow_image_only=materialized is not None,
        instructions=getattr(presentation, "instructions", None), **physical_kwargs)
    set_svg_page(slide, svg_content=svg, content=content, quality=capture.quality)
    image = _save_slide_pngs(presentation.id, [capture.png], "svg-numbering")[0]
    slide.properties = {**(getattr(slide, "properties", None) or {}), "image": image}
    return True


async def _rebind_screen_placement(session, presentation, slide, svg, envelope, *, operation_id,
                                   attempt_token, expected_revision, width, height, service=None):
    from services.svg_asset_persistence import load_placement_manifest_pointer, load_source_manifest_pointer
    from services.svg_document_mutations import rebound_placement

    if (not isinstance(envelope, dict) or envelope.get("schemaVersion") != "svg-slide-assets/v1"
            or envelope.get("canonicalVersion") != "svg-static/v2"):
        raise ValueError("svg_asset_revision_metadata_invalid")
    owner, tenant = await _fenced_operation(session, presentation, operation_id=operation_id,
                                            attempt_token=attempt_token, expected_revision=expected_revision)
    if service is None:
        from services.artifact_store import configured_public_artifact_service
        service = configured_public_artifact_service()
    source = await load_source_manifest_pointer(session, service, pointer=envelope.get("sourceManifest"),
                                                owner_id=owner, tenant_id=tenant)
    previous = await load_placement_manifest_pointer(session, service, pointer=envelope.get("screenPlacement"),
                                                     owner_id=owner, tenant_id=tenant)
    if (source.presentation_id != presentation.id or previous.purpose != "screen"
            or previous.canonical_svg_digest != source_digest(slide.svg_content)):
        raise ValueError("svg_asset_revision_binding_invalid")
    revision = expected_revision + 1
    placement = rebound_placement(svg, source, previous, revision=revision)
    placement.validate_source(source, page_index=envelope.get("sourcePageIndex", slide.index))
    payload = json.dumps(placement.model_dump(mode="json", by_alias=True), sort_keys=True,
                         separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode()
    reference = await service.persist_bytes(session, owner_id=owner, tenant_id=tenant, kind="placement-manifest",
                                            payload=payload, media_type="application/json",
                                            created_by_operation_id=operation_id, retention_class="published")
    from services.svg_asset_resolver import resolve_svg_for_render

    materialized = await resolve_svg_for_render(svg, manifest=source,
        derivatives={item.slot_id: item.derivative for item in placement.placements},
        session=session, owner_id=owner, tenant_id=tenant, width=width,
        height=height, service=service,
        page_index=envelope.get("sourcePageIndex", slide.index), placement_manifest=placement)
    return ({**envelope, "screenPlacement": dict(ref=str(reference.artifact_id), sha256=reference.sha256),
             "slideRevision": revision}, materialized)
