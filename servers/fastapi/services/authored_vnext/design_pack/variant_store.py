"""Draft, write and approve per-canvas design-pack variants below a base pack."""

from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import tempfile
from typing import Any, Awaitable, Callable

from .canvases import RegisteredCanvas, registered_canvases
from .compiler import compile_authored_design_source
from .ingestion import _json_bytes, _package_files, _write_staged
from .manifest import MANIFEST_V2, DesignPackManifest, VariantOf, seal_manifest
from .registry import DesignPackRegistry, RegisteredDesignPack
from .variant_source import (
    VariantDraftError,
    build_variant_source,
    layout_brief,
    relayout_schema,
    source_bytes,
)


VARIANT_SOURCE_FILENAME = "variant-source.json"
Relayout = Callable[[dict[str, Any], dict[str, Any]], Awaitable[dict[str, Any]]]

_SYSTEM = """You re-lay out an approved slide design pack for a different canvas.
Input: every frame of the pack with each element's key, kind, role, current
bounds on the source canvas, and font size; plus the target canvas and its safe
area. Return new bounds (x, y, width, height in target pixels) for EVERY element
of EVERY frame, keyed by frameId and key. Do not add, drop, merge or rename
elements. Keep the design: the same reading order, alignment relationships,
grouping, relative emphasis and decorative motifs. Editable elements (slots)
stay inside the safe area; decorations may reach the canvas edge. Nothing may
leave the canvas. Change fontSize only when the new geometry needs it, keeping
the type hierarchy; otherwise return null."""


def _target(canvas_id: str) -> RegisteredCanvas:
    try:
        return registered_canvases()[canvas_id]
    except KeyError as error:
        raise VariantDraftError("design_pack_canvas_id_invalid") from error


def _variant_root(base: RegisteredDesignPack, canvas_id: str) -> Path:
    return base.root / "variants" / canvas_id


async def request_relayout(brief: dict[str, Any], schema: dict[str, Any]) -> dict[str, Any]:
    """The one deck-level structured call a draft spends."""

    from llmai import get_client
    from llmai.shared import JSONSchemaResponse, SystemMessage, UserMessage

    from utils.llm_config import enforce_llm_egress_policy, get_llm_config
    from utils.llm_provider import get_model
    from utils.llm_utils import generate_structured_with_schema_retries

    enforce_llm_egress_policy()
    return await generate_structured_with_schema_retries(
        get_client(config=get_llm_config()),
        get_model(),
        messages=[
            SystemMessage(content=_SYSTEM),
            UserMessage(content=json.dumps(brief, ensure_ascii=False)),
        ],
        response_format=JSONSchemaResponse(
            name="design_pack_variant_relayout", json_schema=schema, strict=False
        ),
        json_schema=schema,
        strict=False,
        validate_schema=True,
        validate_schema_max_loop_count=2,
        semantic_slot_prefix="design-pack-variant",
    )


def write_variant(
    base: RegisteredDesignPack, canvas_id: str, source: dict[str, Any]
) -> DesignPackManifest:
    """Compile ``source`` and atomically (re)write the draft variant directory."""

    if base.manifest.variant_of is not None:
        raise VariantDraftError("design_pack_variant_base_invalid")
    destination = _variant_root(base, canvas_id)
    if (destination / "manifest.json").exists():
        current = DesignPackManifest.model_validate_json((destination / "manifest.json").read_bytes())
        if current.effective_status == "approved":
            raise VariantDraftError("design_pack_variant_approved_exists")
    compiled = compile_authored_design_source(source)
    contents, inventory = _package_files(compiled)
    manifest = seal_manifest(
        schema_version=MANIFEST_V2,
        pack_id=base.manifest.pack_id,
        package_version=base.manifest.package_version,
        source_kind=compiled.structure.source_kind,
        source_filename=compiled.source_filename,
        source_digest=compiled.structure.source_digest,
        structure_digest=compiled.structure.structure_digest,
        inventory=inventory,
        canvas_id=canvas_id,
        variant_of=VariantOf(
            pack_id=base.manifest.pack_id,
            package_version=base.manifest.package_version,
        ),
        status="draft",
    )
    destination.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".{canvas_id}.", dir=destination.parent))
    try:
        _write_staged(staging, contents, manifest)
        (staging / VARIANT_SOURCE_FILENAME).write_bytes(source_bytes(source))
        if destination.exists():
            shutil.rmtree(destination)
        os.replace(staging, destination)
    finally:
        if staging.exists():
            shutil.rmtree(staging)
    return manifest


def _registered_variant(
    storage_root: Path | str, pack_id: str, package_version: str, canvas_id: str
) -> RegisteredDesignPack:
    variant = DesignPackRegistry(storage_root).variants(
        pack_id, package_version, include_drafts=True
    ).get(canvas_id)
    if variant is None:
        raise VariantDraftError("design_pack_variant_unknown")
    return variant


async def draft_variant(
    storage_root: Path | str,
    *,
    pack_id: str,
    package_version: str,
    canvas_id: str,
    relayout: Relayout = request_relayout,
) -> RegisteredDesignPack:
    target = _target(canvas_id)
    base = DesignPackRegistry(storage_root).get(pack_id, package_version)
    if base.canvas_id == canvas_id:
        raise VariantDraftError("design_pack_variant_same_canvas")
    payload = await relayout(layout_brief(base, target), relayout_schema())
    write_variant(base, canvas_id, build_variant_source(base, target, payload))
    return _registered_variant(storage_root, pack_id, package_version, canvas_id)


def approve_variant(
    storage_root: Path | str, *, pack_id: str, package_version: str, canvas_id: str
) -> RegisteredDesignPack:
    variant = _registered_variant(storage_root, pack_id, package_version, canvas_id)
    if variant.status == "approved":
        return variant
    values = variant.manifest.model_dump(exclude={"package_digest", "inventory_digest"})
    values["status"] = "approved"
    values["variant_of"] = variant.manifest.variant_of
    values["inventory"] = variant.manifest.inventory
    manifest = seal_manifest(**values)
    manifest_path = variant.root / "manifest.json"
    staged = manifest_path.with_name(".manifest.json.tmp")
    staged.write_bytes(_json_bytes(manifest.model_dump(mode="json", by_alias=True)))
    os.replace(staged, manifest_path)
    return _registered_variant(storage_root, pack_id, package_version, canvas_id)


__all__ = [
    "VARIANT_SOURCE_FILENAME",
    "approve_variant",
    "draft_variant",
    "request_relayout",
    "write_variant",
]
