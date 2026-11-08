"""Receipt-consumer attacks; synthetic carriers are not browser observations."""
from copy import deepcopy
from dataclasses import replace
import base64
import hashlib
import json
import os
from pathlib import Path
import struct
from types import SimpleNamespace
import zlib

import pytest

from services.generation_core.drivers.svg.chart_content_paint import (
    _alternative_support, _direct_support_graphs, _glyphs, _targets, _text_projection,
    validate_chart_content_paint as _validate_paint,
)

_SVG = '<svg xmlns="http://www.w3.org/2000/svg"><g><rect x="191" y="230" width="18" height="120"/><text>30</text></g></svg>'
_WRAPPER = f"<html><body>{_SVG}</body></html>"
_TREE = [{"qid": "svg-0", "parentQid": None, "tag": "{http://www.w3.org/2000/svg}svg", "attributes": {}, "text": "", "tail": ""},
         {"qid": "svg-1", "parentQid": "svg-0", "tag": "{http://www.w3.org/2000/svg}g", "attributes": {}, "text": "", "tail": ""},
         {"qid": "svg-2", "parentQid": "svg-1", "tag": "{http://www.w3.org/2000/svg}rect",
          "attributes": {"x": "191", "y": "230", "width": "18", "height": "120"}, "text": "", "tail": ""},
         {"qid": "svg-3", "parentQid": "svg-1", "tag": "{http://www.w3.org/2000/svg}text", "attributes": {}, "text": "30", "tail": ""}]
_DEFAULT_CAPTURE = object()


def _png(width=1280, height=720):
    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xffffffff)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress((b"\x00" + b"\xff" * (width * 3)) * height)) + chunk(b"IEND", b""))


_PNG = _png()


def _capture(**overrides):
    return SimpleNamespace(**{"png": _PNG, "wrapper_html": _WRAPPER, "source_digest": _hash(_SVG), **overrides})


def validate_chart_content_paint(manifest, quality, *, capture=_DEFAULT_CAPTURE):
    return _validate_paint(manifest, quality, capture=_capture() if capture is _DEFAULT_CAPTURE else capture)


def _hash(value):
    return "sha256:" + hashlib.sha256(value.encode("utf-8")).hexdigest()


def _canonical(value):
    return _hash(json.dumps(value, allow_nan=False, sort_keys=True, separators=(",", ":"), ensure_ascii=False))


def _projection():
    return [{"ownerQid": "svg-3", "textRootQid": "svg-3", "rootTextDigest": _hash("30"),
             "ownedTextDigest": _hash("30"), "segments": [
                 {"rootStartUtf16": 0, "rootEndUtf16": 2, "ownedStartUtf16": 0}]}]


def _seal(manifest, quality):
    manifest["manifestDigest"] = _canonical({key: value for key, value in manifest.items()
                                             if key not in {"manifestDigest", "captureContext"}})
    metrics = quality["browser_metrics"]
    proof = metrics["nativeChartPaintProof"]
    proof["manifestDigest"] = manifest["manifestDigest"]
    portable = json.loads(json.dumps(metrics).replace("\\u0000", "\\u241f"))
    proof["baseMetricsDigest"] = _hash(json.dumps({key: value for key, value in portable.items()
                                                   if key != "nativeChartPaintProof"}, sort_keys=True,
                                                  separators=(",", ":"), ensure_ascii=False, default=str))
    quality["metrics_digest"] = _hash(json.dumps(metrics, sort_keys=True, separators=(",", ":"),
                                                 ensure_ascii=False, default=str))


def _mask(count, alpha=None):
    return {"maskDigest": _hash(f"synthetic-mask:{count}"), "pixelCount": count,
            "alphaSum": count * 255 if alpha is None else alpha}


def _paint():
    return {"complete": True, "paintRetained": True, "footprint": _mask(8), "retained": _mask(8),
            **{key: _mask(0) for key in ("altered", "ownedOverlap", "quantizedAtCapture", "unproven")}}


def _glyph(start, value):
    end = start + len(value.encode("utf-16-le")) // 2
    members = [{"qid": "svg-3", "startUtf16": start, "endUtf16": end}]
    return {"startUtf16": start, "endUtf16": end, "members": members, "ownedIntersection": deepcopy(members),
            "sharedBoundary": False, "membershipComplete": True, "authorizationComplete": True,
            "textDigest": _hash(value), "complete": True, "observableReferencePixels": 4,
            "originalContribution": _mask(4), **_mask(4)}


def _fixture():
    """Actual per-datum v1 shape; support-obligation receipt remains unproven."""
    datum = "chart/s0/c0"
    manifest = {"schema": "svg-native-chart-witness/v1", "requirementsRevision": "svg-native-requirements/v5",
                "canonicalSvgDigest": _hash(_SVG), "treeDerivativeDigest": _canonical({"schema": "svg-chart-node-tree/v1", "nodes": _TREE}),
                "nodeBindings": [], "charts": [{"id": "chart", "chartType": "column", "groupQid": "svg-1",
                    "datums": [{"id": datum, "seriesIndex": 0, "categoryIndex": 0, "value": "30",
                                "primitiveQids": ["svg-2"], "geometryRole": "bar-profile",
                                "geometrySupport": {"anchor": {"x": 200, "y": 230}, "baseline": 350}}],
                    "glyphs": [{"qid": "svg-3", "ownedTextDigest": _hash("30"), "role": "value",
                                "ranges": [{"start": 0, "length": 2}], "datumIds": [datum]}],
                    "compositionGroup": {"id": "chart/composition", "primitiveQids": ["svg-2"],
                        "relationships": [{"kind": "value-on-datum", "datumId": datum, "qid": "svg-3",
                                           "start": 0, "length": 2}]}}]}
    primitive = {"id": "chart/primitive/svg-2/chart/s0/c0", "chartId": "chart", "qid": "svg-2", "kind": "primitive",
                 "datumIds": [datum], "ownedGlyphRanges": [{"qid": "svg-3", "start": 0, "length": 2}],
                 "geometrySupport": deepcopy(manifest["charts"][0]["datums"][0]["geometrySupport"]),
                 "ownedRelationships": [], "ownedQids": [], "ownedPaints": {}, "jointPrimitiveFootprint": _mask(8),
                 "regionRule": "canonical-canvas-pixel-center-inclusive/v1", **_paint()}
    glyph = {"id": "chart/glyph/svg-3/0/2", "chartId": "chart", "qid": "svg-3", "kind": "glyph-range",
             "startUtf16": 0, "endUtf16": 2, "offsetScope": "owned-text-nodes", "ownedTextDigest": _hash("30"),
             "roles": ["value"], "datumIds": [datum], **_paint(), "glyphs": [_glyph(0, "3"), _glyph(1, "0")],
             "fontEvidence": {"complete": True, "runs": [
                 {"utf16Length": 2, "faces": [{"postScriptName": "SyntheticFace", "fontDigest": _hash("font"),
                                              "glyphCount": 2, "isCustomFont": False}]}]}}
    for target in (primitive, glyph):
        target.update(unresolvedRanges=[], foreignOccluderQids=[], clippedQids=[], originalContribution=_mask(8))
    target_ids = [primitive["id"], glyph["id"]]
    proof = {"schema": "svg-native-chart-paint-proof/v1", "probeVersion": "svg-chart-native-alpha-dual-paint/v1",
             "paintDomain": "captured-viewport/v1",
             "complete": True, "paintRetained": True, "canonicalSvgDigest": manifest["canonicalSvgDigest"],
             "treeDerivativeDigest": manifest["treeDerivativeDigest"], "materialIdentities": {"embeddedResources": []},
             "wrapperDigest": _hash(_WRAPPER), "pngDigest": "sha256:" + hashlib.sha256(_PNG).hexdigest(), "admittedRenderDigest": _hash("render"),
             "captureContextDigest": _canonical({"schema": "svg-chart-capture-context/v1",
                 "treeDerivativeDigest": manifest["treeDerivativeDigest"], "wrapperDigest": _hash(_WRAPPER),
                 "viewport": {"cssWidth": 1280, "cssHeight": 720}, "textProjection": _projection()}),
             "viewport": {"cssWidth": 1280, "cssHeight": 720, "dpr": 1, "rasterWidth": 1280, "rasterHeight": 720},
             "targets": [primitive, glyph], "charts": [{"id": "chart", "chartType": "column", "targetIds": target_ids,
                 "complete": True, "paintRetained": True, "datums": [{"id": datum, "seriesIndex": 0,
                     "categoryIndex": 0, "geometryRole": "bar-profile", "primitivePaintExpected": True,
                     "geometrySupport": deepcopy(manifest["charts"][0]["datums"][0]["geometrySupport"]),
                     "targetIds": target_ids[:], "complete": True, "paintRetained": True}]}]}
    quality = {"source_digest": manifest["canonicalSvgDigest"], "wrapper_digest": proof["wrapperDigest"],
               "png_digest": proof["pngDigest"], "browser_metrics": {"width": 1280, "height": 720,
                   "source_digest": proof["admittedRenderDigest"], "render_digest": proof["admittedRenderDigest"],
                   "fonts_ready": True, "nativeChartPaintProof": proof}}
    _seal(manifest, quality)
    return manifest, quality


def _proof(quality):
    return quality["browser_metrics"]["nativeChartPaintProof"]


def _with_direct_graph(manifest, quality):
    """Add the current producer's exact own-original obligation result shape."""
    datum, proof = manifest["charts"][0]["datums"][0], _proof(quality)
    support = datum["geometrySupport"]
    support.update(axisKey=_hash("axis"), unitKey="", contributionRegions=[], visibilityObligations=[
        {"id": datum["id"] + "/" + kind, "kind": kind, "primitiveQid": "svg-2",
         "regionRule": "native-alpha-boundary-neighborhood/v1", "radiusPhysicalPixels": 1,
         "segments": [[[191, y], [209, y]]], "alternatives": []}
        for kind, y in [("value-end", 230), ("value-base", 350)]])
    support["geometryDigest"] = _canonical(support)
    primitive, glyph = proof["targets"]
    primitive["geometrySupport"] = deepcopy(support)
    proof["charts"][0]["datums"][0]["geometrySupport"] = deepcopy(support)
    primitive["contributionRegions"] = []
    glyph["contributionRegions"] = []
    primitive["visibilityObligations"] = [{"supportId": item["id"], "complete": True, "paintRetained": True,
        "via": "original-boundary-contribution", "reason": None, "boundaryPixels": 2, "supportedPixels": 2,
        "footprint": _mask(2), "originalContribution": _mask(2)} for item in support["visibilityObligations"]]
    glyph["visibilityObligations"] = []
    proof["supportGraphs"] = [{"complete": True, "paintRetained": True, "equivalenceClasses": [], "unproven": [],
        "targets": [{"targetId": record["id"], "complete": True, "paintRetained": True, "reason": None,
                     "obligations": deepcopy(record["visibilityObligations"])} for record in proof["targets"]]}]
    _seal(manifest, quality)
    return manifest, quality


def test_synthetic_own_original_success_requires_every_exact_advertised_boundary_receipt():
    manifest, quality = _with_direct_graph(*_fixture())
    result = validate_chart_content_paint(manifest, quality)
    assert result["passed"] is True
    assert result["scope"] == "emitted-native-raster-composition"
    assert result["physicalExposureVerified"] is False
    assert result["perClusterFontAttributionVerified"] is False


@pytest.mark.parametrize("attack", ["missing", "extra", "identity", "zero", "count", "complete", "unchecked-alternative", "mirror"])
def test_whole_target_ink_cannot_replace_a_missing_false_or_unbound_boundary_obligation(attack):
    manifest, quality = _with_direct_graph(*_fixture())
    proof = _proof(quality)
    direct = proof["targets"][0]["visibilityObligations"]
    if attack == "missing":
        direct.pop()
    elif attack == "extra":
        direct.append(deepcopy(direct[0]))
    elif attack == "identity":
        direct[0]["supportId"] = "different-datum/value-end"
    elif attack == "zero":
        direct[0].update(originalContribution=_mask(0), supportedPixels=0)
    elif attack == "count":
        direct[0]["supportedPixels"] = 3
    elif attack == "complete":
        direct[0]["complete"] = 1
    elif attack == "unchecked-alternative":
        direct[0]["via"] = "equivalent-native-support"
        direct[0]["classId"] = "unbound-class"
    if attack != "mirror":
        proof["supportGraphs"][0]["targets"][0]["obligations"] = deepcopy(direct)
    else:
        proof["supportGraphs"][0]["targets"][0]["obligations"][0]["supportedPixels"] = 3
    _seal(manifest, quality)
    assert validate_chart_content_paint(manifest, quality)["passed"] is False


def test_bound_own_original_bar_and_glyph_receipt_still_needs_support_obligation_satisfaction():
    manifest, quality = _fixture()
    result = validate_chart_content_paint(manifest, quality)
    assert result == {"passed": False, "reason": "chart_content_paint_support_graph_unproven",
                      "receiptBindingVerified": True, "captureContextVerified": True,
                      "perClusterFontAttributionVerified": False, "fontAttributionScope": "selected-target-aggregate"}


@pytest.mark.parametrize("field", ["complete", "paintRetained"])
@pytest.mark.parametrize("value", [False, 1, "true", None])
def test_top_level_completeness_and_retention_require_distinct_literal_booleans(field, value):
    manifest, quality = _fixture()
    _proof(quality)[field] = value
    _seal(manifest, quality)
    result = validate_chart_content_paint(manifest, quality)
    assert not result["passed"]
    assert "receiptBindingVerified" not in result


@pytest.mark.parametrize("attack", ["manifest", "source", "wrapper", "png", "render", "base_metrics", "metrics", "context"])
def test_each_capture_identity_link_is_required(attack):
    manifest, quality = _fixture()
    proof = _proof(quality)
    if attack == "manifest":
        manifest["charts"][0]["datums"][0]["value"] = "31"
    elif attack == "source":
        quality["source_digest"] = _hash("another-source")
    elif attack == "wrapper":
        quality["wrapper_digest"] = _hash("another-wrapper")
    elif attack == "png":
        quality["png_digest"] = _hash("another-png")
    elif attack == "render":
        proof["admittedRenderDigest"] = _hash("another-render")
        _seal(manifest, quality)
    elif attack == "base_metrics":
        proof["baseMetricsDigest"] = _hash("unbound-metrics")
        quality["metrics_digest"] = _hash(json.dumps(quality["browser_metrics"], sort_keys=True,
                                                      separators=(",", ":"), ensure_ascii=False))
    elif attack == "metrics":
        proof["targets"][0]["retained"]["maskDigest"] = _hash("different-mask")
    else:
        proof["captureContextDigest"] = _canonical(None)
        _seal(manifest, quality)
    result = validate_chart_content_paint(manifest, quality)
    assert not result["passed"]
    assert "receiptBindingVerified" not in result


@pytest.mark.parametrize("attack", ["width", "height", "dpr", "raster", "nan", "boolean"])
def test_viewport_is_bound_to_metrics_and_finite_coherent_raster(attack):
    manifest, quality = _fixture()
    viewport = _proof(quality)["viewport"]
    if attack in {"width", "height"}:
        viewport["cssWidth" if attack == "width" else "cssHeight"] += 1
    elif attack == "dpr":
        viewport["dpr"] = 2
    elif attack == "raster":
        viewport["rasterHeight"] = 720.5
    elif attack == "boolean":
        viewport["dpr"] = True
    else:
        viewport["dpr"] = float("nan")
    if attack != "nan":
        _seal(manifest, quality)
    result = validate_chart_content_paint(manifest, quality)
    assert not result["passed"]
    assert "receiptBindingVerified" not in result


@pytest.mark.parametrize("attack", ["order", "qid", "datum", "range", "role", "owned", "extra", "chart", "datum_index"])
def test_targets_and_chart_datum_associations_match_exactly(attack):
    manifest, quality = _fixture()
    proof = _proof(quality)
    if attack == "order":
        proof["targets"].reverse()
    elif attack == "qid":
        proof["targets"][0]["qid"] = "svg-999"
    elif attack == "datum":
        proof["targets"][0]["datumIds"] = []
    elif attack == "range":
        proof["targets"][1]["endUtf16"] = 3
    elif attack == "role":
        proof["targets"][1]["roles"] = ["category"]
    elif attack == "owned":
        proof["targets"][0]["ownedQids"] = ["svg-20"]
    elif attack == "extra":
        proof["targets"].append(deepcopy(proof["targets"][0]))
    elif attack == "chart":
        proof["charts"][0]["targetIds"].reverse()
    else:
        proof["charts"][0]["datums"][0]["seriesIndex"] = False
    _seal(manifest, quality)
    result = validate_chart_content_paint(manifest, quality)
    assert not result["passed"]
    assert "receiptBindingVerified" not in result


@pytest.mark.parametrize("attack", ["lost_pixel", "lost_alpha", "alpha_bound", "float", "bool", "negative", "hash", "altered", "unproven"])
def test_mask_partition_counts_alpha_and_zero_loss_are_strict(attack):
    manifest, quality = _fixture()
    record = _proof(quality)["targets"][0]
    if attack == "lost_pixel":
        record["retained"]["pixelCount"] -= 1
        record["retained"]["alphaSum"] -= 255
    elif attack == "lost_alpha":
        record["retained"]["alphaSum"] -= 1
    elif attack == "alpha_bound":
        record["footprint"]["alphaSum"] = 8 * 256
    elif attack in {"float", "bool", "negative"}:
        record["retained"]["pixelCount"] = {"float": 8.0, "bool": True, "negative": -1}[attack]
    elif attack == "hash":
        record["retained"]["maskDigest"] = "0" * 64
    else:
        record[attack] = _mask(1)
        record["retained"] = _mask(7)
    _seal(manifest, quality)
    result = validate_chart_content_paint(manifest, quality)
    assert not result["passed"]
    assert "receiptBindingVerified" not in result


@pytest.mark.parametrize("attack", ["gap", "overlap", "last", "ink", "response", "font", "font_digest", "run_length", "complete"])
def test_every_demanded_cluster_has_gap_free_utf16_ink_response_and_font_evidence(attack):
    manifest, quality = _fixture()
    target = _proof(quality)["targets"][1]
    if attack == "gap":
        target["glyphs"] = target["glyphs"][1:]
    elif attack == "overlap":
        target["glyphs"][1]["startUtf16"] = 0
    elif attack == "last":
        target["glyphs"] = target["glyphs"][:1]
    elif attack == "ink":
        target["glyphs"][1].update(_mask(0), observableReferencePixels=0)
    elif attack == "response":
        target["glyphs"][1]["observableReferencePixels"] = 0
    elif attack == "font":
        target["fontEvidence"]["runs"][0]["faces"] = []
    elif attack == "font_digest":
        target["fontEvidence"]["runs"][0]["faces"][0]["fontDigest"] = "unknown"
    elif attack == "run_length":
        target["fontEvidence"]["runs"][0]["utf16Length"] = 1
    else:
        target["glyphs"][1]["complete"] = 1
    _seal(manifest, quality)
    result = validate_chart_content_paint(manifest, quality)
    assert not result["passed"]
    assert "receiptBindingVerified" not in result


def test_quantization_classification_cannot_become_retained_original_color_paint():
    manifest, quality = _fixture()
    target = _proof(quality)["targets"][0]
    target["retained"], target["quantizedAtCapture"] = _mask(0), _mask(8)
    _seal(manifest, quality)
    result = validate_chart_content_paint(manifest, quality)
    assert result["passed"] is False
    assert result["reason"] == "chart_content_paint_support_graph_unproven"


def test_bound_datum_local_line_targets_still_need_each_boundary_obligation_satisfied():
    manifest, quality = _fixture()
    chart, proof = manifest["charts"][0], _proof(quality)
    chart["chartType"], proof["charts"][0]["chartType"] = "line", "line"
    first = chart["datums"][0]
    first["geometryRole"] = "line-trace"
    first["geometrySupport"]["categoryStrip"] = {"minimumX": None, "maximumX": 300}
    second = deepcopy(first)
    second.update(id="chart/s0/c1", categoryIndex=1, value="60")
    second["geometrySupport"] = {"anchor": {"x": 400, "y": 110},
                                  "categoryStrip": {"minimumX": 300, "maximumX": None}}
    chart["datums"].append(second)
    chart["glyphs"][0]["datumIds"].append(second["id"])
    first_target, glyph_target = proof["targets"]
    first_target["geometrySupport"] = deepcopy(first["geometrySupport"])
    second_target = deepcopy(first_target)
    second_target.update(id="chart/primitive/svg-2/chart/s0/c1", datumIds=[second["id"]],
                         geometrySupport=deepcopy(second["geometrySupport"]), ownedGlyphRanges=[])
    glyph_target["datumIds"].append(second["id"])
    proof["targets"] = [first_target, second_target, glyph_target]
    proof["charts"][0]["targetIds"] = [item["id"] for item in proof["targets"]]
    first_row = proof["charts"][0]["datums"][0]
    first_row["geometryRole"] = "line-trace"
    first_row["geometrySupport"] = deepcopy(first["geometrySupport"])
    second_row = deepcopy(first_row)
    second_row.update(id=second["id"], categoryIndex=1, geometrySupport=deepcopy(second["geometrySupport"]),
                      targetIds=[second_target["id"], glyph_target["id"]])
    proof["charts"][0]["datums"].append(second_row)
    _seal(manifest, quality)
    result = validate_chart_content_paint(manifest, quality)
    assert not result["passed"]
    assert result["reason"] == "chart_content_paint_support_graph_unproven"
    assert result["receiptBindingVerified"] is True


def test_nul_normalization_matches_base_metrics_without_changing_containing_metrics_encoder():
    manifest, quality = _fixture()
    quality["browser_metrics"]["metrics_cache_key"] = "first\x00second"
    _seal(manifest, quality)
    assert validate_chart_content_paint(manifest, quality).get("receiptBindingVerified") is True


@pytest.mark.parametrize("mismatch", [False, True])
def test_available_private_context_is_bound_but_does_not_claim_verification_of_wrapper_bytes(mismatch):
    manifest, quality = _fixture()
    context = {"schema": "svg-chart-capture-context/v1", "treeDerivativeDigest": manifest["treeDerivativeDigest"],
               "wrapperDigest": quality["wrapper_digest"], "viewport": {"cssWidth": 1280, "cssHeight": 720},
               "textProjection": _projection()}
    manifest["captureContext"] = context
    _proof(quality)["captureContextDigest"] = _canonical(context)
    if mismatch:
        context["wrapperDigest"] = _hash("wrong-wrapper")
    _seal(manifest, quality)
    result = validate_chart_content_paint(manifest, quality)
    assert not result["passed"]
    assert result.get("receiptBindingVerified", False) is not mismatch


def test_no_renderer_receipt_never_falls_back_to_manifest_geometry():
    manifest, quality = _fixture()
    quality["browser_metrics"].pop("nativeChartPaintProof")
    assert validate_chart_content_paint(manifest, quality)["passed"] is False


@pytest.mark.parametrize("attack", ["missing", "png", "wrapper", "source", "raster", "png_crc", "context", "tree"])
def test_actual_capture_bytes_and_runtime_context_cannot_be_replaced_by_matching_carrier_hashes(attack):
    manifest, quality = _fixture()
    capture, proof = _capture(), _proof(quality)
    if attack == "missing":
        capture = None
    elif attack == "png":
        capture.png += b"different-emitted-bytes"
    elif attack == "wrapper":
        capture.wrapper_html = capture.wrapper_html.replace('height="120"', 'height="121"')
    elif attack == "source":
        capture.source_digest = _hash("different-canonical-source")
    elif attack in {"raster", "png_crc"}:
        capture.png = _png(640, 360) if attack == "raster" else _PNG[:29] + b"\x00\x00\x00\x00" + _PNG[33:]
        digest = "sha256:" + hashlib.sha256(capture.png).hexdigest()
        quality["png_digest"] = proof["pngDigest"] = digest
    elif attack == "context":
        proof["captureContextDigest"] = _hash("arbitrary-runtime-context")
    else:
        # Rebinding every wrapper field still cannot reuse the old runtime tree.
        capture.wrapper_html = capture.wrapper_html.replace('height="120"', 'height="121"')
        digest = _hash(capture.wrapper_html)
        quality["wrapper_digest"] = proof["wrapperDigest"] = digest
        proof["captureContextDigest"] = _canonical({"schema": "svg-chart-capture-context/v1",
            "treeDerivativeDigest": manifest["treeDerivativeDigest"], "wrapperDigest": digest,
            "viewport": {"cssWidth": 1280, "cssHeight": 720}, "textProjection": _projection()})
    _seal(manifest, quality)
    result = validate_chart_content_paint(manifest, quality, capture=capture)
    assert not result["passed"]
    assert "receiptBindingVerified" not in result


def test_actual_png_dimensions_derive_a_coherent_fractional_dpr():
    manifest, quality = _fixture()
    capture, proof = _capture(png=_png(640, 360)), _proof(quality)
    proof["viewport"].update(dpr=.5, rasterWidth=640, rasterHeight=360)
    quality["png_digest"] = proof["pngDigest"] = "sha256:" + hashlib.sha256(capture.png).hexdigest()
    _seal(manifest, quality)
    result = validate_chart_content_paint(manifest, quality, capture=capture)
    assert result["passed"] is False
    assert result["receiptBindingVerified"] is True
    assert result["captureContextVerified"] is True


@pytest.mark.parametrize("scope", ["primitive", "glyph-target", "glyph-cluster"])
def test_potential_dual_response_cannot_excuse_missing_original_color_contribution(scope):
    manifest, quality = _fixture()
    proof = _proof(quality)
    target = proof["targets"][0] if scope == "primitive" else proof["targets"][1]
    if scope == "glyph-cluster":
        target = target["glyphs"][1]
    target["originalContribution"] = _mask(0)
    _seal(manifest, quality)
    result = validate_chart_content_paint(manifest, quality)
    assert not result["passed"]
    if scope != "primitive":
        assert "receiptBindingVerified" not in result


@pytest.mark.parametrize("attack", ["foreign", "partial", "membership", "authorization", "gap", "shared_flag"])
def test_joint_shaping_members_must_be_complete_exact_and_fully_authorized(attack):
    manifest, quality = _fixture()
    glyph = _proof(quality)["targets"][1]["glyphs"][1]
    if attack == "foreign":
        glyph["members"][0]["qid"] = "svg-20"
    elif attack == "partial":
        glyph["ownedIntersection"] = []
    elif attack == "membership":
        glyph["membershipComplete"] = False
    elif attack == "authorization":
        glyph["authorizationComplete"] = 1
    elif attack == "shared_flag":
        glyph.pop("sharedBoundary")
    else:
        glyph["members"][0]["startUtf16"] = 0
        glyph["ownedIntersection"] = deepcopy(glyph["members"])
    _seal(manifest, quality)
    result = validate_chart_content_paint(manifest, quality)
    assert not result["passed"]
    assert "receiptBindingVerified" not in result


def test_joint_glyph_can_have_multiple_exact_owned_members_and_one_aggregate_font_run():
    manifest, quality = _fixture()
    target = _proof(quality)["targets"][1]
    joint = _glyph(0, "30")
    joint.update(members=[{"qid": "svg-3", "startUtf16": 0, "endUtf16": 1},
                          {"qid": "svg-3", "startUtf16": 1, "endUtf16": 2}], sharedBoundary=True)
    joint["ownedIntersection"] = deepcopy(joint["members"])
    target["glyphs"] = [joint]
    _seal(manifest, quality)
    result = validate_chart_content_paint(manifest, quality)
    assert result["passed"] is False
    assert result["receiptBindingVerified"] is True
    assert result["perClusterFontAttributionVerified"] is False


def test_original_global_primitive_ids_are_not_accepted_as_new_datum_local_targets():
    manifest, quality = _fixture()
    _proof(quality)["targets"][0]["id"] = "chart/primitive/svg-2"
    _seal(manifest, quality)
    assert "receiptBindingVerified" not in validate_chart_content_paint(manifest, quality)


@pytest.mark.parametrize("reassign", [False, True])
def test_actual_builder_line_anchor_obligation_schema_and_exact_support_identity(reassign):
    examples = json.loads((Path(__file__).resolve().parents[4] / "docs" / "session-results" /
                           "svg-chart-support-graph-examples-20261108.json").read_text(encoding="utf-8"))
    manifest = deepcopy(next(case for case in examples["examples"] if case["name"] == "coincident-line")["manifest"])
    chart, datum = manifest["charts"][0], manifest["charts"][0]["datums"][0]
    support = datum["geometrySupport"]
    anchor = next(item for item in support["visibilityObligations"] if item["kind"] == "value-anchor")
    qid = anchor["primitiveQid"]
    specifications = [item for item in support["visibilityObligations"] if item["primitiveQid"] == qid]
    obligations = [{"supportId": item["id"], "complete": True, "paintRetained": True,
        "via": "original-boundary-contribution", "reason": None, "boundaryPixels": 1, "supportedPixels": 1,
        "footprint": _mask(1), "originalContribution": _mask(1)} for item in specifications]
    record = {"id": f"{chart['id']}/primitive/{qid}/{datum['id']}", "chartId": chart["id"], "qid": qid,
              "kind": "primitive", "datumIds": [datum["id"]], "geometrySupport": support,
              "footprint": _mask(8), "originalContribution": _mask(8), "visibilityObligations": obligations,
              "contributionRegions": [{"id": item["id"], "kind": item["kind"], "footprint": _mask(1),
                  "originalContribution": _mask(1)} for item in support.get("contributionRegions", [])
                  if item["primitiveQid"] == qid]}
    graph = {"complete": True, "paintRetained": True, "unproven": [],
             "equivalenceClasses": [{"classId": item["id"]} for item in chart["compositionGroup"]["equivalenceClasses"]],
             "targets": [{"targetId": record["id"], "complete": True, "paintRetained": True, "reason": None,
                          "obligations": deepcopy(obligations)}]}
    if reassign:
        record["visibilityObligations"][0]["supportId"] = "different-datum/value-anchor"
        graph["targets"][0]["obligations"] = deepcopy(record["visibilityObligations"])
        with pytest.raises(ValueError, match="support_obligation_association_mismatch"):
            _direct_support_graphs(manifest, {"supportGraphs": [graph]}, [record], 1280 * 720)
    else:
        assert _direct_support_graphs(manifest, {"supportGraphs": [graph]}, [record], 1280 * 720) is None


@pytest.mark.parametrize("attack", [None, "fill", "empty-boundary"])
def test_actual_polar_adjacency_keeps_stroke_and_proved_fill_paint_ownership_distinct(attack):
    examples = json.loads((Path(__file__).resolve().parents[4] / "docs" / "session-results" /
                           "svg-chart-support-graph-examples-20261108.json").read_text(encoding="utf-8"))
    manifest = deepcopy(next(case for case in examples["examples"] if case["name"] == "separator-pie")["manifest"])
    relation = next(item for item in manifest["charts"][0]["compositionGroup"]["relationships"]
                    if item["kind"] == "proved-polar-adjacency")
    if attack:
        overlap = relation["localOverlaps"][0]
        if attack == "fill":
            overlap["occluderPaint"] = "fill"
        else:
            overlap["boundarySegments"] = []
        with pytest.raises(ValueError, match="manifest_local_overlap_invalid"):
            _targets(manifest)
    else:
        targets, _, pending = _targets(manifest)
        assert pending is False
        for target in targets:
            for overlap in target.get("ownedRelationships", []):
                if overlap["regionRule"] == "native-separator-stroke-intersection-in-datum-support/v1":
                    assert overlap["occluderPaint"] == "stroke"
                    assert overlap["boundarySegments"]
                    assert target["ownedPaints"][overlap["occluderQid"]] == "stroke"
                elif overlap["kind"] == "proved-polar-adjacency":
                    assert overlap["occluderPaint"] == "all"
                    assert overlap["polarTopology"]["categoryIndices"]
                    assert target["ownedPaints"][overlap["occluderQid"]] == "all"


def test_prefont_text_projection_preserves_tails_and_supplementary_character_owner_offsets():
    wrapper = '<html><svg xmlns="http://www.w3.org/2000/svg"><text>A<tspan>😀</tspan>B</text></svg></html>'
    assert _text_projection(wrapper) == [
        {"ownerQid": "svg-1", "textRootQid": "svg-1", "rootTextDigest": _hash("A😀B"),
         "ownedTextDigest": _hash("AB"), "segments": [
             {"rootStartUtf16": 0, "rootEndUtf16": 1, "ownedStartUtf16": 0},
             {"rootStartUtf16": 3, "rootEndUtf16": 4, "ownedStartUtf16": 1}]},
        {"ownerQid": "svg-2", "textRootQid": "svg-1", "rootTextDigest": _hash("A😀B"),
         "ownedTextDigest": _hash("😀"), "segments": [
             {"rootStartUtf16": 1, "rootEndUtf16": 3, "ownedStartUtf16": 0}]}]


def test_capture_context_cannot_replace_verified_prefont_projection_with_an_arbitrary_owner_map():
    manifest, quality = _fixture()
    projection = _projection()
    projection[0]["segments"][0]["rootEndUtf16"] = 1
    _proof(quality)["captureContextDigest"] = _canonical({"schema": "svg-chart-capture-context/v1",
        "treeDerivativeDigest": manifest["treeDerivativeDigest"], "wrapperDigest": quality["wrapper_digest"],
        "viewport": {"cssWidth": 1280, "cssHeight": 720}, "textProjection": projection})
    _seal(manifest, quality)
    result = validate_chart_content_paint(manifest, quality)
    assert not result["passed"]
    assert "receiptBindingVerified" not in result


def _alternative_case(name, via):
    """Actual builder identities plus synthetic measured cross-witness masks."""
    examples = json.loads((Path(__file__).resolve().parents[4] / "docs" / "session-results" /
                           "svg-chart-support-graph-examples-20261108.json").read_text(encoding="utf-8"))
    chart = deepcopy(next(case for case in examples["examples"] if case["name"] == name)["manifest"]["charts"][0])
    records = {}
    for datum in chart["datums"]:
        for qid in datum["primitiveQids"]:
            identifier = f"{chart['id']}/primitive/{qid}/{datum['id']}"
            records[identifier] = {"id": identifier, "qid": qid, "chartId": chart["id"], "kind": "primitive",
                "datumIds": [datum["id"]], "geometrySupport": datum["geometrySupport"], "footprint": _mask(8),
                "complete": True, "paintRetained": True,
                "originalContribution": _mask(8), "contributionRegions": [
                    {"id": item["id"], "kind": item["kind"], "footprint": _mask(2), "originalContribution": _mask(1)}
                    for item in datum["geometrySupport"].get("contributionRegions", []) if item["primitiveQid"] == qid],
                "visibilityObligations": [
                    {"supportId": item["id"], "complete": True, "paintRetained": True, "reason": None,
                     "via": "original-boundary-contribution", "boundaryPixels": 2, "supportedPixels": 1,
                     "footprint": _mask(2), "originalContribution": _mask(1)}
                    for item in datum["geometrySupport"]["visibilityObligations"] if item["primitiveQid"] == qid]}
    for qid in chart["compositionGroup"]["primitiveQids"]:
        if not any(qid in datum["primitiveQids"] for datum in chart["datums"]):
            identifier = f"{chart['id']}/primitive/{qid}/furniture"
            records[identifier] = {"id": identifier, "qid": qid, "kind": "primitive", "datumIds": [],
                                   "footprint": _mask(8), "originalContribution": _mask(8)}
    for glyph in chart["glyphs"]:
        for span in glyph["ranges"]:
            identifier = f"{chart['id']}/glyph/{glyph['qid']}/{span['start']}/{span['length']}"
            item = records.setdefault(identifier, {"id": identifier, "qid": glyph["qid"], "kind": "glyph-range", "roles": [],
                "datumIds": [], "complete": True, "paintRetained": True, "originalContribution": _mask(8)})
            if glyph["role"] not in item["roles"]:
                item["roles"].append(glyph["role"])
            item["datumIds"].extend(owner for owner in glyph["datumIds"] if owner not in item["datumIds"])
    datum, specification, alternative = next((datum, item, alternative) for datum in chart["datums"]
        for item in datum["geometrySupport"]["visibilityObligations"] for alternative in item["alternatives"]
        if alternative["kind"] == via)
    target_id = f"{chart['id']}/primitive/{specification['primitiveQid']}/{datum['id']}"
    target = records[target_id]
    result = next(item for item in target["visibilityObligations"] if item["supportId"] == specification["id"])
    result.update(via=via, originalContribution=_mask(0), supportedPixels=0)
    graph, proof = {"equivalenceClasses": []}, {"viewport": {"rasterWidth": 1280, "rasterHeight": 720}}
    if via == "marker-on-datum":
        result.update(supportTargetId=alternative["markerTargetId"], supportedPixels=2,
            markerBoundary={"targetId": target_id, "supportId": specification["id"],
                "markerTargetId": alternative["markerTargetId"], "markerQid": alternative["markerQid"],
                "footprint": _mask(2), "originalContribution": _mask(2)})
    elif via == "bound-stack-value-glyphs":
        result.update(supportTargetId=alternative["neighborTargetId"],
            contributionRegionIds=[alternative["datumInteriorSupportId"], alternative["neighborInteriorSupportId"]],
            boundStackValueGlyphs={**alternative, "targetId": target_id, "supportId": specification["id"]})
    elif via == "equivalent-native-support":
        declared = next(item for item in chart["compositionGroup"]["equivalenceClasses"] if item["id"] == alternative["classId"])
        graph["equivalenceClasses"] = [{"classId": declared["id"], "complete": True, "paintRetained": True,
                                        "via": "joint-original-native-contribution", "reason": None}]
        boundaries = []
        for member in declared["members"]:
            record = records[member["targetId"]]
            record["originalContribution"] = _mask(0)
            for row in record["visibilityObligations"]:
                row.update(originalContribution=_mask(0), supportedPixels=0)
            boundaries.append({**member, "footprint": _mask(2), "originalContribution": _mask(1),
                               "retained": _mask(2), "altered": _mask(0)})
        result["classId"] = declared["id"]
        proof["jointNativePaint"] = [{"classId": declared["id"],
            "memberTargetIds": [item["targetId"] for item in declared["members"]], "memberBoundaries": boundaries,
            "footprint": _mask(8), "originalContribution": _mask(8), "retained": _mask(8), "altered": _mask(0)}]
    elif via == "shared-stack-interface":
        own, neighbor = alternative["datumInteriorSupportId"], alternative["neighborInteriorSupportId"]
        a, b = specification["segments"][0]
        x, y = int((a[0] + b[0]) / 2), int((a[1] + b[1]) / 2)
        dx, dy = (0, 1) if abs(a[0] - b[0]) >= abs(a[1] - b[1]) else (1, 0)
        result.update(supportTargetId=alternative["neighborTargetId"], contributionRegionIds=[own, neighbor],
            stackBoundaryContrast={"targetId": target_id, "supportId": specification["id"],
                "neighborTargetId": alternative["neighborTargetId"], "neighborSupportId": alternative["neighborSupportId"],
                "datumInteriorSupportId": own, "neighborInteriorSupportId": neighbor,
                "samplingRule": "normal-interior-native-pair/v1", "footprint": _mask(2),
                "normalInteriorPairs": _mask(1), "nativeContrast": _mask(1), "originalContribution": _mask(1), "samplePairs": [
                    {"kind": "adjacent-fill", "boundaryPixel": [x, y],
                     "negativeInteriorPixel": [x - dx, y - dy], "positiveInteriorPixel": [x + dx, y + dy]}]})
    else:
        interior, side = alternative["datumInteriorSupportId"], alternative["datumSideSupportId"]
        result.update(supportTargetId=alternative["gridTargetId"], contributionRegionIds=[interior, side],
            calibratedGridBoundary={"targetId": target_id, "supportId": specification["id"],
                "gridTargetId": alternative["gridTargetId"], "gridQid": alternative["gridQid"],
                "datumInteriorSupportId": interior, "datumSideSupportId": side,
                "tickTargetIds": alternative["tickTargetIds"], "titleTargetIds": alternative["titleTargetIds"],
                "footprint": _mask(2), "originalContribution": _mask(1)})
    return chart, graph, proof, list(records.values()), target, specification, result


@pytest.mark.parametrize(("name", "via"), [("coincident-line", "equivalent-native-support"),
    ("stacked-column", "shared-stack-interface"), ("fixed-column", "calibrated-value-line")])
def test_actual_builder_alternative_identities_require_typed_measured_native_cross_witnesses(name, via):
    assert _alternative_support(*_alternative_case(name, via), 1280 * 720) is None


@pytest.mark.parametrize("attack", ["missing-members", "zero-member", "wrong-support", "wrong-unit", "altered-member", "equal-count-different-mask"])
def test_joint_class_summary_cannot_replace_any_individual_member_boundary_measurement(attack):
    args = _alternative_case("coincident-line", "equivalent-native-support")
    chart, _, proof, _, _, _, _ = args
    joint = proof["jointNativePaint"][0]
    member = joint["memberBoundaries"][0]
    if attack == "missing-members":
        joint.pop("memberBoundaries")
    elif attack == "zero-member":
        member["originalContribution"] = _mask(0)
    elif attack == "wrong-support":
        member["supportId"] = "unrelated-boundary"
    elif attack == "altered-member":
        member["altered"] = _mask(1)
    elif attack == "equal-count-different-mask":
        member["footprint"]["maskDigest"] = _hash("different-equal-cardinality-subset")
    else:
        chart["datums"][0]["geometrySupport"]["unitKey"] = "contradictory-unit"
    with pytest.raises(ValueError):
        _alternative_support(*args, 1280 * 720)


def test_materialized_admitted_html_identity_is_distinct_from_the_prefont_wrapper():
    manifest, quality = _with_direct_graph(*_fixture())
    proof, metrics = _proof(quality), quality["browser_metrics"]
    assert len({manifest["canonicalSvgDigest"], quality["wrapper_digest"], proof["admittedRenderDigest"]}) == 3
    assert metrics["source_digest"] == metrics["render_digest"] == proof["admittedRenderDigest"]
    assert validate_chart_content_paint(manifest, quality)["passed"] is True


@pytest.mark.parametrize("field", ["source_digest", "render_digest"])
def test_admitted_metrics_cannot_be_replaced_with_the_prefont_wrapper_identity(field):
    manifest, quality = _with_direct_graph(*_fixture())
    quality["browser_metrics"][field] = quality["wrapper_digest"]
    _seal(manifest, quality)
    result = validate_chart_content_paint(manifest, quality)
    assert result["passed"] is False
    assert "receiptBindingVerified" not in result


@pytest.mark.parametrize("blank", [True, False])
def test_zero_ink_ascii_space_keeps_coverage_while_nonblank_at_the_same_binding_is_rejected(blank):
    _, quality = _fixture()
    target = deepcopy(_proof(quality)["targets"][1])
    target.update(endUtf16=3, ownedTextDigest=_hash("3 0"),
                  glyphs=[_glyph(0, "3"), _glyph(1, " "), _glyph(2, "0")])
    target["fontEvidence"]["runs"][0]["utf16Length"] = 3
    cluster = target["glyphs"][1]
    cluster.update(_mask(0), observableReferencePixels=0, originalContribution=_mask(0))
    if blank:
        assert _glyphs(target, 1280 * 720, set(), owned_text="3 0") is None
    else:
        # QID, UTF-16 ownership, complete flags, fonts and all masks stay identical.
        cluster["textDigest"] = _hash("X")
        target["ownedTextDigest"] = _hash("3X0")
        with pytest.raises(ValueError, match="native_ink_unproven"):
            _glyphs(target, 1280 * 720, set(), owned_text="3X0")


def _bound_text_fixture(text):
    """Synthetic whole-consumer carrier bound to its exact actual wrapper text."""
    manifest, quality = _with_direct_graph(*_fixture())
    proof = _proof(quality)
    svg = _SVG.replace(">30<", f">{text}<")
    wrapper = _WRAPPER.replace(_SVG, svg)
    capture = _capture(wrapper_html=wrapper, source_digest=_hash(svg))
    manifest["canonicalSvgDigest"] = proof["canonicalSvgDigest"] = quality["source_digest"] = _hash(svg)
    quality["wrapper_digest"] = proof["wrapperDigest"] = _hash(wrapper)
    tree = deepcopy(_TREE)
    tree[-1]["text"] = text
    manifest["treeDerivativeDigest"] = proof["treeDerivativeDigest"] = _canonical({
        "schema": "svg-chart-node-tree/v1", "nodes": tree})
    proof["captureContextDigest"] = _canonical({"schema": "svg-chart-capture-context/v1",
        "treeDerivativeDigest": manifest["treeDerivativeDigest"], "wrapperDigest": quality["wrapper_digest"],
        "viewport": {"cssWidth": 1280, "cssHeight": 720}, "textProjection": _text_projection(wrapper)})
    length = len(text.encode("utf-16-le")) // 2
    owner = manifest["charts"][0]["glyphs"][0]
    owner.update(ownedTextDigest=_hash(text), ranges=[{"start": 0, "length": length}])
    manifest["charts"][0]["compositionGroup"]["relationships"][0]["length"] = length
    target = proof["targets"][1]
    proof["targets"][0]["ownedGlyphRanges"][0]["length"] = length
    target.update(id=f"chart/glyph/svg-3/0/{length}", ownedTextDigest=_hash(text), endUtf16=length)
    target["fontEvidence"]["runs"][0]["utf16Length"] = length
    target["glyphs"] = []
    cursor = 0
    for character in text:
        target["glyphs"].append(_glyph(cursor, character))
        cursor += len(character.encode("utf-16-le")) // 2
    proof["charts"][0]["targetIds"][1] = target["id"]
    proof["charts"][0]["datums"][0]["targetIds"][1] = target["id"]
    proof["supportGraphs"][0]["targets"][1]["targetId"] = target["id"]
    _seal(manifest, quality)
    return manifest, quality, capture


def test_nonblank_owned_text_cannot_claim_a_zero_ink_space_cluster_after_outer_rebinding():
    manifest, quality, capture = _bound_text_fixture("3X0")
    cluster = _proof(quality)["targets"][1]["glyphs"][1]
    cluster.update(_mask(0), textDigest=_hash(" "), observableReferencePixels=0,
                   originalContribution=_mask(0))
    _seal(manifest, quality)
    result = _validate_paint(manifest, quality, capture=capture)
    assert result == {"passed": False, "reason": "chart_content_paint_glyph_cluster_text_mismatch"}


@pytest.mark.parametrize("split_surrogate", [False, True])
def test_actual_owned_utf16_supports_a_surrogate_pair_and_rejects_a_range_that_splits_it(split_surrogate):
    manifest, quality, capture = _bound_text_fixture("3😀0")
    if split_surrogate:
        cluster = _proof(quality)["targets"][1]["glyphs"][1]
        cluster["endUtf16"] = 2
        cluster["members"][0]["endUtf16"] = 2
        cluster["ownedIntersection"][0]["endUtf16"] = 2
        _seal(manifest, quality)
    result = _validate_paint(manifest, quality, capture=capture)
    if split_surrogate:
        assert result == {"passed": False, "reason": "chart_content_paint_glyph_cluster_text_mismatch"}
    else:
        assert result["passed"] is True


@pytest.fixture(scope="module")
def genuine_column_capture():
    """Opt-in replay of one immutable service result; no capture or field restamping."""
    manifest_path = os.environ.get("SVG_CHART_PAINT_REPLAY_INPUT_MANIFEST")
    if not manifest_path:
        pytest.skip("Set SVG_CHART_PAINT_REPLAY_INPUT_MANIFEST for the genuine local artifact replay")
    encoded_manifest = Path(manifest_path).read_bytes()
    assert hashlib.sha256(encoded_manifest).hexdigest() == (
        "8aeb95d518c9eac3c7ef2150b4723f3680e8f06a33999521ae1daa4985af8d3e")
    provenance = json.loads(encoded_manifest)
    assert provenance["schema"] == "svg-chart-consumer-replay-input/v1"
    assert provenance["actualResultType"] == "services.svg_renderer.SvgRenderResult"
    artifacts = {}
    for entry in provenance["artifacts"]:
        assert entry["name"] not in artifacts
        artifact_path = Path(entry["path"])
        assert artifact_path.is_absolute()
        data = artifact_path.read_bytes()
        assert "sha256:" + hashlib.sha256(data).hexdigest() == entry["sha256"]
        artifacts[entry["name"]] = data
    stored = json.loads(artifacts["actual-svg-render-result.json"])
    assert stored["schema"] == "svg-render-result-artifact/v1"
    assert stored["type"] == provenance["actualResultType"]
    from services.svg_renderer import SvgRenderResult

    capture = SvgRenderResult(
        png=base64.b64decode(stored["pngBase64"], validate=True), quality=stored["quality"],
        wrapper_html=stored["wrapper_html"], source_digest=stored["source_digest"],
    )
    assert capture.png == artifacts["final.png"]
    assert capture.wrapper_html.encode("utf-8") == artifacts["wrapper.html"]
    assert capture.quality == json.loads(artifacts["quality.json"])
    assert capture.quality["browser_metrics"] == json.loads(artifacts["browser-metrics.json"])
    assert _proof(capture.quality) == json.loads(artifacts["paint-proof.json"])
    canonical_svg = artifacts["canonical.svg"].decode("utf-8")
    manifest = json.loads(artifacts["semantic-manifest.json"])
    requested_source = json.loads(artifacts["requested-source.json"])
    case = json.loads(artifacts["fixture-case-snapshot.json"])
    assert case["canonicalSvg"] == canonical_svg
    assert case["manifest"] == manifest
    assert case["requestedSource"] == requested_source
    assert case["nativeDeclarations"] == json.loads(artifacts["native-declarations.json"])
    assert requested_source["sourceDigest"] == provenance["requestedSourceDigest"]
    assert requested_source["provenance"]["authoredBeforeAcceptanceCapture"] is True
    assert _hash(canonical_svg) == provenance["canonicalSourceDigest"] == capture.source_digest
    assert _hash(capture.wrapper_html) == provenance["wrapperDigest"]
    assert _proof(capture.quality)["admittedRenderDigest"] == provenance["admittedRenderDigest"]
    return manifest, capture, requested_source


def test_genuine_service_result_retains_three_distinct_bound_identities(genuine_column_capture):
    manifest, capture, requested_source = genuine_column_capture
    assert requested_source["content"] and requested_source["payload"]
    quality, proof = capture.quality, _proof(capture.quality)
    assert len({capture.source_digest, quality["wrapper_digest"], proof["admittedRenderDigest"]}) == 3
    assert quality["browser_metrics"]["source_digest"] == quality["browser_metrics"]["render_digest"] == proof["admittedRenderDigest"]
    # Pass the original service object and quality without normalization or resealing.
    result = _validate_paint(manifest, quality, capture=capture)
    assert result["passed"] is True
    assert result["receiptBindingVerified"] is True
    assert result["captureContextVerified"] is True


@pytest.mark.parametrize("attack", ["canonical", "wrapper", "admitted", "png", "context", "metric-source", "metric-render", "metrics"])
def test_genuine_service_capture_still_rejects_broken_identity_links(genuine_column_capture, attack):
    original_manifest, original_capture, _ = genuine_column_capture
    manifest, capture = deepcopy(original_manifest), deepcopy(original_capture)
    quality, proof = capture.quality, _proof(capture.quality)
    if attack == "canonical":
        capture = replace(capture, source_digest=_hash("different-canonical-source"))
    elif attack == "wrapper":
        capture = replace(capture, wrapper_html=capture.wrapper_html + " ")
    elif attack == "admitted":
        proof["admittedRenderDigest"] = _hash("different-admitted-html")
    elif attack == "png":
        capture = replace(capture, png=capture.png + b"different-emitted-bytes")
    elif attack == "context":
        proof["captureContextDigest"] = _hash("different-capture-context")
    elif attack in {"metric-source", "metric-render"}:
        field = "source_digest" if attack == "metric-source" else "render_digest"
        quality["browser_metrics"][field] = quality["wrapper_digest"]
    else:
        proof["targets"][0]["retained"]["maskDigest"] = _hash("different-metrics")
    if attack != "metrics":
        _seal(manifest, quality)
    result = _validate_paint(manifest, quality, capture=capture)
    assert result["passed"] is False
    assert "receiptBindingVerified" not in result


@pytest.mark.parametrize("contributes", [False, True])
def test_equivalent_member_support_terminates_in_actual_direct_contribution_without_cycles(contributes):
    args = _alternative_case("coincident-line", "equivalent-native-support")
    chart, graph, proof, records, current, _, _ = args
    declaration = next(item for item in chart["compositionGroup"]["equivalenceClasses"]
                       if item["id"] == graph["equivalenceClasses"][0]["classId"])
    member = next(item for item in declaration["members"] if item["targetId"] != current["id"])
    contributor = next(item for item in records if item["id"] == member["targetId"])
    root = next(item for item in contributor["visibilityObligations"] if item["supportId"] == member["supportId"])
    graph["equivalenceClasses"][0].update(via="contributing-native-member", contributorTargetId=contributor["id"])
    proof.pop("jointNativePaint")
    if contributes:
        contributor["originalContribution"] = _mask(8)
        root.update(via="original-boundary-contribution", originalContribution=_mask(1), supportedPixels=1)
        assert _alternative_support(*args, 1280 * 720) is None
    else:
        root.update(via="equivalent-native-support", classId=declaration["id"])
        with pytest.raises(ValueError, match="support_class_root_unproven"):
            _alternative_support(*args, 1280 * 720)


@pytest.mark.parametrize("attack", ["missing", "gradient", "no-normal-pair", "neighbor", "no-contrast", "interior", "native-contrast", "missing-native-contrast"])
def test_stack_external_gradients_and_unbound_pairs_cannot_satisfy_internal_boundary(attack):
    args = _alternative_case("stacked-column", "shared-stack-interface")
    _, _, _, _, _, _, result = args
    witness = result["stackBoundaryContrast"]
    if attack == "missing":
        result.pop("stackBoundaryContrast")
    elif attack == "gradient":
        witness["samplingRule"] = "adjacent-rgb-gradient"
    elif attack == "no-normal-pair":
        witness["normalInteriorPairs"] = _mask(0)
    elif attack == "neighbor":
        witness["neighborTargetId"] = "unrelated-neighbor"
    elif attack == "no-contrast":
        witness["originalContribution"] = _mask(0)
    elif attack == "native-contrast":
        witness["nativeContrast"] = _mask(0)
    elif attack == "missing-native-contrast":
        witness.pop("nativeContrast")
    else:
        witness["datumInteriorSupportId"] = "unrelated-interior"
    with pytest.raises(ValueError):
        _alternative_support(*args, 1280 * 720)


@pytest.mark.parametrize("kind", ["adjacent-fill", "native-divider"])
def test_thin_fill_boundary_may_share_an_interior_pixel_but_a_native_divider_must_be_distinct(kind):
    args = _alternative_case("stacked-column", "shared-stack-interface")
    pair = args[-1]["stackBoundaryContrast"]["samplePairs"][0]
    pair.update(kind=kind, boundaryPixel=pair["negativeInteriorPixel"][:])
    if kind == "adjacent-fill":
        assert _alternative_support(*args, 1280 * 720) is None
    else:
        with pytest.raises(ValueError, match="support_stack_sample_pairs_unproven"):
            _alternative_support(*args, 1280 * 720)


@pytest.mark.parametrize("attack", ["missing", "duplicate", "same-interior", "noninteger", "outside", "kind"])
def test_stack_normal_sample_pairs_are_unique_physical_pixels_bound_one_per_contributing_pixel(attack):
    args = _alternative_case("stacked-column", "shared-stack-interface")
    witness = args[-1]["stackBoundaryContrast"]
    pair = witness["samplePairs"][0]
    if attack == "missing":
        witness.pop("samplePairs")
    elif attack == "duplicate":
        witness["samplePairs"].append(deepcopy(pair))
        witness.update(normalInteriorPairs=_mask(2), nativeContrast=_mask(2), originalContribution=_mask(2))
    elif attack == "same-interior":
        pair["positiveInteriorPixel"] = pair["negativeInteriorPixel"][:]
    elif attack == "noninteger":
        pair["boundaryPixel"][0] = True
    elif attack == "outside":
        pair["boundaryPixel"][0] = 1280
    else:
        pair["kind"] = "external-edge"
    with pytest.raises(ValueError):
        _alternative_support(*args, 1280 * 720)


@pytest.mark.parametrize("attack", ["missing", "grid", "tick", "zero", "side"])
def test_calibrated_grid_requires_exact_original_boundary_intersection_and_label_bindings(attack):
    args = _alternative_case("fixed-column", "calibrated-value-line")
    result = args[-1]
    witness = result["calibratedGridBoundary"]
    if attack == "missing":
        result.pop("calibratedGridBoundary")
    elif attack == "grid":
        witness["gridQid"] = "svg-999"
    elif attack == "tick":
        witness["tickTargetIds"] = []
    elif attack == "zero":
        witness["originalContribution"] = _mask(0)
    else:
        witness["datumSideSupportId"] = "unrelated-side"
    with pytest.raises(ValueError):
        _alternative_support(*args, 1280 * 720)


def _marker_case():
    """Actual builder marker ownership with synthetic measured masks."""
    return _alternative_case("marker-line", "marker-on-datum")


def test_explicit_marker_support_requires_entire_original_contributing_native_boundary():
    assert _alternative_support(*_marker_case(), 1280 * 720) is None


@pytest.mark.parametrize("attack", ["no-explicit-alternative", "partial-footprint", "partial-contribution",
    "different-mask", "hidden-marker", "wrong-datum", "wrong-digest", "earlier-marker", "missing-witness", "summary-only"])
def test_marker_intersection_or_summary_cannot_replace_full_bound_native_boundary(attack):
    args = _marker_case()
    chart, _, _, records, _, specification, result = args
    alternative = next(item for item in specification["alternatives"] if item["kind"] == "marker-on-datum")
    marker = next(item for item in records if item["id"] == alternative["markerTargetId"])
    witness = result["markerBoundary"]
    if attack == "no-explicit-alternative":
        specification["alternatives"].remove(alternative)
    elif attack == "partial-footprint":
        witness["footprint"] = _mask(1)
    elif attack == "partial-contribution":
        witness["originalContribution"] = _mask(1)
    elif attack == "different-mask":
        witness["originalContribution"]["maskDigest"] = _hash("different-covered-pixels")
    elif attack == "hidden-marker":
        marker["originalContribution"] = _mask(0)
    elif attack == "wrong-datum":
        marker["datumIds"] = [chart["datums"][-1]["id"]]
    elif attack == "wrong-digest":
        alternative["geometrySupportDigest"] = _hash("different-geometry")
    elif attack == "earlier-marker":
        alternative["markerQid"] = "svg-0"
    elif attack == "missing-witness":
        result.pop("markerBoundary")
    else:
        alternative["requiresEntireBoundaryCoverage"] = False
    with pytest.raises(ValueError):
        _alternative_support(*args, 1280 * 720)


def _stack_glyph_case():
    """Actual builder centered label ownership with synthetic measured masks."""
    return _alternative_case("stacked-column", "bound-stack-value-glyphs")


def test_bound_stack_value_glyphs_resolve_both_full_value_sets_interiors_and_nonrecursive_outer_limits():
    assert _alternative_support(*_stack_glyph_case(), 1280 * 720) is None


def test_bound_stack_value_glyph_contract_needs_no_sibling_but_rejects_a_contradictory_sibling():
    args = _stack_glyph_case()
    specification = args[-2]
    sibling = next(item for item in specification["alternatives"] if item["kind"] == "shared-stack-interface")
    specification["alternatives"].remove(sibling)
    assert _alternative_support(*args, 1280 * 720) is None
    sibling["cumulativeValue"] = "999"
    specification["alternatives"].append(sibling)
    with pytest.raises(ValueError, match="support_stack_glyph_sibling_mismatch"):
        _alternative_support(*args, 1280 * 720)


@pytest.mark.parametrize("attack", ["echo", "swapped-labels", "partial-labels", "hidden-label", "axis-substitute",
    "wrong-unit", "wrong-cumulative", "neighbor-category", "empty-interior", "baseline-cycle", "outer-cycle", "inner-as-outer"])
def test_stack_value_glyphs_cannot_borrow_other_labels_regions_or_recursive_endpoint_summaries(attack):
    args = _stack_glyph_case()
    chart, _, _, records, _, specification, result = args
    alternative = next(item for item in specification["alternatives"] if item["kind"] == "bound-stack-value-glyphs")
    witness = result["boundStackValueGlyphs"]
    by_id = {item["id"]: item for item in records}
    if attack == "echo":
        witness["supportId"] = "another-seam"
    elif attack == "swapped-labels":
        alternative["datumValueGlyphTargetIds"], alternative["neighborValueGlyphTargetIds"] = (
            alternative["neighborValueGlyphTargetIds"], alternative["datumValueGlyphTargetIds"])
    elif attack == "partial-labels":
        alternative["datumValueGlyphTargetIds"] = []
    elif attack == "hidden-label":
        by_id[alternative["datumValueGlyphTargetIds"][0]]["originalContribution"] = _mask(0)
    elif attack == "axis-substitute":
        by_id[alternative["datumValueGlyphTargetIds"][0]]["roles"] = ["axis"]
    elif attack == "wrong-unit":
        alternative["unitKey"] = "unrelated-unit"
    elif attack == "wrong-cumulative":
        alternative["cumulativeValue"] = "999"
    elif attack == "neighbor-category":
        next(item for item in chart["datums"] if item["id"] == alternative["neighborDatumId"])["categoryIndex"] += 1
    elif attack == "empty-interior":
        by_id[alternative["neighborTargetId"]]["contributionRegions"][0]["originalContribution"] = _mask(0)
    elif attack in {"baseline-cycle", "outer-cycle"}:
        endpoint = alternative["stackBaseline" if attack == "baseline-cycle" else "stackOuterTotal"]
        next(item for item in by_id[endpoint["targetId"]]["visibilityObligations"]
             if item["supportId"] == endpoint["supportId"])["via"] = "bound-stack-value-glyphs"
    else:
        alternative["stackOuterTotal"] = deepcopy(alternative["stackBaseline"])
    if attack != "echo":
        result["boundStackValueGlyphs"] = {**alternative, "targetId": args[4]["id"], "supportId": specification["id"]}
    with pytest.raises(ValueError):
        _alternative_support(*args, 1280 * 720)
