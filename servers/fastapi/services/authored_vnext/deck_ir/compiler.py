"""Deterministic server-owned HTML and semantic compiler for one Deck IR slide."""

from __future__ import annotations

from dataclasses import dataclass
from html import escape
import re
from typing import Any, Mapping, Sequence

from .candidates import Candidate
from .component_renderer import COMPONENT_CSS, render_component
from .intent_resolution import verify_resolution
from .zone_layout import solve_zone_grid, connector_direction, ordered_zone_refs
from .grammar import COMPILER_VERSION, DesignGrammarManifest, DeckIrGrammarError, contract_mapping, digest, reject_raw_model_input, server_owned_stylesheet

_ELEMENT_KEY = re.compile(r"^[a-z][a-z0-9]*(?:[._-][a-z0-9]+)*$")
_TAG_BY_KIND = {"text": "p", "metric": "div", "card": "section", "comparison": "section", "process": "ol", "timeline": "ol", "image": "figure", "quote": "blockquote", "table": "table", "chart": "section", "shape": "div", "connector": "div", "group": "section", "decoration": "div"}


@dataclass(frozen=True)
class CompileResult:
    html: str
    semantic: Mapping[str, Any]
    receipt: Mapping[str, Any]


def _qid(slide_key: str, element_key: str) -> str:
    if not _ELEMENT_KEY.fullmatch(element_key):
        raise DeckIrGrammarError("deck_ir_element_key_invalid")
    return f"{slide_key}.{element_key}".lower()




def assert_html_semantic_alignment(html: str, semantic: Mapping[str, Any]) -> None:
    from utils.authored_dom_quality import validate_declared_authored_qids
    if "<script" in html.lower() or re.search(r"\son[a-z]+\s*=", html, flags=re.IGNORECASE) or "<iframe" in html.lower() or "://" in html:
        raise DeckIrGrammarError("compiled_renderer_allowlist_violation")
    qids = re.findall(r'data-qid="([^"]+)"', html)
    semantic_qids = [item.get("qid") for item in semantic.get("elements", []) if isinstance(item, Mapping)]
    if qids != semantic_qids or len(qids) != len(set(qids)):
        raise DeckIrGrammarError("compiled_semantic_mismatch")
    try:
        if list(validate_declared_authored_qids(html)) != qids:
            raise DeckIrGrammarError("compiled_semantic_mismatch")
    except ValueError as error:
        raise DeckIrGrammarError("compiled_semantic_mismatch") from error


def compile_slide(*, operation_id: str, deck_ir: Mapping[str, Any], slide: Mapping[str, Any], grammar: DesignGrammarManifest, candidate: Candidate, selection_receipt: Mapping[str, Any], closure_revision: int = 0, browser_measurement_digest: str | None = None, strategy_decision_digest: str | None = None) -> CompileResult:
    """Compile from a selected feasible candidate; no provider/network call exists here."""

    deck_ir = contract_mapping(deck_ir)
    slide = contract_mapping(slide)
    selection_receipt = contract_mapping(selection_receipt)
    if candidate.hard_constraint_status != "passed":
        raise DeckIrGrammarError("candidate_selection_receipt_invalid")
    if closure_revision < 0 or closure_revision > 2:
        raise DeckIrGrammarError("browser_measurement_closure_failed")
    reject_raw_model_input(deck_ir)
    reject_raw_model_input(slide)
    slide_key = slide.get("slideKey")
    if not isinstance(operation_id, str) or not operation_id or not isinstance(slide_key, str) or candidate.slide_key != slide_key:
        raise DeckIrGrammarError("deck_ir_request_mismatch")
    components = slide.get("components")
    if not isinstance(components, list):
        raise DeckIrGrammarError("deck_ir_component_invalid")
    if len(components) > 12:
        raise DeckIrGrammarError("deck_ir_capacity_unsatisfiable")
    if any(not isinstance(item, Mapping) or not isinstance(item.get("elementKey"), str) or not _ELEMENT_KEY.fullmatch(item["elementKey"]) for item in components):
        raise DeckIrGrammarError("deck_ir_component_invalid")
    verify_resolution(candidate=candidate, slide=slide, grammar=grammar)
    unsigned_selection = {key: value for key, value in selection_receipt.items() if key != "receiptDigest"}
    registered = [item for entry in selection_receipt.get("slides", []) if entry.get("slideKey") == slide_key for item in entry.get("candidates", [])]
    if digest(unsigned_selection) != selection_receipt.get("receiptDigest") or candidate.to_mapping() not in registered:
        raise DeckIrGrammarError("candidate_selection_receipt_invalid")
    seed = digest({"slide": slide, "composition": candidate.composition, "artDirection": candidate.provider_art_intent, "grammar": grammar.manifest_digest})
    if candidate.candidate_digest != digest({"seed": seed, "slideKey": slide_key, "layoutId": candidate.layout_id,
                                            "qualityVector": dict(candidate.quality_vector), "resolutionReceiptDigest": candidate.resolution_receipt["receiptDigest"]}):
        raise DeckIrGrammarError("candidate_selection_receipt_invalid")
    layout_family = candidate.layout_id.split("/", 1)[0]
    body, zone_elements, placement_css, zone_contracts, connector_contracts = _render_zones(slide_key, components, candidate)
    stylesheet = server_owned_stylesheet(grammar, layout_id=candidate.layout_id) + COMPONENT_CSS + placement_css
    root_qid = _qid(slide_key, "root")
    html = f'<!doctype html><html><head><meta charset="utf-8"><style>{stylesheet}</style></head><body><main class="dir-slide layout-family-{escape(layout_family, quote=True)} layout-{escape(candidate.layout_id, quote=True).replace("/", "-")}" data-qid="{root_qid}" data-semantic-role="decoration" aria-label="slide">{body}</main></body></html>'
    semantic: dict[str, Any] = {
        "schemaVersion": "compiled-semantic-source/v1",
        "slideKey": slide_key,
        "elements": [
            {
                "qid": root_qid,
                "elementKey": "root",
                "kind": "shape",
                "semanticRole": "decoration",
                "text": "",
                "assetSlot": None,
                "dataDigest": None,
            },
            *zone_elements,
        ],
    }
    assert_html_semantic_alignment(html, semantic)
    receipt = {
        "schemaVersion": "constraint-compile-receipt/v2", "operationId": operation_id, "slideKey": slide_key,
        "strategyDecisionDigest": strategy_decision_digest or selection_receipt.get("strategyDecisionDigest"),
        "deckIrDigest": digest(deck_ir), "slideIrDigest": digest(slide),
        "compositionIntentGraphDigest": slide.get("compositionIntentGraphDigest"),
        "artDirectionIntentDigest": slide.get("artDirectionIntentDigest"), "grammarDigest": grammar.manifest_digest,
        "compilerVersion": COMPILER_VERSION, "candidateSelectionReceiptDigest": selection_receipt.get("receiptDigest"),
        "chosenLayoutId": candidate.layout_id, "qidRegistryDigest": digest([item["qid"] for item in semantic["elements"]]),
        "htmlDigest": digest(html), "semanticDigest": digest(semantic), "hardConstraintStatus": "passed",
        "softScore": sum(candidate.quality_vector.values()), "measurementClosureRevision": closure_revision,
        "browserMeasurementDigest": browser_measurement_digest, "recoverySteps": [],
        "candidateId": candidate.candidate_id,
        "providerIntentDigest": candidate.resolution_receipt["providerIntentDigest"],
        "resolvedIntentDigest": candidate.resolution_receipt["resolvedIntentDigest"],
        "intentResolutionReceipt": dict(candidate.resolution_receipt),
        "zoneContracts": zone_contracts,
        "compositionRelations": candidate.composition["relations"],
        "connectorContracts": connector_contracts,
        "contentTextDigest": digest([(item["qid"], item["text"]) for item in semantic["elements"] if item["kind"] == "text"]),
    }
    receipt = {**receipt, "receiptDigest": digest(receipt)}
    from .contracts import ConstraintCompileReceiptV2
    ConstraintCompileReceiptV2.model_validate(receipt)
    return CompileResult(html, semantic, receipt)


def _render_zones(slide_key, components, candidate):
    """Pack typed zones into disjoint grid cells; no provider geometry/CSS."""
    by_key = {item["elementKey"]: item for item in components}
    zones = {item["zoneKey"]: item for item in candidate.composition["zones"]}
    family = candidate.layout_id.split("/", 1)[0]
    placements = solve_zone_grid(candidate.composition, components, family)
    refs_by_zone = {key: ordered_zone_refs(zone, by_key) for key, zone in zones.items()}
    css, body, elements, contracts, connectors = [], [], [], [], []
    row_weights = {}
    for index, key in enumerate(candidate.composition["readingOrder"]):
        zone = zones[key]
        refs = refs_by_zone[key]
        placement = placements[key]
        row, column, span = placement["row"], placement["column"], placement["span"]
        zone_qid = _qid(slide_key, f"zone.{key}")
        elements.append({"qid": zone_qid, "elementKey": f"zone.{key}", "kind": "shape", "semanticRole": "decoration", "text": "", "assetSlot": None, "dataDigest": None})
        direction = "row" if zone.get("flow") in {"horizontal", "cluster"} else "column"
        body.append(f'<section class="dir-zone dir-zone-{index} dir-zone-{direction}" data-qid="{zone_qid}" data-semantic-role="decoration">')
        for ref in refs:
            component = by_key[ref]
            orientation = connector_direction(component, zones, placements, refs_by_zone) if component["kind"] == "connector" else None
            html, semantic_elements = render_component(slide_key, component, connector_direction=orientation)
            if orientation is not None:
                connectors.append({"qid": f"{slide_key}.{ref}", "fromQid": f"{slide_key}.{component['payload']['fromElementKey']}",
                                   "toQid": f"{slide_key}.{component['payload']['toElementKey']}", "direction": orientation})
            body.append(html)
            elements.extend(semantic_elements)
        body.append("</section>")
        weights = {"table": 1, "chart": 1, "process": 1, "timeline": 1, "metric": 1, "card": 1}
        weight = max(weights.get(by_key[ref]["kind"], 0) for ref in refs)
        row_weights[row] = max(row_weights.get(row, 0), weight)
        css.append(f'.dir-slide .dir-zone-{index}{{grid-column:{column}/span {span};grid-row:{row}/span {placement["rowSpan"]};flex-direction:{direction};}}')
        contracts.append({"zoneKey": key, "qid": zone_qid, "contentQids": [f"{slide_key}.{ref}" for ref in refs],
                          "minimumColumnSpan": zone["minimumColumnSpan"], "maximumColumnSpan": zone["maximumColumnSpan"],
                          "capability": next(item["capability"] for item in candidate.resolved_art_intent["zoneCapabilities"] if item["zoneKey"] == key)})
    rows = " ".join("minmax(min-content,1fr)" if row_weights[index] else "auto" for index in sorted(row_weights))
    css.append(f'.dir-slide.layout-family-{family}{{grid-template-columns:repeat(12,minmax(0,1fr));grid-template-rows:{rows};}}')
    css.append('.dir-zone{min-width:0;min-height:0;display:flex;flex-direction:column;justify-content:center;gap:16px;position:relative;}')
    css.append('.dir-slide .dir-zone>.dir-component{grid-column:auto;min-height:0;max-height:none;flex-shrink:0;width:100%;}')
    css.append('.dir-slide .dir-zone-row>.dir-component{flex:1 1 0;width:0;min-width:0;}')
    css.append('.dir-slide .dir-zone-row>.dir-connector{flex:0.45 1 0;align-self:center;}')
    css.append('.dir-slide .dir-zone .dir-role-title{align-self:auto;}')
    return "".join(body), elements, "".join(css), contracts, connectors


__all__ = ["CompileResult", "assert_html_semantic_alignment", "compile_slide"]
