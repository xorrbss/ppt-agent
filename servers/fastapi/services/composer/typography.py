"""The Composer source-fit font policy measured in final target CSS pixels."""
from __future__ import annotations

import math
import re
from collections.abc import Mapping

from lxml import html as lxml_html

POLICY_ID = "composer-source-preservation-v2/dense-7-css-px"


def measured_source_typography(html: str, metrics: Mapping | None) -> tuple[dict, tuple[str, ...]]:
    document = lxml_html.document_fromstring(html)
    owners = document.xpath('//body[@data-source-fit-policy="composer-source-fit/v2"]')
    if not owners:
        return {}, ()
    floor = float(owners[0].get("data-source-font-floor-px") or 7)
    if not math.isfinite(floor) or floor < 7:
        raise ValueError("composer_source_font_policy_invalid")
    elements = (metrics or {}).get("elements", [])
    carriers = [item for item in elements if isinstance(item, Mapping)
                and type(item.get("owned_text_length")) is int and item["owned_text_length"] > 0]
    source_text = "".join(document.xpath('//body//text()[not(ancestor::style) and not(ancestor::script)]'))
    text_length = len(re.sub(r"\s", "", source_text).encode("utf-16-le")) // 2
    if any(isinstance(item, Mapping) and "owned_text_length" in item for item in elements):
        # This telemetry follows DOM text ownership, including tail text and
        # child spans whose QID belongs to their ancestor container.
        if sum(item["owned_text_length"] for item in carriers) != text_length:
            return {"policyId": POLICY_ID, "status": "unmeasured"}, ("font_measurement: incomplete text coverage",)
        text_qids = {item.get("qid") or "unbound-text" for item in carriers}
    else:
        # Historical/injected measurements must explicitly cover every text
        # owner; a nested or tail-only node cannot become notApplicable.
        text_qids = set()
        for node in document.xpath('//body//*[not(self::style) and not(self::script)]'):
            if (node.text or "").strip() or any((child.tail or "").strip() for child in node):
                owner = node if node.get("data-qid") else next(iter(node.xpath('ancestor::*[@data-qid][1]')), None)
                text_qids.add(owner.get("data-qid") if owner is not None else "unbound-text")
        if text_length and not text_qids:
            text_qids.add("unbound-text")
    measurements = {
        item.get("qid"): item.get("effective_font_size")
        for item in elements if isinstance(item, Mapping) and item.get("qid") in text_qids
    }
    if not text_qids:
        return {"policyId": POLICY_ID, "status": "notApplicable"}, ()
    invalid = [qid for qid in text_qids if type(measurements.get(qid)) not in (int, float)
               or not math.isfinite(measurements[qid]) or measurements[qid] <= 0]
    if invalid:
        return {"policyId": POLICY_ID, "status": "unmeasured"}, (
            "font_measurement: final effective source font unavailable (" + ", ".join(sorted(invalid)) + ")",
        )
    minimum = min(measurements.values())
    failed = sorted(qid for qid, size in measurements.items() if size + 1e-12 < floor)
    return {
        "policyId": POLICY_ID, "unit": "css-px", "minimum": minimum,
        "requiredMinimum": floor, "measurementSpace": "target-transformed",
        "status": "failed" if failed else "passed",
    }, (("font_floor: final effective source text below floor (" + ", ".join(failed) + ")",) if failed else ())
