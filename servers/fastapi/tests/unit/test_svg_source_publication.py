import asyncio
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from services import svg_document_mutations as mutations
from services.svg_contract import source_digest


@pytest.mark.parametrize("mutation", [None, "reorder", "delete", "content", "marker", "receipt", "unverified"])
def test_source_publication_preserves_verified_svg_and_rejects_mutations(monkeypatch, mutation):
    canvas = SimpleNamespace(width_px=1280, height_px=720)
    marker = {"schemaVersion": "svg-source-preservation/v1", "manifestDigest": "sha256:" + "a" * 64,
              "pageCount": 2}
    presentation = SimpleNamespace(id="presentation", theme={"svg": {"sourcePreservation": marker}},
                                   is_svg=lambda: True)
    slides = []
    for index in range(2):
        svg = f'<svg xmlns="http://www.w3.org/2000/svg"><text>Original {index}</text></svg>'
        receipt = {"contract_version": "svg-source-preservation/v1", "manifest_digest": marker["manifestDigest"],
                   "source_page_index": index, "source_page_id": f"page-{index}", "page_count": 2,
                   "svg_digest": source_digest(svg), "canvas": {"width": 1280, "height": 720},
                   "rendering_verified": True,
                   "checks": {key: True for key in ("source_ids_exact", "text_exact", "table_structure_exact", "image_occurrences_exact")}}
        slides.append(SimpleNamespace(index=index, svg_content=svg, is_svg=lambda: True,
                                      content={"svgReceipt": {"source_preservation": receipt}}))
    originals = [(slide.svg_content, deepcopy(slide.content)) for slide in slides]
    if mutation == "reorder":
        slides.reverse()
        for index, slide in enumerate(slides):
            slide.index = index
    elif mutation == "delete":
        slides.pop()
    elif mutation == "content":
        slides[0].svg_content = slides[0].svg_content.replace("Original", "Changed")
    elif mutation == "marker":
        presentation.theme["svg"].pop("sourcePreservation")
    elif mutation == "receipt":
        slides[0].content = {}
    elif mutation == "unverified":
        slides[0].content["svgReceipt"]["source_preservation"]["rendering_verified"] = False
    session = SimpleNamespace(flush=AsyncMock(), scalars=AsyncMock(return_value=slides))
    monkeypatch.setattr(mutations, "presentation_canvas_contract", lambda _: canvas)

    def forbidden(*args, **kwargs):
        raise AssertionError("A verified source SVG must not be finalized, renumbered or rendered again")

    monkeypatch.setattr(mutations, "finalize_svg", forbidden)
    monkeypatch.setattr(mutations, "read_svg_slide_assets", forbidden)
    if mutation:
        with pytest.raises(ValueError, match="svg_source_publication_binding_invalid"):
            asyncio.run(mutations.refresh_svg_page_numbers(session, presentation))
    else:
        asyncio.run(mutations.refresh_svg_page_numbers(session, presentation))
        assert [(slide.svg_content, slide.content) for slide in slides] == originals
