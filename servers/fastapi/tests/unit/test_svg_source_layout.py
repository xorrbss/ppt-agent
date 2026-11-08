from copy import deepcopy
import asyncio
from html import escape
import xml.etree.ElementTree as ET

import pytest

from services.source_content.contracts import SourceContentManifest
from services.source_content.digests import digest_json
from services.svg_contract import source_digest
from services.svg_source_layout import (SvgLayoutPlan, assemble_source_svg, page_digest,
    source_pages, verify_source_receipt)


def manifest_for(blocks):
    packed = [dict(sourceBlockId=f"p0:{i}", kind=kind, semanticRole=kind,
        order=i, payload=payload, contentDigest=digest_json(payload)) for i, (kind, payload) in enumerate(blocks)]
    manifest = SourceContentManifest(manifest_id="m", owner_id="owner", reference_snapshot_id="snap",
        source_digest="sha256:input", manifest_digest="pending",
        object_inventory=dict(discoveredObjectCount=0, classifiedObjectCount=0, contentBearingObjectCount=0),
        inventory_objects=(), units=[dict(sourceUnitId="p0", sourceIndex=0, blocks=packed)],
        counts=dict(sourceUnits=1, textBlocks=sum(k == "text" for k, p in blocks), tableCells=0,
                    chartValues=0, uniqueImages=0, imageOccurrences=sum(k == "image" for k, p in blocks)))
    payload = manifest.model_dump(mode="json", by_alias=True)
    payload.pop("manifestDigest")
    return manifest.model_copy(update={"manifest_digest": digest_json(payload)})


def plan_for(manifest):
    return dict(source_unit_id="p0", page_digest=page_digest(manifest, 0),
        placements=[dict(block_id=b.source_block_id, x=20 + i * 300, y=20, width=280,
                        height=200, font_size=20) for i, b in enumerate(manifest.units[0].blocks)])


def assemble(manifest, plan=None, **kwargs):
    return assemble_source_svg(manifest, 0, plan or plan_for(manifest), source_images=kwargs.get("source_images", {}),
        image_slots=kwargs.get("image_slots", {}), min_font_size_px=12, style_digest="sha256:style")


def test_raw_korean_and_xml_characters_are_inserted_exactly_and_receipt_bound():
    manifest = manifest_for([("text", {"text": "한글 & <original> 123.4%\n두 번째"})])
    result = assemble(manifest)
    assert "&amp;" in result.svg_content and "&lt;original&gt;" in result.svg_content
    assert "".join(b["text"] for b in result.receipt["bindings"]) == manifest.units[0].blocks[0].payload["text"]
    assert result.receipt["rendering_verified"] is False
    assert verify_source_receipt(result.svg_content, result.receipt, manifest, 0)
    assert assemble(manifest).svg_content == result.svg_content


@pytest.mark.parametrize("mutation", ["missing", "duplicate", "foreign", "stale"])
def test_plan_coverage_and_page_authority_rejected(mutation):
    manifest = manifest_for([("text", {"text": "Original"})])
    plan = plan_for(manifest)
    if mutation == "missing":
        plan["placements"] = []
    elif mutation == "duplicate":
        plan["placements"] *= 2
    elif mutation == "foreign":
        plan["placements"][0]["block_id"] = "p1:0"
    else:
        plan["page_digest"] = "sha256:stale"
    with pytest.raises(ValueError, match="svg_source_plan"):
        assemble(manifest, plan)


def test_model_cannot_supply_text_or_drop_character_ranges():
    manifest = manifest_for([("text", {"text": "Original"})])
    plan = plan_for(manifest)
    plan["placements"][0]["text"] = "Edited"
    with pytest.raises(ValueError):
        assemble(manifest, plan)
    del plan["placements"][0]["text"]
    plan["placements"][0]["line_ends"] = [3]
    with pytest.raises(ValueError, match="range_incomplete"):
        assemble(manifest, plan)


def test_duplicate_asset_occurrences_require_distinct_bound_slots():
    image = {"assetId": "asset", "contentDigest": "sha256:image", "crop": {"left": 0}}
    manifest = manifest_for([("text", {"text": "Title"}), ("image", image), ("image", image)])
    slots = {"p0:1": "source-slot-one", "p0:2": "source-slot-two"}
    images = {slot: {"required": True} for slot in slots.values()}
    result = assemble(manifest, source_images=images, image_slots=slots)
    assert len([b for b in result.receipt["bindings"] if b["kind"] == "image"]) == 2
    with pytest.raises(ValueError, match="image_occurrence"):
        assemble(manifest, source_images=images, image_slots={"p0:1": "source-slot-one", "p0:2": "source-slot-one"})


def test_merged_table_cells_keep_values_structure_and_reading_order():
    manifest = manifest_for([("table", {"rows": [[
        {"text": "매출 100", "gridSpan": 2, "sourceCellId": "cell-a"},
        {"text": "", "hMerge": True, "mergeCovered": True}], [
        {"text": "A&B", "sourceCellId": "cell-b"}, {"text": "25%", "sourceCellId": "cell-c"}]]})])
    result = assemble(manifest)
    assert [b["text"] for b in result.receipt["bindings"]] == ["매출 100", "A&B", "25%"]
    assert result.receipt["bindings"][0]["column_span"] == 2
    assert verify_source_receipt(result.svg_content, result.receipt, manifest, 0)

    # W1 LLM-input view: table text kept once (cells[]), everything else and the original untouched.
    import json
    from services.generation_core.authoring.author_slide_svg import _llm_source_content_view
    from services.generation_core.authoring.author_source_svg_layout import _whole_page_facts

    facts = _whole_page_facts(source_pages(manifest)[0], {})
    content = json.dumps({"source_authoring_contract": {"version": "svg-source-whole-page/v1"},
        "source_page_id": "p0", "source_facts": facts}, ensure_ascii=False)
    original_facts = deepcopy(facts)
    value, receipt = _llm_source_content_view(content)
    assert facts == original_facts and json.loads(content)["source_facts"] == original_facts
    assert value["source_facts"][0]["cells"] == original_facts[0]["cells"]
    assert [c["text"] for c in value["source_facts"][0]["cells"]] == ["매출 100", "A&B", "25%"]
    rows = value["source_facts"][0]["data"]["rows"]
    assert all("text" not in cell for cell in (rows[0][0], rows[1][0], rows[1][1]))
    assert rows[0][1] == original_facts[0]["data"]["rows"][0][1]
    assert rows[0][1]["mergeCovered"] is True and rows[0][0]["gridSpan"] == 2
    assert [cell["sourceCellId"] for cell in (rows[0][0], rows[1][0], rows[1][1])] == ["cell-a", "cell-b", "cell-c"]
    assert receipt["input_assembly_version"] == "svg-author-input-dedup/v1"
    dedup = receipt["input_dedup"]
    assert dedup["table_text_dup_bytes"] > 0 and dedup["json_double_escape_bytes"] > 0
    assert dedup["total_omitted_bytes"] == dedup["table_text_dup_bytes"] + dedup["json_double_escape_bytes"]
    # Receipt sizes are UTF-8 byte counts (the Korean text makes bytes differ from characters).
    assert len(content.encode("utf-8")) > len(content)
    assert dedup["json_double_escape_bytes"] == len(json.dumps(content, ensure_ascii=False).encode("utf-8")) - len(content.encode("utf-8"))
    # Unwrap is lossless: restoring the omitted text reproduces the original fact exactly.
    restored = deepcopy(value["source_facts"][0])
    for cell in restored["cells"]:
        restored["data"]["rows"][cell["row"]][cell["column"]]["text"] = cell["text"]
    assert restored == original_facts[0]
    # Fail-open: one mismatched cell text keeps the whole table as produced.
    mismatched = deepcopy(facts)
    mismatched[0]["cells"][1]["text"] = "A&B "
    kept, kept_receipt = _llm_source_content_view(json.dumps({"source_authoring_contract": {}, "source_page_id": "p0",
        "source_facts": mismatched}, ensure_ascii=False))
    assert kept["source_facts"] == mismatched and kept_receipt["input_dedup"]["table_text_dup_bytes"] == 0
    # Non-canonical or non-typed strings stay strings.
    assert _llm_source_content_view("plain source") == ("plain source", {"input_json_unwrap": False})
    # A lone surrogate cannot be UTF-8 encoded: the view fails open to the unmodified string, never sanitized.
    lone = json.dumps({"source_authoring_contract": {}, "source_page_id": "p0", "source_facts": [{"t": chr(0xD800)}]},
                      ensure_ascii=False)
    assert chr(0xD800) in lone and _llm_source_content_view(lone) == (lone, {"input_json_unwrap": False})
    assert _llm_source_content_view(content.replace(": ", ":", 1))[0] == content.replace(": ", ":", 1)


@pytest.mark.parametrize("kind,payload", [("chart", {}), ("shape", {"preset": "unknownPreset"}),
    ("shape", {"preset": "diamond", "geometry": {"rotationDegrees": 45}}),
    ("shape", {"preset": "diamond", "geometry": {"flipH": True}}),
    ("image", {"crop": {"left": .1}}), ("image", {"geometry": {"rotationDegrees": 90}})])
def test_unsupported_content_fails_preflight(kind, payload):
    with pytest.raises(ValueError, match="unsupported"):
        source_pages(manifest_for([("text", {"text": "Title"}), (kind, payload)]))


def test_image_only_pages_rejected_before_authoring():
    with pytest.raises(ValueError, match="image_only_page"):
        source_pages(manifest_for([("image", {"contentDigest": "sha256:image"})]))


@pytest.mark.parametrize("preset", ["parallelogram", "rightArrow", "leftArrow", "upArrow", "downArrow",
    "leftRightArrow", "upDownArrow", "chevron", "triangle", "pentagon", "hexagon", "star5",
    "arc", "donut", "cloud", "sun", "cube", "flowChartDecision"])
def test_presets_preserve_shape_occurrence_as_editable_svg(preset, tmp_path):
    if preset == "rightArrow":
        import os
        import subprocess
        import sys

        probe = subprocess.run([sys.executable, "-B", "-c",
            "from services.svg_source_preset_shapes import supports_source_preset; "
            "assert supports_source_preset('rightArrow')"], cwd=tmp_path,
            env={key: value for key, value in os.environ.items() if key not in {"PYTHONPATH", "PYTHONHOME"}},
            capture_output=True, text=True, check=False)
        assert probe.returncode == 0, probe.stderr
    manifest = manifest_for([("text", {"text": "Title"}), ("shape", {"preset": preset})])
    result = assemble(manifest)
    root = ET.fromstring(result.svg_content)
    if preset == "parallelogram":
        assert root.find("{http://www.w3.org/2000/svg}polygon") is not None
    else:
        group = root.find("{http://www.w3.org/2000/svg}g")
        paths = list(group)
        assert paths and all(path.tag == "{http://www.w3.org/2000/svg}path" for path in paths)
        assert all(path.get("d", "").startswith("M") for path in paths)
        assert all(not any(command in path.get("d") for command in "ACQ") for path in paths)
    assert len([b for b in result.receipt["bindings"] if b["kind"] == "shape"]) == 1
    assert verify_source_receipt(result.svg_content, result.receipt, manifest, 0)
    assert assemble(manifest).svg_content == result.svg_content


def test_diamond_preserves_all_occurrences_as_editable_polygons():
    manifest = manifest_for([("text", {"text": "Original facts 100%"}),
        *[("shape", {"preset": "diamond"}) for _ in range(8)],
        ("shape", {"preset": "rect"})])
    original = manifest.model_dump(mode="json", by_alias=True)
    plan = plan_for(manifest)
    for index, placement in enumerate(plan["placements"][1:]):
        placement.update(x=50 + index * 100, y=320, width=80, height=40,
                         background="#234567", stroke="#ABCDEF")
    result = assemble(manifest, plan)
    root = ET.fromstring(result.svg_content)
    nodes = {node.get("id"): node for node in root.iter() if node.get("id")}
    shapes = [binding for binding in result.receipt["bindings"] if binding["kind"] == "shape"]
    assert len(shapes) == 9
    for index, binding in enumerate(shapes[:8]):
        node = nodes[binding["element_id"]]
        x = 50 + index * 100
        assert node.tag == "{http://www.w3.org/2000/svg}polygon"
        assert [tuple(map(float, point.split(","))) for point in node.get("points").split()] == [
            (x + 40, 320), (x + 80, 340), (x + 40, 360), (x, 340)]
        assert node.get("fill") == "#234567"
        assert node.get("stroke") == "#ABCDEF"
        assert binding["source_id"] == manifest.units[0].blocks[index + 1].source_block_id
    assert nodes[shapes[-1]["element_id"]].tag == "{http://www.w3.org/2000/svg}rect"
    assert "".join(binding["text"] for binding in result.receipt["bindings"]
                   if binding["kind"] == "text") == "Original facts 100%"
    assert manifest.model_dump(mode="json", by_alias=True) == original
    assert verify_source_receipt(result.svg_content, result.receipt, manifest, 0)


def test_straight_connector_flip_preserves_endpoint_direction():
    manifest = manifest_for([("text", {"text": "Title"}),
                             ("shape", {"preset": "line", "geometry": {"flipH": True}})])
    page = manifest.units[0]
    block = page.blocks[1].model_copy(update={"semantic_role": "connector"})
    manifest = manifest.model_copy(update={"units": (page.model_copy(update={"blocks": (page.blocks[0], block)}),)})
    packed = manifest.model_dump(mode="json", by_alias=True)
    packed.pop("manifestDigest")
    manifest = manifest.model_copy(update={"manifest_digest": digest_json(packed)})
    result = assemble(manifest)
    line = next(n for n in ET.fromstring(result.svg_content) if n.tag.endswith("}line"))
    assert float(line.get("x1")) > float(line.get("x2"))


def test_receipt_rejects_tamper_even_if_attacker_updates_svg_digest():
    manifest = manifest_for([("text", {"text": "Original"})])
    result = assemble(manifest)
    svg = result.svg_content.replace("Original", "Changed")
    receipt = deepcopy(result.receipt)
    receipt["svg_digest"] = source_digest(svg)
    with pytest.raises(ValueError, match="text_mismatch"):
        verify_source_receipt(svg, receipt, manifest, 0)


def _source_linefeed_case(expected, body):
    from services.generation_core.authoring.author_source_svg_layout import _whole_page_facts
    manifest = manifest_for([("text", {"text": expected})])
    facts = _whole_page_facts(manifest.units[0], {})
    svg = ('<svg xmlns="http://www.w3.org/2000/svg" width="1280" height="720" viewBox="0 0 1280 720">'
        '<rect x="10" y="10" width="400" height="100" fill="#ffffff" stroke="#000000"/>'
        f'<text id="{facts[0]["element_id"]}" x="20" y="50" font-family="Pretendard" '
        'font-size="20" font-weight="700" fill="#111111" xml:space="preserve">' + body + '</text></svg>')
    return manifest, facts, svg


def _restore_source_linefeed_case(manifest, facts, svg):
    from services.svg_source_layout import restore_source_linefeeds
    return restore_source_linefeeds(svg, facts=facts, manifest_digest=manifest.manifest_digest,
        source_page_id="p0", raw_output_digest=source_digest(svg))


@pytest.mark.parametrize("expected,body", [
    ("A\nB", 'A<tspan x="20" y="80">B</tspan>'),
    ("A\n\nB\n", 'A\n<tspan x="20" y="80">B</tspan>'),
    ("\nA\nB", '<tspan x="20" y="80">A</tspan>B'),
    ("A & <\nB\t12\nC", 'A &amp; &lt;<tspan x="20" y="80">B\t12</tspan>C'),
])
def test_source_linefeed_restoration_preserves_text_slots_geometry_and_is_idempotent(expected, body):
    manifest, facts, svg = _source_linefeed_case(expected, body)
    before = ET.fromstring(svg)
    restored, proof = _restore_source_linefeed_case(manifest, facts, svg)
    after = ET.fromstring(restored)
    assert "".join(next(n for n in after if n.tag.endswith("}text")).itertext()) == expected
    assert [(n.tag, n.attrib) for n in before.iter()] == [(n.tag, n.attrib) for n in after.iter()]
    assert proof["raw_output_digest"] == source_digest(svg)
    assert proof["input_svg_digest"] == source_digest(svg)
    assert proof["restored_svg_digest"] == source_digest(restored)
    assert proof["restored_lf_count"] == expected.count("\n") - "".join(
        next(n for n in before if n.tag.endswith("}text")).itertext()).count("\n")
    assert proof["bindings"][0]["source_text_digest"] == source_digest(expected)
    assert all(expected[position] == "\n" for position in proof["bindings"][0]["positions"])
    assert _restore_source_linefeed_case(manifest, facts, restored) == (restored, None)


@pytest.mark.parametrize("expected,actual", [
    ("A\n12 B", "A13 B"), ("A\n12 B", "A12  B"),
    ("A\nB", "A\n\nB"), ("A\nBC", "AB\nC"),
    ("A\t\nB", "A B"), ("A\r\nB", "AB"), ("A\nB", "B A"),
])
def test_source_linefeed_restoration_rejects_non_lf_changes_extra_or_moved_lf(expected, actual):
    manifest, facts, svg = _source_linefeed_case(expected, escape(actual))
    assert _restore_source_linefeed_case(manifest, facts, svg) == (svg, None)


@pytest.mark.parametrize("mutation", ["missing", "duplicate", "overlap"])
def test_source_linefeed_restoration_rejects_ambiguous_source_owners(mutation):
    manifest, facts, svg = _source_linefeed_case("A\nB", "AB")
    root = ET.fromstring(svg)
    if mutation == "missing":
        root.remove(root[-1])
    elif mutation == "duplicate":
        root.append(deepcopy(root[-1]))
    else:
        other = deepcopy(facts[0])
        facts.append(other)
    raw = ET.tostring(root, encoding="unicode")
    assert _restore_source_linefeed_case(manifest, facts, raw) == (raw, None)


def test_source_linefeed_binding_is_opt_in_and_final_verbatim_validator_remains_strict():
    from services.svg_source_layout import bind_whole_page_source_svg
    manifest, facts, svg = _source_linefeed_case("A\nB", 'A<tspan x="20" y="80">B</tspan>')
    kwargs = dict(facts=facts, width=1280, height=720, style_snapshot={"digest": "sha256:style",
                  "source_authoring_contract": {"version": "svg-source-whole-page/v1"}},
                  minimum_font_px=12)
    with pytest.raises(ValueError, match="text_mismatch"):
        bind_whole_page_source_svg(svg, manifest, 0, **kwargs)
    restored, receipt = bind_whole_page_source_svg(svg, manifest, 0, **kwargs,
        restore_source_lf=True, raw_output_digest=source_digest(svg))
    proof = receipt["source_lf_restoration"]
    assert proof["canonical_svg_digest"] == receipt["svg_digest"] == source_digest(restored)
    assert proof["manifest_digest"] == manifest.manifest_digest
    assert verify_source_receipt(restored, receipt, manifest, 0)
    tampered = restored.replace("A\n", "A")
    changed_receipt = {**receipt, "svg_digest": source_digest(tampered)}
    with pytest.raises(ValueError, match="text_mismatch"):
        verify_source_receipt(tampered, changed_receipt, manifest, 0)


def test_source_linefeed_table_cell_and_source_image_geometry_are_preserved():
    from services.generation_core.authoring.author_source_svg_layout import _whole_page_facts
    from services.svg_source_layout import restore_source_linefeeds
    manifest = manifest_for([("table", {"rows": [[{"text": "A\nB", "sourceCellId": "cell-original"}]]}),
                             ("image", {"contentDigest": "sha256:image"})])
    facts = _whole_page_facts(manifest.units[0], {"p0:1": "source-slot-image"})
    table, image = facts
    svg = ('<svg xmlns="http://www.w3.org/2000/svg" width="1280" height="720" viewBox="0 0 1280 720">'
        f'<g id="{table["element_id"]}"><rect x="20" y="20" width="200" height="100" fill="#ffffff"/>'
        f'<text id="{table["cells"][0]["element_id"]}" x="30" y="60" font-family="Pretendard" '
        'font-size="20" fill="#111111">A<tspan x="30" y="90">B</tspan></text></g>'
        f'<image id="{image["element_id"]}" x="300" y="40" width="100" height="80" '
        'href="source-slot-image" preserveAspectRatio="xMidYMid meet"/></svg>')
    restored, proof = restore_source_linefeeds(svg, facts=facts, manifest_digest=manifest.manifest_digest,
        source_page_id="p0", raw_output_digest=source_digest(svg))
    assert proof["bindings"][0]["source_id"] == "cell-original"
    assert "".join(next(n for n in ET.fromstring(restored).iter() if n.tag.endswith("}text")).itertext()) == "A\nB"
    assert [(n.tag, n.attrib) for n in ET.fromstring(svg).iter()] == [
        (n.tag, n.attrib) for n in ET.fromstring(restored).iter()]


def test_source_linefeed_author_preserves_sealed_raw_receipt_and_optional_provenance(monkeypatch):
    from services.generation_core.authoring.author_slide_svg import SvgAuthorResult
    from services.generation_core.authoring.author_source_svg_layout import author_source_svg_layout
    manifest, _facts, raw = _source_linefeed_case("A\nB", 'A<tspan x="20" y="80">B</tspan>')
    sealed_receipt = {"raw_digest": source_digest(raw), "source_digest": source_digest(raw),
                      "provider": "codex", "attempt_ordinal": 1}

    async def author(**kwargs):
        return SvgAuthorResult(raw, raw, deepcopy(sealed_receipt), None)

    monkeypatch.setattr("services.generation_core.authoring.author_slide_svg.author_slide_svg", author)
    result = asyncio.run(author_source_svg_layout(manifest, 0,
        {"digest": "sha256:style", "source_authoring_contract": {"version": "svg-source-whole-page/v1"}},
        image_slots={}, source_images={}, min_font_size_px=12, width=1280, height=720))
    assert result.raw_response == raw
    assert sealed_receipt["source_digest"] == source_digest(raw)
    assert result.receipt["raw_digest"] == source_digest(raw)
    assert result.receipt["provider"] == "codex" and result.receipt["attempt_ordinal"] == 1
    assert not result.receipt.get("validation_error")
    preservation = result.receipt["source_preservation"]
    assert preservation["source_lf_restoration"]["restored_lf_count"] == 1
    assert verify_source_receipt(result.svg_content, preservation, manifest, 0)


def test_boxes_cannot_occlude_other_content_and_font_floor_cannot_drop():
    manifest = manifest_for([("text", {"text": "A"}), ("text", {"text": "B"})])
    plan = plan_for(manifest)
    plan["placements"][1]["x"] = 20
    with pytest.raises(ValueError, match="boxes_overlap"):
        assemble(manifest, plan)
    plan = plan_for(manifest)
    plan["placements"][0]["font_size"] = 7
    with pytest.raises(ValueError, match="text_box_unfit"):
        assemble(manifest, plan)


def test_actual_font_measurement_repairs_wrapping_without_editing_source():
    from services.generation_core.authoring.author_source_svg_layout import _source_context, repair_source_plan
    manifest = manifest_for([("text", {"text": "한글 English 숫자 123 " * 10})])
    context = _source_context(manifest, 0)
    assert context["measurements"]["font_digest"].startswith("sha256:")
    plan, count = repair_source_plan(SvgLayoutPlan.model_validate(plan_for(manifest)), manifest, 0, 12)
    assert count == 1
    result = assemble(manifest, plan)
    assert "".join(b["text"] for b in result.receipt["bindings"]) == manifest.units[0].blocks[0].payload["text"]


def test_numeric_custom_geometry_curves_flatten_and_bind_to_source():
    from lxml import etree
    from services.source_content.pptx_custom_geometry import extract_custom_geometry
    element = etree.fromstring(b'''<p:sp xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"
       xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"><p:spPr><a:custGeom><a:pathLst>
       <a:path w="100" h="100"><a:moveTo><a:pt x="0" y="0"/></a:moveTo>
       <a:cubicBezTo><a:pt x="0" y="100"/><a:pt x="100" y="100"/><a:pt x="100" y="0"/></a:cubicBezTo>
       <a:quadBezTo><a:pt x="50" y="20"/><a:pt x="0" y="0"/></a:quadBezTo><a:close/>
       </a:path></a:pathLst></a:custGeom></p:spPr></p:sp>''')
    custom = extract_custom_geometry(element)
    manifest = manifest_for([("text", {"text": "Title"}), ("shape", {"preset": None, "customGeometry": custom})])
    result = assemble(manifest)
    root = ET.fromstring(result.svg_content)
    path = next(n for n in root if n.tag.endswith("}path"))
    assert path.get("d").count("L") > 4 and "C" not in path.get("d") and "Q" not in path.get("d")
    binding = next(b for b in result.receipt["bindings"] if b["kind"] == "shape")
    assert binding["geometry_digest"] == digest_json(custom)
    assert verify_source_receipt(result.svg_content, result.receipt, manifest, 0)


@pytest.mark.parametrize("coordinates", [("guide", "0"), ("NaN", "0")])
def test_custom_geometry_non_numeric_source_fails_closed(coordinates):
    from lxml import etree
    from services.source_content.pptx_custom_geometry import extract_custom_geometry, validate_custom_geometry
    x, y = coordinates
    element = etree.fromstring(f'''<p:sp xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"
       xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"><p:spPr><a:custGeom><a:pathLst>
       <a:path w="100" h="100"><a:moveTo><a:pt x="{x}" y="{y}"/></a:moveTo></a:path>
       </a:pathLst></a:custGeom></p:spPr></p:sp>'''.encode())
    custom = extract_custom_geometry(element)
    assert custom.get("unsupported")
    with pytest.raises(ValueError):
        validate_custom_geometry(custom)


def _v2_style():
    from utils.svg_template_fonts import font_material
    digest, _ = font_material("Noto Sans KR", 400)
    template = dict(digest="sha256:template", rule_version="test/v2",
        fonts={r: dict(family="Noto Sans KR", weight=400, size=20, digest=digest)
               for r in ("heading", "body", "table")},
        tokens=dict(background="#FFFFFF", text="#172033", heading="#172033", accent="#234567"),
        shape=dict(fill="#FFFFFF", border="#234567"), decorations=[],
        table=dict(body_fill="#FFFFFF", border="#234567", header_fill="#234567",
                   header_text="#FFFFFF", header_weight=700, cell_padding=3),
        layouts=[dict(id="content-stack", columns=1, gap=12, min_gap=8,
            title_box=dict(x=40,y=30,width=1043,height=100),
            body_box=dict(x=40,y=160,width=1043,height=584),
            allowed_variations=dict(model_layout=True))])
    return dict(digest="sha256:style", source_template=template)


def _authored_source_table_drawing(manifest, xs, ys):
    from services.generation_core.authoring.author_source_svg_layout import _whole_page_facts
    fact = _whole_page_facts(manifest.units[0], {})[0]
    ns = "{http://www.w3.org/2000/svg}"
    root = ET.Element(ns + "svg", width="1280", height="840", viewBox="0 0 1280 840")
    group = ET.SubElement(root, ns + "g", id=fact["element_id"])
    for cell in fact["cells"]:
        row, column, rs, cs = cell["row"], cell["column"], cell["row_span"], cell["column_span"]
        ET.SubElement(group, ns + "rect", x=str(xs[column]), y=str(ys[row]),
            width=str(xs[column + cs] - xs[column]), height=str(ys[row + rs] - ys[row]),
            fill="#EEF2FF", stroke="#334155", **{"stroke-width": "1"})
        ET.SubElement(group, ns + "text", id=cell["element_id"],
            x=str(xs[column] + 4), y=str(ys[row] + 25),
            **{"font-family": "Inter", "font-size": "18", "fill": "#111111"}).text = cell["text"]
    return root, fact


def _merged_source_table_manifest():
    return manifest_for([("table", {"rows": [
        [{"text": "Merged source", "rowSpan": 2, "gridSpan": 2},
         {"text": "", "mergeCovered": True}, {"text": "A"}],
        [{"text": "", "mergeCovered": True}, {"text": "", "mergeCovered": True}, {"text": "B"}],
        [{"text": "C"}, {"text": "D"}, {"text": "E"}]],
        "columnWidthsEmu": [100, 100, 100], "rowHeightsEmu": [100, 100, 100]})])


def _bind_authored_source_table(root, manifest):
    from services.generation_core.authoring.author_source_svg_layout import _whole_page_facts
    from services.svg_source_layout import bind_whole_page_source_svg
    return bind_whole_page_source_svg(ET.tostring(root, encoding="unicode"), manifest, 0,
        facts=_whole_page_facts(manifest.units[0], {}), width=1280, height=840,
        style_snapshot={"digest": "sha256:style", "source_authoring_contract": {"version": "svg-source-whole-page/v1"}},
        minimum_font_px=12)


@pytest.mark.parametrize("merged", [False, True])
@pytest.mark.parametrize("text_encoding", ["direct", "tspan_only", "mixed"])
@pytest.mark.parametrize("mixed_borders", [False, True])
def test_source_native_table_authored_variable_grid_preserves_projection(merged, text_encoding, mixed_borders):
    from services.svg_source_native_tables import source_table_native_objects, source_table_grid_correction
    if merged:
        manifest = _merged_source_table_manifest()
        widths, heights = [100, 250, 50], [38, 59, 59]
    else:
        # Geometry ratios from an observed 9x6 candidate; no document content.
        widths, heights = [100, 250, 50, 390, 180, 138], [38] + [59] * 8
        manifest = manifest_for([("table", {"rows": [[{"text": f"{r}:{c}"} for c in range(6)] for r in range(9)],
            "columnWidthsEmu": [100] * 6, "rowHeightsEmu": [100] * 9})])
    table_source = deepcopy(manifest.units[0].blocks[0].payload)
    table_source["rows"][0][0]["text"] = "A\nB"
    manifest = manifest_for([("table", table_source)])
    original = manifest.model_dump(mode="json", by_alias=True)
    xs, ys = [40], [120]
    for width in widths:
        xs.append(xs[-1] + width)
    for height in heights:
        ys.append(ys[-1] + height)
    root, fact = _authored_source_table_drawing(manifest, xs, ys)
    if mixed_borders:
        rects = [node for node in root[0] if node.tag.endswith("}rect")]
        for cell, rect in zip(fact["cells"], rects, strict=True):
            if cell["row"] == len(heights) - 1:
                rect.set("stroke", "#2563EB")
    if text_encoding != "direct":
        for node in root[0]:
            if node.tag != "{http://www.w3.org/2000/svg}text" or not node.text:
                continue
            content = node.text
            node.text = None if text_encoding == "tspan_only" else content[:1]
            ET.SubElement(node, "{http://www.w3.org/2000/svg}tspan").text = (
                content if text_encoding == "tspan_only" else content[1:])
    original_svg = ET.tostring(root, encoding="unicode")
    assert source_table_grid_correction(manifest.units[0].blocks[0], root[0]) is None
    svg, receipt = _bind_authored_source_table(root, manifest)
    assert verify_source_receipt(svg, receipt, manifest, 0)
    wire, summary = source_table_native_objects(svg, receipt, manifest, 0, pt_per_px=.75)
    table = wire["objects"][0]
    assert summary["accepted"] == 1 and summary["source_cells_verified"]
    assert table["id"] == fact["element_id"]
    assert table["payload"]["columnWidths"] == widths
    assert table["payload"]["rowHeightsPt"] == [height * .75 for height in heights]
    for cell in fact["cells"]:
        projected = table["payload"]["rows"][cell["row"]][cell["column"]]
        assert projected["text"] == cell["text"]
        assert projected["fontSizePt"] == 13.5 and projected["fontFamily"] == "Inter"
        assert projected["fill"] == "#EEF2FF"
        borders = table["payload"]["cellStyles"][cell["row"]][cell["column"]]["borders"]
        assert set(borders) == {"top", "right", "bottom", "left"}
        for edge, border in borders.items():
            # The last row paints after its neighbours: its top edge also
            # becomes the bottom edge of the cell(s) immediately above it.
            blue = mixed_borders and (cell["row"] == len(heights) - 1 or (
                edge == "bottom" and cell["row"] + cell["row_span"] == len(heights) - 1))
            assert border == {"color": "#2563EB" if blue else "#334155", "widthPt": .75}
    assert table["payload"]["rows"][0][0]["text"] == "A\nB"
    if mixed_borders:
        # Filled cells without an opaque positive stroke can occlude a
        # neighbour's painted edge; they must never become a full native line.
        for changes in ({"stroke": "none"}, {"stroke-opacity": "0"}, {"stroke-width": "0"}):
            unsafe_root = deepcopy(root)
            next(node for node in unsafe_root[0] if node.tag.endswith("}rect")).attrib.update(changes)
            unsafe_svg, unsafe_receipt = _bind_authored_source_table(unsafe_root, manifest)
            with pytest.raises(ValueError, match="^svg_source_native_table_border_style_mismatch$"):
                source_table_native_objects(unsafe_svg, unsafe_receipt, manifest, 0, pt_per_px=.75)
        from services.svg_source_native_tables import _mixed_cell_borders
        from services.generation_core.drivers.svg.native_geometry import Box
        for tiny_cell in (Box(0, 0, 1.5, 10), Box(0, 0, 10, 1.5)):
            with pytest.raises(ValueError, match="^svg_source_native_table_border_style_mismatch$"):
                _mixed_cell_borders([], tiny_cell, under="#FFFFFF", pt_per_px=.75)
    if merged:
        assert table["payload"]["mergedCells"] == [dict(row=0, column=0, rowSpan=2, columnSpan=2)]
    else:
        assert not table["payload"].get("mergedCells")
    assert ET.tostring(root, encoding="unicode") == original_svg
    assert manifest.model_dump(mode="json", by_alias=True) == original


@pytest.mark.parametrize("mutation", ["column_gap", "row_overlap", "nonpositive"])
def test_source_native_table_authored_variable_grid_rejects_inconsistent_boundaries(mutation):
    from services.svg_source_native_tables import source_table_grid_correction
    manifest = _merged_source_table_manifest()
    root, _ = _authored_source_table_drawing(manifest, [40, 140, 390, 440], [120, 158, 217, 276])
    rects = [node for node in root[0] if node.tag.endswith("}rect")]
    if mutation == "column_gap":
        rects[-3].set("width", "100.00001")
    elif mutation == "row_overlap":
        rects[1].set("height", "37.99999")
    else:
        rects[-3].set("width", "0")
    diagnostic = source_table_grid_correction(manifest.units[0].blocks[0], root[0])
    assert diagnostic["problem"] == ("invalid_rectangle_geometry" if mutation == "nonpositive" else "inconsistent_grid_boundary")
    expected_error = "svg_contract_positive_required" if mutation == "nonpositive" else "svg_source_native_table_grid_mismatch"
    with pytest.raises(ValueError, match=expected_error):
        _bind_authored_source_table(root, manifest)


def test_source_native_table_authored_variable_grid_rejects_unproven_merged_boundary():
    from services.svg_source_native_tables import source_table_grid_correction
    manifest = manifest_for([("table", {"rows": [
        [{"text": "A", "gridSpan": 2}, {"text": "", "mergeCovered": True}],
        [{"text": "B", "gridSpan": 2}, {"text": "", "mergeCovered": True}]]})])
    root, _ = _authored_source_table_drawing(manifest, [40, 140, 390], [120, 158, 217])
    diagnostic = source_table_grid_correction(manifest.units[0].blocks[0], root[0])
    assert diagnostic["problem"] == "unproven_grid_boundary"
    assert diagnostic["axis"] == "x" and diagnostic["missing_boundary_indices"] == [1]
    with pytest.raises(ValueError, match="svg_source_native_table_grid_mismatch"):
        _bind_authored_source_table(root, manifest)


def test_source_native_table_preserves_merged_wrapped_and_blank_cells_and_rejects_paint_tamper():
    from services.svg_source_template_layout import resolve_source_plan
    from services.svg_source_native_tables import source_table_native_objects, unwrap_source_table_groups
    rows = [[{"text": "병합 제목", "gridSpan": 2, "sourceCellId": "header"},
             {"text": "", "hMerge": True, "mergeCovered": True}],
            [{"text": "첫 줄 & 원문\n둘째 줄", "sourceCellId": "wrapped"},
             {"text": "", "sourceCellId": "blank"}],
            [{"text": "매출", "sourceCellId": "label"}, {"text": "138", "sourceCellId": "value"}]]
    manifest = manifest_for([("table", {"rows": rows})])
    template = _v2_style()["source_template"]
    template.pop("layouts")
    template["composition_mode"] = "free-svg"
    raw = plan_for(manifest)
    raw.update(width=1280, height=720, template_digest=template["digest"], layout_id="free-svg")
    raw["placements"][0].update(x=64, y=120, width=1152, height=360,
        font_family="Noto Sans KR", font_weight="400", font_size=24,
        background=template["table"]["body_fill"], stroke=template["table"]["border"], fill=template["tokens"]["text"])
    plan, _ = resolve_source_plan(SvgLayoutPlan.model_validate(raw), manifest, 0, 12, template)
    result = assemble_source_svg(manifest, 0, plan, source_images={}, image_slots={},
        min_font_size_px=12, style_digest="sha256:style", template_snapshot=template)
    wire, summary = source_table_native_objects(result.svg_content, result.receipt, manifest, 0, pt_per_px=.75)
    assert summary["accepted"] == 1 and summary["source_cells_verified"]
    table = wire["objects"][0]
    assert [[cell["text"] for cell in row] for row in table["payload"]["rows"]] == [[cell["text"] for cell in row] for row in rows]
    assert table["payload"]["columnWidths"] == [576, 576]
    assert table["payload"]["rowHeightsPt"] == [90, 90, 90]
    assert table["payload"]["mergedCells"] == [dict(row=0, column=0, rowSpan=1, columnSpan=2)]
    assert len([binding for binding in result.receipt["bindings"] if binding["source_id"] == "wrapped"]) == 2
    unwrapped = ET.fromstring(unwrap_source_table_groups(result.svg_content, result.receipt))
    assert not any(node.get("id", "").startswith("source-native-table-") for node in unwrapped)
    root = ET.fromstring(result.svg_content)
    group = next(node for node in root if node.tag.endswith("}g"))
    group.set("transform", "translate(1 0)")
    tampered = ET.tostring(root, encoding="unicode")
    receipt = deepcopy(result.receipt)
    receipt["svg_digest"] = source_digest(tampered)
    with pytest.raises(ValueError, match="svg_source_native_table_group_invalid"):
        source_table_native_objects(tampered, receipt, manifest, 0, pt_per_px=.75)

    # Source-empty owners are invisible, but their exact identities remain mandatory.
    from services.generation_core.authoring.author_source_svg_layout import _whole_page_facts
    from services.svg_source_layout import bind_whole_page_source_svg, restore_source_empty_table_texts, page_digest
    authored_root, authored_fact = _authored_source_table_drawing(manifest,
        [64, 640, 1216], [120, 240, 360, 480])
    complete = ET.tostring(authored_root, encoding="unicode")
    facts = _whole_page_facts(manifest.units[0], {})
    blank_fact = next(cell for cell in authored_fact["cells"] if cell["text"] == "")
    blank_node = next(node for node in authored_root[0] if node.get("id") == blank_fact["element_id"])
    authored_root[0].remove(blank_node)
    missing = ET.tostring(authored_root, encoding="unicode")
    wire_digest = source_digest("the wire response includes its own envelope")
    normalization_args = dict(page=manifest.units[0], facts=facts,
        manifest_digest=manifest.manifest_digest, source_page_digest=page_digest(manifest, 0),
        raw_output_digest=wire_digest)
    binding_args = dict(facts=facts, width=1280, height=840,
        style_snapshot={"digest": "sha256:style", "source_authoring_contract": {"version": "svg-source-whole-page/v1"}},
        minimum_font_px=12, restore_source_lf=True, raw_output_digest=wire_digest)
    restored, restored_receipt = bind_whole_page_source_svg(missing, manifest, 0, **binding_args)
    restoration = restored_receipt["source_empty_table_text_restoration"]
    assert restoration["input_svg_digest"] == source_digest(missing)
    assert restoration["raw_output_digest"] == "sha256:" + wire_digest.removeprefix("sha256:")
    assert restoration["canonical_svg_digest"] == source_digest(restored)
    assert restoration["existing_tree_preserved"] and restoration["added_visible_characters"] == 0
    assert len(restoration["empty_texts"]) == 1 and restoration["empty_texts"][0]["exact_empty"]
    restored_root = ET.fromstring(restored)
    restored_node = next(node for node in restored_root[0] if node.get("id") == blank_fact["element_id"])
    assert not "".join(restored_node.itertext())
    restored_root[0].remove(restored_node)
    assert ET.tostring(restored_root, encoding="unicode") == missing
    complete_svg, complete_receipt = bind_whole_page_source_svg(complete, manifest, 0, **binding_args)
    restored_wire, restored_summary = source_table_native_objects(restored, restored_receipt, manifest, 0, pt_per_px=.75)
    complete_wire, complete_summary = source_table_native_objects(complete_svg, complete_receipt, manifest, 0, pt_per_px=.75)
    assert restored_wire == complete_wire and restored_summary == complete_summary
    # Nonempty text, including whitespace, is never an empty-placeholder exception.
    for source_text in ("source content", " ", "\t"):
        rejected_rows = deepcopy(rows)
        rejected_rows[1][1]["text"] = source_text
        rejected_manifest = manifest_for([("table", {"rows": rejected_rows})])
        rejected_root, rejected_fact = _authored_source_table_drawing(rejected_manifest,
            [64, 640, 1216], [120, 240, 360, 480])
        target = next(cell for cell in rejected_fact["cells"] if cell["row"] == 1 and cell["column"] == 1)
        rejected_root[0].remove(next(node for node in rejected_root[0] if node.get("id") == target["element_id"]))
        rejected_svg = ET.tostring(rejected_root, encoding="unicode")
        rejected_facts = _whole_page_facts(rejected_manifest.units[0], {})
        assert restore_source_empty_table_texts(rejected_svg, page=rejected_manifest.units[0],
            facts=rejected_facts, manifest_digest=rejected_manifest.manifest_digest,
            source_page_digest=page_digest(rejected_manifest, 0), raw_output_digest=wire_digest) == (rejected_svg, None)
        with pytest.raises(ValueError):
            bind_whole_page_source_svg(rejected_svg, rejected_manifest, 0,
                **{**binding_args, "facts": rejected_facts})
    duplicate_root = ET.fromstring(missing)
    duplicate_root[0].append(deepcopy(next(node for node in duplicate_root[0] if node.tag.endswith("}text"))))
    duplicate_svg = ET.tostring(duplicate_root, encoding="unicode")
    assert restore_source_empty_table_texts(duplicate_svg, **normalization_args) == (duplicate_svg, None)
    with pytest.raises(ValueError):
        bind_whole_page_source_svg(duplicate_svg, manifest, 0, **binding_args)
    bad_grid = ET.fromstring(missing)
    first_rect = next(node for node in bad_grid[0] if node.tag.endswith("}rect"))
    first_rect.set("width", str(float(first_rect.get("width")) + 1))
    bad_grid_svg = ET.tostring(bad_grid, encoding="unicode")
    assert restore_source_empty_table_texts(bad_grid_svg, **normalization_args) == (bad_grid_svg, None)
    with pytest.raises(ValueError):
        bind_whole_page_source_svg(bad_grid_svg, manifest, 0, **binding_args)


def test_source_token_wrap_preserves_numeric_latin_runs_and_legacy_snapshot_replay():
    import re
    from services.svg_source_template_layout import wrap_source_text
    from utils.svg_template_rules import reference_template_snapshot, _digest
    class Font:
        def getbbox(self, text):
            return (0, 0, len(text), 20)
        def getlength(self, text):
            return len(text)
    text = "Region North reached 120 units. Revenue 123,456.78% grows."
    old = wrap_source_text(text, 24, 20, Font())
    assert old[0] == 23 and text[:old[0]].endswith("12")
    snapshot = reference_template_snapshot({"style_id": "default", "digest": "sha256:style"}, 1280, 720)
    assert snapshot["text_wrap_policy"] == "latin-token/v1"
    snapshot_body = {key: value for key, value in snapshot.items() if key != "digest"}
    assert snapshot["digest"] == _digest(snapshot_body)
    assert snapshot["digest"] != _digest({key: value for key, value in snapshot_body.items() if key != "text_wrap_policy"})
    ends = wrap_source_text(text, 24, 20, Font(), policy=snapshot["text_wrap_policy"])
    assert ends[0] == 21
    tokens = list(re.finditer(r"[A-Za-z0-9]+(?:[.,:/+_-][A-Za-z0-9]+)*%?", text))
    assert all(not (token.start() < end < token.end()) for token in tokens for end in ends)
    starts = (0,) + ends[:-1]
    assert "".join(text[start:end] for start, end in zip(starts, ends, strict=True)) == text
    assert wrap_source_text(text, 24, 20, Font()) == old
    with pytest.raises(ValueError, match="svg_source_text_box_unfit"):
        wrap_source_text("123,456.78%", 4, 20, Font(), policy=snapshot["text_wrap_policy"])


def test_v2_public_model_schema_has_no_character_ranges_and_rejects_old_response():
    import json
    schema = json.dumps(SvgLayoutPlan.model_json_schema())
    assert "line_ends" not in schema and "cell_line_ends" not in schema
    manifest = manifest_for([("text", {"text": "8 chars!"})])
    plan = plan_for(manifest)
    plan["placements"][0]["line_ends"] = [9]
    with pytest.raises(ValueError, match="extra_forbidden"):
        SvgLayoutPlan.model_validate(plan)


@pytest.mark.parametrize("style_id", [None, "screenshot-blue"])
def test_template_server_path_resolves_or_defers_free_svg_without_provider(style_id):
    from services.svg_source_template_layout import try_template_source_layout
    manifest = manifest_for([("text", {"text": "원문 & <숫자> " * 30}),
        ("table", {"rows": [[{"text": "원본 셀"}, {"text": "42%"}]]}),
        ("image", {"contentDigest": "sha256:image", "geometry": {"cx": 200, "cy": 30}})])
    style, width, height = _v2_style(), 1123, 794
    if style_id:
        from services.generation_core.drivers.svg.svg_style_adapter import adapt_svg_style
        from utils.svg_template_rules import resolve_source_template_style
        width, height = 1188, 840
        style = adapt_svg_style(
            resolve_source_template_style(style_id),
            width=width, height=height, source_template=True,
        )
        manifest = manifest_for([("text", {"text": "업무 화면 안내", "dominantSizePt": 32}),
            ("text", {"text": "제공된 화면을 확인합니다.", "dominantSizePt": 10}),
            ("image", {"contentDigest": "sha256:image", "geometry": {"cx": 1600, "cy": 900}})])
    result = try_template_source_layout(manifest, 0, style, source_images={"source-slot-one": {"required":True}},
        image_slots={"p0:2":"source-slot-one"}, min_font_size_px=12, width=width, height=height,
        local_layout_budget_ms=10000)
    if style_id:
        assert result == dict(escalation_reason="layout_candidate_unavailable", model_layout_allowed=True,
            model_calls=0, composition_mode="free-svg", attempted_layout_ids=[])
        assert [block.kind for block in manifest.units[0].blocks] == ["text", "text", "image"]
        assert manifest.units[0].blocks[2].payload["contentDigest"] == "sha256:image"
        return
    assert result.receipt["model_calls"] == 0
    assert result.receipt["source_layout_version"] == "svg-source-layout/v2"
    preserved = result.receipt["source_preservation"]
    assert preserved["template_application"]["passed"]
    assert verify_source_receipt(result.svg_content, preserved, manifest, 0)


def test_template_ink_overlap_repairs_coordinates_but_empty_margins_may_overlap():
    from services.svg_source_template_layout import resolve_source_plan, repair_template_coordinates
    manifest = manifest_for([("text", {"text": "첫 번째"}), ("text", {"text": "두 번째"})])
    template = _v2_style()["source_template"]
    raw = plan_for(manifest)
    raw.update(width=1123,height=794,template_digest=template["digest"],layout_id="content-stack")
    for i,p in enumerate(raw["placements"]):
        p.update(x=40,y=200+40*i,height=100,font_weight="400",font_family="Noto Sans KR")
    plan, _ = resolve_source_plan(SvgLayoutPlan.model_validate(raw),manifest,0,12,template)
    result = assemble_source_svg(manifest,0,plan,source_images={},image_slots={},min_font_size_px=12,
        style_digest="sha256:style",template_snapshot=template)
    assert result.receipt["template_application"]["passed"]
    raw["placements"][1]["y"] = 204
    plan, _ = resolve_source_plan(SvgLayoutPlan.model_validate(raw),manifest,0,12,template)
    with pytest.raises(ValueError,match="boxes_overlap"):
        assemble_source_svg(manifest,0,plan,source_images={},image_slots={},min_font_size_px=12,
            style_digest="sha256:style",template_snapshot=template)
    repaired,count = repair_template_coordinates(plan,manifest,0,template)
    assert count == 1 and repaired.placements[1].y > 204
    assemble_source_svg(manifest,0,repaired,source_images={},image_slots={},min_font_size_px=12,
        style_digest="sha256:style",template_snapshot=template)


def test_v2_floor_rounds_up_and_noto_lines_use_actual_font_metrics():
    from services.svg_source_template_layout import resolve_source_plan
    from utils.svg_template_fonts import measure_font
    manifest = manifest_for([("text", {"text": "첫 번째 줄\n두 번째 줄"})])
    template = _v2_style()["source_template"]
    raw = plan_for(manifest)
    raw.update(width=1123,height=794,template_digest=template["digest"],layout_id="content-stack")
    raw["placements"][0].update(x=40,y=200,font_family="Noto Sans KR",font_weight="400",font_size=12.700222123)
    plan,_ = resolve_source_plan(SvgLayoutPlan.model_validate(raw),manifest,0,12.700222123,template)
    result = assemble_source_svg(manifest,0,plan,source_images={},image_slots={},min_font_size_px=12.700222123,
        style_digest="sha256:style",template_snapshot=template)
    nodes = [n for n in ET.fromstring(result.svg_content) if n.tag.endswith("}text")]
    assert all(float(n.get("font-size")) >= 12.700222123 for n in nodes)
    ascent,descent = measure_font("Noto Sans KR",400,20).getmetrics()
    assert float(nodes[1].get("y"))-float(nodes[0].get("y")) >= (ascent+descent)/20*12.71


def test_source_prominence_infers_role_without_changing_source_or_template_sizes():
    from services.svg_source_template_layout import page_roles,try_template_source_layout
    manifest = manifest_for([("text", {"text":"원본 제목", "dominantSizePt":32}),
        ("text", {"text":"원본 본문", "dominantSizePt":10}),
        ("text", {"text":"원본 보충", "dominantSizePt":10})])
    assert page_roles(manifest,0)["p0:0"] == "heading"
    result = try_template_source_layout(manifest,0,_v2_style(),source_images={},image_slots={},
        min_font_size_px=12,width=1123,height=794,local_layout_budget_ms=10000)
    classified = result.receipt["source_preservation"]["template_application"]["role_classification"]
    assert classified[0]["basis"] == "source_typography_prominence"
    assert manifest.units[0].blocks[0].payload["dominantSizePt"] == 32


def test_comparison_layout_requires_actual_parallel_source_groups_for_model_too():
    from services.svg_source_template_layout import validate_template_plan
    manifest = manifest_for([("text",{"text":"내용"})])
    template = _v2_style()["source_template"]
    template["layouts"][0]["applicability"] = dict(requires_source_parallel_groups=True,minimum_group_count=3)
    raw = plan_for(manifest)
    raw.update(template_digest=template["digest"],layout_id="content-stack")
    with pytest.raises(ValueError,match="template_layout_unknown"):
        validate_template_plan(SvgLayoutPlan.model_validate(raw),manifest,0,template)


def test_compact_spacing_applies_only_inside_same_proven_parallel_group():
    from services.svg_source_template_layout import parallel_source_groups,source_group_membership,required_content_gap
    blocks = []
    for column in range(3):
        x = column*300
        blocks.append(("shape",{"preset":"rect","geometry":dict(x=x,y=100,cx=250,cy=400)}))
        blocks.extend(("text",{"text":str(row),"geometry":dict(x=x+10,y=120+row*90,cx=200,cy=40)}) for row in range(3))
    manifest = manifest_for(blocks)
    groups = parallel_source_groups(manifest,0)
    assert len(groups) == 3 and all(len(g["member_ids"]) == 3 for g in groups)
    membership = source_group_membership(manifest,0,parallel_only=True)
    layout = dict(min_gap=12,intra_group_gap=3)
    assert required_content_gap("p0:1","p0:2",layout,membership) == 3
    assert required_content_gap("p0:1","p0:5",layout,membership) == 12


def test_straight_connector_preset_preserves_legacy_shape_role_rectangle():
    manifest = manifest_for([("text", {"text": "Title"}),
        ("shape", {"preset": "straightConnector1"})])
    result = assemble(manifest)
    root = ET.fromstring(result.svg_content)
    binding = next(item for item in result.receipt["bindings"]
        if item["source_id"] == "p0:1" and item["kind"] == "shape")
    shape = next((node for node in root.iter() if node.get("id") == binding["element_id"]), None)
    assert shape is not None and shape.get("id") == binding["element_id"]
    assert shape.tag == "{http://www.w3.org/2000/svg}rect"
    assert verify_source_receipt(result.svg_content, result.receipt, manifest, 0)


@pytest.fixture(scope="module")
def source_layout_baseline():
    """Opt-in differential probe against an explicitly selected local Git revision."""
    import os
    from pathlib import Path
    import subprocess
    import sys
    import types

    revision = os.environ.get("SVG_SOURCE_LAYOUT_BASELINE_REVISION")
    if not revision:
        pytest.skip("Set SVG_SOURCE_LAYOUT_BASELINE_REVISION for the local compatibility probe")
    repo = Path(__file__).resolve().parents[4]
    path = "servers/fastapi/services/svg_source_layout.py"
    source = subprocess.run(["git", "show", f"{revision}:{path}"], cwd=repo,
        capture_output=True, check=True).stdout
    name = "_svg_source_layout_compatibility_baseline"
    module = types.ModuleType(name)
    module.__file__ = str(repo / path)
    previous = sys.modules.get(name)
    sys.modules[name] = module
    try:
        exec(compile(source, f"{revision}:{path}", "exec"), module.__dict__)
        yield module
    finally:
        if previous is None:
            sys.modules.pop(name, None)
        else:
            sys.modules[name] = previous


@pytest.mark.parametrize("preset,role", [
    ("rect", "shape"), ("roundRect", "shape"), ("ellipse", "shape"),
    ("line", "shape"), ("straightConnector1", "shape"),
    ("parallelogram", "shape"), ("diamond", "shape"),
    ("line", "connector"), ("straightConnector1", "connector"), (None, "shape"),
])
def test_legacy_source_layout_matches_selected_baseline(source_layout_baseline, preset, role):
    import json

    payload = {"preset": preset}
    if preset is None:
        payload["customGeometry"] = dict(version="numeric-custom-path/v1", width=100, height=100,
            fill="norm", stroke=True, commands=[
                dict(command="moveTo", points=[[0, 0]]),
                dict(command="cubicBezTo", points=[[0, 100], [100, 100], [100, 0]]),
                dict(command="quadBezTo", points=[[50, 20], [0, 0]]),
                dict(command="close", points=[]),
            ])
    manifest = manifest_for([("text", {"text": "Title"}), ("shape", payload)])
    blocks = list(manifest.units[0].blocks)
    blocks[1] = blocks[1].model_copy(update={"semantic_role": role})
    unit = manifest.units[0].model_copy(update={"blocks": tuple(blocks)})
    manifest = manifest.model_copy(update={"units": (unit,)})
    data = manifest.model_dump(mode="json", by_alias=True)
    data.pop("manifestDigest")
    manifest = manifest.model_copy(update={"manifest_digest": digest_json(data)})
    plan = plan_for(manifest)
    previous = source_layout_baseline.assemble_source_svg(manifest, 0, deepcopy(plan),
        source_images={}, image_slots={}, min_font_size_px=12, style_digest="sha256:style")
    current = assemble(manifest, deepcopy(plan))
    assert current.svg_content.encode("utf-8") == previous.svg_content.encode("utf-8")
    def receipt_bytes(receipt):
        return json.dumps(receipt, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    assert receipt_bytes(current.receipt) == receipt_bytes(previous.receipt)
    assert source_layout_baseline.verify_source_receipt(previous.svg_content, previous.receipt, manifest, 0)
    assert verify_source_receipt(previous.svg_content, previous.receipt, manifest, 0)
