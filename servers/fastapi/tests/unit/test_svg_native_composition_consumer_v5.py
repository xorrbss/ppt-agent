"""Portable adversarial checks for bounded native composition receipt decoding."""
from __future__ import annotations

import base64
import copy
import io
from types import SimpleNamespace
import zlib

from PIL import Image
import pytest

from services.generation_core.drivers.svg import chart_content_paint as consumer
from utils.hashing import sha256_prefixed


def raster(raw=b"\xff\x00\xff\x00", size=(2, 2)):
    return {"encoding": "zlib-base64-u8/v1", "width": size[0], "height": size[1],
            "sha256": sha256_prefixed(raw),
            "dataBase64": base64.b64encode(zlib.compress(raw, 9)).decode("ascii")}


def test_decode_binary_and_native_alpha_roles():
    assert consumer._decode_native_raster(raster(), (2, 2), binary=True).tobytes() == b"\xff\x00\xff\x00"
    alpha = raster(b"\x01\x7f\x80\xfe")
    assert consumer._decode_native_raster(alpha, (2, 2)).tobytes() == b"\x01\x7f\x80\xfe"
    with pytest.raises(consumer._Unproven, match="native_mask_domain_invalid"):
        consumer._decode_native_raster(alpha, (2, 2), binary=True)


@pytest.mark.parametrize("change", [
    {"encoding": "zlib-base64-u8/v2"}, {"width": True}, {"height": 4},
    {"extra": 1}, {"sha256": "sha256:" + "0" * 64}, {"dataBase64": "!!!!"},
])
def test_codec_rejects_schema_dimensions_digest_and_invalid_base64(change):
    record = raster()
    record.update(change)
    with pytest.raises(consumer._Unproven):
        consumer._decode_native_raster(record, (2, 2), binary=True)


@pytest.mark.parametrize("compressed", [
    zlib.compress(b"\xff" * 5), zlib.compress(b"\xff" * 3),
    zlib.compress(b"\xff" * 4)[:-1], zlib.compress(b"\xff" * 4) + b"x",
    zlib.compress(b"\xff" * 4) + zlib.compress(b""),
    zlib.compress(b"\xff" * 100000),
])
def test_codec_rejects_expansion_truncation_and_appended_streams(compressed):
    record = raster(b"\xff" * 4)
    record["dataBase64"] = base64.b64encode(compressed).decode("ascii")
    with pytest.raises(consumer._Unproven):
        consumer._decode_native_raster(record, (2, 2), binary=True)


def test_mask_statistics_recomputed_from_exact_alpha_bytes():
    alpha = Image.frombytes("L", (2, 2), b"\x01\x7f\x80\xfe")
    encoded = raster()
    record = {"maskDigest": encoded["sha256"], "pixelCount": 2, "alphaSum": 129,
              "encodedRaster": encoded}
    assert consumer._decode_native_mask(record, alpha, (2, 2)).tobytes() == b"\xff\x00\xff\x00"
    for key, value in (("maskDigest", "sha256:" + "0" * 64), ("pixelCount", True), ("alphaSum", 128)):
        tampered = copy.deepcopy(record)
        tampered[key] = value
        with pytest.raises(consumer._Unproven, match="native_mask_statistics_mismatch"):
            consumer._decode_native_mask(tampered, alpha, (2, 2))


def encoded_mask(raw, alpha=b"\xff" * 4):
    return {"maskDigest": sha256_prefixed(raw), "pixelCount": raw.count(255),
            "alphaSum": sum(a for a, selected in zip(alpha, raw) if selected), "encodedRaster": raster(raw)}


def area_fixture():
    full, empty = b"\xff" * 4, b"\x00" * 4
    chart = {"id": "chart", "chartType": "area", "datums": [],
             "compositionGroup": {"relationships": []}}
    records, rows = [], []
    for index in (1, 2):
        datum_id, qid = f"chart/s{index}/c0", f"svg-{index}"
        target_id = f"chart/primitive/{qid}/{datum_id}"
        spec = {"id": datum_id + "/anchor", "primitiveQid": qid, "kind": "value-anchor",
                "segments": [[[0, 1], [2, 1]]], "radiusPhysicalPixels": 1,
                "regionRule": "native-alpha-boundary-neighborhood/v1", "alternatives": []}
        support = {"geometryDigest": sha256_prefixed(datum_id.encode()), "axisKey": "axis", "unitKey": "unit",
                   "visibilityObligations": [spec]}
        chart["datums"].append({"id": datum_id, "primitiveQids": [qid], "geometrySupport": support})
        masks = {"footprint": full, "originalContribution": full if index == 2 else empty,
                 "retained": full if index == 2 else empty, "ownedOverlap": full if index == 1 else empty,
                 "altered": empty, "quantizedAtCapture": empty, "unproven": empty,
                 "preserved": full, "hidden": full if index == 1 else empty, "residual": empty}
        row = {"kind": "native-area-owned-layer-preservation/v1", "targetId": target_id, "datumId": datum_id,
               "qid": qid, "geometrySupportDigest": support["geometryDigest"], "axisKey": "axis", "unitKey": "unit",
               "canvasMatrix": [1, 0, 0, 1, 0, 0], "deviceScaleFactor": 1,
               "visibility": "preserved-hidden-by-owned" if index == 1 else "visible-native-contribution",
               "sourceVisible": index == 2, "nativeAlpha": raster(full), "occluders": [],
               **{key: encoded_mask(value) for key, value in masks.items()}}
        stats = {key: {field: value for field, value in row[key].items() if field != "encodedRaster"}
                 for key in ("footprint", "originalContribution", *consumer._PARTS)}
        record = {"id": target_id, "qid": qid, "kind": "primitive", "datumIds": [datum_id],
                  "geometrySupport": support, "foreignOccluderQids": [], "clippedQids": [], "unresolvedRanges": [],
                  "ownedRelationships": [], **stats,
                  "visibilityObligations": [{"supportId": spec["id"], "via": row["kind"],
                      "areaVisibilityTargetId": target_id, "boundaryPixels": 4, "supportedPixels": 4,
                      "footprint": stats["footprint"], "originalContribution": stats["originalContribution"]}]}
        records.append(record)
        rows.append(row)
    relation = {"kind": "native-series-layer-order", "datumId": rows[0]["datumId"], "targetQid": "svg-1",
                "occluderQid": "svg-2", "geometrySupportDigest": rows[0]["geometrySupportDigest"],
                "regionRule": "native-alpha-intersection-in-datum-support/v1"}
    records[0]["ownedRelationships"] = [relation]
    chart["compositionGroup"]["relationships"] = [{"kind": "native-series-layer-order",
        "primitiveQids": ["svg-1", "svg-2"], "localOverlaps": [{key: value for key, value in relation.items() if key != "kind"}]}]
    rows[0]["occluders"] = [{"targetId": rows[1]["targetId"], "datumId": rows[1]["datumId"], "qid": "svg-2",
        "geometrySupportDigest": rows[1]["geometrySupportDigest"], "relationDigest": consumer._digest(relation),
        "coverage": encoded_mask(full)}]
    graph = {"nativeComposition": {"schema": "native-composition-pixel-partition/v1", "areaTargets": rows, "jointPairs": []}}
    projection = ((2, 2), [1, 0, 0, 1, 0, 0], 1, Image.new("RGB", (2, 2), "white"))
    return chart, graph, records, projection


def consume_area(fixture):
    chart, graph, records, projection = fixture
    return consumer._native_composition(chart, graph, {}, records, projection)


def test_hidden_native_area_requires_exact_later_native_pixel_coverage():
    fixture = area_fixture()
    resolved = consume_area(fixture)
    assert len(resolved) == 2
    assert set(resolved.values()) == {"native-area-owned-layer-preservation/v1"}


@pytest.mark.parametrize("attack", ["foreign", "uncovered", "cycle", "relation", "matrix", "visibility", "manifest_owner",
                                     "partition", "boundary", "unknown_schema", "native_alpha"])
def test_area_rejects_foreign_uncovered_cyclic_and_resealed_mask_attacks(attack):
    fixture = area_fixture()
    chart, graph, records, projection = fixture
    rows = graph["nativeComposition"]["areaTargets"]
    if attack == "foreign":
        records[0]["ownedRelationships"][0]["occluderQid"] = "svg-99"
    elif attack == "manifest_owner":
        chart["compositionGroup"]["relationships"][0]["localOverlaps"] = []
    elif attack == "uncovered":
        rows[0]["occluders"] = []
    elif attack == "cycle":
        rows[0]["occluders"][0]["targetId"] = rows[0]["targetId"]
    elif attack == "relation":
        rows[0]["occluders"][0]["relationDigest"] = sha256_prefixed(b"foreign")
    elif attack == "matrix":
        rows[0]["canvasMatrix"][4] = 1
    elif attack == "visibility":
        rows[0]["sourceVisible"] = True
    elif attack == "partition":
        rows[0]["retained"] = encoded_mask(b"\xff" * 4)
        records[0]["retained"] = {key: value for key, value in rows[0]["retained"].items() if key != "encodedRaster"}
    elif attack == "boundary":
        records[0]["visibilityObligations"][0]["supportedPixels"] = 3
    elif attack == "unknown_schema":
        graph["nativeComposition"]["schema"] = "native-composition-pixel-partition/v2"
    else:
        rows[1]["nativeAlpha"] = raster(b"\xfe" * 4)
    with pytest.raises(consumer._Unproven):
        consume_area(fixture)


def polar_fixture():
    chart, _, records, projection = area_fixture()
    chart["chartType"] = "donut"
    for datum in chart["datums"]:
        datum["geometrySupport"]["visibilityObligations"] = []
    full, empty, patch, remainder = b"\xff" * 4, b"\0" * 4, b"\xff\0\0\0", b"\0\xff\xff\xff"
    for record in records:
        record["visibilityObligations"] = []
    relation = {"kind": "proved-polar-adjacency", "datumId": chart["datums"][0]["id"],
        "neighborDatumId": chart["datums"][1]["id"], "targetQid": "svg-1", "occluderQid": "svg-2",
        "geometrySupportDigest": chart["datums"][0]["geometrySupport"]["geometryDigest"],
        "neighborGeometrySupportDigest": chart["datums"][1]["geometrySupport"]["geometryDigest"],
        "polarTopology": {"fillInteriorsDisjoint": True, "contourTopologyChecked": True,
                          "sharedRadialSegments": [[[.5, 0], [.5, 2]]]}}
    records[0]["ownedRelationships"] = [relation]
    binding = {"relation": relation, "geometrySupport": chart["datums"][0]["geometrySupport"],
               "partnerGeometrySupport": chart["datums"][1]["geometrySupport"]}
    chart["compositionGroup"]["relationships"] = [{"kind": "proved-polar-adjacency",
        "localOverlaps": [{key: value for key, value in relation.items() if key != "kind"}]}]
    binding_digest = consumer._digest(binding)
    fields = {"kind": "proved-polar-adjacency", "targetId": records[0]["id"],
              "partnerTargetId": records[1]["id"], "qids": ["svg-1", "svg-2"]}
    request = {"id": "native-pair/" + consumer._digest({**fields, "bindingDigest": binding_digest})[7:], **fields}
    bundles = []
    for index, record in enumerate(records):
        masks = {"footprint": full, "originalContribution": full, "retained": remainder if index == 0 else full,
                 "ownedOverlap": empty, "altered": patch if index == 0 else empty,
                 "quantizedAtCapture": empty, "unproven": empty}
        bundle = {"nativeAlpha": raster(full), **{key: encoded_mask(raw) for key, raw in masks.items()}}
        for key in masks:
            actual = patch if index == 0 and key == "ownedOverlap" else empty if key == "altered" else masks[key]
            record[key] = {field: value for field, value in encoded_mask(actual).items() if field != "encodedRaster"}
        bundles.append(bundle)
    row = {"schema": "native-composition-joint-pair/v1", "request": request, "bindingDigest": binding_digest,
        "datumId": chart["datums"][0]["id"], "partnerDatumId": chart["datums"][1]["id"],
        "geometrySupportDigest": relation["geometrySupportDigest"],
        "partnerGeometrySupportDigest": relation["neighborGeometrySupportDigest"],
        "relationDigest": consumer._digest(relation), "canvasMatrix": [1, 0, 0, 1, 0, 0], "deviceScaleFactor": 1,
        "targetMasks": bundles[0], "partnerMasks": bundles[1],
        "pairMasks": {"nativeAlpha": raster(full), "footprint": encoded_mask(full),
                      "originalContribution": encoded_mask(full), "altered": encoded_mask(empty)},
        "regionDemand": encoded_mask(patch), "coveredRegion": encoded_mask(patch),
        "offTraceOriginalContribution": encoded_mask(empty), "incidentSupportIds": [],
        "capturePlanes": {key: [raster(full if key in {"referenceOriginal", "currentOriginal"} else empty)
                                for _ in range(3)]
                          for key in ("expectedResponse", "currentResponse", "referenceOriginal", "currentOriginal", "currentAbsent")}}
    graph = {"nativeComposition": {"schema": "native-composition-pixel-partition/v1", "areaTargets": [], "jointPairs": [row]}}
    return chart, graph, records, projection


def test_polar_joint_requires_exact_original_rgb_and_local_positive_contribution():
    assert consume_area(polar_fixture()) == {}


def test_polar_joint_changes_outside_exact_patch_keep_existing_target_partition_authority():
    fixture = polar_fixture()
    row = fixture[1]["nativeComposition"]["jointPairs"][0]
    row["capturePlanes"]["referenceOriginal"][0] = raster(b"\xff\x7f\xff\xff")
    row["pairMasks"]["altered"] = encoded_mask(b"\0\xff\0\0")
    assert consume_area(fixture) == {}


@pytest.mark.parametrize("attack", ["capture", "dual", "zero_local_contribution", "radial", "pair_order",
                                     "binding", "outside_patch", "ownership", "manifest_relation", "encoded_plane_role"])
def test_polar_joint_rejects_pixel_and_source_pair_attacks(attack):
    fixture = polar_fixture()
    chart, graph, records, projection = fixture
    row = graph["nativeComposition"]["jointPairs"][0]
    if attack in {"capture", "dual"}:
        key = "currentOriginal" if attack == "capture" else "currentResponse"
        row["capturePlanes"][key][0] = raster(b"\x7f" * 4)
    elif attack == "zero_local_contribution":
        row["capturePlanes"]["currentAbsent"] = [raster(b"\xff\0\0\0") for _ in range(3)]
        row["pairMasks"]["originalContribution"] = encoded_mask(b"\0\xff\xff\xff")
    elif attack == "radial":
        records[0]["ownedRelationships"][0]["polarTopology"]["sharedRadialSegments"] = [[[100, 0], [100, 2]]]
    elif attack == "pair_order":
        row["request"]["qids"].reverse()
    elif attack == "binding":
        row["bindingDigest"] = sha256_prefixed(b"unrelated")
    elif attack == "outside_patch":
        row["targetMasks"]["altered"] = encoded_mask(b"\xff\xff\0\0")
        row["targetMasks"]["retained"] = encoded_mask(b"\0\0\xff\xff")
    elif attack == "ownership":
        records[0]["ownedRelationships"] = []
    elif attack == "manifest_relation":
        chart["compositionGroup"]["relationships"] = []
    else:
        row["capturePlanes"]["currentOriginal"].append(raster())
    with pytest.raises(consumer._Unproven):
        consume_area(fixture)


@pytest.mark.parametrize("transform,bbox,expected", [
    (None, {"x": 0, "y": 0, "width": 2, "height": 2}, [1, 0, 0, 1, 0, 0]),
    ("translate(3 4) scale(2)", {"x": 3, "y": 4, "width": 4, "height": 4}, [1, 0, 0, 1, 0, 0]),
])
def test_projection_derives_bbox_viewbox_and_removes_root_transform_once(transform, bbox, expected):
    output = io.BytesIO()
    Image.new("RGB", (2, 2), "white").save(output, format="PNG")
    attr = f' transform="{transform}"' if transform else ""
    capture = SimpleNamespace(png=output.getvalue(), wrapper_html=f'<html><svg viewBox="0 0 2 2"{attr}></svg></html>')
    style = {"width": "2px", "height": "2px", "boxSizing": "content-box", "transform": "none"}
    style.update({"border" + side + "Width": "0px" for side in ("Left", "Right", "Top", "Bottom")})
    style.update({"padding" + side: "0px" for side in ("Left", "Right", "Top", "Bottom")})
    metrics = {"elements": [{"qid": "svg-0", "tag": "svg", "bbox": bbox, "computed_style": style}]}
    actual = consumer._native_projection(capture, metrics, {"dpr": 1})
    assert actual[0] == (2, 2)
    assert actual[1] == expected


def marker_fixture():
    chart, graph, records, projection = polar_fixture()
    chart["chartType"] = "line"
    chart["datums"] = chart["datums"][:1]
    datum = chart["datums"][0]
    support = datum["geometrySupport"]
    records[1].update(id="chart/primitive/svg-2/furniture", datumIds=[], geometrySupport={})
    alternative = {"kind": "marker-on-datum", "markerQid": "svg-2", "markerTargetId": records[1]["id"],
                   "geometrySupportDigest": support["geometryDigest"], "requiresEntireBoundaryCoverage": True,
                   "requiresOriginalContribution": True}
    relation = {"kind": "marker-on-datum", "datumId": datum["id"], "qid": "svg-2"}
    chart["compositionGroup"]["relationships"] = [relation]
    anchor = {"id": datum["id"] + "/anchor", "kind": "value-anchor", "primitiveQid": "svg-1",
              "segments": [[[.5, 0], [.5, 0]]], "regionRule": "native-alpha-boundary-neighborhood/v1",
              "radiusPhysicalPixels": 1, "alternatives": [alternative]}
    incident = {**anchor, "id": datum["id"] + "/edge", "kind": "incident-edge-0",
                "segments": [[[.5, 1.5], [.5, 1.5]]], "alternatives": []}
    support["visibilityObligations"] = [anchor, incident]
    row = graph["nativeComposition"]["jointPairs"][0]
    fields = {"kind": "marker-on-datum", "targetId": records[0]["id"], "partnerTargetId": records[1]["id"],
              "qids": ["svg-1", "svg-2"]}
    binding = {"relation": relation, "obligation": anchor, "alternative": alternative, "geometrySupport": support}
    digest = consumer._digest(binding)
    request = {"id": "native-pair/" + consumer._digest({**fields, "bindingDigest": digest})[7:], **fields}
    empty = b"\0" * 4
    target_alpha, marker_alpha, pair_alpha = b"\xff\0\xff\0", b"\xff\xff\0\0", b"\xff\xff\xff\0"
    original, marker_original, demand = b"\0\0\xff\0", b"\0\xff\0\0", b"\xff\0\0\0"
    for record, key, alpha, contribution, retained, owned in (
        (records[0], "targetMasks", target_alpha, original, original, demand),
        (records[1], "partnerMasks", marker_alpha, marker_original, marker_alpha, empty),
    ):
        masks = {"footprint": alpha, "originalContribution": contribution, "retained": retained,
                 "ownedOverlap": owned, "altered": empty, "quantizedAtCapture": empty, "unproven": empty}
        row[key] = {"nativeAlpha": raster(alpha), **{name: encoded_mask(value, alpha) for name, value in masks.items()}}
        for name, raw in masks.items():
            record[name] = {key: value for key, value in encoded_mask(raw, alpha).items() if key != "encodedRaster"}
    row.update(request=request, bindingDigest=digest, datumId=datum["id"], partnerDatumId=datum["id"],
               geometrySupportDigest=support["geometryDigest"], partnerGeometrySupportDigest=support["geometryDigest"],
               relationDigest=consumer._digest(relation), incidentSupportIds=[incident["id"]],
               pairMasks={"nativeAlpha": raster(pair_alpha), "footprint": encoded_mask(pair_alpha, pair_alpha),
                          "originalContribution": encoded_mask(pair_alpha, pair_alpha), "altered": encoded_mask(empty, pair_alpha)},
               regionDemand=encoded_mask(demand, target_alpha), coveredRegion=encoded_mask(demand, target_alpha),
               offTraceOriginalContribution=encoded_mask(marker_original, marker_alpha))
    stats = lambda raw: {key: value for key, value in encoded_mask(raw, target_alpha).items() if key != "encodedRaster"}
    records[0]["visibilityObligations"] = [
        {"supportId": anchor["id"], "via": "marker-joint-original-contribution/v1",
         "nativeCompositionPairId": request["id"], "footprint": stats(demand), "originalContribution": stats(empty),
         "boundaryPixels": 1, "supportedPixels": 1},
        {"supportId": incident["id"], "via": "original-boundary-contribution", "footprint": stats(target_alpha),
         "originalContribution": stats(original), "boundaryPixels": 2, "supportedPixels": 1},
    ]
    records[0]["ownedRelationships"] = []
    return chart, graph, records, projection


def test_marker_joint_keeps_unilateral_anchor_contribution_zero_and_proves_whole_anchor():
    fixture = marker_fixture()
    resolved = consume_area(fixture)
    chart, _, records, _ = fixture
    assert resolved[(records[0]["id"], chart["datums"][0]["geometrySupport"]["visibilityObligations"][0]["id"])] == "marker-joint-original-contribution/v1"
    assert records[0]["visibilityObligations"][0]["originalContribution"]["pixelCount"] == 0


@pytest.mark.parametrize("attack", ["incident_cycle", "incident_mask", "offtrace", "marker_relation", "pair_id", "axis", "demand"])
def test_marker_joint_rejects_recursive_or_unbound_anchor_support(attack):
    fixture = marker_fixture()
    chart, graph, records, projection = fixture
    row = graph["nativeComposition"]["jointPairs"][0]
    if attack == "incident_cycle":
        records[0]["visibilityObligations"][1]["via"] = "marker-joint-original-contribution/v1"
    elif attack == "incident_mask":
        records[0]["visibilityObligations"][1]["originalContribution"]["maskDigest"] = sha256_prefixed(b"wrong-boundary")
    elif attack == "offtrace":
        row["offTraceOriginalContribution"] = encoded_mask(b"\0" * 4, b"\xff\xff\0\0")
    elif attack == "marker_relation":
        chart["compositionGroup"]["relationships"] = []
    elif attack == "pair_id":
        records[0]["visibilityObligations"][0]["nativeCompositionPairId"] = "foreign"
    elif attack == "axis":
        row["partnerGeometrySupportDigest"] = sha256_prefixed(b"other-axis")
    else:
        row["regionDemand"] = encoded_mask(b"\xff\0\xff\0", b"\xff\0\xff\0")
        row["coveredRegion"] = copy.deepcopy(row["regionDemand"])
    with pytest.raises(consumer._Unproven):
        consume_area(fixture)


def multi_polar_fixture():
    chart, graph, records, projection = polar_fixture()
    rows = graph["nativeComposition"]["jointPairs"]
    third_datum = copy.deepcopy(chart["datums"][1])
    third_datum.update(id="chart/s3/c0", primitiveQids=["svg-3"])
    third_datum["geometrySupport"]["geometryDigest"] = sha256_prefixed(b"third")
    chart["datums"].append(third_datum)
    third_record = copy.deepcopy(records[1])
    third_record.update(id="chart/primitive/svg-3/chart/s3/c0", qid="svg-3", datumIds=[third_datum["id"]],
                        geometrySupport=third_datum["geometrySupport"])
    records.append(third_record)
    rows.append(copy.deepcopy(rows[0]))
    all_changed, retained = b"\xff\0\0\xff", b"\0\xff\xff\0"
    relation = records[0]["ownedRelationships"][0]
    relation["polarTopology"]["sharedRadialSegments"] = [[[-.5, 0], [-.5, 2]]]
    second_relation = copy.deepcopy(relation)
    second_relation.update(neighborDatumId=third_datum["id"], occluderQid="svg-3",
                           neighborGeometrySupportDigest=third_datum["geometrySupport"]["geometryDigest"])
    second_relation["polarTopology"]["sharedRadialSegments"] = [[[2.5, 0], [2.5, 2]]]
    records[0]["ownedRelationships"].append(second_relation)
    chart["compositionGroup"]["relationships"][0]["localOverlaps"] = [
        {key: value for key, value in item.items() if key != "kind"}
        for item in records[0]["ownedRelationships"]]
    spec = {"id": "chart/s1/c0/arc", "kind": "polar-outer-arc", "primitiveQid": "svg-1",
            "segments": [[[0, 1], [2, 1]]], "regionRule": "native-alpha-boundary-neighborhood/v1",
            "radiusPhysicalPixels": 1, "alternatives": []}
    chart["datums"][0]["geometrySupport"]["visibilityObligations"] = [spec]
    for index, (row, partner, local, patch) in enumerate(zip(rows, records[1:], records[0]["ownedRelationships"],
                                                           (b"\xff\0\0\0", b"\0\0\0\xff"))):
        row["targetMasks"]["altered"] = encoded_mask(all_changed)
        row["targetMasks"]["retained"] = encoded_mask(retained)
        row["regionDemand"] = encoded_mask(patch)
        row["coveredRegion"] = encoded_mask(patch)
        row["partnerDatumId"] = partner["datumIds"][0]
        row["partnerGeometrySupportDigest"] = partner["geometrySupport"]["geometryDigest"]
        row["relationDigest"] = consumer._digest(local)
        binding = {"relation": local, "geometrySupport": chart["datums"][0]["geometrySupport"],
                   "partnerGeometrySupport": partner["geometrySupport"]}
        row["bindingDigest"] = consumer._digest(binding)
        fields = {"kind": "proved-polar-adjacency", "targetId": records[0]["id"],
                  "partnerTargetId": partner["id"], "qids": ["svg-1", partner["qid"]]}
        row["request"] = {"id": "native-pair/" + consumer._digest({**fields, "bindingDigest": row["bindingDigest"]})[7:], **fields}
    stats = lambda raw: {key: value for key, value in encoded_mask(raw).items() if key != "encodedRaster"}
    records[0]["retained"] = stats(retained)
    records[0]["ownedOverlap"] = stats(all_changed)
    records[0]["visibilityObligations"] = [{"supportId": spec["id"], "via": "polar-adjacency-joint-original-contribution/v1",
        "nativeCompositionPairIds": [row["request"]["id"] for row in rows], "footprint": stats(b"\xff" * 4),
        "originalContribution": stats(b"\xff" * 4), "boundaryPixels": 4, "supportedPixels": 4}]
    return chart, graph, records, projection


def test_multiple_polar_neighbors_require_disjoint_complete_union_and_exact_boundary_pair_ids():
    fixture = multi_polar_fixture()
    result = consume_area(fixture)
    assert list(result.values()) == ["polar-adjacency-joint-original-contribution/v1"]


@pytest.mark.parametrize("attack", ["missing_pair", "overlap", "order", "annotation", "prestate"])
def test_multiple_polar_neighbors_reject_uncovered_overlap_reordering_and_state_drift(attack):
    fixture = multi_polar_fixture()
    chart, graph, records, projection = fixture
    rows = graph["nativeComposition"]["jointPairs"]
    if attack == "missing_pair":
        rows.pop()
    elif attack == "overlap":
        rows[1]["regionDemand"] = copy.deepcopy(rows[0]["regionDemand"])
        rows[1]["coveredRegion"] = copy.deepcopy(rows[0]["coveredRegion"])
    elif attack == "order":
        rows.reverse()
    elif attack == "annotation":
        records[0]["visibilityObligations"][0]["nativeCompositionPairIds"].pop()
    else:
        rows[1]["targetMasks"]["altered"] = encoded_mask(b"\0\0\0\xff")
        rows[1]["targetMasks"]["retained"] = encoded_mask(b"\xff\xff\xff\0")
    with pytest.raises(consumer._Unproven):
        consume_area(fixture)
