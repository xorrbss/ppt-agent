"""Digest-bound publication for ORM pages, constructors and CAS mappings."""
from copy import deepcopy
import json
from types import SimpleNamespace

import pytest

from services.svg_contract import source_digest
from services.svg_export_page_evidence import svg_authoring, svg_geometry_font
from services.svg_page_publication import set_svg_page
from utils.hashing import sha256_hex


@pytest.mark.parametrize("mapping", [False, True])
def test_matching_receipt_is_preserved_byte_for_byte(mapping):
    svg = '<svg><text>unchanged é</text></svg>'
    content = {"source": "original", "svgReceipt": {
        "source_digest": sha256_hex(svg.encode()),
        "quality": {"source_digest": source_digest(svg), "passed": True},
        "attempts": ["retained"],
    }}
    original = deepcopy(content)
    target = {} if mapping else SimpleNamespace()
    set_svg_page(target, svg_content=svg, content=content)
    stored = target if mapping else vars(target)
    assert stored == {"svg_content": svg, "content": original}
    assert json.dumps(stored["content"], ensure_ascii=False).encode() == (
        json.dumps(original, ensure_ascii=False).encode())
    assert content == original


@pytest.mark.parametrize("mapping", [False, True])
def test_digest_mismatch_is_rejected_before_either_field_is_written(mapping):
    fields = {"svg_content": "old", "content": {"source": "keep"}}
    target = deepcopy(fields) if mapping else SimpleNamespace(**deepcopy(fields))
    with pytest.raises(ValueError, match="svg_page_measurement_digest_mismatch"):
        set_svg_page(target, svg_content="new", content={"svgReceipt": {
            "quality": {"source_digest": source_digest("old")}}})
    assert (target if mapping else vars(target)) == fields


def test_fresh_quality_replaces_stale_quality_and_binds_both_digests():
    fields = {}
    set_svg_page(fields, svg_content="new", content={"svgReceipt": {
        "quality": {"source_digest": source_digest("old")}, "source_digest": "old"}},
        quality={"source_digest": source_digest("new"), "passed": False})
    receipt = fields["content"]["svgReceipt"]
    assert receipt["source_digest"] == sha256_hex(b"new")
    assert receipt["quality"] == {"source_digest": source_digest("new"), "passed": False}


def test_explicit_unmeasured_marker_is_accepted_and_export_flags_missing():
    slide = SimpleNamespace()
    set_svg_page(slide, svg_content="<svg/>", content={"svgReceipt": {}},
                 quality={"measurement_status": "unmeasured", "passed": False})
    assert "source_digest" not in slide.content["svgReceipt"]["quality"]
    for checker, domain in ((svg_authoring, "authoring"), (svg_geometry_font, "geometry_font")):
        assert checker([slide]) == {"required": True, "status": "failed",
                                    "reasonCode": f"svg_{domain}_evidence_missing"}


@pytest.mark.parametrize("quality", [None, {}, {"measurement_status": "unmeasured", "source_digest": "old"},
                                    {"measurement_status": "unmeasured", "measurement_complete": True}])
def test_missing_or_fabricated_measurement_is_rejected(quality):
    with pytest.raises(ValueError):
        set_svg_page({}, svg_content="<svg/>", content={"svgReceipt": {"quality": quality}})
