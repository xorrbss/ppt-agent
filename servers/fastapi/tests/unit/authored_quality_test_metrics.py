"""Browser-metric fixtures shared by authored review orchestration tests."""

from __future__ import annotations

from typing import Any

from utils.authored_dom_quality import build_compact_dom_map, ensure_authored_qids


def complete_browser_metrics(html: str) -> dict[str, Any]:
    """Return complete, issue-free telemetry for every deterministic edit target.

    Orchestration tests replace Chromium rendering with byte-returning fakes.  The
    production acceptance gate now correctly fails closed when candidate telemetry
    is missing, so those fakes must also satisfy the browser-metric contract.
    Zero-area boxes keep geometry out of persistence-focused assertions while the
    qid set and rendered text remain exact.
    """

    enriched = ensure_authored_qids(html)
    return {
        "viewport": {"width": 1280, "height": 720},
        "fonts_ready": True,
        "elements": [
            {
                "qid": node.qid,
                "tag": node.tag,
                "bbox": {"x": 0, "y": 0, "width": 0, "height": 0},
                "clientWidth": 1,
                "clientHeight": 1,
                "scrollWidth": 1,
                "scrollHeight": 1,
                "font_loaded": True,
                "rendered_text": node.text,
                "computed_style": {},
            }
            for node in build_compact_dom_map(enriched)
        ],
    }
