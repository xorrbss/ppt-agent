"""UI-facing, server-issued capability manifest for authored-only migration."""

from __future__ import annotations

from typing import Any

from services.compatibility_migration.admission import (
    authored_only_admission_policy,
)


def build_authored_only_capability_manifest() -> dict[str, Any]:
    policy = authored_only_admission_policy()
    return {
        "schema_version": 1,
        "authored_only": {
            "new_presentation_admission": policy.enabled,
            "canary_stage": policy.canary_stage,
            "new_presentation_driver": policy.new_presentation_driver,
            "blocked_new_drivers": list(policy.blocked_drivers),
        },
        "legacy_boundary": {
            "runtime": "frozen",
            "allowed_actions": [
                "open",
                "preview",
                "export",
                "download",
                "delete",
                "upgrade-fork",
            ],
            "mutation_error_code": "legacy_presentation_frozen",
            "upgrade_creates_new_presentation_id": True,
        },
        "ui": {
            "show_legacy_deck_boundary": True,
            "show_migration_wizard": True,
            "hide_legacy_create_actions": policy.enabled,
            "disable_legacy_mutations": True,
        },
    }


__all__ = ["build_authored_only_capability_manifest"]
