"""Liveness and readiness endpoints (EXT-08).

Both routes are mounted on the root app in ``api/main.py`` *outside* ``/api``
on purpose. ``SessionAuthMiddleware`` (``api/middlewares.py``) only requires a
session for paths starting with ``/api/`` or ``/app_data/``, plus a small
explicit set of non-API paths (``/docs``, ``/openapi.json``, ``/redoc``); see
``SessionAuthMiddleware._requires_auth``. ``/health`` and ``/ready`` are not in
that set, so they are reachable without a session even when auth is enabled —
the same pattern already used by ``/metrics/workspace`` and
``/metrics/authored-render-runtime`` in ``api/main.py``.

Because both routes are unauthenticated, their response bodies never carry
anything beyond a boolean per check plus the overall status: no configured
provider id, no credential-expiry state, no app version. That detail would
otherwise be a free information-disclosure oracle for anyone who can reach the
port. Every check still logs its full detail server-side, so an operator can
diagnose a failing check from the logs.

``/health`` is a liveness probe: it never touches the database or the
filesystem and must answer immediately. ``/ready`` is a readiness probe: it
runs a small set of independent checks, each bounded by
``_CHECK_TIMEOUT_SECONDS``.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import httpx
from fastapi import APIRouter
from sqlalchemy import text
from starlette.responses import JSONResponse

from enums.llm_provider import LLMProvider
from services.database import async_session_maker
from utils.codex_token_refresh import _is_expiring
from utils.get_env import (
    get_codex_access_token_env,
    get_codex_token_refresh_leeway_seconds,
    get_llm_provider_env,
    get_next_internal_base_url,
    get_setting_env,
    is_codex_token_auto_refresh_enabled,
)
from utils.runtime_settings import get_process_runtime_settings
from utils.slide_capture_chrome_discovery import find_chrome


LOGGER = logging.getLogger(__name__)
_CHECK_TIMEOUT_SECONDS = 2.0

router = APIRouter(tags=["health"], include_in_schema=False)


@router.get("/health")
async def liveness() -> JSONResponse:
    """Liveness: no DB, no filesystem, no outbound calls. Always 200 if serving."""
    return JSONResponse({"status": "ok"})


async def _check_database() -> dict[str, Any]:
    try:
        async with async_session_maker() as session:
            await session.execute(text("SELECT 1"))
        return {"ok": True}
    except Exception as exc:  # noqa: BLE001 - reported, never re-raised
        return {"ok": False, "detail": type(exc).__name__}


async def _check_provider_credentials() -> dict[str, Any]:
    settings = get_process_runtime_settings()
    provider_id = (get_llm_provider_env(settings) or "").strip()
    if provider_id != LLMProvider.CODEX.value:
        return {"ok": True, "provider": provider_id or None}

    if not get_codex_access_token_env(settings):
        return {"ok": False, "provider": "codex", "detail": "missing_access_token"}

    leeway_seconds = get_codex_token_refresh_leeway_seconds(settings)
    expiring = _is_expiring(settings, leeway_seconds)
    auto_refresh = is_codex_token_auto_refresh_enabled(settings)
    return {
        "ok": (not expiring) or auto_refresh,
        "provider": "codex",
        "detail": "expiring_soon" if expiring else "fresh",
        "auto_refresh": auto_refresh,
    }


async def _check_chrome() -> dict[str, Any]:
    path = await asyncio.to_thread(find_chrome)
    if path:
        return {"ok": True}
    return {"ok": False, "detail": "no_chrome_found"}


async def _check_nextjs() -> dict[str, Any]:
    if not (get_setting_env("NEXT_INTERNAL_URL") or "").strip():
        return {"ok": True, "skipped": True, "detail": "NEXT_INTERNAL_URL not set"}

    base_url = get_next_internal_base_url()
    try:
        async with httpx.AsyncClient(timeout=_CHECK_TIMEOUT_SECONDS) as client:
            response = await client.get(f"{base_url}/api/health")
        return {"ok": response.status_code < 500, "status_code": response.status_code}
    except Exception as exc:  # noqa: BLE001 - reported, never re-raised
        return {"ok": False, "detail": type(exc).__name__}


async def _bounded(name: str, coro: Any) -> tuple[str, dict[str, Any]]:
    try:
        return name, await asyncio.wait_for(coro, timeout=_CHECK_TIMEOUT_SECONDS)
    except asyncio.TimeoutError:
        return name, {"ok": False, "detail": "timeout"}
    except Exception as exc:  # noqa: BLE001 - reported, never re-raised
        return name, {"ok": False, "detail": type(exc).__name__}


@router.get("/ready")
async def readiness() -> JSONResponse:
    """Readiness: each check is independent and bounded to ~2s.

    The full result of every check (which can include the configured
    provider id and Codex credential-expiry state) is logged server-side
    only. The response body — reachable without a session — exposes nothing
    but a boolean per check plus the overall status. 200 when every check
    reports ``ok: true`` (a skipped check counts as ``ok``); 503 otherwise.
    """
    pairs = await asyncio.gather(
        _bounded("database", _check_database()),
        _bounded("provider_credentials", _check_provider_credentials()),
        _bounded("chrome", _check_chrome()),
        _bounded("nextjs", _check_nextjs()),
    )
    checks = dict(pairs)
    for name, detail in checks.items():
        (LOGGER.debug if detail.get("ok") else LOGGER.warning)(
            "readiness check %r: %s", name, detail
        )
    overall_ok = all(check.get("ok") is True for check in checks.values())
    return JSONResponse(
        {
            "status": "ok" if overall_ok else "degraded",
            "checks": {name: bool(check.get("ok")) for name, check in checks.items()},
        },
        status_code=200 if overall_ok else 503,
    )
