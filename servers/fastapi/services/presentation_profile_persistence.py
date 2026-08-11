"""Single server-owned persistence boundary for presentation profiles."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, cast

from services.presentation_profiles import (
    GENERATION_MODE_COMPATIBILITY,
    PRESENTATION_DRIVER_REGISTRY,
    PROFILE_SCHEMA_VERSION,
    PersistedIdentity,
    PresentationProfile,
    PresentationProfileError,
    resolve_presentation_profile,
)
from utils.datetime_utils import get_current_utc_datetime


def stamp_presentation_profile(
    identity: PersistedIdentity,
    *,
    allow_driver_transition: bool = False,
) -> PresentationProfile:
    """Derive and persist a canonical profile from authoritative row fields."""

    if isinstance(identity, Mapping):
        raise PresentationProfileError("presentation_profile_row_required")
    row = cast(Any, identity)
    existing_driver = getattr(row, "profile_driver_id", None)
    existing_schema = getattr(row, "profile_schema_version", None)
    known_drivers = set(PRESENTATION_DRIVER_REGISTRY)
    if existing_driver is not None and (
        existing_driver not in known_drivers
        or existing_schema != PROFILE_SCHEMA_VERSION
    ):
        raise PresentationProfileError("unsupported_presentation_driver")

    # Canonical fields are output-only. Temporarily exclude them so a client or
    # stale rolling reader cannot select the driver at write time.
    row.profile_driver_id = None
    row.profile_schema_version = None
    try:
        profile = resolve_presentation_profile(identity)
    except BaseException:
        row.profile_driver_id = existing_driver
        row.profile_schema_version = existing_schema
        raise

    if (
        existing_driver is not None
        and existing_driver != profile.driver_id
        and not allow_driver_transition
    ):
        row.profile_driver_id = existing_driver
        row.profile_schema_version = existing_schema
        raise PresentationProfileError("presentation_profile_write_conflict")

    row.profile_driver_id = profile.driver_id
    row.profile_schema_version = PROFILE_SCHEMA_VERSION
    row.profile_backfilled_at = get_current_utc_datetime()
    row.generation_mode = GENERATION_MODE_COMPATIBILITY.encode_profile(profile)
    return profile


__all__ = ["stamp_presentation_profile"]
