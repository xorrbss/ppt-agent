"""Immutable server-owned style profiles for Deck IR generation.

Profiles are derived from retained reference assets, but expose only design
grammar and art-direction policy.  Provider output never receives CSS, HTML,
coordinates, safety authority, client wording, logos, or reference content.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path
from types import MappingProxyType
from typing import Any, Mapping

from .grammar import DesignGrammarManifest, default_design_grammar, digest


@dataclass(frozen=True, slots=True)
class DeckIrStyleProfile:
    profile_id: str
    version: int
    grammar_id: str
    palette_id: str
    type_scale_id: str
    grid_id: str
    motif_id: str
    mood: tuple[str, ...]
    density: str
    variation_budget: int
    style_tokens: Mapping[str, str]
    art_direction: Mapping[str, object]
    retained_reference_digest: str
    profile_digest: str


def _retained_reference_digest() -> str:
    fastapi_root = Path(__file__).resolve().parents[3]
    sources = [
        fastapi_root / "authored_styles" / "hyundai-culture-center.yaml",
        *(
            fastapi_root / "authored_style_assets" / "hyundai-culture-center" / name
            for name in (
                "cover.png",
                "content.png",
                "data.png",
                "timeline.png",
                "architecture.png",
                "closing.png",
            )
        ),
    ]
    if any(not path.is_file() for path in sources):
        raise ValueError("deck_ir_retained_style_reference_unavailable")
    return digest(
        {
            "referenceId": "hyundai-culture-center",
            "files": [
                {
                    "name": path.name,
                    "size": path.stat().st_size,
                    "digest": digest({"bytes": path.read_bytes().hex()}),
                }
                for path in sources
            ],
        }
    )


def _profile(
    *,
    profile_id: str,
    grammar_id: str,
    palette_id: str,
    type_scale_id: str,
    grid_id: str,
    motif_id: str,
    mood: tuple[str, ...],
    density: str,
    variation_budget: int,
    style_tokens: Mapping[str, str],
    art_direction: Mapping[str, object],
    retained_reference_digest: str,
) -> DeckIrStyleProfile:
    unsigned = {
        "schemaVersion": "deck-ir-style-profile/v1",
        "profileId": profile_id,
        "version": 1,
        "grammarId": grammar_id,
        "paletteId": palette_id,
        "typeScaleId": type_scale_id,
        "gridId": grid_id,
        "motifId": motif_id,
        "mood": list(mood),
        "density": density,
        "variationBudget": variation_budget,
        "styleTokens": dict(style_tokens),
        "artDirection": dict(art_direction),
        "retainedReferenceDigest": retained_reference_digest,
    }
    return DeckIrStyleProfile(
        profile_id=profile_id,
        version=1,
        grammar_id=grammar_id,
        palette_id=palette_id,
        type_scale_id=type_scale_id,
        grid_id=grid_id,
        motif_id=motif_id,
        mood=mood,
        density=density,
        variation_budget=variation_budget,
        style_tokens=MappingProxyType(dict(style_tokens)),
        art_direction=MappingProxyType(dict(art_direction)),
        retained_reference_digest=retained_reference_digest,
        profile_digest=digest(unsigned),
    )


def default_style_profile() -> DeckIrStyleProfile:
    grammar = default_design_grammar()
    return _profile(
        profile_id="enterprise-modular",
        grammar_id=grammar.grammar_id,
        palette_id="enterprise-blue",
        type_scale_id="enterprise-korean",
        grid_id="wide-corporate",
        motif_id="modular-rail",
        mood=("credible", "progressive"),
        density="medium",
        variation_budget=6,
        style_tokens=grammar.style_tokens,
        art_direction={
            "focalStrength": "medium",
            "negativeSpaceBias": "balanced",
            "negativeSpaceAmount": "balanced",
            "asymmetry": "subtle",
            "overlapMode": "none",
            "imageTreatment": "contained",
            "cropIntent": "context-preserving",
            "typographyRhythm": "steady",
            "depthIntent": "flat",
            "visualEnergy": "credible",
        },
        retained_reference_digest=digest({"referenceId": "server-default"}),
    )


def hyundai_culture_center_style_profile() -> DeckIrStyleProfile:
    tokens = {
        **dict(default_design_grammar().style_tokens),
        "safeAreaX": "64px",
        "safeAreaY": "54px",
        "gridGap": "18px",
        "titleSize": "40px",
        "bodySize": "17px",
        "minimumFontSize": "15px",
        "metricSize": "40px",
        "surface": "#F9F9F9",
        "ink": "#1D1D1B",
        "muted": "#52677D",
        "accent": "#08916E",
        "line": "#C9DCD6",
        "panel": "#FFFFFF",
        "softFill": "#E0F3FF",
        "strongFill": "#002060",
        "onAccent": "#FFFFFF",
        "warmAccent": "#D68A18",
        "fontFamily": "'Noto Sans KR','Malgun Gothic',Arial,sans-serif",
        "headerHeight": "18px",
        "footerHeight": "8px",
    }
    return _profile(
        profile_id="hyundai-culture-center",
        grammar_id="hyundai-proposal-reference",
        palette_id="emerald-navy-proposal",
        type_scale_id="noto-korean-proposal",
        grid_id="wide-corporate-header-footer",
        motif_id="emerald-band-navy-tab",
        mood=("credible", "thorough"),
        density="medium",
        variation_budget=6,
        style_tokens=tokens,
        art_direction={
            "focalStrength": "medium",
            "negativeSpaceBias": "balanced",
            "negativeSpaceAmount": "compact",
            "asymmetry": "subtle",
            "overlapMode": "none",
            "imageTreatment": "contained",
            "cropIntent": "context-preserving",
            "typographyRhythm": "steady",
            "depthIntent": "flat",
            "visualEnergy": "credible",
        },
        retained_reference_digest=_retained_reference_digest(),
    )


def resolve_style_profile(style_id: object) -> DeckIrStyleProfile:
    normalized = str(style_id or "").strip().lower()
    if not normalized or normalized == "enterprise-modular":
        return default_style_profile()
    if normalized == "hyundai-culture-center":
        return hyundai_culture_center_style_profile()
    raise ValueError("deck_ir_style_profile_unsupported")


def design_grammar_for_style(profile: DeckIrStyleProfile) -> DesignGrammarManifest:
    grammar = default_design_grammar()
    unsigned = grammar.to_mapping()
    unsigned.pop("manifestDigest", None)
    unsigned["grammarId"] = profile.grammar_id
    unsigned["version"] = profile.version
    unsigned["serverStyleTokenDigest"] = digest(dict(profile.style_tokens))
    return replace(
        grammar,
        grammar_id=profile.grammar_id,
        version=profile.version,
        style_tokens=MappingProxyType(dict(profile.style_tokens)),
        manifest_digest=digest(unsigned),
    )


def apply_server_style_profile(
    *,
    deck_ir: Mapping[str, object],
    art_directions: list[object],
    profile: DeckIrStyleProfile,
) -> tuple[dict[str, object], list[dict[str, object]]]:
    """Replace provider style choices with the admitted server-owned profile."""

    styled_deck = dict(deck_ir)
    styled_deck["designLanguage"] = {
        "grammarId": profile.grammar_id,
        "mood": list(profile.mood),
        "paletteId": profile.palette_id,
        "typeScaleId": profile.type_scale_id,
        "gridId": profile.grid_id,
        "motifId": profile.motif_id,
        "density": profile.density,
        "variationBudget": profile.variation_budget,
    }
    styled_art: list[dict[str, object]] = []
    for item in art_directions:
        if not isinstance(item, Mapping):
            raise ValueError("art_direction_intent_invalid")
        focal = item.get("focalAnchor")
        if not isinstance(focal, Mapping) or not isinstance(focal.get("zoneKey"), str):
            raise ValueError("art_direction_intent_invalid")
        styled_art.append(
            {
                "schemaVersion": "art-direction-intent/v1",
                "slideKey": item.get("slideKey"),
                "focalAnchor": {
                    "zoneKey": focal["zoneKey"],
                    "strength": profile.art_direction["focalStrength"],
                },
                "negativeSpace": {
                    "bias": profile.art_direction["negativeSpaceBias"],
                    "amount": profile.art_direction["negativeSpaceAmount"],
                },
                "asymmetry": profile.art_direction["asymmetry"],
                "overlapPolicy": {
                    "mode": profile.art_direction["overlapMode"],
                    "allowedZonePairs": [],
                    "textOcclusion": "forbidden",
                    "sourceOcclusion": "forbidden",
                },
                "imageTreatment": profile.art_direction["imageTreatment"],
                "cropIntent": profile.art_direction["cropIntent"],
                "typographyRhythm": profile.art_direction["typographyRhythm"],
                "depthIntent": profile.art_direction["depthIntent"],
                "visualEnergy": profile.art_direction["visualEnergy"],
            }
        )
    return styled_deck, styled_art


__all__ = [
    "DeckIrStyleProfile",
    "apply_server_style_profile",
    "default_style_profile",
    "design_grammar_for_style",
    "hyundai_culture_center_style_profile",
    "resolve_style_profile",
]
