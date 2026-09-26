"""Canonical source and asset hashes for durable export attempts."""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from hashlib import sha256
import hmac
import json
from pathlib import Path
from typing import Any, Literal
import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from models.sql.presentation import PresentationModel
from models.sql.slide import SlideModel
from services.presentation_export_capabilities import (
    PresentationExportContractError, PresentationExportFreshnessError,
)
from services.workspace_asset_service import resolve_workspace_asset_download
from services.workspace_service import WorkspaceServiceError


def _canonical_json(value: Any) -> bytes:
    try:
        serialized = json.dumps(
            value,
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
    except (TypeError, ValueError) as error:
        raise PresentationExportContractError("presentation_export_snapshot_invalid") from error
    return serialized.encode("utf-8")


def sha256_canonical_json(value: Any) -> str:
    return sha256(_canonical_json(value)).hexdigest()


def build_export_source_snapshot_sha256(
    presentation: PresentationModel,
    slides: Sequence[SlideModel],
    *,
    durable_presentation_id: uuid.UUID | None = None,
) -> str:
    """Hash semantic persisted inputs without transient staging/slide UUIDs."""

    slide_payloads = [
        {
            "index": slide.index,
            "layout_group": slide.layout_group,
            "layout": slide.layout,
            "content": slide.content,
            "ui": slide.ui,
            "html_content": slide.html_content,
            **(
                {"svg_content": slide.svg_content, "source_format": "svg"}
                if getattr(slide, "source_format", None) == "svg"
                else {}
            ),
            "speaker_note": slide.speaker_note,
            "properties": slide.properties,
        }
        for slide in slides
    ]
    slide_payloads.sort(key=lambda item: (item["index"], sha256_canonical_json(item)))

    return sha256_canonical_json(
        {
            "schema_version": 2,
            "presentation": {
                "id": str(durable_presentation_id or presentation.id),
                "revision": presentation.revision,
                "lifecycle_status": presentation.lifecycle_status,
                "version": presentation.version,
                "mode": presentation.mode,
                "content": presentation.content,
                "title": presentation.title,
                "language": presentation.language,
                "reference_snapshot_ids": presentation.reference_snapshot_ids,
                "outlines": presentation.outlines,
                "layout": presentation.layout,
                "structure": presentation.structure,
                "instructions": presentation.instructions,
                "tone": presentation.tone,
                "verbosity": presentation.verbosity,
                "include_table_of_contents": presentation.include_table_of_contents,
                "include_title_slide": presentation.include_title_slide,
                "theme": presentation.theme,
                "fonts": presentation.fonts,
                "generation_mode": presentation.generation_mode,
                "profile_driver_id": presentation.profile_driver_id,
                "profile_schema_version": presentation.profile_schema_version,
            },
            "slides": slide_payloads,
        }
    )


def build_export_html_source_sha256(slides: Sequence[SlideModel]) -> str:
    return sha256_canonical_json(
        [
            {
                "index": slide.index,
                "html_content": slide.html_content,
                **(
                    {"svg_content": slide.svg_content, "source_format": "svg"}
                    if getattr(slide, "source_format", None) == "svg"
                    else {}
                ),
            }
            for slide in sorted(slides, key=lambda item: (item.index, str(item.id)))
        ]
    )


def build_export_composition_sha256(
    presentation: PresentationModel,
    slides: Sequence[SlideModel],
) -> str:
    """Seal exactly the authored paint/layout inputs shared by preview/export."""

    return sha256_canonical_json(
        {
            "schema_version": 1,
            "presentation": {
                "mode": presentation.mode,
                "theme": presentation.theme,
                "fonts": presentation.fonts,
                "layout": presentation.layout,
                "structure": presentation.structure,
                "profile_driver_id": presentation.profile_driver_id,
                "profile_schema_version": presentation.profile_schema_version,
            },
            "slides": [
                {
                    "index": slide.index,
                    "layout_group": slide.layout_group,
                    "layout": slide.layout,
                    "html_content": slide.html_content,
                    **(
                        {"svg_content": slide.svg_content, "source_format": "svg"}
                        if getattr(slide, "source_format", None) == "svg"
                        else {}
                    ),
                    "ui": slide.ui,
                    "properties": slide.properties,
                }
                for slide in sorted(slides, key=lambda item: (item.index, str(item.id)))
            ],
        }
    )


def build_export_asset_manifest_sha256(asset_digests: dict[str, str]) -> str:
    return sha256_canonical_json(
        {
            "schema_version": 1,
            "assets": dict(sorted(asset_digests.items())),
        }
    )


def _sha256_file(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


async def require_export_asset_digests(
    session: AsyncSession,
    *,
    expected_asset_digests: dict[str, str],
) -> None:
    """Revalidate each canonical immutable workspace asset against real bytes."""

    for raw_asset_id, expected_digest in sorted(expected_asset_digests.items()):
        if raw_asset_id.startswith("asset:"):
            # Canonical Scene assets have no workspace UUID row. Their strict
            # manifest entry already binds ID, digest, media type, byte size,
            # and either a digest-verified data URI or the exact asset-store
            # reference. The source snapshot seals that storage reference.
            continue
        try:
            asset_id = uuid.UUID(raw_asset_id)
            asset, private_path = await resolve_workspace_asset_download(
                session,
                asset_id=asset_id,
            )
        except (ValueError, WorkspaceServiceError) as error:
            raise PresentationExportFreshnessError(
                "presentation_export_asset_unavailable",
                path=raw_asset_id,
            ) from error
        stored_digest = (asset.sha256 or "").lower()
        if not stored_digest or not hmac.compare_digest(
            stored_digest,
            expected_digest,
        ):
            raise PresentationExportFreshnessError(
                "presentation_export_asset_digest_mismatch",
                path=raw_asset_id,
            )
        try:
            stat_size = private_path.stat().st_size
            actual_digest = await asyncio.to_thread(_sha256_file, private_path)
        except OSError as error:
            raise PresentationExportFreshnessError(
                "presentation_export_asset_unavailable",
                path=raw_asset_id,
            ) from error
        if stat_size != asset.size_bytes or not hmac.compare_digest(
            actual_digest,
            expected_digest,
        ):
            raise PresentationExportFreshnessError(
                "presentation_export_asset_digest_mismatch",
                path=raw_asset_id,
            )


def build_sync_export_hashes(
    *,
    presentation_id: uuid.UUID,
    export_format: Literal["pptx", "pdf"],
    source_revision: int,
    source_snapshot_sha256: str,
    identity: str,
) -> tuple[str, str]:
    request_hash = sha256_canonical_json(
        {
            "presentation_id": str(presentation_id),
            "format": export_format,
            "source_revision": source_revision,
            "source_snapshot_sha256": source_snapshot_sha256,
        }
    )
    idempotency_key_hash = sha256(f"presentation-export:{identity}".encode("utf-8")).hexdigest()
    return request_hash, idempotency_key_hash
