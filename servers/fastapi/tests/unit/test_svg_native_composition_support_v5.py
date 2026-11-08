"""Portable native-pixel accounting; actual browser acceptance is separate."""
from __future__ import annotations

import base64
import hashlib
import xml.etree.ElementTree as ET
import zlib

from PIL import Image
import pytest

from utils.slide_capture_chart_paint import _canonical_canvas_matrix, _region_mask
from utils.slide_capture_chart_support import (
    encode_native_raster, encoded_mask_record, mask_record, resolve_chart_support,
)


def _pixels(values, size=None):
    image = Image.new("L", size or (len(values), 1))
    image.putdata(values)
    return image


@pytest.mark.parametrize("values", [[0], [255], [0, 1, 127, 128, 254, 255], [255, 0, 255, 0]])
def test_native_raster_encodes_actual_bytes_without_dimension_assumptions(values):
    image = _pixels(values)
    record = encode_native_raster(image)
    assert set(record) == {"encoding", "width", "height", "sha256", "dataBase64"}
    assert record["encoding"] == "zlib-base64-u8/v1"
    assert (record["width"], record["height"]) == image.size
    assert zlib.decompress(base64.b64decode(record["dataBase64"], validate=True)) == bytes(values)
    assert record["sha256"] == "sha256:" + hashlib.sha256(bytes(values)).hexdigest()


def test_encoded_native_mask_binds_pixels_and_native_alpha_statistics():
    mask, alpha = _pixels([255, 0, 255]), _pixels([17, 255, 203])
    record = encoded_mask_record(mask, alpha)
    assert set(record) == {"maskDigest", "pixelCount", "alphaSum", "encodedRaster"}
    assert record["pixelCount"] == 2 and record["alphaSum"] == 220
    assert record["maskDigest"] == record["encodedRaster"]["sha256"]


@pytest.mark.parametrize("mask,alpha", [
    (_pixels([1, 255]), _pixels([255, 255])),
    (_pixels([255]), _pixels([255, 255])),
    (Image.new("RGB", (1, 1)), _pixels([255])),
])
def test_encoded_mask_rejects_nonbinary_or_unbound_pixels(mask, alpha):
    with pytest.raises(ValueError):
        encoded_mask_record(mask, alpha)


def test_native_raster_rejects_color_and_empty_inputs():
    for image in (Image.new("RGB", (1, 1)), Image.new("L", (0, 1))):
        with pytest.raises(ValueError):
            encode_native_raster(image)


def test_canonical_projection_identity_preserves_actual_affine_layout():
    actual = [2, 0.5, -0.25, 3, 10, 20]
    assert _canonical_canvas_matrix(actual, (0, 0, 1)) == actual


def test_canonical_projection_removes_root_once_and_keeps_group_and_viewbox():
    from services.generation_core.drivers.svg.native_geometry import _transform_of

    root = ET.fromstring('<svg viewBox="10 20 100 50" transform="translate(5 7) scale(2)">'
                         '<g transform="translate(3 4) scale(1.5)"/></svg>')
    dx, dy, scale = _transform_of(root)
    gx, gy, gs = _transform_of(root[0])
    # The viewport maps the nonzero viewBox into 300 x 150 CSS pixels.
    viewport = [3, 0, 0, 3, -30, -60]
    raw = [3 * scale, 0, 0, 3 * scale, 3 * dx - 30, 3 * dy - 60]
    canonical = _canonical_canvas_matrix(raw, (dx, dy, scale))
    assert canonical == viewport
    group_point = (gx + gs * 4, gy + gs * 6)
    canonical_point = (dx + scale * group_point[0], dy + scale * group_point[1])
    expected_pixel = (raw[0] * group_point[0] + raw[4], raw[3] * group_point[1] + raw[5])
    assert (canonical[0] * canonical_point[0] + canonical[4],
            canonical[3] * canonical_point[1] + canonical[5]) == expected_pixel


def test_canonical_region_applies_device_scale_exactly_once():
    canonical = _canonical_canvas_matrix([2, 0, 0, 2, 5, 7], (5, 7, 2))
    mask = _region_mask((8, 2), {"categoryStrip": {"minimumX": 1, "maximumX": 2}}, canonical, 2)
    assert list(mask.getdata()) == [0, 0, 255, 255, 0, 0, 0, 0] * 2


@pytest.mark.parametrize("raw,root", [
    ([1, 0, 0, 1, 0], (0, 0, 1)),
    ([1, 0, 0, 1, 0, 0], (0, 0, 0)),
    ([1, 0, 0, 1, 0, 0], (0, 0, -1)),
    ([1, 0, 0, 1, float("nan"), 0], (0, 0, 1)),
    ([True, 0, 0, 1, 0, 0], (0, 0, 1)),
])
def test_canonical_projection_rejects_unproved_transform(raw, root):
    with pytest.raises(ValueError):
        _canonical_canvas_matrix(raw, root)


def _area_layers():
    chart_id = "area"
    datums, records, masks = [], [], {}
    zero = Image.new("L", (4, 4))
    ink = zero.copy()
    ink.putpixel((1, 1), 255)
    for series, qid in enumerate(("svg-1", "svg-2")):
        datum_id = f"{chart_id}/s{series}/c0"
        target_id = f"{chart_id}/primitive/{qid}/{datum_id}"
        support = {"anchor": {"x": 1.5, "y": 1.5},
                   "categoryStrip": {"minimumX": None, "maximumX": None}, "baseline": 2,
                   "axisKey": "axis", "unitKey": "unit", "geometryDigest": "sha256:" + str(series + 1) * 64,
                   "contributionRegions": [], "visibilityObligations": [{
                       "id": datum_id + "/anchor", "kind": "value-anchor", "primitiveQid": qid,
                       "regionRule": "native-alpha-boundary-neighborhood/v1", "radiusPhysicalPixels": 1,
                       "segments": [[[1.5, 1.5], [1.5, 1.5]]], "alternatives": []}]}
        datums.append({"id": datum_id, "seriesIndex": series, "categoryIndex": 0,
                       "primitiveQids": [qid], "geometryRole": "nonzero", "geometrySupport": support})
        raw = {"alpha": ink.copy(), "footprint": ink.copy(),
               "originalContribution": (ink if series else zero).copy(),
               "retained": (ink if series else zero).copy(), "ownedOverlap": (zero if series else ink).copy(),
               "altered": zero.copy(), "quantizedAtCapture": zero.copy(), "unproven": zero.copy(),
               "opaqueOwned": (zero if series else ink).copy(), "canvasMatrix": [1, 0, 0, 1, 0, 0],
               "deviceScaleFactor": 1}
        record = {"id": target_id, "chartId": chart_id, "qid": qid, "kind": "primitive",
                  "datumIds": [datum_id], "geometrySupport": support, "ownedRelationships": [],
                  "complete": bool(series), "paintRetained": bool(series), "unresolvedRanges": [], "clippedQids": [],
                  "reason": None if series else "original_color_semantic_contribution_empty"}
        for key in ("footprint", "originalContribution", "retained", "ownedOverlap", "altered", "quantizedAtCapture", "unproven"):
            record[key] = mask_record(raw[key], raw["alpha"])
        records.append(record)
        masks[target_id] = raw
    support = datums[0]["geometrySupport"]
    relation = {"kind": "native-series-layer-order", "datumId": datums[0]["id"],
                "targetQid": "svg-1", "occluderQid": "svg-2", "axisKey": "axis", "unitKey": "unit",
                "geometrySupportDigest": support["geometryDigest"],
                "geometrySupport": {k: support[k] for k in ("anchor", "categoryStrip", "baseline")},
                "regionRule": "native-alpha-intersection-in-datum-support/v1"}
    records[0]["ownedRelationships"] = [relation]
    layer = {"kind": "native-series-layer-order", "primitiveQids": ["svg-1", "svg-2"],
             "axisKey": "axis", "unitKey": "unit", "localOverlaps": [{k: v for k, v in relation.items() if k != "kind"}]}
    chart = {"id": chart_id, "chartType": "area", "datums": datums,
             "compositionGroup": {"relationships": [layer], "equivalenceClasses": []}}
    return chart, records, masks


def _refresh_record(record, raw):
    for key in ("footprint", "originalContribution", "retained", "ownedOverlap", "altered", "quantizedAtCapture", "unproven"):
        record[key] = mask_record(raw[key], raw["alpha"])


def test_area_hidden_datum_has_complete_native_preservation_without_claimed_visibility():
    chart, records, masks = _area_layers()
    result = resolve_chart_support(chart, records, masks, {})
    assert result["complete"] and result["paintRetained"]
    receipts = {r["targetId"]: r for r in result["nativeComposition"]["areaTargets"]}
    lower, upper = (receipts[r["id"]] for r in records)
    assert lower["visibility"] == "preserved-hidden-by-owned" and lower["sourceVisible"] is False
    assert lower["originalContribution"]["pixelCount"] == 0 and lower["hidden"]["pixelCount"] == 1
    assert lower["preserved"]["pixelCount"] == 1 and lower["residual"]["pixelCount"] == 0
    assert lower["occluders"][0]["targetId"] == records[1]["id"]
    assert upper["visibility"] == "visible-native-contribution" and upper["sourceVisible"] is True
    assert result["targets"][0]["obligations"][0]["via"] == "native-area-owned-layer-preservation/v1"


@pytest.mark.parametrize("mutation", ["foreign", "wrong-order", "wrong-geometry", "wrong-axis", "wrong-unit",
                                     "wrong-datum", "missing-local-overlap", "translucent", "matrix", "foreign-altered"])
def test_area_does_not_turn_unbound_or_incomplete_occlusion_into_preservation(mutation):
    chart, records, masks = _area_layers()
    relation = records[0]["ownedRelationships"][0]
    if mutation == "foreign":
        records[0]["ownedRelationships"] = []
    elif mutation == "wrong-order":
        relation["occluderQid"] = "svg-0"
    elif mutation == "wrong-geometry":
        relation["geometrySupportDigest"] = "sha256:" + "f" * 64
    elif mutation == "wrong-axis":
        chart["datums"][1]["geometrySupport"]["axisKey"] = "other"
    elif mutation == "wrong-unit":
        chart["datums"][1]["geometrySupport"]["unitKey"] = "other"
    elif mutation == "wrong-datum":
        relation["datumId"] = records[1]["datumIds"][0]
    elif mutation == "missing-local-overlap":
        chart["compositionGroup"]["relationships"][0]["localOverlaps"] = []
    elif mutation == "translucent":
        masks[records[1]["id"]]["alpha"].putpixel((1, 1), 254)
        _refresh_record(records[1], masks[records[1]["id"]])
    elif mutation == "matrix":
        masks[records[1]["id"]]["canvasMatrix"] = [1, 0, 0, 1, 1, 0]
    else:
        raw = masks[records[0]["id"]]
        raw["altered"] = raw["footprint"].copy()
        raw["ownedOverlap"] = Image.new("L", (4, 4))
        _refresh_record(records[0], raw)
    result = resolve_chart_support(chart, records, masks, {})
    assert result["complete"] is False and result["paintRetained"] is False


def test_area_resolution_is_transactional_when_later_support_demand_is_empty():
    chart, records, masks = _area_layers()
    chart["datums"][1]["geometrySupport"]["visibilityObligations"][0]["segments"] = [[[20, 20], [20, 20]]]
    result = resolve_chart_support(chart, records, masks, {})
    assert result["complete"] is False
    assert not result["targets"][0]["complete"]
    assert "nativeComposition" not in result or not result["nativeComposition"]["areaTargets"]


def _polar_pair():
    from utils.slide_capture_chart_support import native_composition_requests

    chart, records, masks = _area_layers()
    chart["chartType"] = "donut"
    for index, (datum, record) in enumerate(zip(chart["datums"], records, strict=False)):
        datum["seriesIndex"], datum["categoryIndex"] = 0, index
        datum["geometrySupport"]["visibilityObligations"][0]["kind"] = "polar-outer-arc"
        raw = masks[record["id"]]
        raw["alpha"].putpixel((1, 1), 112 if index == 0 else 98)
        raw["originalContribution"] = raw["footprint"].copy()
        raw["ownedOverlap"] = Image.new("L", (4, 4))
        raw["retained"] = raw["footprint"].copy() if index else Image.new("L", (4, 4))
        raw["altered"] = Image.new("L", (4, 4)) if index else raw["footprint"].copy()
        record.update(complete=True, paintRetained=bool(index), reason=None)
        _refresh_record(record, raw)
    relation = records[0]["ownedRelationships"][0]
    relation.update(kind="proved-polar-adjacency", neighborDatumId=chart["datums"][1]["id"],
                    neighborGeometrySupportDigest=chart["datums"][1]["geometrySupport"]["geometryDigest"],
                    polarTopology={"fillInteriorsDisjoint": True, "contourTopologyChecked": True,
                                   "sharedRadialSegments": [[[1.5, 0.5], [1.5, 2.5]]]})
    chart["compositionGroup"]["relationships"] = [{"kind": "proved-polar-adjacency",
        "localOverlaps": [{k: v for k, v in relation.items() if k != "kind"}]}]
    request, = native_composition_requests(chart, records, masks)
    ink = masks[records[0]["id"]]["footprint"]
    alpha = ink.copy()
    alpha.putpixel((1, 1), 200)
    original = Image.new("RGB", (4, 4), "white")
    original.putpixel((1, 1), (100, 120, 140))
    response = Image.new("RGB", (4, 4))
    response.putpixel((1, 1), (64, 64, 64))
    pair = {**request, "complete": True, "canvasMatrix": [1, 0, 0, 1, 0, 0], "deviceScaleFactor": 1,
            "nativeAlpha": alpha, "footprint": ink.copy(), "originalContribution": ink.copy(),
            "altered": Image.new("L", (4, 4)), "expectedResponse": response.copy(), "currentResponse": response.copy(),
            "referenceOriginal": original.copy(), "currentOriginal": original.copy(),
            "currentAbsent": Image.new("RGB", (4, 4), "white")}
    return chart, records, masks, {"nativeCompositionPairs": {request["id"]: pair}}, pair


def test_partial_alpha_polar_edge_requires_joint_original_color_and_causal_paint():
    from utils.slide_capture_chart_support import _resolve_native_pairs

    chart, records, masks, evidence, _ = _polar_pair()
    receipts, _, _ = _resolve_native_pairs(chart, records, masks, evidence)
    assert len(receipts) == 1
    assert receipts[0]["regionDemand"]["pixelCount"] == 1
    assert receipts[0]["coveredRegion"]["pixelCount"] == 1
    assert records[0]["altered"]["pixelCount"] == 0
    assert records[0]["ownedOverlap"]["pixelCount"] == 1


@pytest.mark.parametrize("mutation", ["same-color-foreign-attenuation", "recolor", "missing-contribution",
                                     "wrong-pair", "wrong-matrix", "missing-source-relation"])
def test_polar_joint_does_not_launder_foreign_or_unbound_pixels(mutation):
    from utils.slide_capture_chart_support import _resolve_native_pairs

    chart, records, masks, evidence, pair = _polar_pair()
    if mutation == "same-color-foreign-attenuation":
        pair["currentResponse"].putpixel((1, 1), (63, 63, 63))
        pair["altered"] = pair["footprint"].copy()
    elif mutation == "recolor":
        pair["currentOriginal"].putpixel((1, 1), (101, 120, 140))
        pair["altered"] = pair["footprint"].copy()
    elif mutation == "missing-contribution":
        pair["currentAbsent"] = pair["currentOriginal"].copy()
        pair["originalContribution"] = Image.new("L", (4, 4))
    elif mutation == "wrong-pair":
        pair["partnerTargetId"] = records[0]["id"]
    elif mutation == "wrong-matrix":
        pair["canvasMatrix"] = [1, 0, 0, 1, 1, 0]
    else:
        chart["compositionGroup"]["relationships"][0]["localOverlaps"] = []
    receipts, _, _ = _resolve_native_pairs(chart, records, masks, evidence)
    assert receipts == []
    assert records[0]["altered"]["pixelCount"] == 1
    assert records[0]["ownedOverlap"]["pixelCount"] == 0


def test_polar_pair_cannot_waive_uncovered_target_pixels_but_ignores_external_pair_aa():
    from utils.slide_capture_chart_support import _resolve_native_pairs

    chart, records, masks, evidence, pair = _polar_pair()
    raw = masks[records[0]["id"]]
    # A separately retained source-over pixel is outside the typed radial patch.
    for key in ("footprint", "originalContribution", "retained"):
        raw[key].putpixel((3, 3), 255)
    raw["alpha"].putpixel((3, 3), 112)
    _refresh_record(records[0], raw)
    pair["nativeAlpha"].putpixel((3, 3), 112)
    for key in ("footprint", "originalContribution", "altered"):
        pair[key].putpixel((3, 3), 255)
    pair["currentOriginal"].putpixel((3, 3), (100, 120, 140))
    pair["referenceOriginal"].putpixel((3, 3), (99, 120, 140))
    receipts, _, _ = _resolve_native_pairs(chart, records, masks, evidence)
    assert len(receipts) == 1 and records[0]["altered"]["pixelCount"] == 0
    assert records[0]["retained"]["pixelCount"] == 1

    chart, records, masks, evidence, pair = _polar_pair()
    raw = masks[records[0]["id"]]
    for key in ("footprint", "originalContribution", "altered"):
        raw[key].putpixel((3, 3), 255)
    raw["alpha"].putpixel((3, 3), 112)
    _refresh_record(records[0], raw)
    receipts, _, _ = _resolve_native_pairs(chart, records, masks, evidence)
    assert receipts == [] and records[0]["altered"]["pixelCount"] == 2
