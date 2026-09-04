"""Composer: server-owned composition for ``source_content_restyle``.

The Composer renders a source page into one slide from a typed plan. Content
is data read from the immutable source manifest; layout is code in this
package; fit is solved by a deterministic ladder and confirmed in Chromium;
the attributes the readback and export gates check (``data-qid``,
``data-semantic-role``, ``data-source-block-id``) are written by the renderer
itself, so those gates run as assertions and a failure is a renderer bug.

Design record: ``docs/generation-quality-root-cause-and-composer-architecture-20260904.md``.

Module map (each file stays under 350 lines behind this facade):

- ``plan``       typed plan and receipt models shared by every stage
- ``contracts``  stage signatures and the attribute contract the renderer honors
- ``classify``   manifest unit -> ``ComposerSlidePlan`` (deterministic, no model);
  helpers ``classify_bindings``, ``classify_common``, ``classify_furniture``,
  ``classify_groups``, ``classify_title``, ``classify_summary``
- ``render``     plan + tokens + fit parameters -> HTML; helpers ``render_css``,
  ``render_text``, ``render_context``, ``render_components``, ``render_table``,
  ``render_chrome`` (band/content-area composition, backed by ``render_flow``
  for source-order segments and ``render_image_region`` for the two image
  regions), ``render_geometry``, ``render_cover`` (the cover archetype) and
  ``render_title`` (the title's own fit axis)
- ``fit``        the fit ladder loop; ``fit_ladder`` the pure step generator
- ``estimate``   font-metric fit estimate run before any Chromium call
- ``measure``    Chromium measurement wrapper and the blocking-defect predicate
- ``pipeline``   deck loop, assertions, publication; ``pipeline_inputs`` derives
  units, bindings, tokens and deck context from the admitted request
- ``publish``    persistence and the shared authored publication call;
  ``publish_semantic`` derives the semantic draft from rendered HTML
- ``receipt``    ``composer-receipt/v1`` and the pipeline error types

Offline evaluation: ``scripts/composer_offline_eval.py`` (no provider calls).
"""

from services.composer.plan import (
    ChromeLevel,
    ComposerComponent,
    ComposerDeckPlan,
    ComposerSlidePlan,
    ComposerTokens,
    FitParameters,
    FitReceipt,
    FitRung,
    Paragraph,
    SourceRect,
    SourceRunRef,
    TextRun,
)

__all__ = [
    "ChromeLevel",
    "ComposerComponent",
    "ComposerDeckPlan",
    "ComposerSlidePlan",
    "ComposerTokens",
    "FitParameters",
    "FitReceipt",
    "FitRung",
    "Paragraph",
    "SourceRect",
    "SourceRunRef",
    "TextRun",
]
