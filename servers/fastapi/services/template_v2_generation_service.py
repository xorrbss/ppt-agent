"""Fail-closed Template V2 generation adapter for the existing generation pipeline."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from copy import deepcopy
from hashlib import sha256
import os
from pathlib import Path
from typing import Any
import uuid

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError, ValidationError as JSONSchemaValidationError
from pathvalidate import sanitize_filename
from pydantic import ValidationError as PydanticValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from models.presentation_and_path import PresentationAndPath
from models.presentation_outline_model import PresentationOutlineModel
from models.presentation_structure_model import PresentationStructureModel
from models.sql.slide import SlideModel
from services.template_v2_generation_contracts import (
    TEMPLATE_V2_COMPILER_MODE as TEMPLATE_V2_COMPILER_MODE,
    TEMPLATE_V2_COMPILER_NAME as TEMPLATE_V2_COMPILER_NAME,
    TEMPLATE_V2_COMPILER_VERSION as TEMPLATE_V2_COMPILER_VERSION,
    TEMPLATE_V2_GENERATION_SCHEMA_VERSION as TEMPLATE_V2_GENERATION_SCHEMA_VERSION,
    TemplateV2GenerationError,
    TemplateV2GenerationLayout,
    TemplateV2GenerationTarget,
    TemplateV2NativePptxPreflight,
    TemplateV2NativeSource,
    _canonical_json,
    _native_source_from_assets,
    source_content_sha256,
)
from services.template_v2_service import TemplateV2NotFoundError, TemplateV2Service
from services.template_v2_revision_service import SNAPSHOT_FIELDS, get_revision
from services.template_v2_native_storage import (
    TemplateV2NativeStorageError,
    read_native_source,
)
from templates.v2.catalog import (
    get_template_v2_catalog_entry,
    is_template_v2_release_ready,
)
from templates.v2.content import hydrate_layout_content
from templates.v2.generation import build_generated_slide
from templates.v2.models.layouts import SlideLayout
from templates.v2.pptx.native_clone import (
    TEMPLATE_V2_NATIVE_CLONE_COMPILER_NAME,
    TEMPLATE_V2_NATIVE_CLONE_COMPILER_VERSION,
    TEMPLATE_V2_NATIVE_CLONE_SCHEMA_VERSION,
    compile_template_v2_native_clone,
)
from templates.v2.pptx.native_compiler import (
    TEMPLATE_V2_NATIVE_PPTX_COMPILER_NAME,
    TEMPLATE_V2_NATIVE_PPTX_COMPILER_VERSION,
    TEMPLATE_V2_NATIVE_PPTX_SCHEMA_VERSION,
    TemplateV2NativePptxCompileError,
    compile_template_v2_pptx,
)
from templates.v2.policy import StructuredTemplatePolicyError, get_structured_template_policy
from templates.v2.schema import get_template_schema
from templates.v2.wire_codec import TemplateV2WireCodecError, load_storage_layouts
from utils.asset_directory_utils import get_exports_directory
from utils.filename_utils import safe_export_basename
from utils.llm_calls.generate_slide_content import get_slide_content_from_type_and_outline


_SLIDE_CONTENT_BATCH_SIZE = 10
_SLIDE_ASSET_CONCURRENCY = 10



def require_template_v2_generation_admission(template_id: str) -> None:
    """Reuse the existing default-OFF/exact-allowlist server policy."""

    try:
        get_structured_template_policy().require_write_enabled(template_id)
    except StructuredTemplatePolicyError as error:
        raise TemplateV2GenerationError(error.code) from error


async def load_template_v2_generation_target(
    sql_session: AsyncSession,
    *,
    template_id: str,
    revision: int,
) -> TemplateV2GenerationTarget:
    """Pin one exact template revision and admit only strictly valid fillable layouts."""

    require_template_v2_generation_admission(template_id)
    try:
        record = await TemplateV2Service(sql_session).get(template_id)
    except TemplateV2NotFoundError as error:
        raise TemplateV2GenerationError("template_v2_template_not_found") from error

    if revision != record.revision:
        raise TemplateV2GenerationError("template_v2_revision_conflict")
    revision_entry = await get_revision(sql_session, template_id, revision)
    if revision_entry is None:
        raise TemplateV2GenerationError("template_v2_snapshot_not_found")

    snapshot = {
        field: deepcopy(getattr(revision_entry, field))
        for field in SNAPSHOT_FIELDS
    }
    from templates.packages.registry import get_ready_template_package

    if (
        get_template_v2_catalog_entry(template_id) is not None
        or get_ready_template_package(template_id) is not None
    ) and not is_template_v2_release_ready(template_id, snapshot.get("assets")):
        raise TemplateV2GenerationError("template_v2_template_not_published")
    name = snapshot["name"]
    layouts_value = snapshot["layouts"]

    if not isinstance(layouts_value, dict):
        raise TemplateV2GenerationError("template_v2_layouts_invalid")

    try:
        strict_layouts = load_storage_layouts(layouts_value).validate_strict()
        fillable_layouts: list[TemplateV2GenerationLayout] = []
        for layout in strict_layouts.layouts:
            schema_envelope = get_template_schema(
                {"layouts": [layout.model_dump(mode="json")]}
            )
            content_schema = schema_envelope["layouts"][0]["schema"]
            if content_schema is None:
                continue
            Draft202012Validator.check_schema(content_schema)
            fillable_layouts.append(
                TemplateV2GenerationLayout(
                    layout=layout.model_copy(deep=True),
                    content_schema=deepcopy(content_schema),
                )
            )
    except (
        KeyError,
        TypeError,
        ValueError,
        SchemaError,
        PydanticValidationError,
        TemplateV2WireCodecError,
    ) as error:
        raise TemplateV2GenerationError("template_v2_layouts_invalid") from error

    if not fillable_layouts:
        raise TemplateV2GenerationError("template_v2_fillable_layout_required")

    native_source = _native_source_from_assets(
        snapshot.get("assets"),
        layout_ids={item.layout.id for item in fillable_layouts},
    )

    snapshot_json = _canonical_json(
        {
            "canonicalization": "presenton-template-v2-snapshot/v1",
            "template_id": template_id,
            "revision": revision,
            "snapshot": snapshot,
        },
        error_code="template_v2_layouts_invalid",
    )
    return TemplateV2GenerationTarget(
        template_id=template_id,
        revision=revision,
        name=name,
        snapshot_sha256=sha256(snapshot_json.encode("utf-8")).hexdigest(),
        layouts=tuple(fillable_layouts),
        preservation_mode=(
            "native_clone" if native_source is not None else "reconstructed"
        ),
        native_source=native_source,
    )


async def build_template_v2_slides(
    *,
    target: TemplateV2GenerationTarget,
    presentation_id: uuid.UUID,
    outlines: PresentationOutlineModel,
    structure: PresentationStructureModel,
    language: str | None,
    tone: str | None,
    verbosity: str | None,
    instructions: str | None,
    on_progress: Callable[[int, int], Awaitable[None]] | None = None,
    process_assets: Callable[[SlideModel], Awaitable[Any]] | None = None,
) -> list[SlideModel]:
    """Generate against fillable schemas, validate again, then persist native UI."""

    pipeline_layout = target.as_pipeline_layout()
    if len(outlines.slides) != len(structure.slides):
        raise TemplateV2GenerationError("template_v2_generation_invalid")

    generation_inputs = []
    for index, layout_index in enumerate(structure.slides):
        if (
            not isinstance(layout_index, int)
            or layout_index < 0
            or layout_index >= len(target.layouts)
        ):
            raise TemplateV2GenerationError("template_v2_generation_invalid")
        try:
            generation_inputs.append(
                (
                    index,
                    target.layouts[layout_index],
                    pipeline_layout.slides[layout_index],
                    outlines.slides[index],
                )
            )
        except IndexError as error:
            raise TemplateV2GenerationError(
                "template_v2_generation_invalid"
            ) from error

    slides: list[SlideModel] = []
    content_tasks: list[asyncio.Task[dict[str, Any]]] = []
    asset_tasks: list[asyncio.Task[Any]] = []
    asset_semaphore = asyncio.Semaphore(_SLIDE_ASSET_CONCURRENCY)

    async def process_slide_assets(slide: SlideModel) -> Any:
        if process_assets is None:
            return None
        async with asset_semaphore:
            return await process_assets(slide)

    try:
        for start in range(0, len(generation_inputs), _SLIDE_CONTENT_BATCH_SIZE):
            for task in asset_tasks:
                if task.done():
                    task.result()
            batch_inputs = generation_inputs[
                start : start + _SLIDE_CONTENT_BATCH_SIZE
            ]
            content_tasks = [
                asyncio.create_task(
                    get_slide_content_from_type_and_outline(
                        pipeline_slide_layout,
                        outline,
                        language,
                        tone,
                        verbosity,
                        instructions,
                    )
                )
                for _, _, pipeline_slide_layout, outline in batch_inputs
            ]
            batch_contents = await asyncio.gather(*content_tasks)
            content_tasks = []

            batch_slides: list[SlideModel] = []
            for (index, target_layout, _, _), generated_content in zip(
                batch_inputs, batch_contents, strict=True
            ):
                if not isinstance(generated_content, dict):
                    raise TemplateV2GenerationError(
                        "template_v2_generation_invalid"
                    )
                slide_content = dict(generated_content)
                speaker_note = slide_content.pop("__speaker_note__", None)
                try:
                    generated = build_generated_slide(
                        target_layout.layout,
                        slide_content,
                    )
                except (ValueError, JSONSchemaValidationError) as error:
                    raise TemplateV2GenerationError(
                        "template_v2_generation_invalid"
                    ) from error

                batch_slides.append(
                    SlideModel(
                        presentation=presentation_id,
                        layout_group="native",
                        layout=generated.layout_id,
                        index=index,
                        speaker_note=speaker_note,
                        content=generated.content,
                        ui=generated.ui,
                    )
                )

            slides.extend(batch_slides)
            completed = start + len(batch_slides)
            if process_assets is not None:
                asset_tasks.extend(
                    asyncio.create_task(process_slide_assets(slide))
                    for slide in batch_slides
                )
            if on_progress is not None:
                await on_progress(completed, len(generation_inputs))

        if asset_tasks:
            await asyncio.gather(*asset_tasks)

        if process_assets is not None:
            for slide in slides:
                refresh_template_v2_slide_ui(target=target, slide=slide)
    except BaseException:
        outstanding_tasks = [*content_tasks, *asset_tasks]
        for task in outstanding_tasks:
            if not task.done():
                task.cancel()
        if outstanding_tasks:
            await asyncio.gather(*outstanding_tasks, return_exceptions=True)
        raise

    return slides


def refresh_template_v2_slide_ui(
    *,
    target: TemplateV2GenerationTarget,
    slide: SlideModel,
) -> None:
    """Rebuild pinned native UI after controlled asset URL enrichment."""

    target_layout = next(
        (item for item in target.layouts if item.layout.id == slide.layout),
        None,
    )
    if target_layout is None or not isinstance(slide.content, dict):
        raise TemplateV2GenerationError("template_v2_generation_invalid")
    try:
        slide.ui = hydrate_layout_content(
            target_layout.layout.model_dump(mode="json"),
            slide.content,
        )
    except (ValueError, JSONSchemaValidationError) as error:
        raise TemplateV2GenerationError(
            "template_v2_generation_invalid"
        ) from error


def preflight_template_v2_native_pptx(
    *,
    target: TemplateV2GenerationTarget,
    slides: list[SlideModel],
    select_for_export: bool = False,
) -> TemplateV2NativePptxPreflight:
    """Compile transient native OOXML and return an explicit capability result."""

    layouts_by_id = {item.layout.id: item.layout for item in target.layouts}
    if len(layouts_by_id) != len(target.layouts):
        raise TemplateV2GenerationError("template_v2_generation_invalid")

    compile_input: list[tuple[SlideLayout, dict[str, Any]]] = []
    clone_input: list[tuple[SlideLayout, dict[str, Any], str]] = []
    for slide in slides:
        layout = layouts_by_id.get(slide.layout)
        if layout is None or not isinstance(slide.content, dict):
            raise TemplateV2GenerationError("template_v2_generation_invalid")
        compile_input.append((layout, deepcopy(slide.content)))
        if target.native_source is not None:
            source_part = target.native_source.layout_source_parts.get(layout.id)
            if source_part is None:
                raise TemplateV2GenerationError("template_v2_native_source_invalid")
            clone_input.append((layout, deepcopy(slide.content), source_part))

    try:
        if target.native_source is not None:
            try:
                source_pptx = read_native_source(
                    target.native_source.storage_key,
                    template_id=target.template_id,
                    revision=target.revision,
                    expected_sha256=target.native_source.source_sha256,
                    expected_size_bytes=target.native_source.source_size_bytes,
                )
            except TemplateV2NativeStorageError as error:
                # Storage exposes granular integrity and filesystem codes for
                # ingestion diagnostics.  Generation deliberately collapses
                # them to one stable, content-free API/metrics dimension so a
                # missing or changed private source cannot break observation
                # logging or reveal storage topology.
                raise TemplateV2GenerationError(
                    "template_v2_native_source_invalid"
                ) from error
            compilation = compile_template_v2_native_clone(
                source_pptx,
                clone_input,
            )
        else:
            compilation = compile_template_v2_pptx(compile_input)
    except TemplateV2NativePptxCompileError as error:
        if target.preservation_mode == "native_clone":
            raise TemplateV2GenerationError(
                "template_v2_native_pptx_unsupported"
            ) from error
        return TemplateV2NativePptxPreflight(
            status="unsupported",
            unsupported_code=error.code,
            unsupported_path=error.path,
            preservation_mode=target.preservation_mode,
        )
    except TemplateV2GenerationError:
        raise
    except (ValueError, JSONSchemaValidationError) as error:
        raise TemplateV2GenerationError("template_v2_generation_invalid") from error

    is_native_clone = target.preservation_mode == "native_clone"
    return TemplateV2NativePptxPreflight(
        status="compiled",
        structural_sha256=compilation.structural_sha256,
        package_sha256=compilation.package_sha256,
        preservation_mode=target.preservation_mode,
        compiler_name=(
            TEMPLATE_V2_NATIVE_CLONE_COMPILER_NAME
            if is_native_clone
            else TEMPLATE_V2_NATIVE_PPTX_COMPILER_NAME
        ),
        compiler_version=(
            TEMPLATE_V2_NATIVE_CLONE_COMPILER_VERSION
            if is_native_clone
            else TEMPLATE_V2_NATIVE_PPTX_COMPILER_VERSION
        ),
        schema_version=(
            TEMPLATE_V2_NATIVE_CLONE_SCHEMA_VERSION
            if is_native_clone
            else TEMPLATE_V2_NATIVE_PPTX_SCHEMA_VERSION
        ),
        selected_for_export=is_native_clone and select_for_export,
        pptx=(compilation.pptx if is_native_clone else None),
    )


def export_template_v2_native_pptx(
    *,
    presentation_id: uuid.UUID,
    title: str,
    preflight: TemplateV2NativePptxPreflight,
) -> PresentationAndPath:
    """Publish the exact admitted native-clone bytes to the owned export directory."""

    if (
        preflight.status != "compiled"
        or preflight.preservation_mode != "native_clone"
        or not preflight.selected_for_export
        or preflight.pptx is None
    ):
        raise TemplateV2GenerationError("template_v2_native_export_not_admitted")
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
            stream.write(preflight.pptx)
        os.replace(temporary, destination)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise
    return PresentationAndPath(
        presentation_id=presentation_id,
        path=str(destination),
    )


__all__ = [
    "TemplateV2GenerationError",
    "TemplateV2GenerationLayout",
    "TemplateV2NativeSource",
    "TemplateV2NativePptxPreflight",
    "TemplateV2GenerationTarget",
    "build_template_v2_slides",
    "export_template_v2_native_pptx",
    "load_template_v2_generation_target",
    "preflight_template_v2_native_pptx",
    "require_template_v2_generation_admission",
    "source_content_sha256",
]
