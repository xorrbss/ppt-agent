"""Capability validation and deterministic AST -> render-plan lowering."""

from __future__ import annotations

import copy
from collections.abc import Callable
from typing import Any

from .models import (
    CanonicalTemplateAst,
    RenderPlanRepeat,
    RepeatNode,
    TemplateRenderPlan,
)


class TemplateCompilerError(ValueError):
    pass


def compile_template_render_plan(
    ast: CanonicalTemplateAst,
    *,
    layout_id: str,
    content: dict[str, Any],
) -> TemplateRenderPlan:
    """Compile one immutable render plan or fail closed before rendering."""

    if ast.capabilities.review_required or not ast.capabilities.hydration:
        issues = ",".join(ast.capabilities.issues) or "unsupported_capability"
        raise TemplateCompilerError(f"template_ast_review_required:{issues}")
    layout = next((item for item in ast.layouts if item.layout_id == layout_id), None)
    if layout is None:
        raise TemplateCompilerError(f"template_ast_layout_not_found:{layout_id}")
    repeats: list[RenderPlanRepeat] = []
    for component in layout.components:
        component_content = content.get(component.component_id, {})
        if not isinstance(component_content, dict):
            component_content = {}
        for repeat in component.repeats:
            values = component_content.get(repeat.content_field)
            count = len(values) if isinstance(values, list) else repeat.max_items
            selected = _select_repeat_sources(repeat, count)
            repeats.append(
                RenderPlanRepeat(
                    repeat_id=repeat.repeat_id,
                    component_id=component.component_id,
                    source_path=repeat.source_path,
                    content_field=repeat.content_field,
                    arrangement=repeat.arrangement,
                    selected_count=count,
                    source_indices=repeat.source_indices,
                    selected_sources=tuple(selected),
                )
            )
    return TemplateRenderPlan(
        layout_id=layout.layout_id,
        source_layout=copy.deepcopy(layout.source),
        content=copy.deepcopy(content),
        repeats=tuple(repeats),
        capabilities=ast.capabilities,
    )


def materialize_render_plan(
    plan: TemplateRenderPlan,
    *,
    item_hydrator: Callable[[dict[str, Any], Any], dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Materialize repeat geometry once for editor, HTML, PDF and PPTX adapters."""

    layout = copy.deepcopy(plan.source_layout)
    for repeat in plan.repeats:
        component_content = plan.content.get(repeat.component_id, {})
        repeat_values = (
            component_content.get(repeat.content_field)
            if isinstance(component_content, dict)
            else None
        )
        relative = _relative_component_path(repeat.source_path)
        component = _component(layout, repeat.component_id)
        target, key = _resolve_parent(component, relative)
        current = target.get(key)
        if not isinstance(current, list):
            raise TemplateCompilerError(
                f"template_render_plan_target_invalid:{repeat.repeat_id}"
            )
        if repeat.arrangement == "positioned":
            selected_by_index = {
                source.get("__canonical_source_index"): source
                for source in repeat.selected_sources
            }
            all_repeat_indexes = set(
                _repeat_source_indexes(plan, repeat.repeat_id)
            )
            replacement: list[Any] = []
            selected_index = 0
            for index, item in enumerate(current):
                if index in all_repeat_indexes:
                    selected = selected_by_index.get(index)
                    if selected is not None:
                        clean = copy.deepcopy(selected)
                        clean.pop("__canonical_source_index", None)
                        clean = _hydrate_selected(
                            clean,
                            repeat_values,
                            selected_index,
                            item_hydrator,
                        )
                        replacement.append(clean)
                        selected_index += 1
                else:
                    replacement.append(item)
            target[key] = replacement
        else:
            target[key] = [
                _hydrate_selected(
                    copy.deepcopy(item), repeat_values, index, item_hydrator
                )
                for index, item in enumerate(repeat.selected_sources)
            ]
    return layout


def _hydrate_selected(
    source: dict[str, Any],
    values: Any,
    index: int,
    hydrator: Callable[[dict[str, Any], Any], dict[str, Any]] | None,
) -> dict[str, Any]:
    if hydrator is None or not isinstance(values, list) or index >= len(values):
        return source
    hydrated = hydrator(source, values[index])
    if not isinstance(hydrated, dict):
        raise TemplateCompilerError("template_repeat_hydrator_invalid")
    return hydrated


def _select_repeat_sources(repeat: RepeatNode, count: int) -> list[dict[str, Any]]:
    if count < repeat.min_items or count > repeat.max_items:
        raise TemplateCompilerError(
            f"template_repeat_count_out_of_bounds:{repeat.repeat_id}:"
            f"{count}:{repeat.min_items}:{repeat.max_items}"
        )
    if repeat.selection == "prototype":
        if repeat.prototype is None:
            raise TemplateCompilerError("template_repeat_prototype_missing")
        return [copy.deepcopy(repeat.prototype) for _index in range(count)]
    selected: list[dict[str, Any]] = []
    for anchor in repeat.anchors[:count]:
        item = copy.deepcopy(anchor.source)
        item["__canonical_source_index"] = anchor.source_index
        selected.append(item)
    return selected


def _component(layout: dict[str, Any], component_id: str) -> dict[str, Any]:
    components = layout.get("components")
    if isinstance(components, list):
        for component in components:
            if isinstance(component, dict) and str(component.get("id")) == component_id:
                return component
    raise TemplateCompilerError(f"template_render_component_not_found:{component_id}")


def _relative_component_path(source_path: str) -> list[str]:
    marker = ".components."
    if marker not in source_path:
        raise TemplateCompilerError(f"template_render_path_invalid:{source_path}")
    tail = source_path.split(marker, 1)[1]
    parts = tail.split(".")
    return parts[1:]


def _resolve_parent(root: dict[str, Any], path: list[str]) -> tuple[dict[str, Any], str]:
    if not path:
        raise TemplateCompilerError("template_render_path_empty")
    current: Any = root
    for segment in path[:-1]:
        if isinstance(current, dict):
            current = current.get(segment)
        elif isinstance(current, list) and segment.isdigit():
            index = int(segment)
            current = current[index] if index < len(current) else None
        else:
            current = None
        if current is None:
            raise TemplateCompilerError("template_render_path_unresolvable")
    if not isinstance(current, dict):
        raise TemplateCompilerError("template_render_parent_invalid")
    return current, path[-1]


def _repeat_source_indexes(plan: TemplateRenderPlan, repeat_id: str) -> tuple[int, ...]:
    # selected sources are a prefix.  The compiler records the complete source
    # index list in a private plan extension to avoid renderer inference.
    source = next(item for item in plan.repeats if item.repeat_id == repeat_id)
    return source.source_indices


__all__ = [
    "TemplateCompilerError",
    "compile_template_render_plan",
    "materialize_render_plan",
]
