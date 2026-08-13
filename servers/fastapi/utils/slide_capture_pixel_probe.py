"""Paired-pixel contrast probes for authored slide evidence."""

from __future__ import annotations

import html as html_lib
import json
import math
import re
from typing import Any, Mapping

from utils.authored_dom_quality import ensure_authored_qids

SLIDE_W = 1280
SLIDE_H = 720

def _append_probe_style(html: str, css: str) -> str:
    style = f"<style data-codex-pixel-probe>{css}</style>"
    closing = re.search(r"</head\s*>", html, flags=re.IGNORECASE)
    if closing:
        return html[: closing.start()] + style + html[closing.start() :]
    return style + html


def _pixel_probe_document(html: str) -> str:
    """Build paired 1280x720 Chrome frames for background and glyph evidence.

    The first frame preserves every painted layer but removes text ink.  The
    second preserves layout/transforms/filters/masks while rendering text as a
    white coverage mask on black.  Keeping both frames in one Chrome screenshot
    avoids cross-process font and animation skew.
    """
    enriched = ensure_authored_qids(html)
    background = _append_probe_style(
        enriched,
        """
        [data-qid], [data-qid] *, [data-qid]::before, [data-qid]::after {
          color: transparent !important;
          -webkit-text-fill-color: transparent !important;
          text-shadow: none !important;
        }
        """,
    )
    mask = _append_probe_style(
        enriched,
        """
        html, body { background: #000 !important; }
        *:not(html):not(body) {
          background-color: transparent !important;
          background-image: none !important;
          border-color: transparent !important;
          box-shadow: none !important;
          outline-color: transparent !important;
          color: transparent !important;
          -webkit-text-fill-color: transparent !important;
          text-shadow: none !important;
        }
        img, video, canvas { visibility: hidden !important; }
        svg * { fill: transparent !important; stroke: transparent !important; }
        [data-qid], [data-qid] * {
          color: #fff !important;
          -webkit-text-fill-color: #fff !important;
        }
        [data-qid] text { fill: #fff !important; }
        """,
    )
    first = html_lib.escape(background, quote=True)
    second = html_lib.escape(mask, quote=True)
    return f"""<!doctype html><html><head><meta charset=\"utf-8\"><style>
      html,body{{margin:0;width:{SLIDE_W}px;height:{SLIDE_H * 2}px;overflow:hidden;background:#000}}
      iframe{{display:block;border:0;width:{SLIDE_W}px;height:{SLIDE_H}px}}
    </style></head><body><iframe srcdoc=\"{first}\"></iframe><iframe srcdoc=\"{second}\"></iframe></body></html>"""


def _relative_luminance(pixel: tuple[int, int, int]) -> float:
    channels = []
    for component in pixel:
        value = component / 255.0
        channels.append(
            value / 12.92
            if value <= 0.04045
            else ((value + 0.055) / 1.055) ** 2.4
        )
    return 0.2126 * channels[0] + 0.7152 * channels[1] + 0.0722 * channels[2]


def _pixel_ratio(first: tuple[int, int, int], second: tuple[int, int, int]) -> float:
    one, two = _relative_luminance(first), _relative_luminance(second)
    return (max(one, two) + 0.05) / (min(one, two) + 0.05)


def _enrich_pixel_contrast(
    metrics: Mapping[str, Any], rendered: "Any", probe: "Any"
) -> Mapping[str, Any]:
    """Attach conservative per-glyph contrast from paired Chrome pixels.

    Only high-coverage mask pixels are used, avoiding anti-aliased edge pixels
    that would make every glyph appear to have a 1:1 contrast edge.  The 5th
    percentile across glyph cores intentionally represents the worst sustained
    background region, so a title spanning a light and dark gradient cannot pass
    based on its best-looking letters.
    """
    result = json.loads(json.dumps(metrics))
    background = probe.crop((0, 0, SLIDE_W, SLIDE_H)).convert("RGB")
    glyph_mask = probe.crop((0, SLIDE_H, SLIDE_W, SLIDE_H * 2)).convert("L")
    foreground = rendered.convert("RGB")
    elements = result.get("elements")
    if not isinstance(elements, list):
        return result
    for item in elements:
        if not isinstance(item, dict):
            continue
        measurement = item.get("measurement")
        if not isinstance(measurement, dict):
            measurement = {}
            item["measurement"] = measurement
        effects = item.get("visual_effects")
        has_complex_effect = isinstance(effects, Mapping) and any(
            value and str(value) not in {"none", "normal"}
            for value in effects.values()
        )
        if has_complex_effect:
            measurement["visual_effects"] = True
        if not item.get("requires_pixel_contrast"):
            continue
        fragments = item.get("text_fragments")
        if not isinstance(fragments, list):
            item["contrast_ratio"] = None
            measurement["contrast"] = False
            item["contrast_measurement_error"] = "text-fragments-unavailable"
            continue
        samples: list[tuple[int, float]] = []
        for fragment in fragments:
            if not isinstance(fragment, Mapping):
                continue
            try:
                left = max(0, math.floor(float(fragment.get("x", 0))))
                top = max(0, math.floor(float(fragment.get("y", 0))))
                right = min(
                    SLIDE_W,
                    math.ceil(
                        float(fragment.get("x", 0))
                        + float(fragment.get("width", 0))
                    ),
                )
                bottom = min(
                    SLIDE_H,
                    math.ceil(
                        float(fragment.get("y", 0))
                        + float(fragment.get("height", 0))
                    ),
                )
            except (TypeError, ValueError):
                continue
            for y in range(top, bottom):
                for x in range(left, right):
                    coverage = glyph_mask.getpixel((x, y))
                    if coverage >= 16:
                        samples.append(
                            (
                                coverage,
                                _pixel_ratio(
                                    foreground.getpixel((x, y)),
                                    background.getpixel((x, y)),
                                ),
                            )
                        )
        if len(samples) < 4:
            item["contrast_ratio"] = None
            measurement["contrast"] = False
            item["contrast_measurement_error"] = "glyph-mask-insufficient"
            continue
        strongest = max(value for value, _ in samples)
        threshold = max(64, int(strongest * 0.72))
        core = sorted(ratio for value, ratio in samples if value >= threshold)
        if len(core) < 4:
            item["contrast_ratio"] = None
            measurement["contrast"] = False
            item["contrast_measurement_error"] = "glyph-core-insufficient"
            continue
        percentile_index = min(len(core) - 1, max(0, int(len(core) * 0.05)))
        item["contrast_ratio"] = round(core[percentile_index], 6)
        item["contrast_sample_count"] = len(core)
        item["contrast_measurement_method"] = "chrome-paired-pixel-glyph-v1"
        item.pop("contrast_measurement_error", None)
        measurement["contrast"] = True
    return result
