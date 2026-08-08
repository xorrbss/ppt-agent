from __future__ import annotations

import asyncio

import httpx

from api.main import app


FRONTEND_ORIGIN = "http://localhost:3000"
WORKSPACE_PROJECTED_HEADER = "x-chat-first-workspace-projected"


def test_chat_first_projection_header_is_exposed_to_browser_clients():
    async def scenario():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://backend.test",
        ) as client:
            preflight = await client.options(
                "/api/v1/ppt/presentation/generate/async",
                headers={
                    "Origin": FRONTEND_ORIGIN,
                    "Access-Control-Request-Method": "POST",
                    "Access-Control-Request-Headers": (
                        "content-type,idempotency-key"
                    ),
                },
            )
            assert preflight.status_code == 200
            assert preflight.headers["access-control-allow-origin"] == (
                FRONTEND_ORIGIN
            )
            assert preflight.headers["access-control-allow-credentials"] == "true"
            assert "POST" in preflight.headers["access-control-allow-methods"]

            # The auth prefix is intentionally public. A missing route still
            # traverses the production middleware stack and proves that actual
            # cross-origin responses expose the admission projection marker.
            actual = await client.get(
                "/api/v1/auth/cors-contract-missing",
                headers={"Origin": FRONTEND_ORIGIN},
            )
            exposed = {
                value.strip().lower()
                for value in actual.headers[
                    "access-control-expose-headers"
                ].split(",")
            }
            assert actual.headers["access-control-allow-origin"] == FRONTEND_ORIGIN
            assert WORKSPACE_PROJECTED_HEADER in exposed

    asyncio.run(scenario())
