"""Fail-closed canonicalization for persisted native Template V2 slide payloads."""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Mapping

from pydantic import BaseModel

from .models.layouts import SlideLayout


def _to_raw_tree(value: Any, active: set[int] | None = None) -> Any:
    """Remove trusted model instances before validating a persistence payload."""

    if not isinstance(value, (BaseModel, Mapping, list, tuple)):
        return deepcopy(value)

    if active is None:
        active = set()
    identity = id(value)
    if identity in active:
        raise ValueError("slide_ui_contains_recursive_reference")
    active.add(identity)
    try:
        if isinstance(value, BaseModel):
            # Read raw fields instead of serializing the model. This preserves
            # manually injected/extra values so ``extra='forbid'`` cannot be
            # bypassed by silently dropping them during model_dump().
            raw_model = dict(vars(value))
            extra = getattr(value, "__pydantic_extra__", None)
            if extra:
                raw_model.update(extra)
            return {
                key: _to_raw_tree(item, active)
                for key, item in raw_model.items()
            }
        if isinstance(value, Mapping):
            return {
                key: _to_raw_tree(item, active)
                for key, item in value.items()
            }
        return [_to_raw_tree(item, active) for item in value]
    finally:
        active.remove(identity)


def canonicalize_slide_ui(value: Any) -> dict[str, Any] | None:
    """Validate one native layout and return an isolated JSON-safe canonical copy."""

    if value is None:
        return None
    # Pydantic does not revalidate model instances nested anywhere in an input
    # tree. Convert every model/container to raw values so post-construction
    # mutations cannot cross a persistence boundary.
    raw_value = _to_raw_tree(value)
    canonical = SlideLayout.model_validate(raw_value).model_dump(mode="json")
    return deepcopy(canonical)


def canonicalize_slide_dump(value: Any) -> dict[str, Any]:
    """Validate and isolate the UI-bearing portion of a version snapshot."""

    if not isinstance(value, Mapping):
        raise ValueError("slide_snapshot_must_be_an_object")
    canonical = deepcopy(dict(value))
    canonical["ui"] = canonicalize_slide_ui(canonical.get("ui"))
    if canonical["ui"] is not None and canonical.get("html_content"):
        raise ValueError("slide_ui_and_authored_html_cannot_coexist")
    return canonical
