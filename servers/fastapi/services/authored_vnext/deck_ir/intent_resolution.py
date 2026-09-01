"""Compiler-owned overlap capabilities; provider proposals never grant authority."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import math
from typing import Any, Mapping

from .grammar import DeckIrGrammarError, DesignGrammarManifest, digest


class OverlapCapability(str, Enum):
    EXCLUSIVE_CONTENT = "exclusive-content"
    OVERLAY_HOST = "overlay-host/backplate"
    ANNOTATION = "annotation"
    AMBIENT = "ambient"


@dataclass(frozen=True)
class ProviderArtIntent:
    """An immutable snapshot, not a compiler permission object."""

    intent: Mapping[str, Any]
    intent_digest: str


@dataclass(frozen=True)
class ResolvedArtIntent:
    intent: Mapping[str, Any]
    receipt: Mapping[str, Any]


def resolve_art_intent(*, slide: Mapping[str, Any], composition: Mapping[str, Any],
                       proposal: Mapping[str, Any], grammar: DesignGrammarManifest,
                       layout_id: str) -> ResolvedArtIntent:
    # Role strings (including "background") are untrusted. Only a registered
    # component renderer may mint a capability. Image-safe regions/ownership
    # are not available in this source-free compiler, so no image overlay is
    # authorized here. It must take the separated path or fail capability checks.
    provider = ProviderArtIntent(dict(proposal), digest(proposal))
    components = {item["elementKey"]: item for item in slide["components"]}
    zones = {item["zoneKey"]: item for item in composition["zones"]}
    if proposal["focalAnchor"]["zoneKey"] not in zones:
        raise DeckIrGrammarError("art_direction_intent_invalid")
    owners: dict[str, str] = {}
    capabilities = []
    for key, zone in zones.items():
        refs = zone["contentRefs"]
        if any(ref not in components or ref in owners for ref in refs):
            raise DeckIrGrammarError("composition_intent_content_owner_invalid")
        owners.update({ref: key for ref in refs})
        ambient = all(components[ref]["kind"] == "decoration" for ref in refs)
        capabilities.append({"zoneKey": key, "contentRefs": list(refs),
                             "capability": (OverlapCapability.AMBIENT if ambient else
                                            OverlapCapability.EXCLUSIVE_CONTENT).value})
    if set(owners) != set(components):
        raise DeckIrGrammarError("composition_intent_content_owner_invalid")
    decisions = []
    for first, second in proposal["overlapPolicy"].get("allowedZonePairs", []):
        if first not in zones or second not in zones or first == second:
            raise DeckIrGrammarError("art_direction_intent_invalid")
        decisions.append({"zonePair": [first, second], "decision": "separate",
                          "reason": "compiler_owned_overlay_evidence_unavailable" if
                          any(item["capability"] == "ambient" and item["zoneKey"] in (first, second)
                              for item in capabilities) else "exclusive_content_overlap_forbidden"})
    # Retain all expressive fields and all content. Only permissions are
    # resolved. This is a new object; source IR/digests are never resealed.
    resolved = {key: value for key, value in provider.intent.items() if key != "intentDigest"}
    resolved["overlapPolicy"] = {"mode": "none", "allowedZonePairs": [],
                                 "textOcclusion": "forbidden", "sourceOcclusion": "forbidden"}
    resolved.update({"schemaVersion": "resolved-art-intent/v1", "layoutId": layout_id,
                     "zoneCapabilities": capabilities})
    receipt = {"schemaVersion": "art-intent-resolution-receipt/v1", "slideKey": slide["slideKey"],
               "providerIntentDigest": provider.intent_digest, "resolvedIntentDigest": digest(resolved),
               "slideDigest": digest(slide), "compositionDigest": digest(composition),
               "grammarDigest": grammar.manifest_digest, "layoutId": layout_id,
               "pairDecisions": decisions, "contentPreserved": True, "providerCallsAdded": 0}
    return ResolvedArtIntent(resolved, {**receipt, "receiptDigest": digest(receipt)})


def verify_resolution(*, candidate: Any, slide: Mapping[str, Any], grammar: DesignGrammarManifest) -> None:
    expected = resolve_art_intent(slide=slide, composition=candidate.composition,
                                  proposal=candidate.provider_art_intent, grammar=grammar,
                                  layout_id=candidate.layout_id)
    if expected.intent != candidate.resolved_art_intent or expected.receipt != candidate.resolution_receipt:
        raise DeckIrGrammarError("art_intent_resolution_digest_mismatch")


def browser_constraint_checks(compiled: Mapping[str, Any], metrics: Mapping[str, Any]) -> dict[str, bool]:
    """Check content, ownership and hard relations against observed leaf boxes."""
    receipt, semantic = compiled["compileReceipt"], compiled["semantic"]
    observed = {item["qid"]: item for item in metrics["elements"] if isinstance(item.get("qid"), str)}
    zones = {item["zoneKey"]: item for item in receipt["zoneContracts"]}
    checks = {key: True for key in ("contentPreservation", "ownedContainment", "exclusiveZoneOverlap", "compositionHardRelations", "zoneSpanBounds", "textSafetyTelemetry")}

    def box(qid):
        value = observed.get(qid, {}).get("bbox", {})
        numbers = tuple(value.get(key) for key in ("x", "y", "width", "height"))
        if not all(isinstance(number, (int, float)) and math.isfinite(number) for number in numbers) or numbers[2] <= 0 or numbers[3] <= 0:
            return None
        x, y, width, height = numbers
        return x, y, x + width, y + height

    for element in semantic["elements"]:
        if element["kind"] != "text":
            continue
        item = observed.get(element["qid"], {})
        text = item.get("rendered_text", item.get("text", item.get("text_content")))
        checks["contentPreservation"] &= isinstance(text, str) and " ".join(text.split()) == " ".join(element["text"].split())
        if element["text"].strip():
            font, contrast, occlusion = item.get("font_size"), item.get("contrast_ratio"), item.get("occluded_fraction")
            checks["textSafetyTelemetry"] &= bool(box(element["qid"])) and isinstance(font, (int, float)) and font >= 15 and isinstance(contrast, (int, float)) and contrast >= (3 if font >= 24 else 4.5) and isinstance(occlusion, (int, float)) and occlusion <= 0.01
    boxes = {key: box(zone["qid"]) for key, zone in zones.items()}
    for key, zone in zones.items():
        bounds = boxes[key]
        if bounds is None:
            checks["ownedContainment"] = checks["zoneSpanBounds"] = False
            continue
        span = (bounds[2] - bounds[0] + 20) / (1152 + 20) * 12
        checks["zoneSpanBounds"] &= zone["minimumColumnSpan"] - .05 <= span <= zone["maximumColumnSpan"] + .05
        for qid in zone["contentQids"]:
            child = box(qid)
            checks["ownedContainment"] &= child is not None and child[0] >= bounds[0] - 2 and child[1] >= bounds[1] - 2 and child[2] <= bounds[2] + 2 and child[3] <= bounds[3] + 2
    for index, first in enumerate(boxes.values()):
        for second in list(boxes.values())[index + 1:]:
            checks["exclusiveZoneOverlap"] &= first is not None and second is not None and (min(first[2], second[2]) <= max(first[0], second[0]) + 2 or min(first[3], second[3]) <= max(first[1], second[1]) + 2)
    for edge in receipt["compositionRelations"]:
        if edge["strength"] != "hard":
            continue
        first, second = boxes.get(edge["from"]), boxes.get(edge["to"])
        if first is None or second is None:
            checks["compositionHardRelations"] = False
            continue
        relation = edge["relation"]
        facts = {"leftOf": first[2] <= second[0] + 2, "rightOf": second[2] <= first[0] + 2,
                 "above": first[3] <= second[1] + 2, "below": second[3] <= first[1] + 2,
                 "alignTop": abs(first[1] - second[1]) <= 2,
                 "alignCenter": abs((first[1] + first[3]) - (second[1] + second[3])) <= 4,
                 "equalWidth": abs((first[2] - first[0]) - (second[2] - second[0])) <= 2,
                 "flow": first[3] <= second[1] + 2 or (abs(first[1] - second[1]) <= 2 and first[2] <= second[0] + 2),
                 "emphasis": (first[2] - first[0]) * (first[3] - first[1]) >= (second[2] - second[0]) * (second[3] - second[1])}
        checks["compositionHardRelations"] &= facts.get(relation, False)
    checks["connectorEndpointOrder"] = True
    for connector in receipt.get("connectorContracts", []):
        first, middle, last = (box(connector[key]) for key in ("fromQid", "qid", "toQid"))
        if first is None or middle is None or last is None:
            checks["connectorEndpointOrder"] = False
            continue
        facts = {"right": first[2] <= middle[0] + 2 and middle[2] <= last[0] + 2,
                 "left": last[2] <= middle[0] + 2 and middle[2] <= first[0] + 2,
                 "down": first[3] <= middle[1] + 2 and middle[3] <= last[1] + 2,
                 "up": last[3] <= middle[1] + 2 and middle[3] <= first[1] + 2}
        checks["connectorEndpointOrder"] &= facts[connector["direction"]]
    return checks
