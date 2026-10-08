"""Content evidence reference routing, with historical profile replay preserved."""
from __future__ import annotations

import json
import re

from services.generation_core.drivers.svg.native_requirements import requested_native_data
from services.svg_authoring_profile import read_authoring_profile, read_quality_recovery_policy
from services.svg_contract import source_digest

SELECTION_VERSION = "svg-reference-selection/v2"
_DATE = r"\d{4}[-./]\d{1,2}[-./]\d{1,2}"
_DATE_RANGE = re.compile(rf"{_DATE}\s*(?:[~～–—-]|부터|to|through)\s*{_DATE}", re.I)
_PERIOD_RANGE = re.compile(
    r"\b\d+\s*(?:[~～–—-]|to)\s*\d+\s*(?:주(?:차)?|일|개월|weeks?|days?|months?)\b", re.I)
_EDGE = re.compile(r"([^\n,;:：→<>]+?)\s*(?:→|->)\s*([^\n,;:：→<>]+)")
_STRUCTURE = re.compile(r"구조|시스템|아키텍처|의존관계|외부\s*연계|\b(?:architecture|dependencies|dependency|system|nodes?)\b", re.I)
_NODE = re.compile(r"\b(?:API|DB|service|database|queue)\b|저장소|이벤트\s*큐|화면|앱|서비스", re.I)
_QUANTITY = re.compile(r"\d[\d.,]*\s*(?:%|%p|건|명|분|시간|원|일|주|개월)?")


def select_reference_role(style_snapshot: dict, page_role: str, content: str,
                          visual_intent: dict | None = None, *, has_source_images: bool = False) -> dict | None:
    """Select existing evidence examples without prescribing a page layout."""
    if read_quality_recovery_policy(style_snapshot) is not None:
        return _select_quality_references(style_snapshot, page_role, content, visual_intent,
                                          has_source_images=has_source_images)
    profile = read_authoring_profile(style_snapshot)
    if profile is None or profile["version"] not in (2, 3, 4, 5, 6, 8):
        return None
    target, basis = page_role, "page_role"
    if page_role != "cover":
        native = requested_native_data(content)
        dated_tasks = sum(bool(_DATE_RANGE.search(line) or _PERIOD_RANGE.search(line))
                          for line in content.splitlines())
        edges = [match for match in _EDGE.finditer(content)
                 if match[1].strip() and match[2].strip()
                 and not _QUANTITY.fullmatch(match[1].strip())
                 and not _QUANTITY.fullmatch(match[2].strip())]
        if native.get("tables") or native.get("charts"):
            target, basis = "data", "explicit_native_data"
        elif dated_tasks >= 2:
            target, basis = "timeline", "multiple_source_time_intervals"
        elif edges and (_STRUCTURE.search(content) or len(edges) >= 2 and _NODE.search(content)):
            target, basis = "architecture", "explicit_source_relationships"
        else:
            target, basis = "content", "source_message_or_process"
            if (visual_intent and visual_intent.get("source_digest") == source_digest(content)
                    and visual_intent.get("reading_flow", {}).get("intent") == "ordered_sequence"):
                basis = "source_ordered_process"
    selected = target if style_snapshot.get("references", {}).get(target) else page_role
    version = SELECTION_VERSION if profile["version"] == 2 else f"svg-reference-selection/v{profile['version']}"
    return {"version": version, "page_role": page_role,
            "requested_role": target, "selected_role": selected, "basis": basis,
            "fallback_to_page_role": selected != target}


_COMPARISON = re.compile(r"\b(?:versus|vs\.?|comparison|compare[ds]?|before\s+and\s+after)\b|비교|대비|전후", re.I)
_SEQUENCE = re.compile(r"\b(?:steps?|stages?|workflow|process|sequence)\b|단계|순서|절차|프로세스", re.I)
_ORDERED = re.compile(r"^\s*\d+[.)]\s+", re.M)


def _select_quality_references(style_snapshot: dict, page_role: str, content: str,
                               visual_intent: dict | None, *, has_source_images: bool) -> dict:
    """Route existing examples by source meaning, without a layout assignment."""
    references = style_snapshot.get("references", {})
    source_kinds = set()
    source_text = content
    try:
        source = json.loads(content)
    except (ValueError, TypeError):
        source = None
    if (isinstance(source, dict)
            and source.get("source_authoring_contract") == {"version": "svg-source-whole-page/v1"}
            and isinstance(source.get("source_facts"), list)):
        facts = source["source_facts"]
        source_kinds = {fact.get("kind") for fact in facts if isinstance(fact, dict) and isinstance(fact.get("kind"), str)}

        def original_text(value):
            if isinstance(value, dict):
                for key, item in value.items():
                    if key in {"text", "title"} and isinstance(item, str):
                        yield item
                    elif isinstance(item, (dict, list)):
                        yield from original_text(item)
            elif isinstance(value, list):
                for item in value:
                    yield from original_text(item)

        source_text = "\n".join(original_text([fact.get("data", {}) for fact in facts if isinstance(fact, dict)]))
    intent = (visual_intent if visual_intent and visual_intent.get("source_digest") == source_digest(content) else {})
    flow = intent.get("reading_flow", {}).get("intent")
    requested = []
    expressions = []

    def consider(role: str, basis: str, advice: str) -> None:
        if role not in [item["role"] for item in requested]:
            requested.append({"role": role, "basis": basis})
        expressions.append({"basis": basis, "advice": advice})

    if page_role in {"cover", "section", "closing"}:
        consider(page_role, "page_purpose", "Give the supplied main statement a distinctive hierarchy and sufficient supporting context.")
    else:
        native = requested_native_data(content)
        if native.get("tables") or native.get("charts") or source_kinds & {"table", "chart"}:
            consider("data", "explicit_native_data", "Let exact native evidence carry the main field; associate each supplied explanation with its values.")
        if _COMPARISON.search(source_text) or flow == "comparison":
            consider("data", "source_comparison", "Make the supplied comparison dimensions easy to inspect with aligned labels and meaningful emphasis; do not invent values.")
        dated = sum(bool(_DATE_RANGE.search(line) or _PERIOD_RANGE.search(line)) for line in source_text.splitlines())
        ordered = len(_ORDERED.findall(source_text)) >= 2
        if dated >= 2 or flow == "ordered_sequence" or ordered and _SEQUENCE.search(source_text):
            consider("timeline", "source_sequence", "Clarify the supplied order with readable stage labels and supporting detail; dates and elapsed-time encoding require source evidence.")
        edges = [match for match in _EDGE.finditer(source_text)
                 if not _QUANTITY.fullmatch(match[1].strip()) and not _QUANTITY.fullmatch(match[2].strip())]
        if edges and (_STRUCTURE.search(source_text) or len(edges) >= 2 and _NODE.search(source_text)):
            consider("architecture", "explicit_source_relationships", "Give shared entities and branches a coherent reading flow while preserving every direction, label and explanation.")
        elif len(edges) >= 2 and _SEQUENCE.search(source_text):
            consider("timeline", "source_connected_process", "Show only source-supported transitions and their complete labels; use whitespace to keep the sequence readable.")
        emphasis = intent.get("emphasis", {})
        if (_QUANTITY.search(emphasis.get("focal_text", ""))
                or len(emphasis.get("supporting_facts", [])) >= 2):
            consider("data", "source_numeric_emphasis", "Give supplied quantities and their context clear visual weight; a numeric example never permits inventing a chart, denominator or comparison.")
        if has_source_images:
            consider("content", "source_image_explanation", "Keep every supplied image group together with its explanation; let image detail and readable captions shape the page.")
        if not requested and intent.get("density", {}).get("level") == "sparse":
            consider("section", "source_focal_message", "Give the source-supported focal statement deliberate visual weight and open space without adding material.")
        if not requested:
            consider("content", "source_message_and_evidence", "Lead with the source message, group its evidence by meaning and use type hierarchy rather than a repeated card arrangement.")
        consider("content", "supporting_source_context", "Retain all supporting wording and exact evidence; combine ideas from relevant examples freely instead of reproducing a sample layout.")
    selected_roles = [item["role"] for item in requested if references.get(item["role"])]
    if not selected_roles:
        selected_roles = [role for role in dict.fromkeys((page_role, "content")) if references.get(role)]
    primary = selected_roles[0] if selected_roles else page_role
    return {"version": "svg-reference-selection/quality-first-v1", "page_role": page_role,
            "requested_role": requested[0]["role"], "selected_role": primary,
            "basis": requested[0]["basis"], "fallback_to_page_role": primary != requested[0]["role"],
            "reference_roles": selected_roles, "requested_examples": requested,
            "expression_advice": expressions,
            "semantics": "Design evidence only; sample wording, values and relationships are never current source."}
