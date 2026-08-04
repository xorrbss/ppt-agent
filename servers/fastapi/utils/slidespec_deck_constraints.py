"""Deterministic cross-slide consistency checks for SlideSpec decks.

The per-slide contract remains authoritative.  These checks add a small set of
deck invariants that can be evaluated without guessing a visual theme.  Facts
that would make comparisons invalid are hard failures; typography and title
placement drift are soft, explainable costs.
"""

from __future__ import annotations

from dataclasses import dataclass
from statistics import median
from typing import Mapping, Sequence

from models.slidespec_v1 import LayoutNode, RenderRequest


@dataclass(frozen=True, slots=True)
class DeckConsistencyViolation:
    code: str
    strength: str
    slide_indices: tuple[int, ...]
    detail: Mapping[str, object]

    def as_dict(self) -> dict[str, object]:
        return {
            "code": self.code,
            "strength": self.strength,
            "slide_indices": list(self.slide_indices),
            "detail": dict(self.detail),
        }


@dataclass(frozen=True, slots=True)
class DeckConsistencyReport:
    violations: tuple[DeckConsistencyViolation, ...]
    slide_count: int

    @property
    def hard_failure(self) -> bool:
        return any(item.strength == "hard" for item in self.violations)

    @property
    def soft_cost(self) -> float:
        return float(sum(1 for item in self.violations if item.strength == "soft"))

    def metrics(self) -> dict[str, int | float | bool]:
        return {
            "slide_count": self.slide_count,
            "violation_count": len(self.violations),
            "hard_failure": self.hard_failure,
            "hard_count": sum(item.strength == "hard" for item in self.violations),
            "soft_count": sum(item.strength == "soft" for item in self.violations),
            "soft_cost": self.soft_cost,
        }


def _walk(root: LayoutNode) -> tuple[LayoutNode, ...]:
    nodes: list[LayoutNode] = []
    stack = [root]
    while stack:
        node = stack.pop()
        nodes.append(node)
        stack.extend(reversed(node.children))
    return tuple(nodes)


def _title_observation(request: RenderRequest) -> tuple[float, float] | None:
    """Return rendered title font size and normalized top coordinate."""

    titles = sorted(
        (item for item in request.spec.content if item.kind == "title"),
        key=lambda item: (item.readingOrder, item.semanticId),
    )
    if not titles:
        return None
    title = titles[0]
    layouts = {
        node.contentSemanticId: node
        for node in _walk(request.spec.layout)
        if node.contentSemanticId is not None
    }
    node = layouts.get(title.semanticId)
    if node is None or node.bounds is None:
        return None
    binding = next(
        (
            item
            for item in (request.source.bindings if request.source is not None else ())
            if item.semanticId == title.semanticId
        ),
        None,
    )
    if binding is None or binding.fontSizePx is None:
        return None
    return binding.fontSizePx * node.fontScale, node.bounds.y


def evaluate_deck_consistency(
    requests: Sequence[tuple[int, RenderRequest]],
) -> DeckConsistencyReport:
    """Evaluate stable hard invariants and conservative soft visual drift."""

    ordered = tuple(sorted(requests, key=lambda item: item[0]))
    violations: list[DeckConsistencyViolation] = []
    if not ordered:
        return DeckConsistencyReport(violations=(), slide_count=0)

    slide_ids: dict[str, list[int]] = {}
    for index, request in ordered:
        slide_ids.setdefault(request.spec.slideId, []).append(index)
    for slide_id, indices in sorted(slide_ids.items()):
        if len(indices) > 1:
            violations.append(
                DeckConsistencyViolation(
                    code="deck.slide_id.duplicate",
                    strength="hard",
                    slide_indices=tuple(indices),
                    detail={"slide_id": slide_id},
                )
            )

    reference_index, reference = ordered[0]
    reference_viewport = (
        (reference.source.viewportWidth, reference.source.viewportHeight)
        if reference.source is not None
        else None
    )
    reference_renderer = (
        reference.renderer.name,
        reference.renderer.version,
        reference.renderer.runtime,
    )
    for index, request in ordered[1:]:
        viewport = (
            (request.source.viewportWidth, request.source.viewportHeight)
            if request.source is not None
            else None
        )
        if viewport != reference_viewport:
            violations.append(
                DeckConsistencyViolation(
                    code="deck.viewport.mismatch",
                    strength="hard",
                    slide_indices=(reference_index, index),
                    detail={"expected": reference_viewport, "actual": viewport},
                )
            )
        renderer = (
            request.renderer.name,
            request.renderer.version,
            request.renderer.runtime,
        )
        if renderer != reference_renderer:
            violations.append(
                DeckConsistencyViolation(
                    code="deck.renderer.mismatch",
                    strength="hard",
                    slide_indices=(reference_index, index),
                    detail={"expected": reference_renderer, "actual": renderer},
                )
            )

    token_values: dict[str, dict[object, list[int]]] = {}
    for index, request in ordered:
        for token in request.spec.tokens:
            if token.important:
                token_values.setdefault(token.name, {}).setdefault(token.value, []).append(index)
    for name, values in sorted(token_values.items()):
        if len(values) > 1:
            indices = tuple(sorted({index for members in values.values() for index in members}))
            violations.append(
                DeckConsistencyViolation(
                    code="deck.important_token.conflict",
                    strength="hard",
                    slide_indices=indices,
                    detail={"token": name, "value_count": len(values)},
                )
            )

    titles = tuple(
        (index, observation)
        for index, request in ordered
        if (observation := _title_observation(request)) is not None
    )
    if len(titles) >= 3:
        median_font = median(item[1][0] for item in titles)
        median_y = median(item[1][1] for item in titles)
        for index, (font_size, y) in titles:
            if median_font > 0 and not (0.8 * median_font <= font_size <= 1.25 * median_font):
                violations.append(
                    DeckConsistencyViolation(
                        code="deck.title_typography.outlier",
                        strength="soft",
                        slide_indices=(index,),
                        detail={
                            "font_size_px": round(font_size, 3),
                            "median_font_size_px": round(median_font, 3),
                        },
                    )
                )
            if abs(y - median_y) > 0.08:
                violations.append(
                    DeckConsistencyViolation(
                        code="deck.title_anchor.outlier",
                        strength="soft",
                        slide_indices=(index,),
                        detail={"title_y": round(y, 6), "median_title_y": round(median_y, 6)},
                    )
                )

    violations.sort(key=lambda item: (item.code, item.slide_indices, repr(sorted(item.detail.items()))))
    return DeckConsistencyReport(violations=tuple(violations), slide_count=len(ordered))


__all__ = [
    "DeckConsistencyReport",
    "DeckConsistencyViolation",
    "evaluate_deck_consistency",
]
