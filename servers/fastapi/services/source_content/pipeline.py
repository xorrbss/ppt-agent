"""Admission and deterministic staging for ``source_content_restyle``."""

from __future__ import annotations

import inspect
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Sequence

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
from services.authored_vnext.generation.effective_style import (
    resolve_authored_composition,
)
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
    SourceContentPlacementSlide,
    WritableLayout,
    WritableSlot,
    canonical_digest,
)
from services.source_content.contracts import (
    RESTYLE_CONTENT_KINDS,
    SourceContentManifest,
    SourceContentManifestPin,
)
from services.source_content.errors import SourceContentError
from services.source_content.materializer import (
    MaterializedSourceContentScene,
    SourceContentSceneMaterializer,
    SourceContentStyleTokens,
)
from services.source_content.outline import (
    SourceDocumentOutlineBuilder,
    build_authored_text_image_slides,
)
from services.authored_composition_policy import resolve_composition_profile


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
    # Freeze a lossless page outline at admission.  It is an identity/read
    # order projection; payloads remain sourced from the immutable manifest.
    try:
        request._source_document_outline = SourceDocumentOutlineBuilder().build(
            manifest=manifest,
            manifest_pin=SourceContentManifestPin(
                manifest_id=pin.manifest_id,
                digest=pin.digest,
            ),
        )
    except ValueError as error:
        code = str(error)
        if not code.startswith("source_content_"):
            code = "source_content_manifest_invalid"
        raise _source_error(code, status_code=409) from error
    return manifest


def prepare_source_content_authored_reflow(request: Any) -> str:
    """Convert the admitted source into the existing authored input contract.

    This is a runtime projection. The durable request keeps its immutable
    manifest pin, and every retry rebuilds the same page-locked outline before
    entering ordinary authored generation.
    """

    manifest = getattr(request, "_source_content_manifest", None)
    outline = getattr(request, "_source_document_outline", None)
    if manifest is None or outline is None:
        raise _source_error("source_content_manifest_invalid", status_code=409)
    try:
        slides = build_authored_text_image_slides(
            manifest=manifest,
            outline=outline,
        )
    except ValueError as error:
        code = str(error)
        if not code.startswith("source_content_"):
            code = "source_content_outline_invalid"
        raise _source_error(code, status_code=409) from error
    text_mode = str(
        getattr(getattr(request, "content_policy", None), "text", "verbatim")
        or "verbatim"
    ).strip().lower()
    if text_mode == "verbatim":
        contract_instruction = (
            "Treat each page's extracted source text and source images as page-locked "
            "verbatim authority. Recompose within the selected template, but copy every "
            "source text run exactly: do not summarize, paraphrase, translate, omit, "
            "or invent content. If a page cannot fit on one slide, fail closed instead "
            "of changing the source. Place every source image offered for that slide "
            "exactly once. Do not create or reproduce tables, charts, or the source "
            "slide layout. Do not generate replacement illustrations."
        )
    else:
        contract_instruction = (
            "Treat each page's extracted source text and source images as a page-locked "
            "content outline, then re-author it with the selected template and the "
            "ordinary authored composition logic. Keep every key fact and source order, "
            "but concise rephrasing is allowed for a clean fit. Place every source image "
            "offered for that slide exactly once. Do not create or reproduce tables, "
            "charts, or the source slide layout. Do not generate replacement illustrations."
        )
    existing_instruction = str(getattr(request, "instructions", None) or "").strip()
    request.instructions = (
        f"{existing_instruction}\n\n{contract_instruction}"
        if existing_instruction
        else contract_instruction
    )
    request.slides_markdown = slides
    request.n_slides = len(slides)
    request.include_title_slide = False
    request.include_table_of_contents = False
    # Source reflow is a visual rewrite, so the first authored render must go
    # through the bounded visual review.  Admission still rejects a caller-
    # supplied visionQa flag above; this internal flag is enabled only after
    # the immutable text/image projection has been admitted.
    request.vision_qa = True
    request._source_content_authored_reflow = True
    request._authored_composition_profile = resolve_composition_profile(request)
    # Fall through to the ordinary authored outline/author/render pipeline.
    request.source_mode = None
    return str(manifest.reference_snapshot_id)


def _style_tokens(style: Any) -> SourceContentStyleTokens:
    palette = list(getattr(style, "preview_palette", None) or [])
    palette_navy = (
        palette[1]
        if len(palette) > 1 and re.fullmatch(r"#[0-9A-Fa-f]{6}", str(palette[1]))
        else "#0F172A"
    )
    return SourceContentStyleTokens(
        background=(getattr(style, "background_color", None) or style.preview_bg),
        surface="#FFFFFF",
        text_primary=palette_navy,
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
            # Synthetic masters are immutable once published. Bump the ID with
            # the placement/composition contract so a corrected template path
            # never conflicts with an older revision-one record.
            "master_id": f"master:source-content-{style.id}-v4",
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
        source_contract = _synthetic_style_master(
            style=style,
            style_tokens=style_tokens,
        )
        composition, effective_contract = resolve_authored_composition(
            source_contract,
            style=style,
            metadata=getattr(request, "_authored_deck_metadata", None),
        )
        if (
            source_contract.digest is None
            or source_contract.font_manifest.digest is None
            or effective_contract.digest is None
        ):
            raise _source_error("source_content_composition_not_applied")
        contract = source_contract
        pin = MasterDigestPin(
            master_id=source_contract.master_id,
            master_revision=1,
            master_digest=source_contract.digest,
            layout_registry_digest=authored_canonical_digest(
                source_contract.layouts.model_dump(mode="json", by_alias=True)
            ),
            font_manifest_digest=source_contract.font_manifest.digest,
        )
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
    if not unit.blocks:
        # SlideSpecV2 requires non-empty compatibility strings, while the
        # canonical Scene must remain genuinely empty for a blank source page.
        # This marker is confined to the legacy snapshot projection.
        return f"[source-content-empty:{source_unit_id}]"
    for block in unit.blocks:
        if block.kind not in RESTYLE_CONTENT_KINDS:
            continue
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


def _compatibility_body_slot_id(
    planned: SourceContentPlacementSlide,
    layouts: Sequence[WritableLayout] | None = None,
) -> str:
    """Return a valid checkpoint slot without inventing Scene content."""

    if planned.placements:
        return next(
            (
                item.slot_id
                for item in planned.placements
                if not item.slot_id.endswith("title")
            ),
            planned.placements[0].slot_id,
        )
    if layouts:
        return next(
            (
                slot.slot_id
                for slot in layouts[0].slots
                if not slot.slot_id.endswith("title")
            ),
            layouts[0].slots[0].slot_id,
        )
    return "slot:source-content-empty"


def _deck_snapshot(
    *,
    manifest: SourceContentManifest,
    plan: SourceContentPlacementPlan,
    materialized: MaterializedSourceContentScene,
    master_contract: DeckMasterContract,
    master_pin: MasterDigestPin,
    composition: Any | None,
    layouts: Sequence[WritableLayout] | None = None,
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
        body_slot = _compatibility_body_slot_id(planned, layouts)
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


def _layouts_and_canvas(
    master_contract: Any | None,
    composition: Any | None = None,
) -> tuple[list[WritableLayout], Rect]:
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
    template = getattr(composition, "authored_template", None)
    resolved = getattr(template, "resolved_reference_layout", None)
    if resolved is not None:
        layouts: list[WritableLayout] = []
        for record in resolved.layouts:
            archetype = str(record.get("archetype") or "content")
            retained_layout_id = str(record.get("id") or archetype)
            if not retained_layout_id.startswith("layout:"):
                retained_layout_id = f"layout:{retained_layout_id}"
            if archetype in {"cover", "section", "closing"}:
                source_bounds = canvas
            else:
                source_bounds = Rect(
                    x=max(24.0, canvas.width * 0.025),
                    y=max(48.0, canvas.height * 0.075),
                    width=canvas.width - max(48.0, canvas.width * 0.05),
                    height=canvas.height - max(102.0, canvas.height * 0.15),
                )
            layouts.append(
                WritableLayout(
                    layout_role=archetype,
                    layout_id=retained_layout_id,
                    slots=(
                        WritableSlot(
                            slot_id=f"slot:template-{archetype}-source",
                            bounds=source_bounds,
                            accepted_kinds=("text", "table", "chart", "image", "shape"),
                            capacity_units=1000,
                        ),
                    ),
                    placement_strategy="sourceGeometry",
                )
            )
        if layouts:
            return layouts, canvas
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
    layouts, canvas = _layouts_and_canvas(master_contract, composition)
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
            outline=getattr(request, "_source_document_outline", None),
        )
        expected_units = len(manifest.units)
        if len(plan.slides) != expected_units or [
            slide.source_unit_id for slide in plan.slides
        ] != [
            unit.source_unit_id
            for unit in sorted(
                manifest.units,
                key=lambda item: (item.source_index, item.source_unit_id),
            )
        ]:
            raise _source_error("source_content_source_unit_cardinality_mismatch")
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
            layouts=layouts,
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
    "prepare_source_content_authored_reflow",
]
