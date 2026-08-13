"""Fail-closed Google Fonts availability transport."""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
from urllib.parse import quote_plus
from urllib.parse import urlsplit

import aiohttp

from services.image_generation_stock_adapter import pinned_connector
from services.provider_egress_policy import (
    EgressCategory,
    EgressPolicyError,
    ProviderEgressPolicy,
    validate_configured_provider_endpoint,
)


_FONT_EGRESS_ALLOWLIST_ENV = "PROVIDER_FONT_EGRESS_ALLOWLIST"
_MAX_FONT_RESPONSE_BYTES = 64 * 1024 * 1024


def _google_fonts_stylesheet_url(font_name: str) -> str:
    return f"https://fonts.googleapis.com/css2?family={quote_plus(font_name)}&display=swap"


async def check_google_font_availability(font_name: str) -> bool:
    """Check one font only after explicit operator admission and DNS pinning."""

    try:
        endpoint = validate_configured_provider_endpoint(
            _google_fonts_stylesheet_url(font_name),
            category=EgressCategory.FONT,
            service_id="google-fonts",
            allowlist_env=_FONT_EGRESS_ALLOWLIST_ENV,
        )
        async with aiohttp.ClientSession(
            trust_env=False,
            connector=pinned_connector(
                endpoint,
                connected_ip_validator=ProviderEgressPolicy.validate_connected_ip,
            ),
        ) as session:
            async with session.head(
                endpoint.url,
                timeout=aiohttp.ClientTimeout(total=10),
                allow_redirects=False,
            ) as response:
                ProviderEgressPolicy.reject_redirect(response.status)
                return response.status == 200
    except (EgressPolicyError, aiohttp.ClientError, OSError, TimeoutError):
        return False


async def fetch_google_fonts_stylesheet(url: str) -> str | None:
    """Return an admitted Google Fonts stylesheet with a bounded response."""

    try:
        endpoint = validate_configured_provider_endpoint(
            url,
            category=EgressCategory.FONT,
            service_id="google-fonts",
            allowlist_env=_FONT_EGRESS_ALLOWLIST_ENV,
        )
        async with aiohttp.ClientSession(
            trust_env=False,
            connector=pinned_connector(
                endpoint,
                connected_ip_validator=ProviderEgressPolicy.validate_connected_ip,
            ),
        ) as session:
            async with session.get(
                endpoint.url,
                timeout=aiohttp.ClientTimeout(total=10),
                allow_redirects=False,
            ) as response:
                ProviderEgressPolicy.reject_redirect(response.status)
                if response.status != 200:
                    return None
                content = await response.content.read(1024 * 1024 + 1)
                if len(content) > 1024 * 1024:
                    return None
                return content.decode(response.charset or "utf-8", errors="replace")
    except (EgressPolicyError, aiohttp.ClientError, OSError, TimeoutError):
        return None


async def get_google_font_file_urls(family_name: str, api_key: str) -> list[str]:
    """Resolve font binaries through the admitted Google Webfonts API."""

    try:
        endpoint = validate_configured_provider_endpoint(
            "https://www.googleapis.com/webfonts/v1/webfonts",
            category=EgressCategory.FONT,
            service_id="google-fonts-api",
            allowlist_env=_FONT_EGRESS_ALLOWLIST_ENV,
        )
        async with aiohttp.ClientSession(
            trust_env=False,
            connector=pinned_connector(
                endpoint,
                connected_ip_validator=ProviderEgressPolicy.validate_connected_ip,
            ),
        ) as session:
            async with session.get(
                endpoint.url,
                params={"family": family_name, "key": api_key},
                timeout=aiohttp.ClientTimeout(total=20),
                allow_redirects=False,
            ) as response:
                ProviderEgressPolicy.reject_redirect(response.status)
                if response.status != 200:
                    return []
                data = await response.json(content_type=None)
        items = data.get("items", []) if isinstance(data, dict) else []
        files = (items[0] or {}).get("files", {}) if items else {}
        urls = []
        for value in files.values():
            if not isinstance(value, str) or not value:
                continue
            fixed = value.replace("http://", "https://", 1)
            if fixed.lower().endswith((".ttf", ".otf")):
                urls.append(fixed)
        return urls
    except (EgressPolicyError, aiohttp.ClientError, OSError, TimeoutError, ValueError):
        return []


async def download_google_font_file(url: str, save_directory: str) -> str | None:
    """Download one admitted font binary without redirects or proxy inheritance."""

    try:
        endpoint = validate_configured_provider_endpoint(
            url,
            category=EgressCategory.FONT,
            service_id="google-fonts-file",
            allowlist_env=_FONT_EGRESS_ALLOWLIST_ENV,
        )
        filename = Path(urlsplit(endpoint.url).path).name
        if not filename or filename in {".", ".."}:
            return None
        os.makedirs(save_directory, exist_ok=True)
        save_path = os.path.join(save_directory, filename)
        async with aiohttp.ClientSession(
            trust_env=False,
            connector=pinned_connector(
                endpoint,
                connected_ip_validator=ProviderEgressPolicy.validate_connected_ip,
            ),
        ) as session:
            async with session.get(
                endpoint.url,
                timeout=aiohttp.ClientTimeout(total=30),
                allow_redirects=False,
            ) as response:
                ProviderEgressPolicy.reject_redirect(response.status)
                if response.status != 200:
                    return None
                declared = response.content_length
                if declared is not None and declared > _MAX_FONT_RESPONSE_BYTES:
                    return None
                content = await response.content.read(_MAX_FONT_RESPONSE_BYTES + 1)
                if len(content) > _MAX_FONT_RESPONSE_BYTES:
                    return None
                await asyncio.to_thread(Path(save_path).write_bytes, content)
        return save_path
    except (EgressPolicyError, aiohttp.ClientError, OSError, TimeoutError):
        return None


__all__ = [
    "check_google_font_availability",
    "download_google_font_file",
    "fetch_google_fonts_stylesheet",
    "get_google_font_file_urls",
]
