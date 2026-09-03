"""Plan-level summaries: semantic-role refinement, archetype and character totals.

These read the components a plan already holds and never look back at the
manifest, so the facade can call them last.
"""

from __future__ import annotations

from collections.abc import Sequence
import statistics

from services.composer.classify_groups import WIDE_SHARE
from services.composer.plan import Archetype, ComposerComponent, Paragraph

SUPPORTING_RATIO = 0.7
LIST_ARCHETYPE_SHARE = 0.6
IMAGE_GRID_MIN_IMAGES = 3
IMAGE_GRID_MAX_CHARS = 160
COVER_MAX_PARAGRAPHS = 4
COVER_MAX_CHARS = 200
COVER_MAX_IMAGES = 1
COVER_MAX_IMAGE_AREA_SHARE = 0.08
COVER_MAX_KPI = 3

_LIST_KINDS = frozenset({"paragraph", "bullets", "numbered"})
_TEXT_ONLY_KINDS = frozenset({"paragraph", "bullets", "numbered", "caption"})
_COVER_EXCLUDED_KINDS = frozenset({"bullets", "numbered", "table", "comparison", "process"})


def mark_supporting(components: Sequence[ComposerComponent]) -> None:
    """Rule 11: body text set well below the body median size is supporting copy."""

    sizes = [
        component.source_size_pt
        for component in components
        if component.semantic_role == "body" and component.source_size_pt
    ]
    if len(sizes) < 2:
        return
    threshold = SUPPORTING_RATIO * statistics.median(sizes)
    for component in components:
        if component.kind in _LIST_KINDS and component.source_size_pt and component.source_size_pt < threshold:
            component.semantic_role = "supporting"


def component_paragraphs(component: ComposerComponent) -> list[Paragraph]:
    paragraphs = list(component.paragraphs)
    paragraphs.extend(paragraph for group in component.groups for paragraph in group)
    paragraphs.extend(header for header in component.group_headers if header is not None)
    paragraphs.extend(cell for row in component.table_rows for cell in row)
    return paragraphs


def component_chars(component: ComposerComponent) -> int:
    """Normalized characters of every bound run the component carries."""

    return sum(len(paragraph.text) for paragraph in component_paragraphs(component))


def is_cover(
    title: ComposerComponent | None,
    subtitle: ComposerComponent | None,
    components: Sequence[ComposerComponent],
    images: Sequence[ComposerComponent],
    *,
    slide_index: int,
    title_placeholder: str | None,
) -> bool:
    """Rule 9a: a title page, checked before the general archetype rules.

    A cover needs a title that either opens the deck or carries the centered
    title placeholder; no list, table, comparison or process component; up to
    ``COVER_MAX_KPI`` KPI callouts (large stat numbers pulled out of the title
    zone -- see ``classify_title``) on top of remaining text (subtitle plus
    body) that is at most ``COVER_MAX_PARAGRAPHS`` short paragraphs totalling
    ``COVER_MAX_CHARS`` characters, each carried by a block no wider than
    ``WIDE_SHARE`` of the page -- a label, tagline or KPI beside the title,
    not a full-width body paragraph; and at most one image, sized like a
    small logo rather than cover art.
    """

    if title is None:
        return False
    if title_placeholder != "ctrTitle" and slide_index != 0:
        return False
    kpi_components = [component for component in components if component.kind == "kpi"]
    body_components = [component for component in components if component.kind != "kpi"]
    if len(kpi_components) > COVER_MAX_KPI:
        return False
    if any(component.kind in _COVER_EXCLUDED_KINDS for component in body_components):
        return False
    text_components = ([subtitle] if subtitle is not None else []) + body_components
    if any(
        component.source_rect is None or component.source_rect.width >= WIDE_SHARE
        for component in (*text_components, *kpi_components)
    ):
        return False
    paragraphs = [paragraph for component in text_components for paragraph in component_paragraphs(component)]
    if len(paragraphs) > COVER_MAX_PARAGRAPHS:
        return False
    if sum(len(paragraph.text) for paragraph in paragraphs) > COVER_MAX_CHARS:
        return False
    if len(images) > COVER_MAX_IMAGES:
        return False
    return all(
        image.source_rect is not None
        and image.source_rect.width * image.source_rect.height <= COVER_MAX_IMAGE_AREA_SHARE
        for image in images
    )


def archetype_of(
    components: Sequence[ComposerComponent],
    images: Sequence[ComposerComponent],
    body_chars: int,
    *,
    has_text: bool,
) -> Archetype:
    """Rule 10, checked in this order: image_only, image_grid, table, comparison,
    process, list (60% of list-kind paragraphs are items), text, else mixed."""

    kinds = {component.kind for component in components}
    if not has_text and not components:
        return "image_only" if images else "text"
    if len(images) >= IMAGE_GRID_MIN_IMAGES and body_chars <= IMAGE_GRID_MAX_CHARS:
        return "image_grid"
    if "table" in kinds:
        return "table"
    if "comparison" in kinds:
        return "comparison"
    if "process" in kinds:
        return "process"
    paragraphs = [
        paragraph for component in components if component.kind in _LIST_KINDS for paragraph in component.paragraphs
    ]
    items = sum(1 for paragraph in paragraphs if paragraph.bullet)
    if paragraphs and items >= LIST_ARCHETYPE_SHARE * len(paragraphs):
        return "list"
    if not images and kinds <= _TEXT_ONLY_KINDS:
        return "text"
    return "mixed"


__all__ = ["archetype_of", "component_chars", "component_paragraphs", "is_cover", "mark_supporting"]
