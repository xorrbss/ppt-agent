"""PPTX adapter boundary for canonical repeat materialization.

Reconstructed PPTX consumes the selected/hydrated layout.  Native clone may
only patch positioned repeats when every source anchor remains present; it
cannot safely invent or delete OOXML shapes for prototype or reduced repeats.
"""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from dataclasses import dataclass
from typing import Any, Literal

from templates.v2.ast import compile_template_render_plan, decode_template_ast
from templates.v2.content import hydrate_layout_content
from templates.v2.models.layouts import SlideLayout


class TemplateV2PptxRepeatAdapterError(ValueError):
    def __init__(self, code: str, *, path: str):
        self.code = code
        self.path = path
        super().__init__(f"{code}: {path}")


@dataclass(frozen=True, slots=True)
class PreparedPptxSlide:
    layout: SlideLayout
    content: dict[str, Any]
    materialization: Literal["canonical-reconstructed", "native-clone-fixed"]


def prepare_repeatable_pptx_slide(
    layout: SlideLayout,
    content: Mapping[str, Any],
    *,
    preservation_mode: Literal["reconstructed", "native_clone"],
) -> PreparedPptxSlide:
    source = layout.model_dump(mode="json")
    ast = decode_template_ast({"layouts": [source]})
    plan = compile_template_render_plan(
        ast,
        layout_id=layout.id,
        content=dict(content),
    )
    fixed_prototype_fields: list[tuple[str, str]] = []
    if preservation_mode == "native_clone":
        for repeat in plan.repeats:
            source_repeat = next(
                candidate
                for component in ast.layouts[0].components
                for candidate in component.repeats
                if candidate.repeat_id == repeat.repeat_id
            )
            if source_repeat.selection == "prototype":
                component_scope = content.get(source_repeat.component_id)
                fixed_value = (
                    component_scope.get(source_repeat.content_field)
                    if isinstance(component_scope, Mapping)
                    else None
                )
                is_fixed_cardinality = (
                    source_repeat.min_items
                    == source_repeat.max_items
                    == len(source_repeat.source_indices)
                    and isinstance(fixed_value, Mapping)
                )
                if not is_fixed_cardinality:
                    raise TemplateV2PptxRepeatAdapterError(
                        "template_v2_native_clone_repeat_prototype_unsupported",
                        path=repeat.source_path,
                    )
                fixed_prototype_fields.append(
                    (source_repeat.component_id, source_repeat.content_field)
                )
                continue
            if repeat.selected_count != source_repeat.max_items:
                raise TemplateV2PptxRepeatAdapterError(
                    "template_v2_native_clone_repeat_cardinality_unsupported",
                    path=repeat.source_path,
                )

    hydrated = hydrate_layout_content(source, content)
    hydrated_layout = SlideLayout.model_validate(hydrated)
    exact_content = _layout_content_from_hydrated(hydrated_layout)
    for component_id, content_field in fixed_prototype_fields:
        component_scope = content.get(component_id)
        exact_scope = exact_content.get(component_id)
        if isinstance(component_scope, Mapping) and isinstance(exact_scope, dict):
            exact_scope[content_field] = deepcopy(component_scope[content_field])
    return PreparedPptxSlide(
        layout=(layout if preservation_mode == "native_clone" else hydrated_layout),
        content=exact_content,
        materialization=(
            "native-clone-fixed"
            if preservation_mode == "native_clone"
            else "canonical-reconstructed"
        ),
    )


def _layout_content_from_hydrated(layout: SlideLayout) -> dict[str, Any]:
    return {
        component.id: _elements_content(component.elements)
        for component in layout.components
    }


def _elements_content(elements: list[Any]) -> dict[str, Any]:
    scope: dict[str, Any] = {}
    for element in elements:
        kind = element.type
        name = getattr(element, "name", None)
        if kind in {"text", "math", "image", "text-list", "table", "chart", "infographic"}:
            if getattr(element, "decorative", True) is False and isinstance(name, str):
                scope[name] = _element_value(element)
            continue
        if kind == "container" and element.child is not None:
            scope.update(_elements_content([element.child]))
        elif kind == "group" and isinstance(name, str):
            scope[name] = _elements_content(element.children)
        elif kind in {"flex", "grid"} and isinstance(name, str):
            scope[name] = [_elements_content([child]) for child in element.children]
    return scope


def _element_value(element: Any) -> Any:
    if element.type == "text":
        return "".join(run.text for run in element.runs)
    if element.type == "math":
        return element.latex
    if element.type == "image":
        return element.data
    if element.type == "text-list":
        return ["".join(run.text for run in item) for item in element.items]
    if element.type == "table":
        return {
            "columns": ["".join(run.text for run in cell.runs) for cell in element.columns],
            "rows": [
                ["".join(run.text for run in cell.runs) for cell in row]
                for row in element.rows
            ],
        }
    return element.model_dump(mode="json")


__all__ = [
    "PreparedPptxSlide",
    "TemplateV2PptxRepeatAdapterError",
    "prepare_repeatable_pptx_slide",
]
