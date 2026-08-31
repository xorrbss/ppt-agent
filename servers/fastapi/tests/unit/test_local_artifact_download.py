from __future__ import annotations

import asyncio
import base64
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import io
import json
from pathlib import Path
from urllib.parse import parse_qs, urlsplit
import uuid

import pytest
from fastapi import FastAPI, HTTPException
import httpx
from starlette.requests import Request

from api.middlewares import SessionAuthMiddleware
from api.v1.auth.principal import PrincipalEnvelope
from api.v2.router import API_V2_PUBLIC_ROUTER, download_local_development_artifact
from models.sql.public_artifact import ArtifactRef, artifact_storage_key
from services.artifact_store import (
    configured_local_development_artifact_store,
)


def _artifact(payload: bytes) -> ArtifactRef:
    owner_id = uuid.uuid4()
    digest = sha256(payload).hexdigest()
    return ArtifactRef(
        artifact_id=uuid.uuid4(),
        owner_id=owner_id,
        tenant_id="local-development",
        kind="presentation-pptx",
        sha256=digest,
        size_bytes=len(payload),
        media_type="application/vnd.openxmlformats-officedocument.presentationml.presentation",
        storage_key=artifact_storage_key(
            owner_id=owner_id,
            tenant_id="local-development",
            digest=digest,
        ),
        created_by_operation_id=uuid.uuid4(),
        retention_class="published",
    )


def _response_body(response) -> bytes:
    async def collect() -> bytes:
        return b"".join([chunk async for chunk in response.body_iterator])

    return asyncio.run(collect())


def test_local_signed_download_is_loopback_bound_streamed_and_range_capable(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("ENVIRONMENT", "local")
    monkeypatch.setenv("PUBLIC_ARTIFACT_STORE_BACKEND", "local-development")
    monkeypatch.setenv("PUBLIC_WORKER_OBJECT_STORE_ROOT", str(tmp_path / "objects"))
    monkeypatch.setenv("PUBLIC_LOCAL_ARTIFACT_DOWNLOAD_SIGNING_KEY", "k" * 32)
    monkeypatch.setenv("PUBLIC_LOCAL_ARTIFACT_DOWNLOAD_ORIGIN", "http://127.0.0.1:8010")
    payload = b"0123456789abcdef"
    reference = _artifact(payload)
    store = configured_local_development_artifact_store()
    asyncio.run(store.put_stream(reference, io.BytesIO(payload)))
    grant = asyncio.run(store.create_signed_download(reference, expires_seconds=120))
    parsed = urlsplit(grant.url)
    assert parsed.hostname == "127.0.0.1"
    token = parse_qs(parsed.query)["token"][0]

    full = asyncio.run(
        download_local_development_artifact(
            str(reference.artifact_id), token=token, range_header=None
        )
    )
    assert full.status_code == 200
    assert full.headers["content-length"] == str(len(payload))
    assert _response_body(full) == payload

    ranged = asyncio.run(
        download_local_development_artifact(
            str(reference.artifact_id), token=token, range_header="bytes=2-7"
        )
    )
    assert ranged.status_code == 206
    assert ranged.headers["content-range"] == "bytes 2-7/16"
    assert _response_body(ranged) == payload[2:8]

    # Exercise the real middleware -> public router -> cryptographic local
    # store boundary. Only session lookup and the SQL grant authority are
    # fixtures; no grant/session is injected into the redemption requests.
    principal = PrincipalEnvelope(
        user_id=reference.owner_id, username="local-download-fixture",
        is_admin=False, method="jwt", tenant_id=reference.tenant_id,
        trace_id="download-fixture", subject=str(reference.owner_id),
        issuer="presenton-local-session", audience="presenton-public-api",
        scopes=("artifacts:read",),
    )

    class Session:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            pass

        async def scalar(self, _statement):
            return 1

    async def resolve_principal(request, _session):
        if request.cookies.get("fixture-session") == "owner":
            return principal, None
        if request.cookies.get("fixture-session") == "other":
            return replace(principal, user_id=uuid.uuid4()), None
        return None, None

    class GrantAuthority:
        async def authorize_download(self, *, actor, artifact_id):
            if (actor.owner_id, actor.tenant_id, artifact_id) != (
                reference.owner_id, reference.tenant_id, str(reference.artifact_id),
            ):
                raise HTTPException(status_code=404)
            return await store.create_signed_download(reference, expires_seconds=120)

    monkeypatch.setattr("api.middlewares.is_disable_auth_enabled", lambda: False)
    monkeypatch.setattr("api.middlewares.async_session_maker", Session)
    monkeypatch.setattr("api.middlewares.resolve_request_principal", resolve_principal)
    for name in ("PPT_PUBLIC_OIDC_ISSUER", "PPT_PUBLIC_OIDC_AUDIENCE", "PPT_PUBLIC_OIDC_JWKS_FILE"):
        monkeypatch.delenv(name, raising=False)
    app = FastAPI()
    app.state.public_v2_orchestrator = GrantAuthority()
    app.include_router(API_V2_PUBLIC_ROUTER)
    app.add_middleware(SessionAuthMiddleware)

    async def http_boundary() -> None:
        transport = httpx.ASGITransport(app=app, client=("127.0.0.1", 50123))
        async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1:8010") as client:
            issuance_path = f"/api/v2/artifacts/{reference.artifact_id}/download"
            assert (await client.get(issuance_path)).status_code == 401
            assert (await client.get(issuance_path, headers={
                "Cookie": "fixture-session=other",
            })).status_code == 404
            issued = await client.get(issuance_path, headers={
                "Cookie": "fixture-session=owner",
            })
            assert issued.status_code == 303
            signed_url = issued.headers["location"]
            assert not client.cookies
            http_range = await client.get(signed_url, headers={"Range": "bytes=2-7"})
            assert http_range.status_code == 206 and http_range.content == payload[2:8]
            assert http_range.headers["content-range"] == "bytes 2-7/16"
            http_full = await client.get(signed_url)
            assert http_full.status_code == 200
            assert sha256(http_full.content).hexdigest() == reference.sha256
            assert http_full.headers["cache-control"] == "no-store"

            redemption_path = parsed.path
            expired = store._issue_download_token(
                reference, expires_at=datetime.now(timezone.utc) - timedelta(seconds=1),
            )
            encoded_payload, encoded_signature = token.split(".", 1)
            decoded = json.loads(base64.urlsafe_b64decode(
                encoded_payload + "=" * (-len(encoded_payload) % 4),
            ))
            decoded["artifact"]["ownerId"] = str(uuid.uuid4())
            changed_owner = store._token_part(json.dumps(decoded).encode()) + "." + encoded_signature
            invalid_signature = encoded_payload + "." + store._token_part(b"x" * 32)
            for rejected in ("not-a-token", expired, invalid_signature, changed_owner):
                response = await client.get(redemption_path, params={"token": rejected})
                assert response.status_code == 404
                assert payload not in response.content
            mismatched_path = f"/api/v2/artifacts/{uuid.uuid4()}/local-download"
            assert (await client.get(mismatched_path, params={"token": token})).status_code == 404
            assert (await client.get(redemption_path)).status_code == 422
            assert (await client.get(signed_url, headers={"Range": "bytes=99-"})).status_code == 416
            for method in ("POST", "PUT", "DELETE", "HEAD"):
                assert (await client.request(method, signed_url)).status_code == 401
            for other_path in (
                redemption_path + "/extra", redemption_path + "/",
                "/api/v2/artifacts/not-a-uuid/local-download",
                "/api/v2/operations/fixture-operation",
            ):
                assert (await client.get(other_path, params={"token": token})).status_code == 401
            # Issuer boundary remains first, even with a valid signed token.
            assert (await client.get(signed_url, headers={"Host": "remote.example"})).status_code == 503
            monkeypatch.setenv("PUBLIC_ARTIFACT_STORE_BACKEND", "s3")
            assert (await client.get(signed_url)).status_code == 401
            monkeypatch.setenv("PUBLIC_ARTIFACT_STORE_BACKEND", "local-development")
            monkeypatch.setenv("ENVIRONMENT", "production")
            assert (await client.get(signed_url)).status_code == 503
            monkeypatch.setenv("ENVIRONMENT", "local")
        remote_transport = httpx.ASGITransport(app=app, client=("203.0.113.9", 50123))
        async with httpx.AsyncClient(transport=remote_transport) as remote:
            assert (await remote.get(grant.url)).status_code == 503

    asyncio.run(http_boundary())
    middleware = SessionAuthMiddleware(app=None)
    traversal = f"/api/v2/artifacts/{reference.artifact_id}/../{reference.artifact_id}/local-download"
    request = Request({
        "type": "http", "method": "GET", "path": traversal,
        "headers": [(b"host", b"127.0.0.1")], "client": ("127.0.0.1", 123),
        "scheme": "http", "query_string": b"",
    })
    assert not middleware._delegates_local_download_grant(request, middleware._normalize(traversal))

    monkeypatch.setenv("ENVIRONMENT", "production")
    with pytest.raises(RuntimeError, match="local_download_development_only"):
        configured_local_development_artifact_store()
