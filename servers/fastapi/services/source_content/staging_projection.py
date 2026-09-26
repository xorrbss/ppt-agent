"""Deterministic source-content staging projections."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Sequence

from models.authored_vnext.deck.checkpoint import (
    MasterDigestPin, SlideValidationCheckpoint, ValidatedSlideSpecV2,
)
from models.authored_vnext.deck.operations import DeckGenerationSnapshot
from models.authored_vnext.master.contract import DeckMasterContract, Ownership
from models.authored_vnext.master.slide import ExplicitTitle, SlideSpecV2, TitleFitResult
from services.authored_vnext.generation.pipeline import _seal_deck
from services.authored_vnext.generation.effective_style import resolve_authored_composition
from services.authored_vnext.master.catalog_seed import catalog_seed_contracts
from services.authored_vnext.master.digests import (
    canonical_digest as authored_canonical_digest, seal_master_contract,
)
from services.source_content.allocator import (
    SourceContentPlacementPlan, SourceContentPlacementSlide, WritableLayout, canonical_digest,
)
from services.source_content.contracts import RESTYLE_CONTENT_KINDS, SourceContentManifest
from services.source_content.errors import SourceContentError
from services.source_content.materializer import MaterializedSourceContentScene, SourceContentStyleTokens
from utils.color_contrast import normalize_hex, readable_accent_color, readable_color_scheme


def _source_error(code: str, *, status_code: int = 422) -> SourceContentError:
    return SourceContentError(code, status_code=status_code)


def _style_tokens(style: Any) -> SourceContentStyleTokens:
    """Derive readable deck tokens from the style's own background.

    Text never comes from ``preview_palette`` -- a decorative swatch list with
    no readability contract, indexing into which once put white body text on
    an off-white page.  ``utils.color_contrast`` derives the panel and both
    text planes from the background's luminance instead.
    """

    scheme = readable_color_scheme(
        normalize_hex(
            getattr(style, "background_color", None) or getattr(style, "preview_bg", None),
            "#FFFFFF",
        )
    )
    accent = normalize_hex(
        getattr(style, "primary_color", None) or getattr(style, "preview_accent", None),
        "#2563EB",
    )
    return SourceContentStyleTokens(
        background=scheme.background,
        surface=scheme.surface,
        text_primary=scheme.text_primary,
        text_secondary=scheme.text_secondary,
        accent=accent,
        accent_text=readable_accent_color(accent, scheme.background, scheme.surface),
        heading_font=(getattr(style, "heading_font", None) or "Noto Sans KR"),
        body_font=(getattr(style, "body_font", None) or "Noto Sans KR"),
    )


def _synthetic_style_master(*, style: Any, style_tokens: SourceContentStyleTokens) -> DeckMasterContract:
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
            name: getattr(base.furniture, name).model_copy(update={"visibility": "hidden"})
            for name in ("logo", "header", "footer", "divider", "page_number")
        }
    )
    contract = base.model_copy(
        update={
            # Synthetic masters are immutable once published. Bump the ID with
            # the placement/composition contract so a corrected template path
            # never conflicts with an older revision-one record.  v5 carries
            # the contrast-derived colour tokens (``_style_tokens``); v4
            # records hold the unreadable palette-index colours.
            "master_id": f"master:source-content-{style.id}-v5",
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


def _compatibility_text_for_slide(*, manifest: SourceContentManifest, source_unit_id: str) -> str:
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
            (item.slot_id for item in planned.placements if not item.slot_id.endswith("title")),
            planned.placements[0].slot_id,
        )
    if layouts:
        return next(
            (slot.slot_id for slot in layouts[0].slots if not slot.slot_id.endswith("title")),
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
            "ownership": Ownership(locked=False, model_writable=True).model_dump(mode="json", by_alias=True),
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
            visual_plan_digest=canonical_digest(planned.model_dump(mode="json", by_alias=True)),
            scene_digest=scene.slide_digest,
            quality_policy_version="source-content-preserve/v1",
            layout_id=planned.layout_id,
            body_slot_id=body_slot,
            validated_at=_utc_now(),
        )
        validated.append(ValidatedSlideSpecV2(slide=slide, checkpoint=checkpoint, scene=scene))
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
