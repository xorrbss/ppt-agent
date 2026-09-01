"""Versioned, server-owned design grammar for the Deck IR compiler.

This module deliberately accepts only mappings.  WP1 contract models can be
adapted at the boundary without making the compiler depend on their import
order or on provider-owned objects.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import math
import re
from types import MappingProxyType
from typing import Any, Mapping, Sequence


GRAMMAR_SCHEMA_VERSION = "design-grammar-manifest/v1"
COMPILER_VERSION = "constraint-compiler/v1"
RENDERER_ABI_VERSION = "design-component-renderer/v3-complete-qids"
SOLVER_ABI_VERSION = "constraint-solver/v3-zone-connectors"
BALANCED_QUALITY_K = 6
MAX_PARETO_BEAM_WIDTH = 8

_SAFE_PRIMITIVES = frozenset({"grid", "rail", "split", "stage", "stack", "flow", "cluster", "inset", "band"})
_SAFE_COMPONENTS = frozenset({"text", "metric", "card", "comparison", "process", "timeline", "image", "quote", "table", "chart", "shape", "connector", "group", "decoration"})
_SOURCE_FREE_COMPONENTS = _SAFE_COMPONENTS - {"image", "group"}
_SAFE_RELATIONS = frozenset({"leftOf", "rightOf", "above", "below", "alignTop", "alignCenter", "equalWidth", "emphasis", "flow"})
_SAFE_ART_DIRECTION = MappingProxyType({
    "focalStrength": frozenset({"subtle", "medium", "strong"}),
    "negativeSpaceBias": frozenset({"left", "right", "top", "bottom", "balanced", "center"}),
    "negativeSpaceAmount": frozenset({"compact", "balanced", "generous"}),
    "asymmetry": frozenset({"none", "subtle", "moderate", "strong"}),
    "overlapMode": frozenset({"none", "decorative-layering", "background-layering"}),
    "imageTreatment": frozenset({"contained", "edge-crop", "full-bleed", "framed", "masked"}),
    "cropIntent": frozenset({"none", "subject-emphasis", "context-preserving", "dramatic"}),
    "typographyRhythm": frozenset({"steady", "editorial-contrast", "metric-led", "narrative"}),
    "depthIntent": frozenset({"flat", "layered", "stage"}),
    "visualEnergy": frozenset({"calm", "credible", "progressive", "dynamic"}),
})
_SERVER_STYLE_TOKENS = MappingProxyType({
    "viewportWidth": "1280px",
    "viewportHeight": "720px",
    "safeAreaX": "64px",
    "safeAreaY": "48px",
    "gridGap": "20px",
    "titleSize": "42px",
    "titleLineHeight": "1.4",
    "bodySize": "18px",
    "minimumFontSize": "15px",
    "metricSize": "38px",
    "surface": "#F8FAFC",
    "ink": "#102A43",
    "muted": "#52677D",
    "accent": "#1463FF",
    "line": "#C9D6E2",
    "panel": "#FFFFFF",
    "softFill": "#EEF4FF",
    "strongFill": "#102A43",
    "onAccent": "#FFFFFF",
    "warmAccent": "#D68A18",
    "fontFamily": "'Malgun Gothic',Arial,sans-serif",
    "headerHeight": "0px",
    "footerHeight": "0px",
})
_FORBIDDEN_MODEL_KEYS = frozenset({
    "x", "y", "z", "width", "height", "left", "right", "top", "bottom", "position", "coordinates",
    "css", "style", "html", "svg", "javascript", "script", "onclick", "onload", "url", "href", "transform",
    "fontfamily", "fontsize", "color", "opacity", "zindex", "shadow", "filter",
})
_ELEMENT_KEY = re.compile(r"^[a-z][a-z0-9]*(?:[._-][a-z0-9]+)*$")


class DeckIrGrammarError(ValueError):
    """Stable, content-free invalid grammar or intent error."""

    retryable = False

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def canonical_json(value: Any) -> str:
    # Prefer the WP1 canonical ABI once it is installed.  Keep a small local
    # fallback so D1 fixture tests can import this module independently.
    try:
        from .canonical import canonical_json as contract_canonical_json
    except ImportError:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return contract_canonical_json(value)


def digest(value: Any) -> str:
    try:
        from .canonical import canonical_digest
    except ImportError:
        return "sha256:" + hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()
    return canonical_digest(value)


def _as_mapping(value: object, *, reason: str) -> Mapping[str, Any]:
    return contract_mapping(value, reason=reason)


def contract_mapping(value: object, *, reason: str = "deck_ir_contract_mapping_invalid") -> Mapping[str, Any]:
    """Adapt WP1 Pydantic contracts lazily while keeping compiler tests plain.

    No WP1 class is imported at module load time: this package therefore stays
    usable for D1 fixtures while contracts are independently upgraded.
    """

    if isinstance(value, Mapping):
        return value
    dump = getattr(value, "model_dump", None)
    if callable(dump):
        candidate = dump(mode="json", by_alias=True)
        if isinstance(candidate, Mapping):
            return candidate
    raise DeckIrGrammarError(reason)


def reject_raw_model_input(value: Any) -> None:
    """Reject raw layout/CSS/URL input recursively before any rendering.

    A number in a typed chart payload is allowed; only directive keys are
    rejected.  This is intentionally stricter for keys than for text values.
    """

    if isinstance(value, Mapping):
        for key, item in value.items():
            normalized = str(key).replace("-", "").replace("_", "").lower()
            if normalized in _FORBIDDEN_MODEL_KEYS or normalized.endswith("px"):
                raise DeckIrGrammarError("deck_ir_raw_layout_input_forbidden")
            reject_raw_model_input(item)
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        for item in value:
            reject_raw_model_input(item)


@dataclass(frozen=True)
class PrimitiveSpec:
    primitive_id: str
    family: str
    variants: tuple[str, ...]


@dataclass(frozen=True)
class DesignGrammarManifest:
    grammar_id: str
    version: int
    primitives: tuple[PrimitiveSpec, ...]
    component_kinds: frozenset[str]
    art_direction: Mapping[str, frozenset[str]]
    candidate_policy_digest: str
    manifest_digest: str
    style_tokens: Mapping[str, str]
    compiler_compatibility: tuple[str, ...] = (COMPILER_VERSION,)
    renderer_abi_version: str = RENDERER_ABI_VERSION
    solver_abi_version: str = SOLVER_ABI_VERSION

    def supports_component(self, kind: object) -> bool:
        return isinstance(kind, str) and kind in self.component_kinds

    def supports_primitive(self, family: object) -> bool:
        return isinstance(family, str) and any(item.family == family for item in self.primitives)

    def to_mapping(self) -> dict[str, Any]:
        return {
            "schemaVersion": GRAMMAR_SCHEMA_VERSION,
            "grammarId": self.grammar_id,
            "version": self.version,
            "primitives": [
                {"primitiveId": item.primitive_id, "family": item.family, "variants": list(item.variants)}
                for item in self.primitives
            ],
            "componentKinds": sorted(self.component_kinds),
            "artDirection": {key: sorted(value) for key, value in sorted(self.art_direction.items())},
            "candidatePolicyDigest": self.candidate_policy_digest,
            "serverStyleTokenDigest": digest(dict(self.style_tokens)),
            "rendererAbiVersion": self.renderer_abi_version,
            "solverAbiVersion": self.solver_abi_version,
            "compilerCompatibility": list(self.compiler_compatibility),
            "manifestDigest": self.manifest_digest,
        }


def default_design_grammar() -> DesignGrammarManifest:
    primitives = tuple(
        PrimitiveSpec(f"{family}/v1", family, ("base", "emphasis", "spacious"))
        for family in ("grid", "rail", "split", "stage", "stack", "flow", "cluster", "inset", "band")
    )
    unsigned = {
        "schemaVersion": GRAMMAR_SCHEMA_VERSION,
        "grammarId": "enterprise-modular",
        "version": 2,
        "primitives": [{"primitiveId": item.primitive_id, "family": item.family, "variants": list(item.variants)} for item in primitives],
        "componentKinds": sorted(_SOURCE_FREE_COMPONENTS),
        "artDirection": {key: sorted(value) for key, value in sorted(_SAFE_ART_DIRECTION.items())},
        "candidatePolicy": {"balancedQualityK": BALANCED_QUALITY_K, "beamWidthMaximum": MAX_PARETO_BEAM_WIDTH, "candidateObservationsMaximum": BALANCED_QUALITY_K, "perCandidateRecompilesMaximum": 2, "intentResolver": "owned-overlap-capabilities/v1", "payloadProfile": "source-free-bar-connector/v1"},
        "serverStyleTokenDigest": digest(dict(_SERVER_STYLE_TOKENS)),
        "rendererAbiVersion": RENDERER_ABI_VERSION,
        "solverAbiVersion": SOLVER_ABI_VERSION,
        "compilerCompatibility": [COMPILER_VERSION],
    }
    policy_digest = digest(unsigned.pop("candidatePolicy"))
    unsigned["candidatePolicyDigest"] = policy_digest
    return DesignGrammarManifest(
        grammar_id="enterprise-modular", version=2, primitives=primitives, component_kinds=_SOURCE_FREE_COMPONENTS,
        art_direction=_SAFE_ART_DIRECTION, candidate_policy_digest=policy_digest, manifest_digest=digest(unsigned),
        style_tokens=_SERVER_STYLE_TOKENS,
    )


def validate_manifest(value: DesignGrammarManifest | Mapping[str, Any]) -> DesignGrammarManifest:
    if isinstance(value, DesignGrammarManifest):
        manifest = value
    else:
        raw = _as_mapping(value, reason="design_grammar_unknown")
        if raw.get("schemaVersion") != GRAMMAR_SCHEMA_VERSION:
            raise DeckIrGrammarError("design_grammar_unknown")
        grammar_id, version = raw.get("grammarId"), raw.get("version")
        if not isinstance(grammar_id, str) or not grammar_id or isinstance(version, bool) or not isinstance(version, int) or version < 1:
            raise DeckIrGrammarError("design_grammar_unknown")
        raw_primitives = raw.get("primitives")
        if not isinstance(raw_primitives, list) or not raw_primitives:
            raise DeckIrGrammarError("design_grammar_unknown")
        primitives: list[PrimitiveSpec] = []
        for raw_primitive in raw_primitives:
            item = _as_mapping(raw_primitive, reason="design_grammar_unknown")
            family, primitive_id, variants = item.get("family"), item.get("primitiveId"), item.get("variants")
            if family not in _SAFE_PRIMITIVES or not isinstance(primitive_id, str) or not isinstance(variants, list) or not variants:
                raise DeckIrGrammarError("design_grammar_unknown")
            if any(not isinstance(variant, str) or not variant for variant in variants):
                raise DeckIrGrammarError("design_grammar_unknown")
            primitives.append(PrimitiveSpec(primitive_id, family, tuple(variants)))
        kinds = raw.get("componentKinds")
        if not isinstance(kinds, list) or not set(kinds).issubset(_SAFE_COMPONENTS):
            raise DeckIrGrammarError("design_grammar_unknown")
        art_raw = _as_mapping(raw.get("artDirection"), reason="design_grammar_unknown")
        art = {key: frozenset(values) for key, values in art_raw.items() if isinstance(values, list)}
        if set(art) != set(_SAFE_ART_DIRECTION) or any(not values.issubset(_SAFE_ART_DIRECTION[key]) for key, values in art.items()):
            raise DeckIrGrammarError("design_grammar_unknown")
        compatibility = raw.get("compilerCompatibility")
        if not isinstance(compatibility, list) or COMPILER_VERSION not in compatibility:
            raise DeckIrGrammarError("design_grammar_incompatible")
        unsigned = dict(raw)
        supplied_digest = unsigned.pop("manifestDigest", None)
        calculated = digest(unsigned)
        if supplied_digest is not None and supplied_digest != calculated:
            raise DeckIrGrammarError("design_grammar_digest_mismatch")
        policy_digest = raw.get("candidatePolicyDigest")
        if not isinstance(policy_digest, str):
            raise DeckIrGrammarError("design_grammar_unknown")
        manifest = DesignGrammarManifest(grammar_id, version, tuple(primitives), frozenset(kinds), MappingProxyType(art), policy_digest, calculated, _SERVER_STYLE_TOKENS, tuple(compatibility), str(raw.get("rendererAbiVersion")), str(raw.get("solverAbiVersion")))
    if COMPILER_VERSION not in manifest.compiler_compatibility:
        raise DeckIrGrammarError("design_grammar_incompatible")
    return manifest


def server_owned_stylesheet(grammar: DesignGrammarManifest, *, layout_id: str) -> str:
    """Serialize fixed grammar tokens into a scoped, renderer-owned stylesheet.

    The only CSS in compiled output originates here.  ``layout_id`` selects a
    registered primitive family; it cannot inject a selector or CSS value.
    """

    family = layout_id.split("/", 1)[0]
    if not grammar.supports_primitive(family):
        raise DeckIrGrammarError("design_grammar_unknown")
    token = grammar.style_tokens
    if set(token) != set(_SERVER_STYLE_TOKENS):
        raise DeckIrGrammarError("design_grammar_token_invalid")
    layouts = {
        "grid": "grid-template-columns:repeat(12,minmax(0,1fr));grid-auto-rows:minmax(0,1fr);",
        "rail": "grid-template-columns:minmax(180px,3fr) minmax(0,9fr);grid-auto-rows:minmax(0,1fr);",
        "split": "grid-template-columns:minmax(0,5fr) minmax(0,7fr);grid-auto-rows:minmax(0,1fr);",
        "stage": "grid-template-columns:minmax(0,1fr);grid-template-rows:minmax(0,2fr) minmax(0,1fr);",
        "stack": "grid-template-columns:minmax(0,1fr);grid-auto-rows:minmax(0,1fr);",
        "flow": "grid-template-columns:repeat(auto-fit,minmax(220px,1fr));grid-auto-rows:minmax(0,1fr);",
        "cluster": "grid-template-columns:repeat(3,minmax(0,1fr));grid-auto-rows:minmax(0,1fr);",
        "inset": "grid-template-columns:minmax(0,1fr);grid-template-rows:minmax(0,1fr);",
        "band": "grid-template-columns:repeat(12,minmax(0,1fr));grid-auto-rows:minmax(0,1fr);",
    }
    return (
        "html,body{width:1280px;height:720px;margin:0;overflow:hidden;}"
        ".dir-slide,.dir-slide *{box-sizing:border-box;}"
        f".dir-slide{{width:{token['viewportWidth']};height:{token['viewportHeight']};padding:{token['safeAreaY']} {token['safeAreaX']};"
        f"display:grid;gap:{token['gridGap']};position:relative;isolation:isolate;overflow:hidden;background:{token['surface']};color:{token['ink']};"
        f"box-shadow:inset 0 {token['headerHeight']} 0 {token['accent']},inset 0 -{token['footerHeight']} 0 {token['strongFill']};"
        f"font-family:{token['fontFamily']};line-height:1.5;align-content:stretch;}}"
        f".dir-slide.layout-family-{family}{{{layouts[family]}}}"
        ".dir-component{min-width:0;min-height:0;overflow:visible;overflow-wrap:anywhere;word-break:keep-all;position:relative;}"
        f".dir-role-title{{font-size:{token['titleSize']};font-weight:750;line-height:{token['titleLineHeight']};letter-spacing:-0.02em;grid-column:1/-1;max-height:3.6em;}}"
        f".dir-role-body,.dir-role-evidence{{font-size:{token['bodySize']};color:{token['ink']};}}"
        f".dir-metric{{font-size:{token['metricSize']};font-weight:750;color:{token['accent']};display:grid;place-items:center;min-height:120px;border:1px solid {token['line']};border-radius:16px;background:#FFF;}}"
        f".dir-card,.dir-process{{font-size:{token['bodySize']};border:1px solid {token['line']};border-radius:16px;padding:20px;background:#FFF;}}"
        f".dir-image{{min-height:160px;border:1px solid {token['line']};border-radius:16px;background:#E8F0FE;color:{token['muted']};display:grid;place-items:center;}}"
        f".dir-slide .dir-component:not(.dir-role-title){{font-size:max({token['minimumFontSize']},{token['bodySize']});}}"
        ".dir-slide.layout-family-split .dir-component:nth-child(odd),.dir-slide.layout-family-rail .dir-component:nth-child(odd){grid-column:1;}.dir-slide.layout-family-split .dir-component:nth-child(even),.dir-slide.layout-family-rail .dir-component:nth-child(even){grid-column:2;}"
        ".dir-slide.layout-family-stage .dir-role-title{align-self:end;}.dir-slide.layout-family-cluster .dir-component{align-self:stretch;}.dir-slide.layout-family-flow .dir-component{min-height:120px;}"
        f".dir-slide .dir-metric-value{{color:{token['accent']};}}"
        f".dir-slide .dir-badge{{color:{token['strongFill']};}}"
        f".dir-slide .dir-card,.dir-slide .dir-process,.dir-slide .dir-metric{{background:{token['panel']};border-color:{token['line']};}}"
        f".dir-slide .dir-step{{background:{token['softFill']};border-top-color:{token['accent']};}}"
        f".dir-slide .dir-data-table th{{background:{token['accent']};color:{token['onAccent']};}}"
        f".dir-slide .dir-data-table tr:nth-child(even),.dir-slide .dir-shape{{background:{token['softFill']};}}"
        f".dir-slide .dir-shape{{border-color:{token['accent']};}}"
        f".dir-slide .dir-bar,.dir-slide .dir-ambient-mark,.dir-slide .dir-connector-arrow{{background:{token['accent']};}}"
        f".dir-slide.slide-first,.dir-slide.slide-last{{background:{token['strongFill']};color:{token['onAccent']};}}"
        f".dir-slide.slide-first .dir-role-body,.dir-slide.slide-first .dir-role-evidence,.dir-slide.slide-last .dir-role-body,.dir-slide.slide-last .dir-role-evidence{{color:{token['onAccent']};}}"
        f".dir-slide.slide-first .dir-card,.dir-slide.slide-first .dir-process,.dir-slide.slide-first .dir-timeline,.dir-slide.slide-first .dir-step,"
        f".dir-slide.slide-last .dir-card,.dir-slide.slide-last .dir-process,.dir-slide.slide-last .dir-timeline,.dir-slide.slide-last .dir-step{{color:{token['ink']};}}"
    )


def validate_composition_intent(value: Mapping[str, Any], *, grammar: DesignGrammarManifest) -> None:
    reject_raw_model_input(value)
    zones = value.get("zones")
    relations = value.get("relations", [])
    reading_order = value.get("readingOrder")
    if not isinstance(zones, list) or not zones or not isinstance(relations, list) or not isinstance(reading_order, list):
        raise DeckIrGrammarError("composition_intent_graph_invalid")
    owners: dict[str, str] = {}
    zone_keys: set[str] = set()
    for zone in zones:
        item = _as_mapping(zone, reason="composition_intent_graph_invalid")
        key = item.get("zoneKey")
        spans = (item.get("minimumColumnSpan"), item.get("preferredColumnSpan"), item.get("maximumColumnSpan"))
        refs = item.get("contentRefs")
        if not isinstance(key, str) or not key or key in zone_keys or not isinstance(refs, list):
            raise DeckIrGrammarError("composition_intent_graph_invalid")
        if any(isinstance(span, bool) or not isinstance(span, int) or span < 1 or span > 24 for span in spans) or not spans[0] <= spans[1] <= spans[2]:
            raise DeckIrGrammarError("composition_intent_graph_invalid")
        zone_keys.add(key)
        for ref in refs:
            if not isinstance(ref, str) or not _ELEMENT_KEY.fullmatch(ref) or ref in owners:
                raise DeckIrGrammarError("composition_intent_graph_invalid")
            owners[ref] = key
    if set(reading_order) != zone_keys or len(reading_order) != len(zone_keys):
        raise DeckIrGrammarError("composition_intent_graph_invalid")
    graph: dict[str, set[str]] = {key: set() for key in zone_keys}
    for relation in relations:
        item = _as_mapping(relation, reason="composition_intent_graph_invalid")
        source, target, kind, strength = item.get("from"), item.get("to"), item.get("relation"), item.get("strength")
        if source not in zone_keys or target not in zone_keys or source == target or kind not in _SAFE_RELATIONS or strength not in {"hard", "soft"}:
            raise DeckIrGrammarError("composition_intent_graph_invalid")
        if strength == "hard" and kind in {"leftOf", "rightOf", "above", "below", "flow"}:
            if kind in {"rightOf", "below"}:
                source, target = target, source
            graph[source].add(target)
    visiting: set[str] = set()
    visited: set[str] = set()
    def visit(node: str) -> None:
        if node in visiting:
            raise DeckIrGrammarError("composition_intent_graph_invalid")
        if node not in visited:
            visiting.add(node)
            for next_node in graph[node]:
                visit(next_node)
            visiting.remove(node)
            visited.add(node)
    for key in sorted(graph):
        visit(key)


def validate_art_direction_intent(value: Mapping[str, Any], *, grammar: DesignGrammarManifest) -> None:
    reject_raw_model_input(value)
    focal = _as_mapping(value.get("focalAnchor"), reason="art_direction_intent_invalid")
    space = _as_mapping(value.get("negativeSpace"), reason="art_direction_intent_invalid")
    overlap = _as_mapping(value.get("overlapPolicy"), reason="art_direction_intent_invalid")
    choices = {
        "focalStrength": focal.get("strength"), "negativeSpaceBias": space.get("bias"),
        "negativeSpaceAmount": space.get("amount"), "asymmetry": value.get("asymmetry"),
        "overlapMode": overlap.get("mode"), "imageTreatment": value.get("imageTreatment"),
        "cropIntent": value.get("cropIntent"), "typographyRhythm": value.get("typographyRhythm"),
        "depthIntent": value.get("depthIntent"), "visualEnergy": value.get("visualEnergy"),
    }
    if any(item not in grammar.art_direction[key] for key, item in choices.items()):
        raise DeckIrGrammarError("art_direction_intent_invalid")
    if overlap.get("textOcclusion") != "forbidden" or overlap.get("sourceOcclusion") != "forbidden":
        raise DeckIrGrammarError("art_direction_intent_invalid")
    pairs = overlap.get("allowedZonePairs", [])
    if not isinstance(pairs, list) or any(not isinstance(pair, list) or len(pair) != 2 for pair in pairs):
        raise DeckIrGrammarError("art_direction_intent_invalid")


__all__ = ["BALANCED_QUALITY_K", "COMPILER_VERSION", "DesignGrammarManifest", "DeckIrGrammarError", "MAX_PARETO_BEAM_WIDTH", "PrimitiveSpec", "canonical_json", "contract_mapping", "default_design_grammar", "digest", "reject_raw_model_input", "server_owned_stylesheet", "validate_art_direction_intent", "validate_composition_intent", "validate_manifest"]
