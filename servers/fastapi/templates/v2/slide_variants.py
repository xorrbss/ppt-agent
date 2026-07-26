"""Slide-scoped Template V2 variant preview/apply/restore contract.

Candidates are bounded visual patches, never replacement presentation JSON.
Applying one candidate emits a journal payload containing only the selected
layout snapshot so callers can append it through the existing revision journal.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
import hashlib
import json
from typing import Any, Mapping, Sequence

from .wire_codec import decode_wire_layouts


VARIANT_KINDS = frozenset(
    {"data_focused", "image_focused", "executive_summary"}
)
_ALLOWED_VISUAL_FIELDS = frozenset(
    {
        "x",
        "y",
        "width",
        "height",
        "size",
        "bold",
        "italic",
        "color",
        "opacity",
        "fit",
        "focus_x",
        "focus_y",
        "crop_scale",
        "legend",
        "x_axis",
        "y_axis",
        "x_axis_grid",
        "y_axis_grid",
        "data_labels",
    }
)
_FORBIDDEN_PATH_PARTS = frozenset(
    {
        "id",
        "name",
        "description",
        "type",
        "runs",
        "items",
        "columns",
        "rows",
        "categories",
        "series",
        "data",
        "source",
        "asset_provenance",
    }
)
_SEMANTIC_IGNORED_FIELDS = _ALLOWED_VISUAL_FIELDS | frozenset(
    {
        "position",
        "font",
        "fill",
        "stroke",
        "shadow",
        "alignment",
        "rotation",
        "border_radius",
    }
)


class TemplateV2VariantError(ValueError):
    """Stable fail-closed variant contract error."""


@dataclass(frozen=True, slots=True)
class VariantPatch:
    path: tuple[str | int, ...]
    after: Any


@dataclass(frozen=True, slots=True)
class VariantRequest:
    kind: str
    label: str
    patches: tuple[VariantPatch, ...]


@dataclass(frozen=True, slots=True)
class SlideVariantCandidate:
    kind: str
    label: str
    patches: tuple[VariantPatch, ...]
    before_after: tuple[tuple[tuple[str | int, ...], Any, Any], ...]
    semantic_digest: str
    render_digest: str


@dataclass(frozen=True, slots=True)
class SlideVariantPreview:
    preview_id: str
    source_digest: str
    source_revision: int
    layout_index: int
    source_layout_digest: str
    candidates: tuple[SlideVariantCandidate, ...]


@dataclass(frozen=True, slots=True)
class SlideVariantApplyResult:
    layouts: dict[str, Any]
    revision: int
    preview_id: str
    selected_kind: str
    journal_entry: dict[str, Any]


@dataclass(frozen=True, slots=True)
class SlideVariantRestoreResult:
    layouts: dict[str, Any]
    revision: int
    restored_from_revision: int


def _digest(value: Any) -> str:
    encoded = json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _read_path(source: Any, path: Sequence[str | int]) -> Any:
    value = source
    for part in path:
        if isinstance(part, int):
            if not isinstance(value, list) or not 0 <= part < len(value):
                raise TemplateV2VariantError(
                    "template_v2_variant_patch_path_invalid"
                )
            value = value[part]
        else:
            if not isinstance(value, Mapping) or part not in value:
                raise TemplateV2VariantError(
                    "template_v2_variant_patch_path_invalid"
                )
            value = value[part]
    return value


def _write_path(source: Any, path: Sequence[str | int], value: Any) -> None:
    parent = _read_path(source, path[:-1])
    field = path[-1]
    if isinstance(field, int):
        if not isinstance(parent, list) or not 0 <= field < len(parent):
            raise TemplateV2VariantError(
                "template_v2_variant_patch_path_invalid"
            )
        parent[field] = deepcopy(value)
    elif isinstance(parent, dict) and field in parent:
        parent[field] = deepcopy(value)
    else:
        raise TemplateV2VariantError(
            "template_v2_variant_patch_path_invalid"
        )


def _validate_patch_path(path: Sequence[str | int]) -> None:
    direct_fields = _ALLOWED_VISUAL_FIELDS - {
        "x",
        "y",
        "width",
        "height",
        "size",
    }
    nested_fields = {
        ("position", "x"),
        ("position", "y"),
        ("size", "width"),
        ("size", "height"),
        ("font", "size"),
        ("font", "bold"),
        ("font", "italic"),
        ("font", "color"),
        ("fill", "color"),
        ("fill", "opacity"),
    }
    prefix_is_direct_element = (
        len(path) >= 5
        and path[0] == "components"
        and isinstance(path[1], int)
        and path[2] == "elements"
        and isinstance(path[3], int)
    )
    tail = tuple(path[4:])
    tail_is_visual = (
        len(tail) == 1 and tail[0] in direct_fields
    ) or tail in nested_fields
    if not prefix_is_direct_element or not tail_is_visual or any(
        part in _FORBIDDEN_PATH_PARTS
        for part in path
        if isinstance(part, str)
    ):
        raise TemplateV2VariantError(
            "template_v2_variant_patch_not_visual"
        )


def _semantic_projection(value: Any) -> Any:
    if isinstance(value, list):
        return [_semantic_projection(item) for item in value]
    if isinstance(value, Mapping):
        return {
            key: _semantic_projection(item)
            for key, item in value.items()
            if key not in _SEMANTIC_IGNORED_FIELDS
        }
    return value


def preview_slide_variants(
    layouts: Mapping[str, Any],
    *,
    layout_index: int,
    source_revision: int,
    requests: Sequence[VariantRequest],
) -> SlideVariantPreview:
    """Validate and preview exactly two or three bounded visual candidates."""

    if (
        isinstance(source_revision, bool)
        or not isinstance(source_revision, int)
        or source_revision < 1
    ):
        raise TemplateV2VariantError("template_v2_variant_revision_invalid")
    if len(requests) not in {2, 3}:
        raise TemplateV2VariantError(
            "template_v2_variant_candidate_count_invalid"
        )
    kinds = [request.kind for request in requests]
    if (
        any(kind not in VARIANT_KINDS for kind in kinds)
        or len(kinds) != len(set(kinds))
    ):
        raise TemplateV2VariantError("template_v2_variant_kind_invalid")

    wire = decode_wire_layouts(layouts)
    wire.validate_strict()
    source = wire.to_wire_value()
    layout_values = source.get("layouts")
    if (
        not isinstance(layout_values, list)
        or not 0 <= layout_index < len(layout_values)
    ):
        raise TemplateV2VariantError(
            "template_v2_variant_layout_index_invalid"
        )
    source_layout = layout_values[layout_index]
    source_semantic_digest = _digest(_semantic_projection(source_layout))
    candidates: list[SlideVariantCandidate] = []
    for request in requests:
        if not request.label.strip() or not request.patches:
            raise TemplateV2VariantError(
                "template_v2_variant_candidate_empty"
            )
        candidate_layout = deepcopy(source_layout)
        before_after = []
        seen_paths: set[tuple[str | int, ...]] = set()
        for patch in request.patches:
            _validate_patch_path(patch.path)
            if patch.path in seen_paths:
                raise TemplateV2VariantError(
                    "template_v2_variant_patch_duplicate"
                )
            seen_paths.add(patch.path)
            before = deepcopy(_read_path(source_layout, patch.path))
            if before == patch.after:
                raise TemplateV2VariantError(
                    "template_v2_variant_patch_noop"
                )
            _write_path(candidate_layout, patch.path, patch.after)
            before_after.append((patch.path, before, deepcopy(patch.after)))

        candidate_source = deepcopy(source)
        candidate_source["layouts"][layout_index] = candidate_layout
        decode_wire_layouts(candidate_source).validate_strict()
        if _digest(_semantic_projection(candidate_layout)) != source_semantic_digest:
            raise TemplateV2VariantError(
                "template_v2_variant_semantics_changed"
            )
        candidates.append(
            SlideVariantCandidate(
                kind=request.kind,
                label=request.label,
                patches=request.patches,
                before_after=tuple(before_after),
                semantic_digest=source_semantic_digest,
                render_digest=_digest(candidate_layout),
            )
        )

    source_digest = _digest(source)
    preview_id = _digest(
        {
            "source_digest": source_digest,
            "source_revision": source_revision,
            "layout_index": layout_index,
            "candidates": [
                {
                    "kind": candidate.kind,
                    "render_digest": candidate.render_digest,
                }
                for candidate in candidates
            ],
        }
    )
    return SlideVariantPreview(
        preview_id=preview_id,
        source_digest=source_digest,
        source_revision=source_revision,
        layout_index=layout_index,
        source_layout_digest=_digest(source_layout),
        candidates=tuple(candidates),
    )


def cancel_slide_variants(preview: SlideVariantPreview) -> dict[str, str]:
    """Explicitly cancel a preview without producing a document value."""

    return {"preview_id": preview.preview_id, "status": "cancelled"}


def apply_slide_variant(
    layouts: Mapping[str, Any],
    preview: SlideVariantPreview,
    *,
    selected_kind: str,
    expected_revision: int,
    current_revision: int,
) -> SlideVariantApplyResult:
    """Apply only one candidate and return its slide-scoped journal snapshot."""

    if expected_revision != current_revision or (
        current_revision != preview.source_revision
    ):
        raise TemplateV2VariantError("template_v2_variant_stale_revision")
    wire = decode_wire_layouts(layouts)
    wire.validate_strict()
    source = wire.to_wire_value()
    if _digest(source) != preview.source_digest:
        raise TemplateV2VariantError("template_v2_variant_preview_stale")
    selected = next(
        (
            candidate
            for candidate in preview.candidates
            if candidate.kind == selected_kind
        ),
        None,
    )
    if selected is None:
        raise TemplateV2VariantError(
            "template_v2_variant_selection_invalid"
        )

    result = deepcopy(source)
    source_layout = deepcopy(result["layouts"][preview.layout_index])
    for patch, before_after in zip(selected.patches, selected.before_after):
        _, before, after = before_after
        if _read_path(source_layout, patch.path) != before:
            raise TemplateV2VariantError(
                "template_v2_variant_preview_tampered"
            )
        _write_path(result["layouts"][preview.layout_index], patch.path, after)
    decode_wire_layouts(result).validate_strict()
    applied_digest = _digest(result)
    return SlideVariantApplyResult(
        layouts=result,
        revision=current_revision + 1,
        preview_id=preview.preview_id,
        selected_kind=selected_kind,
        journal_entry={
            "reason": f"slide-variant:{selected_kind}",
            "revision": current_revision,
            "layout_index": preview.layout_index,
            "before_layout": source_layout,
            "applied_digest": applied_digest,
        },
    )


def restore_slide_variant_from_journal(
    layouts: Mapping[str, Any],
    journal_entry: Mapping[str, Any],
    *,
    expected_revision: int,
    current_revision: int,
) -> SlideVariantRestoreResult:
    """Restore one selected layout from an apply result's revision snapshot."""

    if expected_revision != current_revision:
        raise TemplateV2VariantError("template_v2_variant_stale_revision")
    wire = decode_wire_layouts(layouts)
    wire.validate_strict()
    source = wire.to_wire_value()
    if _digest(source) != journal_entry.get("applied_digest"):
        raise TemplateV2VariantError(
            "template_v2_variant_restore_source_stale"
        )
    layout_index = journal_entry.get("layout_index")
    before_layout = journal_entry.get("before_layout")
    restored_from_revision = journal_entry.get("revision")
    if (
        isinstance(layout_index, bool)
        or not isinstance(layout_index, int)
        or not isinstance(before_layout, Mapping)
        or isinstance(restored_from_revision, bool)
        or not isinstance(restored_from_revision, int)
        or not 0 <= layout_index < len(source["layouts"])
    ):
        raise TemplateV2VariantError(
            "template_v2_variant_journal_invalid"
        )
    result = deepcopy(source)
    result["layouts"][layout_index] = deepcopy(dict(before_layout))
    decode_wire_layouts(result).validate_strict()
    return SlideVariantRestoreResult(
        layouts=result,
        revision=current_revision + 1,
        restored_from_revision=restored_from_revision,
    )


__all__ = [
    "SlideVariantApplyResult",
    "SlideVariantCandidate",
    "SlideVariantPreview",
    "SlideVariantRestoreResult",
    "TemplateV2VariantError",
    "VariantPatch",
    "VariantRequest",
    "apply_slide_variant",
    "cancel_slide_variants",
    "preview_slide_variants",
    "restore_slide_variant_from_journal",
]
