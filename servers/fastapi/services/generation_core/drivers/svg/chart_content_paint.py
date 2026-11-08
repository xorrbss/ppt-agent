"""Pure v5 consumer for the existing renderer's native chart paint receipts.

This module never renders, imports capture machinery, or reconstructs paint
from geometry. Actual PNG/wrapper bytes bind the private runtime context;
canonical source bytes remain represented by the trusted capture identity.
Every demanded support obligation must terminate in its measured original
contribution or an exactly bound, typed native alternative. Unknown alternative
receipts remain unproven even when their target bindings are valid.
Quantized-at-capture masks describe the emitted raster, never physical exposure.
"""
from __future__ import annotations

from collections import Counter
from decimal import Decimal, InvalidOperation
import json
import math
import re
import struct
from xml.etree import ElementTree as ET
import zlib

from utils.hashing import canonical_json_bytes, sha256_prefixed

_KEY = "nativeChartPaintProof"
_SCHEMA = "svg-native-chart-paint-proof/v1"
_PROBE = "svg-chart-native-alpha-dual-paint/v1"
_HASH = re.compile(r"sha256:[a-f0-9]{64}\Z")
_QID = re.compile(r"svg-\d+\Z")
_PARTS = ("retained", "altered", "ownedOverlap", "quantizedAtCapture", "unproven")
_ROLES = {"bar-profile", "stack-segment", "line-trace", "area-boundary", "polar-sector",
          "polar-ring-sector", "zero", "degenerate"}
_SPACE = "\t\n\v\f\r\x1c\x1d\x1e\x1f\x85\xa0\u1680\u2000\u2001\u2002\u2003\u2004\u2005\u2006\u2007\u2008\u2009\u200a\u2028\u2029\u202f\u205f\u3000"
_BLANK_CLUSTERS = {(sha256_prefixed(value.encode("utf-8")), len(value.encode("utf-16-le")) // 2)
                   for value in [*_SPACE, "\r\n"]}


class _Unproven(ValueError):
    pass


def _require(condition, reason):
    if not condition:
        raise _Unproven(reason)


def _digest(value):
    return sha256_prefixed(canonical_json_bytes(value))


def _hash(value):
    return isinstance(value, str) and _HASH.fullmatch(value) is not None


def _int(value, minimum=0):
    return type(value) is int and value >= minimum


def _positive_number(value):
    return type(value) in (int, float) and math.isfinite(value) and value > 0


def _same(actual, expected):
    # Python equality alone would accept bools in place of integer indices.
    return canonical_json_bytes(actual) == canonical_json_bytes(expected)


def _association(record, expected, reason):
    _require(isinstance(record, dict), reason)
    _require(all(key in record and _same(record[key], value) for key, value in expected.items()), reason)


def _base_metrics_digest(metrics):
    # Mirror the producer's portable NUL rule and RenderCapture's JSON encoder.
    portable = json.loads(json.dumps(dict(metrics)).replace("\\u0000", "\\u241f"))
    return sha256_prefixed(json.dumps({key: value for key, value in portable.items() if key != _KEY},
                                      sort_keys=True, separators=(",", ":"), ensure_ascii=False,
                                      default=str).encode("utf-8"))


def _metrics_digest(metrics):
    return sha256_prefixed(json.dumps(metrics, sort_keys=True, separators=(",", ":"),
                                      ensure_ascii=False, default=str).encode("utf-8"))


def _runtime_tree_digest(wrapper):
    match = re.search(r"(<(?:[\w.-]+:)?svg\b.*?</(?:[\w.-]+:)?svg>)", wrapper, re.S | re.I)
    _require(match is not None, "capture_svg_missing")
    root = ET.fromstring(match.group(1))
    nodes = list(root.iter())
    parents = {child: node for node in nodes for child in node}
    indexes = {node: index for index, node in enumerate(nodes)}
    qid = lambda node: f"svg-{indexes[node]}"
    rows = [{"qid": qid(node), "parentQid": qid(parents[node]) if node in parents else None,
             "tag": node.tag, "attributes": {key: value for key, value in node.attrib.items() if key != "data-qid"},
             "text": node.text or "", "tail": node.tail or ""} for node in nodes]
    return _digest({"schema": "svg-chart-node-tree/v1", "nodes": rows})


def _text_projection(wrapper):
    """Mirror the verified pre-font direct-text/tail ownership projection."""
    match = re.search(r"(<(?:[\w.-]+:)?svg\b.*?</(?:[\w.-]+:)?svg>)", wrapper, re.S | re.I)
    _require(match is not None, "capture_svg_missing")
    root = ET.fromstring(match.group(1))
    nodes = list(root.iter())
    qids = {node: node.get("data-qid") or f"svg-{index}" for index, node in enumerate(nodes)}
    result = []
    for text_root in nodes:
        if text_root.tag.rsplit("}", 1)[-1] != "text":
            continue
        owners, offset = {}, 0

        def piece(owner, value):
            nonlocal offset
            value = value or ""
            length = len(value.encode("utf-16-le")) // 2
            if value.strip():
                item = owners.setdefault(owner, {"text": "", "segments": []})
                item["segments"].append({"rootStartUtf16": offset, "rootEndUtf16": offset + length,
                                         "ownedStartUtf16": len(item["text"].encode("utf-16-le")) // 2})
                item["text"] += value
            offset += length

        def visit(node):
            piece(node, node.text)
            for child in node:
                visit(child)
                piece(node, child.tail)

        visit(text_root)
        for owner in text_root.iter():
            if owner in owners:
                item = owners[owner]
                result.append({"ownerQid": qids[owner], "textRootQid": qids[text_root],
                               "rootTextDigest": sha256_prefixed("".join(text_root.itertext()).encode("utf-8")),
                               "ownedTextDigest": sha256_prefixed(item["text"].encode("utf-8")), "segments": item["segments"]})
    return result


def _png_dimensions(png):
    _require(type(png) is bytes and len(png) >= 33 and png[:8] == b"\x89PNG\r\n\x1a\n"
             and png[8:16] == b"\x00\x00\x00\rIHDR", "capture_png_invalid")
    _require(zlib.crc32(png[12:29]) & 0xffffffff == int.from_bytes(png[29:33], "big"), "capture_png_invalid")
    width, height, depth, color, compression, filtering, interlace = struct.unpack(">IIBBBBB", png[16:29])
    valid_depths = {0: {1, 2, 4, 8, 16}, 2: {8, 16}, 3: {1, 2, 4, 8}, 4: {8, 16}, 6: {8, 16}}
    _require(width > 0 and height > 0 and depth in valid_depths.get(color, set())
             and compression == 0 and filtering == 0 and interlace in {0, 1}, "capture_png_invalid")
    return width, height


def _capture_binding(capture, manifest, quality, proof, width, height, viewport):
    _require(capture is not None, "capture_missing")
    png, wrapper, source = (getattr(capture, name, None) for name in ("png", "wrapper_html", "source_digest"))
    _require(isinstance(wrapper, str) and _hash(source), "capture_invalid")
    raster_width, raster_height = _png_dimensions(png)
    _require(sha256_prefixed(png) == quality["png_digest"] == proof["pngDigest"], "capture_png_digest_mismatch")
    wrapper_digest = sha256_prefixed(wrapper.encode("utf-8"))
    _require(wrapper_digest == quality["wrapper_digest"] == proof["wrapperDigest"], "capture_wrapper_digest_mismatch")
    # Source bytes are not present in SvgRenderResult. Bind its trusted source
    # identity without claiming that the canonical SVG was rehashed here.
    _require(source == manifest["canonicalSvgDigest"] == quality["source_digest"] == proof["canonicalSvgDigest"],
             "capture_source_digest_mismatch")
    dpr = raster_width / width
    _require(raster_height / height == dpr and viewport["dpr"] == dpr
             and viewport["rasterWidth"] == raster_width and viewport["rasterHeight"] == raster_height,
             "capture_raster_mismatch")
    context = {"schema": "svg-chart-capture-context/v1", "treeDerivativeDigest": _runtime_tree_digest(wrapper),
               "wrapperDigest": wrapper_digest, "viewport": {"cssWidth": width, "cssHeight": height},
               "textProjection": _text_projection(wrapper)}
    for chart in manifest["charts"]:
        for glyph in chart["glyphs"]:
            owners = [item for item in context["textProjection"] if item["ownerQid"] == glyph["qid"]]
            _require(len(owners) == 1 and owners[0]["ownedTextDigest"] == glyph["ownedTextDigest"],
                     "capture_text_owner_mismatch")
            length = max(segment["ownedStartUtf16"] + segment["rootEndUtf16"] - segment["rootStartUtf16"]
                         for segment in owners[0]["segments"])
            _require(all(span["start"] + span["length"] <= length for span in glyph["ranges"]),
                     "capture_text_range_mismatch")
    _require(proof.get("captureContextDigest") == _digest(context), "capture_context_mismatch")
    if manifest.get("captureContext") is not None:
        _require(_same(manifest["captureContext"], context), "capture_context_mismatch")


def _support_identity(support):
    if "geometryDigest" not in support:
        return
    initial = json.loads(canonical_json_bytes(support))
    digest = initial.pop("geometryDigest")
    obligations = initial.get("visibilityObligations")
    _require(isinstance(obligations, list), "manifest_support_graph_invalid")
    for obligation in obligations:
        _require(isinstance(obligation, dict) and isinstance(obligation.get("alternatives"), list), "manifest_support_graph_invalid")
        obligation["alternatives"] = []
    _require(_hash(digest) and digest == _digest(initial), "manifest_support_digest_mismatch")


def _qid(value):
    return isinstance(value, str) and _QID.fullmatch(value) is not None


def _unique_strings(values, *, qids=False):
    return (isinstance(values, list) and all(_qid(value) if qids else isinstance(value, str) and value
                                           for value in values) and len(set(values)) == len(values))


def _targets(manifest):
    """Mirror only the producer's documented association projection, in order."""
    targets, chart_ids, datum_ids, needs_local, separator_pending = [], set(), set(), False, False
    charts = manifest.get("charts")
    _require(isinstance(charts, list) and charts, "manifest_charts_invalid")
    for chart in charts:
        _require(isinstance(chart, dict), "manifest_chart_invalid")
        identifier = chart.get("id")
        _require(isinstance(identifier, str) and identifier and identifier not in chart_ids, "manifest_chart_invalid")
        chart_ids.add(identifier)
        _require(chart.get("chartType") in {"bar", "column", "line", "area", "pie", "donut"}, "manifest_chart_invalid")
        datums, glyphs, group = chart.get("datums"), chart.get("glyphs"), chart.get("compositionGroup")
        _require(isinstance(datums, list) and datums and isinstance(glyphs, list)
                 and isinstance(group, dict), "manifest_chart_invalid")
        primitives, relationships = group.get("primitiveQids"), group.get("relationships")
        _require(_unique_strings(primitives, qids=True) and isinstance(relationships, list), "manifest_composition_invalid")
        local_ids, pairs, uses = set(), set(), Counter()
        for datum in datums:
            _require(isinstance(datum, dict), "manifest_datum_invalid")
            datum_id = datum.get("id")
            _require(isinstance(datum_id, str) and datum_id and datum_id not in datum_ids, "manifest_datum_invalid")
            datum_ids.add(datum_id)
            local_ids.add(datum_id)
            s, c = datum.get("seriesIndex"), datum.get("categoryIndex")
            _require(_int(s) and _int(c) and (s, c) not in pairs, "manifest_datum_invalid")
            pairs.add((s, c))
            _require(isinstance(datum.get("value"), str) and Decimal(datum["value"]).is_finite(), "manifest_datum_invalid")
            _require(datum.get("geometryRole") in _ROLES and _unique_strings(datum.get("primitiveQids"), qids=True)
                     and set(datum["primitiveQids"]) <= set(primitives), "manifest_datum_invalid")
            support = datum.get("geometrySupport")
            _require(isinstance(support, dict), "manifest_datum_support_invalid")
            _support_identity(support)
            if "categoryStrip" in support:
                strip = support["categoryStrip"]
                _require(isinstance(strip, dict) and set(strip) == {"minimumX", "maximumX"}
                         and all(value is None or type(value) in (int, float) and math.isfinite(value)
                                 for value in strip.values()), "manifest_datum_support_invalid")
                _require(strip["minimumX"] is None or strip["maximumX"] is None
                         or strip["minimumX"] < strip["maximumX"], "manifest_datum_support_invalid")
            uses.update(datum["primitiveQids"])
        for qid, count in uses.items():
            if count > 1:
                needs_local |= chart["chartType"] not in {"line", "area"} or any(
                    not isinstance(datum["geometrySupport"].get("categoryStrip"), dict)
                    for datum in datums if qid in datum["primitiveQids"])
        glyph_targets = {}
        for glyph in glyphs:
            _require(isinstance(glyph, dict) and _qid(glyph.get("qid")) and _hash(glyph.get("ownedTextDigest"))
                     and isinstance(glyph.get("role"), str) and glyph["role"]
                     and _unique_strings(glyph.get("datumIds")) and set(glyph["datumIds"]) <= local_ids,
                     "manifest_glyph_invalid")
            _require(isinstance(glyph.get("ranges"), list) and glyph["ranges"], "manifest_glyph_invalid")
            for span in glyph["ranges"]:
                _require(isinstance(span, dict) and _int(span.get("start")) and _int(span.get("length"), 1),
                         "manifest_glyph_range_invalid")
                key = (glyph["qid"], span["start"], span["length"])
                target = glyph_targets.setdefault(key, {
                    "id": f"{identifier}/glyph/{key[0]}/{key[1]}/{key[2]}", "chartId": identifier,
                    "qid": key[0], "kind": "glyph-range", "startUtf16": key[1], "endUtf16": key[1] + key[2],
                    "ownedTextDigest": glyph["ownedTextDigest"], "offsetScope": "owned-text-nodes", "roles": [], "datumIds": []})
                _require(target["ownedTextDigest"] == glyph["ownedTextDigest"], "manifest_glyph_digest_conflict")
                if glyph["role"] not in target["roles"]:
                    target["roles"].append(glyph["role"])
                target["datumIds"].extend(value for value in glyph["datumIds"] if value not in target["datumIds"])
        primitive_owners = [(qid, datum) for qid in primitives for datum in datums if qid in datum["primitiveQids"]]
        primitive_owners.extend((qid, None) for qid in primitives if not any(qid in datum["primitiveQids"] for datum in datums))
        for qid, datum in primitive_owners:
            owners = [datum["id"]] if datum is not None else []
            owned, ranges, local, owned_paints = [], [], [], {}

            def own(source_qid, paint):
                owned.append(source_qid)
                if owned_paints.get(source_qid) != "all":
                    owned_paints[source_qid] = paint
            for relation in relationships:
                _require(isinstance(relation, dict) and isinstance(relation.get("kind"), str), "manifest_relationship_invalid")
                kind = relation["kind"]
                if kind == "value-on-datum" and relation["datumId"] in owners:
                    _require(_qid(relation.get("qid")) and _int(relation.get("start")) and _int(relation.get("length"), 1),
                             "manifest_relationship_invalid")
                    ranges.append({key: relation[key] for key in ("qid", "start", "length")})
                elif kind == "marker-on-datum" and relation["datumId"] in owners:
                    _require(_qid(relation.get("qid")), "manifest_relationship_invalid")
                    own(relation["qid"], "all")
                overlaps = relation.get("localOverlaps", [])
                _require(isinstance(overlaps, list), "manifest_relationship_invalid")
                for overlap in overlaps:
                    _require(isinstance(overlap, dict), "manifest_relationship_invalid")
                    if overlap.get("datumId") in owners and overlap.get("targetQid") == qid:
                        support = datum["geometrySupport"]
                        _require(overlap.get("geometrySupportDigest") == support.get("geometryDigest")
                                 and _hash(overlap.get("geometrySupportDigest"))
                                 and _same(overlap.get("geometrySupport"), {key: value for key, value in support.items()
                                           if key in {"anchor", "categoryStrip", "baseline"}})
                                 and _qid(overlap.get("occluderQid")), "manifest_local_overlap_invalid")
                        if overlap.get("regionRule") == "native-separator-stroke-intersection-in-datum-support/v1":
                            segments = overlap.get("boundarySegments")
                            _require(kind == "proved-polar-adjacency" and overlap.get("occluderPaint") == "stroke"
                                     and overlap["occluderQid"] in primitives
                                     and isinstance(segments, list) and segments and all(
                                         isinstance(segment, list) and len(segment) == 2 and all(
                                             isinstance(point, list) and len(point) == 2 and all(
                                                 type(value) in (int, float) and math.isfinite(value) for value in point)
                                             for point in segment) for segment in segments), "manifest_local_overlap_invalid")
                            own(overlap["occluderQid"], "stroke")
                            local.append({"kind": kind, **overlap})
                            continue
                        _require(overlap.get("regionRule") == "native-alpha-intersection-in-datum-support/v1",
                                 "manifest_local_overlap_invalid")
                        if kind == "proved-polar-adjacency":
                            neighbors = [item for item in datums if item["id"] == overlap.get("neighborDatumId")]
                            topology = overlap.get("polarTopology")
                            _require(chart["chartType"] in {"pie", "donut"} and overlap.get("occluderPaint") == "all"
                                     and len(neighbors) == 1 and overlap["occluderQid"] in neighbors[0]["primitiveQids"]
                                     and neighbors[0]["id"] != datum["id"]
                                     and overlap.get("neighborGeometrySupportDigest") == neighbors[0]["geometrySupport"].get("geometryDigest")
                                     and overlap.get("axisKey") == support.get("axisKey") == neighbors[0]["geometrySupport"].get("axisKey")
                                     and overlap.get("unitKey") == support.get("unitKey") == neighbors[0]["geometrySupport"].get("unitKey")
                                     and isinstance(topology, dict) and isinstance(topology.get("categoryIndices"), list)
                                     and _same(sorted(topology["categoryIndices"]), sorted([datum["categoryIndex"], neighbors[0]["categoryIndex"]]))
                                     and "boundarySegments" not in overlap, "manifest_local_overlap_invalid")
                        own(overlap["occluderQid"], "all")
                        local.append({"kind": kind, **overlap})
            identity = f"{identifier}/primitive/{qid}" + (f"/{datum['id']}" if datum is not None else "/furniture")
            targets.append({"id": identity, "chartId": identifier, "qid": qid, "kind": "primitive",
                            "datumIds": owners, "geometrySupport": datum["geometrySupport"] if datum is not None else {},
                            "ownedRelationships": local, "ownedGlyphRanges": ranges,
                            "ownedPaints": {source_qid: owned_paints[source_qid]
                                            for source_qid in sorted(set(owned) - {qid}, key=lambda value: int(value[4:]))},
                            "ownedQids": sorted(set(owned) - {qid}, key=lambda value: int(value[4:]))})
        targets.extend(glyph_targets.values())
    _require(targets and len({target["id"] for target in targets}) == len(targets), "manifest_targets_invalid")
    return targets, needs_local, separator_pending


def _mask(record, pixels, *, ink=False):
    _require(isinstance(record, dict) and _hash(record.get("maskDigest"))
             and _int(record.get("pixelCount")) and _int(record.get("alphaSum")), "mask_invalid")
    count, alpha = record["pixelCount"], record["alphaSum"]
    _require(count <= pixels and count <= alpha <= 255 * count, "mask_count_or_alpha_invalid")
    _require(not ink or count > 0, "native_ink_unproven")
    return count, alpha


def _partition(record, pixels):
    footprint = _mask(record.get("footprint"), pixels, ink=True)
    masks = {key: _mask(record.get(key), pixels) for key in _PARTS}
    _require(tuple(sum(masks[key][i] for key in _PARTS) for i in (0, 1)) == footprint, "mask_partition_invalid")
    _require(masks["altered"] == (0, 0), "paint_altered")
    _require(masks["unproven"] == (0, 0), "paint_unproven")
    return masks


def _glyphs(record, pixels, embedded_fonts):
    clusters, evidence = record.get("glyphs"), record.get("fontEvidence")
    _require(isinstance(clusters, list) and clusters and isinstance(evidence, dict)
             and evidence.get("complete") is True and isinstance(evidence.get("runs"), list)
             and evidence["runs"], "glyph_font_evidence_unproven")
    # Current font runs cover actual selected nodes, not individual clusters.
    fonts, coverage = {}, 0
    for run in evidence["runs"]:
        _require(isinstance(run, dict) and _int(run.get("utf16Length"), 1)
                 and isinstance(run.get("faces"), list), "glyph_font_evidence_unproven")
        coverage += run["utf16Length"]
        for face in run["faces"]:
            _require(isinstance(face, dict) and isinstance(face.get("postScriptName"), str) and face["postScriptName"]
                     and _hash(face.get("fontDigest")) and _int(face.get("glyphCount"), 1)
                     and type(face.get("isCustomFont")) is bool, "glyph_font_evidence_unproven")
            _require(not face["isCustomFont"] or face["fontDigest"] in embedded_fonts, "glyph_font_material_unbound")
            identity = (face["fontDigest"], face["isCustomFont"])
            _require(face["postScriptName"] not in fonts or fonts[face["postScriptName"]] == identity,
                     "glyph_font_resource_ambiguous")
            fonts[face["postScriptName"]] = identity
    _require(fonts and coverage >= record["endUtf16"], "glyph_font_evidence_unproven")
    cursor, demanded = record["startUtf16"], 0
    for glyph in clusters:
        _require(isinstance(glyph, dict) and _int(glyph.get("startUtf16")) and _int(glyph.get("endUtf16"))
                 and glyph["startUtf16"] == cursor and glyph["endUtf16"] > cursor
                 and glyph["endUtf16"] <= record["endUtf16"] and _hash(glyph.get("textDigest"))
                 and glyph.get("complete") is True, "glyph_cluster_coverage_unproven")
        length = glyph["endUtf16"] - cursor
        members = glyph.get("members")
        _require(isinstance(members, list) and members and _same(members, glyph.get("ownedIntersection"))
                 and type(glyph.get("sharedBoundary")) is bool and glyph.get("membershipComplete") is True
                 and glyph.get("authorizationComplete") is True, "glyph_joint_membership_unproven")
        member_cursor = cursor
        for member in members:
            _require(isinstance(member, dict) and set(member) == {"qid", "startUtf16", "endUtf16"}
                     and member.get("qid") == record["qid"] and _int(member.get("startUtf16"))
                     and _int(member.get("endUtf16")) and member["startUtf16"] == member_cursor
                     and member["endUtf16"] > member_cursor and member["endUtf16"] <= glyph["endUtf16"],
                     "glyph_joint_membership_unproven")
            member_cursor = member["endUtf16"]
        _require(member_cursor == glyph["endUtf16"], "glyph_joint_membership_unproven")
        blank = (glyph["textDigest"], length) in _BLANK_CLUSTERS
        count, alpha = _mask(glyph, pixels, ink=not blank)
        _require(count <= record["footprint"]["pixelCount"] and alpha <= record["footprint"]["alphaSum"],
                 "glyph_native_footprint_mismatch")
        observable = glyph.get("observableReferencePixels")
        _require(_int(observable) and observable <= count and (blank or observable > 0), "glyph_observable_contribution_unproven")
        contribution = _mask(glyph.get("originalContribution"), pixels, ink=not blank)
        _require(contribution[0] <= count and contribution[1] <= alpha, "glyph_original_contribution_invalid")
        if not blank:
            demanded += 1
        cursor = glyph["endUtf16"]
    _require(cursor == record["endUtf16"] and demanded, "glyph_cluster_coverage_unproven")


def _chart_links(manifest, proof, targets):
    observed = proof.get("charts")
    _require(isinstance(observed, list) and len(observed) == len(manifest["charts"]), "chart_links_mismatch")
    for chart, row in zip(manifest["charts"], observed):
        chart_targets = [target for target in targets if target["chartId"] == chart["id"]]
        _association(row, {"id": chart["id"], "chartType": chart["chartType"],
                           "targetIds": [target["id"] for target in chart_targets]}, "chart_links_mismatch")
        _require(row.get("complete") is True and row.get("paintRetained") is True, "chart_paint_unproven")
        datums = row.get("datums")
        _require(isinstance(datums, list) and len(datums) == len(chart["datums"]), "datum_links_mismatch")
        for datum, item in zip(chart["datums"], datums):
            linked = [target["id"] for target in chart_targets if datum["id"] in target["datumIds"]]
            _association(item, {"id": datum["id"], "seriesIndex": datum["seriesIndex"],
                                "categoryIndex": datum["categoryIndex"], "geometryRole": datum["geometryRole"],
                                "geometrySupport": datum["geometrySupport"],
                                "primitivePaintExpected": bool(datum["primitiveQids"]), "targetIds": linked}, "datum_links_mismatch")
            empty = not datum["primitiveQids"] and datum["geometryRole"] in {"zero", "degenerate"}
            _require((linked or empty) and item.get("complete") is True and item.get("paintRetained") is True,
                     "datum_paint_unproven")


def _subset_mask(child, parent, pixels, reason, *, ink=False):
    subset, limit = _mask(child, pixels, ink=ink), _mask(parent, pixels)
    _require(subset[0] <= limit[0] and subset[1] <= limit[1], reason)
    # These records share their native-alpha basis. Equal-cardinality subsets
    # must be the same binary mask and therefore have the same alpha sum.
    _require(subset[0] != limit[0] or _same(child, parent), reason)
    return subset


def _alternative_support(chart, graph, proof, records, record, specification, obligation, pixels):
    """Resolve alternatives only from their own measured contribution roots."""
    by_id = {item["id"]: item for item in records}
    datums = {item["id"]: item for item in chart["datums"]}
    datum = datums[record["datumIds"][0]]
    support = datum["geometrySupport"]
    via = obligation.get("via")

    def native_region(target_id, region_id, kind):
        target = by_id.get(target_id)
        _require(target is not None and target["kind"] == "primitive", "support_region_unproven")
        declarations = [item for item in target["geometrySupport"].get("contributionRegions", [])
                        if item["id"] == region_id and item["kind"] == kind and item["primitiveQid"] == target["qid"]]
        measured = [item for item in target.get("contributionRegions", []) if item.get("id") == region_id and item.get("kind") == kind]
        _require(len(declarations) == len(measured) == 1, "support_region_unproven")
        _subset_mask(measured[0]["footprint"], target["footprint"], pixels, "support_region_mask_invalid")
        _subset_mask(measured[0]["originalContribution"], measured[0]["footprint"], pixels,
                     "support_region_unproven", ink=True)
        _subset_mask(measured[0]["originalContribution"], target["originalContribution"], pixels, "support_region_mask_invalid")

    def local_witness(witness, identity):
        _association(witness, identity, "support_cross_witness_mismatch")
        _subset_mask(witness.get("footprint"), obligation["footprint"], pixels, "support_cross_mask_invalid")
        _subset_mask(witness.get("originalContribution"), witness["footprint"], pixels,
                     "support_cross_contribution_unproven", ink=True)

    if via == "marker-on-datum":
        alternatives = [item for item in specification["alternatives"]
                        if item.get("kind") == via and item.get("markerTargetId") == obligation.get("supportTargetId")]
        _require(len(alternatives) == 1, "support_marker_alternative_unproven")
        alternative = alternatives[0]
        marker_id, marker_qid = alternative.get("markerTargetId"), alternative.get("markerQid")
        marker = by_id.get(marker_id)
        _require(chart["chartType"] in {"line", "area"}
                 and datum["geometryRole"] in {"line-trace", "area-boundary"}
                 and datum["primitiveQids"] and record["qid"] == datum["primitiveQids"][0]
                 and (specification["kind"] == "value-anchor" or specification["kind"].startswith("incident-edge"))
                 and alternative.get("geometrySupportDigest") == support.get("geometryDigest")
                 and alternative.get("requiresEntireBoundaryCoverage") is True
                 and alternative.get("requiresOriginalContribution") is True
                 and marker is not None and marker.get("kind") == "primitive"
                 and marker.get("datumIds") == [datum["id"]] and marker.get("qid") == marker_qid
                 and marker.get("geometrySupport", {}).get("geometryDigest") == support.get("geometryDigest")
                 and marker_id == f"{chart['id']}/primitive/{marker_qid}/{datum['id']}"
                 and _qid(marker_qid) and int(marker_qid[4:]) > int(record["qid"][4:])
                 and any(item.get("kind") == via and item.get("datumId") == datum["id"]
                         and item.get("qid") == marker_qid for item in chart["compositionGroup"]["relationships"]),
                 "support_marker_identity_mismatch")
        _require(marker.get("complete") is True and marker.get("paintRetained") is True,
                 "support_marker_original_unproven")
        _mask(marker.get("originalContribution"), pixels, ink=True)
        witness = obligation.get("markerBoundary")
        local_witness(witness, {"targetId": record["id"], "supportId": specification["id"],
            "markerTargetId": marker_id, "markerQid": marker_qid})
        _require(set(witness) == {"targetId", "supportId", "markerTargetId", "markerQid", "footprint", "originalContribution"}
                 and _same(witness["footprint"], obligation["footprint"])
                 and _same(witness["originalContribution"], obligation["footprint"])
                 and witness["footprint"]["pixelCount"] <= marker["footprint"]["pixelCount"]
                 and witness["originalContribution"]["pixelCount"] <= marker["originalContribution"]["pixelCount"],
                 "support_marker_entire_boundary_unproven")
        return

    if via == "bound-stack-value-glyphs":
        alternatives = [item for item in specification["alternatives"]
                        if item.get("kind") == via and item.get("neighborTargetId") == obligation.get("supportTargetId")]
        _require(len(alternatives) == 1, "support_stack_glyph_alternative_unproven")
        alternative = alternatives[0]
        _require(set(alternative) == {"kind", "neighborDatumId", "neighborTargetId", "neighborSupportId",
            "geometrySupportDigest", "cumulativeValue", "grouping", "axisKey", "unitKey", "datumValueGlyphTargetIds",
            "neighborValueGlyphTargetIds", "datumInteriorSupportId", "neighborInteriorSupportId", "stackBaseline", "stackOuterTotal"},
            "support_stack_glyph_alternative_unproven")
        _require(_same(obligation.get("boundStackValueGlyphs"),
                       {**alternative, "targetId": record["id"], "supportId": specification["id"]}),
                 "support_stack_glyph_witness_mismatch")
        neighbor = datums.get(alternative.get("neighborDatumId"))
        target = by_id.get(alternative.get("neighborTargetId"))
        _require(chart["chartType"] in {"bar", "column"} and neighbor is not None and target is not None
                 and target.get("datumIds") == [neighbor["id"]]
                 and alternative.get("geometrySupportDigest") == neighbor["geometrySupport"].get("geometryDigest")
                 and alternative.get("grouping") in {"stacked", "percentStacked"}
                 and datum["geometryRole"] == neighbor["geometryRole"] == "stack-segment"
                 and neighbor["categoryIndex"] == datum["categoryIndex"]
                 and abs(neighbor["seriesIndex"] - datum["seriesIndex"]) == 1
                 and alternative.get("axisKey") == support.get("axisKey") == neighbor["geometrySupport"].get("axisKey")
                 and alternative.get("unitKey") == support.get("unitKey") == neighbor["geometrySupport"].get("unitKey"),
                 "support_stack_glyph_identity_mismatch")
        matches = [item for item in neighbor["geometrySupport"]["visibilityObligations"]
                   if item.get("id") == alternative.get("neighborSupportId")
                   and item.get("primitiveQid") == target["qid"] and item.get("segments") == specification["segments"]]
        _require(len(matches) == 1 and {matches[0]["kind"], specification["kind"]} == {"value-base", "value-end"},
                 "support_stack_glyph_identity_mismatch")
        for owner, source in ((datum, record), (neighbor, target)):
            _require(owner["primitiveQids"] and source["qid"] == owner["primitiveQids"][0],
                     "support_stack_glyph_identity_mismatch")
        siblings = [item for item in specification["alternatives"] if item.get("kind") == "shared-stack-interface"
                    and item.get("neighborDatumId") == neighbor["id"]]
        sibling_fields = ("neighborDatumId", "neighborTargetId", "neighborSupportId", "geometrySupportDigest",
                          "cumulativeValue", "grouping", "axisKey", "datumInteriorSupportId", "neighborInteriorSupportId")
        _require(not siblings or any(all(item.get(key) == alternative[key] for key in sibling_fields) for item in siblings),
                 "support_stack_glyph_sibling_mismatch")
        category = sorted((item for item in chart["datums"] if item["categoryIndex"] == datum["categoryIndex"]),
                          key=lambda item: item["seriesIndex"])
        _require(category and category[0]["seriesIndex"] == 0
                 and all(item["geometryRole"] == "stack-segment" and Decimal(item["value"]) > 0 for item in category),
                 "support_stack_glyph_identity_mismatch")
        amount = sum((Decimal(item["value"]) for item in category
                      if item["seriesIndex"] <= min(datum["seriesIndex"], neighbor["seriesIndex"])), Decimal(0))
        if alternative["grouping"] == "percentStacked":
            amount /= sum((Decimal(item["value"]) for item in category), Decimal(0))
        _require(isinstance(alternative.get("cumulativeValue"), str)
                 and Decimal(alternative["cumulativeValue"]).is_finite()
                 and Decimal(alternative["cumulativeValue"]) == amount, "support_stack_glyph_value_mismatch")
        for owner, key in ((datum, "datumValueGlyphTargetIds"), (neighbor, "neighborValueGlyphTargetIds")):
            expected_ids = list(dict.fromkeys(f"{chart['id']}/glyph/{glyph['qid']}/{span['start']}/{span['length']}"
                for glyph in chart["glyphs"] if glyph["role"] == "value" and glyph["datumIds"] == [owner["id"]]
                for span in glyph["ranges"]))
            _require(expected_ids and _same(alternative.get(key), expected_ids), "support_stack_glyph_set_unproven")
            for identifier in expected_ids:
                glyph = by_id.get(identifier)
                _require(glyph is not None and glyph.get("kind") == "glyph-range"
                         and glyph.get("datumIds") == [owner["id"]] and "value" in glyph.get("roles", [])
                         and glyph.get("complete") is True and glyph.get("paintRetained") is True,
                         "support_stack_glyph_set_unproven")
                _mask(glyph.get("originalContribution"), pixels, ink=True)
        own_region, neighbor_region = alternative.get("datumInteriorSupportId"), alternative.get("neighborInteriorSupportId")
        _association(obligation, {"contributionRegionIds": [own_region, neighbor_region]}, "support_region_association_mismatch")
        native_region(record["id"], own_region, "interior-profile")
        native_region(target["id"], neighbor_region, "interior-profile")
        for key, owner, kind in (("stackBaseline", category[0], "value-base"),
                                 ("stackOuterTotal", category[-1], "value-end")):
            reference = alternative.get(key)
            _require(isinstance(reference, dict) and set(reference) == {"datumId", "targetId", "supportId", "geometrySupportDigest"}
                     and reference["datumId"] == owner["id"]
                     and owner["geometrySupport"].get("axisKey") == support.get("axisKey")
                     and owner["geometrySupport"].get("unitKey") == support.get("unitKey")
                     and reference["geometrySupportDigest"] == owner["geometrySupport"].get("geometryDigest"),
                     "support_stack_endpoint_identity_mismatch")
            endpoint = by_id.get(reference["targetId"])
            endpoint_specs = [item for item in owner["geometrySupport"]["visibilityObligations"]
                              if item.get("id") == reference["supportId"] and item.get("kind") == kind
                              and endpoint is not None and item.get("primitiveQid") == endpoint.get("qid")]
            rows = [] if endpoint is None else [item for item in endpoint.get("visibilityObligations", [])
                                               if item.get("supportId") == reference["supportId"]]
            _require(endpoint is not None and endpoint.get("datumIds") == [owner["id"]]
                     and owner["primitiveQids"] and endpoint["qid"] == owner["primitiveQids"][0]
                     and len(endpoint_specs) == len(rows) == 1, "support_stack_endpoint_identity_mismatch")
            row = rows[0]
            _require(row.get("complete") is True and row.get("paintRetained") is True and row.get("reason") is None
                     and row.get("via") in {"original-boundary-contribution", "calibrated-value-line"},
                     "support_stack_endpoint_unproven")
            if row["via"] == "original-boundary-contribution":
                _subset_mask(row.get("originalContribution"), row.get("footprint"), pixels,
                             "support_stack_endpoint_unproven", ink=True)
                _subset_mask(row["originalContribution"], endpoint.get("originalContribution"), pixels,
                             "support_stack_endpoint_unproven")
                _require(row.get("supportedPixels") == row["originalContribution"]["pixelCount"],
                         "support_stack_endpoint_unproven")
            else:
                _alternative_support(chart, graph, proof, records, endpoint, endpoint_specs[0], row, pixels)
        return

    if via == "equivalent-native-support":
        identifier = obligation.get("classId")
        _require(any(item.get("kind") == via and item.get("classId") == identifier
                     for item in specification["alternatives"]), "support_class_association_mismatch")
        declarations = [item for item in chart["compositionGroup"].get("equivalenceClasses", []) if item.get("id") == identifier]
        resolutions = [item for item in graph["equivalenceClasses"] if item.get("classId") == identifier]
        _require(len(declarations) == len(resolutions) == 1, "support_class_association_mismatch")
        equivalent, resolution = declarations[0], resolutions[0]
        _require(resolution.get("complete") is True and resolution.get("paintRetained") is True
                 and resolution.get("reason") is None and equivalent.get("requiresJointOriginalContribution") is True,
                 "support_class_unproven")
        members = equivalent.get("members")
        _require(isinstance(members, list) and len({item.get("datumId") for item in members}) >= 2
                 and any(item.get("targetId") == record["id"] and item.get("supportId") == specification["id"]
                         for item in members), "support_class_association_mismatch")
        member_specs = []
        for member in members:
            target, owner = by_id.get(member.get("targetId")), datums.get(member.get("datumId"))
            _require(target is not None and owner is not None and target["datumIds"] == [owner["id"]]
                     and member.get("geometrySupportDigest") == owner["geometrySupport"].get("geometryDigest")
                     and equivalent.get("axisKey") == owner["geometrySupport"].get("axisKey")
                     and equivalent.get("unitKey") == owner["geometrySupport"].get("unitKey")
                     and equivalent.get("categoryIndex") == owner["categoryIndex"]
                     and equivalent.get("value") == owner["value"], "support_class_identity_mismatch")
            specs = [item for item in owner["geometrySupport"]["visibilityObligations"]
                     if item.get("id") == member.get("supportId") and item.get("primitiveQid") == target["qid"]]
            _require(len(specs) == 1, "support_class_identity_mismatch")
            member_specs.append(specs[0])
        identity = lambda item: ("incident-edge" if item["kind"].startswith("incident-edge") else item["kind"],
                                 sorted(sorted(segment) for segment in item["segments"]), item.get("ellipse"))
        _require(all(_same(identity(item), identity(member_specs[0])) for item in member_specs), "support_class_identity_mismatch")
        if resolution.get("via") == "contributing-native-member":
            roots = [item for item in members if item["targetId"] == resolution.get("contributorTargetId")]
            proved = False
            for member in roots:
                target = by_id[member["targetId"]]
                rows = [item for item in target.get("visibilityObligations", []) if item.get("supportId") == member["supportId"]]
                if len(rows) == 1 and rows[0].get("via") == "original-boundary-contribution":
                    root = rows[0]
                    _require(root.get("complete") is True and root.get("paintRetained") is True
                             and root.get("supportedPixels") == _mask(root.get("originalContribution"), pixels, ink=True)[0],
                             "support_class_root_unproven")
                    _subset_mask(root["originalContribution"], root["footprint"], pixels, "support_class_root_unproven")
                    _subset_mask(root["originalContribution"], target["originalContribution"], pixels, "support_class_root_unproven")
                    proved = True
            _require(proved, "support_class_root_unproven")
            return
        _require(resolution.get("via") == "joint-original-native-contribution", "support_class_root_unproven")
        joints = [item for item in proof.get("jointNativePaint", []) if item.get("classId") == identifier]
        _require(len(joints) == 1, "support_joint_witness_unproven")
        joint = joints[0]
        _association(joint, {"classId": identifier, "memberTargetIds": [item["targetId"] for item in members]}, "support_joint_identity_mismatch")
        _require(_mask(joint.get("altered"), pixels) == (0, 0)
                 and _same(joint.get("retained"), joint.get("footprint")), "support_joint_paint_altered")
        _subset_mask(joint.get("originalContribution"), joint["footprint"], pixels, "support_joint_witness_unproven", ink=True)
        boundaries = joint.get("memberBoundaries")
        _require(isinstance(boundaries, list) and len(boundaries) == len(members), "support_joint_boundary_unproven")
        for member, boundary in zip(members, boundaries):
            _association(boundary, member, "support_joint_identity_mismatch")
            target = by_id[member["targetId"]]
            rows = [item for item in target.get("visibilityObligations", []) if item.get("supportId") == member["supportId"]]
            _require(len(rows) == 1, "support_joint_boundary_unproven")
            _subset_mask(boundary.get("footprint"), rows[0]["footprint"], pixels, "support_joint_boundary_invalid")
            _subset_mask(boundary.get("footprint"), target["footprint"], pixels, "support_joint_boundary_invalid")
            _subset_mask(boundary.get("originalContribution"), boundary["footprint"], pixels,
                         "support_joint_boundary_unproven", ink=True)
            _require(boundary["footprint"]["pixelCount"] <= joint["footprint"]["pixelCount"]
                     and boundary["originalContribution"]["pixelCount"] <= joint["originalContribution"]["pixelCount"],
                     "support_joint_boundary_invalid")
            _require(_mask(boundary.get("altered"), pixels) == (0, 0)
                     and _same(boundary.get("retained"), boundary["footprint"]), "support_joint_paint_altered")
        return
    alternatives = [item for item in specification["alternatives"] if item.get("kind") == via
                    and item.get("neighborTargetId" if via == "shared-stack-interface" else "gridTargetId") == obligation.get("supportTargetId")]
    _require(len(alternatives) == 1, "support_alternative_unproven")
    alternative = alternatives[0]
    _require(alternative.get("axisKey") == support.get("axisKey"), "support_alternative_identity_mismatch")
    if via == "shared-stack-interface":
        neighbor = datums.get(alternative.get("neighborDatumId"))
        target = by_id.get(alternative.get("neighborTargetId"))
        _require(neighbor is not None and target is not None and target["datumIds"] == [neighbor["id"]]
                 and alternative.get("geometrySupportDigest") == neighbor["geometrySupport"].get("geometryDigest")
                 and neighbor["categoryIndex"] == datum["categoryIndex"]
                 and abs(neighbor["seriesIndex"] - datum["seriesIndex"]) == 1
                 and neighbor["geometrySupport"].get("axisKey") == support.get("axisKey")
                 and neighbor["geometrySupport"].get("unitKey") == support.get("unitKey")
                 and datum["geometryRole"] == neighbor["geometryRole"] == "stack-segment"
                 and alternative.get("grouping") in {"stacked", "percentStacked"}
                 and alternative.get("requiresBothInteriorContribution") is True
                 and alternative.get("requiresBoundaryContrast") is True, "support_stack_identity_mismatch")
        matching = [item for item in neighbor["geometrySupport"]["visibilityObligations"]
                    if item.get("id") == alternative.get("neighborSupportId") and item.get("segments") == specification["segments"]]
        _require(len(matching) == 1, "support_stack_identity_mismatch")
        own_region, neighbor_region = alternative.get("datumInteriorSupportId"), alternative.get("neighborInteriorSupportId")
        _association(obligation, {"contributionRegionIds": [own_region, neighbor_region]}, "support_region_association_mismatch")
        native_region(record["id"], own_region, "interior-profile")
        native_region(target["id"], neighbor_region, "interior-profile")
        witness = obligation.get("stackBoundaryContrast")
        local_witness(witness, {"targetId": record["id"], "supportId": specification["id"],
            "neighborTargetId": target["id"], "neighborSupportId": alternative["neighborSupportId"],
            "datumInteriorSupportId": own_region, "neighborInteriorSupportId": neighbor_region,
            "samplingRule": "normal-interior-native-pair/v1"})
        _subset_mask(witness.get("normalInteriorPairs"), witness["footprint"], pixels, "support_stack_normal_pair_unproven", ink=True)
        _subset_mask(witness.get("nativeContrast"), witness["normalInteriorPairs"], pixels, "support_stack_native_contrast_unproven", ink=True)
        _subset_mask(witness["originalContribution"], witness["nativeContrast"], pixels, "support_stack_contrast_unproven", ink=True)
        samples, viewport = witness.get("samplePairs"), proof.get("viewport")
        _require(isinstance(samples, list) and len(samples) == witness["originalContribution"]["pixelCount"]
                 and isinstance(viewport, dict) and _positive_number(viewport.get("rasterWidth"))
                 and _positive_number(viewport.get("rasterHeight"))
                 and int(viewport["rasterWidth"]) == viewport["rasterWidth"]
                 and int(viewport["rasterHeight"]) == viewport["rasterHeight"]
                 and viewport["rasterWidth"] * viewport["rasterHeight"] == pixels, "support_stack_sample_pairs_unproven")
        boundary_pixels = set()
        for sample in samples:
            _require(isinstance(sample, dict) and set(sample) == {"kind", "boundaryPixel", "negativeInteriorPixel", "positiveInteriorPixel"}
                     and sample.get("kind") in {"adjacent-fill", "native-divider"}, "support_stack_sample_pairs_unproven")
            for key in ("boundaryPixel", "negativeInteriorPixel", "positiveInteriorPixel"):
                point = sample[key]
                _require(isinstance(point, list) and len(point) == 2 and all(_int(value) for value in point)
                         and point[0] < viewport["rasterWidth"] and point[1] < viewport["rasterHeight"], "support_stack_sample_pairs_unproven")
            boundary_pixel = tuple(sample["boundaryPixel"])
            _require(boundary_pixel not in boundary_pixels
                     and sample["negativeInteriorPixel"] != sample["positiveInteriorPixel"]
                     and (sample["kind"] != "native-divider" or len({tuple(sample[key])
                          for key in ("boundaryPixel", "negativeInteriorPixel", "positiveInteriorPixel")}) == 3),
                     "support_stack_sample_pairs_unproven")
            boundary_pixels.add(boundary_pixel)
        return
    _require(via == "calibrated-value-line" and alternative.get("unitKey") == support.get("unitKey")
             and alternative.get("requiresDatumInteriorContribution") is True
             and alternative.get("requiresIncidentSideContribution") is True, "support_alternative_unproven")
    expected_values = {Decimal(0) if specification["kind"] == "value-base" else Decimal(datum["value"])}
    if datum["geometryRole"] == "stack-segment":
        category = [item for item in chart["datums"] if item["categoryIndex"] == datum["categoryIndex"]]
        stop = datum["seriesIndex"] + (specification["kind"] == "value-end")
        cumulative = sum((Decimal(item["value"]) for item in category if item["seriesIndex"] < stop), Decimal(0))
        groupings = {item.get("grouping") for owner in category
                     for boundary in owner["geometrySupport"]["visibilityObligations"]
                     for item in boundary["alternatives"]
                     if item.get("kind") in {"shared-stack-interface", "bound-stack-value-glyphs"}}
        _require(len(groupings) == 1 and groupings <= {"stacked", "percentStacked"}, "support_grid_value_mismatch")
        expected_values = {cumulative}
        if "percentStacked" in groupings:
            total = sum((Decimal(item["value"]) for item in category), Decimal(0))
            _require(total > 0, "support_grid_value_mismatch")
            expected_values = {cumulative / total, cumulative / total * 100}
    _require(isinstance(alternative.get("value"), str) and Decimal(alternative["value"]).is_finite()
             and Decimal(alternative["value"]) in expected_values, "support_grid_value_mismatch")
    grid_id, grid_qid = alternative.get("gridTargetId"), alternative.get("gridQid")
    grid = by_id.get(grid_id)
    _require(grid is not None and grid_id == f"{chart['id']}/primitive/{grid_qid}/furniture"
             and grid["qid"] == grid_qid and grid["kind"] == "primitive" and not grid["datumIds"], "support_grid_identity_mismatch")
    ticks, titles = alternative.get("tickTargetIds"), alternative.get("titleTargetIds")
    _require(_unique_strings(ticks) and ticks and _unique_strings(titles), "support_grid_labels_unproven")
    for identifier in ticks + titles:
        label = by_id.get(identifier)
        _require(label is not None and label["kind"] == "glyph-range"
                 and ("axis" if identifier in ticks else "title-unit") in label["roles"], "support_grid_labels_unproven")
        _mask(label.get("originalContribution"), pixels, ink=True)
    interior, side = alternative.get("datumInteriorSupportId"), alternative.get("datumSideSupportId")
    _association(obligation, {"contributionRegionIds": [interior, side]}, "support_region_association_mismatch")
    native_region(record["id"], interior, "interior-profile")
    native_region(record["id"], side, "incident-sides")
    witness = obligation.get("calibratedGridBoundary")
    local_witness(witness, {"targetId": record["id"], "supportId": specification["id"], "gridTargetId": grid_id,
        "gridQid": grid_qid, "datumInteriorSupportId": interior, "datumSideSupportId": side,
        "tickTargetIds": ticks, "titleTargetIds": titles})
    _require(witness["footprint"]["pixelCount"] <= grid["footprint"]["pixelCount"]
             and witness["originalContribution"]["pixelCount"] <= grid["originalContribution"]["pixelCount"], "support_grid_mask_invalid")


def _direct_support_graphs(manifest, proof, records, pixels):
    """Consume every declared boundary and its measured native support root."""
    graphs = proof.get("supportGraphs")
    _require(isinstance(graphs, list) and len(graphs) == len(manifest["charts"]), "support_graph_unproven")
    declared_classes = {item["id"]: item for chart in manifest["charts"]
                        for item in chart["compositionGroup"].get("equivalenceClasses", [])}
    joints = proof.get("jointNativePaint", [])
    _require(isinstance(joints, list) and all(isinstance(item, dict) and item.get("classId") in declared_classes
                                             for item in joints)
             and len({item["classId"] for item in joints}) == len(joints), "support_joint_identity_mismatch")
    for joint in joints:
        _association(joint, {"memberTargetIds": [item["targetId"] for item in declared_classes[joint["classId"]]["members"]]},
                     "support_joint_identity_mismatch")
    for chart, graph in zip(manifest["charts"], graphs):
        _require(isinstance(graph, dict) and graph.get("complete") is True
                 and graph.get("paintRetained") is True and graph.get("unproven") == [], "support_graph_unproven")
        chart_records = [record for record in records if record["chartId"] == chart["id"]]
        resolved = graph.get("targets")
        classes = graph.get("equivalenceClasses")
        expected_classes = chart["compositionGroup"].get("equivalenceClasses", [])
        _require(isinstance(classes, list) and len(classes) == len(expected_classes)
                 and all(isinstance(row, dict) and row.get("classId") == expected["id"]
                         for row, expected in zip(classes, expected_classes)), "support_class_association_mismatch")
        _require(isinstance(resolved, list) and len(resolved) == len(chart_records), "support_target_association_mismatch")
        for record, result in zip(chart_records, resolved):
            _association(result, {"targetId": record["id"], "complete": True, "paintRetained": True,
                                  "reason": None}, "support_target_association_mismatch")
            support = record.get("geometrySupport", {})
            specifications = support.get("visibilityObligations", [])
            _require(isinstance(specifications, list) and all(isinstance(item, dict) for item in specifications)
                     and len({item.get("id") for item in specifications}) == len(specifications), "support_specification_invalid")
            expected = [item for item in specifications if item.get("primitiveQid") == record["qid"]]
            obligations = result.get("obligations")
            _require(isinstance(obligations, list) and len(obligations) == len(expected)
                     and _same(record.get("visibilityObligations"), obligations), "support_obligation_association_mismatch")
            if record["kind"] == "primitive" and record["datumIds"]:
                _require(isinstance(support.get("visibilityObligations"), list), "support_graph_unproven")
            if not expected:
                _mask(record.get("originalContribution"), pixels, ink=True)
            regions = support.get("contributionRegions", [])
            expected_regions = [region for region in regions if region.get("primitiveQid") == record["qid"]]
            observed_regions = record.get("contributionRegions", [])
            _require(isinstance(observed_regions, list) and len(observed_regions) == len(expected_regions), "support_region_association_mismatch")
            for specification, region in zip(expected_regions, observed_regions):
                _association(region, {"id": specification["id"], "kind": specification["kind"]}, "support_region_association_mismatch")
                footprint = _mask(region.get("footprint"), pixels)
                contribution = _mask(region.get("originalContribution"), pixels)
                _require(contribution[0] <= footprint[0] <= record["footprint"]["pixelCount"]
                         and contribution[1] <= footprint[1] <= record["footprint"]["alphaSum"]
                         and contribution[0] <= record["originalContribution"]["pixelCount"]
                         and contribution[1] <= record["originalContribution"]["alphaSum"], "support_region_mask_invalid")
            for specification, obligation in zip(expected, obligations):
                kind = specification.get("kind")
                _require(isinstance(specification.get("id"), str) and specification["id"]
                         and isinstance(kind, str) and (kind in {"value-end", "value-base", "value-anchor", "polar-outer-arc"}
                         or re.fullmatch(r"incident-edge-\d+", kind) is not None)
                         and specification.get("regionRule") == "native-alpha-boundary-neighborhood/v1"
                         and type(specification.get("radiusPhysicalPixels")) in (int, float)
                         and specification["radiusPhysicalPixels"] == 1
                         and isinstance(specification.get("segments"), list)
                         and isinstance(specification.get("alternatives"), list), "support_specification_invalid")
                for segment in specification["segments"]:
                    _require(isinstance(segment, list) and len(segment) == 2 and all(
                        isinstance(point, list) and len(point) == 2 and all(
                            type(value) in (int, float) and math.isfinite(value) for value in point)
                        for point in segment), "support_specification_invalid")
                ellipse = specification.get("ellipse")
                if ellipse is not None:
                    _require(isinstance(ellipse, dict) and set(ellipse) == {"center", "radii", "startAngle", "sweepAngle"}
                             and all(isinstance(ellipse[key], list) and len(ellipse[key]) == 2
                                     and all(type(value) in (int, float) and math.isfinite(value) for value in ellipse[key])
                                     for key in ("center", "radii"))
                             and all(value > 0 for value in ellipse["radii"])
                             and type(ellipse["startAngle"]) in (int, float) and math.isfinite(ellipse["startAngle"])
                             and _positive_number(ellipse["sweepAngle"]), "support_specification_invalid")
                _require(specification["segments"] or ellipse is not None, "support_specification_invalid")
                _association(obligation, {"supportId": specification["id"], "complete": True,
                                          "paintRetained": True, "reason": None}, "support_obligation_association_mismatch")
                boundary, original = _mask(obligation.get("footprint"), pixels), _mask(obligation.get("originalContribution"), pixels)
                _require(_int(obligation.get("boundaryPixels")) and obligation["boundaryPixels"] == boundary[0]
                         and _int(obligation.get("supportedPixels")) and obligation["supportedPixels"] <= pixels
                         and original[0] <= boundary[0] <= record["footprint"]["pixelCount"]
                         and original[1] <= boundary[1] <= record["footprint"]["alphaSum"]
                         and original[0] <= record["originalContribution"]["pixelCount"]
                         and original[1] <= record["originalContribution"]["alphaSum"], "support_boundary_mask_invalid")
                if obligation.get("via") == "original-boundary-contribution":
                    _require(original[0] > 0 and obligation["supportedPixels"] == original[0], "support_original_contribution_unproven")
                else:
                    _require(obligation["supportedPixels"] == (boundary[0] if obligation.get("via") == "marker-on-datum" else original[0]),
                             "support_original_count_mismatch")
                    _alternative_support(chart, graph, proof, chart_records, record, specification, obligation, pixels)


def validate_chart_content_paint(manifest, quality, *, capture=None) -> dict:
    """Validate current receipt bindings, with no geometry-only paint fallback.

    Actual PNG/wrapper bytes and every demanded visibility obligation are
    mandatory; target association alone cannot authorize retained content.
    """
    try:
        _require(isinstance(manifest, dict) and isinstance(quality, dict), "carrier_invalid")
        canonical_json_bytes(manifest)
        _require(manifest.get("schema") == "svg-native-chart-witness/v1"
                 and manifest.get("requirementsRevision") == "svg-native-requirements/v5", "manifest_schema_invalid")
        _require(_hash(manifest.get("manifestDigest")) and manifest["manifestDigest"] == _digest({
            key: value for key, value in manifest.items() if key not in {"manifestDigest", "captureContext"}}), "manifest_digest_mismatch")
        _require(_hash(manifest.get("canonicalSvgDigest")) and _hash(manifest.get("treeDerivativeDigest")), "manifest_source_invalid")
        targets, needs_local, separator_pending = _targets(manifest)
        metrics = quality.get("browser_metrics")
        _require(isinstance(metrics, dict), "metrics_missing")
        canonical_json_bytes(metrics)
        proof = metrics.get(_KEY)
        _require(isinstance(proof, dict) and proof.get("schema") == _SCHEMA
                 and proof.get("probeVersion") == _PROBE, "proof_schema_invalid")
        _require(proof.get("paintDomain") == "captured-viewport/v1", "paint_domain_unproven")
        _require(proof.get("complete") is True, "proof_incomplete")
        _require(proof.get("paintRetained") is True, "proof_paint_not_retained")
        _require(_hash(quality.get("metrics_digest")) and quality["metrics_digest"] == _metrics_digest(metrics), "metrics_digest_mismatch")
        _require(_hash(proof.get("baseMetricsDigest")) and proof["baseMetricsDigest"] == _base_metrics_digest(metrics), "base_metrics_digest_mismatch")
        for proof_key, actual in (("canonicalSvgDigest", manifest["canonicalSvgDigest"]),
                                  ("treeDerivativeDigest", manifest["treeDerivativeDigest"]),
                                  ("wrapperDigest", quality.get("wrapper_digest")), ("pngDigest", quality.get("png_digest"))):
            _require(_hash(actual) and proof.get(proof_key) == actual, "capture_digest_mismatch")
        _require(quality.get("source_digest") == manifest["canonicalSvgDigest"]
                 and metrics.get("source_digest") == quality["wrapper_digest"], "source_digest_mismatch")
        _require(_hash(metrics.get("render_digest")) and proof.get("admittedRenderDigest") == metrics["render_digest"], "render_digest_mismatch")
        _require(proof.get("manifestDigest") == manifest["manifestDigest"], "manifest_receipt_mismatch")
        width, height, viewport = metrics.get("width"), metrics.get("height"), proof.get("viewport")
        _require(_int(width, 1) and _int(height, 1) and isinstance(viewport, dict)
                 and set(viewport) == {"cssWidth", "cssHeight", "dpr", "rasterWidth", "rasterHeight"}, "viewport_invalid")
        _require(type(viewport["cssWidth"]) is int and viewport["cssWidth"] == width
                 and type(viewport["cssHeight"]) is int and viewport["cssHeight"] == height, "viewport_mismatch")
        dpr, raster_width, raster_height = (viewport[key] for key in ("dpr", "rasterWidth", "rasterHeight"))
        _require(all(_positive_number(value) for value in (dpr, raster_width, raster_height))
                 and raster_width == width * dpr and raster_height == height * dpr
                 and int(raster_width) == raster_width and int(raster_height) == raster_height, "viewport_raster_mismatch")
        _capture_binding(capture, manifest, quality, proof, width, height, viewport)
        if separator_pending:
            return {"passed": False, "reason": "chart_content_paint_separator_paint_receipt_unproven",
                    "captureContextVerified": True}
        materials = proof.get("materialIdentities")
        _require(isinstance(materials, dict) and isinstance(materials.get("embeddedResources"), list), "material_identity_unproven")
        resources = materials["embeddedResources"]
        _require(all(isinstance(row, dict) and set(row) == {"mimeType", "digest"}
                     and isinstance(row["mimeType"], str) and row["mimeType"] and _hash(row["digest"])
                     for row in resources), "material_identity_unproven")
        _require(_same(resources, sorted(resources, key=lambda row: (row["mimeType"], row["digest"]))), "material_identity_unproven")
        embedded_fonts = {row["digest"] for row in resources if row["mimeType"].startswith("font/")}
        observed = proof.get("targets")
        _require(isinstance(observed, list) and len(observed) == len(targets), "target_association_mismatch")
        pixels = int(raster_width) * int(raster_height)
        for expected, record in zip(targets, observed):
            _association(record, expected, "target_association_mismatch")
            _require(record.get("complete") is True, "target_incomplete")
            _require(record.get("paintRetained") is True, "target_paint_not_retained")
            _require(record.get("unresolvedRanges") == [] and _unique_strings(record.get("foreignOccluderQids"), qids=True)
                     and record.get("clippedQids") == [], "target_visibility_unresolved")
            _partition(record, pixels)
            contribution = _mask(record.get("originalContribution"), pixels, ink=expected["kind"] == "glyph-range")
            _require(contribution[0] <= record["footprint"]["pixelCount"]
                     and contribution[1] <= record["footprint"]["alphaSum"], "original_contribution_invalid")
            if expected["kind"] == "glyph-range":
                _glyphs(record, pixels, embedded_fonts)
            else:
                joint = _mask(record.get("jointPrimitiveFootprint"), pixels, ink=True)
                _require(record.get("regionRule") == "canonical-canvas-pixel-center-inclusive/v1"
                         and joint[0] >= record["footprint"]["pixelCount"] and joint[1] >= record["footprint"]["alphaSum"],
                         "datum_local_footprint_unproven")
        _chart_links(manifest, proof, targets)
        binding = {"receiptBindingVerified": True, "captureContextVerified": True,
                   "perClusterFontAttributionVerified": False, "fontAttributionScope": "selected-target-aggregate"}
        if needs_local:
            return {"passed": False, "reason": "chart_content_paint_datum_local_support_unproven", **binding}
        try:
            _direct_support_graphs(manifest, proof, observed, pixels)
        except _Unproven as error:
            return {"passed": False, "reason": "chart_content_paint_" + str(error), **binding}
        return {"passed": True, "reason": "chart_content_paint_captured_native_composition_retained",
                "scope": "emitted-native-raster-composition", "physicalExposureVerified": False, **binding}
    except _Unproven as error:
        return {"passed": False, "reason": "chart_content_paint_" + str(error)}
    except (KeyError, TypeError, ValueError, OverflowError, InvalidOperation, UnicodeError, RecursionError, ET.ParseError):
        return {"passed": False, "reason": "chart_content_paint_carrier_invalid"}
