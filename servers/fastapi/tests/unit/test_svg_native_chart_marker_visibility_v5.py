"""Bound marker source visibility; final same-capture paint remains mandatory."""
from xml.etree import ElementTree as ET

import pytest

from services.generation_core.drivers.svg.native_chart_evidence import chart_drawing_evidence
from services.generation_core.drivers.svg.native_requirements import (
    REQUIREMENTS_VERSION_V5 as V5,
    required_numeric_claims_with_native_charts,
)
from tests.unit.test_svg_native_chart_retention_v5 import NS, _edit, _marker_page


SOURCE = "Line chart with later native same-datum markers.\nchart: Series 1: A 10, B 20, C 30"


def _proof(page):
    evidence = chart_drawing_evidence(page, page["svg_content"], revision=V5)
    assert len(evidence) == 1
    return evidence["native-chart-sales"]


def _claims(page, content=SOURCE):
    return required_numeric_claims_with_native_charts(
        content, page, page["svg_content"], native_source=SOURCE, pt_per_px=.75, revision=V5)


def test_same_color_marker_off_trace_ink_is_visible_without_literal_value_labels():
    page = _marker_page()
    proof = _proof(page)
    assert proof["contentGeometry"]["passed"] is True
    assert proof["contentGeometry"]["numericLabelsRequired"] is False
    assert all("markerShapeIndex" in point for point in proof["contentGeometry"]["points"])
    assert _claims(page) == []
    assert _claims(page, SOURCE + "\nA separate prose obligation is 20.") == ["20"]
    assert proof["paintEvidence"]["passed"] is False
    assert proof["paintEvidence"]["status"] == "pending"


def test_stroked_marker_off_trace_ink_uses_actual_paint():
    def edit(root, raw):
        for marker in root.findall(".//" + NS + "circle"):
            marker.set("fill", "none")
            marker.set("stroke", "#2563EB")
            marker.set("stroke-width", "2")

    page = _edit(_marker_page(), edit)
    assert _proof(page)["contentGeometry"]["passed"] is True
    assert _claims(page) == []


@pytest.mark.parametrize("mutation", ["missing", "off-center", "hidden-later", "same-background", "inside-thick-trace"])
def test_marker_visibility_uncertainty_keeps_literal_claims(mutation):
    def edit(root, raw):
        group = root.find(NS + "g")
        marker = group.find(NS + "circle")
        if mutation == "missing":
            group.remove(marker)
        elif mutation == "off-center":
            marker.set("cx", str(float(marker.get("cx")) + 1))
        elif mutation == "hidden-later":
            ET.SubElement(root, NS + "rect", {"x": "190", "y": "300", "width": "20",
                "height": "20", "fill": "#2563EB"})
        elif mutation == "same-background":
            root.insert(0, ET.Element(NS + "rect", {"x": "0", "y": "0", "width": "1280",
                "height": "720", "fill": "#2563EB"}))
        else:
            group.find(NS + "polyline").set("stroke-width", "20")

    page = _edit(_marker_page(), edit)
    evidence = chart_drawing_evidence(page, page["svg_content"], revision=V5)
    assert not evidence or evidence["native-chart-sales"]["contentGeometry"]["passed"] is False
    claims = _claims(page)
    assert "20" in claims and "30" in claims


def test_unbound_circle_does_not_gain_marker_visibility_authority():
    def edit(root, raw):
        # The extra circle has no independently requested datum/marker binding.
        group = root.find(NS + "g")
        ET.SubElement(group, NS + "circle", {"cx": "300", "cy": "290", "r": "4", "fill": "#2563EB"})

    page = _edit(_marker_page(), edit)
    evidence = chart_drawing_evidence(page, page["svg_content"], revision=V5)
    assert not evidence or evidence["native-chart-sales"]["contentGeometry"]["passed"] is False
    assert "20" in _claims(page)
