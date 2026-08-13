from __future__ import annotations

import asyncio
import logging
from typing import Any

from services.provider_credential_vault import CredentialRef
from services.provider_secure_runtime import (
    delete_provider_setting_references,
    protect_provider_settings_for_storage,
)
from services.provider_settings_values import sanitize_provider_settings_for_storage


logger = logging.getLogger(__name__)


def sanitize_for_storage(config: dict[str, Any]) -> dict[str, Any]:
    return sanitize_provider_settings_for_storage(config)


def prepare_for_storage(
    config: dict[str, Any],
    *,
    created_references: list[CredentialRef] | None = None,
) -> dict[str, Any]:
    return dict(
        protect_provider_settings_for_storage(
            sanitize_for_storage(config),
            created_references=created_references,
        )
    )


async def delete_setting_references_best_effort(
    references: list[CredentialRef],
    *,
    outcome: str,
) -> None:
    if not references:
        return
    try:
        await asyncio.to_thread(delete_provider_setting_references, references)
    except Exception as error:
        logger.warning(
            "provider setting credential cleanup outcome=%s count=%d error=%s",
            outcome,
            len(references),
            type(error).__name__,
        )
