"""Fail-closed readback for explicitly sized native chart labels."""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any
from xml.etree import ElementTree as ET

from services.authored_local_fit_policy import POLICY_REVISION, font_size_allowed

NS = {"c": "http://schemas.openxmlformats.org/drawingml/2006/chart",
      "a": "http://schemas.openxmlformats.org/drawingml/2006/main"}


def verify_chart_labels(
    payload: bytes,
    runs: Sequence[Mapping[str, Any]],
    *,
    scale: float = 1,
    policy_revision: str = POLICY_REVISION,
) -> int:
    from services.authored_local_fit_export import _content_digest, _fail
    root = ET.fromstring(payload)
    # Automatic tick generation has no source run correspondence. It must be
    # represented explicitly before the original-size contract can be proved.
    for axis in root.findall(".//c:valAx", NS):
        if axis.find("c:delete", NS) is None or axis.find("c:delete", NS).get("val") != "1":
            _fail("chart_tick_baseline_unavailable")
    checked = 0

    def verify(text: str, properties: ET.Element | None) -> None:
        nonlocal checked
        if properties is None or properties.get("sz") is None:
            _fail("unverifiable_font_size")
        size = float(properties.get("sz")) / 100 * scale
        matches = [run for run in runs if _content_digest(text, str(run.get("content_digest", "")))
                   == run.get("content_digest")]
        if not matches:
            _fail("chart_label_baseline_unavailable")
        if not all(
            font_size_allowed(
                float(run["original_pt"]), size,
                policy_revision=policy_revision,
            )
            for run in matches
        ):
            _fail("font_floor_or_shrink_limit")
        checked += 1

    for body in root.findall(".//c:rich", NS):
        for paragraph in body.findall("a:p", NS):
            default = paragraph.find("a:pPr/a:defRPr", NS)
            for native in paragraph.findall("a:r", NS):
                value = native.find("a:t", NS)
                if value is not None and value.text:
                    properties = native.find("a:rPr", NS)
                    verify(value.text, properties if properties is not None and properties.get("sz") else default)
    categories = root.findall(".//c:cat//c:pt/c:v", NS)
    axes = root.findall(".//c:catAx", NS)
    for axis in axes:
        deleted = axis.find("c:delete", NS)
        if deleted is not None and deleted.get("val") == "1":
            continue
        properties = axis.find("c:txPr/a:p/a:pPr/a:defRPr", NS)
        for value in categories:
            if value.text:
                verify(value.text, properties)
    legend = root.find(".//c:legend", NS)
    if legend is not None:
        properties = legend.find("c:txPr/a:p/a:pPr/a:defRPr", NS)
        labels = root.findall(".//c:ser/c:tx//c:v", NS)
        for value in labels:
            if value.text:
                verify(value.text, properties)
    if root.findall(".//c:dLbls", NS):
        _fail("chart_data_label_baseline_unavailable")
    if checked == 0:
        _fail("chart_font_evidence_unavailable")
    return checked
