"""Deterministic furniture-free composition for explicit no-master Authored decks."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from models.authored_vnext.deck import AuthoredCompositionPin, MasterDigestPin
from models.authored_vnext.master.operations import MasterRevisionResponse
from services.authored_vnext.generation.effective_style import (
    resolve_authored_composition,
)
from services.authored_vnext.generation.legacy_pin import ResolvedLegacyMasterPin
from services.authored_vnext.master.catalog_seed import catalog_seed_contracts
from services.authored_vnext.master.digests import canonical_digest, seal_master_contract
from services.authored_vnext.master.visibility import INTERNAL_AUTHORED_MASTER_PREFIX
from utils.authored_styles import AuthoredStyle


@dataclass(frozen=True, slots=True)
class InternalAuthoredComposition:
    master: MasterRevisionResponse
    resolved: ResolvedLegacyMasterPin
    composition: AuthoredCompositionPin


def build_internal_authored_composition(
    *,
    owner_id,
    style: AuthoredStyle,
    metadata: Any | None,
) -> InternalAuthoredComposition:
    """Seal the hidden storage contract used when the user selected no master.

    This record supplies stable layout/font CAS inputs to the generation log. It
    is not a company-standard selection and must never be projected into the
    public master catalog or copied into the incoming request's master fields.
    """

    palette = list(style.preview_palette or [])
    text_primary = (
        str(palette[1])
        if len(palette) > 1
        and isinstance(palette[1], str)
        and len(palette[1]) == 7
        and palette[1].startswith("#")
        else "#0F172A"
    )
    background = style.background_color or style.preview_bg
    accent = style.primary_color or style.preview_accent
    heading_font = style.heading_font or "Noto Sans KR"
    body_font = style.body_font or "Noto Sans KR"
    identity = canonical_digest(
        {
            "contract": "internal-authored-composition/v1",
            "styleId": style.id,
            "background": background,
            "accent": accent,
            "textPrimary": text_primary,
            "headingFont": heading_font,
            "bodyFont": body_font,
        }
    ).removeprefix("sha256:")[:16]

    base = catalog_seed_contracts(None)[1]
    colors = base.tokens.colors.model_copy(
        update={
            "background": background,
            "surface": "#FFFFFF",
            "text_primary": text_primary,
            "text_secondary": "#334155",
            "accent": accent,
        }
    )
    fonts = base.tokens.fonts.model_copy(
        update={
            "latin": body_font,
            "east_asian": body_font,
            "complex_script": body_font,
            "fallbacks": [body_font, "Arial", "sans-serif"],
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
    contract = seal_master_contract(
        base.model_copy(
            update={
                "master_id": (
                    f"{INTERNAL_AUTHORED_MASTER_PREFIX}{style.id}-{identity}-v1"
                ),
                "name": "Internal Authored composition",
                "tokens": base.tokens.model_copy(
                    update={"colors": colors, "fonts": fonts}
                ),
                "furniture": furniture,
                "font_manifest": base.font_manifest.model_copy(
                    update={
                        "primary": body_font,
                        "fallbacks": [body_font, "Arial", "sans-serif"],
                        "windows_fallback": "Arial",
                        "theme_scripts": base.font_manifest.theme_scripts.model_copy(
                            update={
                                "latin": heading_font,
                                "hang": body_font,
                                "jpan": body_font,
                                "hans": body_font,
                                "hant": body_font,
                            }
                        ),
                    }
                ),
            }
        )
    )
    composition, _effective = resolve_authored_composition(
        contract,
        style=style,
        metadata=metadata,
    )
    if contract.digest is None or contract.font_manifest.digest is None:
        raise ValueError("internal Authored master must be sealed")
    pin = MasterDigestPin(
        master_id=contract.master_id,
        master_revision=1,
        master_digest=contract.digest,
        layout_registry_digest=canonical_digest(
            contract.layouts.model_dump(mode="json", by_alias=True)
        ),
        font_manifest_digest=contract.font_manifest.digest,
    )
    master = MasterRevisionResponse(
        master_id=contract.master_id,
        revision=1,
        digest=contract.digest,
        status="published",
        contract=contract,
    )
    return InternalAuthoredComposition(
        master=master,
        resolved=ResolvedLegacyMasterPin(
            owner_id=owner_id,
            pin=pin,
            contract=contract,
        ),
        composition=composition,
    )


__all__ = ["InternalAuthoredComposition", "build_internal_authored_composition"]
