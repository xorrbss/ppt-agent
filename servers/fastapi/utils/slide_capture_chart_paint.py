"""Private, resolution-bound chart paint observations from the existing CDP page.

This module measures browser paint, not chart semantics. Only the server-side
chart witness builder may decide which primitives, glyphs and overlaps belong
to a datum. Missing attribution remains unproven rather than geometric success.
"""
from __future__ import annotations

from utils.slide_capture_chart_support import mask_record as _mask_record

import math

import base64
from collections.abc import Mapping
import io
import json
from functools import lru_cache
import os
from pathlib import Path
import re
import unicodedata
import xml.etree.ElementTree as ET

from utils.hashing import canonical_json_bytes, sha256_prefixed

PROOF_KEY = "nativeChartPaintProof"
PROOF_SCHEMA = "svg-native-chart-paint-proof/v1"
PROBE_VERSION = "svg-chart-native-alpha-dual-paint/v1"
MANIFEST_SCHEMA = "svg-native-chart-witness/v1"


def _digest(value):
    return sha256_prefixed(canonical_json_bytes(value))


def _manifest_digest(manifest):
    digest = _digest({key: value for key, value in manifest.items() if key not in {"manifestDigest", "captureContext"}})
    if manifest.get("manifestDigest") != digest:
        raise ValueError("chart_witness_manifest_digest_mismatch")
    return digest


def _node_tree(source):
    root = _svg_root(source)
    nodes = list(root.iter())
    parents = {child: node for node in nodes for child in node}
    indexes = {node: index for index, node in enumerate(nodes)}
    def qid(node):
        return f"svg-{indexes[node]}"
    tree = [{"qid": qid(node), "parentQid": qid(parents[node]) if node in parents else None,
             "tag": node.tag, "attributes": {key: value for key, value in node.attrib.items() if key != "data-qid"},
             "text": node.text or "", "tail": node.tail or ""} for node in nodes]
    return tree


def _tree_digest(source):
    return _digest({"schema": "svg-chart-node-tree/v1", "nodes": _node_tree(source)})


def _text_projection(source):
    """Bind canonical direct-owned text through authorized font-only tspans."""
    root = _svg_root(source)
    nodes = list(root.iter())
    qids = {node: node.get("data-qid") or f"svg-{index}" for index, node in enumerate(nodes)}
    result = []
    for text_root in nodes:
        if text_root.tag.rsplit("}", 1)[-1] != "text":
            continue
        root_text = "".join(text_root.itertext())
        owners = {}
        offset = 0
        def piece(owner, value, *, owners=owners):
            nonlocal offset
            value = value or ""
            length = len(value.encode("utf-16-le"))//2
            if value.strip():
                item = owners.setdefault(owner, {"text": "", "segments": []})
                item["segments"].append({"rootStartUtf16": offset, "rootEndUtf16": offset+length,
                                         "ownedStartUtf16": len(item["text"].encode("utf-16-le"))//2})
                item["text"] += value
            offset += length
        def visit(node):
            piece(node, node.text)
            for child in node:
                visit(child)
                piece(node, child.tail)
        visit(text_root)
        for owner in text_root.iter():
            if owner not in owners:
                continue
            item = owners[owner]
            result.append({"ownerQid": qids[owner], "textRootQid": qids[text_root],
                           "rootTextDigest": sha256_prefixed(root_text.encode("utf-8")),
                           "ownedTextDigest": sha256_prefixed(item["text"].encode("utf-8")),
                           "segments": item["segments"]})
    return result


def _targets(manifest):
    """Adapt the chart owner's semantic witness; accept no author allowlist."""
    def finite_coordinate(value):
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return False
        try:
            return math.isfinite(value)
        except OverflowError:
            return False
    result = []
    for chart in manifest["charts"]:
        group = chart["compositionGroup"]
        primitives = group["primitiveQids"]
        glyph_targets = {}
        for glyph in chart["glyphs"]:
            for span in glyph["ranges"]:
                key = (glyph["qid"], span["start"], span["length"])
                item = glyph_targets.setdefault(key, {
                    "id": f'{chart["id"]}/glyph/{key[0]}/{key[1]}/{key[2]}', "chartId": chart["id"],
                    "qid": key[0], "kind": "glyph-range", "startUtf16": key[1], "endUtf16": key[1]+key[2],
                    "ownedTextDigest": glyph["ownedTextDigest"], "offsetScope": "owned-text-nodes",
                    "roles": [], "datumIds": []})
                if glyph["role"] not in item["roles"]:
                    item["roles"].append(glyph["role"])
                item["datumIds"].extend(value for value in glyph["datumIds"] if value not in item["datumIds"])
        primitive_owners = [(qid, datum) for qid in primitives for datum in chart["datums"]
                            if qid in datum["primitiveQids"]]
        primitive_owners.extend((qid, None) for qid in primitives
                                if not any(qid in datum["primitiveQids"] for datum in chart["datums"]))
        for qid, datum in primitive_owners:
            datum_ids = [datum["id"]] if datum is not None else []
            owned = []
            owned_paints = {}
            def own(source_qid, paint, *, owned=owned, owned_paints=owned_paints):
                owned.append(source_qid)
                if owned_paints.get(source_qid) != "all":
                    owned_paints[source_qid] = paint
            owned_ranges = []
            local_relationships = []
            for relation in group["relationships"]:
                kind = relation["kind"]
                if kind == "value-on-datum" and relation["datumId"] in datum_ids:
                    owned_ranges.append({key: relation[key] for key in ("qid", "start", "length")})
                elif kind == "marker-on-datum" and relation["datumId"] in datum_ids:
                    own(relation["qid"], "all")
                for overlap in relation.get("localOverlaps", []):
                    if overlap.get("datumId") in datum_ids and overlap.get("targetQid") == qid:
                        if (datum is None or overlap.get("geometrySupportDigest") != datum.get("geometrySupport", {}).get("geometryDigest")
                                or overlap.get("geometrySupport") != {key: value for key, value in datum.get("geometrySupport", {}).items() if key in {"anchor", "categoryStrip", "baseline"}}):
                            raise ValueError("chart_owned_overlap_support_mismatch")
                        rule = overlap.get("regionRule")
                        if rule == "native-alpha-intersection-in-datum-support/v1":
                            paint = "all"
                        elif rule == "native-separator-stroke-intersection-in-datum-support/v1":
                            segments = overlap.get("boundarySegments")
                            if (kind != "proved-polar-adjacency" or overlap.get("occluderPaint") != "stroke"
                                    or overlap.get("occluderQid") not in primitives
                                    or not isinstance(segments, list) or not segments
                                    or any(not isinstance(segment, list) or len(segment) != 2
                                           or any(not isinstance(point, list) or len(point) != 2
                                                  or any(not finite_coordinate(value) for value in point)
                                                  for point in segment) for segment in segments)):
                                raise ValueError("chart_owned_separator_stroke_invalid")
                            paint = "stroke"
                        else:
                            raise ValueError("chart_owned_overlap_rule_unsupported")
                        own(overlap["occluderQid"], paint)
                        local_relationships.append({"kind": kind, **overlap})
            identity = f'{chart["id"]}/primitive/{qid}' + (f'/{datum["id"]}' if datum is not None else '/furniture')
            result.append({"id": identity, "chartId": chart["id"], "qid": qid,
                           "kind": "primitive", "datumIds": datum_ids,
                           "geometrySupport": datum.get("geometrySupport", {}) if datum is not None else {},
                           "ownedRelationships": local_relationships,
                           "ownedGlyphRanges": owned_ranges,
                           "ownedPaints": {source_qid: owned_paints[source_qid]
                                           for source_qid in sorted(set(owned)-{qid}, key=lambda value: int(value[4:]))},
                           "ownedQids": sorted(set(owned)-{qid}, key=lambda value: int(value[4:]))})
        result.extend(glyph_targets.values())
    return result


def _base_metrics_digest(metrics):
    # Exactly RenderCapture's metrics encoding, without the proof itself.
    portable = json.loads(json.dumps(dict(metrics)).replace("\\u0000", "\\u241f"))
    return sha256_prefixed(json.dumps(
        {key: value for key, value in portable.items() if key != PROOF_KEY},
        sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str,
    ).encode("utf-8"))


def validate_manifest(manifest, *, source_html=None, width_px=None, height_px=None):
    """Validate an internal carrier without author-controlled overlap expansion."""
    if not isinstance(manifest, dict):
        raise ValueError("chart_witness_manifest_invalid")
    # The canonical serializer rejects NaN and non-JSON carrier values.
    canonical_json_bytes(manifest)
    if manifest.get("schema") != MANIFEST_SCHEMA:
        raise ValueError("chart_witness_manifest_schema_invalid")
    _manifest_digest(manifest)
    if manifest.get("requirementsRevision") != "svg-native-requirements/v5":
        raise ValueError("chart_witness_manifest_revision_invalid")
    if not isinstance(manifest.get("charts"), list) or not manifest["charts"]:
        raise ValueError("chart_witness_manifest_charts_invalid")
    for chart in manifest["charts"]:
        if (not isinstance(chart, dict) or not isinstance(chart.get("datums"), list) or not chart["datums"]
                or not isinstance(chart.get("compositionGroup"), dict)
                or not (chart["compositionGroup"].get("primitiveQids") or chart.get("glyphs"))):
            raise ValueError("chart_witness_manifest_chart_empty")
    try:
        targets = _targets(manifest)
    except (KeyError, TypeError, ValueError) as error:
        raise ValueError("chart_witness_manifest_targets_invalid") from error
    if not targets:
        raise ValueError("chart_witness_manifest_targets_empty")
    identifiers = set()
    for target in targets:
        if (not isinstance(target, dict) or not isinstance(target.get("id"), str)
                or target["id"] in identifiers
                or re.fullmatch(r"svg-\d+", str(target.get("qid", ""))) is None
                or target.get("kind") not in {"primitive", "glyph-range"}):
            raise ValueError("chart_witness_manifest_target_invalid")
        identifiers.add(target["id"])
        if target["kind"] == "glyph-range":
            for key in ("startUtf16", "endUtf16"):
                if type(target.get(key)) is not int or target[key] < 0:
                    raise ValueError("chart_witness_manifest_range_invalid")
            if target["startUtf16"] >= target["endUtf16"]:
                raise ValueError("chart_witness_manifest_range_invalid")
    if not isinstance(manifest.get("canonicalSvgDigest"), str) or re.fullmatch(
            r"sha256:[a-f0-9]{64}", manifest["canonicalSvgDigest"]) is None:
        raise ValueError("chart_witness_manifest_source_invalid")
    if source_html is not None:
        rows = _node_tree(source_html)
        qids = {row["qid"] for row in rows}
        if any(target["qid"] not in qids for target in targets):
            raise ValueError("chart_witness_manifest_qid_missing")
        context = manifest.get("captureContext")
        expected_tree = context.get("treeDerivativeDigest") if context is not None else manifest.get("treeDerivativeDigest")
        if _tree_digest(source_html) != expected_tree:
            raise ValueError("chart_witness_manifest_tree_mismatch")
        if context is None:
            indexed = {row["qid"]: row for row in rows}
            for binding in manifest.get("nodeBindings", []):
                row = indexed.get(binding.get("qid"))
                if (row is None or binding.get("parentQid") != row["parentQid"]
                        or binding.get("tag") != row["tag"].rsplit("}", 1)[-1]
                        or binding.get("attributesDigest") != _digest(row["attributes"])):
                    raise ValueError("chart_witness_manifest_node_binding_mismatch")
                owned = "".join(value for value in [row["text"], *(child["tail"] for child in rows if child["parentQid"] == row["qid"])]
                                if value and value.strip())
                if binding.get("ownedTextDigest") is not None and binding["ownedTextDigest"] != sha256_prefixed(owned.encode("utf-8")):
                    raise ValueError("chart_witness_manifest_owned_text_mismatch")
        if context is not None:
            if (context.get("schema") != "svg-chart-capture-context/v1" or
                    context.get("wrapperDigest") != sha256_prefixed(source_html.encode("utf-8")) or
                    context.get("viewport") != {"cssWidth": width_px, "cssHeight": height_px} or
                    context.get("textProjection") != _text_projection(source_html)):
                raise ValueError("chart_witness_capture_context_mismatch")
    return manifest


def prepare_chart_capture_manifest(manifest, *, canonical_svg, wrapper_html, width_px, height_px):
    """Service-owned runtime binding; keep the semantic builder digest intact."""
    validate_manifest(manifest, source_html=canonical_svg, width_px=width_px, height_px=height_px)
    if manifest["canonicalSvgDigest"] != sha256_prefixed(canonical_svg.encode("utf-8")):
        raise ValueError("chart_witness_manifest_canonical_svg_mismatch")
    copied = json.loads(canonical_json_bytes(manifest))
    copied["captureContext"] = {"schema": "svg-chart-capture-context/v1",
                                "treeDerivativeDigest": _tree_digest(wrapper_html),
                                "wrapperDigest": sha256_prefixed(wrapper_html.encode("utf-8")),
                                "textProjection": _text_projection(wrapper_html),
                                "viewport": {"cssWidth": width_px, "cssHeight": height_px}}
    validate_manifest(copied, source_html=wrapper_html, width_px=width_px, height_px=height_px)
    return copied


def _svg_root(source):
    # Greedy to the last closing tag: a nested <svg> must not truncate the document.
    match = re.search(r"(<(?:[\w.-]+:)?svg\b.*</(?:[\w.-]+:)?svg>)", source, re.S | re.I)
    if match is None:
        raise ValueError("chart_witness_svg_missing")
    try:
        return ET.fromstring(match.group(1))
    except ET.ParseError as error:
        raise ValueError("chart_witness_svg_invalid") from error


def _font_faces(payload):
    from fontTools.ttLib import TTCollection, TTFont
    stream = io.BytesIO(payload)
    collection = TTCollection(stream, lazy=True) if payload[:4] == b"ttcf" else None
    faces = collection.fonts if collection is not None else [TTFont(stream, lazy=True)]
    try:
        records = []
        for index, face in enumerate(faces):
            record = {"postScriptName": face["name"].getDebugName(6), "faceIndex": index,
                      "coverage": frozenset(code for code, glyph in (face.getBestCmap() or {}).items()
                                            if glyph != ".notdef"),
                      "fontDigest": sha256_prefixed(payload)}
            if "fvar" in face:
                axes = face["fvar"].axes
                valid_axes = bool(axes) and all(
                    all(isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)
                        for value in (axis.minValue, axis.defaultValue, axis.maxValue))
                    and axis.minValue <= axis.defaultValue <= axis.maxValue for axis in axes)
                variable_name = face["name"].getDebugName(25)
                if valid_axes and isinstance(variable_name, str) and variable_name.strip():
                    record["variablePostScriptName"] = variable_name
            records.append(record)
        return records
    finally:
        if collection is not None:
            collection.close()
        else:
            faces[0].close()


@lru_cache(maxsize=128)
def _installed_face(postscript):
    """Read only the fixed OS font roots used by authored_font_identity."""
    from fontTools.ttLib import TTCollection, TTFont
    if os.name == "nt":
        roots = [Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts"]
        if os.environ.get("LOCALAPPDATA"):
            roots.append(Path(os.environ["LOCALAPPDATA"]) / "Microsoft/Windows/Fonts")
    else:
        roots = [Path("/usr/share/fonts"), Path("/usr/local/share/fonts"), Path.home()/".fonts",
                 Path.home()/".local/share/fonts", Path("/System/Library/Fonts"), Path("/Library/Fonts")]
    found = []
    for root in roots:
        if not root.is_dir():
            continue
        for path in sorted(root.rglob("*")):
            if path.suffix.lower() not in {".ttf", ".otf", ".ttc", ".woff", ".woff2"} or not path.is_file():
                continue
            try:
                collection = TTCollection(str(path), lazy=True) if path.suffix.lower() == ".ttc" else None
                faces = collection.fonts if collection is not None else [TTFont(str(path), lazy=True)]
                try:
                    matches = any(face["name"].getDebugName(6) == postscript for face in faces)
                finally:
                    if collection is not None:
                        collection.close()
                    else:
                        faces[0].close()
                if matches:
                    found.extend(face for face in _font_faces(path.read_bytes()) if face["postScriptName"] == postscript)
            except Exception:
                # One unreadable system font (TTLibError, a WOFF2 without brotli, ...)
                # must not make every glyph target unprovable.
                continue
    return tuple(found)


def _glyph_font_evidence(command, embedded, target):
    """Bind required codepoints to actual Chromium-selected font faces."""
    requested = _evaluate(command, """(() => window.__chartPaint.selected.map((node,index)=>{
      node.setAttribute('data-chart-paint-selected',String(index));return {index,text:node.textContent};}))()""")
    command("DOM.enable", {})
    command("CSS.enable", {})
    document = command("DOM.getDocument", {"depth": 0})["root"]["nodeId"]
    observations = []
    for selected in requested:
        node_id = command("DOM.querySelector", {"nodeId": document,
                          "selector": f'[data-chart-paint-selected="{selected["index"]}"]'})["nodeId"]
        fonts = command("CSS.getPlatformFontsForNode", {"nodeId": node_id}).get("fonts", [])
        required = {ord(char) for char in selected["text"] if not char.isspace()
                    and unicodedata.category(char) != "Cf" and not 0xFE00 <= ord(char) <= 0xFE0F
                    and not 0xE0100 <= ord(char) <= 0xE01EF}
        coverage = set()
        recorded = []
        for font in fonts:
            postscript = font.get("postScriptName")
            if not isinstance(postscript, str) or type(font.get("glyphCount")) is not int or font["glyphCount"] <= 0:
                continue
            if font.get("isCustomFont") is True:
                candidates = { (face["fontDigest"], face["faceIndex"]): face for face in embedded
                    if postscript == face["postScriptName"] or postscript == face.get("variablePostScriptName") }
                if len(candidates) > 1:
                    return {"complete": False, "reason": "selected_font_resource_ambiguous"}
                faces = list(candidates.values())
            else:
                faces = _installed_face(postscript)
                if len({face["fontDigest"] for face in faces}) > 1:
                    return {"complete": False, "reason": "selected_font_resource_ambiguous"}
            for face in faces:
                coverage.update(face["coverage"])
                recorded.append({"postScriptName": postscript, "fontDigest": face["fontDigest"],
                                 "glyphCount": font["glyphCount"], "isCustomFont": font.get("isCustomFont") is True})
        if required - coverage:
            return {"complete": False, "reason": "font_glyph_coverage_unproven"}
        observations.append({"utf16Length": len(selected["text"].encode("utf-16-le"))//2, "faces": recorded})
    return {"complete": True, "runs": observations}


def finalize_chart_paint_proof(metrics, png, manifest, *, wrapper_html):
    result = dict(metrics or {})
    proof = dict(result.get(PROOF_KEY) or {})
    immutable = {"schema": PROOF_SCHEMA, "probeVersion": PROBE_VERSION,
                 "paintDomain": "captured-viewport/v1",
                 "canonicalSvgDigest": manifest["canonicalSvgDigest"],
                 "treeDerivativeDigest": manifest["treeDerivativeDigest"],
                 "wrapperDigest": sha256_prefixed(wrapper_html.encode("utf-8")),
                 "manifestDigest": _manifest_digest(manifest), "pngDigest": sha256_prefixed(png),
                 "captureContextDigest": _digest(manifest.get("captureContext"))}
    if any(proof.get(key) != value for key, value in immutable.items()):
        raise ValueError("chart_paint_capture_identity_mismatch")
    proof["baseMetricsDigest"] = _base_metrics_digest(result)
    result[PROOF_KEY] = proof
    return result


def validate_chart_paint_binding(metrics, png, manifest, *, wrapper_html, width_px, height_px, device_scale_factor=1):
    proof = metrics.get(PROOF_KEY) if isinstance(metrics, Mapping) else None
    expected = {"schema": PROOF_SCHEMA, "probeVersion": PROBE_VERSION,
                "paintDomain": "captured-viewport/v1",
                "canonicalSvgDigest": manifest["canonicalSvgDigest"],
                "treeDerivativeDigest": manifest["treeDerivativeDigest"],
                "wrapperDigest": sha256_prefixed(wrapper_html.encode("utf-8")),
                "manifestDigest": _manifest_digest(manifest), "pngDigest": sha256_prefixed(png),
                "baseMetricsDigest": _base_metrics_digest(metrics or {})}
    if not isinstance(proof, Mapping) or any(proof.get(key) != value for key, value in expected.items()):
        raise ValueError("chart_paint_proof_identity_mismatch")
    viewport = proof.get("viewport") or {}
    if viewport != {"cssWidth": width_px, "cssHeight": height_px, "dpr": device_scale_factor,
                    "rasterWidth": width_px*device_scale_factor, "rasterHeight": height_px*device_scale_factor}:
        raise ValueError("chart_paint_proof_viewport_mismatch")
    context = manifest.get("captureContext")
    contexts = {_digest(context)}
    if context is None:
        contexts.add(_digest({"schema": "svg-chart-capture-context/v1", "treeDerivativeDigest": _tree_digest(wrapper_html),
                              "wrapperDigest": sha256_prefixed(wrapper_html.encode("utf-8")),
                              "textProjection": _text_projection(wrapper_html),
                              "viewport": {"cssWidth": width_px, "cssHeight": height_px}}))
    if proof.get("captureContextDigest") not in contexts:
        raise ValueError("chart_paint_proof_capture_context_mismatch")
    observed = proof.get("targets")
    if not isinstance(observed, list) or [item.get("id") for item in observed] != [
            item["id"] for item in _targets(manifest)]:
        raise ValueError("chart_paint_proof_targets_mismatch")
    if any(any(record.get(key) != value for key, value in target.items())
           for target, record in zip(_targets(manifest), observed, strict=False)):
        raise ValueError("chart_paint_proof_target_association_mismatch")


_SETUP = r"""(async () => {
  const cfg=__CONFIG__, svg=document.querySelector('body>svg');
  if(!svg || window.innerWidth!==cfg.width || window.innerHeight!==cfg.height || window.devicePixelRatio!==cfg.dpr)
    throw Error('viewport_mismatch');
  await document.fonts.ready;
  const images=Array.from(svg.querySelectorAll('image'));
  await Promise.all(images.map(node=>new Promise(resolve=>{
    const image=new Image();image.onload=()=>resolve();image.onerror=()=>resolve();
    image.src=node.href.baseVal;node.__chartImage=image;
  })));
  if(images.some(node=>!node.__chartImage.complete || !node.__chartImage.naturalWidth)) throw Error('image_unavailable');
  const all=Array.from(svg.querySelectorAll('*'));
  for(const node of [svg,...all]) {
    const css=getComputedStyle(node);
    if(css.filter!=='none' || css.mixBlendMode!=='normal' || css.clipPath!=='none' ||
       (css.maskImage||'none')!=='none' || css.animationName!=='none') throw Error('compositing_unproven');
  }
  const leaves=all.filter(node=>!['g','text','tspan','defs','style'].includes(node.localName) || node.localName==='text');
  const saved=Array.from(document.querySelectorAll('*')).map(node=>[node,node.getAttribute('style')]);
  const restore=()=>{for(const [node,style] of [...saved,...(window.__chartPaint?.extraSaved||[])]) {if(style===null)node.removeAttribute('style');else node.setAttribute('style',style);}};
  window.__chartPaint={cfg,svg,all,leaves,saved,restore,wrapped:[],extraSaved:[],target:null};
  __TEXT_HELPERS__
  return {fontsReady:document.fonts.status==='loaded',imagesReady:true,
    textRoots:window.__chartPaint.textRootSnapshots||[],textOwners:window.__chartPaint.textProjectionSnapshots||[]};
})()"""

_TARGET = r"""(async () => {
  const state=window.__chartPaint,target=__TARGET__;
  const matches=[state.svg,...state.svg.querySelectorAll('[data-qid]')].filter(node=>node.getAttribute('data-qid')===target.qid);
  if(matches.length!==1)return {complete:false,reason:'qid_not_unique'};
  const node=matches[0];state.target=node;state.targetInfo=target;state.selected=[node];
  for(const qid of target.jointQids||[]) {
    const member=[state.svg,...state.svg.querySelectorAll('[data-qid]')].find(item=>item.getAttribute('data-qid')===qid);
    if(!member)return {complete:false,reason:'joint_native_qid_missing'};
    if(!state.selected.includes(member))state.selected.push(member);
  }
  if(state.selected.some(member=>member!==node&&
      !!(member.compareDocumentPosition(node)&Node.DOCUMENT_POSITION_FOLLOWING)))
    return {complete:false,reason:'joint_native_target_not_earliest'};
  state.selectedRanges=[];state.ownedRanges=[];state.clusters=[];state.owned=new Set(target.ownedQids||[]);
  state.ownedPaints=target.ownedPaints||{};
  const css=getComputedStyle(node),box=node.getBoundingClientRect(),matrix=node.getScreenCTM();
  if(target.kind==='glyph-range') {
    const prepared=state.prepareRanges(target.qid,target.startUtf16,target.endUtf16);
    if(!prepared.complete)return prepared;
    if(prepared.clusters.some(cluster=>!cluster.authorizationComplete))
      return {complete:false,reason:'joint_shaping_membership_unproven'};
    state.selectedRanges=prepared.ranges;
    state.clusters=prepared.clusters;state.ownedText=prepared.ownedText;
  }
  for(const span of target.ownedGlyphRanges||[]) {
    const prepared=state.prepareRanges(span.qid,span.start,span.start+span.length);
    if(!prepared.complete)return prepared;
    // A shared shaped glyph is owned only if every member is authorized.
    if(prepared.clusters.some(cluster=>!cluster.authorizationComplete))continue;
    state.ownedRanges.push(...prepared.ranges.filter(entry=>!!(node.compareDocumentPosition(entry.parent)&Node.DOCUMENT_POSITION_FOLLOWING)));
  }
  state.paints=state.selected.flatMap(selected=>[selected,...selected.querySelectorAll('*')])
    .filter(item=>!['g','defs','style','text','tspan'].includes(item.localName))
    .map(item=>{const paint=getComputedStyle(item);return [item,paint.fill,paint.stroke];});
  state.geometry=()=>[state.svg,...state.svg.querySelectorAll('[data-qid]')].map(item=>{
    const rect=item.getBoundingClientRect(),chars=[];
    if(item.getNumberOfChars)for(let i=0;i<item.getNumberOfChars();i++){
      const extent=item.getExtentOfChar(i);chars.push([extent.x,extent.y,extent.width,extent.height]);}
    return [item.getAttribute('data-qid'),rect.x,rect.y,rect.width,rect.height,chars];});
  state.allSelectedRanges=state.selectedRanges;
  const clusters=state.clusters.map(({ranges,...metadata})=>metadata);
  const ctm=state.svg.getScreenCTM();
  return {complete:true,bbox:{x:box.x,y:box.y,width:box.width,height:box.height},geometry:state.geometry(),
    clusters,ownedText:state.ownedText,canvasMatrix:[ctm.a,ctm.b,ctm.c,ctm.d,ctm.e,ctm.f],
    foreignOccluderQids:state.leaves.filter(leaf=>!!(node.compareDocumentPosition(leaf)&Node.DOCUMENT_POSITION_FOLLOWING))
      .map(leaf=>leaf.getAttribute('data-qid')).filter(qid=>qid&&!state.owned.has(qid))};
})()"""

_PLANE = r"""(async () => {
  const state=window.__chartPaint,mode=__MODE__,target=state.target;
  const selected=state.selectedRanges;
  const belongs=node=>state.targetInfo.kind==='primitive'&&state.selected.some(item=>item===node||item.contains(node));
  const owned=node=>{for(let item=node;item&&item!==state.svg;item=item.parentElement)
    if(state.owned.has(item.getAttribute('data-qid')))return true;return false;};
  const strokeOnly=node=>{let typed=false;
    for(let item=node;item&&item!==state.svg;item=item.parentElement) {
      const qid=item.getAttribute('data-qid');if(!state.owned.has(qid))continue;
      if((state.ownedPaints[qid]||'all')==='all')return false;typed=true;
    }return typed;};
  const later=node=>!!(target.compareDocumentPosition(node)&Node.DOCUMENT_POSITION_FOLLOWING);
  for(const leaf of state.leaves) {
    if(['text','tspan'].includes(leaf.localName))continue;
    const active=belongs(leaf);
    if(mode==='alpha'&&!active||mode==='owned-alpha'&&(!owned(leaf)||!later(leaf))||
       mode.startsWith('reference')&&later(leaf)&&!owned(leaf)&&!active||mode==='current-absent'&&active)
      leaf.style.setProperty('opacity','0','important');
    if((mode==='owned-alpha'||mode.startsWith('reference'))&&owned(leaf)&&strokeOnly(leaf))
      leaf.style.setProperty('fill','none','important');
  }
  state.clearTextPaint();
  state.paintRange(state.textEntries,{original:true,priority:0});
  for(const entry of state.textEntries) {
    if(mode==='alpha'||mode==='owned-alpha'&&(!owned(entry.parent)||!later(entry.parent))||mode.startsWith('reference')&&later(entry.parent)&&!owned(entry.parent))
      state.paintRange([entry],{hidden:true,priority:10});
  }
  // Native source-over alpha is independent of solid foreground RGB. White
  // forces a noninitial SVG selection paint while retaining original opacity,
  // fill/stroke presence and shaping; only the alpha channel is consumed.
  if(mode==='alpha')state.paintRange(selected,{fill:'#fff',stroke:'#fff',priority:20});
  if(mode==='owned-alpha')state.paintRange(state.ownedRanges,{fill:'#fff',stroke:'#fff',priority:20});
  if(mode.startsWith('reference'))state.paintRange(state.ownedRanges,{original:true,priority:20});
  if(mode==='reference-original')state.paintRange(selected,{original:true,priority:30});
  if(mode==='current-absent')state.paintRange(selected,{hidden:true,priority:30});
  if(mode==='alpha'||mode==='owned-alpha') {
    document.documentElement.style.setProperty('background','transparent','important');
    document.body.style.setProperty('background','transparent','important');
    if(mode==='owned-alpha')for(const leaf of [...state.leaves.filter(owned),...state.ownedRanges.map(entry=>entry.parent)])
      for(let ancestor=leaf.parentElement;ancestor;ancestor=ancestor.parentElement)
        if(ancestor.contains(target))ancestor.style.setProperty('opacity','1','important');
  } else if(mode.endsWith('white')||mode.endsWith('black')) {
    // Chromium treats initial black selection fill as originating paint.
    // A fixed noninitial dark diagnostic makes the native override explicit.
    const color=mode.endsWith('white')?'#fff':'#010101';
    for(const [node,fill,stroke] of state.paints){
      if(fill!=='none')node.style.setProperty('fill',color,'important');
      if(stroke!=='none')node.style.setProperty('stroke',color,'important');}
    state.paintRange(selected,{fill:color,stroke:color,priority:30});
  }
  state.flushTextPaint();
  await new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)));
  return true;
})()"""

def _evaluate(command, script):
    response = command("Runtime.evaluate", {"expression": script, "returnByValue": True, "awaitPromise": True})
    if response.get("exceptionDetails"):
        description = (response["exceptionDetails"].get("exception") or {}).get("description", "")
        first_line = description.splitlines()[0] if isinstance(description, str) and description else ""
        if re.fullmatch(r"Error: chart_[a-z_]+", first_line):
            raise ValueError(first_line[7:])
        raise ValueError("chart_paint_browser_probe_incomplete")
    return response.get("result", {}).get("value")


def _shot(command):
    from PIL import Image
    # PNG stays lossless; optimizeForSpeed only lowers Chrome's zlib effort, so the
    # decoded pixels are identical while each of the hundreds of plane captures
    # spends less time encoding.
    response = command("Page.captureScreenshot", {"format": "png", "fromSurface": True,
                                                 "captureBeyondViewport": False, "optimizeForSpeed": True})
    return Image.open(io.BytesIO(base64.b64decode(response["data"], validate=True))).convert("RGBA")


def _source_over_original_color(native, background, original, appearance, expected, current,
                                owned_alpha, original_contribution):
    """A sufficient exact byte-compositing witness, never an error tolerance.

    The isolated native RGBA fixes one integer premultiplied color and one
    shared alpha. Its unpremultiplication must round-trip exactly. Only an
    appearance mismatch with unchanged causal response and no owned overpaint
    can use this witness; all other paint classifications stay unchanged.

    Vectorized form of :func:`_source_over_original_color_reference` (same
    integer arithmetic per pixel); masks in other modes use the reference.
    """
    import numpy as np
    from PIL import Image

    size = native.size
    if any(image.size != size for image in (background, original, appearance, expected,
                                           current, owned_alpha, original_contribution)):
        raise ValueError("chart_source_over_witness_size_mismatch")
    if any(image.mode not in {"1", "L"} for image in (appearance, owned_alpha, original_contribution)):
        return _source_over_original_color_reference(native, background, original, appearance, expected,
                                                     current, owned_alpha, original_contribution)
    qualified = np.zeros((size[1], size[0]), dtype=np.uint8)
    bounds = appearance.getbbox()
    if bounds is None:
        return Image.fromarray(qualified, "L")
    x0, y0, x1, y1 = bounds

    def window(image, mode=None):
        array = np.asarray(image.convert(mode) if mode else image)
        return array[y0:y1, x0:x1].astype(np.int64)

    rgba = window(native, "RGBA")
    alpha = rgba[..., 3]
    paint = rgba[..., :3]
    selected = ((window(appearance) != 0) & (window(owned_alpha) == 0) & (window(original_contribution) != 0)
                & np.all(window(expected, "RGB") == window(current, "RGB"), axis=2) & (alpha != 0))
    alpha3 = alpha[..., np.newaxis]
    premultiplied = (alpha3 * paint + 127) // 255
    divisor = np.where(alpha3 == 0, 1, alpha3)
    selected &= np.all((255 * premultiplied + alpha3 // 2) // divisor == paint, axis=2)
    reconstructed = premultiplied + (window(background, "RGB") * (255 - alpha3) + 127) // 255
    selected &= np.all(reconstructed == window(original, "RGB"), axis=2)
    qualified[y0:y1, x0:x1] = np.where(selected, 255, 0)
    return Image.fromarray(qualified, "L")


def _source_over_original_color_reference(native, background, original, appearance, expected, current,
                                owned_alpha, original_contribution):
    """A sufficient exact byte-compositing witness, never an error tolerance.

    The isolated native RGBA fixes one integer premultiplied color and one
    shared alpha. Its unpremultiplication must round-trip exactly. Only an
    appearance mismatch with unchanged causal response and no owned overpaint
    can use this witness; all other paint classifications stay unchanged.
    """
    from PIL import Image

    size = native.size
    if any(image.size != size for image in (background, original, appearance, expected,
                                           current, owned_alpha, original_contribution)):
        raise ValueError("chart_source_over_witness_size_mismatch")
    qualified = Image.new("L", size)
    bounds = appearance.getbbox()
    if bounds is None:
        return qualified
    native, background, original = native.convert("RGBA"), background.convert("RGB"), original.convert("RGB")
    expected, current = expected.convert("RGB"), current.convert("RGB")
    for y in range(bounds[1], bounds[3]):
        for x in range(bounds[0], bounds[2]):
            point = (x, y)
            if (not appearance.getpixel(point) or owned_alpha.getpixel(point) != 0 or
                    not original_contribution.getpixel(point) or
                    expected.getpixel(point) != current.getpixel(point)):
                continue
            rgba = native.getpixel(point)
            alpha = rgba[3]
            if not alpha:
                continue
            premultiplied = tuple((alpha * channel + 127) // 255 for channel in rgba[:3])
            if any((255 * paint + alpha // 2) // alpha != channel
                   for paint, channel in zip(premultiplied, rgba[:3], strict=False)):
                continue
            reconstructed = tuple(paint + (backdrop * (255 - alpha) + 127) // 255
                                  for paint, backdrop in zip(premultiplied, background.getpixel(point), strict=False))
            if reconstructed == original.getpixel(point):
                qualified.putpixel(point, 255)
    return qualified


def _canonical_canvas_matrix(raw, root_transform):
    """Remove the SVG root transform already included in canonical geometry."""
    if (len(raw) != 6 or len(root_transform) != 3 or
            any(isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value)
                for value in (*raw, *root_transform))):
        raise ValueError("chart_canonical_canvas_matrix_invalid")
    dx, dy, scale = root_transform
    if scale <= 0:
        raise ValueError("chart_canonical_root_transform_invalid")
    a, b, c, d, e, f = raw
    return [a / scale, b / scale, c / scale, d / scale,
            e - (a * dx + c * dy) / scale, f - (b * dx + d * dy) / scale]




def _region_mask(size, support, matrix, dpr):
    """Partition shared primitive pixels by canonical-canvas pixel-center strips."""
    import numpy as np
    from PIL import Image
    strip = support.get("categoryStrip")
    if strip is None:
        return Image.new("L", size, 255)
    a, b, c, d, e, f = matrix
    determinant = a*d-b*c
    if not determinant:
        raise ValueError("chart_canvas_transform_singular")
    minimum, maximum = strip.get("minimumX"), strip.get("maximumX")
    # Same float64 operations, in the same order, as the per-pixel formula
    # (d*((x+.5)/dpr-e)-c*((y+.5)/dpr-f))/determinant.
    xs = (np.arange(size[0], dtype=np.float64) + .5) / dpr - e
    ys = (np.arange(size[1], dtype=np.float64) + .5) / dpr - f
    canvas_x = (d * xs[np.newaxis, :] - c * ys[:, np.newaxis]) / determinant
    inside = np.ones((size[1], size[0]), dtype=bool)
    if minimum is not None:
        inside &= canvas_x >= minimum
    if maximum is not None:
        inside &= canvas_x <= maximum
    return Image.fromarray(np.where(inside, 255, 0).astype(np.uint8), "L")


def collect_chart_paint_proof(*, command, manifest, source_html, final_png, metrics,
                              width_px, height_px, device_scale_factor=1, wrapper_digest=None,
                              diagnostic_capture=None, timing=None):
    """Use disposable native-CDP planes; never mutate the emitted capture page."""
    from utils.slide_capture_chart_timing import timing_phase
    with timing_phase(timing, "support_cpu"):
        from PIL import Image, ImageChops
        from utils.slide_capture_chart_text import TEXT_HELPERS
        from utils.svg_uniform_transform import parse_uniform_transform
        validate_manifest(manifest)
        # The service binds exact canonical source; this admitted derivative
        # changes fonts only. Reuse the same root parser as the V5 scene reader.
        root_transform = parse_uniform_transform(_svg_root(source_html).get("transform"))
        viewport = {"cssWidth": width_px, "cssHeight": height_px, "dpr": device_scale_factor,
                    "rasterWidth": width_px*device_scale_factor, "rasterHeight": height_px*device_scale_factor}
        proof = {"schema": PROOF_SCHEMA, "probeVersion": PROBE_VERSION, "complete": False,
                 "paintDomain": "captured-viewport/v1",
                 "canonicalSvgDigest": manifest["canonicalSvgDigest"], "treeDerivativeDigest": manifest["treeDerivativeDigest"],
                 "wrapperDigest": wrapper_digest or sha256_prefixed(source_html.encode("utf-8")),
                 "manifestDigest": _manifest_digest(manifest), "pngDigest": sha256_prefixed(final_png),
                 "captureContextDigest": _digest(manifest.get("captureContext")),
                 "admittedRenderDigest": sha256_prefixed(source_html.encode("utf-8")),
                 "viewport": viewport, "targets": []}
        original = Image.open(io.BytesIO(final_png)).convert("RGB")
        support_masks = {}
        joint_evidence = {"boundaryContrast": {}, "incidentSideContribution": {}}
        failure = None
        embedded_fonts = []
        material_records = []
        for mime, encoded in re.findall(r"data:([^;,\s]+);base64,([A-Za-z0-9+/=]+)", source_html):
            try:
                payload = base64.b64decode(encoded, validate=True)
            except ValueError:
                failure = "chart_embedded_resource_invalid"
                continue
            material_records.append({"mimeType": mime, "digest": sha256_prefixed(payload)})
            if mime.startswith("font/"):
                try:
                    embedded_fonts.extend(_font_faces(payload))
                except Exception:
                    # An unreadable embedded font leaves the proof incomplete instead
                    # of aborting the capture.
                    failure = "chart_embedded_font_unreadable"
        proof["materialIdentities"] = {"embeddedResources": sorted(material_records, key=lambda row:(row["mimeType"],row["digest"]))}
        projection = (manifest.get("captureContext") or {}).get("textProjection")
        config = {"width":width_px,"height":height_px,"dpr":device_scale_factor}
        if projection is not None:
            config["textProjection"] = projection
        setup_script = _SETUP.replace("__CONFIG__", json.dumps(config)).replace("__TEXT_HELPERS__",TEXT_HELPERS)

        def plane(target, mode, cluster=None, font=False):
            if diagnostic_capture is None:
                raise ValueError("chart_disposable_diagnostic_context_missing")
            def observe(probe_command, fresh_metrics):
                # Source/admission/materialization are supplied by the original driver.
                # Every plane starts at precisely the emitted original RGB frame.
                # The post-preparation all-on screenshot below binds this same fresh
                # page to the final PNG after resource, ownership and selection setup.
                with timing_phase(timing, "setup_projection_target"):
                    setup = _evaluate(probe_command, setup_script)
                    if not isinstance(setup,dict) or not setup.get("fontsReady") or not setup.get("imagesReady"):
                        raise ValueError("chart_resources_unproven")
                    if projection is not None:
                        roots = {item["textRootQid"]:item["text"] for item in setup["textRoots"]}
                        owners = {(item["ownerQid"],item["textRootQid"]):item["ownedText"] for item in setup["textOwners"]}
                        if len(owners)!=len(projection):
                            raise ValueError("chart_font_projection_ownership_mismatch")
                        for item in projection:
                            actual_root = roots.get(item["textRootQid"])
                            actual_owned = owners.get((item["ownerQid"],item["textRootQid"]))
                            if (not isinstance(actual_root,str) or sha256_prefixed(actual_root.encode("utf-8"))!=item["rootTextDigest"] or
                                    not isinstance(actual_owned,str) or sha256_prefixed(actual_owned.encode("utf-8"))!=item["ownedTextDigest"]):
                                raise ValueError("chart_authorized_font_text_projection_mismatch")
                    observed = _evaluate(probe_command,_TARGET.replace("__TARGET__",json.dumps(target)))
                    if not isinstance(observed,dict) or observed.get("complete") is not True:
                        return None, observed or {"complete":False,"reason":"target_unproven"}, None
                    geometry = observed["geometry"]
                    if cluster is not None:
                        _evaluate(probe_command,f"window.__chartPaint.selectedRanges=window.__chartPaint.clusters[{cluster}].ranges;true")
                    # Native selection must preserve original shaping and raster,
                    # including exactly the range used by this counterfactual plane.
                with timing_phase(timing, "selection_all_on"):
                    _evaluate(probe_command,"window.__chartPaint.paintRange(window.__chartPaint.textEntries,{original:true,priority:0});true")
                    _evaluate(probe_command,"window.__chartPaint.paintRange([...window.__chartPaint.selectedRanges,...window.__chartPaint.ownedRanges],{original:true,priority:1,forceSelection:true});true")
                    _evaluate(probe_command,"window.__chartPaint.flushTextPaint();true")
                    _evaluate(probe_command,"new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(()=>resolve(true))))")
                    if ImageChops.difference(_shot(probe_command).convert("RGB"),original).getbbox():
                        return None, {"complete":False,"reason":"glyph_original_highlight_raster_changed",
                            "rangePaintDiagnostics":_evaluate(probe_command,"window.__chartPaint.selectionForegroundEvidence||[]")}, None
                    if _evaluate(probe_command,"window.__chartPaint.geometry()") != geometry:
                        raise ValueError("chart_original_shaping_changed")
                    font_evidence = _glyph_font_evidence(probe_command,embedded_fonts,target) if font else None
                with timing_phase(timing, "mutation_geometry"):
                    probe_command("Emulation.setDefaultBackgroundColorOverride", {"color":{"r":0,"g":0,"b":0,"a":0}} if mode.endswith("alpha") else {})
                    _evaluate(probe_command,_PLANE.replace("__MODE__",json.dumps(mode)))
                    if _evaluate(probe_command,"window.__chartPaint.geometry()") != geometry:
                        raise ValueError("chart_target_geometry_changed")
                with timing_phase(timing, "final_screenshot"):
                    return _shot(probe_command), observed, font_evidence
            return diagnostic_capture(observe)

        def nonzero_rgb(image):
            channels=image.convert("RGB").split()
            return ImageChops.lighter(ImageChops.lighter(channels[0],channels[1]),channels[2]).point(lambda value:255 if value else 0)

        try:
            for target in _targets(manifest):
                record=dict(target,complete=False,paintRetained=False,unresolvedRanges=[])
                proof["targets"].append(record)
                planes={}
                observed=None
                for mode in ("alpha","owned-alpha","reference-original","reference-white","reference-black","current-white","current-black","current-absent"):
                    image, current_observation, font_evidence=plane(target,mode,font=target["kind"]=="glyph-range" and mode=="alpha")
                    if image is None:
                        record["reason"]=current_observation.get("reason","target_unproven")
                        if "rangePaintDiagnostics" in current_observation:
                            record["rangePaintDiagnostics"]=current_observation["rangePaintDiagnostics"]
                        break
                    if observed is not None and current_observation != observed:
                        raise ValueError("chart_diagnostic_geometry_or_membership_mismatch")
                    observed=current_observation
                    planes[mode]=image
                    if font_evidence is not None:
                        record["fontEvidence"]=font_evidence
                if len(planes)!=8:
                    if target["kind"]=="glyph-range":
                        record["unresolvedRanges"]=[{"startUtf16":target["startUtf16"],"endUtf16":target["endUtf16"],"reason":record["reason"]}]
                    continue
                record["foreignOccluderQids"]=observed["foreignOccluderQids"]
                record["clippedQids"]=[]
                alpha=planes["alpha"].getchannel("A")
                canvas_matrix = _canonical_canvas_matrix(observed["canvasMatrix"], root_transform)
                region=_region_mask(alpha.size,target.get("geometrySupport",{}),canvas_matrix,device_scale_factor)
                full_alpha=alpha
                alpha=ImageChops.multiply(alpha,region)
                footprint=alpha.point(lambda value:255 if value else 0)
                if target["kind"]=="primitive":
                    record["jointPrimitiveFootprint"]=_mask_record(full_alpha.point(lambda value:255 if value else 0),full_alpha)
                    record["regionRule"]="canonical-canvas-pixel-center-inclusive/v1"
                original_contribution=ImageChops.multiply(nonzero_rgb(ImageChops.difference(original,planes["current-absent"].convert("RGB"))),footprint)
                record["originalContribution"]=_mask_record(original_contribution,alpha)
                glyphs_complete=True
                if target["kind"]=="glyph-range":
                    if sha256_prefixed(observed["ownedText"].encode("utf-8"))!=target["ownedTextDigest"]:
                        record["reason"]="owned_text_binding_mismatch"
                        continue
                    if record["fontEvidence"]["complete"] is not True:
                        record["reason"]=record["fontEvidence"]["reason"]
                        continue
                    record["glyphs"]=[]
                    for index,cluster in enumerate(observed["clusters"]):
                        cluster_planes={}
                        for mode in ("alpha","reference-white","reference-black","current-absent"):
                            image,_,_=plane(target,mode,cluster=index)
                            if image is None:
                                raise ValueError("chart_cluster_paint_unproven")
                            cluster_planes[mode]=image
                        cluster_alpha=cluster_planes["alpha"].getchannel("A")
                        mask=cluster_alpha.point(lambda value:255 if value else 0)
                        response=ImageChops.multiply(nonzero_rgb(ImageChops.difference(cluster_planes["reference-white"],cluster_planes["reference-black"])),mask)
                        contribution=ImageChops.multiply(nonzero_rgb(ImageChops.difference(original,cluster_planes["current-absent"].convert("RGB"))),mask)
                        demanded=bool(cluster["text"].strip())
                        complete=not demanded or bool(mask.getbbox()) and bool(response.getbbox()) and bool(contribution.getbbox())
                        glyphs_complete=glyphs_complete and complete
                        record["glyphs"].append({**{key:value for key,value in cluster.items() if key!="text"},
                            "textDigest":sha256_prefixed(cluster["text"].encode("utf-8")),"complete":complete,
                            "observableReferencePixels":response.histogram()[255],
                            "originalContribution":_mask_record(contribution,cluster_alpha),**_mask_record(mask,cluster_alpha)})
                expected=ImageChops.subtract(planes["reference-white"].convert("RGB"),planes["reference-black"].convert("RGB"))
                current=ImageChops.subtract(planes["current-white"].convert("RGB"),planes["current-black"].convert("RGB"))
                altered=ImageChops.multiply(nonzero_rgb(ImageChops.difference(expected,current)),footprint)
                appearance=ImageChops.multiply(nonzero_rgb(ImageChops.difference(original,planes["reference-original"].convert("RGB"))),footprint)
                owner_alpha=planes["owned-alpha"].getchannel("A")
                if target["kind"] == "primitive":
                    source_over = _source_over_original_color(
                        planes["alpha"], planes["current-absent"], original, appearance,
                        expected, current, owner_alpha, original_contribution)
                    if source_over.getbbox():
                        record["sourceOverOriginalColor"] = {
                            "rule": "native-rgba-exact-source-over/v1", **_mask_record(source_over, alpha)}
                        appearance = ImageChops.subtract(appearance, source_over)
                altered=ImageChops.lighter(altered,appearance)
                exposed=nonzero_rgb(expected)
                zero=ImageChops.multiply(ImageChops.invert(exposed),footprint)
                opaque_owned=ImageChops.multiply(owner_alpha.point(lambda value:255 if value==255 else 0),footprint)
                owned=ImageChops.multiply(owner_alpha.point(lambda value:255 if value else 0),footprint)
                zero=ImageChops.subtract(ImageChops.subtract(zero,opaque_owned),altered)
                current_response=nonzero_rgb(current)
                quantized=ImageChops.subtract(zero,ImageChops.lighter(ImageChops.lighter(altered,current_response),original_contribution))
                zero=ImageChops.subtract(zero,quantized)
                owned=ImageChops.subtract(owned,ImageChops.lighter(ImageChops.lighter(altered,zero),quantized))
                retained=ImageChops.subtract(footprint,ImageChops.lighter(ImageChops.lighter(altered,zero),ImageChops.lighter(owned,quantized)))
                record.update(footprint=_mask_record(footprint,alpha),retained=_mask_record(retained,alpha),
                    altered=_mask_record(altered,alpha),ownedOverlap=_mask_record(owned,alpha),
                    quantizedAtCapture=_mask_record(quantized,alpha),unproven=_mask_record(zero,alpha))
                support_masks[record["id"]] = {"footprint":footprint,"originalContribution":original_contribution,
                    "retained":retained,"ownedOverlap":owned,"altered":altered,"alpha":alpha,
                    "opaqueOwned":opaque_owned,
                    "unproven":zero,
                    "nativeGeometryBounds":{"left":observed["bbox"]["x"]*device_scale_factor,
                        "top":observed["bbox"]["y"]*device_scale_factor,
                        "right":(observed["bbox"]["x"]+observed["bbox"]["width"])*device_scale_factor,
                        "bottom":(observed["bbox"]["y"]+observed["bbox"]["height"])*device_scale_factor},
                    "canvasMatrix":canvas_matrix,"deviceScaleFactor":device_scale_factor}
                if not footprint.getbbox():
                    record["reason"]="reference_paint_empty"
                elif not glyphs_complete:
                    record["reason"]="glyph_original_contribution_missing"
                elif not original_contribution.getbbox():
                    record["reason"]="original_color_semantic_contribution_empty"
                elif not exposed.getbbox():
                    record["reason"]="reference_observable_contribution_empty"
                elif zero.getbbox():
                    record["reason"]="owned_or_quantized_paint_unresolved"
                else:
                    record.update(complete=True,reason=None,paintRetained=not bool(altered.getbbox()))
            from utils.slide_capture_chart_support import (
                boundary_mask, native_composition_requests, resolve_chart_support, stack_boundary_contrast,
            )
            by_target={record["id"]:record for record in proof["targets"]}
            pair_absences={}
            pair_native_paints={}
            for chart in manifest["charts"]:
                chart_records=[record for record in proof["targets"] if record["chartId"]==chart["id"]]
                pair_evidence = joint_evidence.setdefault("nativeCompositionPairs", {})
                for request in native_composition_requests(chart, chart_records, support_masks):
                    if request["id"] in pair_evidence:
                        continue
                    first, second = (by_target[request[key]] for key in ("targetId", "partnerTargetId"))
                    if request["qids"] != [first["qid"], second["qid"]]:
                        raise ValueError("chart_native_composition_pair_identity_mismatch")
                    common_owned = set(first.get("ownedQids", ())) & set(second.get("ownedQids", ()))
                    common_owned.difference_update(request["qids"])
                    common_ranges = [span for span in first.get("ownedGlyphRanges", ())
                                     if span in second.get("ownedGlyphRanges", ())]
                    owned_paints = {qid: ("all" if all(record.get("ownedPaints", {}).get(qid, "all") == "all"
                                                     for record in (first, second)) else "stroke")
                                    for qid in common_owned}
                    template = dict(first, jointQids=request["qids"], ownedQids=sorted(common_owned),
                                    ownedPaints=owned_paints, ownedGlyphRanges=common_ranges)
                    images, pair_observation = {}, None
                    for mode in ("alpha", "reference-original", "reference-white", "reference-black",
                                 "current-white", "current-black", "current-absent"):
                        image, observation, _ = plane(template, mode)
                        if image is None:
                            raise ValueError("chart_native_composition_pair_capture_unproven")
                        if pair_observation is not None and observation != pair_observation:
                            raise ValueError("chart_native_composition_pair_geometry_mismatch")
                        pair_observation = observation
                        images[mode] = image
                    native_alpha = images["alpha"].getchannel("A")
                    pair_matrix = _canonical_canvas_matrix(pair_observation["canvasMatrix"], root_transform)
                    pair_region = _region_mask(native_alpha.size, first.get("geometrySupport", {}), pair_matrix, device_scale_factor)
                    footprint = ImageChops.multiply(native_alpha.point(lambda value: 255 if value else 0), pair_region)
                    expected = ImageChops.subtract(images["reference-white"].convert("RGB"), images["reference-black"].convert("RGB"))
                    current = ImageChops.subtract(images["current-white"].convert("RGB"), images["current-black"].convert("RGB"))
                    altered = ImageChops.multiply(nonzero_rgb(ImageChops.difference(expected, current)), footprint)
                    reference_original = images["reference-original"].convert("RGB")
                    altered = ImageChops.lighter(altered, ImageChops.multiply(nonzero_rgb(ImageChops.difference(original, reference_original)), footprint))
                    absent = images["current-absent"].convert("RGB")
                    contribution = ImageChops.multiply(nonzero_rgb(ImageChops.difference(original, absent)), footprint)
                    pair_evidence[request["id"]] = {
                        **request, "complete": True, "canvasMatrix": pair_matrix,
                        "deviceScaleFactor": device_scale_factor, "nativeAlpha": native_alpha,
                        "footprint": footprint, "originalContribution": contribution, "altered": altered,
                        "expectedResponse": expected, "currentResponse": current,
                        "referenceOriginal": reference_original, "currentOriginal": original, "currentAbsent": absent,
                    }
                for equivalent in chart["compositionGroup"].get("equivalenceClasses",[]):
                    members=[by_target.get(member["targetId"]) for member in equivalent["members"]]
                    if not members or any(member is None or member["id"] not in support_masks for member in members):
                        continue
                    template=dict(members[0],jointQids=sorted({member["qid"] for member in members},key=lambda qid:int(qid[4:])))
                    images={mode:plane(template,mode)[0] for mode in ("alpha","reference-original","reference-white","reference-black","current-white","current-black","current-absent")}
                    if any(image is None for image in images.values()):
                        continue
                    native_alpha=images["alpha"].getchannel("A")
                    region=_region_mask(native_alpha.size,template["geometrySupport"],support_masks[template["id"]]["canvasMatrix"],device_scale_factor)
                    native_alpha=ImageChops.multiply(native_alpha,region)
                    native_footprint=native_alpha.point(lambda value:255 if value else 0)
                    expected=ImageChops.subtract(images["reference-white"].convert("RGB"),images["reference-black"].convert("RGB"))
                    current=ImageChops.subtract(images["current-white"].convert("RGB"),images["current-black"].convert("RGB"))
                    altered=ImageChops.multiply(nonzero_rgb(ImageChops.difference(expected,current)),native_footprint)
                    altered=ImageChops.lighter(altered,ImageChops.multiply(nonzero_rgb(ImageChops.difference(original,images["reference-original"].convert("RGB"))),native_footprint))
                    contribution=ImageChops.multiply(nonzero_rgb(ImageChops.difference(original,images["current-absent"].convert("RGB"))),native_footprint)
                    joint_evidence[equivalent["id"]]={"complete":True,"memberTargetIds":[member["targetId"] for member in equivalent["members"]],"footprint":native_footprint,
                        "originalContribution":contribution,"retained":ImageChops.subtract(native_footprint,altered),"altered":altered}
                    member_boundaries=[]
                    joint_retained=ImageChops.subtract(native_footprint,altered)
                    for member in equivalent["members"]:
                        member_record=by_target.get(member["targetId"])
                        member_raw=support_masks.get(member["targetId"])
                        if (member_record is None or member_raw is None
                                or member["datumId"] not in member_record.get("datumIds",[])
                                or member_record.get("geometrySupport",{}).get("geometryDigest")!=member["geometrySupportDigest"]):
                            raise ValueError("chart_joint_member_support_identity_mismatch")
                        matching=[item for item in member_record.get("geometrySupport",{}).get("visibilityObligations",[])
                                  if item.get("id")==member["supportId"] and item.get("primitiveQid")==member_record["qid"]]
                        if len(matching)!=1:
                            raise ValueError("chart_joint_member_boundary_missing")
                        selected=boundary_mask(matching[0],member_raw["footprint"].size,member_raw["canvasMatrix"],member_raw["deviceScaleFactor"])
                        selected=ImageChops.multiply(ImageChops.multiply(selected,member_raw["footprint"]),native_footprint)
                        member_boundaries.append({"datumId":member["datumId"],"targetId":member["targetId"],
                            "supportId":member["supportId"],"geometrySupportDigest":member["geometrySupportDigest"],
                            "footprint":_mask_record(selected,member_raw["alpha"]),
                            "originalContribution":_mask_record(ImageChops.multiply(selected,contribution),member_raw["alpha"]),
                            "retained":_mask_record(ImageChops.multiply(selected,joint_retained),member_raw["alpha"]),
                            "altered":_mask_record(ImageChops.multiply(selected,altered),member_raw["alpha"])})
                    proof.setdefault("jointNativePaint",[]).append({"classId":equivalent["id"],
                        "memberTargetIds":[member["targetId"] for member in equivalent["members"]],
                        "memberBoundaries":member_boundaries,
                        "footprint":_mask_record(native_footprint,native_alpha),"originalContribution":_mask_record(contribution,native_alpha),
                        "retained":_mask_record(joint_retained,native_alpha),"altered":_mask_record(altered,native_alpha)})
                for record in chart_records:
                    raw=support_masks.get(record["id"])
                    if raw is None:
                        continue
                    record["contributionRegions"]=[]
                    for region in record.get("geometrySupport",{}).get("contributionRegions",[]):
                        if region.get("primitiveQid")!=record["qid"]:
                            continue
                        native_region=ImageChops.multiply(boundary_mask(region,raw["footprint"].size,raw["canvasMatrix"],device_scale_factor),raw["footprint"])
                        contributed=ImageChops.multiply(native_region,raw["originalContribution"])
                        record["contributionRegions"].append({"id":region["id"],"kind":region["kind"],
                            "footprint":_mask_record(native_region,raw["alpha"]),"originalContribution":_mask_record(contributed,raw["alpha"])})
                    for obligation in record.get("geometrySupport",{}).get("visibilityObligations",[]):
                        if obligation.get("primitiveQid")!=record["qid"]:
                            continue
                        boundary=ImageChops.multiply(boundary_mask(obligation,raw["footprint"].size,raw["canvasMatrix"],device_scale_factor),raw["footprint"])
                        for alternative in obligation.get("alternatives",[]):
                            if alternative["kind"]!="shared-stack-interface":
                                continue
                            neighbor=by_target.get(alternative.get("neighborTargetId"))
                            neighbor_raw=support_masks.get(alternative.get("neighborTargetId"))
                            if neighbor is None or neighbor_raw is None:
                                continue
                            pair=tuple(sorted({record["qid"],neighbor["qid"]},key=lambda qid:int(qid[4:])))
                            if pair not in pair_absences:
                                template=dict(record,jointQids=list(pair))
                                pair_absences[pair]=plane(template,"current-absent")[0]
                            if pair not in pair_native_paints:
                                template=dict(record,jointQids=list(pair))
                                pair_native_paints[pair]=plane(template,"alpha")[0]
                            absent=pair_absences[pair]
                            native_pair=pair_native_paints[pair]
                            if absent is None or native_pair is None:
                                continue
                            measurement=stack_boundary_contrast(obligation,raw["canvasMatrix"],device_scale_factor,
                                raw["nativeGeometryBounds"],neighbor_raw["nativeGeometryBounds"],
                                original,absent.convert("RGB"),native_pair.convert("RGBA"),boundary)
                            witness={"targetId":record["id"],"supportId":obligation["id"],
                                "neighborTargetId":neighbor["id"],"neighborSupportId":alternative["neighborSupportId"],
                                "datumInteriorSupportId":alternative["datumInteriorSupportId"],
                                "neighborInteriorSupportId":alternative["neighborInteriorSupportId"],
                                "samplingRule":"normal-interior-native-pair/v1",
                                **{name:_mask_record(measurement[name],raw["alpha"])
                                   for name in ("footprint","normalInteriorPairs","nativeContrast","originalContribution")},
                                "samplePairs":measurement["samplePairs"]}
                            joint_evidence["boundaryContrast"][obligation["id"]+"|"+neighbor["id"]]={
                                "complete":True,**measurement,"witness":witness}
                resolution=resolve_chart_support(chart,chart_records,support_masks,joint_evidence)
                proof.setdefault("supportGraphs",[]).append(resolution)
                for resolved in resolution["targets"]:
                    record=by_target[resolved["targetId"]]
                    record["visibilityObligations"]=resolved["obligations"]
                    raw=support_masks.get(record["id"])
                    if raw is not None:
                        specifications={item["id"]:item for item in record.get("geometrySupport",{}).get("visibilityObligations",[])}
                        for item in record["visibilityObligations"]:
                            specification=specifications.get(item["supportId"])
                            if specification is None:
                                continue
                            native_boundary=ImageChops.multiply(boundary_mask(specification,raw["footprint"].size,raw["canvasMatrix"],device_scale_factor),raw["footprint"])
                            item["footprint"]=_mask_record(native_boundary,raw["alpha"])
                            item["originalContribution"]=_mask_record(ImageChops.multiply(native_boundary,raw["originalContribution"]),raw["alpha"])
                    record["complete"]=resolved["complete"]
                    record["paintRetained"]=resolved["paintRetained"]
                    if record["complete"] and record["paintRetained"]:
                        record["reason"]=None
        except Exception as error:
            failure=str(error)[:160]
        # The primary emitted frame was never used for counterfactual mutation.
        if ImageChops.difference(_shot(command).convert("RGB"),original).getbbox():
            failure="chart_immutable_capture_frame_changed"
        if failure:
            proof["reason"]=failure
            for record in proof["targets"]:
                record.update(complete=False,paintRetained=False,reason=failure)
        present={record["id"] for record in proof["targets"]}
        for target in _targets(manifest):
            if target["id"] not in present:
                proof["targets"].append(dict(target,complete=False,paintRetained=False,reason=failure or "target_unproven",unresolvedRanges=[]))
        proof["charts"]=[]
        for chart in manifest["charts"]:
            chart_targets=[record for record in proof["targets"] if record["chartId"]==chart["id"]]
            datums=[]
            for datum in chart["datums"]:
                linked=[record for record in chart_targets if datum["id"] in record["datumIds"]]
                empty=not datum["primitiveQids"] and datum["geometryRole"] in {"zero","degenerate"}
                datums.append({"id":datum["id"],"seriesIndex":datum["seriesIndex"],"categoryIndex":datum["categoryIndex"],
                    "geometryRole":datum["geometryRole"],"geometrySupport":datum.get("geometrySupport",{}),
                    "primitivePaintExpected":bool(datum["primitiveQids"]),"targetIds":[record["id"] for record in linked],
                    "complete":(bool(linked) or empty) and all(record["complete"] for record in linked),
                    "paintRetained":(bool(linked) or empty) and all(record.get("paintRetained") is True for record in linked)})
            proof["charts"].append({"id":chart["id"],"chartType":chart["chartType"],"datums":datums,
                "targetIds":[record["id"] for record in chart_targets],
                "complete":all(record["complete"] for record in chart_targets) and all(row["complete"] for row in datums),
                "paintRetained":all(record.get("paintRetained") is True for record in chart_targets) and all(row["paintRetained"] for row in datums)})
        proof["complete"]=not failure and all(record["complete"] for record in proof["targets"]) and all(chart["complete"] for chart in proof["charts"])
        proof["paintRetained"]=proof["complete"] and all(record.get("paintRetained") is True for record in proof["targets"])
        return proof
