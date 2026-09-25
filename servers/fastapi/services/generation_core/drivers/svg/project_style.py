"""StyleContext -> the svg author's design-system input.

This is the svg-specific half of what ``utils.svg_style_adapter.adapt_svg_style``
used to do inline: reading only the preset's own fields. Admission-shaped
validation (canvas bounds, exact-reconstruction rejection, preset
``svg_overrides`` shape, reference-image bytes) stays in ``svg_style_adapter``;
this module turns an already-built, format-neutral :class:`StyleContext` into
the flat dict the svg author prompt serializes verbatim (see
``utils.llm_calls.author_slide_svg.author_slide_svg``: the whole dict minus
``references``/``diagnostics`` is dumped into the prompt).
"""
from __future__ import annotations

from hashlib import sha256
import json

from services.svg_contract import SVG_CONTRACT_VERSION

from ...style.context import StyleContext

SVG_STYLE_ADAPTER_VERSION = "svg-style/v2"


def _digest(value: object) -> str:
    return "sha256:" + sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def project_style(
    context: StyleContext,
    *,
    width: int,
    height: int,
    overrides: dict,
    diagnostics: list[dict],
    references: dict,
) -> dict:
    """Build the svg author's flat design-system dict from a ``StyleContext``.

    ``overrides``/``diagnostics``/``references`` are already validated by the
    caller (``svg_style_adapter.adapt_svg_style``); this function only reads
    them through to the output shape, unchanged from before this session.
    """

    tokens = {
        "primary": context.palette.primary,
        "background": context.palette.background,
        "palette": list(context.palette.swatches),
        "heading_font": context.fonts.heading,
        "body_font": context.fonts.body,
        "mono_font": context.fonts.mono,
        "wordmark": context.wordmark,
        "chrome": {},
    }
    common_guidance: dict[str, list[str]] = {}
    for line in context.brief_lines:
        if "svg" in line.applies_to:
            common_guidance.setdefault(line.section, []).append(line.text)
    result = {
        "adapter_version": SVG_STYLE_ADAPTER_VERSION,
        "contract_version": SVG_CONTRACT_VERSION,
        "style_id": context.preset_id,
        "canvas": {"width": width, "height": height},
        "tokens": tokens,
        "common_guidance": common_guidance,
        "mapped_guidance": context.mapped_guidance,
        "overrides": overrides,
        "diagnostics": diagnostics,
        "unresolved": [],
        "references": references,
        "provenance": {
            "tokens": "authored_style_fields",
            "brief_digest": _digest([line.text for line in context.brief_lines]),
            "references": "authored_style_role_images",
        },
    }
    if context.master_rules is not None:
        result["master_template"] = context.master_rules
        result["master_pin"] = context.master_pin
    if context.design_pack_rules is not None:
        result["design_pack_template"] = context.design_pack_rules
    return result


__all__ = ["SVG_STYLE_ADAPTER_VERSION", "project_style"]
