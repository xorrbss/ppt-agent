"""Small dashboard reads, separate from the compatibility deck list."""

from dataclasses import asdict
from copy import deepcopy

from fastapi import HTTPException
from sqlalchemy import JSON, func, type_coerce
from sqlalchemy.orm import defer, load_only
from sqlmodel import select

from api.v1.ppt.endpoints.presentation_list_projection import (
    DashboardPresentationCover,
    DashboardPresentationListItem,
    DashboardPresentationListPage,
    _list_json_projection,
    _list_slide_without_html,
)
from models.presentation_profiles import PresentationProfileError, resolve_presentation_driver
from models.sql.presentation import PRESENTATION_LIFECYCLE_PUBLISHED, PresentationModel
from models.sql.slide import SlideModel
from services.authored_canvas import presentation_canvas_metadata


_CARD_FIELDS = (
    "id", "title", "n_slides", "language", "created_at", "updated_at", "mode",
    "version", "revision", "lifecycle_status", "generation_mode",
    "profile_driver_id", "profile_schema_version",
)


def _json_path(value, path, dialect):
    if dialect == "postgresql":
        return type_coerce(func.json_extract_path(value, *path), JSON)
    if dialect == "sqlite":
        return type_coerce(func.json_quote(func.json_extract(value, "$." + ".".join(path))), JSON)
    raise ValueError(f"Unsupported dashboard projection dialect: {dialect}")


def _path_present(value, path, dialect):
    if dialect == "postgresql":
        return func.json_extract_path(value, *path).is_not(None)
    return func.json_type(value, "$." + ".".join(path)).is_not(None)


def _theme_columns(dialect):
    theme = PresentationModel.theme
    root_type = func.json_typeof(theme) if dialect == "postgresql" else func.json_type(theme)
    return (
        root_type,
        _json_path(theme, ("mode",), dialect),
        _json_path(theme, ("documentCanvas",), dialect),
        _path_present(theme, ("documentCanvas",), dialect),
        _json_path(theme, ("authored_vnext", "canvas"), dialect),
        _path_present(theme, ("authored_vnext", "canvas"), dialect),
    )


def _compact_theme(values):
    root_type, mode, document_canvas, has_document_canvas, canvas, has_canvas = values
    if root_type not in (None, "null", "object"):
        # Preserve fail-closed driver validation without transferring a scalar
        # or malformed root that might itself carry a large diagnostic value.
        return []
    theme = {}
    if mode is not None:
        theme["mode"] = mode
    if has_document_canvas:
        theme["documentCanvas"] = document_canvas
    if has_canvas:
        theme["authored_vnext"] = {"canvas": canvas}
    return theme


def _card_query(dialect):
    # Retaining the ORM entity preserves the central owner loader criteria.
    # Every other field is deferred and forbidden from lazy loading, including
    # the original theme and large outlines/layout/source metadata.
    return select(PresentationModel).options(
        load_only(
            *(getattr(PresentationModel, name) for name in _CARD_FIELDS),
            PresentationModel.dashboard_metadata, raiseload=True,
        )
    )


async def _card_themes(sql_session, presentations, dialect):
    themes = {}
    missing = []
    for presentation in presentations:
        metadata = presentation.dashboard_metadata
        if isinstance(metadata, dict) and metadata.get("schema_version") == 1 and "theme" in metadata:
            themes[presentation.id] = deepcopy(metadata["theme"])
        else:
            missing.append(presentation.id)
    if missing:
        # Compatibility for metadata-created databases and an older reader's
        # NULL rows. The migration backfills these once; cache hits never read
        # or parse the large original theme.
        query = select(PresentationModel, *_theme_columns(dialect)).where(
            PresentationModel.id.in_(missing)
        ).options(load_only(PresentationModel.id, raiseload=True))
        for presentation, *theme_values in (await sql_session.execute(query)).all():
            themes[presentation.id] = _compact_theme(theme_values)
    return themes


async def dashboard_presentation_page(sql_session, *, filters, order_columns, page, page_size):
    dialect = sql_session.get_bind().dialect.name
    query = (
        _card_query(dialect).where(*filters).order_by(*order_columns)
        .offset((page - 1) * page_size).limit(page_size)
    )
    rows = list((await sql_session.execute(query)).scalars().all())
    themes = await _card_themes(sql_session, rows, dialect)
    thumbnails = {}
    if rows:
        cover_query = (
            select(
                SlideModel,
                _json_path(SlideModel.content, ("image",), dialect),
                _json_path(SlideModel.properties, ("image",), dialect),
            )
            .where(SlideModel.presentation.in_([row.id for row in rows]), SlideModel.index == 0)
            .order_by(SlideModel.presentation, SlideModel.id)
            .options(load_only(SlideModel.id, SlideModel.presentation, raiseload=True))
        )
        for cover, content_image, properties_image in (await sql_session.execute(cover_query)).all():
            image = content_image or properties_image
            thumbnails.setdefault(cover.presentation, image if isinstance(image, str) else None)

    items = []
    for presentation in rows:
        identity = {name: getattr(presentation, name) for name in _CARD_FIELDS}
        theme = themes[presentation.id]
        identity["theme"] = theme
        try:
            profile, capabilities, _ = resolve_presentation_driver(identity)
        except PresentationProfileError:
            identity.update(profile=None, capabilities=None, theme=None, unsupported_template_format=True)
        else:
            # Reuse the canonical canvas validator and default geometry. It
            # reads only the two compact canvas paths retained by the query.
            canvas = presentation_canvas_metadata(theme)
            if "documentCanvas" in theme:
                theme["documentCanvas"] = canvas
            else:
                theme["authored_vnext"] = {"canvas": canvas}
            identity.update(profile=asdict(profile), capabilities=asdict(capabilities))
        identity["thumbnail_url"] = thumbnails.get(presentation.id)
        items.append(DashboardPresentationListItem(**identity))
    total = int(await sql_session.scalar(select(func.count(PresentationModel.id)).where(*filters)) or 0)
    return DashboardPresentationListPage(items=items, total=total, page=page, page_size=page_size)


async def dashboard_presentation_cover(sql_session, presentation_id, canvas_contracts=None):
    dialect = sql_session.get_bind().dialect.name
    row = (await sql_session.execute(
        _card_query(dialect).where(
            PresentationModel.id == presentation_id,
            PresentationModel.lifecycle_status == PRESENTATION_LIFECYCLE_PUBLISHED,
        )
    )).scalars().first()
    if row is None:
        raise HTTPException(status_code=404, detail="Presentation not found")
    presentation = row
    theme = (await _card_themes(sql_session, [presentation], dialect))[presentation.id]
    from services.document_canvas import require_canvas_capability
    require_canvas_capability(theme, canvas_contracts)
    identity = {name: getattr(presentation, name) for name in _CARD_FIELDS}
    identity["theme"] = theme
    try:
        resolve_presentation_driver(identity)
    except PresentationProfileError as error:
        raise HTTPException(status_code=409, detail=error.code) from error
    cover = (await sql_session.execute(
        select(SlideModel, _list_json_projection(
            SlideModel.content, (("svgReceipt",), ("svgAssetManifest",)), dialect,
        ))
        .where(SlideModel.presentation == presentation_id, SlideModel.index == 0)
        .order_by(SlideModel.id).limit(1)
        .options(defer(SlideModel.content, raiseload=True))
    )).first()
    slide = None if cover is None else _list_slide_without_html(
        cover[0], content=cover[1]
    ).model_copy(update={"html_content": cover[0].html_content})
    return DashboardPresentationCover(slide=slide, theme=theme)
