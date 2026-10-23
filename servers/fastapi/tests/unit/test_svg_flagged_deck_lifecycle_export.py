"""A flagged source deck runs through the isolated export worker.

The pages are the real producers' output (``assemble_source_svg`` for source
pages, ``placeholder_page_svg`` for the terminal ladder's placeholder), the
bundle is ``build_svg_export_bundle``'s, and the worker, its converter and the
PPTX readback are the production functions. Only the artifact store is a
fixture (``MemoryStore``).
"""
from __future__ import annotations

import asyncio
import json
import time
import uuid
from io import BytesIO
from types import SimpleNamespace

import pytest
from pptx import Presentation

from services.authored_canvas import canvas_metadata_for_aspect_ratio
from services.generation_core.drivers.svg.export_native_wire import PageNativeObjects
from services.generation_core.drivers.svg.fallback import placeholder_page_svg
from services.source_content.contracts import SourceContentManifest
from services.source_content.digests import digest_json
from services.svg_contract import source_digest
from services.svg_export_bundle import build_svg_export_bundle
from services.svg_export_source_preservation import page_svg_digest
from services.svg_source_layout import assemble_source_svg, page_digest
from tests.unit.test_svg_vector_pdf_export import MemoryStore
from utils.svg_template_fonts import FONT_FAMILY
from workers.public_svg_export_worker import execute_svg_export
from workers.public_svg_pptx_readback import iter_shapes, source_preservation_readback
from workers.public_worker_runtime import PublicWorkerExecutionError

TEXTS = ["원문 첫 페이지 125%", "원문 둘째 페이지", "원문 셋째 페이지"]


def _manifest(texts):
    units = []
    for page, text in enumerate(texts):
        payload = {"text": text}
        units.append(dict(sourceUnitId=f"p{page}", sourceIndex=page, blocks=[dict(
            sourceBlockId=f"p{page}:0", kind="text", semanticRole="text", order=0,
            payload=payload, contentDigest=digest_json(payload))]))
    manifest = SourceContentManifest(manifest_id="m", owner_id="owner", reference_snapshot_id="snap",
        source_digest="sha256:input", manifest_digest="pending",
        object_inventory=dict(discoveredObjectCount=0, classifiedObjectCount=0, contentBearingObjectCount=0),
        inventory_objects=(), units=units,
        counts=dict(sourceUnits=len(texts), textBlocks=len(texts), tableCells=0,
                    chartValues=0, uniqueImages=0, imageOccurrences=0))
    payload = manifest.model_dump(mode="json", by_alias=True)
    payload.pop("manifestDigest")
    return manifest.model_copy(update={"manifest_digest": digest_json(payload)})


def _source_page(manifest, index, *, rendering_verified):
    plan = dict(source_unit_id=f"p{index}", page_digest=page_digest(manifest, index),
                placements=[dict(block_id=f"p{index}:0", x=60, y=60, width=700, height=200, font_size=28)])
    result = assemble_source_svg(manifest, index, plan, source_images={}, image_slots={},
                                 min_font_size_px=12, style_digest="sha256:style")
    # render.py records ``rendering_verified`` after a passing readback; a page
    # whose readback did not pass publishes with ``source_visibility`` instead.
    return result.svg_content, {**result.receipt, "rendering_verified": rendering_verified}


def _deck():
    """A clean page, a source_visibility page and a failure placeholder."""
    manifest = _manifest(TEXTS)
    canvas = canvas_metadata_for_aspect_ratio("16:9")
    clean = _source_page(manifest, 0, rendering_verified=True)
    flagged = _source_page(manifest, 1, rendering_verified=False)
    placeholder = placeholder_page_svg(title=TEXTS[2], width=canvas.width_px, height=canvas.height_px,
                                       font_family=FONT_FAMILY)
    return canvas, [clean[0], flagged[0], placeholder], [clean[1], flagged[1], None]


def _export(tmp_path, canvas, svgs, receipts):
    store, operation_id = MemoryStore(tmp_path), uuid.uuid4()
    refs = [store.put(svg.encode(), operation_id=operation_id, kind="slide-svg", media_type="image/svg+xml")
            for svg in svgs]
    claim = SimpleNamespace(expected_revision=1, presentation_id=uuid.uuid4())
    bundle = build_svg_export_bundle(claim=claim, slide_refs=[ref.to_wire() for ref in refs],
        source_digests=[ref.sha256 for ref in refs], notes=[""] * len(svgs),
        canvas=canvas.model_dump(mode="json", by_alias=True), export_format="pptx", font_embedding=False,
        assets=[None] * len(svgs), source_preservation=receipts)
    ref = store.put(json.dumps(bundle).encode(), operation_id=operation_id, kind="export-bundle",
                    media_type="application/json")
    job = SimpleNamespace(input_artifact=ref, operation_id=operation_id, deadline_epoch_seconds=time.time() + 300)
    output = asyncio.run(execute_svg_export(job, store))
    return Presentation(BytesIO(store.read(output.artifact))), output.evidence


def test_a_flagged_source_deck_exports_through_the_worker(tmp_path):
    canvas, svgs, receipts = _deck()
    assert receipts[1]["rendering_verified"] is False and receipts[2] is None
    deck, evidence = _export(tmp_path, canvas, svgs, receipts)
    assert len(deck.slides) == 3
    readback = evidence["structuralReadback"]["sourcePreservation"]
    assert readback["passed"] is True
    assert readback["manifestDigest"] == receipts[0]["manifest_digest"]
    pages = readback["pages"]
    # Both source pages are read back exactly, the flagged one included.
    assert [page["sourcePageId"] for page in pages[:2]] == ["p0", "p1"]
    assert all(page["text"]["matchedBindingCount"] == 1 for page in pages[:2])
    # The placeholder is exported as drawn and claims no source page.
    assert pages[2] == {"sourcePageId": None, "sourcePageIndex": 2, "failurePlaceholder": True}
    texts = "".join(shape.text_frame.text for shape in iter_shapes(deck.slides[2].shapes)
                    if shape.has_text_frame)
    assert TEXTS[2].replace(" ", "") in texts.replace(" ", "").replace("\n", "")
    assert evidence["visualQualityVerified"] is False


@pytest.mark.parametrize("case", ["bytes", "receipt", "foreign_source"])
def test_a_receipt_that_does_not_bind_the_page_stays_fatal_in_the_worker(tmp_path, case):
    canvas, svgs, receipts = _deck()
    if case == "bytes":
        svgs[1] = svgs[1].replace(TEXTS[1], "요약된 페이지")
    elif case == "receipt":
        receipts[1] = {**receipts[1], "svg_digest": source_digest(svgs[0])}
    else:
        receipts[1] = {**receipts[1], "manifest_digest": "sha256:" + "9" * 64}
    with pytest.raises(PublicWorkerExecutionError):
        _export(tmp_path, canvas, svgs, receipts)


def test_a_placeholder_may_not_carry_a_source_claim_in_the_readback(tmp_path):
    canvas, svgs, receipts = _deck()
    deck, _ = _export(tmp_path, canvas, svgs, receipts)
    with pytest.raises(ValueError, match="svg_source_pptx_image_expectation_mismatch"):
        source_preservation_readback(deck, receipts, [{}, {}, {"slot": "0" * 64}])
    whole_page = dict(native_objects=[PageNativeObjects()] * 3, source_svgs=svgs,
                      ownership_claims=[[], [], []], fragment_claims=[[], [], []])
    with pytest.raises(ValueError, match="svg_source_pptx_placeholder_claims_source"):
        source_preservation_readback(deck, [None, None, None], [{}, {}, {}],
                                     **{**whole_page, "fragment_claims": [[], [], [{"sourceId": "x"}]]})


def test_whole_page_readback_binds_a_placeholder_to_its_own_bytes():
    canvas, svgs, _ = _deck()
    deck = Presentation()
    for _ in svgs:
        deck.slides.add_slide(deck.slide_layouts[6])
    readback = source_preservation_readback(deck, [None] * 3, [{}] * 3,
        native_objects=[PageNativeObjects()] * 3, source_svgs=svgs,
        ownership_claims=[[]] * 3, fragment_claims=[[]] * 3)
    assert [page["svgDigest"] for page in readback["pages"]] == [page_svg_digest(None, svg) for svg in svgs]
    assert readback["sourceDigest"] is None and readback["manifestDigest"] is None


# --------------------------------------------------------------------------
# renumbering: flaggedSlides follow the pages' new positions
# --------------------------------------------------------------------------


def _numbered_slide(index, svg, *, flag_index=None):
    from services.generation_core.quality.flag_classification import REVIEW_REQUIRED
    from services.generation_core.quality.terminal_slide_flag_only import flagged_entry
    quality = {"passed": True, "verified": flag_index is None, "failures": [], "measurement_complete": True,
               "source_digest": source_digest(svg),
               "classification": "passed" if flag_index is None else REVIEW_REQUIRED}
    receipt = {"quality": quality}
    if flag_index is not None:
        # Raised while the page sat at ``flag_index``.
        receipt["review_flag"] = flagged_entry(index=flag_index, reason="design:information_structure",
                                               attempted=False, outcome="not_attempted")
    return SimpleNamespace(id=uuid.uuid4(), index=index, svg_content=svg, is_svg=lambda: True,
                           content={"svgReceipt": receipt}, properties={})


def test_renumbering_rebuilds_flagged_slides_by_position(monkeypatch):
    from unittest.mock import AsyncMock
    from services import svg_document_mutations as mutations
    svgs = [f'<svg xmlns="http://www.w3.org/2000/svg"><text x="0" y="10">page {n}</text></svg>' for n in range(3)]
    # The flagged page was page 3 and is now page 1; a page was deleted.
    slides = [_numbered_slide(0, svgs[0], flag_index=2), _numbered_slide(1, svgs[1])]
    presentation = SimpleNamespace(id=uuid.uuid4(), is_svg=lambda: True, instructions=None, theme={"svg": {
        "styleSnapshot": {"tokens": {}}, "qualitySummary": {"status": "review_required"},
        "flaggedSlides": [{"index": 2, "reason": "design:information_structure", "slideId": "stale"}]}})
    session = SimpleNamespace(flush=AsyncMock(), scalars=AsyncMock(return_value=slides), add=lambda _row: None)
    monkeypatch.setattr(mutations, "presentation_canvas_contract",
                        lambda _theme: SimpleNamespace(width_px=1280, height_px=720))
    monkeypatch.setattr(mutations, "finalize_svg", lambda svg, **_kwargs: svg)
    asyncio.run(mutations.refresh_svg_page_numbers(session, presentation))
    svg_meta = presentation.theme["svg"]
    assert svg_meta["reviewPages"] == [0] and svg_meta["qualityStatus"] == "review_required"
    assert [(entry["index"], entry["slideId"]) for entry in svg_meta["flaggedSlides"]] == [(0, str(slides[0].id))]
    assert svg_meta["qualitySummary"]["pageCount"] == 2
    assert slides[0].content["svgReceipt"]["review_flag"]["index"] == 0


# --------------------------------------------------------------------------
# header revision: a flagged page keeps its body
# --------------------------------------------------------------------------


@pytest.mark.parametrize(("case", "reusable"), [
    ("clean", True), ("design_flag", True), ("terminal", False), ("stale", False), ("frame", False)])
def test_a_header_revision_reuses_a_flagged_page_body(case, reusable):
    from services.generation_core.quality.terminal_slide_flag_only import flagged_entry
    from services.workspace_svg_header_revision_inputs import reusable_body
    svg = '<svg xmlns="http://www.w3.org/2000/svg"><text x="0" y="10">body</text></svg>'
    quality = {"passed": True, "geometry_passed": True, "measurement_complete": True,
               "source_digest": source_digest(svg)}
    receipt = {"quality": quality}
    flag = flagged_entry(index=0, reason="design:density_balance", attempted=False, outcome="not_attempted")
    if case == "design_flag":
        receipt.update(review_flag=flag, flag={**flag, "classification": "review_required"})
        quality.update(verified=False, classification="review_required")
    elif case == "terminal":
        receipt["terminal"] = flagged_entry(index=0, reason="author:attempts_exhausted", attempted=False,
                                            outcome="svg_fallback_page")
    elif case == "stale":
        quality["source_digest"] = source_digest(svg + " ")
    old = {"body_box": {"x": 0, "y": 80}, "shared_style": {}}
    new = {"body_box": {"x": 0, "y": 96}, "shared_style": {}} if case == "frame" else dict(old)
    assert reusable_body(old, new, SimpleNamespace(svg_content=svg, content={"svgReceipt": receipt})) is reusable
