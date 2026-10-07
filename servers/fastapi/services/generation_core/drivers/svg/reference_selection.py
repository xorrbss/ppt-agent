"""Content evidence reference routing, with historical profile replay preserved."""
from __future__ import annotations

import re

from services.generation_core.drivers.svg.native_requirements import requested_native_data
from services.svg_authoring_profile import read_authoring_profile
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
                          visual_intent: dict | None = None) -> dict | None:
    """Select existing evidence examples without prescribing a page layout."""
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
