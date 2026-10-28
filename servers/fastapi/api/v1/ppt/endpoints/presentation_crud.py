"""Presentation CRUD endpoints: list / fetch / delete / create.

Split out of the presentation god-file. These are the plain persistence
operations on a PresentationModel (no generation or export pipeline).
"""

import uuid
from typing import Annotated, List, Literal, Optional

from fastapi import APIRouter, Body, Depends, Header, HTTPException, Query, Request, Response
from sqlalchemy import func, or_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import defer
from sqlmodel import select
from models.generate_presentation_request import DesignPackPinInput, SvgTemplatePinInput
from models.svg_canvas import SvgCanvasInput
from models.common_header_design import CommonHeaderDesignInput

from api.v1.ppt.endpoints.presentation_helpers import resolve_presentation_fonts
from api.v1.ppt.endpoints.presentation_crud_helpers import (
    _get_presentation_with_slides as _get_presentation_with_slides,
    apply_presentation_delete as _apply_presentation_delete,
)
from api.v1.ppt.endpoints.presentation_generation_context import (
    generation_context_fields,
    validate_previous_attempt,
)
from api.v1.ppt.endpoints.presentation_create_contracts import (
    attachment_file_name as _attachment_file_name,
    normalize_presentation_create_theme as _normalize_presentation_create_theme,
)
from constants.presentation import (
    DEFAULT_ATTACHMENT_GENERATION_PROMPT,
    MAX_NUMBER_OF_SLIDES,
)
from enums.tone import Tone
from enums.verbosity import Verbosity
from models.presentation_generation_info import (
    PresentationGenerationInfo,
    PresentationGenerationSettings,
)
from models.presentation_with_slides import PresentationWithSlides
from models.document_canvas import DOCUMENT_CANVAS_CONTRACTS_HEADER
from models.sql.presentation import (
    PRESENTATION_LIFECYCLE_PUBLISHED,
    PRESENTATION_LIFECYCLE_STAGING,
    PresentationModel,
)
from models.sql.slide import SlideModel
from models.sql.workspace import (
    WorkspaceAsset,
    WorkspaceOperation,
    get_workspace_owner_scope,
)
from models.workspace import WorkspaceBootstrapRequest
from services.database import get_async_session, sql_engine
from services.authored_canvas import merge_presentation_canvas
from services.compatibility_migration.admission import (
    stamp_new_authored_presentation,
)
from services.presentation_deletion_service import PresentationDeletionService as PresentationDeletionService
from services.presentation_access_service import require_live_presentation
from models.presentation_profiles import PresentationProfileError, resolve_presentation_driver
from services.presentation_export_projection import (
    PRESENTATION_EXPORT_PROJECTION,
    PRESENTATION_EXPORT_PROJECTION_HEADER,
    project_presentation_for_export,
)
from services.references import (
    ReferenceProviderError,
    admit_reference_snapshots,
    claim_reference_snapshots,
)
from services.workspace_asset_service import (
    import_workspace_document_asset,
    inspect_workspace_document_asset,
)
from services.workspace_legacy_write_gate import (
    require_legacy_presentation_write_path,
)
from services.workspace_runtime_profile import (
    UnsupportedWorkspaceRuntimeError,
    require_supported_workspace_runtime,
)
from services.workspace_service import (
    WorkspaceServiceError,
    bootstrap_workspace,
    workspace_idempotency_hash,
)
from utils.document_path_security import validate_document_paths
from utils.owner_context import get_current_owner_id
from api.v1.ppt.endpoints.presentation_draft_preview import PRESENTATION_DRAFT_PREVIEW_ROUTER
from api.v1.ppt.endpoints.presentation_export_attempts import (
    PRESENTATION_EXPORT_ATTEMPT_ROUTER,
)
from api.v1.ppt.endpoints.presentation_list_projection import (
    PresentationListItem, PresentationListPage, UnsupportedPresentationListItem,
    DashboardPresentationListPage, DashboardPresentationCover,
    _escaped_like_fragment, _list_slide_without_html,
    _list_theme_projection, _list_content_projection,
)
from api.v1.ppt.endpoints.presentation_dashboard_projection import (
    dashboard_presentation_page, dashboard_presentation_cover,
)

PRESENTATION_CRUD_ROUTER = APIRouter(prefix="/presentation", tags=["Presentation"])
PRESENTATION_CRUD_ROUTER.include_router(PRESENTATION_EXPORT_ATTEMPT_ROUTER)
PRESENTATION_CRUD_ROUTER.include_router(PRESENTATION_DRAFT_PREVIEW_ROUTER)


def _chat_first_create_enabled() -> bool:
    """Fail closed when the SQL runtime cannot host the operation store."""

    try:
        require_supported_workspace_runtime(sql_engine.url)
    except UnsupportedWorkspaceRuntimeError as error:
        raise HTTPException(status_code=501, detail=error.code) from error
    return True


@PRESENTATION_CRUD_ROUTER.get(
    "/all",
    response_model=List[PresentationListItem] | PresentationListPage | DashboardPresentationListPage,
)
async def get_all_presentations(
    sql_session: AsyncSession = Depends(get_async_session),
    q: Annotated[Optional[str], Query(max_length=200)] = None,
    page: Annotated[Optional[int], Query(ge=1)] = None,
    page_size: Annotated[Optional[int], Query(ge=1, le=100)] = None,
    sort: Annotated[Optional[Literal["asc", "desc"]], Query()] = None,
    view: Annotated[Optional[Literal["dashboard"]], Query()] = None,
):
    """List published decks with optional server search and pagination.

    The original no-query call keeps returning a bare list for older clients.
    Supplying any list option enables the paginated envelope used by the
    dashboard.  Count and page queries share the exact same predicates, while
    cover slides are fetched in one bounded query so missing/duplicate covers
    cannot skew ``total``.
    """

    legacy_response = q is None and page is None and page_size is None and sort is None and view is None
    normalized_q = (q or "").strip()
    resolved_page = page or 1
    resolved_page_size = page_size or 24
    resolved_sort = sort or "desc"

    filters = [
        PresentationModel.lifecycle_status == PRESENTATION_LIFECYCLE_PUBLISHED
    ]
    if normalized_q:
        pattern = f"%{_escaped_like_fragment(normalized_q)}%"
        filters.append(
            or_(
                PresentationModel.title.ilike(pattern, escape="\\"),
                PresentationModel.content.ilike(pattern, escape="\\"),
            )
        )

    order_columns = (
        (PresentationModel.updated_at.asc(), PresentationModel.id.asc())
        if resolved_sort == "asc"
        else (PresentationModel.updated_at.desc(), PresentationModel.id.desc())
    )
    dialect = sql_session.get_bind().dialect.name
    if view == "dashboard":
        return await dashboard_presentation_page(
            sql_session, filters=filters, order_columns=order_columns,
            page=resolved_page, page_size=resolved_page_size,
        )
    presentation_query = (
        select(PresentationModel, _list_theme_projection(dialect))
        .where(*filters)
        .order_by(*order_columns)
        .options(defer(PresentationModel.theme, raiseload=True))
    )
    if not legacy_response:
        presentation_query = presentation_query.offset(
            (resolved_page - 1) * resolved_page_size
        ).limit(resolved_page_size)

    presentations = list((await sql_session.execute(presentation_query)).all())
    cover_by_presentation: dict[uuid.UUID, SlideModel] = {}
    if presentations:
        cover_query = (
            select(SlideModel, _list_content_projection(dialect))
            .where(
                SlideModel.presentation.in_(
                    [presentation.id for presentation, _theme in presentations]
                ),
                SlideModel.index == 0,
            )
            .order_by(SlideModel.presentation, SlideModel.id)
            # Keep the column out of the SELECT rather than fetching and
            # discarding it. ``raiseload`` makes accidental reads fail loudly.
            .options(
                defer(SlideModel.html_content, raiseload=True),
                defer(SlideModel.content, raiseload=True),
            )
        )
        for cover, content in (await sql_session.execute(cover_query)).all():
            cover_by_presentation.setdefault(
                cover.presentation, _list_slide_without_html(cover, content=content)
            )

    presentations_with_slides: list[PresentationListItem] = []
    for presentation, theme in presentations:
        first_slide = cover_by_presentation.get(presentation.id)
        slides = [] if first_slide is None else [first_slide]
        identity = presentation.model_dump(exclude={"fonts", "theme"})
        identity["theme"] = theme
        try:
            resolve_presentation_driver(identity)
        except PresentationProfileError:
            # A pre-removal row (e.g. an old Template V2 deck) has no
            # supported driver. Mark this row instead of failing the whole
            # dashboard list for every other, resolvable presentation.
            presentations_with_slides.append(
                UnsupportedPresentationListItem(
                    id=presentation.id,
                    title=presentation.title,
                    created_at=presentation.created_at,
                    updated_at=presentation.updated_at,
                    lifecycle_status=presentation.lifecycle_status,
                )
            )
            continue
        fonts = await resolve_presentation_fonts(presentation, slides, sql_session)
        presentations_with_slides.append(
            PresentationWithSlides(**identity, slides=slides, fonts=fonts)
        )

    if legacy_response:
        return presentations_with_slides
    total = int(
        (
            await sql_session.scalar(
                select(func.count(PresentationModel.id)).where(*filters)
            )
        )
        or 0
    )
    return PresentationListPage(
        items=presentations_with_slides,
        total=total,
        page=resolved_page,
        page_size=resolved_page_size,
    )


@PRESENTATION_CRUD_ROUTER.get("/{id}/cover", response_model=DashboardPresentationCover)
async def get_presentation_cover(
    id: uuid.UUID, sql_session: AsyncSession = Depends(get_async_session),
    canvas_contracts: Annotated[Optional[str], Header(alias=DOCUMENT_CANVAS_CONTRACTS_HEADER)] = None,
):
    """Fetch one cover only when a dashboard card has no usable saved image."""
    return await dashboard_presentation_cover(sql_session, id, canvas_contracts)


@PRESENTATION_CRUD_ROUTER.get("/{id}", response_model=PresentationWithSlides)
async def get_presentation(
    id: uuid.UUID, sql_session: AsyncSession = Depends(get_async_session),
    canvas_contracts: Annotated[Optional[str], Header(alias=DOCUMENT_CANVAS_CONTRACTS_HEADER)] = None,
):
    from services.document_canvas import require_canvas_capability

    presentation = await _get_presentation_with_slides(id, sql_session)
    require_canvas_capability(presentation.theme, canvas_contracts)
    return presentation


@PRESENTATION_CRUD_ROUTER.get(
    "/{id}/export-projection",
    response_model=PresentationWithSlides,
)
async def get_presentation_export_projection(
    id: uuid.UUID,
    response: Response,
    sql_session: AsyncSession = Depends(get_async_session),
    canvas_contracts: Annotated[Optional[str], Header(alias=DOCUMENT_CANVAS_CONTRACTS_HEADER)] = None,
):
    from services.document_canvas import require_canvas_capability

    presentation = await _get_presentation_with_slides(id, sql_session)
    require_canvas_capability(presentation.theme, canvas_contracts)
    response.headers[PRESENTATION_EXPORT_PROJECTION_HEADER] = (
        PRESENTATION_EXPORT_PROJECTION
    )
    return project_presentation_for_export(
        presentation.model_dump(mode="json")
    )


@PRESENTATION_CRUD_ROUTER.get(
    "/{id}/generation-info",
    response_model=PresentationGenerationInfo,
)
async def get_presentation_generation_info(
    id: uuid.UUID, sql_session: AsyncSession = Depends(get_async_session)
):
    presentation = await require_live_presentation(sql_session, id)

    prompt = presentation.content
    generation_operation = await sql_session.scalar(
        select(WorkspaceOperation)
        .where(
            WorkspaceOperation.presentation_id == id,
            WorkspaceOperation.kind == "presentation.generate",
            WorkspaceOperation.owner_scope == get_workspace_owner_scope(),
        )
        .order_by(
            WorkspaceOperation.anchor_position.asc(),
            WorkspaceOperation.created_at.asc(),
        )
        .limit(1)
    )
    if generation_operation is not None:
        # Once a workspace generation exists, ``presentation.content`` may
        # contain the generated outline rather than the user's original input.
        # Do not present that outline (or the automatic attachment fallback)
        # as though it were the original prompt.
        prompt = ""
        original_prompt = generation_operation.request_payload.get("prompt")
        if (
            isinstance(original_prompt, str)
            and original_prompt.strip()
            and original_prompt.strip() != DEFAULT_ATTACHMENT_GENERATION_PROMPT
        ):
            prompt = original_prompt

    attachment_file_names: list[str] = []
    if presentation.reference_snapshot_ids:
        try:
            snapshots = await admit_reference_snapshots(
                sql_session,
                snapshot_ids=[
                    uuid.UUID(str(snapshot_id))
                    for snapshot_id in presentation.reference_snapshot_ids
                ],
            )
            attachment_file_names = [
                str(document.get("name") or "Uploaded document")
                for snapshot in snapshots
                for document in snapshot.payload().get("documents", [])
                if isinstance(document, dict)
            ]
        except (ReferenceProviderError, ValueError):
            attachment_file_names = []
    else:
        attachment_file_names = [
            file_name
            for file_path in presentation.file_paths or []
            if (file_name := _attachment_file_name(file_path))
        ]
    return PresentationGenerationInfo(
        **await generation_context_fields(sql_session, presentation_id=id, operation=generation_operation),
        prompt=prompt,
        attachment_file_names=attachment_file_names,
        settings=PresentationGenerationSettings(
            n_slides=presentation.n_slides,
            language=presentation.language,
            tone=presentation.tone,
            verbosity=presentation.verbosity,
            instructions=presentation.instructions,
            include_table_of_contents=presentation.include_table_of_contents,
            include_title_slide=presentation.include_title_slide,
            web_search=presentation.web_search,
            mode=presentation.mode,
            version=presentation.version,
        ),
    )


async def apply_presentation_delete(id: uuid.UUID, sql_session: AsyncSession) -> None:
    await _apply_presentation_delete(id, sql_session, deletion_service_type=PresentationDeletionService)

@PRESENTATION_CRUD_ROUTER.delete("/{id}", status_code=204)
async def delete_presentation(
    id: uuid.UUID, sql_session: AsyncSession = Depends(get_async_session)
):
    require_legacy_presentation_write_path(id)
    await apply_presentation_delete(id, sql_session)


@PRESENTATION_CRUD_ROUTER.post("/create", response_model=PresentationModel)
async def create_presentation(
    content: Annotated[str, Body()],
    presentation_id: Annotated[Optional[uuid.UUID], Body()] = None,
    previous_presentation_id: Annotated[Optional[uuid.UUID], Body()] = None,
    n_slides: Annotated[Optional[int], Body()] = None,
    language: Annotated[Optional[str], Body()] = None,
    file_paths: Annotated[Optional[List[str]], Body()] = None,
    reference_snapshot_ids: Annotated[Optional[List[uuid.UUID]], Body()] = None,
    tone: Annotated[Tone, Body()] = Tone.DEFAULT,
    verbosity: Annotated[Verbosity, Body()] = Verbosity.STANDARD,
    instructions: Annotated[Optional[str], Body()] = None,
    include_table_of_contents: Annotated[bool, Body()] = False,
    include_title_slide: Annotated[bool, Body()] = True,
    web_search: Annotated[bool, Body()] = False,
    strategy: Annotated[Literal["authored", "svg"], Body()] = "svg",
    common_header_mode: Annotated[Optional[Literal["uniform", "free"]], Body()] = None,
    common_header_design: Annotated[Optional[CommonHeaderDesignInput], Body()] = None,
    common_header_cover_exception: Annotated[bool, Body()] = False,
    theme: Annotated[Optional[dict], Body()] = None,
    aspect_ratio: Annotated[Literal["16:9", "4:3", "9:16", "9:13", "1:1", "21:9"], Body()] = "16:9",
    page_size: Annotated[Optional[Literal["A4"]], Body()] = None,
    orientation: Annotated[Optional[Literal["landscape", "portrait"]], Body()] = None,
    svg_canvas: Annotated[Optional[SvgCanvasInput], Body()] = None,
    svg_design_reference_pin: Annotated[Optional[DesignPackPinInput], Body()] = None,
    svg_design_reference_snapshot_id: Annotated[Optional[uuid.UUID], Body()] = None,
    svg_template_pin: Annotated[Optional[SvgTemplatePinInput], Body()] = None,
    svg_template_mode: Annotated[Optional[Literal["free-design", "template-redesign"]], Body()] = None,
    svg_template_frame_map: Annotated[Optional[dict[str, str]], Body()] = None,
    http_request: Request = None,
    idempotency_key: Annotated[
        Optional[str], Header(alias="Idempotency-Key")
    ] = None,
    sql_session: AsyncSession = Depends(get_async_session),
):
    from api.v1.ppt.endpoints.presentation_create_canvas import admitted_create_canvas

    create_canvas = await admitted_create_canvas(
        http_request, page_size, orientation, aspect_ratio,
        strategy=strategy, svg_canvas=svg_canvas,
    )
    if (svg_template_pin is None) != (svg_template_mode is None):
        raise HTTPException(status_code=422, detail="svg_template_pin_mode_required")
    if svg_template_frame_map is not None and svg_template_mode != "template-redesign":
        raise HTTPException(status_code=422, detail="svg_template_frame_map_requires_redesign")
    if svg_template_pin is not None:
        body = await http_request.json()
        if strategy != "svg" or body.get("strategy") != "svg":
            raise HTTPException(status_code=422, detail="svg_template_strategy_required")
        if svg_design_reference_pin is not None or svg_design_reference_snapshot_id is not None:
            raise HTTPException(status_code=422, detail="svg_template_selection_conflict")
        if svg_template_mode == "template-redesign" and (
            common_header_mode == "uniform" or common_header_design is not None or common_header_cover_exception
        ):
            raise HTTPException(status_code=422, detail="svg_template_common_header_conflict")
    if (svg_design_reference_pin is not None or svg_design_reference_snapshot_id is not None) and strategy != "svg":
        raise HTTPException(status_code=422, detail="svg_design_reference_strategy_required")
    if common_header_mode is not None and strategy != "svg":
        raise HTTPException(status_code=422, detail="common_header_svg_strategy_required")
    if common_header_design is not None:
        if strategy != "svg" or common_header_mode == "free":
            raise HTTPException(status_code=422, detail="common_header_design_requires_svg_uniform")
        # Header references reuse only the separately owner-admitted style
        # reference below; body/document references cannot grant this access.
        if any(reference_id != svg_design_reference_snapshot_id
               for reference_id in common_header_design.reference_snapshot_ids):
            raise HTTPException(status_code=422, detail="svg_common_header_design_reference_admission_required")
    if common_header_cover_exception and common_header_mode != "uniform":
        raise HTTPException(status_code=422, detail="common_header_cover_exception_requires_uniform")
    if svg_design_reference_pin is not None and svg_design_reference_snapshot_id is not None:
        raise HTTPException(status_code=422, detail="svg_design_reference_conflict")
    if svg_design_reference_snapshot_id is not None:
        from services.references.service import admit_reference_snapshot

        try:
            design_reference = await admit_reference_snapshot(
                sql_session, snapshot_id=svg_design_reference_snapshot_id,
            )
        except ReferenceProviderError as error:
            raise HTTPException(status_code=error.status_code, detail=error.code) from error
        if design_reference.provider_id != "uploaded_document" or len(design_reference.asset_bindings) != 1:
            raise HTTPException(status_code=422, detail="svg_uploaded_design_reference_single_asset_required")
    proposed_presentation_id = presentation_id or uuid.uuid4()
    chat_first_enabled = _chat_first_create_enabled()
    if previous_presentation_id is not None and not chat_first_enabled:
        raise HTTPException(status_code=409, detail="generation_attempt_link_unavailable")
    resolved_theme = (
        _normalize_presentation_create_theme(theme) if chat_first_enabled else None
    )
    if file_paths and reference_snapshot_ids:
        raise HTTPException(
            status_code=400,
            detail="presentation_document_source_conflict",
        )
    if file_paths:
        file_paths = validate_document_paths(file_paths)
    admitted_snapshots = ()
    canonical_asset_ids: list[uuid.UUID] = []
    if reference_snapshot_ids:
        try:
            admitted_snapshots = await admit_reference_snapshots(
                sql_session,
                snapshot_ids=reference_snapshot_ids,
            )
        except ReferenceProviderError as error:
            raise HTTPException(
                status_code=error.status_code,
                detail=error.code,
            ) from error
        if any(
            snapshot.provider_id != "uploaded_document"
            for snapshot in admitted_snapshots
        ):
            raise HTTPException(
                status_code=400,
                detail="uploaded_reference_snapshot_required",
            )
        canonical_asset_ids = [
            binding.asset_id
            for snapshot in admitted_snapshots
            for binding in snapshot.asset_bindings
        ]
        if len(canonical_asset_ids) != len(set(canonical_asset_ids)):
            raise HTTPException(
                status_code=400,
                detail="duplicate_reference_asset",
            )

    if n_slides is not None and n_slides < 1:
        raise HTTPException(
            status_code=400,
            detail="Number of slides must be greater than 0",
        )

    if n_slides is not None and n_slides > MAX_NUMBER_OF_SLIDES:
        raise HTTPException(
            status_code=400,
            detail=f"Number of slides cannot be greater than {MAX_NUMBER_OF_SLIDES}",
        )

    if include_table_of_contents and n_slides is not None and n_slides < 3:
        raise HTTPException(
            status_code=400,
            detail="Number of slides cannot be less than 3 if table of contents is included",
        )

    if chat_first_enabled and idempotency_key is not None and presentation_id is None:
        raise HTTPException(
            status_code=400,
            detail="presentation_id_required_for_idempotency",
        )

    # The caller-proposed id is the durable identity the workspace bootstrap and
    # every retry hash against, so it is always the one persisted.
    resolved_presentation_id = proposed_presentation_id
    resolved_idempotency_key = (
        idempotency_key or f"presentation-create:{resolved_presentation_id}"
    )
    language_to_store = (language or "").strip()
    # DB schema stores an int; 0 is used as internal marker for auto slide count.
    n_slides_to_store = n_slides if n_slides is not None else 0

    presentation = PresentationModel(
        id=resolved_presentation_id,
        content=content,
        n_slides=n_slides_to_store,
        language=language_to_store,
        file_paths=file_paths,
        reference_snapshot_ids=(
            [str(snapshot_id) for snapshot_id in reference_snapshot_ids]
            if reference_snapshot_ids
            else None
        ),
        tone=tone.value,
        verbosity=verbosity.value,
        instructions=instructions,
        include_table_of_contents=include_table_of_contents,
        include_title_slide=include_title_slide,
        web_search=web_search,
        theme=resolved_theme,
        lifecycle_status=(
            PRESENTATION_LIFECYCLE_STAGING
            if chat_first_enabled
            else PRESENTATION_LIFECYCLE_PUBLISHED
        ),
    )
    if strategy == "svg":
        from services.compatibility_migration.admission import stamp_new_svg_presentation

        stamp_new_svg_presentation(presentation)
    else:
        stamp_new_authored_presentation(presentation)
    presentation.theme = merge_presentation_canvas(
        presentation.theme,
        create_canvas,
    )
    if svg_template_pin is not None:
        presentation.theme = {**(presentation.theme or {}),
                              "svgTemplatePin": svg_template_pin.model_dump(mode="json", by_alias=True),
                              "svgTemplateMode": svg_template_mode}
        if svg_template_frame_map is not None:
            presentation.theme["svgTemplateFrameMap"] = svg_template_frame_map
    if svg_design_reference_pin is not None:
        presentation.theme = {**(presentation.theme or {}), "svgDesignReferencePin":
                              svg_design_reference_pin.model_dump(mode="json", by_alias=True)}
    if svg_design_reference_snapshot_id is not None:
        presentation.theme = {**(presentation.theme or {}),
                              "svgDesignReferenceSnapshotId": str(svg_design_reference_snapshot_id)}
    if common_header_design is not None:
        presentation.theme = {**(presentation.theme or {}), "commonHeaderDesign":
                              common_header_design.model_dump(mode="json", by_alias=True)}
    # Idempotency comparisons and workspace settings must use the same
    # server-issued theme identity that will be persisted.
    resolved_theme = presentation.theme

    if chat_first_enabled:
        settings = {
            "n_slides": n_slides,
            "language": language,
            "tone": tone.value,
            "verbosity": verbosity.value,
            "instructions": instructions,
            "include_table_of_contents": include_table_of_contents,
            "include_title_slide": include_title_slide,
            "web_search": web_search,
            "aspect_ratio": aspect_ratio,
        }
        if strategy == "svg" and common_header_mode is not None:
            settings["common_header_mode"] = common_header_mode
            settings["common_header_cover_exception"] = common_header_cover_exception
        if common_header_design is not None:
            settings["common_header_design"] = common_header_design.model_dump(mode="json", by_alias=True)
        if page_size == "A4":
            settings.pop("aspect_ratio")
            settings.update({"page_size": page_size, "orientation": orientation})
        if svg_canvas is not None:
            settings.pop("aspect_ratio", None)
            settings["svg_canvas"] = svg_canvas.model_dump(mode="json", by_alias=True)
        if svg_template_pin is not None:
            settings["svg_template_pin"] = svg_template_pin.model_dump(mode="json", by_alias=True)
            settings["svg_template_mode"] = svg_template_mode
            if svg_template_frame_map is not None:
                settings["svg_template_frame_map"] = svg_template_frame_map
        if svg_design_reference_pin is not None:
            settings["svg_design_reference_pin"] = svg_design_reference_pin.model_dump(mode="json", by_alias=True)
        if svg_design_reference_snapshot_id is not None:
            settings["svg_design_reference_snapshot_id"] = str(svg_design_reference_snapshot_id)
        # Authored-only admission stamps a theme-less presentation with the
        # internal {"mode": "authored"} marker. That marker is not a catalog
        # theme and therefore has no public id/name/user metadata.
        if resolved_theme is not None and resolved_theme.get("id"):
            settings.update(
                {
                    "theme_id": resolved_theme["id"],
                    "theme_name": resolved_theme["name"],
                    "theme_source": (
                        "builtin"
                        if resolved_theme["user"] == "builtin"
                        else "custom"
                    ),
                }
            )
        if reference_snapshot_ids:
            settings["reference_snapshot_ids"] = [
                str(snapshot_id) for snapshot_id in reference_snapshot_ids
            ]
        asset_ids: list[uuid.UUID] = []
        imported_paths = []
        try:
            existing_presentation = (
                await sql_session.execute(
                    select(PresentationModel).where(
                        PresentationModel.id == resolved_presentation_id
                    ).execution_options(skip_owner_scope=True)
                )
            ).scalar_one_or_none()
            if existing_presentation is not None:
                if existing_presentation.owner_id != get_current_owner_id():
                    raise HTTPException(
                        status_code=409,
                        detail="presentation_id_unavailable",
                    )
                if existing_presentation.theme != resolved_theme:
                    raise HTTPException(
                        status_code=409,
                        detail="idempotency_key_payload_conflict",
                    )
                existing_operation = await sql_session.scalar(
                    select(WorkspaceOperation).where(
                        WorkspaceOperation.presentation_id
                        == resolved_presentation_id,
                        WorkspaceOperation.owner_scope
                        == get_workspace_owner_scope(),
                        WorkspaceOperation.kind == "presentation.generate",
                    )
                )
                if (
                    existing_operation is not None
                    and existing_operation.idempotency_key_hash
                    != workspace_idempotency_hash(resolved_idempotency_key)
                ):
                    raise HTTPException(
                        status_code=409,
                        detail="idempotency_key_payload_conflict",
                    )
                if existing_operation is None:
                    raise HTTPException(
                        status_code=409,
                        detail="idempotency_record_incomplete",
                    )
                if existing_operation.request_payload.get("previous_presentation_id") != (
                    str(previous_presentation_id) if previous_presentation_id else None
                ):
                    raise HTTPException(status_code=409, detail="idempotency_key_payload_conflict")
                existing_asset_ids = [
                    uuid.UUID(asset_id)
                    for asset_id in existing_operation.request_payload.get(
                        "asset_ids", []
                    )
                ]
                expected_reference_ids = [
                    str(snapshot_id) for snapshot_id in reference_snapshot_ids or []
                ]
                if (
                    list(existing_presentation.reference_snapshot_ids or [])
                    != expected_reference_ids
                ):
                    raise HTTPException(
                        status_code=409,
                        detail="idempotency_key_payload_conflict",
                    )
                expected_asset_count = (
                    len(canonical_asset_ids)
                    if reference_snapshot_ids
                    else len(file_paths or [])
                )
                if len(existing_asset_ids) != expected_asset_count:
                    raise HTTPException(
                        status_code=409,
                        detail="idempotency_key_payload_conflict",
                    )
                existing_assets = {
                    asset.id: asset
                    for asset in (
                        await sql_session.scalars(
                            select(WorkspaceAsset).where(
                                WorkspaceAsset.id.in_(existing_asset_ids)
                            )
                        )
                    ).all()
                }
                if len(existing_assets) != len(existing_asset_ids):
                    raise HTTPException(
                        status_code=409,
                        detail="idempotency_record_incomplete",
                    )
                for path, asset_id in zip(file_paths or [], existing_asset_ids):
                    file_name, media_type, size_bytes, digest, _extension = (
                        await inspect_workspace_document_asset(
                            source_path=path,
                            original_name=_attachment_file_name(path),
                        )
                    )
                    existing_asset = existing_assets[asset_id]
                    if (
                        existing_asset.original_name != file_name
                        or existing_asset.media_type != media_type
                        or existing_asset.size_bytes != size_bytes
                        or existing_asset.sha256 != digest
                    ):
                        raise HTTPException(
                            status_code=409,
                            detail="idempotency_key_payload_conflict",
                        )
                if reference_snapshot_ids and existing_asset_ids != canonical_asset_ids:
                    raise HTTPException(
                        status_code=409,
                        detail="idempotency_key_payload_conflict",
                    )
                asset_ids = existing_asset_ids
                presentation = existing_presentation
            else:
                await validate_previous_attempt(
                    sql_session,
                    previous_presentation_id=previous_presentation_id,
                    presentation_id=resolved_presentation_id,
                )
                durable_file_paths: list[str] = []
                asset_ids.extend(canonical_asset_ids)
                for path in file_paths or []:
                    asset, durable_path = await import_workspace_document_asset(
                        sql_session,
                        source_path=path,
                        original_name=_attachment_file_name(path),
                    )
                    imported_paths.append(durable_path)
                    durable_file_paths.append(str(durable_path))
                    asset_ids.append(asset.id)
                presentation.file_paths = durable_file_paths or None
                sql_session.add(presentation)
                await sql_session.flush()
            conversation, _message, _operation, _replayed = await bootstrap_workspace(
                sql_session,
                presentation_id=resolved_presentation_id,
                request=WorkspaceBootstrapRequest(
                    idempotency_key=resolved_idempotency_key,
                    prompt=(
                        content
                        if content.strip()
                        else DEFAULT_ATTACHMENT_GENERATION_PROMPT
                    ),
                    generation_settings=settings,
                    asset_ids=asset_ids,
                    operation_type="presentation.generate",
                    expected_revision=presentation.revision,
                ),
                initial_state="awaiting_approval",
                previous_presentation_id=previous_presentation_id,
                commit=False,
            )
            if reference_snapshot_ids:
                await claim_reference_snapshots(
                    sql_session,
                    snapshot_ids=reference_snapshot_ids,
                    conversation_id=conversation.id,
                )
            await sql_session.commit()
        except WorkspaceServiceError as error:
            await sql_session.rollback()
            for path in imported_paths:
                path.unlink(missing_ok=True)
                try:
                    path.parent.rmdir()
                except OSError:
                    pass
            raise HTTPException(
                status_code=error.status_code,
                detail=error.code,
            ) from error
        except ReferenceProviderError as error:
            await sql_session.rollback()
            for path in imported_paths:
                path.unlink(missing_ok=True)
                try:
                    path.parent.rmdir()
                except OSError:
                    pass
            raise HTTPException(
                status_code=error.status_code,
                detail=error.code,
            ) from error
        except IntegrityError as error:
            await sql_session.rollback()
            for path in imported_paths:
                path.unlink(missing_ok=True)
                try:
                    path.parent.rmdir()
                except OSError:
                    pass
            raise HTTPException(
                status_code=409,
                detail="presentation_create_conflict",
            ) from error
        except Exception:
            await sql_session.rollback()
            for path in imported_paths:
                path.unlink(missing_ok=True)
                try:
                    path.parent.rmdir()
                except OSError:
                    pass
            raise
    else:
        sql_session.add(presentation)
        await sql_session.commit()

    return presentation
