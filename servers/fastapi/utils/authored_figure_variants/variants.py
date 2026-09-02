"""Enumerate the CSS-only candidates a figure slide can be rendered as.

A candidate is the *same authored document* with a different combination of the
two library knobs, so producing one costs a string rewrite rather than another
authoring call. The order below is the order candidates are rendered in, and it
is fixed: the baseline first (so a deck with the loop switched on still renders
exactly what the author wrote), then one tighter type scale, then the figure
without its emphasis, then one looser type scale.
"""

from __future__ import annotations

from dataclasses import dataclass

from utils.authored_figure_variants.markup import (
    find_figure,
    set_density_class,
    set_emphasis,
    shift_density,
)

#: How many candidates a slide may be rendered as, including the baseline.
MAX_FIGURE_CANDIDATES = 4
DEFAULT_FIGURE_CANDIDATES = 3

_KEEP = "keep"
_CLEAR = "clear"
#: ``(density step, emphasis action, variant id)``, rendered in this order.
_STEPS: tuple[tuple[int, str, str], ...] = (
    (0, _KEEP, "baseline"),
    (1, _KEEP, "density-tighter"),
    (0, _CLEAR, "emphasis-off"),
    (-1, _KEEP, "density-looser"),
)


@dataclass(frozen=True)
class FigureVariant:
    """One renderable candidate: an id, its HTML, and the knobs it stands for."""

    variant_id: str
    html: str
    density_class: str
    emphasis_index: int | None

    def to_dict(self) -> dict[str, object]:
        return {
            "variant_id": self.variant_id,
            "density_class": self.density_class,
            "emphasis_index": self.emphasis_index,
        }


def figure_variants(
    html: str, *, max_candidates: int = DEFAULT_FIGURE_CANDIDATES
) -> tuple[FigureVariant, ...]:
    """Return the candidates for one slide, baseline first.

    A slide without an archetype figure yields a single baseline candidate, so
    callers can run the same code path for every slide and pay nothing extra.
    Steps that would reproduce an earlier candidate byte for byte are dropped:
    a figure already at ``n6`` has no tighter variant, and a table figure has no
    emphasis to clear.
    """
    limit = max(1, min(MAX_FIGURE_CANDIDATES, int(max_candidates)))
    figure = find_figure(html)
    if figure is None:
        return (
            FigureVariant(
                variant_id="baseline", html=html, density_class="", emphasis_index=None
            ),
        )
    seen: set[str] = set()
    candidates: list[FigureVariant] = []
    for step, emphasis_action, variant_id in _STEPS:
        if len(candidates) >= limit:
            break
        density = shift_density(figure.density_class, step)
        candidate = set_density_class(html, density)
        emphasis = figure.emphasis_index
        if emphasis_action == _CLEAR:
            candidate = set_emphasis(candidate, None)
            emphasis = None
        if candidate in seen:
            continue
        seen.add(candidate)
        candidates.append(
            FigureVariant(
                variant_id=variant_id,
                html=candidate,
                density_class=density,
                emphasis_index=emphasis,
            )
        )
    return tuple(candidates)


__all__ = [
    "DEFAULT_FIGURE_CANDIDATES",
    "MAX_FIGURE_CANDIDATES",
    "FigureVariant",
    "figure_variants",
]
