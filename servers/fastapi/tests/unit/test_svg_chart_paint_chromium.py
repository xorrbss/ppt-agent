"""Provider-free same-CDP-frame chart paint fixtures, at the captured resolution."""
from __future__ import annotations

import base64
import io
import json
from pathlib import Path
import re
import subprocess
import sys
import unicodedata
from xml.etree import ElementTree as ET

import pytest
from PIL import Image

from services.svg_contract import source_digest
from services.svg_renderer import svg_preview_html
from utils import slide_capture
from utils.renderer_isolation import admit_static_output_html
from utils.slide_capture_chart_paint import (
    MANIFEST_SCHEMA, PROOF_KEY, collect_chart_paint_proof,
    finalize_chart_paint_proof, validate_chart_paint_binding, _tree_digest, _digest,
)

WIDTH, HEIGHT = 240, 160
NATIVE_EXAMPLES = json.loads((Path(__file__).parent / "fixtures" / "svg_chart_paint_native_examples_v1.json").read_text(encoding="utf-8"))["examples"]


def _diagnostic(proof):
    restore = proof.get("restoreDiagnostics", {})
    targets = [(row["kind"], row.get("reason"),
                {key: row.get(key, {}).get("pixelCount") for key in ("footprint", "altered", "originalContribution")},
                [(item.get("complete"), item.get("pixelCount"), item.get("observableReferencePixels"),
                  item.get("originalContribution", {}).get("pixelCount")) for item in row.get("glyphs", [])])
               for row in proof["targets"]]
    ranges = [row.get("rangePaintDiagnostics") for row in proof["targets"] if "rangePaintDiagnostics" in row]
    return f'{proof.get("reason")} restore_pixels={restore.get("changedPixels")} restore_max={restore.get("maximumChannelDelta")} targets={targets} ranges={ranges}'


def _fixture(mark, *, after="", label="10", glyph_range=None, owned=False):
    svg = (f'<svg xmlns="http://www.w3.org/2000/svg" width="{WIDTH}" height="{HEIGHT}" '
           f'viewBox="0 0 {WIDTH} {HEIGHT}"><rect width="240" height="160" fill="#ffffff"/>'
           f'<g id="chart" transform="translate(4 2)" opacity="0.8">{mark}'
           f'<text id="label" x="55" y="68" font-family="Arial" font-size="20" fill="#111111">'
           f'{label}</text></g>{after}</svg>')
    wrapper = svg_preview_html(svg, width=WIDTH, height=HEIGHT)
    root = ET.fromstring(svg)
    qids = {node.get("id"): f"svg-{index}" for index, node in enumerate(root.iter()) if node.get("id")}
    glyphs, relationships = [], []
    label_node = next(node for node in root.iter() if node.get("id") == "label")
    owned_label = "".join(value for value in [label_node.text, *(child.tail for child in label_node)] if value and value.strip())
    if glyph_range is None and owned:
        glyph_range = (0, len(owned_label.encode("utf-16-le"))//2)
    if glyph_range is not None:
        start, end = glyph_range
        glyphs.append({"qid": qids["label"], "role": "value", "datumIds": ["datum-0"],
                       "ownedTextDigest": source_digest(owned_label), "ranges": [{"start": start, "length": end-start}]})
        if owned:
            relationships.append({"kind": "value-on-datum", "datumId": "datum-0",
                                  "qid": qids["label"], "start": start, "length": end-start})
    manifest = {"schema": MANIFEST_SCHEMA, "requirementsRevision": "svg-native-requirements/v5",
                "canonicalSvgDigest": source_digest(svg), "treeDerivativeDigest": _tree_digest(svg),
                "nodeBindings": [], "charts": [{"id": "chart", "groupQid": qids["chart"],
                    "chartType": "bar", "datums": [{"id": "datum-0", "seriesIndex": 0, "categoryIndex": 0,
                    "value": "10", "primitiveQids": [qids["mark"]], "geometryRole": "bar-profile", "geometrySupport": {}}],
                    "glyphs": glyphs, "compositionGroup": {"id": "chart/composition", "primitiveQids": [qids["mark"]],
                    "glyphTargets": [{"qid": qids["label"], "start": row["start"], "length": row["length"]}
                                     for glyph in glyphs for row in glyph["ranges"]], "relationships": relationships}}]}
    manifest["manifestDigest"] = _digest(manifest)
    return svg, wrapper, manifest


def _render(wrapper, manifest):
    admitted = admit_static_output_html(wrapper)
    return slide_capture._render_admitted_html_over_pipe(
        admitted, 60, width_px=WIDTH, height_px=HEIGHT, chart_witness_manifest=manifest)


@pytest.mark.parametrize("kind,mark", [
    ("bar", '<rect id="mark" x="30" y="35" width="100" height="45" rx="5" fill="#3b82f6"/>'),
    ("column", '<rect id="mark" x="30" y="35" width="45" height="95" fill="#3b82f6"/>'),
    ("line", '<polyline id="mark" points="30,100 80,45 130,75" fill="none" stroke="#3b82f6" stroke-width="5" stroke-linecap="round" stroke-linejoin="round"/>'),
    ("area", '<polygon id="mark" points="30,120 30,100 80,45 130,75 130,120" fill="#3b82f6" fill-opacity="0.7"/>'),
    ("pie", '<polygon id="mark" points="80,80 130,80 126,60 115,45 100,34 80,30" fill="#3b82f6"/>'),
    ("donut", '<path id="mark" d="M30 30 L130 30 L130 130 L30 130 Z M60 60 L60 100 L100 100 L100 60 Z" fill="#3b82f6"/>'),
])
def test_full_raster_primitive_masks_for_chart_families(kind, mark):
    _svg, wrapper, manifest = _fixture(mark)
    _png, metrics = _render(wrapper, manifest)
    proof = metrics[PROOF_KEY]
    assert proof["complete"] is True, (kind, _diagnostic(proof))
    target = proof["targets"][0]
    assert target["footprint"]["pixelCount"] > 0
    # The label is foreign unless the manifest associates its ownership.
    if kind in {"bar", "column", "area", "donut"}:
        assert target["altered"]["pixelCount"] > 0
    assert sum(target[key]["pixelCount"] for key in ("retained", "altered", "ownedOverlap", "quantizedAtCapture", "unproven")) == target["footprint"]["pixelCount"]


@pytest.mark.parametrize("mark", [
    '<line id="mark" x1="-4" y1="15" x2="70" y2="15" stroke="#3b82f6" stroke-width="4" stroke-linecap="butt"/>',
    '<line id="mark" x1="-2" y1="15" x2="70" y2="15" stroke="#3b82f6" stroke-width="4" stroke-linecap="round"/>',
    '<rect id="mark" x="-3" y="10" width="50" height="20" fill="#3b82f6" stroke="#111111" stroke-width="2"/>',
    '<circle id="mark" cx="12" cy="18" r="15" fill="#3b82f6" stroke="#111111" stroke-width="2"/>',
    '<ellipse id="mark" cx="12" cy="18" rx="15" ry="10" fill="#3b82f6" stroke="#111111" stroke-width="2"/>',
])
def test_exact_native_stroke_bounds_accept_viewport_edge(mark):
    _svg, wrapper, manifest = _fixture(mark)
    _png, metrics = _render(wrapper, manifest)
    proof = metrics[PROOF_KEY]
    assert proof["complete"] is True, _diagnostic(proof)
    assert proof["paintRetained"] is True, _diagnostic(proof)
    assert proof["targets"][0]["footprint"]["pixelCount"] > 0


def test_transparent_image_with_tiny_same_color_erasure_is_observed():
    image = Image.new("RGBA", (240, 160), (0, 0, 0, 0))
    for x in range(85, 89):
        for y in range(85, 89):
            image.putpixel((x, y), (98, 155, 248, 255))
    output = io.BytesIO()
    image.save(output, format="PNG")
    href = "data:image/png;base64," + base64.b64encode(output.getvalue()).decode()
    mark = '<rect id="mark" x="30" y="35" width="110" height="95" fill="#3b82f6"/>'
    _baseline_svg, baseline_wrapper, baseline_manifest = _fixture(mark)
    _baseline_png, baseline_metrics = _render(baseline_wrapper, baseline_manifest)
    baseline_altered = baseline_metrics[PROOF_KEY]["targets"][0]["altered"]["pixelCount"]
    _svg, wrapper, manifest = _fixture(mark,
        after=f'<image x="0" y="0" width="240" height="160" href="{href}" preserveAspectRatio="xMidYMid meet"/>')
    _png, metrics = _render(wrapper, manifest)
    proof = metrics[PROOF_KEY]
    assert proof["complete"] is True, _diagnostic(proof)
    assert proof["targets"][0]["altered"]["pixelCount"] - baseline_altered == 16
    assert proof["targets"][0]["paintRetained"] is False


def test_owned_label_and_partial_utf16_value_range_retain_real_paint():
    _svg, wrapper, manifest = _fixture(
        '<rect id="mark" x="100" y="35" width="110" height="95" fill="#3b82f6"/>',
        label="Q1 10", glyph_range=(3, 5), owned=True)
    png, metrics = _render(wrapper, manifest)
    proof = metrics[PROOF_KEY]
    assert proof["complete"] is True, _diagnostic(proof)
    assert proof["targets"][0]["ownedOverlap"]["pixelCount"] > 0
    assert all(item["paintRetained"] for item in proof["targets"])
    finalized = finalize_chart_paint_proof(metrics, png, manifest, wrapper_html=wrapper)
    with pytest.raises(ValueError, match="capture_identity"):
        finalize_chart_paint_proof(metrics, png+b"stale", manifest, wrapper_html=wrapper)
    stale = dict(manifest, canonicalSvgDigest=source_digest("stale"))
    stale["manifestDigest"] = _digest({key: value for key, value in stale.items() if key != "manifestDigest"})
    with pytest.raises(ValueError, match="capture_identity"):
        finalize_chart_paint_proof(metrics, png, stale, wrapper_html=wrapper)
    with pytest.raises(ValueError, match="capture_identity"):
        finalize_chart_paint_proof(metrics, png, manifest, wrapper_html=wrapper+" ")
    validate_chart_paint_binding(finalized, png, manifest, wrapper_html=wrapper, width_px=WIDTH, height_px=HEIGHT)
    changed = dict(manifest, canonicalSvgDigest=source_digest("stale"))
    changed["manifestDigest"] = _digest({key: value for key, value in changed.items() if key != "manifestDigest"})
    with pytest.raises(ValueError, match="identity"):
        validate_chart_paint_binding(finalized, png, changed, wrapper_html=wrapper, width_px=WIDTH, height_px=HEIGHT)
    with pytest.raises(ValueError, match="identity"):
        validate_chart_paint_binding(finalized, png+b"stale", manifest, wrapper_html=wrapper, width_px=WIDTH, height_px=HEIGHT)
    with pytest.raises(ValueError, match="viewport"):
        validate_chart_paint_binding(finalized, png, manifest, wrapper_html=wrapper, width_px=WIDTH+1, height_px=HEIGHT)


def test_base_metrics_digest_survives_existing_portable_nul_normalization():
    from services.svg_renderer import _portable_metrics
    _svg, wrapper, manifest = _fixture('<rect id="mark" x="30" y="35" width="100" height="45" fill="#3b82f6"/>')
    png, metrics = _render(wrapper, manifest)
    metrics = dict(metrics, synthetic_cache_key="first\0second")
    finalized = finalize_chart_paint_proof(metrics, png, manifest, wrapper_html=wrapper)
    portable = _portable_metrics(finalized)
    assert portable["synthetic_cache_key"] == "first\u241fsecond"
    validate_chart_paint_binding(portable, png, manifest, wrapper_html=wrapper, width_px=WIDTH, height_px=HEIGHT)


def test_actual_capture_identity_cannot_be_rebound_to_other_artifacts_or_associations():
    _svg, wrapper, manifest = _fixture('<rect id="mark" x="30" y="35" width="100" height="45" fill="#3b82f6"/>')
    png, metrics = _render(wrapper, manifest)
    finalized = finalize_chart_paint_proof(metrics, png, manifest, wrapper_html=wrapper)
    stale = dict(manifest, canonicalSvgDigest=source_digest("stale"))
    stale["manifestDigest"] = _digest({key: value for key, value in stale.items() if key != "manifestDigest"})
    for artifact, witness, source in [(png+b"stale", manifest, wrapper), (png, stale, wrapper), (png, manifest, wrapper+" ")]:
        with pytest.raises(ValueError, match="capture_identity"):
            finalize_chart_paint_proof(metrics, artifact, witness, wrapper_html=source)
    for artifact, witness in [(png+b"stale", manifest), (png, stale)]:
        with pytest.raises(ValueError, match="identity"):
            validate_chart_paint_binding(finalized, artifact, witness, wrapper_html=wrapper, width_px=WIDTH, height_px=HEIGHT)
    with pytest.raises(ValueError, match="viewport"):
        validate_chart_paint_binding(finalized, png, manifest, wrapper_html=wrapper, width_px=WIDTH+1, height_px=HEIGHT)
    corrupted = dict(finalized, nativeChartPaintProof=dict(finalized[PROOF_KEY], targets=[dict(finalized[PROOF_KEY]["targets"][0], qid="svg-999")]))
    with pytest.raises(ValueError, match="association"):
        validate_chart_paint_binding(corrupted, png, manifest, wrapper_html=wrapper, width_px=WIDTH, height_px=HEIGHT)


def test_mixed_tspan_tail_whitespace_keeps_direct_owned_utf16_binding():
    label = '\n  10  <tspan>%</tspan>  tail  '
    owned = '\n  10    tail  '
    _svg, wrapper, manifest = _fixture('<rect id="mark" x="30" y="35" width="100" height="45" fill="#3b82f6"/>',
                                      label=label, glyph_range=(0, len(owned)))
    _png, metrics = _render(wrapper, manifest)
    proof = metrics[PROOF_KEY]
    glyph = next(row for row in proof["targets"] if row["kind"] == "glyph-range")
    assert glyph["complete"] is True, _diagnostic(proof)
    assert glyph["ownedTextDigest"] == source_digest(owned)
    assert all(row["complete"] for row in glyph["glyphs"])


def test_original_white_glyph_on_white_has_no_semantic_contribution():
    svg, wrapper, manifest = _fixture(
        '<rect id="mark" x="100" y="35" width="110" height="95" fill="#3b82f6"/>',
        label="10", glyph_range=(0, 2))
    svg = svg.replace('fill="#111111"', 'fill="#ffffff"')
    wrapper = svg_preview_html(svg, width=WIDTH, height=HEIGHT)
    manifest["canonicalSvgDigest"] = source_digest(svg)
    manifest["treeDerivativeDigest"] = _tree_digest(svg)
    manifest["manifestDigest"] = _digest({key: value for key, value in manifest.items() if key != "manifestDigest"})
    _png, metrics = _render(wrapper, manifest)
    glyph = next(row for row in metrics[PROOF_KEY]["targets"] if row["kind"] == "glyph-range")
    assert glyph["paintRetained"] is False
    assert glyph["originalContribution"]["pixelCount"] == 0


def test_same_color_glyph_on_its_native_opaque_fill_has_no_semantic_contribution():
    svg, _wrapper, manifest = _fixture(
        '<rect id="mark" x="100" y="35" width="110" height="95" fill="#3b82f6"/>',
        label="10", glyph_range=(0, 2))
    svg = svg.replace('id="label" x="55"', 'id="label" x="125"').replace('fill="#111111"', 'fill="#3b82f6"')
    wrapper = svg_preview_html(svg, width=WIDTH, height=HEIGHT)
    manifest["canonicalSvgDigest"] = source_digest(svg)
    manifest["treeDerivativeDigest"] = _tree_digest(svg)
    manifest["manifestDigest"] = _digest({key: value for key, value in manifest.items() if key != "manifestDigest"})
    _png, metrics = _render(wrapper, manifest)
    glyph = next(row for row in metrics[PROOF_KEY]["targets"] if row["kind"] == "glyph-range")
    assert glyph["paintRetained"] is False, _diagnostic(metrics[PROOF_KEY])
    assert glyph["originalContribution"]["pixelCount"] == 0, _diagnostic(metrics[PROOF_KEY])


@pytest.mark.parametrize("ink", ["#111111", "#000000"])
def test_original_text_paint_preserves_admitted_alpha_stroke_and_spacing(ink):
    svg, _wrapper, manifest = _fixture(
        '<rect id="mark" x="100" y="35" width="110" height="95" fill="#3b82f6"/>',
        label="10", glyph_range=(0, 2))
    svg = svg.replace('id="label"', 'id="label" fill-opacity="0.6" stroke="#111111" stroke-width="0.5" stroke-opacity="0.4" letter-spacing="1.5" font-weight="700" xml:space="preserve"')
    svg = svg.replace('fill="#111111"', f'fill="{ink}"')
    wrapper = svg_preview_html(svg, width=WIDTH, height=HEIGHT)
    manifest["canonicalSvgDigest"] = source_digest(svg)
    manifest["treeDerivativeDigest"] = _tree_digest(svg)
    manifest["manifestDigest"] = _digest({key: value for key, value in manifest.items() if key != "manifestDigest"})
    _png, metrics = _render(wrapper, manifest)
    glyph = next(row for row in metrics[PROOF_KEY]["targets"] if row["kind"] == "glyph-range")
    assert glyph["complete"] is True, _diagnostic(metrics[PROOF_KEY])
    assert glyph["paintRetained"] is True, _diagnostic(metrics[PROOF_KEY])


def test_shared_line_receipt_preserves_each_datum_category_strip():
    _svg, wrapper, manifest = _fixture(
        '<polyline id="mark" points="30,110 100,110 170,110" fill="none" stroke="#3b82f6" stroke-width="5"/>',
        after='<rect x="125" y="100" width="65" height="25" fill="#ffffff"/>')
    chart = manifest["charts"][0]
    first = chart["datums"][0]
    first["geometrySupport"] = {"anchor": {"x": 34, "y": 112}, "categoryStrip": {"minimumX": None, "maximumX": 104}}
    second = dict(first, id="datum-1", categoryIndex=1,
                  geometrySupport={"anchor": {"x": 174, "y": 112}, "categoryStrip": {"minimumX": 104, "maximumX": None}})
    chart["datums"].append(second)
    chart["chartType"] = "line"
    manifest["manifestDigest"] = _digest({key: value for key, value in manifest.items() if key != "manifestDigest"})
    _png, metrics = _render(wrapper, manifest)
    targets = [row for row in metrics[PROOF_KEY]["targets"] if row["kind"] == "primitive"]
    assert [row["datumIds"] for row in targets] == [["datum-0"], ["datum-1"]]
    assert all(row["geometrySupport"] == datum["geometrySupport"] for row, datum in zip(targets, chart["datums"]))
    assert targets[0]["altered"]["pixelCount"] == 0
    assert targets[1]["altered"]["pixelCount"] > 0
    assert targets[1]["paintRetained"] is False


def test_coincident_equal_value_series_use_bound_contributing_native_support():
    svg, wrapper, manifest = _fixture(
        '<polyline id="mark" points="30,110 170,110" fill="none" stroke="#3b82f6" stroke-width="5"/>'
        '<polyline id="upper" points="30,110 170,110" fill="none" stroke="#3b82f6" stroke-width="5"/>')
    nodes = list(ET.fromstring(svg).iter())
    upper_qid = next(f"svg-{index}" for index, node in enumerate(nodes) if node.get("id") == "upper")
    chart = manifest["charts"][0]
    chart["chartType"] = "line"
    first = chart["datums"][0]
    first["geometryRole"] = "line-trace"
    second = dict(first, id="datum-1", seriesIndex=1, primitiveQids=[upper_qid])
    chart["datums"].append(second)
    group = chart["compositionGroup"]
    group["primitiveQids"].append(upper_qid)
    members = []
    class_id = "chart/equivalence/same-native-value"
    for datum in chart["datums"]:
        qid = datum["primitiveQids"][0]
        obligation = {"id": datum["id"] + "/incident-edge-0", "kind": "incident-edge-0", "primitiveQid": qid,
                      "regionRule": "native-alpha-boundary-neighborhood/v1", "radiusPhysicalPixels": 1,
                      "segments": [[[34, 112], [174, 112]]], "alternatives": []}
        support = {"anchor": {"x": 104, "y": 112}, "categoryStrip": {"minimumX": None, "maximumX": None},
                   "axisKey": "same-axis", "unitKey": "", "visibilityObligations": [obligation]}
        support["geometryDigest"] = _digest(support)
        datum["geometrySupport"] = support
        members.append({"datumId": datum["id"], "targetId": f"chart/primitive/{qid}/{datum['id']}",
                        "supportId": obligation["id"], "geometrySupportDigest": support["geometryDigest"]})
        obligation["alternatives"].append({"kind": "equivalent-native-support", "classId": class_id})
    group["equivalenceClasses"] = [{"id": class_id, "axisKey": "same-axis", "unitKey": "", "categoryIndex": 0,
                                    "value": "10", "members": members, "requiresJointOriginalContribution": True}]
    support = first["geometrySupport"]
    group["relationships"].append({"kind": "native-series-layer-order", "primitiveQids": group["primitiveQids"],
        "localOverlaps": [{"datumId": first["id"], "targetQid": first["primitiveQids"][0], "occluderQid": upper_qid,
            "geometrySupportDigest": support["geometryDigest"], "regionRule": "native-alpha-intersection-in-datum-support/v1",
            "geometrySupport": {key: support[key] for key in ("anchor", "categoryStrip")}}]})
    manifest["manifestDigest"] = _digest({key: value for key, value in manifest.items() if key != "manifestDigest"})
    _png, metrics = _render(wrapper, manifest)
    proof = metrics[PROOF_KEY]
    assert proof["complete"] is True, _diagnostic(proof)
    assert proof["paintRetained"] is True, _diagnostic(proof)
    assert proof["targets"][0]["visibilityObligations"][0]["supportedPixels"] == 0
    assert proof["targets"][0]["visibilityObligations"][0]["via"] == "equivalent-native-support"


def test_chart_observation_preserves_the_emitted_default_png_and_base_metrics():
    _svg, wrapper, manifest = _fixture('<rect id="mark" x="30" y="35" width="100" height="45" fill="#3b82f6"/>')
    admitted = admit_static_output_html(wrapper)
    baseline_png, baseline_metrics = slide_capture._render_admitted_html_over_pipe(
        admitted, 60, width_px=WIDTH, height_px=HEIGHT)
    observed_png, observed_metrics = slide_capture._render_admitted_html_over_pipe(
        admitted, 60, width_px=WIDTH, height_px=HEIGHT, chart_witness_manifest=manifest)
    assert observed_png == baseline_png
    assert {key: value for key, value in observed_metrics.items() if key != PROOF_KEY} == baseline_metrics


def test_absent_capture_matches_actual_head_png_and_portable_metrics(tmp_path):
    """Run baseline shared sources, rather than comparing two current branches."""
    backend = Path(__file__).resolve().parents[2]
    checkout = backend.parents[1]
    baseline = tmp_path / "head-overlay"
    files = ["utils/slide_capture.py", "utils/authored_renderer_session.py", "utils/authored_renderer_worker.py",
             "utils/renderer_isolation.py", "utils/slide_capture_worker_client.py", "utils/slide_capture_pipe_render.py",
             "services/svg_renderer.py"]
    for relative in files:
        payload = subprocess.check_output(["git", "show", "HEAD:servers/fastapi/" + relative], cwd=checkout)
        path = baseline / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(payload)
        if relative == "utils/slide_capture_pipe_render.py":
            assert (backend / relative).read_bytes() == payload
    _svg, wrapper, _manifest = _fixture('<rect id="mark" x="30" y="35" width="100" height="45" fill="#3b82f6"/>')
    admitted = admit_static_output_html(wrapper)
    script = """import base64,json,sys
import utils,services
from pathlib import Path
overlay=Path(sys.argv[1]);utils.__path__.insert(0,str(overlay/'utils'));services.__path__.insert(0,str(overlay/'services'))
from utils import slide_capture
from utils.renderer_isolation import AdmittedHtml
from services.svg_renderer import _portable_metrics
payload=json.load(sys.stdin)
record=payload['admitted'];record['asset_digests']=tuple(record['asset_digests'])
png,metrics=slide_capture._render_admitted_html_over_pipe(AdmittedHtml(**record),60,width_px=240,height_px=160)
print(json.dumps({'png':base64.b64encode(png).decode(),'metrics':_portable_metrics(metrics)},ensure_ascii=False))
"""
    from dataclasses import asdict
    run = subprocess.run([sys.executable, "-c", script, str(baseline)], input=json.dumps({"admitted": asdict(admitted)}),
                         cwd=backend, capture_output=True, text=True, encoding="utf-8", timeout=60, check=True)
    actual_head = json.loads(run.stdout.strip().splitlines()[-1])
    png, metrics = slide_capture._render_admitted_html_over_pipe(
        admitted, 60, width_px=WIDTH, height_px=HEIGHT)
    from services.svg_renderer import _portable_metrics
    assert png == base64.b64decode(actual_head["png"], validate=True)
    assert _portable_metrics(metrics) == actual_head["metrics"]


@pytest.mark.parametrize("example", NATIVE_EXAMPLES, ids=lambda example: example["name"])
def test_actual_builder_native_composition_examples(example, tmp_path):
    from utils.slide_capture_chart_paint import prepare_chart_capture_manifest
    source, manifest = example["canonicalSvg"], example["manifest"]
    width, height = 1280, 720
    wrapper = svg_preview_html(source, width=width, height=height)
    prepared = prepare_chart_capture_manifest(manifest, canonical_svg=source, wrapper_html=wrapper,
                                              width_px=width, height_px=height)
    png, metrics = slide_capture._render_admitted_html_over_pipe(
        admit_static_output_html(wrapper), 60, width_px=width, height_px=height,
        chart_witness_manifest=prepared)
    proof = metrics[PROOF_KEY]
    if not proof.get("complete") or not proof.get("paintRetained"):
        receipt = tmp_path / (example["name"] + "-paint-proof.json")
        receipt.write_text(json.dumps(proof, ensure_ascii=False, indent=2), encoding="utf-8")
        pytest.fail(f"{example['name']} native paint proof incomplete; retained receipt: {receipt}")
    assert proof["complete"] is True, (example["name"], _diagnostic(proof))
    assert proof["paintRetained"] is True, (example["name"], _diagnostic(proof))
    finalized = finalize_chart_paint_proof(metrics, png, prepared, wrapper_html=wrapper)
    validate_chart_paint_binding(finalized, png, prepared, wrapper_html=wrapper, width_px=width, height_px=height)


def test_authorized_font_fallback_preserves_canonical_owned_text_ranges():
    from services.svg_renderer import materialize_trusted_svg_fonts
    from utils.renderer_isolation import admit_authored_html
    from utils.slide_capture_chart_paint import prepare_chart_capture_manifest
    from utils.svg_template_fonts import _packaged_font, _fallback_coverage, trusted_font_marker
    missing = _fallback_coverage() - _packaged_font()[2]
    candidates = [ord(char) for char in "₿₽↗€αβ✓∞" if ord(char) in missing]
    candidates += [code for code in sorted(missing) if 127 < code < 65536
                   and unicodedata.category(chr(code))[0] in "LNS" and not chr(code).isspace()]
    assert candidates, "existing authorized fallback face has no eligible missing glyph"
    label = "10" + chr(candidates[0])
    source, _wrapper, manifest = _fixture(
        '<rect id="mark" x="100" y="35" width="110" height="95" fill="#3b82f6"/>',
        label=label, glyph_range=(0, len(label.encode("utf-16-le"))//2))
    source = source.replace('font-family="Arial"', 'font-family="Noto Sans KR"')
    manifest["canonicalSvgDigest"] = source_digest(source)
    manifest["treeDerivativeDigest"] = _tree_digest(source)
    manifest["manifestDigest"] = _digest({key: value for key, value in manifest.items() if key != "manifestDigest"})
    wrapper = svg_preview_html(source, width=WIDTH, height=HEIGHT, source_font_profile=trusted_font_marker())
    prepared = prepare_chart_capture_manifest(manifest, canonical_svg=source, wrapper_html=wrapper,
                                              width_px=WIDTH, height_px=HEIGHT)
    admitted = materialize_trusted_svg_fonts(wrapper, admit_authored_html(wrapper))
    assert admitted.html.count("<tspan") > wrapper.count("<tspan")
    png, metrics = slide_capture._render_admitted_html_over_pipe(
        admitted, 60, width_px=WIDTH, height_px=HEIGHT, chart_witness_manifest=prepared)
    proof = metrics[PROOF_KEY]
    assert proof["complete"] is True, _diagnostic(proof)
    assert proof["paintRetained"] is True, _diagnostic(proof)
    finalized = finalize_chart_paint_proof(metrics, png, prepared, wrapper_html=wrapper)
    validate_chart_paint_binding(finalized, png, prepared, wrapper_html=wrapper, width_px=WIDTH, height_px=HEIGHT)


def test_service_context_binds_materialized_wrapper_without_mutating_semantic_manifest():
    from utils.slide_capture_chart_paint import prepare_chart_capture_manifest, validate_manifest
    svg, wrapper, manifest = _fixture('<rect id="mark" x="30" y="35" width="100" height="45" fill="#3b82f6"/>')
    changed_wrapper = wrapper.replace('<body>', '<body class="codex-timeline-ink-v1">')
    prepared = prepare_chart_capture_manifest(manifest, canonical_svg=svg, wrapper_html=changed_wrapper,
                                             width_px=WIDTH, height_px=HEIGHT)
    assert "captureContext" not in manifest
    assert prepared["manifestDigest"] == manifest["manifestDigest"]
    validate_manifest(prepared, source_html=changed_wrapper, width_px=WIDTH, height_px=HEIGHT)
    with pytest.raises(ValueError, match="context"):
        validate_manifest(prepared, source_html=wrapper, width_px=WIDTH, height_px=HEIGHT)
    with pytest.raises(ValueError, match="canonical_svg"):
        prepare_chart_capture_manifest(manifest, canonical_svg=svg+" ", wrapper_html=wrapper,
                                       width_px=WIDTH, height_px=HEIGHT)


def test_missing_resource_observation_is_unproven_never_complete():
    _svg, wrapper, manifest = _fixture('<rect id="mark" x="30" y="35" width="100" height="45" fill="#3b82f6"/>')
    output = io.BytesIO()
    Image.new("RGB", (WIDTH, HEIGHT), "white").save(output, format="PNG")

    def command(method, params):
        if method == "Runtime.evaluate":
            return {"result": {"value": {"fontsReady": False, "imagesReady": True}}}
        if method == "Page.captureScreenshot":
            return {"data": base64.b64encode(output.getvalue()).decode()}
        return {}

    proof = collect_chart_paint_proof(command=command, manifest=manifest, source_html=wrapper,
                                    final_png=output.getvalue(), metrics={}, width_px=WIDTH, height_px=HEIGHT)
    assert proof["complete"] is False
    assert all(item["complete"] is False for item in proof["targets"])
