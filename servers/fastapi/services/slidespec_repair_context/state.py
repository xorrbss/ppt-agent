"""Read-only indexes shared by the SlideSpec repair projection bands."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
import math
from typing import Any, Callable

from models.slidespec_v1 import LayoutNode, RenderRequest, SlideSpec
from services.slidespec_repair_pipeline import DomEvidence
from utils.authored_dom_quality import normalize_browser_metrics
from utils.slidespec_constraints import ConstraintReport
from utils.slidespec_core import render_slidespec


def _walk_layout(node: LayoutNode) -> Sequence[LayoutNode]:
    result = [node]
    for child in node.children:
        result.extend(_walk_layout(child))
    return result


@dataclass(slots=True)
class RepairContextState:
    """Stable inputs and lookup indexes for one repair-context build."""

    request: RenderRequest | SlideSpec
    evidence: DomEvidence
    constraint_report: ConstraintReport | None
    spec: SlideSpec
    source: Any
    layout_by_semantic: dict[str, LayoutNode]
    content_by_semantic: dict[str, Any]
    binding_by_semantic: dict[str, Any]
    layout_by_id: dict[str, LayoutNode]
    parent_by_id: dict[str, str | None]
    normalized_metrics: Mapping[str, Any]
    metric_by_qid: dict[str, Mapping[str, Any]]
    native_qid_by_semantic: dict[str, str]
    semantic_by_qid: dict[str, str]

    @classmethod
    def build(
        cls,
        request: RenderRequest | SlideSpec,
        evidence: DomEvidence,
        browser_metrics: Mapping[str, Any] | None,
        constraint_report: ConstraintReport | None,
        *,
        render_request_type: type[Any] = RenderRequest,
        render_native: Callable[[Any], Any] = render_slidespec,
    ) -> RepairContextState:
        is_render_request = isinstance(request, render_request_type)
        spec = request.spec if is_render_request else request
        source = request.source if is_render_request else None
        layout_by_semantic = {
            node.contentSemanticId: node for node in _walk_layout(spec.layout) if node.contentSemanticId is not None
        }
        content_by_semantic = {node.semanticId: node for node in spec.content}
        binding_by_semantic = {binding.semanticId: binding for binding in (source.bindings if source else ())}
        layout_by_id = {node.layoutId: node for node in _walk_layout(spec.layout)}
        parent_by_id: dict[str, str | None] = {spec.layout.layoutId: None}
        stack = [spec.layout]
        while stack:
            parent = stack.pop()
            for child in parent.children:
                parent_by_id[child.layoutId] = parent.layoutId
                stack.append(child)
        normalized_metrics = normalize_browser_metrics(browser_metrics)
        metric_by_qid = {str(item.get("qid")): item for item in normalized_metrics.get("elements", ())}
        native_qid_by_semantic: dict[str, str] = {}
        if source is None and is_render_request:
            native_qid_by_semantic = {entry.semanticId: entry.qid for entry in render_native(request).semanticDom}
        semantic_by_qid = {binding.qid: binding.semanticId for binding in (source.bindings if source else ())}
        semantic_by_qid.update({qid: semantic_id for semantic_id, qid in native_qid_by_semantic.items()})
        return cls(
            request=request,
            evidence=evidence,
            constraint_report=constraint_report,
            spec=spec,
            source=source,
            layout_by_semantic=layout_by_semantic,
            content_by_semantic=content_by_semantic,
            binding_by_semantic=binding_by_semantic,
            layout_by_id=layout_by_id,
            parent_by_id=parent_by_id,
            normalized_metrics=normalized_metrics,
            metric_by_qid=metric_by_qid,
            native_qid_by_semantic=native_qid_by_semantic,
            semantic_by_qid=semantic_by_qid,
        )

    def qid_for_semantic(self, semantic_id: str) -> str | None:
        imported = self.binding_by_semantic.get(semantic_id)
        return imported.qid if imported is not None else self.native_qid_by_semantic.get(semantic_id)

    def metric_for_semantic(self, semantic_id: str) -> Mapping[str, Any] | None:
        imported = self.binding_by_semantic.get(semantic_id)
        qid = imported.qid if imported is not None else self.native_qid_by_semantic.get(semantic_id)
        metric = self.metric_by_qid.get(qid) if qid else None
        return metric if isinstance(metric, Mapping) else None

    @staticmethod
    def finite_number(value: object, default: float = 0.0) -> float:
        try:
            number = float(value)
        except (TypeError, ValueError):
            return default
        return number if math.isfinite(number) else default

    @staticmethod
    def envelope_can_change(raw: object) -> bool:
        if not isinstance(raw, Mapping) or raw.get("locked") is True:
            return False
        allowed = raw.get("allowedOperations")
        if allowed is None:
            return True
        if not isinstance(allowed, Sequence) or isinstance(allowed, (str, bytes)):
            return False
        return bool(
            {
                "translateWithinParent",
                "setBounds",
                "resizeWithinBounds",
                "setFontScale",
            }.intersection(str(value) for value in allowed)
        )

    def metric_qids_related(self, first: str, second: str) -> bool:
        for start, target in ((first, second), (second, first)):
            current = start
            visited: set[str] = set()
            while current and current not in visited:
                if current == target:
                    return True
                visited.add(current)
                item = self.metric_by_qid.get(current)
                current = str(item.get("parent_qid", "")) if item is not None else ""
        return False

    def metric_qid_is_ancestor(self, ancestor: str, descendant: str) -> bool:
        current = descendant
        visited: set[str] = set()
        while current and current not in visited:
            if current == ancestor:
                return True
            visited.add(current)
            item = self.metric_by_qid.get(current)
            current = str(item.get("parent_qid", "")) if item is not None else ""
        return False

    def bound_semantic_for_metric_qid(self, qid: str) -> tuple[str, str] | None:
        current = qid
        visited: set[str] = set()
        while current and current not in visited:
            semantic_id = self.semantic_by_qid.get(current)
            if semantic_id is not None:
                return semantic_id, current
            visited.add(current)
            item = self.metric_by_qid.get(current)
            current = str(item.get("parent_qid", "")) if item is not None else ""
        return None
