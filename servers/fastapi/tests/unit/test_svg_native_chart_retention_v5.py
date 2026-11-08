"""Fresh SVG chart requirements prove drawn meaning before granting retention."""
from copy import deepcopy
import json
import math
from xml.etree import ElementTree as ET

import pytest

from services.generation_core.drivers.svg.native_chart_evidence import chart_drawing_evidence
from services.generation_core.drivers.svg.native_declarations import (
    bind_native_declarations, extract_native_declarations,
)
from services.generation_core.drivers.svg.native_publish import page_native_objects
from services.generation_core.drivers.svg.native_requirements import (
    REQUIREMENTS_VERSION as V2, REQUIREMENTS_VERSION_V3 as V3,
    REQUIREMENTS_VERSION_V4 as V4, REQUIREMENTS_VERSION_V5 as V5,
    apply_requested_native_quality, requested_native_data, requirements_revision,
)
from services.generation_core.quality.page_failure_kind import quality_keeps_content

NS = "{http://www.w3.org/2000/svg}"
SOURCE = "chart: Sales: Alpha 30, Beta 60, Gamma 90"


def _page(*, horizontal=False, appearance=True):
    root = ET.Element(NS + "svg", {"viewBox": "0 0 1280 720"})
    raw = {"chartType": "bar" if horizontal else "column",
           "categories": ["Alpha", "Beta", "Gamma"],
           "series": [{"name": "Sales", "values": [30, 60, 90]}], "title": "Sales trend"}
    if appearance:
        raw.update(categoryOrder="topToBottom" if horizontal else "leftToRight",
                   valueLabels={"shown": True, "position": "outsideEnd"}, markers=False)
    group = ET.SubElement(root, NS + "g", {"id": "native-chart-sales",
                         "data-native-chart": json.dumps(raw)})

    def text(value, x, y):
        node = ET.SubElement(group, NS + "text", {"x": str(x), "y": str(y),
                             "font-size": "16", "fill": "#222222", "text-anchor": "middle"})
        node.text = value

    text("Sales trend", 400, 120)
    text("Sales", 700, 150)
    for index, (category, value) in enumerate(zip(raw["categories"], [30, 60, 90], strict=True)):
        point = 250 + index * 150
        rect = {"x": "250", "y": str(point - 30), "width": str(value * 2), "height": "60"} if horizontal else {
            "x": str(point - 30), "y": str(450 - value * 2), "width": "60", "height": str(value * 2)}
        ET.SubElement(group, NS + "rect", {**rect, "fill": "#2563EB"})
        text(category, 170 if horizontal else point, point + 6 if horizontal else 480)
        text(str(value), 250 + value * 2 + 25 if horizontal else point,
             point + 6 if horizontal else 438 - value * 2)
    record = extract_native_declarations(root)
    svg = ET.tostring(root, encoding="unicode")
    page = {"svg_content": svg}
    bind_native_declarations(page, svg, record)
    return page


def _edit(page, edit):
    root = ET.fromstring(page["svg_content"])
    edit(root, page["nativeDeclarations"]["objects"][0]["raw"])
    page["svg_content"] = ET.tostring(root, encoding="unicode")
    bind_native_declarations(page, page["svg_content"], page["nativeDeclarations"])
    return page


def _text(root, value):
    return next(node for node in root.iter(NS + "text") if node.text == value)


def _quality(page, *, source=SOURCE, revision=V5):
    quality = {"passed": True, "failures": []}
    apply_requested_native_quality(quality, page, page["svg_content"], source,
                                   pt_per_px=.75, revision=revision)
    return quality


def _proof(page):
    evidence = chart_drawing_evidence(page, page["svg_content"], revision=V5).get("native-chart-sales", {})
    return evidence.get("contentGeometry", {"passed": False, "reason": "native_chart_drawing_unproven"})


def _capture_unproven_quality(page):
    quality = {"passed": True, "failures": [], "measurement_complete": True,
               "browser_metrics": {"width": 1280, "height": 720}}
    apply_requested_native_quality(quality, page, page["svg_content"], SOURCE, pt_per_px=.75, revision=V5)
    return quality


@pytest.mark.parametrize("horizontal", [False, True])
def test_correct_chart_proves_category_value_and_bar_geometry(horizontal):
    page = _page(horizontal=horizontal)
    before = deepcopy(page)
    assert _proof(page)["passed"]
    assert _quality(page)["passed"]
    assert page == before


def test_safe_editability_only_failure_keeps_correct_drawing():
    page = _page()
    def mixed_positions(root, raw):
        _text(root, "90").set("y", "300")
    _edit(page, mixed_positions)
    assert _proof(page)["passed"]
    quality = _quality(page)
    assert not quality["passed"]
    assert quality["requestedNativeObjects"]["projection"]["accepted"] == 0
    assert quality["failures"][0]["kind"] == "native_requested_chart_not_editable"
    assert quality_keeps_content(quality)


@pytest.mark.parametrize("appearance", [False, True])
def test_wrong_bar_height_never_earns_retention_even_with_every_correct_token(appearance):
    page = _page(appearance=appearance)
    def wrong_height(root, raw):
        bar = list(root.iter(NS + "rect"))[1]
        bar.set("height", "60")
        bar.set("y", "390")
        _text(root, "60").set("y", "378")
    _edit(page, wrong_height)
    if not appearance:
        assert page_native_objects(page, pt_per_px=.75, revision=V5)[1]["accepted"] == 1
    quality = _quality(page)
    assert quality["failures"][0]["kind"] == "native_requested_chart_missing"
    assert not quality_keeps_content(quality)
    assert _proof(page)["reason"] == "chart_content_bar_common_scale_or_baseline_mismatch"


@pytest.mark.parametrize("labels", [("Alpha", "Beta"), ("30", "60")])
def test_swapped_category_or_value_labels_do_not_borrow_correct_token_counts(labels):
    page = _page(appearance=False)
    def swap(root, raw):
        first, second = (_text(root, value) for value in labels)
        first.text, second.text = second.text, first.text
    _edit(page, swap)
    assert not quality_keeps_content(_quality(page))
    assert not _proof(page)["passed"]


def test_correct_values_piled_into_one_category_do_not_prove_association():
    page = _edit(_page(appearance=False), lambda root, raw: _text(root, "60").set("x", "250"))
    assert not quality_keeps_content(_quality(page))


@pytest.mark.parametrize(("attribute", "value"), [
    ("opacity", "0"), ("opacity", "1e-9"), ("fill", "none"),
    ("display", "none"), ("visibility", "hidden"), ("font-size", "0"),
    ("font-size", "1e-9"), ("textLength", "0"),
    ("fill", "#FFFFFF"),
    ("clip-path", "url(#clip)"), ("style", "opacity:1"),
    ("baseline-shift", "900"), ("dominant-baseline", "hanging"),
    ("alignment-baseline", "after-edge"), ("direction", "rtl"), ("unicode-bidi", "bidi-override"),
])
def test_hidden_or_unproved_value_visibility_is_strict(attribute, value):
    page = _edit(_page(), lambda root, raw: _text(root, "60").set(attribute, value))
    assert not quality_keeps_content(_quality(page))


@pytest.mark.parametrize("kind", ["later_cover", "internal_cover", "off_canvas", "stylesheet"])
def test_unproved_visibility_and_occlusion_cannot_authorize_retention(kind):
    def obscure(root, raw):
        if kind == "off_canvas":
            root.set("viewBox", "0 0 200 200")
        elif kind == "stylesheet":
            ET.SubElement(root, NS + "style").text = "text { opacity: 0 }"
        else:
            parent = root if kind == "later_cover" else root[0]
            ET.SubElement(parent, NS + "rect", {"x": "100", "y": "100", "width": "800",
                                                "height": "500", "fill": "#FFFFFF"})
    page = _edit(_page(), obscure)
    assert not quality_keeps_content(_quality(page))


def test_value_painted_before_its_bar_is_not_visible_proof():
    def obscure(root, raw):
        label = _text(root, "90")
        label.set("y", "300")
        root[0].remove(label)
        root[0].insert(0, label)
    page = _edit(_page(), obscure)
    assert not _proof(page)["passed"]
    assert not quality_keeps_content(_quality(page))


def test_bars_matching_the_preceding_page_background_do_not_prove_geometry():
    def obscure(root, raw):
        root.insert(0, ET.Element(NS + "rect", {"x": "0", "y": "0", "width": "1280",
                                               "height": "720", "fill": "#2563EB"}))
    page = _edit(_page(), obscure)
    assert not _proof(page)["passed"]
    assert not quality_keeps_content(_quality(page))


@pytest.mark.parametrize("dimension", ["width", "height"])
def test_negative_raw_rect_dimensions_do_not_borrow_normalized_primitive_boxes(dimension):
    def invalid_rect(root, raw):
        rect = next(root.iter(NS + "rect"))
        position = "x" if dimension == "width" else "y"
        rect.set(position, str(float(rect.get(position)) + float(rect.get(dimension))))
        rect.set(dimension, "-" + rect.get(dimension))
    page = _edit(_page(appearance=False), invalid_rect)
    assert not _proof(page)["passed"]
    assert not quality_keeps_content(_quality(page))


def test_repositioned_tspan_does_not_borrow_parent_text_coordinates():
    def move(root, raw):
        label = _text(root, "60")
        label.text = None
        ET.SubElement(label, NS + "tspan", {"x": "900"}).text = "60"
    page = _edit(_page(appearance=False), move)
    assert not _proof(page)["passed"]
    assert not quality_keeps_content(_quality(page))


@pytest.mark.parametrize("mode", ["negative_background", "stroked_background"])
def test_invalid_rects_or_actual_obscuring_strokes_cannot_prove_content(mode):
    def change(root, raw):
        if mode == "rounded_bar":
            next(root.iter(NS + "rect")).set("rx", "30")
            return
        background = {"x": "0", "y": "0", "width": "1280", "height": "720", "fill": "#000000"}
        if mode == "negative_background":
            background.update(x="1280", width="-1280")
        elif mode == "rounded_background":
            background.update(rx="640", ry="360")
        else:
            background.update(stroke="#FFFFFF", **{"stroke-width": "2000"})
        root.insert(0, ET.Element(NS + "rect", background))
        for node in root.iter(NS + "text"):
            node.set("fill", "#FFFFFF")
    page = _edit(_page(appearance=False), change)
    assert not _proof(page)["passed"]
    assert not quality_keeps_content(_quality(page))


def test_nested_svg_viewport_cannot_borrow_unclipped_canvas_coordinates():
    def nest(root, raw):
        group = root[0]
        root.remove(group)
        viewport = ET.SubElement(root, NS + "svg", {"x": "1000", "y": "0", "width": "1", "height": "1"})
        viewport.append(group)
    assert not quality_keeps_content(_quality(_edit(_page(), nest)))


@pytest.mark.parametrize("mode", ["tspan", "wide_stroke", "css_translate"])
def test_later_paint_requires_capture_evidence_before_retention(mode):
    def cover(root, raw):
        if mode == "wide_stroke":
            ET.SubElement(root, NS + "line", {"x1": "1000", "x2": "1000", "y1": "0", "y2": "700",
                                             "stroke": "#FFFFFF", "stroke-width": "2000"})
        elif mode == "css_translate":
            ET.SubElement(root, NS + "rect", {"x": "1200", "y": "0", "width": "70", "height": "700",
                                             "fill": "#FFFFFF", "style": "transform:translateX(-1000px)"})
        else:
            text = ET.SubElement(root, NS + "text", {"x": "1000", "y": "318", "font-size": "16",
                                                       "text-anchor": "middle", "fill": "#FFFFFF"})
            ET.SubElement(text, NS + "tspan", {"x": "400"}).text = "60"
    page = _edit(_page(), cover)
    if mode == "tspan":
        assert _proof(page)["passed"]  # Correct data geometry is not final paint visibility.
    else:
        assert not _proof(page)["passed"]
    assert not quality_keeps_content(_capture_unproven_quality(page))


def test_overlapping_earlier_text_is_not_assumed_to_be_a_white_backdrop():
    def obscure(root, raw):
        cover = ET.Element(NS + "text", {"x": "400", "y": "340", "font-size": "100",
                                         "text-anchor": "middle", "fill": "#222222"})
        cover.text = "████"
        root.insert(0, cover)
    page = _edit(_page(), obscure)
    assert _proof(page)["passed"]
    assert _quality(page)["requestedNativeObjects"]["contentRetentionProven"] is False
    assert not quality_keeps_content(_capture_unproven_quality(page))


@pytest.mark.parametrize("mode", ["missing", "extra", "duplicate", "extra_rect", "wrong_unit"])
def test_value_occurrences_extras_and_units_remain_strict(mode):
    def change(root, raw):
        if mode == "missing":
            root[0].remove(_text(root, "60"))
        elif mode == "extra_rect":
            rect = deepcopy(next(root.iter(NS + "rect")))
            rect.set("x", "800")
            root[0].append(rect)
        elif mode == "wrong_unit":
            _text(root, "60").text = "60kg"
        else:
            label = deepcopy(_text(root, "60"))
            label.set("x", "800")
            label.text = "77" if mode == "extra" else "60"
            root[0].append(label)
    assert not quality_keeps_content(_quality(_edit(_page(), change)))


def test_repeated_values_need_an_occurrence_at_each_category():
    def repeated(root, raw):
        raw["series"][0]["values"][1] = 30
        rect = list(root.iter(NS + "rect"))[1]
        rect.set("height", "60")
        rect.set("y", "390")
        _text(root, "60").text = "30"
        labels = [node for node in root.iter(NS + "text") if node.text == "30"]
        labels[1].set("y", "378")
    page = _edit(_page(), repeated)
    source = SOURCE.replace("Beta 60", "Beta 30")
    assert _quality(page, source=source)["passed"]
    def remove(root, raw):
        root[0].remove([node for node in root.iter(NS + "text") if node.text == "30"][1])
    _edit(page, remove)
    assert not quality_keeps_content(_quality(page, source=source))


@pytest.mark.parametrize("chart_type", ["pie", "donut", "line", "area"])
def test_bar_geometry_cannot_impersonate_another_chart_family(chart_type):
    page = _edit(_page(appearance=False), lambda root, raw: raw.update(chartType=chart_type))
    assert page_native_objects(page, pt_per_px=.75, revision=V5)[1]["accepted"] == 1
    assert not quality_keeps_content(_quality(page))
    assert not _proof(page)["passed"]


def test_multiple_series_fail_closed_even_when_tokens_are_complete():
    def multiseries(root, raw):
        raw["series"].append({"name": "Costs", "values": [10, 20, 30]})
    assert not quality_keeps_content(_quality(_edit(_page(), multiseries)))


def test_repeated_categories_require_each_positioned_occurrence():
    def repeated(root, raw):
        raw["categories"][1] = "Alpha"
        _text(root, "Beta").text = "Alpha"
    page = _edit(_page(), repeated)
    source = SOURCE.replace("Beta", "Alpha")
    assert _proof(page)["passed"]
    def remove(root, raw):
        root[0].remove([node for node in root.iter(NS + "text") if node.text == "Alpha"][1])
    _edit(page, remove)
    assert not quality_keeps_content(_quality(page, source=source))


def test_units_need_the_required_title_and_compatible_value_units():
    def with_units(root, raw):
        raw["title"] = "Sales trend (kg)"
        _text(root, "Sales trend").text = raw["title"]
        for value in ("30", "60", "90"):
            _text(root, value).text = value + "kg"
    page = _edit(_page(), with_units)
    source = SOURCE + ". unit: kg"
    assert _quality(page, source=source)["passed"]
    _edit(page, lambda root, raw: setattr(_text(root, "60kg"), "text", "60g"))
    assert not quality_keeps_content(_quality(page, source=source))


def test_stale_declarations_and_independent_content_failures_remain_strict():
    page = _page()
    page["nativeDeclarations"]["svgDigest"] = "stale"
    assert not quality_keeps_content(_quality(page))
    quality = _quality(_page())
    quality["failures"].append({"kind": "required_text_missing"})
    quality["passed"] = False
    assert not quality_keeps_content(quality)


def test_v5_requirements_keep_literal_data_and_old_revision_derivation():
    assert requirements_revision({}) == V2
    previous = requested_native_data(SOURCE, V4)
    current = requested_native_data(SOURCE, V5)
    assert current == {**previous, "version": V5}
    assert requirements_revision({"native_requirements_revision": V4}) == V4


def test_old_revision_quality_and_evidence_shapes_remain_unchanged():
    page = _page()
    def wrong_height(root, raw):
        bar = list(root.iter(NS + "rect"))[1]
        bar.set("height", "60")
        bar.set("y", "390")
        _text(root, "60").set("y", "378")
    _edit(page, wrong_height)
    v4 = _quality(page, revision=V4)
    assert v4["failures"][0]["kind"] == "native_requested_chart_not_editable"
    assert quality_keeps_content(v4)
    proof = chart_drawing_evidence(page, page["svg_content"], revision=V4)["native-chart-sales"]
    assert set(proof) == {"chart", "missingLabels", "valueTexts"}
    legacy = _quality(page, revision=None)
    assert legacy == _quality(page, revision=V2)
    v3 = _quality(page, revision=V3)
    assert {**legacy["requestedNativeObjects"], "version": V3} == v3["requestedNativeObjects"]
    assert legacy["failures"] == v3["failures"]
    assert not quality_keeps_content(legacy)


def _title_only_page():
    return _edit(_page(), lambda root, raw: root[0].remove(_text(root, "Sales")))


def test_v5_publication_inherits_v4_single_series_title_projection():
    from services.generation_core.drivers.svg.publish import svg_slide_content

    page = _title_only_page()
    before = deepcopy(page)
    kwargs = {"role": "content", "source": SOURCE, "pt_per_px": .75}
    current = svg_slide_content(page, revision=V5, **kwargs)
    # v5 alone keeps the bound chart declarations for post-publication paint
    # verification; the projection itself is v4's.
    from services.generation_core.drivers.svg.chart_paint_verification import WITNESS_SOURCE_KEY
    assert current.pop(WITNESS_SOURCE_KEY)["declarations"] == page["nativeDeclarations"]
    assert current == svg_slide_content(page, revision=V4, **kwargs)
    assert current["svgReceipt"]["nativeObjects"]["accepted"] == 1
    assert current["nativeObjects"]["objects"][0]["payload"]["series"][0]["values"] == [30, 60, 90]
    assert _quality(page)["passed"]
    assert page == before
    for revision in (None, V2, V3):
        legacy = svg_slide_content(page, revision=revision, **kwargs)
        assert "nativeObjects" not in legacy
        assert legacy["svgReceipt"]["nativeObjects"]["accepted"] == 0


@pytest.mark.parametrize("wrong_geometry", [False, True])
def test_v5_remake_proves_content_before_returning_native_projection(wrong_geometry):
    from services.authored_canvas import canvas_metadata_for_aspect_ratio
    from services.svg_page_remake import _native_candidate, _native_projection

    page = _title_only_page()
    if wrong_geometry:
        def distort(root, raw):
            raw.pop("categoryOrder")
            raw.pop("valueLabels")
            raw.pop("markers")
            bar = list(root.iter(NS + "rect"))[1]
            bar.set("height", "60")
            bar.set("y", "390")
            _text(root, "60").set("y", "378")
        _edit(page, distort)
        assert page_native_objects(page, pt_per_px=.75, revision=V5)[1]["accepted"] == 1
    svg = page["svg_content"]
    native = _native_candidate(svg, svg, page["nativeDeclarations"])
    metadata = canvas_metadata_for_aspect_ratio("16:9")
    if wrong_geometry:
        with pytest.raises(ValueError, match="^svg_page_remake_requested_native_missing$"):
            _native_projection(svg, native, metadata, SOURCE, revision=V5)
    else:
        wire, summary = _native_projection(svg, native, metadata, SOURCE, revision=V5)
        assert summary["accepted"] == 1
        assert wire["objects"][0]["payload"]["series"][0]["values"] == [30, 60, 90]


@pytest.mark.parametrize("source_grounded", [False, True])
def test_v5_standard_author_prompt_adds_only_v5_integrity_guidance(source_grounded):
    from services.generation_core.authoring import author_slide_svg as author
    from tests.unit.test_svg_native_chart_guidance_v4 import _snapshot

    kwargs = {"source_grounded": source_grounded}
    from services.generation_core.drivers.svg.native_declarations import NATIVE_CHART_GUIDANCE_V5
    current = author.svg_author_rules(_snapshot(V5, **kwargs), 18)
    assert current.count(NATIVE_CHART_GUIDANCE_V5) == 1
    assert current.replace(NATIVE_CHART_GUIDANCE_V5, "") == author.svg_author_rules(
        _snapshot(V4, **kwargs), 18)


@pytest.mark.parametrize("header_applied", [False, True])
def test_v5_layered_author_prompt_adds_only_v5_integrity_guidance(header_applied):
    from services.generation_core.authoring import author_slide_svg as author
    from tests.unit.test_svg_native_chart_guidance_v4 import LEGACY_NATIVE_RULES, _snapshot

    from services.generation_core.drivers.svg.native_declarations import NATIVE_CHART_GUIDANCE_V5
    for factory, _digest in LEGACY_NATIVE_RULES:
        assert factory(requirements_revision=V5).replace(NATIVE_CHART_GUIDANCE_V5, "") == factory(requirements_revision=V4)
    kwargs = {"language": "Korean", "header_applied": header_applied}
    current = author._layered_author_rules(_snapshot(V5, layered=True), "{}", 18, **kwargs)
    assert current.count(NATIVE_CHART_GUIDANCE_V5) == 1
    assert current.replace(NATIVE_CHART_GUIDANCE_V5, "") == author._layered_author_rules(
        _snapshot(V4, layered=True), "{}", 18, **kwargs)


def test_v5_guidance_requires_family_and_data_fidelity_without_retention_shortcuts():
    from services.generation_core.drivers.svg.native_declarations import (
        NATIVE_CHART_GUIDANCE_V5, _native_chart_rules,
    )
    assert NATIVE_CHART_GUIDANCE_V5 in _native_chart_rules(V5)
    for old_revision in (None, V2, V3, V4):
        assert NATIVE_CHART_GUIDANCE_V5 not in _native_chart_rules(old_revision)
    for obligation in ("requested chart family", "signed or zero values and units",
                       "correct numeric text alone", "do not convert", "invent data",
                       "captured-paint evidence", "does not establish native editability or release readiness"):
        assert obligation in NATIVE_CHART_GUIDANCE_V5


@pytest.mark.parametrize("prose", [
    "단위: kg\nAlpha 30, Beta 60\nGamma 90",
    "Alpha 30kg, Beta 60kg, Gamma 90kg",
])
def test_v5_prose_derivation_inherits_v4_written_values_and_unit_context(prose):
    from services.generation_core.drivers.svg.prose_chart_literal import derive_chart_literals

    current = derive_chart_literals(prose, revision=V5)
    assert current == derive_chart_literals(prose, revision=V4)
    assert len(current) == 1
    assert current[0].values == ("30", "60", "90")
    assert current[0].unit == "kg"
    if prose.startswith("단위"):
        assert derive_chart_literals(prose, revision=V3) == []


def _two_charts(*, unsafe_first=True):
    first, second = _page(appearance=False), _page(appearance=False)
    root = ET.fromstring(first["svg_content"])
    root.set("viewBox", "0 0 2560 720")
    group = ET.fromstring(second["svg_content"])[0]
    group.set("id", "native-chart-second")
    for node in group.iter():
        if "x" in node.attrib:
            node.set("x", str(float(node.get("x")) + 1280))
    root.append(group)
    bad = root[0] if unsafe_first else root[1]
    bar = list(bad.iter(NS + "rect"))[1]
    bar.set("height", "60")
    bar.set("y", "390")
    _text(bad, "60").set("y", "378")
    record = deepcopy(first["nativeDeclarations"])
    declaration = deepcopy(second["nativeDeclarations"]["objects"][0])
    declaration["id"] = "native-chart-second"
    record["objects"].append(declaration)
    svg = ET.tostring(root, encoding="unicode")
    page = {"svg_content": svg}
    bind_native_declarations(page, svg, record)
    return page


@pytest.mark.parametrize("unsafe_first", [False, True])
def test_safe_duplicate_cannot_mask_an_unsafe_accepted_chart(unsafe_first):
    page = _two_charts(unsafe_first=unsafe_first)
    assert page_native_objects(page, pt_per_px=.75, revision=V5)[1]["accepted"] == 2
    quality = _quality(page)
    bad_id = "native-chart-sales" if unsafe_first else "native-chart-second"
    assert any(failure.get("declarationId") == bad_id
               and failure["kind"] == "native_requested_chart_missing" for failure in quality["failures"])
    assert not quality_keeps_content(quality)


@pytest.mark.parametrize("mode", ["safe", "distorted", "stale", "dropped"])
def test_v5_self_declared_charts_cannot_bypass_quality_without_a_literal(mode):
    page = _page(appearance=False)
    if mode == "distorted":
        page = _two_charts()
    elif mode == "stale":
        page["nativeDeclarations"]["svgDigest"] = "stale"
    elif mode == "dropped":
        record = page["nativeDeclarations"]
        record["objects"] = []
        record["dropped"] = [{"id": "native-chart-sales", "kind": "chart",
                               "reason": "native_declaration_json_invalid"}]
    quality = _quality(page, source="A synthetic chart described without a literal.")
    assert quality["passed"] is (mode == "safe")
    if mode != "safe":
        assert not quality_keeps_content(quality)


def test_v5_pages_without_chart_declarations_or_literals_keep_existing_quality():
    page = {"svg_content": '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1280 720" />'}
    quality = {"passed": True, "failures": []}
    before = deepcopy(quality)
    apply_requested_native_quality(quality, page, page["svg_content"], "Synthetic prose.", pt_per_px=.75, revision=V5)
    assert quality == before


@pytest.mark.parametrize("source", [
    "A synthetic chart described without a literal.",
    "chart: Alpha 30, Beta 60, Gamma 90",
])
def test_chart_cannot_choose_between_conflicting_title_and_series_units(source):
    def conflicting_units(root, raw):
        raw["title"] = "Sales (kg)"
        raw["series"][0]["name"] = "Sales (m)"
        _text(root, "Sales trend").text = raw["title"]
        _text(root, "Sales").text = raw["series"][0]["name"]
    page = _edit(_page(), conflicting_units)
    assert _proof(page)["passed"]
    quality = _quality(page, source=source)
    assert not quality["passed"]
    assert not quality_keeps_content(quality)


@pytest.mark.parametrize("location", ["title", "series"])
def test_explicit_unitless_data_cannot_gain_a_recognized_declared_unit(location):
    def add_unit(root, raw):
        if location == "title":
            raw["title"] = "Sales (kg)"
            _text(root, "Sales trend").text = raw["title"]
        else:
            raw["series"][0]["name"] = "Sales (kg)"
            _text(root, "Sales").text = raw["series"][0]["name"]
    page = _edit(_page(), add_unit)
    assert _proof(page)["passed"]
    assert page_native_objects(page, pt_per_px=.75, revision=V5)[1]["accepted"] == 1
    quality = _quality(page, source="chart: Alpha 30, Beta 60, Gamma 90")
    assert not quality["passed"]
    assert not quality_keeps_content(quality)


def _polar_page(kind="pie", values=(30, 60, 90), *, shown=True, hole=.7, overlay=False, single_circle=False):
    root = ET.Element(NS + "svg", {"viewBox": "0 0 1280 720"})
    categories = ["Alpha", "Beta", "Gamma"][:len(values)]
    raw = {"chartType": kind, "categories": categories,
           "series": [{"name": "Sales", "values": list(values)}], "title": "Sales trend",
           "categoryOrder": "clockwise", "valueLabels": {"shown": shown, "position": "center" if shown else None},
           "markers": False}
    group = ET.SubElement(root, NS + "g", {"id": "native-chart-sales", "data-native-chart": json.dumps(raw)})
    def text(value, x, y):
        node = ET.SubElement(group, NS + "text", {"x": str(x), "y": str(y), "font-size": "16",
                             "text-anchor": "middle", "fill": "#222222"})
        node.text = value
    text("Sales trend", 400, 100)
    text("Sales", 900, 100)
    cx, cy, radius, angle = 450, 370, 160, -math.pi / 2
    total = sum(values)
    for index, (category, value) in enumerate(zip(categories, values, strict=True)):
        sweep = 2 * math.pi * value / total if total else 0
        middle = angle + sweep / 2
        if value > 0:
            if single_circle:
                attributes = {"cx": str(cx), "cy": str(cy), "r": str(radius), "fill": "#2563EB"}
                if kind == "donut":
                    attributes.update(fill="none", stroke="#2563EB", **{"stroke-width": "40"})
                ET.SubElement(group, NS + "circle", attributes)
            else:
                steps = max(2, math.ceil(sweep / .06))
                outer = [(cx + radius * math.cos(angle + sweep * step / steps),
                          cy + radius * math.sin(angle + sweep * step / steps)) for step in range(steps + 1)]
                inner = ([(cx + radius * hole * math.cos(angle + sweep * step / steps),
                           cy + radius * hole * math.sin(angle + sweep * step / steps)) for step in reversed(range(steps + 1))]
                         if kind == "donut" and not overlay else [(cx, cy)])
                ET.SubElement(group, NS + "polygon", {"points": " ".join(f"{x:.5f},{y:.5f}" for x, y in [*outer, *inner]),
                              "fill": ["#2563EB", "#DC2626", "#15803D"][index]})
        label_angle = middle if value else angle
        text(category, cx + (radius + 55) * math.cos(label_angle), cy + (radius + 55) * math.sin(label_angle) + 6)
        if shown:
            point_radius = radius * ((1 + hole) / 2 if kind == "donut" else .62)
            text(str(value), cx + point_radius * math.cos(label_angle), cy + point_radius * math.sin(label_angle) + 6)
        angle += sweep
    if overlay:
        ET.SubElement(group, NS + "circle", {"cx": str(cx), "cy": str(cy), "r": str(radius * hole), "fill": "#FFFFFF"})
    record = extract_native_declarations(root)
    svg = ET.tostring(root, encoding="unicode")
    page = {"svg_content": svg}
    bind_native_declarations(page, svg, record)
    return page


@pytest.mark.parametrize("kind", ["pie", "donut"])
@pytest.mark.parametrize("shown", [False, True])
def test_full_polar_contract_proves_real_partition_with_optional_labels(kind, shown):
    page = _polar_page(kind, shown=shown)
    quality = _quality(page)
    assert quality_keeps_content(quality), quality
    assert _proof(page)["passed"]


@pytest.mark.parametrize("kind", ["pie", "donut"])
@pytest.mark.parametrize("single_circle", [False, True])
def test_single_category_polar_chart_is_a_complete_partition(kind, single_circle):
    page = _polar_page(kind, values=(100,), shown=False, single_circle=single_circle)
    assert _proof(page)["passed"]
    assert quality_keeps_content(_quality(page, source="chart: Sales: Alpha 100"))


@pytest.mark.parametrize("kind", ["pie", "donut"])
def test_zero_polar_category_owns_no_invented_wedge(kind):
    page = _polar_page(kind, values=(0, 60, 90), shown=False)
    assert _proof(page)["passed"]
    assert quality_keeps_content(_quality(page, source="chart: Sales: Alpha 0, Beta 60, Gamma 90"))


def test_donut_center_overlay_preserves_the_same_data_partition():
    page = _polar_page("donut", overlay=True, shown=False)
    assert _proof(page)["passed"]
    assert quality_keeps_content(_quality(page))


def test_single_category_donut_accepts_a_filled_outer_circle_and_proved_center_hole():
    def circles(root, raw):
        group = root[0]
        for polygon in list(group.iter(NS + "polygon")):
            group.remove(polygon)
        ET.SubElement(group, NS + "circle", {"cx": "450", "cy": "370", "r": "160", "fill": "#2563EB"})
        ET.SubElement(group, NS + "circle", {"cx": "450", "cy": "370", "r": "112", "fill": "#FFFFFF"})
    page = _edit(_polar_page("donut", values=(100,), shown=False), circles)
    assert _proof(page)["passed"]
    assert _proof(page)["holeRatio"] == .7


def test_disconnected_arc_caps_do_not_impersonate_a_full_singleton_pie():
    def caps(root, raw):
        polygon = next(root.iter(NS + "polygon"))
        paths = []
        for quadrant in range(4):
            arc = [(450 + 160 * math.cos(quadrant * math.pi / 2 + math.pi * i / 100),
                    370 + 160 * math.sin(quadrant * math.pi / 2 + math.pi * i / 100)) for i in range(51)]
            paths.append("M " + " L ".join(f"{x},{y}" for x, y in arc) + " Z")
        polygon.tag = NS + "path"
        polygon.attrib.pop("points")
        polygon.set("d", " ".join(paths))
    page = _edit(_polar_page(values=(100,), shown=False), caps)
    assert not _proof(page)["passed"]


@pytest.mark.parametrize("mutation", ["wrong_share", "reassigned_categories", "different_radius", "overlap"])
def test_polar_numeric_tokens_never_authorize_misleading_slices(mutation):
    def change(root, raw):
        if mutation == "wrong_share":
            raw["series"][0]["values"] = [60, 30, 90]
        elif mutation == "reassigned_categories":
            a, b = _text(root, "Alpha"), _text(root, "Beta")
            a.text, b.text = b.text, a.text
        else:
            polygons = list(root.iter(NS + "polygon"))
            if mutation == "overlap":
                polygons[1].set("points", polygons[0].get("points"))
            else:
                values = [float(value) for value in polygons[1].get("points").replace(",", " ").split()]
                polygons[1].set("points", " ".join(f"{450 + (x - 450) * .7},{370 + (y - 370) * .7}"
                    for x, y in zip(values[::2], values[1::2], strict=True)))
    page = _edit(_polar_page(shown=False), change)
    assert not _proof(page)["passed"]
    assert not quality_keeps_content(_quality(page))


@pytest.mark.parametrize("kind", ["pie", "donut"])
@pytest.mark.parametrize("values", [(-1, 60, 90), (0, 0, 0)])
def test_polar_mathematically_invalid_values_are_not_reinterpreted(kind, values):
    from services.generation_core.drivers.svg.chart_content_polar import prove_polar
    from services.generation_core.drivers.svg.native_chart import _model_chart
    from services.generation_core.drivers.svg.native_geometry import collect_primitives, find_group
    page = _polar_page(kind, shown=False)
    raw = deepcopy(page["nativeDeclarations"]["objects"][0]["raw"])
    raw["series"][0]["values"] = list(values)
    root = ET.fromstring(page["svg_content"])
    from services.generation_core.drivers.svg.native_chart import CHART_ELEMENTS
    prims = collect_primitives(*find_group(root, "native-chart-sales"), CHART_ELEMENTS)
    assert not prove_polar(raw, _model_chart(raw), prims)["passed"]


@pytest.mark.parametrize("style", ["rounded", "opacity", "stroke", "text_outline", "translate", "scale", "tspan", "spacing"])
def test_supported_chart_styles_do_not_become_content_failures(style):
    def style_chart(root, raw):
        group = root[0]
        if style == "rounded":
            for rect in group.iter(NS + "rect"):
                rect.set("rx", "8")
                rect.set("ry", "8")
        elif style == "opacity":
            for rect in group.iter(NS + "rect"):
                rect.set("fill-opacity", ".6")
        elif style == "stroke":
            for rect in group.iter(NS + "rect"):
                rect.set("stroke", "#111111")
                rect.set("stroke-width", "2")
                rect.set("stroke-linejoin", "round")
        elif style == "text_outline":
            _text(root, "60").set("stroke", "#FFFFFF")
            _text(root, "60").set("stroke-width", "1")
        elif style in {"translate", "scale"}:
            group.set("transform", "translate(25,10)" if style == "translate" else "matrix(1.1,0,0,1.1,5,5)")
        elif style == "tspan":
            label = _text(root, "60")
            label.text = None
            ET.SubElement(label, NS + "tspan").text = "6"
            ET.SubElement(label, NS + "tspan").text = "0"
        else:
            _text(root, "60").set("letter-spacing", "1")
    page = _edit(_page(appearance=False), style_chart)
    assert _proof(page)["passed"]
    assert quality_keeps_content(_quality(page))


def _cartesian_page(kind, values, *, labels=None, grouping=None):
    from tests.unit.test_svg_chart_content_cartesian import _fixture
    raw, group = _fixture(kind, values, grouping=grouping, appearance=True)
    if labels is not None:
        raw["valueLabels"] = {"shown": True, "position": labels}
        for row in values:
            for category, value in enumerate(row):
                y = 350 - value * 4
                text = ET.SubElement(group, "text", {"x": str(200 + category * 200),
                    "y": str(y + (6 if labels == "center" else -12)), "font-size": "16",
                    "fill": "#222222", "text-anchor": "middle"})
                text.text = str(value)
    group.set("id", "native-chart-sales")
    group.set("data-native-chart", json.dumps(raw))
    for node in group.iter():
        node.tag = NS + node.tag
    root = ET.Element(NS + "svg", {"viewBox": "0 0 1280 720"})
    root.append(group)
    declarations = extract_native_declarations(root)
    svg = ET.tostring(root, encoding="unicode")
    page = {"svg_content": svg}
    bind_native_declarations(page, svg, declarations)
    return page


@pytest.mark.parametrize("kind", ["bar", "column", "line", "area"])
def test_common_v5_proof_preserves_signed_zero_multiseries_and_actual_names(kind):
    page = _cartesian_page(kind, [[-10, 0, 20], [10, -5, 30]])
    assert _proof(page)["passed"]
    assert quality_keeps_content(_quality(page, source=""))


@pytest.mark.parametrize("kind", ["line", "area"])
@pytest.mark.parametrize("position", ["above", "center"])
def test_multiseries_value_labels_bind_before_numeric_matching(kind, position):
    page = _cartesian_page(kind, [[10, 20, 30], [30, 40, 50]], labels=position)
    assert _proof(page)["passed"]
    root = ET.fromstring(page["svg_content"])
    labels = [node for node in root.iter(NS + "text") if node.text in {"10", "20", "30", "40", "50"}]
    labels[0].text, labels[3].text = labels[3].text, labels[0].text
    page["svg_content"] = ET.tostring(root, encoding="unicode")
    bind_native_declarations(page, page["svg_content"], page["nativeDeclarations"])
    assert not _proof(page)["passed"]
    assert not quality_keeps_content(_quality(page, source=""))


def test_geometry_pattern_cannot_assign_floating_multiseries_names():
    page = _cartesian_page("line", [[10, 20, 30], [30, 40, 50]])
    root = ET.fromstring(page["svg_content"])
    group = root[0]
    for rect in list(group.iter(NS + "rect")):
        group.remove(rect)
    page["svg_content"] = ET.tostring(root, encoding="unicode")
    bind_native_declarations(page, page["svg_content"], page["nativeDeclarations"])
    assert not _proof(page)["passed"]


def test_manifest_binds_exact_svg_tree_utf16_glyphs_and_is_absent_for_old_revisions():
    from services.generation_core.drivers.svg.native_chart_evidence import (
        build_chart_witness_manifest, chart_witness_render_kwargs,
    )
    from services.svg_contract import source_digest
    from utils.hashing import canonical_json_digest
    page = _edit(_page(), lambda root, raw: (raw["categories"].__setitem__(0, "Alpha😀"),
                                            setattr(_text(root, "Alpha"), "text", "Alpha😀")))
    manifest = build_chart_witness_manifest(page, page["svg_content"], revision=V5)
    assert manifest["canonicalSvgDigest"] == source_digest(page["svg_content"])
    assert manifest["manifestDigest"] == canonical_json_digest({key: value for key, value in manifest.items()
                                                                if key != "manifestDigest"})
    assert any(target["ranges"] == [{"start": 0, "length": 7}] for target in manifest["charts"][0]["glyphs"])
    assert len(manifest["charts"][0]["datums"]) == 3
    for revision in (None, V2, V3, V4):
        assert chart_witness_render_kwargs(page, page["svg_content"], revision=revision) == {}


def test_manifest_includes_external_axis_unit_and_tick_glyphs():
    from tests.unit.test_svg_chart_content_cartesian import _fixed_fixture
    from services.generation_core.drivers.svg.native_chart_evidence import build_chart_witness_manifest
    raw, group, siblings = _fixed_fixture()
    group.set("id", "native-chart-sales")
    group.set("data-native-chart", json.dumps(raw))
    for node in (*group.iter(), *siblings.iter()):
        node.tag = NS + node.tag
    root = ET.Element(NS + "svg", {"viewBox": "0 0 1280 720"})
    root.extend([group, siblings])
    declarations = extract_native_declarations(root)
    svg = ET.tostring(root, encoding="unicode")
    page = {"svg_content": svg}
    bind_native_declarations(page, svg, declarations)
    manifest = build_chart_witness_manifest(page, svg, revision=V5)
    glyphs = manifest["charts"][0]["glyphs"]
    parsed = ET.fromstring(svg)
    ids = {f"svg-{index}": node for index, node in enumerate(parsed.iter())}
    assert {ids[item["qid"]].text for item in glyphs if item["role"] == "axis"} == {"0", "50", "100"}
    assert "kg" in {ids[item["qid"]].text for item in glyphs if item["role"] == "title-unit"}


def test_static_geometry_is_not_final_captured_content_retention():
    quality = _quality(_page())
    assert quality["requestedNativeObjects"]["passed"]
    assert quality["requestedNativeObjects"]["contentRetentionProven"] is False
    assert all(proof["status"] == "pending" for proof in quality["requestedNativeObjects"]["paintEvidence"].values())


def test_rendered_chart_without_bound_capture_is_measurement_unproven_not_unsupported():
    page = _page()
    quality = _capture_unproven_quality(page)
    assert not quality_keeps_content(quality)
    assert any(failure["kind"] == "measurement_incomplete" and failure.get("scope") == "native_chart_paint"
               for failure in quality["failures"])
    assert not any(failure["kind"] == "native_requested_chart_unsupported" for failure in quality["failures"])
    assert quality["requestedNativeObjects"]["contentRetentionProven"] is False


def test_explicit_post_render_with_missing_quality_fields_cannot_fall_back_to_pending():
    page = _page()
    quality = {"passed": True, "failures": []}
    apply_requested_native_quality(quality, page, page["svg_content"], SOURCE,
                                   pt_per_px=.75, revision=V5, post_render=True)
    assert quality["requestedNativeObjects"]["passed"] is False
    assert any(item["kind"] == "measurement_incomplete" and item.get("scope") == "native_chart_paint"
               for item in quality["failures"])
    assert not quality_keeps_content(quality)


def test_equivalent_curve_support_ignores_collinear_source_vertex_spelling():
    from services.generation_core.drivers.svg.native_chart_evidence import build_chart_witness_manifest
    page = _cartesian_page("line", [[10, 20, 30], [10, 20, 30]])
    def tessellate(root, raw):
        curves = list(root.iter(NS + "polyline"))
        curves[1].set("points", "200,310 300,290 400,270 500,250 600,230")
    _edit(page, tessellate)
    manifest = build_chart_witness_manifest(page, page["svg_content"], revision=V5)
    classes = manifest["charts"][0]["compositionGroup"]["equivalenceClasses"]
    assert len(classes) == 7
    assert all(len(item["members"]) == 2 and item["requiresJointOriginalContribution"] is True for item in classes)
    assert all(any(item["kind"] == "value-anchor" for item in datum["geometrySupport"]["visibilityObligations"])
               for datum in manifest["charts"][0]["datums"])


def test_equal_numbers_with_different_actual_units_cannot_share_support_witnesses():
    from services.generation_core.drivers.svg.native_chart_evidence import build_chart_witness_manifest
    page = _cartesian_page("line", [[10, 20, 30], [10, 20, 30]], labels="above")
    def units(root, raw):
        labels = [node for node in root.iter(NS + "text") if node.text in {"10", "20", "30"}]
        for index, node in enumerate(labels):
            node.text += "kg" if index < 3 else "%"
            node.set("fill", "#2563EB" if index < 3 else "#F97316")
            node.set("x", str(float(node.get("x")) + (-20 if index < 3 else 20)))
    _edit(page, units)
    manifest = build_chart_witness_manifest(page, page["svg_content"], revision=V5)
    chart = manifest["charts"][0]
    assert {datum["geometrySupport"]["unitKey"] for datum in chart["datums"]} == {"kg", "%"}
    assert chart["compositionGroup"]["equivalenceClasses"] == []


def test_native_support_alternatives_reference_actual_contribution_region_ids():
    from services.generation_core.drivers.svg.native_chart_evidence import build_chart_witness_manifest
    page = _cartesian_page("column", [[10, 20, 30], [30, 40, 50]], grouping="stacked")
    manifest = build_chart_witness_manifest(page, page["svg_content"], revision=V5)
    datums = manifest["charts"][0]["datums"]
    regions = {region["id"] for datum in datums for region in datum["geometrySupport"]["contributionRegions"]}
    alternatives = [alternative for datum in datums for obligation in datum["geometrySupport"]["visibilityObligations"]
                    for alternative in obligation["alternatives"] if alternative["kind"] == "shared-stack-interface"]
    assert alternatives
    assert all(item["datumInteriorSupportId"] in regions and item["neighborInteriorSupportId"] in regions for item in alternatives)


def test_outline_bar_contribution_profiles_use_its_actual_longitudinal_strokes():
    from services.generation_core.drivers.svg.native_chart_evidence import build_chart_witness_manifest
    def outline(root, raw):
        for rect in root[0].iter(NS + "rect"):
            rect.set("stroke", rect.get("fill"))
            rect.set("fill", "none")
            rect.set("stroke-width", "2")
    page = _edit(_page(), outline)
    manifest = build_chart_witness_manifest(page, page["svg_content"], revision=V5)
    for datum in manifest["charts"][0]["datums"]:
        regions = {item["kind"]: item for item in datum["geometrySupport"]["contributionRegions"]}
        assert regions["interior-profile"]["segments"] == regions["incident-sides"]["segments"]


def test_paired_title_units_do_not_borrow_suffix_of_another_series_name():
    from services.generation_core.drivers.svg.native_chart_evidence import build_chart_witness_manifest
    page = _cartesian_page("line", [[10, 20, 30], [10, 20, 30]])
    def rename(root, raw):
        for index, name in enumerate(("A", "BA")):
            _text(root, f"Series {index + 1}").text = name
            raw["series"][index]["name"] = name
        raw["title"] = "BA (kg), A (%)"
        title = ET.SubElement(root[0], NS + "text", {"x": "500", "y": "30", "font-size": "16", "fill": "#222222"})
        title.text = raw["title"]
    _edit(page, rename)
    manifest = build_chart_witness_manifest(page, page["svg_content"], revision=V5)
    assert {datum["seriesIndex"]: datum["geometrySupport"]["unitKey"] for datum in manifest["charts"][0]["datums"]} == {0: "%", 1: "kg"}
    assert manifest["charts"][0]["compositionGroup"]["equivalenceClasses"] == []


@pytest.mark.parametrize("kind", ["pie", "donut"])
def test_polar_ownership_requires_actual_disjoint_fills_and_shared_radial_segment(kind):
    from services.generation_core.drivers.svg.native_chart_evidence import build_chart_witness_manifest
    def separators(root, raw):
        for sector in root.iter(NS + "polygon"):
            sector.set("stroke", "#FFFFFF")
            sector.set("stroke-width", "2")
    page = _edit(_polar_page(kind, shown=False), separators)
    manifest = build_chart_witness_manifest(page, page["svg_content"], revision=V5)
    relations = [item for item in manifest["charts"][0]["compositionGroup"]["relationships"]
                 if item["kind"] == "proved-polar-adjacency"]
    assert len(relations) == 1
    assert all(item["occluderPaint"] == "all" and item["polarTopology"]["sharedRadialSegments"]
               and item["polarTopology"]["fillInteriorsDisjoint"] is True
               and item["regionRule"] == "native-alpha-intersection-in-datum-support/v1"
               and int(item["occluderQid"][4:]) > int(item["targetQid"][4:])
               and item["neighborGeometrySupportDigest"].startswith("sha256:")
               for item in relations[0]["localOverlaps"])


@pytest.mark.parametrize("revision", [None, V2, V3, V4, V5])
def test_preserved_chart_gate_is_final_only_for_literal_v5_declarations(revision):
    from types import SimpleNamespace
    from services.generation_core.drivers.svg.render import _apply_preserved_v5_chart_quality
    page = _page()
    original_failure = {"kind": "source_native_chart_unprojectable"}
    quality = {"passed": False, "failures": [original_failure]}
    before = deepcopy(quality)
    capture = SimpleNamespace(quality=quality, png=b"unmeasured")
    _apply_preserved_v5_chart_quality(quality, page, page["svg_content"], SOURCE,
        revision=revision, pt_per_px=.75, render_capture=capture)
    if revision == V5:
        assert quality["passed"] is False
        assert original_failure in quality["failures"]
        assert any(failure["kind"] == "measurement_incomplete" for failure in quality["failures"])
        assert not quality_keeps_content(quality)
    else:
        assert quality == before


def test_preserved_v5_gate_leaves_pages_without_chart_declarations_unchanged():
    from services.generation_core.drivers.svg.render import _apply_preserved_v5_chart_quality
    quality = {"passed": True, "failures": []}
    _apply_preserved_v5_chart_quality(quality, {}, '<svg xmlns="http://www.w3.org/2000/svg"/>', SOURCE,
        revision=V5, pt_per_px=.75, render_capture=None)
    assert quality == {"passed": True, "failures": []}


def test_preserved_v5_chart_gate_does_not_replace_existing_table_authority():
    from services.generation_core.drivers.svg.render import _apply_preserved_v5_chart_quality
    page = _page()
    quality = {"passed": True, "failures": []}
    source = SOURCE + "\n\ntable: | A | B |\n|---|---|\n| 1 | 2 |"
    _apply_preserved_v5_chart_quality(quality, page, page["svg_content"], source,
        revision=V5, pt_per_px=.75, render_capture=None)
    assert quality["requestedNativeObjects"]["requiredTables"] == 0
    assert quality["requestedNativeObjects"]["requiredCharts"] == 1
    assert any(failure["kind"] == "measurement_incomplete" for failure in quality["failures"])
    assert not any(failure["kind"] == "native_requested_table_missing" for failure in quality["failures"])


@pytest.mark.parametrize("other", [
    [(0, 0), (-2, 1), (-1, 2)],  # Center contact has no positive radial boundary.
    [(0, 0), (2, 0), (1, 1)],  # Shared edge lies on the same filled side.
    [(0, 0), (2, 0), (0, 2)],  # Identical interiors are not adjacent sectors.
])
def test_polar_exact_topology_does_not_authorize_touch_or_overlapping_interiors(other):
    from fractions import Fraction
    from services.generation_core.drivers.svg.chart_content_polar import (
        _fill_edges, _disjoint_fill_interiors, _shared_radial_fill_boundaries,
    )
    first = _fill_edges([[(Fraction(x), Fraction(y)) for x, y in [(0, 0), (2, 0), (0, 2)]]])
    second = _fill_edges([[(Fraction(x), Fraction(y)) for x, y in other]])
    geometry = {"center": [0, 0], "radii": [2, 2], "holeRatio": 0}
    assert not (_disjoint_fill_interiors(first, second)
                and _shared_radial_fill_boundaries(first, second, geometry))


def test_polar_exact_topology_accepts_positive_radial_boundary_and_rejects_extra_chord():
    from fractions import Fraction
    from types import SimpleNamespace
    from services.generation_core.drivers.svg.chart_content_polar import (
        _fill_edges, _disjoint_fill_interiors, _shared_radial_fill_boundaries, _linear_fill_paths,
    )
    paths = [[(Fraction(x), Fraction(y)) for x, y in points] for points in (
        [(0, 0), (2, 0), (0, 2)], [(0, 0), (0, -2), (2, 0)])]
    first, second = [_fill_edges([path]) for path in paths]
    assert _disjoint_fill_interiors(first, second)
    assert _shared_radial_fill_boundaries(first, second, {"center": [0, 0], "radii": [2, 2]})
    assert _linear_fill_paths(SimpleNamespace(subpaths=(paths[0], ((0, 0), (1, 1))), vertices=())) is None


def _stack_labels_page(values=None, *, same_color=False):
    values = values or [[10, 20, 30], [30, 40, 50]]
    page = _cartesian_page("column", values, grouping="stacked")
    def add_labels(root, raw):
        raw["valueLabels"] = {"shown": True, "position": "center"}
        bars = [node for node in root.iter(NS + "rect") if node.get("width") == "18"]
        for bar, value in zip(bars, (value for row in values for value in row), strict=True):
            x = float(bar.get("x")) + float(bar.get("width")) / 2
            y = float(bar.get("y")) + float(bar.get("height")) / 2
            label = ET.SubElement(root[0], NS + "text", {"x": str(x), "y": str(y + 3.75),
                "font-size": "10", "fill": "#222222", "text-anchor": "middle"})
            label.text = str(value)
        if same_color:
            for shape in root.iter(NS + "rect"):
                shape.set("fill", "#2563EB")
            for series in raw["series"]:
                series["pointColors"] = ["#2563EB"] * len(raw["categories"])
    return _edit(page, add_labels)


@pytest.mark.parametrize("same_color", [False, True])
def test_stack_glyph_alternative_binds_spatial_labels_both_interiors_and_outer_boundaries(same_color):
    from services.generation_core.drivers.svg.native_chart_evidence import build_chart_witness_manifest
    page = _stack_labels_page(same_color=same_color)
    manifest = build_chart_witness_manifest(page, page["svg_content"], revision=V5)
    chart = manifest["charts"][0]
    alternatives = [alternative for datum in chart["datums"]
                    for obligation in datum["geometrySupport"]["visibilityObligations"]
                    for alternative in obligation["alternatives"] if alternative["kind"] == "bound-stack-value-glyphs"]
    assert len(alternatives) == 6
    for alternative in alternatives:
        assert alternative["datumValueGlyphTargetIds"] and alternative["neighborValueGlyphTargetIds"]
        assert alternative["datumInteriorSupportId"].endswith("/interior-profile")
        assert alternative["neighborInteriorSupportId"].endswith("/interior-profile")
        assert alternative["stackBaseline"]["supportId"].endswith("/value-base")
        assert alternative["stackOuterTotal"]["supportId"].endswith("/value-end")
        assert isinstance(alternative["cumulativeValue"], str)


@pytest.mark.parametrize("attack", ["swap", "boundary", "extra", "height"])
def test_stack_numbers_cannot_choose_ambiguous_or_wrong_segments(attack):
    page = _stack_labels_page()
    def corrupt(root, raw):
        labels = [node for node in root.iter(NS + "text") if node.get("font-size") == "10"]
        bars = [node for node in root.iter(NS + "rect") if node.get("width") == "18"]
        if attack == "swap":
            labels[0].text, labels[3].text = labels[3].text, labels[0].text
        elif attack == "boundary":
            labels[0].set("y", str(float(bars[0].get("y")) + 3.75))
        elif attack == "extra":
            root[0].append(deepcopy(labels[0]))
        else:
            bars[0].set("height", "20")
    page = _edit(page, corrupt)
    assert not _proof(page)["passed"]
    assert not quality_keeps_content(_quality(page, source=""))


def test_repeated_equal_stack_values_keep_distinct_spatial_glyph_ownership():
    from services.generation_core.drivers.svg.native_chart_evidence import build_chart_witness_manifest
    page = _stack_labels_page([[10, 10, 10], [10, 10, 10]])
    manifest = build_chart_witness_manifest(page, page["svg_content"], revision=V5)
    assert manifest is not None
    value_glyphs = [glyph for glyph in manifest["charts"][0]["glyphs"] if glyph["role"] == "value"]
    assert len(value_glyphs) == 6
    assert len({tuple(glyph["datumIds"]) for glyph in value_glyphs}) == 6


@pytest.mark.parametrize("values", [None, [[10, 20, 30], [3, 3, 3]]])
def test_stack_outside_end_label_inside_neighbor_keeps_endpoint_association(values):
    from services.generation_core.drivers.svg.native_chart_evidence import build_chart_witness_manifest
    page = _stack_labels_page(values)
    def outside_labels(root, raw):
        raw["valueLabels"]["position"] = "outsideEnd"
        bars = [node for node in root.iter(NS + "rect") if node.get("width") == "18"]
        labels = [node for node in root.iter(NS + "text") if node.get("font-size") == "10"]
        for bar, label in zip(bars, labels, strict=True):
            label.set("y", str(float(bar.get("y")) - 8))
    page = _edit(page, outside_labels)
    assert _proof(page)["passed"]
    manifest = build_chart_witness_manifest(page, page["svg_content"], revision=V5)
    assert not any(alternative["kind"] == "bound-stack-value-glyphs" for datum in manifest["charts"][0]["datums"]
                   for obligation in datum["geometrySupport"]["visibilityObligations"]
                   for alternative in obligation["alternatives"])


def _marker_page():
    from tests.unit.test_svg_chart_content_cartesian import _fixture
    raw, group = _fixture("line", [[10, 20, 30]], markers=True, appearance=True)
    group.set("id", "native-chart-sales")
    group.set("data-native-chart", json.dumps(raw))
    for node in group.iter():
        node.tag = NS + node.tag
    root = ET.Element(NS + "svg", {"viewBox": "0 0 1280 720"})
    root.append(group)
    declarations = extract_native_declarations(root)
    svg = ET.tostring(root, encoding="unicode")
    page = {"svg_content": svg}
    bind_native_declarations(page, svg, declarations)
    return page


def test_marker_alternative_is_explicit_same_datum_complete_boundary_contract():
    from services.generation_core.drivers.svg.native_chart_evidence import build_chart_witness_manifest
    page = _marker_page()
    svg = page["svg_content"]
    manifest = build_chart_witness_manifest(page, svg, revision=V5)
    for datum in manifest["charts"][0]["datums"]:
        for obligation in datum["geometrySupport"]["visibilityObligations"]:
            marker = next(alt for alt in obligation["alternatives"] if alt["kind"] == "marker-on-datum")
            assert marker["markerTargetId"].endswith("/" + datum["id"])
            assert marker["geometrySupportDigest"] == datum["geometrySupport"]["geometryDigest"]
            assert marker["requiresEntireBoundaryCoverage"] is True
            assert marker["requiresOriginalContribution"] is True


def test_an_unprovable_second_chart_does_not_withdraw_the_first_charts_witness():
    from services.generation_core.drivers.svg.native_chart_evidence import build_chart_witness_manifest
    from services.generation_core.drivers.svg.native_chart_evidence import bound_declarations
    original = _page()
    kept, _ = bound_declarations(original, original["svg_content"])
    root = ET.fromstring(original["svg_content"])
    raw = {"chartType": "column", "categories": ["X", "Y"], "series": [{"name": "Empty", "values": [5, 7]}]}
    ET.SubElement(root, NS + "g", {"id": "native-chart-empty", "data-native-chart": json.dumps(raw)})
    added = extract_native_declarations(root)
    svg = ET.tostring(root, encoding="unicode")
    page = {"svg_content": svg}
    bind_native_declarations(page, svg, {**added, "objects": [*kept["objects"], *added["objects"]]})
    manifest = build_chart_witness_manifest(page, svg, revision=V5)
    assert manifest is not None
    assert [chart["id"] for chart in manifest["charts"]] == ["native-chart-sales"]


def _with_page_extras(*elements, root_class=False):
    from services.generation_core.drivers.svg.native_chart_evidence import bound_declarations
    original = _page()
    kept, _ = bound_declarations(original, original["svg_content"])
    root = ET.fromstring(original["svg_content"])
    for index, element in enumerate(elements):
        root.insert(index, ET.fromstring(element))
    if root_class:
        root.set("class", "slide")
    svg = ET.tostring(root, encoding="unicode")
    page = {"svg_content": svg}
    bind_native_declarations(page, svg, kept)
    return page


@pytest.mark.parametrize("extras,root_class", [
    (('<defs xmlns="http://www.w3.org/2000/svg"><linearGradient id="g1"><stop offset="0" stop-color="#fff"/>'
      '</linearGradient></defs>',), False),
    (('<rect xmlns="http://www.w3.org/2000/svg" class="decor" x="0" y="0" width="10" height="10" fill="#000"/>',),
     False),
    (('<title xmlns="http://www.w3.org/2000/svg">Slide</title>',), False),
])
def test_unrelated_non_painting_svg_features_keep_v5_chart_evidence(extras, root_class):
    from services.generation_core.drivers.svg.native_chart_evidence import chart_drawing_evidence
    page = _with_page_extras(*extras, root_class=root_class)
    evidence = chart_drawing_evidence(page, page["svg_content"], revision=V5)
    assert evidence["native-chart-sales"]["contentGeometry"]["passed"] is True


@pytest.mark.parametrize("extras", [
    ('<style xmlns="http://www.w3.org/2000/svg">.decor{fill:red}</style>',
     '<rect xmlns="http://www.w3.org/2000/svg" class="decor" x="0" y="0" width="10" height="10"/>'),
    ('<defs xmlns="http://www.w3.org/2000/svg"><linearGradient id="g1"/></defs>',
     '<rect xmlns="http://www.w3.org/2000/svg" x="0" y="0" width="1280" height="720" fill="url(#g1)"/>'),
])
def test_stylesheets_and_reference_paints_still_refuse_the_v5_scene(extras):
    # (A class on a chart's own ancestors stays refused by the shared V4/V5 group check.)
    from services.generation_core.drivers.svg.native_chart_evidence import chart_drawing_evidence
    page = _with_page_extras(*extras)
    assert chart_drawing_evidence(page, page["svg_content"], revision=V5) == {}


def test_unmeasured_ink_fragments_fall_back_to_estimated_text_boxes():
    """The renderer reports text whose ink it could not measure as a null fragment
    (seen live on a marker-line chart); that must not crash the V5 scene reader."""
    from services.generation_core.drivers.svg.native_chart_evidence import chart_drawing_evidence
    from services.svg_contract import source_digest
    page = _page()
    svg = page["svg_content"]
    root = ET.fromstring(svg)
    texts = [f"svg-{index}" for index, node in enumerate(root.iter()) if node.tag == NS + "text"]
    measurement = {
        "measurement_complete": True, "source_digest": source_digest(svg), "wrapper_digest": "sha256:wrapper",
        "browser_metrics": {"source_digest": "sha256:wrapper", "elements": [
            {"qid": qid, "tag": "text", "has_owned_text": True, "text_ink_fragments": [None]} for qid in texts]},
    }
    measured = chart_drawing_evidence(page, svg, revision=V5, measurement=measurement)
    static = chart_drawing_evidence(page, svg, revision=V5)
    assert measured["native-chart-sales"]["contentGeometry"]["passed"] is True
    assert measured["native-chart-sales"]["contentGeometry"] == static["native-chart-sales"]["contentGeometry"]


def _occlusion_measurement(svg, occluded_text):
    from services.svg_contract import source_digest
    root = ET.fromstring(svg)
    elements = [{"qid": f"svg-{index}", "tag": "text", "has_owned_text": True,
                 "occluded_fraction": 0.17 if node.text == occluded_text else 0}
                for index, node in enumerate(root.iter()) if node.tag == NS + "text"]
    return {"measurement_complete": True, "source_digest": source_digest(svg), "wrapper_digest": "sha256:wrapper",
            "browser_metrics": {"source_digest": "sha256:wrapper", "elements": elements}}


def test_partly_occluded_text_outside_every_chart_does_not_withdraw_the_chart():
    """Seen live: a KPI caption beside a bar chart measured 17% occluded and the
    whole chart lost its V5 evidence. Text outside the chart group proves nothing
    about the chart; its estimated box still counts as a foreign occluder."""
    page = _with_page_extras(
        '<text xmlns="http://www.w3.org/2000/svg" x="1000" y="80" font-size="16" fill="#222222">KPI note</text>')
    svg = page["svg_content"]
    measured = chart_drawing_evidence(page, svg, revision=V5, measurement=_occlusion_measurement(svg, "KPI note"))
    assert measured["native-chart-sales"]["contentGeometry"]["passed"] is True


def test_partly_occluded_text_inside_the_chart_still_withdraws_the_chart():
    page = _page()
    svg = page["svg_content"]
    assert chart_drawing_evidence(page, svg, revision=V5, measurement=_occlusion_measurement(svg, "60")) == {}


def _won_magnitude_page(label_suffix):
    page = _page(horizontal=True)
    root = ET.fromstring(page["svg_content"])
    raw = page["nativeDeclarations"]["objects"][0]["raw"]
    raw["title"] = raw["series"][0]["name"] = "Sales (억원)"
    group = next(node for node in root.iter(NS + "g") if node.get("id") == "native-chart-sales")
    group.set("data-native-chart", json.dumps(raw, ensure_ascii=False))
    for node in group.iter(NS + "text"):
        if node.text in {"Sales trend", "Sales"}:
            node.text = "Sales (억원)" if node.text == "Sales trend" else None
        elif node.text in {"30", "60", "90"}:
            node.text += label_suffix
    for node in [node for node in group if node.tag == NS + "text" and node.text is None]:
        group.remove(node)
    svg = ET.tostring(root, encoding="unicode")
    page = {"svg_content": svg}
    bind_native_declarations(page, svg, extract_native_declarations(ET.fromstring(svg)))
    return page


@pytest.mark.parametrize("suffix,proven", [("억", True), ("억원", True), ("명", False)])
def test_v5_reads_a_korean_magnitude_with_or_without_won_as_one_unit(suffix, proven):
    page = _won_magnitude_page(suffix)
    quality = {"passed": True, "failures": []}
    apply_requested_native_quality(quality, page, page["svg_content"], "", pt_per_px=.75, revision=V5,
                                   paint_deferred=True)
    missing = [failure for failure in quality["failures"] if failure["kind"] == "native_requested_chart_missing"]
    assert (not missing) is proven, quality["failures"]


def test_won_magnitude_synonym_is_v5_only():
    from services.generation_core.drivers.svg.native_requirements import _unit_key, _v5_unit_key
    assert _v5_unit_key("억") == _v5_unit_key("억원") == _v5_unit_key("억 원")
    assert _v5_unit_key("만") == _v5_unit_key("만원")
    assert _v5_unit_key("억") != _v5_unit_key("억명")
    assert _unit_key("억") != _unit_key("억원")
