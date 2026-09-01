"""Owner-scoped, data-only Design Pack registration API."""

# FastAPI dependency declarations intentionally call Depends/File/Form in defaults.
# ruff: noqa: B008

from __future__ import annotations

import base64
from collections.abc import Mapping
from pathlib import Path
import tempfile
from typing import Literal
import uuid

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from models.sql.slide import SlideModel
from services.authored_vnext.design_pack import (
    DesignPackCompileError,
    DesignPackIngestionError,
    DesignPackRegistry,
    DesignPackRegistryError,
    ingest_design_pack,
)
from services.authored_vnext.generation_route import design_pack_storage_root
from services.database import get_async_session
from services.presentation_access_service import require_live_presentation
from utils.authored_dom_quality import normalize_browser_metrics
from utils.owner_context import get_current_owner_id
from utils.slide_capture import render_html_to_png_with_metrics


DESIGN_PACKS_ROUTER = APIRouter(
    prefix="/design-packs/v1",
    tags=["Design Packs"],
)

_ROLES = {
    "cover",
    "section",
    "content",
    "data",
    "timeline",
    "architecture",
    "process",
    "closing",
}


class _WireModel(BaseModel):
    model_config = ConfigDict(
        alias_generator=lambda value: value.split("_")[0]
        + "".join(part.title() for part in value.split("_")[1:]),
        populate_by_name=True,
        extra="forbid",
    )


class RegisterPresentationDesignPackRequest(_WireModel):
    schema_version: Literal["register-presentation-design-pack/v1"] = (
        "register-presentation-design-pack/v1"
    )
    presentation_id: uuid.UUID
    design_pack_id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{1,119}$")
    version: int = Field(ge=1)
    slide_indexes: list[int] | None = Field(default=None, max_length=100)
    roles: list[str] | None = Field(default=None, max_length=100)

    @model_validator(mode="after")
    def validate_selection(self) -> "RegisterPresentationDesignPackRequest":
        if self.slide_indexes is not None:
            if not self.slide_indexes or any(index < 0 for index in self.slide_indexes):
                raise ValueError("design_pack_slide_indexes_invalid")
            if len(set(self.slide_indexes)) != len(self.slide_indexes):
                raise ValueError("design_pack_slide_indexes_duplicate")
        if self.roles is not None:
            if any(role not in _ROLES for role in self.roles):
                raise ValueError("design_pack_role_invalid")
            expected = len(self.slide_indexes) if self.slide_indexes is not None else None
            if expected is not None and len(self.roles) != expected:
                raise ValueError("design_pack_roles_cardinality_mismatch")
        return self


class DesignPackPinResponse(_WireModel):
    schema_version: Literal["design-pack-pin/v1"] = "design-pack-pin/v1"
    design_pack_id: str
    version: int
    digest: str
    source_kind: Literal["pptx", "authored"]
    frame_count: int = Field(ge=1)
    output_capability: Literal["fidelity"] = "fidelity"


def _owner_id() -> uuid.UUID:
    owner = get_current_owner_id()
    if not isinstance(owner, uuid.UUID):
        raise HTTPException(status_code=401, detail="authentication_required")
    return owner


def _role(index: int, total: int, requested: list[str] | None) -> str:
    if requested is not None:
        return requested[index]
    if index == 0:
        return "cover"
    if index == total - 1:
        return "closing"
    return "content"


def _kind_and_role(item: Mapping[str, object]) -> tuple[str, str]:
    qid = str(item.get("qid") or "").casefold()
    tag = str(item.get("tag") or "").casefold()
    if tag == "img" or "image" in qid or "visual" in qid:
        return "image", "visual"
    if "title" in qid or "heading" in qid:
        return "text", "title"
    if "metric" in qid or "value" in qid:
        return "text", "metric"
    return "text", "body"


async def _authored_frame(
    slide: SlideModel,
    *,
    sequence_index: int,
    role: str,
) -> dict[str, object]:
    html = (slide.html_content or "").strip()
    if not html:
        raise HTTPException(status_code=422, detail="design_pack_authored_html_missing")
    capture = await render_html_to_png_with_metrics(html)
    if not capture.png:
        raise HTTPException(status_code=422, detail="design_pack_source_render_missing")
    metrics = normalize_browser_metrics(dict(capture.browser_metrics or {}))
    elements: list[dict[str, object]] = []
    seen: set[str] = set()
    for ordinal, item in enumerate(metrics.get("elements", []), start=1):
        if not isinstance(item, Mapping):
            continue
        qid = str(item.get("qid") or "").strip()
        bounds = item.get("bbox")
        if not qid or qid in seen or not isinstance(bounds, Mapping):
            continue
        try:
            x = max(0.0, float(bounds.get("x", 0)))
            y = max(0.0, float(bounds.get("y", 0)))
            width = min(1280.0 - x, float(bounds.get("width", 0)))
            height = min(720.0 - y, float(bounds.get("height", 0)))
        except (TypeError, ValueError):
            continue
        if width <= 0 or height <= 0:
            continue
        kind, semantic_role = _kind_and_role(item)
        elements.append(
            {
                "id": qid[:180] or f"element-{ordinal}",
                "kind": kind,
                "editable": True,
                "semanticRole": semantic_role,
                "bounds": {"x": x, "y": y, "width": width, "height": height},
            }
        )
        seen.add(qid)
        if len(elements) >= 128:
            break
    if not elements:
        elements = [
            {
                "id": "narrative-body",
                "kind": "text",
                "editable": True,
                "semanticRole": "body",
                "bounds": {"x": 64, "y": 64, "width": 1152, "height": 592},
            }
        ]
    return {
        "id": f"slide-{sequence_index + 1}",
        "type": "html",
        "role": role,
        "html": html,
        "htmlDigest": capture.html_digest,
        "sourceRender": "data:image/png;base64,"
        + base64.b64encode(capture.png).decode("ascii"),
        "sourceRenderDigest": capture.png_digest,
        "elements": elements,
    }


def _response(manifest: object, frame_count: int) -> DesignPackPinResponse:
    return DesignPackPinResponse(
        design_pack_id=getattr(manifest, "pack_id"),
        version=int(getattr(manifest, "package_version").split(".", 1)[0]),
        digest=getattr(manifest, "package_digest"),
        source_kind=getattr(manifest, "source_kind"),
        frame_count=frame_count,
    )


def _http_error(error: Exception) -> HTTPException:
    code = getattr(error, "code", None) or str(error)
    if not isinstance(code, str) or not code:
        code = "design_pack_registration_failed"
    return HTTPException(status_code=422, detail=code[:160])


@DESIGN_PACKS_ROUTER.post(
    "/from-presentation",
    response_model=DesignPackPinResponse,
    response_model_by_alias=True,
)
async def register_design_pack_from_presentation(
    request: RegisterPresentationDesignPackRequest,
    session: AsyncSession = Depends(get_async_session),
) -> DesignPackPinResponse:
    owner_id = _owner_id()
    await require_live_presentation(session, request.presentation_id)
    slides = list(
        await session.scalars(
            select(SlideModel)
            .where(
                SlideModel.presentation == request.presentation_id,
                SlideModel.owner_id == owner_id,
            )
            .order_by(SlideModel.index)
        )
    )
    if request.slide_indexes is not None:
        by_index = {slide.index: slide for slide in slides}
        try:
            slides = [by_index[index] for index in request.slide_indexes]
        except KeyError as error:
            raise HTTPException(status_code=422, detail="design_pack_slide_missing") from error
    if not slides:
        raise HTTPException(status_code=422, detail="design_pack_slides_missing")
    if request.roles is not None and len(request.roles) != len(slides):
        raise HTTPException(status_code=422, detail="design_pack_roles_cardinality_mismatch")
    frames = [
        await _authored_frame(
            slide,
            sequence_index=index,
            role=_role(index, len(slides), request.roles),
        )
        for index, slide in enumerate(slides)
    ]
    source = {
        "schemaVersion": "authored-design-source/v1",
        "canvas": {"width": 1280, "height": 720},
        "sourcePresentationId": str(request.presentation_id),
        "frames": frames,
    }
    try:
        manifest = ingest_design_pack(
            source,
            pack_id=request.design_pack_id,
            package_version=f"{request.version}.0.0",
            storage_root=design_pack_storage_root(owner_id),
        )
    except (DesignPackCompileError, DesignPackIngestionError) as error:
        raise _http_error(error) from error
    return _response(manifest, len(frames))


@DESIGN_PACKS_ROUTER.post(
    "/from-pptx",
    response_model=DesignPackPinResponse,
    response_model_by_alias=True,
)
async def register_design_pack_from_pptx(
    design_pack_id: str = Form(alias="designPackId"),
    version: int = Form(),
    file: UploadFile = File(),
) -> DesignPackPinResponse:
    owner_id = _owner_id()
    if not file.filename or not file.filename.lower().endswith(".pptx"):
        raise HTTPException(status_code=422, detail="design_pack_pptx_required")
    content = await file.read(100 * 1024 * 1024 + 1)
    if not content or len(content) > 100 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="design_pack_source_size_invalid")
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(suffix=".pptx", delete=False) as handle:
            handle.write(content)
            temporary = Path(handle.name)
        manifest = ingest_design_pack(
            temporary,
            pack_id=design_pack_id,
            package_version=f"{version}.0.0",
            storage_root=design_pack_storage_root(owner_id),
        )
        package = DesignPackRegistry(design_pack_storage_root(owner_id)).get(
            manifest.pack_id, manifest.package_version
        )
        return _response(manifest, len(package.structure.frames))
    except (DesignPackCompileError, DesignPackIngestionError, DesignPackRegistryError) as error:
        raise _http_error(error) from error
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


@DESIGN_PACKS_ROUTER.get(
    "",
    response_model=list[DesignPackPinResponse],
    response_model_by_alias=True,
)
async def list_design_packs() -> list[DesignPackPinResponse]:
    owner_id = _owner_id()
    try:
        packages = DesignPackRegistry(design_pack_storage_root(owner_id)).list()
    except DesignPackRegistryError as error:
        raise _http_error(error) from error
    return [_response(item.manifest, len(item.structure.frames)) for item in packages]


__all__ = ["DESIGN_PACKS_ROUTER"]
