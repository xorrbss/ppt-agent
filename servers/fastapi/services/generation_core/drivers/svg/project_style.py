"""StyleContext -> the svg author's design-system input.

This is the svg-specific half of what ``services.generation_core.drivers.svg.svg_style_adapter.adapt_svg_style``
used to do inline: reading only the preset's own fields. Admission-shaped
validation (canvas bounds, exact-reconstruction rejection, preset
``svg_overrides`` shape, reference-image bytes) stays in ``svg_style_adapter``;
this module turns an already-built, format-neutral :class:`StyleContext` into
the flat dict the svg author prompt serializes verbatim (see
``services.generation_core.authoring.author_slide_svg.author_slide_svg``: the whole dict minus
``references``/``diagnostics`` is dumped into the prompt).
"""
from __future__ import annotations

import json
import re

from services.svg_contract import SVG_CONTRACT_VERSION

from ...style.context import StyleContext
from .canvas_brief import SVG_CANVAS_BRIEF_VERSION, adapt_brief_line, svg_brief_replacements
from utils.hashing import sha256_prefixed

SVG_STYLE_ADAPTER_VERSION = "svg-style/v4"


def _digest(value: object) -> str:
    return sha256_prefixed(json.dumps(value, sort_keys=True, ensure_ascii=False).encode())


def project_style(
    context: StyleContext,
    *,
    width: int,
    height: int,
    overrides: dict,
    diagnostics: list[dict],
    references: dict,
    quality_recovery_policy: dict | None = None,
) -> dict:
    """Build the svg author's flat design-system dict from a ``StyleContext``.

    ``overrides``/``diagnostics``/``references`` are already validated by the
    caller (``svg_style_adapter.adapt_svg_style``); this function only reads
    them through to the output shape, unchanged from before this session.
    """

    from services.svg_authoring_profile import validate_quality_recovery_policy

    quality_policy = validate_quality_recovery_policy(quality_recovery_policy)
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
    # Brief lines are worded against 1280x720; off that canvas they are
    # rewritten here, the one place every svg brief line passes. ``overrides``
    # (the preset's own svg text) are deliberately left alone.
    common_guidance: dict[str, list[str]] = {}
    for line in context.brief_lines:
        if quality_policy is not None:
            advice = _svg_reference_design_advice(line.text)
            if advice:
                common_guidance.setdefault(line.section, []).append(adapt_brief_line(advice, width, height))
        elif "svg" in line.applies_to and line.section not in {"LAYOUT SYSTEM", "SLIDE ARCHETYPES"}:
            common_guidance.setdefault(line.section, []).append(adapt_brief_line(line.text, width, height))
    result = {
        "adapter_version": SVG_STYLE_ADAPTER_VERSION,
        "contract_version": SVG_CONTRACT_VERSION,
        "style_id": context.preset_id,
        "composition_mode": "free-svg",
        "reference_semantics": "design-identity",
        "source_identity_policy": "svg-source-identity/v2",
        "composition_guidance": [
            "Compose the complete SVG freely for this canvas and this slide's evidence.",
            "Reference examples convey palette, typography, imagery and component treatment.",
            "Choose geometry, grouping and reading order from the content; reference column counts and positions are not constraints.",
        ],
        "canvas": {"width": width, "height": height},
        "tokens": tokens,
        "common_guidance": common_guidance,
        "mapped_guidance": context.mapped_guidance,
        "overrides": {key: value for key, value in overrides.items() if key == "common_guidance"},
        "diagnostics": diagnostics,
        "unresolved": [],
        "references": references,
        "reference_examples": [
            {"role": role, "digest": item["digest"], "semantics": "art-direction"}
            for role, items in references.items() for item in items
        ],
        "provenance": {
            "tokens": "authored_style_fields",
            "brief_digest": _digest([line.text for line in context.brief_lines]),
            "references": "authored_style_role_images",
        },
    }
    if quality_policy is not None:
        result["quality_recovery_policy"] = quality_policy
        result["provenance"]["design_guidance_projection"] = "svg-quality-first-guidance/v1"
    if svg_brief_replacements(width, height):
        # Absent at 16:9 so that dict (and its digest) stays byte-identical;
        # off-16:9 it lets a receipt reader tell a pre-rewrite snapshot apart.
        result["provenance"]["canvas_brief_rewrite"] = SVG_CANVAS_BRIEF_VERSION
    if context.master_rules is not None:
        result["master_template"] = context.master_rules
        result["master_pin"] = context.master_pin
    if context.design_reference_rules is not None:
        # An uploaded design reference or an SVG user template's style
        # projection. The snapshot key keeps its historical name: stored
        # snapshots are digest-bound and replayed by that key.
        result["design_pack_template"] = {
            key: value for key, value in context.design_reference_rules.items()
            if key not in {"references", "diagnostics"}
        }
        if context.master_rules is None:
            result["tokens"]["palette"] = context.design_reference_rules.get("tokens", {}).get("palette", [])
            if context.design_reference_rules.get("version") == "svg-uploaded-design-reference/v1":
                identity_tokens = context.design_reference_rules.get("tokens", {})
                for key in ("background", "foreground", "accent"):
                    if key in identity_tokens:
                        result["tokens"][key] = identity_tokens[key]
                if "accent" in identity_tokens:
                    result["tokens"]["primary"] = identity_tokens["accent"]
                for role in ("heading", "body"):
                    family = context.design_reference_rules.get("fonts", {}).get(role, {}).get("family")
                    if family:
                        result["tokens"][f"{role}_font"] = family
        if context.design_reference_rules.get("version") == "svg-uploaded-design-reference/v1":
            # A role without its own uploaded sample uses uploaded content art
            # direction rather than an unrelated catalogue cover or ending.
            result["references"] = context.design_reference_rules.get("references", {})
            result["reference_examples"] = context.design_reference_rules.get("reference_examples", [])
        else:
            result["references"] = {**references, **context.design_reference_rules.get("references", {})}
            result["reference_examples"] += context.design_reference_rules.get("reference_examples", [])
    return result


def _svg_reference_design_advice(text: str) -> str:
    """Retain visual intent from mixed-format prose, never executable mechanics."""
    # Inline markup/code examples are not design prose. Other clauses retain
    # identity, emphasis and composition advice, with HTML-only mechanics
    # explicitly neutralized as reference implementation details.
    if re.search(r"<[/a-zA-Z]|```", text):
        return ""
    text = re.sub(r"\b(?:HTML\s*/\s*CSS|HTML|CSS|DOM)\b", "reference implementation", text, flags=re.I)
    text = re.sub(r"\b(?:flexbox|flex|grid-template(?:-[a-z]+)?)\b|position\s*:\s*absolute",
                  "reference arrangement", text, flags=re.I)
    return text


__all__ = ["SVG_STYLE_ADAPTER_VERSION", "project_style"]
