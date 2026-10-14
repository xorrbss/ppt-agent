"""Immutable admission bounds for one missing-title preparation call.

The plan is sealed before provider work and is carried beside the frozen style.
It never adds slots to a restored request or changes admitted monetary ceilings.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
import re

from services.svg_common_header import common_header_source_digest
from utils.hashing import canonical_json_digest


INTERNAL_HEADER_TITLE_PLAN_KEY = "_internal_svg_header_title_plan"
HEADER_TITLE_PLAN_VERSION = "svg-common-header-title-plan/v1"
_HASH = re.compile(r"^sha256:[a-f0-9]{64}$")
_LIMITS = {
    "call_limit": 1,
    "input_token_limit": 32768,
    "output_token_limit": 8192,
    "call_timeout_seconds": 120,
}


def _input_identity(pages: Sequence[Mapping], missing_indices: Sequence[int]) -> dict:
    if not isinstance(pages, (list, tuple)) or not pages:
        raise ValueError("svg_common_header_title_plan_input_invalid")
    if (not isinstance(missing_indices, (list, tuple)) or not missing_indices
            or any(type(index) is not int for index in missing_indices)
            or list(missing_indices) != sorted(set(missing_indices))
            or any(index < 0 or index >= len(pages) for index in missing_indices)):
        raise ValueError("svg_common_header_title_plan_input_invalid")
    sources, titles = [], []
    for index, page in enumerate(pages):
        if (not isinstance(page, Mapping) or page.get("page_index") != index
                or type(page.get("page_index")) is not int
                or not isinstance(page.get("content"), str)
                or page.get("source_digest") != common_header_source_digest(page["content"])):
            raise ValueError("svg_common_header_title_plan_input_invalid")
        title = page.get("title")
        if title is not None and not isinstance(title, str):
            raise ValueError("svg_common_header_title_plan_input_invalid")
        if ((index in missing_indices) != (title is None or not title.strip())):
            raise ValueError("svg_common_header_title_plan_input_invalid")
        sources.append({"page_index": index, "source_digest": page["source_digest"]})
        titles.append({"page_index": index, "title": title})
    # UTF-8 bytes are a conservative upper bound for source tokenization. Keep
    # a fixed allowance for instructions, schema and page metadata; reject a
    # batch that would need splitting instead of starting another paid call.
    if (sum(len(page["content"].encode("utf-8")) for page in pages) + 4096
            > _LIMITS["input_token_limit"]
            or len(missing_indices) * 256 + 1024 > _LIMITS["output_token_limit"]):
        raise ValueError("svg_common_header_title_capacity_exceeded")
    return {"page_count": len(pages), "source_digest": canonical_json_digest(sources),
            "titles_digest": canonical_json_digest(titles), "missing_indices": list(missing_indices)}


def validate_header_title_plan(value: object) -> dict | None:
    if value is None:
        return None
    expected = {"schema", "stage", "page_count", "source_digest", "titles_digest",
                "missing_indices", "plan_digest", *_LIMITS}
    if (not isinstance(value, Mapping) or set(value) != expected
            or value.get("schema") != HEADER_TITLE_PLAN_VERSION or value.get("stage") != "outline"
            or type(value.get("page_count")) is not int or value["page_count"] < 1
            or any(type(value.get(key)) is not int or value[key] != limit
                   for key, limit in _LIMITS.items())
            or any(not isinstance(value.get(key), str) or not _HASH.fullmatch(value[key])
                   for key in ("source_digest", "titles_digest", "plan_digest"))):
        raise ValueError("svg_common_header_title_plan_invalid")
    indices = value.get("missing_indices")
    if (not isinstance(indices, list) or not indices
            or any(type(index) is not int for index in indices)
            or indices != sorted(set(indices))
            or any(index < 0 or index >= value["page_count"] for index in indices)
            or canonical_json_digest({key: item for key, item in value.items() if key != "plan_digest"})
            != value["plan_digest"]):
        raise ValueError("svg_common_header_title_plan_invalid")
    return {**value, "missing_indices": list(indices)}


def seal_new_header_title_plan(request: object, pages: Sequence[Mapping],
                               missing_indices: Sequence[int]) -> dict | None:
    """Pin a fresh external-input batch; never expand restored admission."""
    existing = validate_header_title_plan(getattr(request, "_svg_header_title_plan", None))
    if existing is not None:
        return require_header_title_plan(request, pages, missing_indices)
    if not missing_indices or getattr(request, "common_header_mode", None) != "uniform":
        return None
    if (getattr(request, "_svg_style_snapshot", None) is not None
            or getattr(request, "_svg_authoring_profile_selected", False)):
        raise ValueError("svg_common_header_title_plan_missing")
    identity = _input_identity(pages, missing_indices)
    plan = {"schema": HEADER_TITLE_PLAN_VERSION, "stage": "outline", **identity, **_LIMITS}
    plan["plan_digest"] = canonical_json_digest(plan)
    request._svg_header_title_plan = plan
    return validate_header_title_plan(plan)


def seal_new_external_header_title_plan(request: object) -> dict | None:
    """Use the same outline normalization and title classifier as execution."""
    markdown = getattr(request, "slides_markdown", None)
    if not markdown or getattr(request, "common_header_mode", None) != "uniform":
        return None
    from models.presentation_outline_model import SlideOutlineModel
    from utils.outline_utils import explicit_outline_title

    pages, missing = [], []
    for index, content in enumerate(markdown):
        slide = SlideOutlineModel(content=content)
        title = explicit_outline_title(slide.content)
        pages.append({"page_index": index, "content": slide.content,
                      "source_digest": common_header_source_digest(slide.content), "title": title})
        if title is None:
            missing.append(index)
    return seal_new_header_title_plan(request, pages, missing)


def require_header_title_plan(request: object, pages: Sequence[Mapping],
                              missing_indices: Sequence[int]) -> dict:
    """Execution may consume only the exact already-admitted source batch."""
    plan = validate_header_title_plan(getattr(request, "_svg_header_title_plan", None))
    if plan is None:
        raise ValueError("svg_common_header_title_plan_missing")
    if (getattr(request, "common_header_mode", None) != "uniform"
            or any(plan[key] != value for key, value in _input_identity(pages, missing_indices).items())):
        raise ValueError("svg_common_header_title_plan_conflict")
    return plan


def svg_header_title_admission_budget(request: object, slide_count: int) -> int:
    """Zero for legacy/normal outlines; one only for the presealed batch."""
    plan = validate_header_title_plan(getattr(request, "_svg_header_title_plan", None))
    if plan is None:
        return 0
    if (type(slide_count) is not int or plan["page_count"] != slide_count
            or getattr(request, "common_header_mode", None) != "uniform"):
        raise ValueError("svg_common_header_title_plan_conflict")
    return plan["call_limit"]
