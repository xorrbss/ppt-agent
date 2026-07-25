"""Fail-closed Template V2 generation adapter for the existing generation pipeline."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
import json
from typing import Any
import uuid

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError, ValidationError as JSONSchemaValidationError
from pydantic import ValidationError as PydanticValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from models.presentation_layout import PresentationLayoutModel, SlideLayoutModel
from models.presentation_outline_model import PresentationOutlineModel
from models.presentation_structure_model import PresentationStructureModel
from models.sql.slide import SlideModel
from services.template_v2_service import (
    TemplateV2NotFoundError,
    TemplateV2Service,
)
from services.template_v2_revision_service import SNAPSHOT_FIELDS, get_revision
from templates.v2.generation import build_generated_slide
from templates.v2.models.layouts import SlideLayout
from templates.v2.policy import (
    StructuredTemplatePolicyError,
    get_structured_template_policy,
)
from templates.v2.schema import get_template_schema
from templates.v2.wire_codec import (
    UPSTREAM_TEMPLATE_V2_SHA,
    TemplateV2WireCodecError,
    load_storage_layouts,
)
from utils.llm_calls.generate_slide_content import (
    get_slide_content_from_type_and_outline,
)

TEMPLATE_V2_GENERATION_SCHEMA_VERSION = "presenton.template-v2-generation-schema/v1"
TEMPLATE_V2_COMPILER_MODE = "deterministic-native-ui"
TEMPLATE_V2_COMPILER_NAME = "presenton-template-v2-generation-adapter"
TEMPLATE_V2_COMPILER_VERSION = "1"


def _canonical_json(value: Any, *, error_code: str) -> str:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    except (TypeError, ValueError) as error:
        raise TemplateV2GenerationError(error_code) from error


def source_content_sha256(source_envelope: dict[str, Any]) -> str:
    """Hash normalized source inputs without retaining private source text."""

    canonical = _canonical_json(
        source_envelope,
        error_code="template_v2_source_invalid",
    )
    return sha256(canonical.encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class TemplateV2GenerationLayout:
    layout: SlideLayout
    content_schema: dict[str, Any]


@dataclass(frozen=True, slots=True)
class TemplateV2GenerationTarget:
    template_id: str
    revision: int
    name: str
    snapshot_sha256: str
    layouts: tuple[TemplateV2GenerationLayout, ...]

    def as_pipeline_layout(self) -> PresentationLayoutModel:
        """Project only fillable schemas into the existing structure selector."""

        return PresentationLayoutModel(
            name=f"template-v2:{self.template_id}:{self.revision}",
            ordered=False,
            slides=[
                SlideLayoutModel(
                    id=item.layout.id,
                    name=item.layout.id,
                    description=item.layout.description,
                    json_schema=deepcopy(item.content_schema),
                )
                for item in self.layouts
            ],
        )

    def provenance(
        self,
        *,
        source_sha256: str,
        request_id: str,
        job_id: str,
        created_at: datetime,
    ) -> dict[str, Any]:
        return {
            "schema": "presenton.template-v2-generation-provenance/v1",
            "schema_version": TEMPLATE_V2_GENERATION_SCHEMA_VERSION,
            "request_strategy": "template_v2",
            "generation_strategy": "template-v2",
            "generation_profile": "staged-a-hybrid-v1",
            "editor_capability": "template-v2",
            "export_strategy": "template-v2-general",
            "compiler_mode": TEMPLATE_V2_COMPILER_MODE,
            "compiler_name": TEMPLATE_V2_COMPILER_NAME,
            "compiler_version": TEMPLATE_V2_COMPILER_VERSION,
            "upstream_baseline_sha": UPSTREAM_TEMPLATE_V2_SHA,
            "source_content_sha256": f"sha256:{source_sha256}",
            "request_id": request_id,
            "job_id": job_id,
            "created_at": created_at.astimezone(timezone.utc).isoformat(),
            "vision": None,
            "template_v2_id": self.template_id,
            "template_v2_revision": self.revision,
            "template_snapshot_sha256": f"sha256:{self.snapshot_sha256}",
        }


class TemplateV2GenerationError(ValueError):
    """Stable error raised before any Template V2 presentation is persisted."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


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
) -> list[SlideModel]:
    """Generate against fillable schemas, validate again, then persist native UI."""

    pipeline_layout = target.as_pipeline_layout()
    if len(outlines.slides) != len(structure.slides):
        raise TemplateV2GenerationError("template_v2_generation_invalid")

    slides: list[SlideModel] = []
    for index, layout_index in enumerate(structure.slides):
        if (
            not isinstance(layout_index, int)
            or layout_index < 0
            or layout_index >= len(target.layouts)
        ):
            raise TemplateV2GenerationError("template_v2_generation_invalid")
        try:
            target_layout = target.layouts[layout_index]
            pipeline_slide_layout = pipeline_layout.slides[layout_index]
        except IndexError as error:
            raise TemplateV2GenerationError(
                "template_v2_generation_invalid"
            ) from error

        generated_content = await get_slide_content_from_type_and_outline(
            pipeline_slide_layout,
            outlines.slides[index],
            language,
            tone,
            verbosity,
            instructions,
        )
        if not isinstance(generated_content, dict):
            raise TemplateV2GenerationError("template_v2_generation_invalid")
        speaker_note = generated_content.pop("__speaker_note__", None)
        try:
            generated = build_generated_slide(
                target_layout.layout,
                generated_content,
            )
        except (ValueError, JSONSchemaValidationError) as error:
            raise TemplateV2GenerationError(
                "template_v2_generation_invalid"
            ) from error

        slides.append(
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
    return slides


__all__ = [
    "TemplateV2GenerationError",
    "TemplateV2GenerationLayout",
    "TemplateV2GenerationTarget",
    "build_template_v2_slides",
    "load_template_v2_generation_target",
    "require_template_v2_generation_admission",
    "source_content_sha256",
]
