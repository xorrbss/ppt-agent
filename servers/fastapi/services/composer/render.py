"""Composer renderer: plan + tokens + fit parameters -> one admitted slide document.

This is the ``Renderer`` of ``services.composer.contracts``. The document it
returns is exactly 1280x720, carries ``data-qid`` / ``data-semantic-role`` /
``data-source-block-id`` on every element it creates, and has already passed
``utils.renderer_isolation.admit_authored_html`` and
``utils.authored_dom_quality.validate_declared_authored_qids``; a rejection is
raised as :class:`ComposerRenderError` because it is a renderer bug, never a
reason to retry a model.

Module map (thin facade over):

- ``render_css``        chrome geometry, density, type sizes, stylesheet
- ``render_text``       bound text elements, markers, QID namespaces
- ``render_context``    per-render state shared by the component renderers
- ``render_components`` archetype markup (text, list, process, comparison, image)
- ``render_table``      the native-table contract
- ``render_chrome``     title/footer bands, furniture, the content area
- ``render_flow``       source-order segments, balanced columns, wide rows
- ``render_image_region`` the fixed-height image grid and the flex image row
- ``render_geometry``   rung 5 source-rectangle placement
- ``render_title``      title/subtitle fit, independent of the body type scale
- ``render_cover``      the ``cover`` archetype: a centred title/subtitle stack
"""

from __future__ import annotations

import re

from services.composer.plan import (
    ComposerDeckPlan,
    ComposerSlidePlan,
    ComposerTokens,
    FitParameters,
)
from services.composer.render_chrome import balanced_partition, layout_chrome, render_document
from services.composer.render_context import RenderContext
from services.composer.render_css import (
    MIN_FONT_PX,
    TITLE_LEFT_PX,
    ChromeGeometry,
    DensitySpec,
    chrome_geometry,
    density_spec,
    stylesheet,
    template_px,
    text_px,
)
from services.composer.render_geometry import layout_source_geometry, page_box, rect_box
from services.composer.render_text import BindingRegistry, QidFactory, TextRenderError
from services.composer.render_title import fitted_title_px, subtitle_px_from_title
from utils.authored_dom_quality import validate_declared_authored_qids
from utils.renderer_isolation import RendererPolicyError, admit_authored_html

#: The one authoring-only marker the renderer writes. Like
#: ``utils.llm_calls.author_slide``, admission is checked on a projection
#: without it; the source-image stage resolves it to a ``src`` and strips it
#: before final admission.
_ASSET_MARKER = re.compile(r'\sdata-asset-id="[^"]*"')


class ComposerRenderError(ValueError):
    """The renderer produced, or was asked for, something the gates refuse."""

    def __init__(self, code: str, *, detail: str = "") -> None:
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code
        self.detail = detail


def admission_projection(html: str) -> str:
    """The document as the admission policy sees it once image slots are resolved."""

    return _ASSET_MARKER.sub("", html)


def validate_rendered_html(html: str) -> tuple[str, ...]:
    """Run the admission and QID gates; return the declared QIDs."""

    try:
        admit_authored_html(admission_projection(html))
    except RendererPolicyError as error:
        raise ComposerRenderError("composer_render_not_admitted", detail=str(error)) from error
    try:
        return validate_declared_authored_qids(html)
    except ValueError as error:
        raise ComposerRenderError("composer_render_qid_invalid", detail=str(error)) from error


def build_context(
    plan: ComposerSlidePlan,
    tokens: ComposerTokens,
    params: FitParameters,
    deck: ComposerDeckPlan,
) -> RenderContext:
    """Geometry and density for one render; rung 5 always uses the dense variant."""

    density = density_spec(1 if params.source_geometry else params.density)
    geometry = chrome_geometry(params.chrome_level)
    title_px = fitted_title_px(plan, deck, params, geometry)
    return RenderContext(
        tokens=tokens,
        params=params,
        deck=deck,
        geometry=geometry,
        density=density,
        registry=BindingRegistry(QidFactory()),
        title_px=title_px,
        subtitle_px=subtitle_px_from_title(title_px),
    )


def render_slide(
    plan: ComposerSlidePlan,
    tokens: ComposerTokens,
    params: FitParameters,
    deck: ComposerDeckPlan,
) -> str:
    """Render one slide and prove it passes the gates the pipeline will run."""

    ctx = build_context(plan, tokens, params, deck)
    try:
        body = layout_source_geometry(plan, ctx) if params.source_geometry else layout_chrome(plan, ctx)
    except TextRenderError as error:
        raise ComposerRenderError("composer_render_plan_invalid", detail=str(error)) from error
    html = render_document(body, stylesheet(tokens, ctx.geometry, ctx.density))
    validate_rendered_html(html)
    return html


__all__ = [
    "MIN_FONT_PX",
    "TITLE_LEFT_PX",
    "ChromeGeometry",
    "ComposerRenderError",
    "DensitySpec",
    "RenderContext",
    "admission_projection",
    "balanced_partition",
    "build_context",
    "chrome_geometry",
    "density_spec",
    "page_box",
    "rect_box",
    "render_slide",
    "template_px",
    "text_px",
    "validate_rendered_html",
]
