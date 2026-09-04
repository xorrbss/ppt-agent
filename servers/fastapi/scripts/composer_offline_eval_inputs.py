"""Source bundle, PPTX extraction, tokens and deck context for the Composer harness.

The PPTX path runs ``extract_pptx_source_content`` directly (no database, no
manifest store): re-analyzing an already-analyzed snapshot through the store
now yields a different digest and a 409, so the harness never calls it.
Database lookup lives in ``composer_offline_eval_db``.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Sequence

from scripts.composer_offline_eval_report import find_slide_size

TITLE_ROLES = frozenset({"title", "heading", "section"})

NEUTRAL_TOKENS: dict[str, str] = {
    "background": "#FFFFFF",
    "surface": "#F5F7FA",
    "text_primary": "#0F172A",
    "text_secondary": "#334155",
    "accent": "#2563EB",
    # Already >= 4.5:1 against both "background" and "surface" above
    # (utils.color_contrast.readable_accent_color returns it unchanged), so
    # the neutral placeholder's accent_text is the accent itself.
    "accent_text": "#2563EB",
    "accent_soft": "#DBEAFE",
    "rule": "#CBD5E1",
}



class SourceLookupError(RuntimeError):
    """The requested manifest or PPTX could not be found."""


@dataclass
class SourceBundle:
    label: str
    mode: str
    manifest: dict[str, Any]
    stored_manifest: dict[str, Any] | None
    pptx_path: Path | None
    owner_scope: str
    reference_snapshot_id: str
    typography_source: str
    notes: list[str] = field(default_factory=list)
    #: Extracted image bytes by asset id, so ``data-asset-id`` markers can be
    #: resolved offline exactly as the pipeline resolves them from the store.
    assets: dict[str, bytes] = field(default_factory=dict)

    @property
    def manifest_id(self) -> str:
        return str(self.manifest.get("manifestId", ""))

    @property
    def units(self) -> list[dict[str, Any]]:
        return sorted_units(self.manifest)

    def summary(self) -> dict[str, Any]:
        return {
            "label": self.label,
            "mode": self.mode,
            "manifestId": self.manifest_id,
            "manifestDigest": self.manifest.get("manifestDigest"),
            "storedManifestId": (self.stored_manifest or {}).get("manifestId"),
            "referenceSnapshotId": self.reference_snapshot_id,
            "ownerScope": self.owner_scope,
            "pptxPath": str(self.pptx_path) if self.pptx_path else None,
            "typographySource": self.typography_source,
            "pageCount": len(self.units),
        }


# --- Environment ---------------------------------------------------------------


def load_env_json(path: Path) -> int:
    """``setdefault`` every string value of a captured environment file.

    Values are never printed: the file carries ``DATABASE_URL`` and keys.
    """

    with path.open(encoding="utf-8") as handle:
        values = json.load(handle)
    count = 0
    for key, value in values.items():
        if isinstance(value, str) and key not in os.environ:
            os.environ[key] = value
            count += 1
    return count


# --- Manifest helpers ----------------------------------------------------------


def sorted_units(manifest: Mapping[str, Any]) -> list[dict[str, Any]]:
    return sorted(
        (dict(unit) for unit in manifest.get("units", ()) or ()),
        key=lambda unit: (int(unit.get("sourceIndex", 0)), str(unit.get("sourceUnitId", ""))),
    )


def deck_title_from_units(units: Sequence[Mapping[str, Any]]) -> str:
    """First title-role text of the first page, else its first text line."""

    for unit in units[:1]:
        blocks = sorted(unit.get("blocks") or (), key=lambda block: int(block.get("order", 0)))
        texts = [block for block in blocks if block.get("kind") == "text"]
        titles = [block for block in texts if str(block.get("semanticRole", "")) in TITLE_ROLES]
        for block in titles or texts:
            text = str((block.get("payload") or {}).get("text") or "")
            for line in text.splitlines():
                if line.strip():
                    return line.strip()[:120]
    return ""


def file_digest(path: Path) -> str:
    from services.source_content.digests import digest_bytes

    return digest_bytes(path.read_bytes())


def extract_bundle(
    path: Path, *, owner_id: str, reference_snapshot_id: str
) -> tuple[dict[str, Any], dict[str, bytes]]:
    """Run the enriched PPTX extraction: manifest as JSON data plus asset bytes by id."""

    from services.source_content.pptx import extract_pptx_source_content

    extracted = extract_pptx_source_content(path, owner_id=owner_id, reference_snapshot_id=reference_snapshot_id)
    assets = {asset.asset_id: bytes(asset.payload) for asset in extracted.assets}
    return extracted.manifest.model_dump(mode="json", by_alias=True), assets


def extract_manifest(path: Path, *, owner_id: str, reference_snapshot_id: str) -> dict[str, Any]:
    """Run the enriched PPTX extraction and return the manifest as JSON data."""

    return extract_bundle(path, owner_id=owner_id, reference_snapshot_id=reference_snapshot_id)[0]


def bundle_from_pptx(path: Path, *, label: str | None = None) -> SourceBundle:
    if not path.is_file():
        raise SourceLookupError(f"PPTX not found: {path}")
    digest = file_digest(path)
    snapshot_id = f"offline-{digest.removeprefix('sha256:')[:16]}"
    manifest, assets = extract_bundle(path, owner_id="offline", reference_snapshot_id=snapshot_id)
    return SourceBundle(
        label=label or path.stem,
        mode="pptx",
        manifest=manifest,
        stored_manifest=None,
        pptx_path=path,
        owner_scope="offline",
        reference_snapshot_id=snapshot_id,
        typography_source="re-extracted",
        notes=[f"extracted from {path} ({digest})"],
        assets=assets,
    )


# --- Tokens and deck context ---------------------------------------------------


def blend_hex(foreground: str, background: str, share: float) -> str:
    """Mix ``share`` of ``background`` into ``foreground`` (both ``#RRGGBB``)."""

    share = min(1.0, max(0.0, share))
    fg = [int(foreground[i : i + 2], 16) for i in (1, 3, 5)]
    bg = [int(background[i : i + 2], 16) for i in (1, 3, 5)]
    mixed = [round(f * (1.0 - share) + b * share) for f, b in zip(fg, bg, strict=True)]
    return "#" + "".join(f"{channel:02X}" for channel in mixed)


def build_tokens(style_id: str | None, *, deck_title: str) -> tuple[Any, str]:
    """``ComposerTokens`` plus a label saying where they came from.

    The harness derives them through the exact function the generation pipeline
    calls (``services.composer.pipeline_inputs.composer_tokens``).  A private
    copy of that derivation would let the evaluated palette and font stack drift
    away from what production renders, which is how the stack the eval measured
    came to differ from the stack the product shipped.
    """

    from services.composer.fonts import FONT_FAMILY, font_family_stack
    from services.composer.pipeline_inputs import composer_tokens
    from services.composer.plan import ComposerTokens

    if not style_id:
        tokens = ComposerTokens(
            **NEUTRAL_TOKENS,
            font_family=font_family_stack(),
            deck_title=deck_title,
        )
        label = "neutral palette"
    else:
        from utils.authored_styles import resolve_authored_style

        style = resolve_authored_style(style_id)
        tokens = composer_tokens(style, deck_title_text=deck_title)
        label = f"services.composer.pipeline_inputs.composer_tokens for style '{style.id}'"
    leading_family = tokens.font_family.split(",", 1)[0].strip().strip("'\"")
    if leading_family.casefold() != FONT_FAMILY.casefold():
        # The exporter admits a Korean run as native text only when the run's
        # own stack leads with the embeddable family; an eval that measured any
        # other stack would not be measuring the product.
        raise SourceLookupError(
            f"composer font stack must lead with {FONT_FAMILY}, got {tokens.font_family!r}"
        )
    return tokens, label


def build_deck_context(units: Sequence[Mapping[str, Any]], manifest: Mapping[str, Any], *, deck_title: str) -> Any:
    from services.composer.contracts import DeckContext, repeated_texts

    _, page_emu = find_slide_size(manifest)
    return DeckContext(
        page_count=len(units),
        repeated_texts=repeated_texts(units),
        source_page_emu=page_emu,
        deck_title=deck_title,
    )


__all__ = [
    "NEUTRAL_TOKENS",
    "SourceBundle",
    "SourceLookupError",
    "blend_hex",
    "build_deck_context",
    "build_tokens",
    "bundle_from_pptx",
    "deck_title_from_units",
    "extract_manifest",
    "file_digest",
    "load_env_json",
    "sorted_units",
]
