"""Measured, fail-closed gates for retiring legacy persistence facades."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import os

from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from models.sql.image_asset import ImageAsset
from models.sql.presentation import PresentationModel
from models.sql.presentation_generation_job import PresentationGenerationJob
from services.assets.compatibility import dangling_canonical_image_count
from services.presentation_profiles import (
    GENERATION_MODE_COMPATIBILITY,
    PROFILE_SCHEMA_VERSION,
    PresentationProfileError,
    resolve_presentation_profile,
)
from utils.datetime_utils import get_current_utc_datetime
from utils.owner_context import get_current_owner_id, get_current_owner_is_admin


_TRUE = frozenset({"1", "true", "yes", "on"})


@dataclass(frozen=True, slots=True)
class CompatibilityReadiness:
    evidence_scope: str
    global_removal_evidence: bool
    image_rows: int
    image_rows_without_canonical_asset: int
    image_rows_with_dangling_or_unready_asset: int
    presentation_rows: int
    presentation_rows_without_canonical_profile: int
    presentation_rows_with_invalid_profile: int
    generation_mode_codec_mismatches: int
    presentations_with_raw_file_paths: int
    jobs_with_raw_file_paths: int
    legacy_image_path_callers_disabled: bool
    legacy_generation_mode_readers_disabled: bool
    legacy_raw_path_callers_disabled: bool

    @property
    def image_path_api_removal_ready(self) -> bool:
        return (
            self.global_removal_evidence
            and self.legacy_image_path_callers_disabled
            and self.image_rows_without_canonical_asset == 0
            and self.image_rows_with_dangling_or_unready_asset == 0
        )

    @property
    def raw_file_request_removal_ready(self) -> bool:
        return (
            self.global_removal_evidence
            and self.legacy_raw_path_callers_disabled
            and self.presentations_with_raw_file_paths == 0
            and self.jobs_with_raw_file_paths == 0
        )

    @property
    def generation_mode_removal_ready(self) -> bool:
        return (
            self.global_removal_evidence
            and self.legacy_generation_mode_readers_disabled
            and self.presentation_rows_without_canonical_profile == 0
            and self.presentation_rows_with_invalid_profile == 0
            and self.generation_mode_codec_mismatches == 0
        )

    def as_dict(self) -> dict[str, object]:
        return {
            **asdict(self),
            "image_path_api_removal_ready": self.image_path_api_removal_ready,
            "raw_file_request_removal_ready": self.raw_file_request_removal_ready,
            "generation_mode_removal_ready": self.generation_mode_removal_ready,
        }

    def require_safe_removal(self, facade: str) -> None:
        readiness = {
            "image_path_api": self.image_path_api_removal_ready,
            "raw_file_request": self.raw_file_request_removal_ready,
            "generation_mode": self.generation_mode_removal_ready,
        }.get(facade)
        if readiness is None:
            raise ValueError("compatibility_facade_unknown")
        if not readiness:
            raise RuntimeError(f"compatibility_facade_not_ready:{facade}")


@dataclass(frozen=True, slots=True)
class PresentationProfileBackfillResult:
    scanned: int
    backfilled: int
    failed: int


def _owner_predicate(model):
    owner_id = get_current_owner_id()
    column = model.owner_id
    return column.is_(None) if owner_id is None else column == owner_id


def _env_true(name: str) -> bool:
    return (os.getenv(name) or "").strip().lower() in _TRUE


async def backfill_presentation_profiles(
    session: AsyncSession,
    *,
    aggregate_all_owners: bool = False,
    limit: int = 500,
) -> PresentationProfileBackfillResult:
    """Backfill canonical profile identity without removing the legacy codec.

    Ordinary calls remain owner-scoped. Only an explicit administrator
    aggregate may traverse other owners and anonymous bootstrap rows.
    """

    if limit < 1 or limit > 5000:
        raise ValueError("presentation_profile_backfill_limit_invalid")
    if aggregate_all_owners and not get_current_owner_is_admin():
        raise PermissionError("admin_aggregate_required")
    statement = select(PresentationModel).where(
        PresentationModel.profile_driver_id.is_(None)
    )
    if aggregate_all_owners:
        statement = statement.execution_options(skip_owner_scope=True)
    else:
        statement = statement.where(_owner_predicate(PresentationModel))
    rows = (
        await session.execute(
            statement.order_by(
                PresentationModel.created_at,
                PresentationModel.id,
            ).limit(limit)
        )
    ).scalars().all()
    backfilled = 0
    failed = 0
    for row in rows:
        try:
            profile = resolve_presentation_profile(row)
        except PresentationProfileError:
            failed += 1
            continue
        row.profile_driver_id = profile.driver_id
        row.profile_schema_version = PROFILE_SCHEMA_VERSION
        row.profile_backfilled_at = get_current_utc_datetime()
        # Keep rolling readers coherent while the compatibility column exists.
        row.generation_mode = GENERATION_MODE_COMPATIBILITY.encode_profile(profile)
        session.add(row)
        backfilled += 1
    await session.commit()
    return PresentationProfileBackfillResult(len(rows), backfilled, failed)


async def compatibility_readiness(
    session: AsyncSession,
    *,
    aggregate_all_owners: bool = False,
) -> CompatibilityReadiness:
    """Collect scoped evidence; only an admin aggregate can authorize removal."""

    if aggregate_all_owners and not get_current_owner_is_admin():
        raise PermissionError("admin_aggregate_required")

    image_statement = select(ImageAsset)
    presentation_statement = select(PresentationModel)
    if aggregate_all_owners:
        image_statement = image_statement.execution_options(skip_owner_scope=True)
        presentation_statement = presentation_statement.execution_options(
            skip_owner_scope=True
        )
    else:
        image_statement = image_statement.where(_owner_predicate(ImageAsset))
        presentation_statement = presentation_statement.where(
            _owner_predicate(PresentationModel)
        )
    images = (await session.execute(image_statement)).scalars().all()
    presentations = (await session.execute(presentation_statement)).scalars().all()
    invalid_profiles = 0
    codec_mismatches = 0
    for presentation in presentations:
        if presentation.profile_driver_id is None:
            continue
        try:
            profile = resolve_presentation_profile(presentation)
            if (
                GENERATION_MODE_COMPATIBILITY.encode_profile(profile)
                != presentation.generation_mode
            ):
                codec_mismatches += 1
        except PresentationProfileError:
            invalid_profiles += 1

    presentation_ids = {row.id for row in presentations}
    job_statement = select(PresentationGenerationJob)
    if aggregate_all_owners:
        job_statement = job_statement.execution_options(skip_owner_scope=True)
    jobs = (await session.execute(job_statement)).scalars().all()
    raw_jobs = sum(
        1
        for job in jobs
        if job.presentation_id in presentation_ids
        and isinstance(job.request_payload, dict)
        and bool(job.request_payload.get("files"))
    )
    return CompatibilityReadiness(
        evidence_scope="global" if aggregate_all_owners else "owner",
        global_removal_evidence=aggregate_all_owners,
        image_rows=len(images),
        image_rows_without_canonical_asset=sum(
            image.canonical_asset_id is None for image in images
        ),
        image_rows_with_dangling_or_unready_asset=(
            await dangling_canonical_image_count(
                session,
                aggregate_all_owners=aggregate_all_owners,
            )
        ),
        presentation_rows=len(presentations),
        presentation_rows_without_canonical_profile=sum(
            row.profile_driver_id is None for row in presentations
        ),
        presentation_rows_with_invalid_profile=invalid_profiles,
        generation_mode_codec_mismatches=codec_mismatches,
        presentations_with_raw_file_paths=sum(
            bool(row.file_paths) for row in presentations
        ),
        jobs_with_raw_file_paths=raw_jobs,
        legacy_image_path_callers_disabled=_env_true(
            "LEGACY_IMAGE_PATH_CALLERS_DISABLED"
        ),
        legacy_generation_mode_readers_disabled=_env_true(
            "LEGACY_GENERATION_MODE_READERS_DISABLED"
        ),
        legacy_raw_path_callers_disabled=_env_true(
            "LEGACY_RAW_PATH_CALLERS_DISABLED"
        ),
    )


__all__ = [
    "CompatibilityReadiness",
    "PresentationProfileBackfillResult",
    "backfill_presentation_profiles",
    "compatibility_readiness",
]
