"""Presentation CRUD endpoints: list / fetch / delete / create.

Split out of the presentation god-file. These are the plain persistence
operations on a PresentationModel (no generation or export pipeline).
"""

import json
import uuid
from pathlib import PurePosixPath
from typing import Annotated, Any, List, Optional

from fastapi import APIRouter, Body, Depends, Header, HTTPException
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
)
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from api.v1.ppt.endpoints.presentation_helpers import resolve_presentation_fonts
from constants.presentation import MAX_NUMBER_OF_SLIDES
from enums.tone import Tone
from enums.verbosity import Verbosity
from models.presentation_generation_info import (
    PresentationGenerationInfo,
    PresentationGenerationSettings,
)
from models.presentation_with_slides import PresentationWithSlides
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
from services.presentation_deletion_service import (
    PresentationDeleteConflictError,
    PresentationDeleteRetryableError,
    PresentationDeletionService,
    is_retryable_sqlite_lock,
)
from services.presentation_access_service import require_live_presentation
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

PRESENTATION_CRUD_ROUTER = APIRouter(prefix="/presentation", tags=["Presentation"])
MAX_PRESENTATION_THEME_BYTES = 256 * 1024


class _PresentationCreateTheme(BaseModel):
    """Allowlisted presentation theme persisted by chat-first admission."""

    model_config = ConfigDict(extra="forbid", strict=True)

    id: str = Field(min_length=1, max_length=256)
    name: str = Field(min_length=1, max_length=256)
    description: str = Field(max_length=2048)
    user: str = Field(min_length=1, max_length=256)
    logo: Optional[str] = Field(default=None, max_length=512)
    logo_url: Optional[str] = Field(default=None, max_length=4096)
    company_name: Optional[str] = Field(default=None, max_length=256)
    data: dict[str, Any]

    @field_validator("id", "name", "user")
    @classmethod
    def reject_blank_identifiers(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("must not be blank")
        return value


def _normalize_presentation_create_theme(theme: Optional[dict]) -> Optional[dict]:
    if theme is None:
        return None
    try:
        normalized = _PresentationCreateTheme.model_validate(theme).model_dump()
        encoded = json.dumps(
            normalized,
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
    except (TypeError, ValueError, ValidationError) as error:
        raise HTTPException(
            status_code=422,
            detail="invalid_presentation_theme",
        ) from error
    if len(encoded) > MAX_PRESENTATION_THEME_BYTES:
        raise HTTPException(status_code=422, detail="presentation_theme_too_large")
    return normalized


def _attachment_file_name(file_path: str) -> str:
    """Return only the final path component, regardless of source OS."""
    return PurePosixPath(file_path.replace("\\", "/")).name


def _chat_first_create_enabled() -> bool:
    """Fail closed when the SQL runtime cannot host the operation store."""

    try:
        require_supported_workspace_runtime(sql_engine.url)
    except UnsupportedWorkspaceRuntimeError as error:
        raise HTTPException(status_code=501, detail=error.code) from error
    return True


@PRESENTATION_CRUD_ROUTER.get("/all", response_model=List[PresentationWithSlides])
async def get_all_presentations(sql_session: AsyncSession = Depends(get_async_session)):
    query = (
        select(PresentationModel, SlideModel)
        .where(
            PresentationModel.lifecycle_status
            == PRESENTATION_LIFECYCLE_PUBLISHED
        )
        .join(
            SlideModel,
            (SlideModel.presentation == PresentationModel.id) & (SlideModel.index == 0),
        )
        .order_by(PresentationModel.created_at.desc())
    )

    results = await sql_session.execute(query)
    rows = results.all()
    presentations_with_slides = []
    for presentation, first_slide in rows:
        slides = [first_slide]
        fonts = await resolve_presentation_fonts(presentation, slides, sql_session)
        presentations_with_slides.append(
            PresentationWithSlides(
                **presentation.model_dump(),
                slides=slides,
                fonts=fonts,
            )
        )
    return presentations_with_slides


@PRESENTATION_CRUD_ROUTER.get("/{id}", response_model=PresentationWithSlides)
async def get_presentation(
    id: uuid.UUID, sql_session: AsyncSession = Depends(get_async_session)
):
    presentation = await require_live_presentation(sql_session, id)
    slides_result = await sql_session.scalars(
        select(SlideModel)
        .where(SlideModel.presentation == id)
        .order_by(SlideModel.index)
    )
    slides = list(slides_result)
    fonts = await resolve_presentation_fonts(presentation, slides, sql_session)
    return PresentationWithSlides(
        **presentation.model_dump(),
        slides=slides,
        fonts=fonts,
    )


@PRESENTATION_CRUD_ROUTER.get(
    "/{id}/generation-info",
    response_model=PresentationGenerationInfo,
)
async def get_presentation_generation_info(
    id: uuid.UUID, sql_session: AsyncSession = Depends(get_async_session)
):
    presentation = await require_live_presentation(sql_session, id)

    attachment_file_names = [
        file_name
        for file_path in presentation.file_paths or []
        if (file_name := _attachment_file_name(file_path))
    ]
    return PresentationGenerationInfo(
        prompt=presentation.content,
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


@PRESENTATION_CRUD_ROUTER.delete("/{id}", status_code=204)
async def delete_presentation(
    id: uuid.UUID, sql_session: AsyncSession = Depends(get_async_session)
):
    require_legacy_presentation_write_path(id)
    try:
        presentation = await require_live_presentation(sql_session, id)
    except OperationalError as error:
        await sql_session.rollback()
        if not is_retryable_sqlite_lock(error, sql_session):
            raise
        raise HTTPException(
            status_code=503,
            detail={
                "code": "presentation_delete_temporarily_unavailable",
                "presentation_id": str(id),
                "retryable": True,
            },
            headers={"Retry-After": "1"},
        ) from error
    try:
        await PresentationDeletionService(sql_session).delete(presentation)
    except PresentationDeleteConflictError as error:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "presentation_delete_dependency_conflict",
                "presentation_id": str(error.presentation_id),
            },
        ) from error
    except PresentationDeleteRetryableError as error:
        raise HTTPException(
            status_code=503,
            detail={
                "code": "presentation_delete_temporarily_unavailable",
                "presentation_id": str(error.presentation_id),
                "retryable": True,
            },
            headers={"Retry-After": "1"},
        ) from error


@PRESENTATION_CRUD_ROUTER.post("/create", response_model=PresentationModel)
async def create_presentation(
    content: Annotated[str, Body()],
    presentation_id: Annotated[Optional[uuid.UUID], Body()] = None,
    n_slides: Annotated[Optional[int], Body()] = None,
    language: Annotated[Optional[str], Body()] = None,
    file_paths: Annotated[Optional[List[str]], Body()] = None,
    tone: Annotated[Tone, Body()] = Tone.DEFAULT,
    verbosity: Annotated[Verbosity, Body()] = Verbosity.STANDARD,
    instructions: Annotated[Optional[str], Body()] = None,
    include_table_of_contents: Annotated[bool, Body()] = False,
    include_title_slide: Annotated[bool, Body()] = True,
    web_search: Annotated[bool, Body()] = False,
    theme: Annotated[Optional[dict], Body()] = None,
    idempotency_key: Annotated[
        Optional[str], Header(alias="Idempotency-Key")
    ] = None,
    sql_session: AsyncSession = Depends(get_async_session),
):
    proposed_presentation_id = presentation_id or uuid.uuid4()
    chat_first_enabled = _chat_first_create_enabled()
    resolved_theme = (
        _normalize_presentation_create_theme(theme) if chat_first_enabled else None
    )
    if file_paths:
        file_paths = validate_document_paths(file_paths)

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
        }
        if resolved_theme is not None:
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
                existing_asset_ids = [
                    uuid.UUID(asset_id)
                    for asset_id in existing_operation.request_payload.get(
                        "asset_ids", []
                    )
                ]
                if len(existing_asset_ids) != len(file_paths or []):
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
                asset_ids = existing_asset_ids
                presentation = existing_presentation
            else:
                durable_file_paths: list[str] = []
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
            await bootstrap_workspace(
                sql_session,
                presentation_id=resolved_presentation_id,
                request=WorkspaceBootstrapRequest(
                    idempotency_key=resolved_idempotency_key,
                    prompt=(
                        content
                        if content.strip()
                        else "Generate a presentation from the attached files."
                    ),
                    generation_settings=settings,
                    asset_ids=asset_ids,
                    operation_type="presentation.generate",
                    expected_revision=presentation.revision,
                ),
                initial_state="awaiting_approval",
                commit=False,
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
