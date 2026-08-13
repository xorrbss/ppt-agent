"""Immutable legacy-to-authored upgrade fork service."""

from __future__ import annotations

import json
import uuid
from copy import deepcopy

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from models.compatibility_migration import UpgradeForkRequest, UpgradeForkResponse
from models.sql.presentation import (
    PRESENTATION_LIFECYCLE_DELETED,
    PRESENTATION_LIFECYCLE_PUBLISHED,
    PresentationModel,
)
from models.sql.slide import SlideModel
from services.compatibility_migration.archive import build_signed_archive_manifest
from services.compatibility_migration.census import classify_presentation
from services.presentation_profile_persistence import stamp_presentation_profile
from services.presentation_profiles import (
    PresentationProfileError,
    resolve_presentation_profile,
)
from utils.owner_context import get_current_owner_id

UPGRADE_CONTRACT_ID = "authored-compatibility-upgrade-v1"


class CompatibilityUpgradeError(ValueError):
    def __init__(self, code: str, *, status_code: int = 409):
        self.code = code
        self.status_code = status_code
        super().__init__(code)


def _canonical_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")


async def _load_owned_source(
    sql_session: AsyncSession,
    presentation_id: uuid.UUID,
) -> tuple[PresentationModel, list[SlideModel]]:
    source = await sql_session.get(PresentationModel, presentation_id)
    owner_id = get_current_owner_id()
    if (
        source is None
        or source.lifecycle_status == PRESENTATION_LIFECYCLE_DELETED
        or source.owner_id != owner_id
    ):
        raise CompatibilityUpgradeError(
            "compatibility_presentation_not_found", status_code=404
        )
    slides = list(
        (
            await sql_session.scalars(
                select(SlideModel)
                .where(SlideModel.presentation == presentation_id)
                .order_by(SlideModel.index, SlideModel.id)
            )
        ).all()
    )
    if any(slide.owner_id != owner_id for slide in slides):
        raise CompatibilityUpgradeError(
            "compatibility_presentation_not_found", status_code=404
        )
    return source, slides


def _source_snapshot(
    source: PresentationModel,
    slides: list[SlideModel],
) -> bytes:
    return _canonical_bytes(
        {
            "presentation": source.model_dump(mode="json"),
            "slides": [slide.model_dump(mode="json") for slide in slides],
        }
    )


async def upgrade_presentation_fork(
    sql_session: AsyncSession,
    *,
    source_presentation_id: uuid.UUID,
    request: UpgradeForkRequest,
    archive_signing_key: bytes,
    archive_signing_key_id: str,
) -> UpgradeForkResponse:
    """Create a new authored deck while leaving the source row and slides untouched."""

    source, source_slides = await _load_owned_source(
        sql_session, source_presentation_id
    )
    census_entry = classify_presentation(source, source_slides)
    if census_entry.quarantine:
        raise CompatibilityUpgradeError("compatibility_presentation_quarantined")
    try:
        source_profile = resolve_presentation_profile(source)
    except PresentationProfileError as error:
        raise CompatibilityUpgradeError(
            "compatibility_presentation_quarantined"
        ) from error
    if source_profile.driver_id == "authored":
        raise CompatibilityUpgradeError("compatibility_upgrade_source_not_legacy")
    if source.revision != request.expected_source_revision:
        raise CompatibilityUpgradeError("compatibility_source_revision_conflict")

    source_snapshot = _source_snapshot(source, source_slides)
    target_id = uuid.uuid4()
    if target_id == source.id:  # defensive even though UUID4 collision is negligible
        raise CompatibilityUpgradeError("compatibility_upgrade_target_id_conflict")

    theme = deepcopy(source.theme) if isinstance(source.theme, dict) else {}
    theme["mode"] = "authored"
    target = PresentationModel(
        id=target_id,
        owner_id=source.owner_id,
        content=source.content,
        n_slides=len(request.slides),
        language=source.language,
        title=request.title or source.title,
        file_paths=deepcopy(source.file_paths),
        reference_snapshot_ids=deepcopy(source.reference_snapshot_ids),
        outlines=deepcopy(source.outlines),
        layout=None,
        structure=None,
        instructions=source.instructions,
        tone=source.tone,
        verbosity=source.verbosity,
        include_table_of_contents=source.include_table_of_contents,
        include_title_slide=source.include_title_slide,
        web_search=source.web_search,
        theme=theme,
        fonts=deepcopy(source.fonts),
        generation_mode="standard",
        community_design_ids=None,
        deck_plan=None,
        mode="authored",
        version="v1-standard",
        lifecycle_status=PRESENTATION_LIFECYCLE_PUBLISHED,
    )
    stamp_presentation_profile(target)
    target_slides = [
        SlideModel(
            owner_id=source.owner_id,
            presentation=target_id,
            layout_group="authored",
            layout=f"authored:{UPGRADE_CONTRACT_ID}",
            index=slide.index,
            content={
                "__authored__": True,
                "role": "COMPATIBILITY_UPGRADE",
                "source_slide_index": slide.index,
            },
            html_content=slide.html_content,
            speaker_note=slide.speaker_note,
            properties=deepcopy(slide.properties),
        )
        for slide in request.slides
    ]

    runtime_snapshot = _canonical_bytes(
        {
            "contract_id": UPGRADE_CONTRACT_ID,
            "profile_schema_version": source_profile.schema_version,
            "source_driver_id": source_profile.driver_id,
            "source_revision": source.revision,
        }
    )
    artifact_snapshot = _canonical_bytes(
        {
            "presentation_id": target_id,
            "driver_id": "authored",
            "slides": [slide.model_dump(mode="json") for slide in request.slides],
        }
    )
    manifest = build_signed_archive_manifest(
        source_presentation_id=source.id,
        target_presentation_id=target_id,
        owner_id=source.owner_id,
        source_payload=source_snapshot,
        runtime_payload=runtime_snapshot,
        artifact_payload=artifact_snapshot,
        signing_key=archive_signing_key,
        signing_key_id=archive_signing_key_id,
    )

    try:
        sql_session.add(target)
        sql_session.add_all(target_slides)
        await sql_session.commit()
    except BaseException:
        await sql_session.rollback()
        raise

    # The source snapshot is only retained as a digest in ``manifest``.
    return UpgradeForkResponse(
        source_presentation_id=source.id,
        presentation_id=target.id,
        source_revision=source.revision,
        archive_manifest=manifest,
    )


__all__ = [
    "CompatibilityUpgradeError",
    "UPGRADE_CONTRACT_ID",
    "upgrade_presentation_fork",
]
