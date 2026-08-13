"""Immutable Template V2 generation target contracts and source admission helpers."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
import json
import re
from typing import Any

from pydantic import ValidationError as PydanticValidationError

from models.presentation_layout import PresentationLayoutModel, SlideLayoutModel
from services.template_v2_native_storage import NATIVE_SOURCE_SCHEMA_VERSION
from templates.v2.models.layouts import SlideLayout
from templates.v2.pptx.native_compiler import (
    TEMPLATE_V2_NATIVE_PPTX_COMPILER_NAME,
    TEMPLATE_V2_NATIVE_PPTX_COMPILER_VERSION,
    TEMPLATE_V2_NATIVE_PPTX_SCHEMA_VERSION,
)
from templates.v2.publication import evaluate_template_v2_publication
from templates.v2.wire_codec import UPSTREAM_TEMPLATE_V2_SHA


TEMPLATE_V2_GENERATION_SCHEMA_VERSION = "presenton.template-v2-generation-schema/v1"
TEMPLATE_V2_COMPILER_MODE = "deterministic-native-ui"
TEMPLATE_V2_COMPILER_NAME = "presenton-template-v2-generation-adapter"
TEMPLATE_V2_COMPILER_VERSION = "1"
_SOURCE_PART = re.compile(r"^ppt/slides/slide[1-9][0-9]*\.xml$")
_NATIVE_CAPABILITIES = {
    "native_clone",
    "text_patch",
    "table_patch",
    "master_layout_theme",
    "relationships_media",
}



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
class TemplateV2NativeSource:
    storage_key: str
    source_sha256: str
    source_size_bytes: int
    layout_source_parts: dict[str, str]
    capabilities: dict[str, bool]
    publication_status: str


@dataclass(frozen=True, slots=True)
class TemplateV2NativePptxPreflight:
    """Serializable evidence from the non-exporting native compiler preflight."""

    status: str
    structural_sha256: str | None = None
    package_sha256: str | None = None
    unsupported_code: str | None = None
    unsupported_path: str | None = None
    preservation_mode: str = "reconstructed"
    compiler_name: str = TEMPLATE_V2_NATIVE_PPTX_COMPILER_NAME
    compiler_version: str = TEMPLATE_V2_NATIVE_PPTX_COMPILER_VERSION
    schema_version: str = TEMPLATE_V2_NATIVE_PPTX_SCHEMA_VERSION
    selected_for_export: bool = False
    pptx: bytes | None = None

    def to_provenance(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "compiler_name": self.compiler_name,
            "compiler_version": self.compiler_version,
            "preservation_mode": self.preservation_mode,
            "status": self.status,
            "selected_for_export": self.selected_for_export,
            "structural_sha256": (
                f"sha256:{self.structural_sha256}"
                if self.structural_sha256 is not None
                else None
            ),
            "package_sha256": (
                f"sha256:{self.package_sha256}"
                if self.package_sha256 is not None
                else None
            ),
            "unsupported": (
                {
                    "code": self.unsupported_code,
                    "path": self.unsupported_path,
                }
                if self.status == "unsupported"
                else None
            ),
        }


@dataclass(frozen=True, slots=True)
class TemplateV2GenerationTarget:
    template_id: str
    revision: int
    name: str
    snapshot_sha256: str
    layouts: tuple[TemplateV2GenerationLayout, ...]
    preservation_mode: str = "reconstructed"
    native_source: TemplateV2NativeSource | None = None

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
        native_pptx_preflight: TemplateV2NativePptxPreflight | None = None,
    ) -> dict[str, Any]:
        provenance = {
            "schema": "presenton.template-v2-generation-provenance/v1",
            "schema_version": TEMPLATE_V2_GENERATION_SCHEMA_VERSION,
            "request_strategy": "template_v2",
            "generation_strategy": "template-v2",
            "generation_profile": "staged-a-hybrid-v1",
            "editor_capability": "template-v2",
            "export_strategy": (
                "template-v2-native-clone"
                if self.preservation_mode == "native_clone"
                else "template-v2-general"
            ),
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
            "preservation_mode": self.preservation_mode,
        }
        if native_pptx_preflight is not None:
            provenance["native_pptx_preflight"] = (
                native_pptx_preflight.to_provenance()
            )
        return provenance

    def to_durable_payload(self) -> dict[str, Any]:
        """Serialize the exact admitted snapshot for a restart-safe worker."""

        return {
            "template_id": self.template_id,
            "revision": self.revision,
            "name": self.name,
            "snapshot_sha256": self.snapshot_sha256,
            "preservation_mode": self.preservation_mode,
            "native_source": (
                {
                    "storage_key": self.native_source.storage_key,
                    "source_sha256": self.native_source.source_sha256,
                    "source_size_bytes": self.native_source.source_size_bytes,
                    "layout_source_parts": deepcopy(
                        self.native_source.layout_source_parts
                    ),
                    "capabilities": deepcopy(self.native_source.capabilities),
                    "publication_status": self.native_source.publication_status,
                }
                if self.native_source is not None
                else None
            ),
            "layouts": [
                {
                    "layout": item.layout.model_dump(mode="json"),
                    "content_schema": deepcopy(item.content_schema),
                }
                for item in self.layouts
            ],
        }

    @classmethod
    def from_durable_payload(
        cls, payload: dict[str, Any]
    ) -> "TemplateV2GenerationTarget":
        """Rehydrate a previously admitted immutable target without policy rereads."""

        try:
            layouts = tuple(
                TemplateV2GenerationLayout(
                    layout=SlideLayout.model_validate(item["layout"]),
                    content_schema=deepcopy(item["content_schema"]),
                )
                for item in payload["layouts"]
            )
            target = cls(
                template_id=str(payload["template_id"]),
                revision=int(payload["revision"]),
                name=str(payload["name"]),
                snapshot_sha256=str(payload["snapshot_sha256"]),
                layouts=layouts,
                preservation_mode=str(
                    payload.get("preservation_mode", "reconstructed")
                ),
                native_source=_native_source_from_payload(
                    payload.get("native_source")
                ),
            )
        except (KeyError, TypeError, ValueError, PydanticValidationError) as error:
            raise TemplateV2GenerationError("template_v2_snapshot_invalid") from error
        if (
            not target.layouts
            or len(target.snapshot_sha256) != 64
            or (target.preservation_mode == "native_clone")
            != (target.native_source is not None)
        ):
            raise TemplateV2GenerationError("template_v2_snapshot_invalid")
        return target


class TemplateV2GenerationError(ValueError):
    """Stable error raised before any Template V2 presentation is persisted."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def _native_source_from_assets(
    assets: Any,
    *,
    layout_ids: set[str],
) -> TemplateV2NativeSource | None:
    if assets is None:
        return None
    if not isinstance(assets, dict):
        raise TemplateV2GenerationError("template_v2_native_source_invalid")
    value = assets.get("native_pptx")
    if value is None:
        return None
    if not isinstance(value, dict):
        raise TemplateV2GenerationError("template_v2_native_source_invalid")
    preservation_mode = value.get("preservation_mode")
    if preservation_mode == "reconstructed":
        publication = evaluate_template_v2_publication(assets)
        if not publication.publishable:
            raise TemplateV2GenerationError("template_v2_native_source_invalid")
        return None
    if (
        value.get("schema_version") != NATIVE_SOURCE_SCHEMA_VERSION
        or preservation_mode != "native_clone"
        or value.get("publication_status") != "publishable"
    ):
        raise TemplateV2GenerationError("template_v2_native_source_invalid")
    return _native_source_from_payload(value, layout_ids=layout_ids)


def _native_source_from_payload(
    value: Any,
    *,
    layout_ids: set[str] | None = None,
) -> TemplateV2NativeSource | None:
    if value is None:
        return None
    if not isinstance(value, dict):
        raise TemplateV2GenerationError("template_v2_native_source_invalid")
    try:
        storage_key = value["storage_key"]
        source_sha256_value = value["source_sha256"]
        source_size_bytes = value["source_size_bytes"]
        source_parts = value["layout_source_parts"]
        capabilities = value["capabilities"]
        publication_status = value["publication_status"]
    except KeyError as error:
        raise TemplateV2GenerationError("template_v2_native_source_invalid") from error
    if (
        not isinstance(storage_key, str)
        or not isinstance(source_sha256_value, str)
        or re.fullmatch(r"[0-9a-f]{64}", source_sha256_value) is None
        or isinstance(source_size_bytes, bool)
        or not isinstance(source_size_bytes, int)
        or source_size_bytes <= 0
        or not isinstance(source_parts, dict)
        or not all(
            isinstance(key, str)
            and isinstance(part, str)
            and _SOURCE_PART.fullmatch(part) is not None
            for key, part in source_parts.items()
        )
        or not isinstance(capabilities, dict)
        or any(capabilities.get(name) is not True for name in _NATIVE_CAPABILITIES)
        or publication_status != "publishable"
    ):
        raise TemplateV2GenerationError("template_v2_native_source_invalid")
    if layout_ids is not None and not layout_ids.issubset(source_parts):
        raise TemplateV2GenerationError("template_v2_native_source_invalid")
    return TemplateV2NativeSource(
        storage_key=storage_key,
        source_sha256=source_sha256_value,
        source_size_bytes=source_size_bytes,
        layout_source_parts=deepcopy(source_parts),
        capabilities={name: True for name in sorted(_NATIVE_CAPABILITIES)},
        publication_status=publication_status,
    )


__all__ = [
    "TEMPLATE_V2_COMPILER_MODE",
    "TEMPLATE_V2_COMPILER_NAME",
    "TEMPLATE_V2_COMPILER_VERSION",
    "TEMPLATE_V2_GENERATION_SCHEMA_VERSION",
    "TemplateV2GenerationError",
    "TemplateV2GenerationLayout",
    "TemplateV2GenerationTarget",
    "TemplateV2NativePptxPreflight",
    "TemplateV2NativeSource",
    "source_content_sha256",
]

