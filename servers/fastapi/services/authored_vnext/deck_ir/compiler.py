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


def compile_slide(*, operation_id: str, deck_ir: Mapping[str, Any], slide: Mapping[str, Any], grammar: DesignGrammarManifest, candidate: Candidate, selection_receipt: Mapping[str, Any], closure_revision: int = 0, browser_measurement_digest: str | None = None, strategy_decision_digest: str | None = None, authored_style: str | None = None, style_profile_digest: str | None = None) -> CompileResult:
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
    profile = _composition_profile(deck_ir, slide)
    reference_frame = _reference_frame(profile, authored_style)
    stylesheet = server_owned_stylesheet(grammar, layout_id=candidate.layout_id) + COMPONENT_CSS + placement_css + _EDITORIAL_CSS + (reference_frame["css"] if reference_frame else "")
    root_qid = _qid(slide_key, "root")
    furniture_items = _reference_furniture(slide_key, profile) if reference_frame else []
    furniture = ''.join(item["html"] for item in furniture_items)
    deck_slides = deck_ir.get("slides")
    slide_index = (
        next(
            (index for index, item in enumerate(deck_slides) if isinstance(item, Mapping) and item.get("slideKey") == slide_key),
            -1,
        )
        if isinstance(deck_slides, list)
        else -1
    )
    position_class = (
        " slide-first"
        if slide_index == 0
        else " slide-last"
        if isinstance(deck_slides, list) and slide_index == len(deck_slides) - 1
        else ""
    )
    html = f'<!doctype html><html><head><meta charset="utf-8"><style>{stylesheet}</style></head><body><main class="dir-slide composition-{profile} dir-variant-{candidate.variant_id} layout-family-{escape(layout_family, quote=True)} layout-{escape(candidate.layout_id, quote=True).replace("/", "-")}{position_class}" data-qid="{root_qid}" data-semantic-role="decoration" aria-label="slide">{furniture}{body}</main></body></html>'
    if reference_frame and profile == "chart":
        # The release-review preinspection canonicalizes this coordinate-bearing
        # wrapper to explicit zero offsets. Emit the same stable form up front
        # so a no-op review cannot drift the persisted HTML identity.
        html = html.replace(
            'class="dir-gate-outcomes">',
            'class="dir-gate-outcomes" style="padding-top: 0.0000px; padding-left: 0.0000px">',
        )
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
            *[item["semantic"] for item in furniture_items],
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
        "zoneContracts": ([{**item, "minimumColumnSpan": 1, "maximumColumnSpan": 12} for item in zone_contracts] if reference_frame else zone_contracts),
        "compositionRelations": ([] if reference_frame else candidate.composition["relations"]),
        "connectorContracts": connector_contracts,
        "contentTextDigest": digest([(item["qid"], item["text"]) for item in semantic["elements"] if item["kind"] == "text"]),
        "styleProfileDigest": style_profile_digest,
    }
    if reference_frame:
        receipt["referenceFrame"] = {key: value for key, value in reference_frame.items() if key != "css"}
    receipt = {**receipt, "receiptDigest": digest(receipt)}
    from .contracts import ConstraintCompileReceiptV2
    ConstraintCompileReceiptV2.model_validate(receipt)
    return CompileResult(html, semantic, receipt)


def _composition_profile(deck, slide):
    """Bounded renderer roles inferred from typed content, never provider CSS."""
    kinds = {item["kind"] for item in slide["components"]}
    if any(item["kind"] == "text" and item["semanticRole"] == "title"
           and item["payload"]["text"] == deck.get("title") for item in slide["components"]):
        return "cover"
    for kind, profile in (("table", "table"), ("chart", "chart"), ("timeline", "roadmap"), ("process", "architecture")):
        if kind in kinds:
            return profile
    return "narrative"


_REFERENCE_STYLE_ID = "legacy-reference-current-v1"
_REFERENCE_FRAME_DIGESTS = {
    "cover": "sha256:69104402a23289fe6a04e7b42f547adebba9eec5c9071e4d7b5b3556ff22ee22",
    "table": "sha256:5855854e15213abf9383d23e2bfae2836082eb58d6b261bfc01e96d1114287dd",
    "architecture": "sha256:28e6b7454bce2be56bc9ddd3f703ede08e6d05fe3c2e1c5119abea00ee8f326b",
    "roadmap": "sha256:544f677e977ec3b1ed6ded6bc56f89cbb54fbbe9d78198f746ea93b4789e0ca1",
    "chart": "sha256:4000ef600445823d7a8e98f45b14ac50682d5b46279be3f4747ed5be4e53bbe4",
}
_REFERENCE_SOURCE_SLIDE = {"cover": 1, "table": 8, "architecture": 3, "roadmap": 4, "chart": 9}


def _reference_frame(profile: str, authored_style: str | None) -> dict[str, Any] | None:
    """Select an opt-in, server-owned frame derived from the retained HTML."""
    if authored_style != _REFERENCE_STYLE_ID:
        return None
    source_slide = _REFERENCE_SOURCE_SLIDE.get(profile)
    source_digest = _REFERENCE_FRAME_DIGESTS.get(profile)
    if source_slide is None or source_digest is None:
        raise DeckIrGrammarError("reference_frame_profile_unsupported")
    css = _REFERENCE_FRAME_CSS + _REFERENCE_PROFILE_CSS[profile]
    return {
        "schemaVersion": "retained-reference-frame/v1",
        "styleId": _REFERENCE_STYLE_ID,
        "profile": profile,
        "sourceSlide": source_slide,
        "sourceHtmlDigest": source_digest,
        "frameCssDigest": digest(css),
        "contentMode": "typed-deck-ir-slots",
        "css": css,
    }


def _reference_furniture(slide_key: str, profile: str) -> list[dict[str, Any]]:
    items = [
        ("ref.tick", "ref-tick", ""),
        ("ref.eyebrow", "ref-eyebrow", "AUTHORED BASELINE"),
        ("ref.wordmark", "ref-wordmark", "Authored baseline"),
    ]
    if profile == "architecture":
        items.append(("ref.backplate", "ref-backplate", ""))
    if profile in {"table", "architecture", "chart"}:
        items.append(("ref.rail", "ref-rail", ""))
    if profile == "roadmap":
        items.append(("ref.year", "ref-year", "2026"))
    result = []
    for element_key, css_class, value in items:
        qid = _qid(slide_key, element_key)
        result.append({
            "html": f'<div class="{css_class}" data-qid="{qid}" data-semantic-role="decoration" aria-hidden="true">{escape(value)}</div>',
            "semantic": {"qid": qid, "elementKey": element_key, "kind": "shape", "semanticRole": "decoration", "text": value, "assetSlot": None, "dataDigest": None},
        })
    return result


_REFERENCE_FRAME_CSS = r"""
.dir-slide{background:#fff;color:#0F172A;font-family:'Malgun Gothic',Arial,sans-serif;border-top:0!important}
.ref-tick{position:absolute;left:64px;top:59px;z-index:20;width:28px;height:3px;background:#0B4BCC;color:#fff}.ref-eyebrow{position:absolute;left:104px;top:54px;z-index:20;color:#0B4BCC;font-size:15px;font-weight:800;letter-spacing:.12em}.ref-wordmark{position:absolute;right:64px;top:56px;z-index:20;color:#334155;font-size:15px;font-weight:700;letter-spacing:.02em}.ref-backplate,.ref-year,.ref-rail{display:none;position:absolute}
.dir-slide>.dir-zone{z-index:2}.dir-slide .dir-role-title{color:#0F172A;font-weight:900;letter-spacing:-.045em}
"""

_REFERENCE_PROFILE_CSS = {
"cover": r"""
.composition-cover.dir-slide{padding:58px 54px 46px 64px;grid-template-columns:620px 1fr;grid-template-rows:520px 96px;column-gap:70px;row-gap:0;background:#fff}.composition-cover .ref-wordmark{right:54px;color:#fff;background:#0F62C7}.composition-cover .dir-zone:has(.dir-role-title){grid-column:1!important;grid-row:1/3!important;padding-top:78px!important}.composition-cover .dir-zone:has(.dir-metric){grid-column:2!important;grid-row:1!important;padding:72px 44px 8px!important;color:#fff;background:#0F62C7!important;border:0!important;border-radius:0!important;box-shadow:0 0 0 10px #0F62C7!important}.composition-cover .dir-zone:has(.dir-role-visual-motif){grid-column:2!important;grid-row:2!important;padding:8px 44px 18px!important;color:#fff!important;background:#0F62C7!important;border:0!important;border-radius:0!important;box-shadow:0 0 0 10px #0F62C7!important}.composition-cover .dir-role-visual-motif{color:#fff!important}.composition-cover .dir-ambient-mark{background:#fff!important}.composition-cover .dir-role-title{font-size:58px!important;line-height:1.28!important;max-height:4.7em!important;padding-bottom:8px}.composition-cover .dir-zone:has(.dir-role-title) .dir-component:not(.dir-role-title){color:#334155!important}.composition-cover .dir-zone:has(.dir-role-title) .dir-card{padding:16px!important;gap:6px!important}
.composition-cover .dir-metric{background:transparent!important;border-radius:0!important;color:#fff!important;border-bottom:1px solid rgba(255,255,255,.34)!important;padding:16px 0!important;min-height:130px!important}.composition-cover .dir-metric-rule{display:none!important}.composition-cover .dir-metric-value,.composition-cover .dir-metric-unit,.composition-cover .dir-metric-label{color:#fff!important}.composition-cover .dir-metric-label{margin-left:4px!important;margin-right:4px!important}
""",
"table": r"""
.composition-table.dir-slide{padding:54px 64px 32px;grid-template-columns:472px 1fr;grid-template-rows:122px 1fr 116px;background:#fff}.composition-table .ref-rail{display:block;left:0;top:0;width:8px;height:720px;background:#1473E6;color:#fff;z-index:0}.composition-table .dir-zone:has(.dir-role-title){grid-column:1!important;grid-row:1/4!important;padding:42px 24px 18px 0!important;align-self:stretch!important;background:#F8FAFC!important;box-shadow:-100px 0 0 100px #F8FAFC!important}.composition-table .dir-role-title{font-size:48px!important;line-height:1.28!important}.composition-table .dir-zone:has(.dir-table){grid-column:2!important;grid-row:1/4!important;justify-content:flex-start!important;padding-top:30px!important}.composition-table .dir-table{font-size:15px!important;border-radius:18px!important;box-shadow:0 16px 30px rgba(15,23,42,.08);color:#334155!important}.composition-table .dir-table th{background:#0F62C7!important;color:#fff!important;padding:14px!important;font-size:16px!important}.composition-table .dir-table td{padding:12px 14px!important;color:#334155!important}
.composition-table .dir-zone:has(.dir-role-evidence){grid-column:1!important;grid-row:3!important;padding:0 24px 0 0!important;align-self:end!important}
.composition-table:has([class*="qualification"]) .dir-zone:has(.dir-role-title){grid-row:1/3!important}.composition-table .dir-zone:has([class*="qualification"]){grid-column:1!important;grid-row:3!important;padding:0 24px 0 0!important;align-self:end!important;color:#334155!important}
""",
"architecture": r"""
.composition-architecture.dir-slide{padding:54px 64px 42px;gap:12px;grid-template-rows:232px 300px 62px;background:#fff}.composition-architecture .ref-rail{display:block;left:0;top:0;width:18px;height:720px;background:#1473E6;color:#fff;z-index:0}.composition-architecture:has(.dir-comparison-columns),.composition-architecture:has(>.dir-zone>.dir-card:nth-of-type(3)){grid-template-rows:100px 300px 194px}.composition-architecture .dir-zone:has(.dir-role-title){grid-column:1/-1!important;grid-row:1!important;padding-top:34px!important}.composition-architecture .dir-role-title{width:max-content!important;max-width:850px!important;font-size:46px!important;line-height:1.24!important}.composition-architecture .dir-zone:has(.dir-process){grid-column:1/-1!important;grid-row:2!important}.composition-architecture .dir-zone:has(.dir-comparison-columns){grid-column:1/span 7!important;grid-row:3!important}.composition-architecture .dir-zone:has(.dir-comparison-columns)+.dir-zone:has(.dir-card),.composition-architecture .dir-zone:has(.dir-card){grid-column:8/span 5!important;grid-row:3!important}.composition-architecture .dir-zone:has(>.dir-card:nth-of-type(3)){grid-column:1/-1!important}.composition-architecture .dir-zone:has(.dir-role-evidence){color:#334155!important}
.composition-architecture .ref-backplate{display:block;z-index:0;left:64px;right:64px;top:296px;bottom:92px;background:linear-gradient(90deg,#0F62C7 0 31%,transparent 31% 34.5%,#F4F8FD 34.5% 65.5%,transparent 65.5% 69%,#F4F8FD 69%);border:1px solid #D4DEE9;border-radius:28px;box-shadow:0 12px 24px rgba(15,23,42,.08)}.composition-architecture:has(.dir-comparison-columns) .ref-backplate,.composition-architecture:has(>.dir-zone>.dir-card:nth-of-type(3)) .ref-backplate{top:166px;bottom:238px}.composition-architecture .dir-process-serpentine{grid-template-columns:1fr 18px 1fr 18px 1fr!important;grid-template-rows:1fr 1fr!important;gap:0!important;position:relative}
.composition-architecture .dir-step{z-index:1;border:0!important;background:transparent!important;border-radius:0!important;min-height:0!important;padding:22px 24px!important;color:#334155!important}.composition-architecture .dir-step-grid-1,.composition-architecture .dir-step-grid-2{grid-column:1!important;background:#0F62C7!important;color:#fff!important}.composition-architecture .dir-step-grid-1{grid-row:1!important;border-radius:26px 0 0 0!important}.composition-architecture .dir-step-grid-2{grid-row:2!important;border-radius:0 0 0 26px!important}.composition-architecture .dir-step-grid-3,.composition-architecture .dir-step-grid-4{grid-column:3!important}.composition-architecture .dir-step-grid-3{grid-row:1!important}.composition-architecture .dir-step-grid-4{grid-row:2!important}.composition-architecture .dir-step-grid-5,.composition-architecture .dir-step-grid-6{grid-column:5!important}.composition-architecture .dir-step-grid-5{grid-row:1!important}.composition-architecture .dir-step-grid-6{grid-row:2!important}.composition-architecture .dir-step *{color:#1E365E!important}.composition-architecture .dir-step-grid-1 *,.composition-architecture .dir-step-grid-2 *{color:#fff!important}.composition-architecture .dir-step-detail{font-size:15px!important;line-height:1.28!important}.composition-architecture .dir-sequence-arrow{display:none!important}
.composition-architecture .dir-comparison-columns{gap:10px!important}.composition-architecture .dir-comparison-columns ul{padding:8px 10px 8px 26px!important;font-size:15px!important;line-height:1.22!important}.composition-architecture .dir-comparison-columns li+li{margin-top:2px!important}.composition-architecture .dir-zone:has(.dir-card){justify-content:center!important}.composition-architecture .dir-zone:has(.dir-card) .dir-card{padding:10px 14px!important;gap:4px!important;font-size:15px!important}.composition-architecture .dir-zone:has(.dir-card) .dir-card>strong{font-size:16px!important}.composition-architecture .dir-zone:has(.dir-card) .dir-card>p{font-size:16px!important;line-height:1.3!important}
.composition-architecture .dir-zone:has([class*="qualification"]){grid-column:8/-1!important;grid-row:3!important;padding:12px 0 0 12px!important;align-self:stretch!important;color:#334155!important}
.composition-architecture .dir-zone:has(.dir-role-title):has(.dir-role-thesis){display:grid!important;grid-template-columns:minmax(0,1.5fr) minmax(0,1fr)!important;column-gap:24px!important;align-items:start!important;padding-top:28px!important}.composition-architecture .dir-zone:has(.dir-role-title):has(.dir-role-thesis) .dir-role-thesis{font-size:16px!important;line-height:1.35!important;color:#475569!important;padding-top:6px!important}
.composition-architecture .dir-zone:has(.dir-role-title):has(>.dir-component:nth-of-type(2)){display:grid!important;grid-template-columns:minmax(0,1.5fr) minmax(0,1fr)!important;column-gap:24px!important;align-items:start!important;padding-top:28px!important}.composition-architecture .dir-zone:has(.dir-role-title):has(>.dir-component:nth-of-type(2))>.dir-component:not(.dir-role-title){font-size:16px!important;line-height:1.35!important;color:#475569!important;padding-top:6px!important}.composition-architecture:has(.dir-zone [class*="qualification"]) .dir-zone:has(>.dir-card:nth-of-type(3)){grid-column:1/span 7!important}
""",
"roadmap": r"""
.composition-roadmap.dir-slide{background:#fff}.composition-roadmap .dir-zone:has(.dir-role-title){padding-top:30px!important}.composition-roadmap .dir-role-title{font-size:48px!important;line-height:1.28!important}.composition-roadmap .ref-year{display:block;left:64px;top:212px;color:#1473E6;font-size:58px;font-weight:900;z-index:3}.composition-roadmap .dir-step:last-of-type{background:#0F62C7!important;color:#fff!important}.composition-roadmap .dir-step:last-of-type *{color:#fff!important}.composition-roadmap .dir-component:not(.dir-role-title){color:#334155}
""",
"chart": r"""
.composition-chart.dir-slide{padding:54px 64px 16px;gap:0;grid-template-columns:200px 1fr;grid-template-rows:350px 204px 96px;background:#fff}.composition-chart .ref-rail{display:block;left:0;top:0;width:294px;height:720px;background:#F4F8FD;color:#0F172A;z-index:0}.composition-chart .dir-zone:has(.dir-role-title){grid-column:1!important;grid-row:1!important;width:196px!important;justify-content:flex-start!important;padding-top:46px!important}.composition-chart .dir-role-title{font-size:42px!important;line-height:1.28!important}.composition-chart .dir-zone:has(.dir-process){grid-column:2!important;grid-row:2!important;justify-content:flex-start!important;padding-left:22px!important}.composition-chart .dir-conditional-gate{display:grid!important;grid-template-columns:1fr 1.5fr 1fr!important;gap:16px!important;align-items:stretch!important;height:162px!important}.composition-chart .dir-gate-entry{grid-column:1!important;width:auto!important;padding:18px!important}.composition-chart .dir-gate-stem,.composition-chart .dir-gate-branch,.composition-chart .dir-gate-outcome-dot{display:none!important}.composition-chart .dir-gate-outcomes{display:contents!important}.composition-chart .dir-gate-outcome{position:static!important;min-height:0!important;padding:18px!important}.composition-chart .dir-gate-outcome-positive{grid-column:2!important}.composition-chart .dir-gate-outcome-negative{grid-column:3!important}.composition-chart .dir-zone:has(.dir-chart){grid-column:2!important;grid-row:3!important;justify-content:flex-start!important;padding-left:22px!important;padding-right:16px!important;gap:0!important}.composition-chart .dir-chart{padding:0!important;border:0!important;background:#fff!important;color:#334155!important;font-size:15px!important}.composition-chart .dir-chart-caption,.composition-chart .dir-series-name{display:block!important;margin:0!important;font-size:15px!important;line-height:15px!important}.composition-chart .dir-bar-row{grid-template-columns:minmax(150px,1.1fr) minmax(0,4fr) 40px!important;gap:8px!important;margin-top:2px!important;font-size:15px!important;line-height:15px!important}.composition-chart .dir-bar-track{height:14px!important}.composition-chart .dir-zone:has([class*="qualification"]){grid-column:1!important;grid-row:2/4!important;width:196px!important;color:#334155!important;justify-content:flex-end!important;padding-bottom:8px!important}.composition-chart .dir-zone:has(.dir-chart):has([class*="qualification"]){width:196px!important;padding-left:0!important;padding-right:0!important}.composition-chart .dir-zone:has(.dir-chart):has([class*="qualification"]) .dir-chart,.composition-chart .dir-zone:has(.dir-chart):has([class*="qualification"]) .dir-chart-caption,.composition-chart .dir-zone:has(.dir-chart):has([class*="qualification"]) .dir-series-name{font-size:16px!important;line-height:20px!important}.composition-chart .dir-zone:has(.dir-chart):has([class*="qualification"]) .dir-bar-row{grid-template-columns:100px minmax(0,1fr) 30px!important;gap:4px!important;font-size:16px!important;line-height:20px!important}.composition-chart .dir-zone:has(.dir-chart):has([class*="qualification"]) .dir-chart-value{font-size:16px!important}.composition-chart [class*="qualification"]{font-size:15px!important;line-height:1.25!important}.composition-chart .dir-component:not(.dir-role-title){color:#334155!important}
""",
}


def _render_zones(slide_key, components, candidate):
    """Pack typed zones into disjoint grid cells; no provider geometry/CSS."""
    by_key = {item["elementKey"]: item for item in components}
    zones = {item["zoneKey"]: item for item in candidate.composition["zones"]}
    family = candidate.layout_id.split("/", 1)[0]
    placements = solve_zone_grid(candidate.composition, components, family,
                                 variant=candidate.variant_id, art_direction=candidate.resolved_art_intent)
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
        kinds = {by_key[ref]["kind"] for ref in refs}
        has_title = any(by_key[ref]["semanticRole"] == "title" for ref in refs)
        treatment = "title" if has_title else "note" if kinds <= {"text", "decoration"} else "content"
        zone_classes = f"dir-zone dir-zone-{index} dir-zone-{direction} dir-zone-{treatment}"
        process_led = any(other.get("visualWeight") == "dominant" and any(
            by_key[ref]["kind"] == "process" for ref in other["contentRefs"]
        ) for other in zones.values())
        if kinds == {"card"} and (zone.get("visualWeight") == "supporting" or process_led):
            zone_classes += " dir-zone-supporting-cards"
        if "metric" in kinds and kinds <= {"metric", "text", "decoration"} and not has_title:
            zone_classes += " dir-zone-metrics"
        if len(refs) == 1:
            zone_classes += " dir-zone-single"
        body.append(f'<section class="{zone_classes}" data-qid="{zone_qid}" data-semantic-role="decoration">')
        for ref in refs:
            component = by_key[ref]
            orientation = connector_direction(component, zones, placements, refs_by_zone) if component["kind"] == "connector" else None
            available_columns = span / len(refs) if direction == "row" else span
            html, semantic_elements = render_component(slide_key, component, connector_direction=orientation,
                                                       narrow=available_columns <= 6)
            if orientation is not None:
                connectors.append({"qid": f"{slide_key}.{ref}", "fromQid": f"{slide_key}.{component['payload']['fromElementKey']}",
                                   "toQid": f"{slide_key}.{component['payload']['toElementKey']}", "direction": orientation})
            body.append(html)
            elements.extend(semantic_elements)
        body.append("</section>")
        weights = {"table": 1, "chart": 1, "process": 1, "timeline": 1, "metric": 1, "card": 1}
        weight = max(weights.get(by_key[ref]["kind"], 0) for ref in refs)
        row_weights[row] = max(row_weights.get(row, 0), placement.get("rowWeight", weight))
        css.append(f'.dir-slide .dir-zone-{index}{{grid-column:{column}/span {span};grid-row:{row}/span {placement["rowSpan"]};flex-direction:{direction};}}')
        contracts.append({"zoneKey": key, "qid": zone_qid, "contentQids": [f"{slide_key}.{ref}" for ref in refs],
                          "minimumColumnSpan": zone["minimumColumnSpan"], "maximumColumnSpan": zone["maximumColumnSpan"],
                          "capability": next(item["capability"] for item in candidate.resolved_art_intent["zoneCapabilities"] if item["zoneKey"] == key)})
    rows = " ".join(f"minmax(min-content,{row_weights[index]}fr)" if row_weights[index] else "auto" for index in sorted(row_weights))
    css.append(f'.dir-slide.layout-family-{family}{{grid-template-columns:repeat(12,minmax(0,1fr));grid-template-rows:{rows};}}')
    css.append('.dir-zone{min-width:0;min-height:0;display:flex;flex-direction:column;justify-content:center;gap:16px;position:relative;}')
    css.append('.dir-slide .dir-zone>.dir-component{grid-column:auto;min-height:0;max-height:none;flex-shrink:0;width:100%;}')
    css.append('.dir-slide .dir-zone-row>.dir-component{flex:1 1 0;width:0;min-width:0;}')
    css.append('.dir-slide .dir-zone-row>.dir-connector{flex:0.45 1 0;align-self:center;}')
    css.append('.dir-slide .dir-zone .dir-role-title{align-self:auto;}')
    return "".join(body), elements, "".join(css), contracts, connectors


_EDITORIAL_CSS = """
.dir-slide{border-top:6px solid #176FE5;}
.dir-zone-title{min-height:min-content;justify-content:flex-start;padding-bottom:16px;border-bottom:1px solid #DCE6F3;}
.dir-zone-note{min-height:min-content;}
.dir-zone-supporting-cards>.dir-card{border:0;border-left:2px solid #BFD4F2;border-radius:0;padding:10px 14px;gap:6px;background:#F8FAFC;}
.dir-zone-supporting-cards .dir-card-rule{display:none;}
.dir-zone-supporting-cards>.dir-card>.dir-badge{grid-column:1/-1;grid-row:1;justify-self:start;background:transparent;padding:0;line-height:1.2;}
.dir-zone-supporting-cards>.dir-card>strong{grid-column:1/-1;grid-row:2;font-size:20px;line-height:1.25;}
.dir-zone-supporting-cards>.dir-card>p{grid-column:1/-1;grid-row:3;font-size:17px;line-height:1.4;}
.dir-zone-title>.dir-role-title{font-weight:900;letter-spacing:-.045em;text-wrap:balance;}
.dir-zone-note>.dir-component:not(.dir-role-title){font-size:16px;line-height:1.5;color:#52677D;}
.dir-zone-content.dir-zone-single{align-items:stretch;}
.dir-zone-content.dir-zone-single>.dir-process{flex:1;display:flex;flex-direction:column;justify-content:center;}
.dir-zone-content.dir-zone-single>.dir-process>.dir-sequence{flex:1;}
.dir-zone-content.dir-zone-single>.dir-timeline{flex:0 0 auto;width:100%;display:flex;flex-direction:column;justify-content:center;}
.composition-roadmap .dir-zone-content{justify-content:center;}
.composition-cover{border-top:0;background:#FFF;}
.composition-cover .dir-zone-title{border:0;padding:16px 20px 16px 0;gap:26px;justify-content:center;}
.composition-cover .dir-role-title{font-size:60px;line-height:1.28;letter-spacing:-.055em;max-height:none;color:#101B30;}
.composition-cover .dir-zone-title>.dir-component:not(.dir-role-title){font-size:21px;line-height:1.5;}
.composition-cover .dir-zone-title>.dir-card{margin-top:16px;border-left:5px solid #176FE5;border-radius:0;background:#F3F7FC;padding:20px;}
.composition-cover .dir-zone-metrics{background:#176FE5;color:#FFF;border-radius:4px;padding:30px;gap:30px;box-shadow:0 18px 40px rgba(15,67,130,.13);}
.composition-cover .dir-zone-metrics>.dir-metric{color:#FFF;background:transparent;border:0;border-bottom:1px solid rgba(255,255,255,.3);border-radius:0;padding:24px 0;}
.composition-cover .dir-zone-metrics .dir-metric-value{color:#FFF;font-size:86px;font-weight:900;}
.composition-cover .dir-zone-metrics .dir-metric-label{color:#FFF;font-size:23px;}
.composition-cover .dir-zone-metrics .dir-leaf{color:#FFF;}
.composition-table .dir-zone-content{justify-content:center;}
.composition-chart .dir-zone-content{justify-content:center;}
.composition-chart .dir-process .dir-sequence{flex-direction:column;}
.composition-chart .dir-process .dir-step{min-height:0;}
.dir-variant-spacious .dir-zone{gap:24px;}
.dir-variant-emphasis .dir-zone-title>.dir-role-title{color:#0B4BCC;}
"""


__all__ = ["CompileResult", "assert_html_semantic_alignment", "compile_slide"]
