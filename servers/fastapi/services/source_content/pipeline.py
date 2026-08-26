"""Admission and deterministic staging for ``source_content_restyle``."""

from __future__ import annotations

import inspect
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable

from models.authored_vnext.deck.checkpoint import (
    MasterDigestPin,
    SlideValidationCheckpoint,
    ValidatedSlideSpecV2,
)
from models.authored_vnext.deck.operations import DeckGenerationSnapshot
from models.authored_vnext.master.contract import DeckMasterContract, Ownership, Rect
from models.authored_vnext.master.slide import (
    ExplicitTitle,
    SlideSpecV2,
    TitleFitResult,
)
from services.authored_vnext.generation.pipeline import _seal_deck
from services.authored_vnext.generation.scene_compiler import (
    compatibility_composition_digest,
)
from services.authored_vnext.master.catalog_seed import catalog_seed_contracts
from services.authored_vnext.master.digests import (
    canonical_digest as authored_canonical_digest,
    seal_master_contract,
)
from services.source_content.allocator import (
    ContentAllocator,
    PlacementCompositionPin,
    SourceContentPlacementPlan,
    WritableLayout,
    WritableSlot,
    canonical_digest,
)
from services.source_content.contracts import (
    SourceContentManifest,
    SourceContentManifestPin,
)
from services.source_content.errors import SourceContentError
from services.source_content.materializer import (
    MaterializedSourceContentScene,
    SourceContentSceneMaterializer,
    SourceContentStyleTokens,
)


SOURCE_MODE = "source_content_restyle"
_INSTRUCTION_CONFLICT = re.compile(
    r"(?:요약|축약|번역|의역|재작성|문구\s*수정|내용\s*추가|사실\s*추가|"
    r"summari[sz]e|translate|rewrite|paraphrase|add\s+(?:content|facts?))",
    re.IGNORECASE,
)


@dataclass(frozen=True, slots=True)
class SourceContentGenerationStaging:
    placement_plan: SourceContentPlacementPlan
    materialized_scene: MaterializedSourceContentScene
    deck_snapshot: DeckGenerationSnapshot
    master_contract: DeckMasterContract


def _source_error(code: str, *, status_code: int = 422) -> SourceContentError:
    return SourceContentError(code, status_code=status_code)


def _validate_design_instruction(instructions: str | None) -> None:
    if instructions and _INSTRUCTION_CONFLICT.search(instructions):
        raise _source_error("source_content_instruction_conflict")


async def admit_source_content_request(
    request: Any,
    *,
    sql_session: Any,
    owner_id: str | None,
) -> SourceContentManifest | None:
    """Resolve and freeze the immutable owner-scoped manifest for generation."""

    if getattr(request, "source_mode", None) != SOURCE_MODE:
        return None
    if owner_id is None:
        raise _source_error("source_content_owner_required", status_code=401)
    pin = getattr(request, "source_content_manifest_pin", None)
    if pin is None:
        raise _source_error("source_content_manifest_invalid", status_code=400)
    _validate_design_instruction(getattr(request, "instructions", None))
    if getattr(request, "web_search", False) or getattr(request, "vision_qa", False):
        raise _source_error("source_content_generation_option_conflict", status_code=400)
    if getattr(request, "export_as", None) != "pptx":
        raise _source_error("source_content_format_not_supported", status_code=415)

    from services.source_content.manifest_store import (
        admit_source_content_manifest_pin,
    )

    manifest = await admit_source_content_manifest_pin(
        sql_session,
        owner_id=owner_id,
        manifest_id=pin.manifest_id,
        digest=pin.digest,
    )
    if manifest.source_type != "pptx":
        raise _source_error("source_content_format_not_supported", status_code=415)
    snapshots = {str(value) for value in getattr(request, "reference_snapshot_ids", ())}
    if snapshots and manifest.reference_snapshot_id not in snapshots:
        raise _source_error("source_content_manifest_invalid", status_code=409)
    request._source_content_manifest = manifest
    return manifest


def _style_tokens(style: Any) -> SourceContentStyleTokens:
    return SourceContentStyleTokens(
        background=(getattr(style, "background_color", None) or style.preview_bg),
        surface="#FFFFFF",
        text_primary="#0F172A",
        text_secondary="#334155",
        accent=(getattr(style, "primary_color", None) or style.preview_accent),
        heading_font=(getattr(style, "heading_font", None) or "Noto Sans KR"),
        body_font=(getattr(style, "body_font", None) or "Noto Sans KR"),
    )


def _synthetic_style_master(
    *, style: Any, style_tokens: SourceContentStyleTokens
) -> DeckMasterContract:
    """Create the internal furniture-free master required by Workbench storage."""

    base = catalog_seed_contracts(None)[1]  # deterministic clean geometry only
    colors = base.tokens.colors.model_copy(
        update={
            "background": style_tokens.background,
            "surface": style_tokens.surface,
            "text_primary": style_tokens.text_primary,
            "text_secondary": style_tokens.text_secondary,
            "accent": style_tokens.accent,
        }
    )
    fonts = base.tokens.fonts.model_copy(
        update={
            "latin": style_tokens.body_font,
            "east_asian": style_tokens.body_font,
            "complex_script": style_tokens.body_font,
            "fallbacks": [style_tokens.body_font, "Arial", "sans-serif"],
        }
    )
    furniture = base.furniture.model_copy(
        update={
            name: getattr(base.furniture, name).model_copy(
                update={"visibility": "hidden"}
            )
            for name in ("logo", "header", "footer", "divider", "page_number")
        }
    )
    contract = base.model_copy(
        update={
            "master_id": f"master:source-content-{style.id}",
            "name": f"Source content · {style.id}",
            "tokens": base.tokens.model_copy(update={"colors": colors, "fonts": fonts}),
            "furniture": furniture,
            "font_manifest": base.font_manifest.model_copy(
                update={
                    "primary": style_tokens.body_font,
                    "fallbacks": [style_tokens.body_font, "Arial", "sans-serif"],
                    "windows_fallback": "Arial",
                    "theme_scripts": base.font_manifest.theme_scripts.model_copy(
                        update={
                            "latin": style_tokens.body_font,
                            "hang": style_tokens.body_font,
                            "jpan": style_tokens.body_font,
                            "hans": style_tokens.body_font,
                            "hant": style_tokens.body_font,
                        }
                    ),
                }
            ),
        }
    )
    return seal_master_contract(contract)


def _master_context(
    *, request: Any, style: Any, style_tokens: SourceContentStyleTokens
) -> tuple[DeckMasterContract, MasterDigestPin, Any | None]:
    contract = getattr(request, "_authored_master_contract", None)
    pin = getattr(request, "_authored_master_pin", None)
    composition = getattr(request, "_authored_composition_pin", None)
    if contract is None or pin is None:
        contract = _synthetic_style_master(style=style, style_tokens=style_tokens)
        if contract.digest is None or contract.font_manifest.digest is None:
            raise _source_error("source_content_composition_not_applied")
        pin = MasterDigestPin(
            master_id=contract.master_id,
            master_revision=1,
            master_digest=contract.digest,
            layout_registry_digest=authored_canonical_digest(
                contract.layouts.model_dump(mode="json", by_alias=True)
            ),
            font_manifest_digest=contract.font_manifest.digest,
        )
        composition = None
    return contract, pin, composition


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _compatibility_text_for_slide(
    *, manifest: SourceContentManifest, source_unit_id: str
) -> str:
    unit = next(
        (item for item in manifest.units if item.source_unit_id == source_unit_id),
        None,
    )
    if unit is None:
        raise _source_error("source_content_plan_manifest_mismatch")
    for block in unit.blocks:
        payload = block.payload
        candidates: list[Any] = [
            payload.get("text"),
            payload.get("altText"),
            payload.get("shapeName"),
        ]
        cells = payload.get("cells")
        if isinstance(cells, list) and cells and isinstance(cells[0], list) and cells[0]:
            candidates.append(cells[0][0])
        series = payload.get("series")
        if isinstance(series, list) and series and isinstance(series[0], dict):
            candidates.append(series[0].get("name"))
        for candidate in candidates:
            if isinstance(candidate, str) and candidate.strip():
                value = " ".join(candidate.split())
                if len(value) > 4000:
                    raise _source_error("source_content_unplaceable")
                return value
    raise _source_error("source_content_unplaceable")


def _deck_snapshot(
    *,
    manifest: SourceContentManifest,
    plan: SourceContentPlacementPlan,
    materialized: MaterializedSourceContentScene,
    master_contract: DeckMasterContract,
    master_pin: MasterDigestPin,
    composition: Any | None,
) -> DeckGenerationSnapshot:
    validated: list[ValidatedSlideSpecV2] = []
    scene_by_id = {slide.slide_id: slide for slide in materialized.scene.slides}
    for planned in plan.slides:
        slide_id = f"slide:source-{planned.output_index + 1}"
        scene = scene_by_id[slide_id]
        title_text = _compatibility_text_for_slide(
            manifest=manifest,
            source_unit_id=planned.source_unit_id,
        )
        title = ExplicitTitle(text=title_text, lines=[title_text])
        title_fit = TitleFitResult(
            layout_id=planned.layout_id,
            explicit_lines=[title_text],
            font_family=master_contract.tokens.fonts.east_asian,
            font_manifest_digest=master_pin.font_manifest_digest,
            chosen_font_pt=master_contract.semantic_styles.title.font_pt,
            margins=master_contract.semantic_styles.title.margins,
            clipped=False,
        )
        slide_payload = {
            "schemaVersion": "slidespec/v2",
            "slideId": slide_id,
            "revision": 1,
            "layoutId": planned.layout_id,
            "title": title.model_dump(mode="json", by_alias=True),
            "body": title_text,
            "ownership": Ownership(locked=False, model_writable=True).model_dump(
                mode="json", by_alias=True
            ),
            "masterDigest": master_pin.master_digest,
            "titleFit": title_fit.model_dump(mode="json", by_alias=True),
            "contentDigest": canonical_digest(
                {
                    "sourceUnitId": planned.source_unit_id,
                    "sourcePart": planned.source_part,
                    "sceneDigest": scene.slide_digest,
                }
            ),
        }
        slide = SlideSpecV2(
            **slide_payload,
            specDigest=authored_canonical_digest(slide_payload),
        )
        body_slot = next(
            (
                item.slot_id
                for item in planned.placements
                if not item.slot_id.endswith("title")
            ),
            planned.placements[0].slot_id,
        )
        checkpoint = SlideValidationCheckpoint(
            master_digest=master_pin.master_digest,
            layout_registry_digest=master_pin.layout_registry_digest,
            font_manifest_digest=master_pin.font_manifest_digest,
            source_digest=manifest.manifest_digest,
            composition=composition,
            visual_plan_digest=canonical_digest(
                planned.model_dump(mode="json", by_alias=True)
            ),
            scene_digest=scene.slide_digest,
            quality_policy_version="source-content-preserve/v1",
            layout_id=planned.layout_id,
            body_slot_id=body_slot,
            validated_at=_utc_now(),
        )
        validated.append(
            ValidatedSlideSpecV2(slide=slide, checkpoint=checkpoint, scene=scene)
        )
    return _seal_deck(
        deck_id=materialized.scene.deck_id,
        revision=1,
        parent=None,
        pin=master_pin,
        composition=composition,
        source_digest=manifest.manifest_digest,
        slides=validated,
        canonical_scene=materialized.scene,
    )


def _fallback_layout(canvas: Rect) -> WritableLayout:
    gap = max(12.0, canvas.height * 0.025)
    title_height = max(68.0, canvas.height * 0.14)
    body_top = canvas.y + title_height + gap
    body_height = max(1.0, canvas.bottom - body_top)
    return WritableLayout(
        layout_role="title-content",
        layout_id="layout:source-content-standard",
        slots=(
            WritableSlot(
                slot_id="slot:source-content-title",
                bounds=Rect(
                    x=canvas.x,
                    y=canvas.y,
                    width=canvas.width,
                    height=title_height,
                ),
                accepted_kinds=("text",),
                semantic_roles=("title", "heading", "section"),
                capacity_units=2,
            ),
            WritableSlot(
                slot_id="slot:source-content-body",
                bounds=Rect(
                    x=canvas.x,
                    y=body_top,
                    width=canvas.width,
                    height=body_height,
                ),
                capacity_units=12,
            ),
        ),
    )


def _layouts_and_canvas(master_contract: Any | None) -> tuple[list[WritableLayout], Rect]:
    if master_contract is None:
        canvas = Rect(x=0, y=0, width=1280, height=720)
        safe = Rect(x=60, y=60, width=1160, height=600)
        return [_fallback_layout(safe)], canvas
    canvas_contract = master_contract.canvas
    canvas = Rect(
        x=0,
        y=0,
        width=float(canvas_contract.width_px),
        height=float(canvas_contract.height_px),
    )
    standard = master_contract.layouts.standard
    if not standard.slots:
        return [_fallback_layout(canvas_contract.safe_area)], canvas
    slots = []
    for raw in standard.slots:
        role = str(raw.role)
        slots.append(
            WritableSlot(
                slot_id=raw.id,
                bounds=raw.bounds,
                accepted_kinds=("text",) if role == "title" else (
                    "text",
                    "table",
                    "chart",
                    "image",
                    "shape",
                    "source_note",
                ),
                semantic_roles=("title", "heading", "section") if role == "title" else (),
                capacity_units=2 if role == "title" else 12,
            )
        )
    return [
        WritableLayout(
            layout_role="title-content",
            layout_id=standard.id,
            slots=tuple(slots),
        )
    ], canvas


def _composition_pin(
    *,
    request: Any,
    style: Any,
    style_tokens: SourceContentStyleTokens,
    layouts: list[WritableLayout],
) -> PlacementCompositionPin:
    style_tokens_digest = canonical_digest(
        style_tokens.model_dump(mode="json", by_alias=True)
    )
    style_identity = {
        "id": style.id,
        "name": style.name,
        "description": style.description,
        "styleTokenDigest": style_tokens_digest,
    }
    master_pin = getattr(request, "_authored_master_pin", None)
    geometry_digest = canonical_digest(
        [layout.model_dump(mode="json", by_alias=True) for layout in layouts]
    )
    return PlacementCompositionPin(
        authored_style_id=style.id,
        # Coverage compares this directly to the tokens actually present in Scene.
        authored_style_digest=style_tokens_digest,
        authored_style_pin_digest=canonical_digest(style_identity),
        company_standard_master_id=(
            master_pin.master_id if master_pin is not None else None
        ),
        company_standard_revision=(
            master_pin.master_revision if master_pin is not None else None
        ),
        master_digest=(master_pin.master_digest if master_pin is not None else None),
        master_geometry_digest=geometry_digest,
    )


async def build_source_content_staging(
    *,
    request: Any,
    presentation_id: Any,
    sql_session: Any,
    owner_id: Any | None,
) -> SourceContentGenerationStaging:
    """Allocate and materialize the already-admitted request with no LLM calls."""

    manifest = getattr(request, "_source_content_manifest", None)
    style = getattr(request, "_authored_style_snapshot", None)
    pin = getattr(request, "source_content_manifest_pin", None)
    if manifest is None or style is None or pin is None:
        raise _source_error("source_content_manifest_invalid", status_code=409)
    if owner_id is None:
        raise _source_error("source_content_owner_required", status_code=401)
    from services.source_content.manifest_store import (
        load_source_content_manifest_assets,
    )

    sealed_assets = await load_source_content_manifest_assets(
        sql_session,
        owner_id=owner_id,
        manifest_id=pin.manifest_id,
    )
    tokens = _style_tokens(style)
    master_contract, master_pin, composition = _master_context(
        request=request,
        style=style,
        style_tokens=tokens,
    )
    layouts, canvas = _layouts_and_canvas(master_contract)
    composition_pin = _composition_pin(
        request=request,
        style=style,
        style_tokens=tokens,
        layouts=layouts,
    )
    try:
        plan = ContentAllocator().allocate(
            manifest=manifest,
            manifest_pin=SourceContentManifestPin(
                manifest_id=pin.manifest_id,
                digest=pin.digest,
            ),
            composition_pin=composition_pin,
            layouts=layouts,
        )
        materialized = SourceContentSceneMaterializer().materialize(
            manifest=manifest,
            plan=plan,
            layouts=layouts,
            style_tokens=tokens,
            canvas_bounds=canvas,
            deck_id=(
                f"deck:semantic-{presentation_id.hex}"
                if hasattr(presentation_id, "hex")
                else f"deck:semantic-{str(presentation_id).lower().replace('-', '')}"
            ),
            sealed_assets=sealed_assets,
            scene_composition_digest=(
                composition.digest
                if composition is not None
                else compatibility_composition_digest(master_pin=master_pin)
            ),
        )
        snapshot = _deck_snapshot(
            manifest=manifest,
            plan=plan,
            materialized=materialized,
            master_contract=master_contract,
            master_pin=master_pin,
            composition=composition,
        )
        from services.source_content.manifest_store import (
            bind_source_content_generation,
        )

        await bind_source_content_generation(
            sql_session,
            owner_id=owner_id,
            manifest=manifest,
            composition_pin=plan.composition_pin.model_dump(
                mode="json", by_alias=True
            ),
            content_policy=request.content_policy.model_dump(
                mode="json", by_alias=True
            ),
            generation_engine_version="source-content-deterministic-scene/v1",
            presentation_id=presentation_id,
        )
    except SourceContentError:
        raise
    except ValueError as error:
        code = str(error)
        if not code.startswith("source_content_"):
            code = "source_content_manifest_invalid"
        raise _source_error(code) from error
    return SourceContentGenerationStaging(
        placement_plan=plan,
        materialized_scene=materialized,
        deck_snapshot=snapshot,
        master_contract=master_contract,
    )


async def generate_source_content_restyle_presentation(
    *,
    request: Any,
    presentation_id: Any,
    async_status: Any,
    export_cookie_header: str | None,
    generation_job: Any,
    progress_reporter: Any,
    sql_session: Any,
    publisher: Callable[..., Awaitable[Any]] | None = None,
) -> Any:
    """Enter the source-content product branch before any generative authoring."""

    from utils.owner_context import get_current_owner_id

    staging = await build_source_content_staging(
        request=request,
        presentation_id=presentation_id,
        sql_session=sql_session,
        owner_id=get_current_owner_id(),
    )
    if publisher is None:
        try:
            from services.source_content.publication import (
                publish_source_content_generation as publisher,
            )
        except ImportError as error:
            raise _source_error(
                "source_content_publication_unavailable", status_code=503
            ) from error
    result = publisher(
        request=request,
        presentation_id=presentation_id,
        manifest=request._source_content_manifest,
        placement_plan=staging.placement_plan,
        materialized_scene=staging.materialized_scene,
        deck_snapshot=staging.deck_snapshot,
        master_contract=staging.master_contract,
        async_status=async_status,
        export_cookie_header=export_cookie_header,
        generation_job=generation_job,
        progress_reporter=progress_reporter,
        sql_session=sql_session,
    )
    return await result if inspect.isawaitable(result) else result


__all__ = [
    "SOURCE_MODE",
    "SourceContentGenerationStaging",
    "admit_source_content_request",
    "build_source_content_staging",
    "generate_source_content_restyle_presentation",
]
