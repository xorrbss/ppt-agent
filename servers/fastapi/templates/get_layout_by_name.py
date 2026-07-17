import logging
import json
import os
import aiohttp
from typing import Any

from fastapi import HTTPException

from models.slide_spec_model import archetype_to_layout_id
from templates.custom_layout_from_db import load_custom_presentation_layout
from templates.presentation_layout import PresentationLayoutModel
from utils.archetype_profiles import ARCHETYPE_PROFILES
from utils.get_env import get_next_internal_base_url
from utils.icon_weights import extract_icon_weight_from_settings
from utils.internal_http import internal_request_headers

LOGGER = logging.getLogger(__name__)

_MAX_LOG_DETAIL = 600


def _preview_detail(text: str, limit: int = _MAX_LOG_DETAIL) -> str:
    text = text.replace("\n", " ").strip()
    if len(text) <= limit:
        return text
    return text[: limit - 3] + "..."


def _read_builtin_template_settings(layout_name: str) -> dict[str, Any] | None:
    if not layout_name or layout_name.startswith("custom-"):
        return None
    if "/" in layout_name or "\\" in layout_name or layout_name in {".", ".."}:
        return None

    service_dir = os.path.dirname(__file__)
    candidates = [
        os.path.abspath(
            os.path.join(
                service_dir,
                "..",
                "..",
                "nextjs",
                "app",
                "presentation-templates",
                layout_name,
                "settings.json",
            )
        ),
        os.path.abspath(
            os.path.join(
                os.getcwd(),
                "..",
                "nextjs",
                "app",
                "presentation-templates",
                layout_name,
                "settings.json",
            )
        ),
    ]

    for settings_path in candidates:
        if not os.path.isfile(settings_path):
            continue
        try:
            with open(settings_path, "r", encoding="utf-8") as settings_file:
                settings = json.load(settings_file)
            return settings if isinstance(settings, dict) else None
        except Exception as exc:  # noqa: BLE001
            LOGGER.warning(
                "[template_layout] failed reading local template settings template=%r path=%s error=%s",
                layout_name,
                settings_path,
                _preview_detail(str(exc)),
            )
            return None

    return None


def _read_builtin_layout_artifact(layout_name: str) -> dict[str, Any] | None:
    """Return the build-time compiled layout payload for a built-in group.

    `scripts/generate-layout-schemas.mjs` compiles every built-in template's Zod
    Schema into `layouts.generated.json` at build time; reading it here removes the
    per-request headless scrape / runtime compile. Returns None when the artifact is
    absent (e.g. dev before a build) so the caller falls back to the Next.js route.
    """
    if not layout_name or layout_name.startswith("custom-"):
        return None
    if "/" in layout_name or "\\" in layout_name or layout_name in {".", ".."}:
        return None

    service_dir = os.path.dirname(__file__)
    candidates = [
        os.path.abspath(
            os.path.join(
                service_dir, "..", "..", "nextjs", "app",
                "presentation-templates", "layouts.generated.json",
            )
        ),
        os.path.abspath(
            os.path.join(
                os.getcwd(), "..", "nextjs", "app",
                "presentation-templates", "layouts.generated.json",
            )
        ),
    ]

    for artifact_path in candidates:
        if not os.path.isfile(artifact_path):
            continue
        try:
            with open(artifact_path, "r", encoding="utf-8") as artifact_file:
                data = json.load(artifact_file)
        except Exception as exc:  # noqa: BLE001
            LOGGER.warning(
                "[template_layout] failed reading layout artifact path=%s error=%s",
                artifact_path,
                _preview_detail(str(exc)),
            )
            return None
        payload = data.get(layout_name) if isinstance(data, dict) else None
        if isinstance(payload, dict) and payload.get("slides"):
            # Copy: the caller mutates icon_weight from live settings.json.
            return dict(payload)
        return None

    return None


async def _fetch_template_fallback_payload(
    layout_name: str,
) -> tuple[dict[str, Any] | None, str | None]:
    fallback_url = f"{get_next_internal_base_url()}/api/template?group={layout_name}"
    LOGGER.info(
        "[template_layout] trying HTTP fallback template=%r url=%s",
        layout_name,
        fallback_url,
    )
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                fallback_url, headers=internal_request_headers()
            ) as response:
                if response.status == 200:
                    payload = await response.json()
                    LOGGER.info(
                        "[template_layout] fallback OK template=%r slide_count=%d",
                        layout_name,
                        len(payload.get("slides") or []),
                    )
                    return payload, None

                error = await response.text()
                LOGGER.warning(
                    "[template_layout] fallback HTTP %s template=%r body=%s",
                    response.status,
                    layout_name,
                    _preview_detail(error or ""),
                )
                return None, error
    except aiohttp.ClientError as exc:
        error = str(exc)
        LOGGER.warning(
            "[template_layout] fallback request failed template=%r error=%s",
            layout_name,
            error,
        )
        return None, error
    except Exception as exc:  # noqa: BLE001
        error = str(exc)
        LOGGER.warning(
            "[template_layout] fallback unexpected error template=%r error=%s",
            layout_name,
            _preview_detail(error),
        )
        return None, error


def _build_adaptive_layout() -> PresentationLayoutModel:
    """Build the adaptive group's layout directly from ARCHETYPE_PROFILES.

    The adaptive group has no per-file Schema TSX templates — its "layout" is
    just the declared archetype list (single source of truth: ARCHETYPE_PROFILES).
    Resolving it from the backend needs neither the export runtime (extract-schema)
    nor the /api/template route (both only understand per-file Schema templates),
    so the one-shot /generate path works on every platform. The composer fills
    closed per-archetype Pydantic models, so each slide's json_schema is unused.
    """
    settings = _read_builtin_template_settings("adaptive")
    icon_weight = extract_icon_weight_from_settings(settings)
    slides = [
        {
            "id": archetype_to_layout_id(archetype),
            "name": archetype,
            "description": profile.get("desc", ""),
            "json_schema": {},
        }
        for archetype, profile in ARCHETYPE_PROFILES.items()
    ]
    return PresentationLayoutModel(
        name="adaptive", ordered=False, icon_weight=icon_weight, slides=slides
    )


async def get_layout_by_name(layout_name: str) -> PresentationLayoutModel:
    if layout_name.startswith("custom-"):
        return await load_custom_presentation_layout(layout_name)

    if layout_name == "adaptive":
        return _build_adaptive_layout()

    # 1) Build-time artifact (deterministic; no running frontend required).
    schema_payload = _read_builtin_layout_artifact(layout_name)
    if schema_payload is not None:
        LOGGER.info(
            "[template_layout] resolved from build artifact template=%r slides=%d",
            layout_name,
            len(schema_payload.get("slides") or []),
        )

    # 2) Fall back to the Next.js JSON route (honours NEXT_INTERNAL_URL) — the dev
    #    path before a build has produced the artifact.
    if schema_payload is None:
        schema_payload, fallback_error = await _fetch_template_fallback_payload(
            layout_name
        )
        if schema_payload is None:
            error_detail = fallback_error or "unknown error"
            LOGGER.error(
                "[template_layout] no schema payload template=%r detail=%s",
                layout_name,
                _preview_detail(error_detail),
            )
            raise HTTPException(
                status_code=404,
                detail=f"Template '{layout_name}' not found: {error_detail}",
            )

    # Live settings.json wins for icon weight: the artifact already carries it, but a
    # dev edit to settings.json should still take effect without a rebuild.
    local_settings = _read_builtin_template_settings(layout_name)
    if local_settings:
        local_icon_weight = extract_icon_weight_from_settings(local_settings)
        schema_payload["icon_weight"] = local_icon_weight
        LOGGER.info(
            "[template_layout] local settings applied template=%r icon_weight=%s",
            layout_name,
            local_icon_weight,
        )

    slides = schema_payload.get("slides") or []
    if not slides:
        LOGGER.error(
            "[template_layout] slides empty after resolve template=%r keys=%s",
            layout_name,
            list(schema_payload.keys()),
        )
        raise HTTPException(
            status_code=404,
            detail=f"Template '{layout_name}' not found",
        )

    LOGGER.info(
        "[template_layout] building PresentationLayoutModel template=%r slides=%d icon_weight=%s",
        layout_name,
        len(slides),
        schema_payload.get("icon_weight"),
    )
    return PresentationLayoutModel(**schema_payload)
