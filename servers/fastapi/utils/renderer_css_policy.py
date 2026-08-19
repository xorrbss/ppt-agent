"""The CSS a renderer-isolated authored slide may declare.

Split out of ``renderer_isolation.py`` so the policy can be read, reviewed and
tested as the list it is, rather than as a wall inside the validator.

The allowlist is deliberately **value-blind**: admitting a property admits every
value of it. That is why membership here is decided on whether the property can
carry an escape or can make the rendered slide disagree with what the
hybrid-PPTX measurer extracts — not on whether some value of it is tidy.

Three families are refused on purpose, and none of them for security:

``content``
    Puts text outside the DOM. ``lib/authored-hybrid/browser-source.ts`` builds
    native shapes from DOM text, so a slide using it renders correctly and then
    exports missing those words.

``content-visibility`` / ``contain``
    Let the browser skip laying out a subtree. The measurer would report
    geometry for content the reader can see, or none for content it cannot.

``--*`` custom properties
    A separate decision from this list: resolving them happens in the browser,
    while the deck's frozen design tokens are meant to reach the slide as
    literals. Admitting them is a design change, not a widening.

``visibility`` IS admitted. The measurer already skips ``hidden``/``collapse``
subtrees (browser-source.ts), exactly as it skips ``opacity: 0`` — which this
list has always allowed — so refusing it protected nothing and cost real slides.
"""

from __future__ import annotations

#: Properties an authored slide may declare. See the module docstring for what
#: is deliberately absent.
ALLOWED_CSS_PROPERTIES: frozenset[str] = frozenset({
    "align-content", "align-items", "align-self", "background", "background-color",
    "background-image", "background-position", "background-repeat", "background-size",
    "border", "border-bottom", "border-bottom-color", "border-bottom-left-radius",
    "border-bottom-right-radius", "border-bottom-style", "border-bottom-width",
    "border-collapse", "border-color", "border-left", "border-left-color",
    "border-left-style", "border-left-width", "border-radius", "border-right",
    "border-right-color", "border-right-style", "border-right-width", "border-spacing",
    "border-style", "border-top", "border-top-color", "border-top-left-radius",
    "border-top-right-radius", "border-top-style", "border-top-width", "border-width",
    "bottom", "box-shadow", "box-sizing", "clip-path", "color", "column-gap",
    "display", "fill", "fill-opacity", "filter", "flex", "flex-basis", "flex-direction",
    "flex-flow", "flex-grow", "flex-shrink", "flex-wrap", "font", "font-family",
    "font-size", "font-stretch", "font-style", "font-variant", "font-variant-numeric",
    "font-weight", "gap", "grid", "grid-area", "grid-auto-columns", "grid-auto-flow",
    "grid-auto-rows", "grid-column", "grid-column-end", "grid-column-start",
    "grid-row", "grid-row-end", "grid-row-start", "grid-template", "grid-template-areas",
    "grid-template-columns", "grid-template-rows", "height", "inset", "justify-content",
    "justify-items", "justify-self", "left", "letter-spacing", "line-height",
    "list-style", "list-style-position", "margin", "margin-bottom", "margin-left",
    "margin-right", "margin-top", "mask", "max-height", "max-width", "min-height",
    "min-width", "object-fit", "object-position", "opacity", "outline",
    "outline-color", "outline-offset", "outline-style", "outline-width",
    "overflow", "overflow-wrap", "overflow-x", "overflow-y", "padding",
    "padding-bottom", "padding-left", "padding-right", "padding-top", "place-content",
    "place-items", "place-self", "position", "right", "row-gap", "stroke",
    "stroke-dasharray", "stroke-linecap", "stroke-linejoin", "stroke-opacity",
    "stroke-width", "table-layout", "text-align", "text-decoration", "text-decoration-color",
    "text-decoration-line", "text-decoration-style", "text-indent", "text-overflow",
    "text-shadow", "text-transform", "top", "transform", "transform-origin",
    "vertical-align", "white-space", "width", "word-break", "word-spacing",
    "z-index",
    # Box, flow and multi-column layout.
    "aspect-ratio", "float", "clear", "order", "isolation", "visibility",
    "columns", "column-count", "column-width", "column-fill", "column-span",
    "column-rule", "column-rule-color", "column-rule-style", "column-rule-width",
    # Logical (writing-mode relative) box model. The physical forms are already
    # admitted above; a deck authored for vertical or right-to-left Korean text
    # reaches for these instead, and rejecting them would fail the slide for
    # spelling the same box a different way.
    "inline-size", "block-size", "min-inline-size", "min-block-size", "max-inline-size",
    "max-block-size", "margin-inline", "margin-inline-start", "margin-inline-end",
    "margin-block", "margin-block-start", "margin-block-end", "padding-inline",
    "padding-inline-start", "padding-inline-end", "padding-block", "padding-block-start",
    "padding-block-end", "inset-inline", "inset-inline-start", "inset-inline-end",
    "inset-block", "inset-block-start", "inset-block-end", "border-inline",
    "border-inline-start", "border-inline-end", "border-inline-color", "border-inline-style",
    "border-inline-width", "border-block", "border-block-start", "border-block-end",
    "border-block-color", "border-block-style", "border-block-width",
    # Typography. `writing-mode` is what a vertical axis label on a Korean chart
    # needs, and it was the first property observed failing a real deck.
    "writing-mode", "text-orientation", "direction", "hyphens", "line-break",
    "tab-size", "text-wrap", "text-wrap-mode", "text-wrap-style", "word-wrap",
    "text-underline-offset", "text-underline-position", "text-decoration-thickness",
    "text-emphasis", "text-emphasis-color", "text-emphasis-position", "text-emphasis-style",
    "font-feature-settings", "font-variation-settings", "font-kerning",
    "font-optical-sizing", "font-size-adjust", "list-style-type", "list-style-image",
    "text-rendering",
    # Paint and compositing. Every URL-bearing value is already refused by the
    # unsafe-CSS pattern, so these carry geometry and colour only.
    "background-attachment", "background-blend-mode", "background-clip",
    "background-origin", "mix-blend-mode", "backdrop-filter", "image-rendering",
    "box-decoration-break", "print-color-adjust", "border-image", "border-image-outset",
    "border-image-repeat", "border-image-slice", "border-image-source",
    "border-image-width",
    # Individual transform components and 3D context. `transform` itself is
    # already admitted; these are the same geometry written longhand.
    "rotate", "scale", "translate", "transform-box", "transform-style",
    "perspective", "perspective-origin", "backface-visibility",
    # Motion. Inert in this pipeline — a slide is captured as a single static
    # frame and `@keyframes` is refused outright — but models emit these
    # constantly, and failing a slide over a declaration that cannot animate
    # trades a real page for no safety.
    "transition", "transition-delay", "transition-duration", "transition-property",
    "transition-timing-function", "animation", "animation-delay", "animation-direction",
    "animation-duration", "animation-fill-mode", "animation-iteration-count",
    "animation-name", "animation-play-state", "animation-timing-function",
    # SVG presentation attributes used as CSS. Diagram-heavy proposal decks build
    # their own inline charts, and the existing stroke/fill set stops short of
    # what labelling an axis takes.
    "stroke-dashoffset", "stroke-miterlimit", "fill-rule", "clip-rule",
    "paint-order", "dominant-baseline", "alignment-baseline", "text-anchor",
    "vector-effect", "shape-rendering", "stop-color", "stop-opacity", "flood-color",
    "flood-opacity", "lighting-color",
    # Tables.
    "caption-side", "empty-cells",
    # Interaction hints. Meaningless to a headless single-frame capture, and
    # harmless, but habitually emitted.
    "pointer-events", "user-select", "cursor",
    # Vendor prefixes that current models still emit by reflex. Each mirrors a
    # standard property already admitted here.
    "-webkit-font-smoothing", "-moz-osx-font-smoothing", "-webkit-background-clip",
    "-webkit-text-fill-color", "-webkit-text-stroke", "-webkit-text-stroke-color",
    "-webkit-text-stroke-width", "-webkit-line-clamp", "-webkit-box-orient",
    "-webkit-box-decoration-break", "-webkit-print-color-adjust", "-webkit-backdrop-filter",
    "-webkit-user-select", "-webkit-mask", "-webkit-mask-image", "-webkit-mask-size",
    "-webkit-mask-repeat", "-webkit-mask-position",
})


__all__ = ["ALLOWED_CSS_PROPERTIES"]
