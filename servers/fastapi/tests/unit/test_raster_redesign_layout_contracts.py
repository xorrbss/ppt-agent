"""Redesign rendering against real sealed contracts and the content ledger."""

from __future__ import annotations

import hashlib
import re
import uuid
import xml.etree.ElementTree as ET

import pytest

import test_raster_redesign_layout_fixtures as fx
from models.sql.public_artifact import ArtifactRef, artifact_storage_key
from services.raster_source_assets import raster_slot_id
from services.source_content.raster_contracts import seal_analysis
from services.source_content.raster_structure import redesign_content_ledger
from services.source_content.raster_svg_layout import render_raster_page_svg

NS = "{http://www.w3.org/2000/svg}"
SHA = "a" * 64


def _crop():
    owner = uuid.uuid4()
    return ArtifactRef(
        artifact_id=uuid.uuid4(), owner_id=owner, tenant_id="tenant", kind="image-derivative",
        sha256="b" * 64, size_bytes=10, media_type="image/png",
        storage_key=artifact_storage_key(owner_id=owner, tenant_id="tenant", digest="b" * 64),
        created_by_operation_id=uuid.uuid4(), retention_class="checkpoint",
    )


def _box(ns):
    return dict(x=ns.bbox.x, y=ns.bbox.y, width=ns.bbox.width, height=ns.bbox.height)


def _tokens(region):
    if region.role == "table" and region.table_cells:
        return [token for row in region.table_cells for cell in row for token in cell.split()]
    if region.role in {"title", "body"}:
        return (region.text or "").split()
    return []


def _region(ns, inventory, order):
    indices = []
    for token in _tokens(ns):
        indices.append(len(inventory))
        inventory.append(dict(text=token, bbox=_box(ns), engine="fixture", source_sha256=SHA, review_required=False))
    crop = ns.crop_artifact_ref is not None or ns.role == "source_reference"
    return dict(
        region_id=ns.region_id, page_number=ns.page_number, role=ns.role, bbox=_box(ns), z_order=0,
        text=ns.text, table_cells=ns.table_cells, reading_order=order, review_required=False,
        source_pixel_bbox=ns.source_pixel_bbox, ocr_token_indices=tuple(indices) if indices else None,
        **(dict(crop_artifact_ref=_crop(), source_occurrence_id="occ", crop_source_sha256=SHA,
                pixel_sha256="c" * 64) if crop else {}),
    )


def sealed_deck():
    deck = fx.deck()
    pages = []
    for page in deck.pages:
        inventory: list[dict] = []
        regions = [_region(region, inventory, order) for order, region in enumerate(page.regions)]
        reference = page.reference_region
        reference.role = "source_reference"
        reference.crop_artifact_ref = "crop"
        pages.append(dict(
            page_number=page.page_number, regions=regions, ocr_inventory=inventory,
            render_mode="redesign", approval_method="automatic_redesign",
            reference_region=_region(reference, [], None),
        ))
    return seal_analysis(dict(
        source=dict(source_sha256=SHA, page_count=len(pages), canvas_width_emu=fx.SOURCE_W,
                    canvas_height_emu=fx.SOURCE_H),
        target_canvas=dict(orientation="landscape", width_units=1188, height_units=840),
        pages=pages,
    ))


ANALYSIS = sealed_deck()


def _dense(value):
    return re.sub(r"\s+", "", value or "")


@pytest.mark.parametrize("number", range(1, len(ANALYSIS.pages) + 1))
def test_ledger_tokens_are_carried_exactly_once_by_the_rendered_page(number):
    ledger = redesign_content_ledger(ANALYSIS, number)
    assert ledger.lossless, ledger.violations
    approved = [r.region_id for p in ANALYSIS.pages for r in p.regions]
    svg = render_raster_page_svg(ANALYSIS, number, approved_region_ids=approved, instruction="compact")
    root = ET.fromstring(svg)
    texts = [(node.get("id") or "", "".join(node.itertext())) for node in root.iter(f"{NS}text")]
    hrefs = [node.get("href") for node in root.iter(f"{NS}image")]
    title = next((r for r in ANALYSIS.pages[number - 1].regions if r.role == "title"), None)
    for entry in ledger.entries:
        if entry.carrier == "pixels":
            if entry.role != "decoration":
                assert hrefs.count(raster_slot_id(number, entry.region_id)) == 1
            continue
        tag = hashlib.sha256(entry.region_id.encode("utf-8")).hexdigest()[:10]
        prefix = "rd-title" if title is not None and entry.region_id == title.region_id else f"rd-{tag}"
        emitted = "".join(value for key, value in texts if key == prefix or key.startswith(prefix + "-"))
        assert _dense(emitted) == "".join(_dense(token) for token in entry.tokens)
    reference = ANALYSIS.pages[number - 1].reference_region
    assert raster_slot_id(number, reference.region_id) not in svg
