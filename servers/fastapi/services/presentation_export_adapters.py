"""Concrete PPTX/general adapters behind the capability router."""

from __future__ import annotations

import os
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import Literal
import uuid

from pathvalidate import sanitize_filename

from models.presentation_and_path import PresentationAndPath
from services.presentation_export_capabilities import (
    PresentationExportAdapterId,
    PresentationExportContractError,
    PresentationExportSlideInput,
    UnsupportedCapability,
)
from services.template_v2_generation_service import (
    TemplateV2GenerationTarget,
)
from services.template_v2_native_storage import (
    TemplateV2NativeStorageError,
    read_native_source,
)
from templates.v2.pptx.native_clone import (
    TEMPLATE_V2_NATIVE_CLONE_COMPILER_NAME,
    TEMPLATE_V2_NATIVE_CLONE_COMPILER_VERSION,
    compile_template_v2_native_clone,
)
from templates.v2.pptx.native_compiler import (
    TEMPLATE_V2_NATIVE_PPTX_COMPILER_NAME,
    TEMPLATE_V2_NATIVE_PPTX_COMPILER_VERSION,
    TemplateV2NativePptxCompileError,
    compile_template_v2_pptx,
)
from utils.asset_directory_utils import get_exports_directory
from utils.export_utils import export_presentation
from utils.filename_utils import safe_export_basename


_CLONE_UNSUPPORTED_CODES = frozenset(
    {
        "template_v2_native_clone_repeat_prototype_unsupported",
        "template_v2_native_clone_repeat_cardinality_unsupported",
        "template_v2_native_clone_slide_reuse_unsupported",
        "template_v2_native_clone_image_relationship_replacement_unsupported",
        "template_v2_native_clone_chart_relationship_replacement_unsupported",
        "template_v2_native_clone_element_unsupported",
        "template_v2_native_pptx_text_list_shape_change_unsupported",
        "template_v2_native_clone_text_list_paragraph_unsupported",
        "template_v2_native_pptx_table_shape_change_unsupported",
    }
)
_RECONSTRUCTED_UNSUPPORTED_CODES = frozenset(
    {
        "template_v2_native_pptx_element_unsupported",
        "template_v2_native_pptx_flex_wrap_unsupported",
        "template_v2_native_pptx_repeat_child_size_required",
        "template_v2_native_pptx_marker_unsupported",
        "template_v2_native_pptx_container_alignment_unsupported",
        "template_v2_native_pptx_container_padding_unsupported",
        "template_v2_native_pptx_nested_rotation_unsupported",
        "template_v2_native_pptx_shadow_unsupported",
        "template_v2_native_pptx_border_radius_unsupported",
        "template_v2_native_pptx_dash_unsupported",
        "template_v2_native_pptx_letter_spacing_unsupported",
        "template_v2_native_pptx_line_height_unsupported",
        "template_v2_native_pptx_ellipsis_unsupported",
        "template_v2_native_pptx_opacity_unsupported",
    }
)


@dataclass(frozen=True, slots=True)
class PresentationExportAdapterRequest:
    presentation_id: uuid.UUID
    title: str
    export_format: Literal["pptx", "pdf"]
    expected_revision: int
    cookie_header: str | None
    slides: tuple[PresentationExportSlideInput, ...] = ()
    template_target: TemplateV2GenerationTarget | None = None


@dataclass(frozen=True, slots=True)
class PresentationExportAdapterResult:
    presentation: PresentationAndPath
    selected_adapter: PresentationExportAdapterId
    artifact_sha256: str
    artifact_size_bytes: int
    compiler: str
    compiler_version: str
    runtime: str
    runtime_version: str
    package_sha256: str | None


def _publish_native_bytes(
    *,
    presentation_id: uuid.UUID,
    title: str,
    payload: bytes,
) -> PresentationAndPath:
    normalized_title = (title or str(presentation_id)).strip() or str(
        presentation_id
    )
    safe_title = safe_export_basename(sanitize_filename(normalized_title))
    destination = Path(get_exports_directory()) / (
        f"{safe_title}-{str(presentation_id)[:8]}.pptx"
    )
    temporary = destination.with_name(
        f"{destination.name}.{uuid.uuid4().hex}.exporting"
    )
    try:
        with temporary.open("xb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, destination)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise
    return PresentationAndPath(
        presentation_id=presentation_id,
        path=str(destination),
    )


def _result(
    presentation: PresentationAndPath,
    *,
    adapter: PresentationExportAdapterId,
    compiler: str,
    compiler_version: str,
    runtime: str,
    runtime_version: str,
    package_sha256: str | None = None,
) -> PresentationExportAdapterResult:
    path = Path(presentation.path)
    payload = path.read_bytes()
    digest = sha256(payload).hexdigest()
    if package_sha256 is not None and package_sha256 != digest:
        raise PresentationExportContractError(
            "presentation_export_package_digest_mismatch",
            path="artifact",
        )
    return PresentationExportAdapterResult(
        presentation=presentation,
        selected_adapter=adapter,
        artifact_sha256=digest,
        artifact_size_bytes=len(payload),
        compiler=compiler,
        compiler_version=compiler_version,
        runtime=runtime,
        runtime_version=runtime_version,
        package_sha256=package_sha256,
    )


def _unsupported(
    error: TemplateV2NativePptxCompileError,
    *,
    adapter: PresentationExportAdapterId,
    allowed: frozenset[str],
) -> UnsupportedCapability:
    if error.code not in allowed:
        raise error
    return UnsupportedCapability(error.code, adapter=adapter, path=error.path)


async def run_native_clone_adapter(
    request: PresentationExportAdapterRequest,
) -> PresentationExportAdapterResult:
    target = request.template_target
    if target is None or target.native_source is None:
        raise UnsupportedCapability(
            "presentation_export_native_clone_source_unavailable",
            adapter="native_clone",
        )
    try:
        source = read_native_source(
            target.native_source.storage_key,
            template_id=target.template_id,
            revision=target.revision,
            expected_sha256=target.native_source.source_sha256,
            expected_size_bytes=target.native_source.source_size_bytes,
        )
    except TemplateV2NativeStorageError as error:
        raise PresentationExportContractError(
            "presentation_export_native_package_digest_mismatch",
            path="native_source",
        ) from error
    clone_input = []
    for index, slide in enumerate(request.slides):
        if slide.source_part is None:
            raise PresentationExportContractError(
                "presentation_export_native_source_part_required",
                path=f"slides.{index}.source_part",
            )
        clone_input.append((slide.layout, dict(slide.content), slide.source_part))
    try:
        compilation = compile_template_v2_native_clone(source, clone_input)
    except TemplateV2NativePptxCompileError as error:
        raise _unsupported(
            error,
            adapter="native_clone",
            allowed=_CLONE_UNSUPPORTED_CODES,
        ) from error
    published = _publish_native_bytes(
        presentation_id=request.presentation_id,
        title=request.title,
        payload=compilation.pptx,
    )
    return _result(
        published,
        adapter="native_clone",
        compiler=TEMPLATE_V2_NATIVE_CLONE_COMPILER_NAME,
        compiler_version=TEMPLATE_V2_NATIVE_CLONE_COMPILER_VERSION,
        runtime="python-pptx-ooxml-clone",
        runtime_version="1",
        package_sha256=compilation.package_sha256,
    )


async def run_native_reconstructed_adapter(
    request: PresentationExportAdapterRequest,
) -> PresentationExportAdapterResult:
    try:
        compilation = compile_template_v2_pptx(
            [(slide.layout, dict(slide.content)) for slide in request.slides]
        )
    except TemplateV2NativePptxCompileError as error:
        raise _unsupported(
            error,
            adapter="native_reconstructed",
            allowed=_RECONSTRUCTED_UNSUPPORTED_CODES,
        ) from error
    published = _publish_native_bytes(
        presentation_id=request.presentation_id,
        title=request.title,
        payload=compilation.pptx,
    )
    return _result(
        published,
        adapter="native_reconstructed",
        compiler=TEMPLATE_V2_NATIVE_PPTX_COMPILER_NAME,
        compiler_version=TEMPLATE_V2_NATIVE_PPTX_COMPILER_VERSION,
        runtime="python-pptx",
        runtime_version="1",
        package_sha256=compilation.package_sha256,
    )


async def run_general_adapter(
    request: PresentationExportAdapterRequest,
) -> PresentationExportAdapterResult:
    exported = await export_presentation(
        request.presentation_id,
        request.title,
        request.export_format,
        cookie_header=request.cookie_header,
        expected_revision=request.expected_revision,
    )
    return _result(
        exported,
        adapter="general",
        compiler="presentation-export",
        compiler_version="0.4.3",
        runtime="chromium-dom-export",
        runtime_version="presenton.render-artifact/v1",
    )


__all__ = [
    "PresentationExportAdapterRequest",
    "PresentationExportAdapterResult",
    "run_general_adapter",
    "run_native_clone_adapter",
    "run_native_reconstructed_adapter",
]
