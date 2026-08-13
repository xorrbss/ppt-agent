"""Pinned-network adapters for public stock-image providers."""

from __future__ import annotations

import socket
from collections.abc import Callable
from typing import Any

import aiohttp
from fastapi import HTTPException

from services.provider_egress_policy import ValidatedEndpoint


class PinnedAiohttpResolver(aiohttp.abc.AbstractResolver):
    """Return only the public addresses approved by the egress admission step."""

    def __init__(self, endpoint: ValidatedEndpoint) -> None:
        self._endpoint = endpoint

    async def resolve(
        self,
        host: str,
        port: int = 0,
        family: int = socket.AF_UNSPEC,
    ) -> list[dict[str, object]]:
        if host.lower().rstrip(".") != self._endpoint.hostname or port not in {
            0,
            self._endpoint.port,
        }:
            raise OSError("egress_dns_pin_mismatch")
        results: list[dict[str, object]] = []
        for address in sorted(self._endpoint.resolved_ips):
            resolved_family = socket.AF_INET6 if ":" in address else socket.AF_INET
            if family not in {socket.AF_UNSPEC, resolved_family}:
                continue
            results.append(
                {
                    "hostname": host,
                    "host": address,
                    "port": self._endpoint.port,
                    "family": resolved_family,
                    "proto": socket.IPPROTO_TCP,
                    "flags": 0,
                }
            )
        if not results:
            raise OSError("egress_dns_pin_unavailable")
        return results

    async def close(self) -> None:
        return None


def pinned_connector(
    endpoint: ValidatedEndpoint,
    *,
    connected_ip_validator: Callable[[ValidatedEndpoint, str], None],
) -> aiohttp.TCPConnector:
    class ValidatingResolver(PinnedAiohttpResolver):
        async def resolve(
            self,
            host: str,
            port: int = 0,
            family: int = socket.AF_UNSPEC,
        ) -> list[dict[str, object]]:
            results = await super().resolve(host, port, family)
            for result in results:
                connected_ip_validator(endpoint, str(result["host"]))
            return results

    return aiohttp.TCPConnector(
        resolver=ValidatingResolver(endpoint),
        use_dns_cache=True,
        ttl_dns_cache=None,
    )


async def fetch_pexels_images(
    prompt: str,
    api_key: str,
    limit: int,
    *,
    endpoint_validator: Callable[..., ValidatedEndpoint],
    connected_ip_validator: Callable[[ValidatedEndpoint, str], None],
    redirect_rejector: Callable[[int], None],
    category: Any,
) -> str | list[str]:
    per_page = max(1, min(limit, 80))
    endpoint = endpoint_validator(
        "https://api.pexels.com/v1/search",
        category=category,
        service_id="pexels",
        allowed_hostnames=("api.pexels.com",),
    )
    async with aiohttp.ClientSession(
        trust_env=False,
        connector=pinned_connector(
            endpoint,
            connected_ip_validator=connected_ip_validator,
        ),
    ) as session:
        response = await session.get(
            endpoint.url,
            params={"query": prompt, "per_page": per_page},
            headers={"Authorization": api_key} if api_key else {},
            timeout=aiohttp.ClientTimeout(total=20),
            allow_redirects=False,
        )
        redirect_rejector(response.status)
        if response.status in {401, 403}:
            raise HTTPException(status_code=401, detail="Invalid Pexels API key")
        if response.status != 200:
            raise HTTPException(status_code=502, detail="Pexels request failed")
        data = await response.json()
    image_urls = [
        photo.get("src", {}).get("large")
        for photo in data.get("photos", [])
        if photo.get("src", {}).get("large")
    ]
    return (image_urls[0] if image_urls else "") if limit <= 1 else image_urls[:limit]


async def fetch_pixabay_images(
    prompt: str,
    api_key: str,
    limit: int,
    *,
    endpoint_validator: Callable[..., ValidatedEndpoint],
    connected_ip_validator: Callable[[ValidatedEndpoint, str], None],
    redirect_rejector: Callable[[int], None],
    category: Any,
) -> str | list[str]:
    per_page = max(3, min(limit, 200))
    endpoint = endpoint_validator(
        "https://pixabay.com/api/",
        category=category,
        service_id="pixabay",
        allowed_hostnames=("pixabay.com",),
    )
    async with aiohttp.ClientSession(
        trust_env=False,
        connector=pinned_connector(
            endpoint,
            connected_ip_validator=connected_ip_validator,
        ),
    ) as session:
        response = await session.get(
            endpoint.url,
            params={
                "key": api_key,
                "q": prompt[:99],
                "image_type": "photo",
                "per_page": per_page,
            },
            timeout=aiohttp.ClientTimeout(total=20),
            allow_redirects=False,
        )
        redirect_rejector(response.status)
        if response.status in {401, 403}:
            raise HTTPException(status_code=401, detail="Invalid Pixabay API key")
        if response.status == 400:
            raise HTTPException(status_code=400, detail="Pixabay request invalid")
        if response.status != 200:
            raise HTTPException(status_code=502, detail="Pixabay request failed")
        data = await response.json()
    image_urls = [
        hit.get("largeImageURL")
        for hit in data.get("hits", [])
        if hit.get("largeImageURL")
    ]
    return (image_urls[0] if image_urls else "") if limit <= 1 else image_urls[:limit]


__all__ = ["fetch_pexels_images", "fetch_pixabay_images"]
