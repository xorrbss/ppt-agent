from copy import deepcopy
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


@pytest.mark.parametrize("kind,payload", [("chart", {}), ("shape", {"preset": "star5"}),
    ("image", {"crop": {"left": .1}}), ("image", {"geometry": {"rotationDegrees": 90}})])
def test_unsupported_content_fails_preflight(kind, payload):
    with pytest.raises(ValueError, match="unsupported"):
        source_pages(manifest_for([("text", {"text": "Title"}), (kind, payload)]))


def test_image_only_pages_rejected_before_authoring():
    with pytest.raises(ValueError, match="image_only_page"):
        source_pages(manifest_for([("image", {"contentDigest": "sha256:image"})]))


def test_parallelogram_preserves_shape_occurrence_as_static_polygon():
    manifest = manifest_for([("text", {"text": "Title"}), ("shape", {"preset": "parallelogram"})])
    result = assemble(manifest)
    assert "polygon" in result.svg_content
    assert len([b for b in result.receipt["bindings"] if b["kind"] == "shape"]) == 1


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
    from utils.llm_calls.author_source_svg_layout import _source_context, repair_source_plan
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
def test_template_server_path_resolves_korean_table_and_image_without_provider(style_id):
    from services.svg_source_template_layout import try_template_source_layout
    manifest = manifest_for([("text", {"text": "원문 & <숫자> " * 30}),
        ("table", {"rows": [[{"text": "원본 셀"}, {"text": "42%"}]]}),
        ("image", {"contentDigest": "sha256:image", "geometry": {"cx": 200, "cy": 30}})])
    style, width, height = _v2_style(), 1123, 794
    if style_id:
        from utils.svg_style_adapter import adapt_svg_style
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
    assert result.receipt["model_calls"] == 0
    assert result.receipt["source_layout_version"] == "svg-source-layout/v2"
    preserved = result.receipt["source_preservation"]
    assert preserved["template_application"]["passed"]
    assert verify_source_receipt(result.svg_content, preserved, manifest, 0)
    if style_id:
        root = ET.fromstring(result.svg_content)
        image = next(n for n in root.iter() if n.tag.endswith("}image"))
        assert image.get("href") == "source-slot-one"
        assert image.get("preserveAspectRatio") == "xMidYMid meet"
        assert float(image.get("width")) > width * .7
        assert float(image.get("height")) > height * .5


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
