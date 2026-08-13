"""Fail-closed admission for new presentations during authored-only rollout.

The persisted presentation profile remains authoritative for existing decks.
This module only controls creation: it never reclassifies or mutates an existing
presentation, and it deliberately does not provide a compatibility bypass.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Final

from fastapi import HTTPException

from models.sql.presentation import PresentationModel
from services.presentation_profiles import PROFILE_SCHEMA_VERSION
from services.compatibility_migration.rollout import (
    AuthoredOnlyRolloutFlags,
    CanaryStage,
)
from utils.owner_context import get_current_owner_id


AUTHORED_ONLY_ADMISSION_CLOSED: Final = "legacy_generation_admission_closed"
AUTHORED_ONLY_BLANK_CLOSED: Final = "legacy_blank_presentation_admission_closed"
AUTHORED_ONLY_TEMPLATE_IMPORT_CLOSED: Final = "template_v2_import_admission_closed"
_TRUE_VALUES: Final = frozenset({"1", "true", "yes", "on"})
_FALSE_VALUES: Final = frozenset({"0", "false", "no", "off"})


@dataclass(frozen=True, slots=True)
class AuthoredOnlyAdmissionPolicy:
    schema_version: int
    enabled: bool
    new_presentation_driver: str
    blocked_drivers: tuple[str, ...]
    stable_error_code: str
    canary_stage: str


def _environment_boolean(name: str, *, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    normalized = raw.strip().lower()
    if normalized in _TRUE_VALUES:
        return True
    if normalized in _FALSE_VALUES:
        return False
    # A malformed safety flag must not reopen legacy creation.
    return True


def authored_only_admission_policy() -> AuthoredOnlyAdmissionPolicy:
    enabled = _environment_boolean(
        "AUTHORED_ONLY_NEW_PRESENTATION_ADMISSION",
        default=False,
    )
    raw_stage = os.getenv("AUTHORED_ONLY_CANARY_STAGE", "internal").strip().lower()
    try:
        stage = CanaryStage(raw_stage)
    except ValueError:
        # Invalid rollout configuration must never admit legacy creation.
        stage = CanaryStage.PERCENT_100
    internal_owners = frozenset(
        owner.strip()
        for owner in os.getenv("AUTHORED_ONLY_INTERNAL_OWNER_IDS", "").split(",")
        if owner.strip()
    )
    rollout = AuthoredOnlyRolloutFlags(
        stage=stage,
        salt=os.getenv("AUTHORED_ONLY_ROLLOUT_SALT", "authored-only-default-v1"),
        internal_owner_ids=internal_owners,
        enabled=enabled,
    )
    owner_id = get_current_owner_id()
    owner_key = str(owner_id) if owner_id is not None else "anonymous"
    return AuthoredOnlyAdmissionPolicy(
        schema_version=1,
        enabled=rollout.owner_enabled(owner_key),
        new_presentation_driver="authored",
        blocked_drivers=("standard", "smart", "template-v2"),
        stable_error_code=AUTHORED_ONLY_ADMISSION_CLOSED,
        canary_stage=stage.value,
    )


def requested_driver_from_generation(request: object) -> str:
    """Resolve only server-supported compatibility request fields.

    Client-supplied profile/capability objects are intentionally ignored.
    """

    strategy = getattr(request, "strategy", None)
    template = str(getattr(request, "template", "") or "").strip().lower()
    if strategy == "template_v2":
        return "template-v2"
    if strategy == "authored" or template == "authored":
        return "authored"
    if strategy == "adaptive" or (strategy is None and template == "adaptive"):
        return "smart"
    return "standard"


def require_authored_generation_admission(request: object) -> None:
    policy = authored_only_admission_policy()
    driver = requested_driver_from_generation(request)
    if policy.enabled and driver != policy.new_presentation_driver:
        raise HTTPException(
            status_code=409,
            detail={
                "code": policy.stable_error_code,
                "requested_driver": driver,
                "required_driver": policy.new_presentation_driver,
                "upgrade_path": "compatibility.upgrade-fork",
            },
        )


def require_legacy_creation_closed(*, operation: str) -> None:
    """Reject non-generation entry points that mint a legacy-format deck."""

    policy = authored_only_admission_policy()
    if not policy.enabled:
        return
    code_by_operation = {
        "blank": AUTHORED_ONLY_BLANK_CLOSED,
        "template-v2-import": AUTHORED_ONLY_TEMPLATE_IMPORT_CLOSED,
    }
    raise HTTPException(
        status_code=409,
        detail={
            "code": code_by_operation.get(operation, policy.stable_error_code),
            "operation": operation,
            "required_driver": policy.new_presentation_driver,
            "upgrade_path": "compatibility.upgrade-fork",
        },
    )


def stamp_new_authored_presentation(presentation: PresentationModel) -> None:
    """Stamp a newly-created staging shell with canonical authored identity."""

    policy = authored_only_admission_policy()
    if not policy.enabled:
        return
    presentation.generation_mode = "standard"
    presentation.mode = "authored"
    presentation.version = "v1-standard"
    presentation.profile_driver_id = "authored"
    presentation.profile_schema_version = PROFILE_SCHEMA_VERSION
    if presentation.theme is None:
        presentation.theme = {"mode": "authored"}
    elif isinstance(presentation.theme, Mapping):
        # Preserve selected style data while making the server-issued mode
        # explicit. Do not mutate the caller-owned request object in place.
        presentation.theme = {**dict(presentation.theme), "mode": "authored"}


__all__ = [
    "AUTHORED_ONLY_ADMISSION_CLOSED",
    "AUTHORED_ONLY_BLANK_CLOSED",
    "AUTHORED_ONLY_TEMPLATE_IMPORT_CLOSED",
    "AuthoredOnlyAdmissionPolicy",
    "authored_only_admission_policy",
    "requested_driver_from_generation",
    "require_authored_generation_admission",
    "require_legacy_creation_closed",
    "stamp_new_authored_presentation",
]
