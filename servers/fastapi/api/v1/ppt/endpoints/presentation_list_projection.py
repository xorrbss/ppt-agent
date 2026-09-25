"""Dashboard projections that withhold source and diagnostic payloads in SQL."""
from typing import List
from pydantic import BaseModel
from sqlalchemy import JSON, String, case, cast, column, func, literal, select, type_coerce
from models.presentation_with_slides import PresentationWithSlides
from models.sql.slide import SlideModel

# The deck list renders each cover slide from ``content``/``properties``; it has
# never read ``SlideModel.html_content``.  Authored decks store a whole HTML
# document there, so carrying it made the list response ~93% authored HTML that
# no caller used. Content is supplied separately after SQL removes SVG receipts;
# the remaining columns are carried by name to preserve cover rendering.
_LIST_SLIDE_CARRIED_FIELDS = tuple(
    name for name in SlideModel.model_fields if name not in {"html_content", "content"}
)


def _postgres_list_json_projection(value, paths: tuple[tuple[str, ...], ...]):
    """Filter JSON objects without materializing large payloads as JSONB.

    Only objects on a removed path are expanded. Each correlated aggregate
    reconstructs that object once, preserving JSON values and untouched keys.
    """
    empty_object = cast(literal("{}"), JSON)
    is_object = func.json_typeof(value) == "object"
    entries = func.json_each(
        case((is_object, value), else_=empty_object)
    ).table_valued(column("key", String), column("value", JSON)).alias()
    removed = [path[0] for path in paths if len(path) == 1]
    nested = {}
    for path in paths:
        if len(path) > 1 and path[0] not in removed:
            nested.setdefault(path[0], []).append(path[1:])
    projected_value = entries.c.value
    if nested:
        projected_value = case(
            *(
                (entries.c.key == key,
                 _postgres_list_json_projection(entries.c.value, tuple(child_paths)))
                for key, child_paths in nested.items()
            ),
            else_=entries.c.value,
        )
    aggregate = select(func.json_object_agg(entries.c.key, projected_value)).select_from(entries)
    if removed:
        aggregate = aggregate.where(entries.c.key.not_in(removed))
    projected = func.coalesce(
        aggregate.correlate_except(entries).scalar_subquery(), empty_object
    )
    # Preserve SQL NULL, JSON null and other scalar roots unchanged.
    return type_coerce(case((is_object, projected), else_=value), JSON)


def _list_json_projection(column, paths: tuple[tuple[str, ...], ...], dialect: str):
    """Remove large, detail-only JSON paths before the database sends the row.

    Keep the ORM entity in the caller's SELECT so owner/tenant loader criteria
    still apply. The projected value must only be used on transient copies.
    """
    if dialect == "postgresql":
        return _postgres_list_json_projection(column, paths)
    if dialect == "sqlite":
        return type_coerce(
            func.json_remove(column, *("$." + ".".join(path) for path in paths)),
            JSON,
        )
    raise ValueError(f"Unsupported dashboard projection dialect: {dialect}")


def _list_theme_projection(dialect: str):
    from models.sql.presentation import PresentationModel

    return _list_json_projection(
        PresentationModel.theme,
        (
            ("svg", "quality"),
            ("svg", "initialQuality"),
            ("svg", "styleSnapshot"),
            ("authoredQualityGate",),
            ("authored_vnext", "scene"),
        ),
        dialect,
    ).label("list_theme")


def _list_content_projection(dialect: str):
    return _list_json_projection(
        SlideModel.content, (("svgReceipt",),), dialect,
    ).label("list_content")


def _list_slide_without_html(slide: SlideModel, *, content: dict) -> SlideModel:
    """Copy a cover slide for the list response, withholding authored HTML.

    Returns a transient row instead of blanking the loaded one: assigning to a
    persistent instance marks it dirty, and the next query on the same session
    autoflushes ``UPDATE slides SET html_content=NULL`` — a read endpoint one
    stray commit away from erasing authored decks.  ``html_content`` is never
    read here, so the deferred column is never lazily loaded either.
    """

    carried = {name: getattr(slide, name) for name in _LIST_SLIDE_CARRIED_FIELDS}
    return SlideModel(**carried, content=content, html_content=None)


class PresentationListPage(BaseModel):
    items: List[PresentationWithSlides]
    total: int
    page: int
    page_size: int


def _escaped_like_fragment(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
