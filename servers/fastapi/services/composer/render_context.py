"""Per-render state shared by every component renderer."""

from __future__ import annotations

from dataclasses import dataclass

from services.composer.plan import (
    ComposerComponent,
    ComposerDeckPlan,
    ComposerTokens,
    FitParameters,
)
from services.composer.render_css import ChromeGeometry, DensitySpec, text_px
from services.composer.render_text import BindingRegistry, QidFactory

_SIZE_ROLE_BY_KIND = {
    "title": "title",
    "subtitle": "subtitle",
    "kpi": "kpi",
    "caption": "caption",
    "furniture": "furniture",
}


@dataclass
class RenderContext:
    tokens: ComposerTokens
    params: FitParameters
    deck: ComposerDeckPlan
    geometry: ChromeGeometry
    density: DensitySpec
    registry: BindingRegistry
    #: Fitted title/subtitle size for this slide (``services.composer.render_title``).
    #: Unused at rungs 4-5, where a title's own paragraphs size from their source.
    title_px: float
    subtitle_px: float

    @property
    def qids(self) -> QidFactory:
        return self.registry.qids

    def size(self, role: str, source_pt: float | None, box_scale: float = 1.0) -> float:
        return text_px(role, source_pt, self.params, self.deck, box_scale=box_scale)


def component_role(component: ComposerComponent) -> str:
    return component.semantic_role or "body"


def size_role(component: ComposerComponent, params: FitParameters | None = None) -> str:
    """Rung of the type scale a component's text is set at.

    A dense (``params.density >= 1``) process step drops to the supporting
    scale: a step card is already tight on width once the deck is dense, and
    its own text is the first practical dial for a slide the ladder pushes
    past the template rungs (see ``composer-offline-eval`` final2-43 slides
    12-15/32 -- five- and six-step "절감 동인" pages that estimated over at
    every rung 0-4 step). Mirrors ``estimate_components.type_scale_key``.
    """

    if component.kind == "process" and params is not None and int(params.density) >= 1:
        return "supporting"
    return _SIZE_ROLE_BY_KIND.get(component.kind, component_role(component))


__all__ = ["RenderContext", "component_role", "size_role"]
