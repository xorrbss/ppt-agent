import asyncio
import base64
from datetime import datetime, timedelta, timezone
import json
from types import SimpleNamespace
import uuid

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
import jwt
from starlette.requests import Request

from api.v1.auth.principal import (
    PrincipalBoundaryError,
    require_public_issuer_boundary,
    resolve_request_principal,
)
from models.sql.public_principal_binding import (
    PublicPrincipalBinding,
    build_public_principal_binding,
)
from models.sql.user import User


def _request(*, host: str, peer: str, authorization: str | None = None) -> Request:
    headers = [(b"host", host.encode("ascii"))]
    if authorization is not None:
        headers.append((b"authorization", authorization.encode("ascii")))
    return Request(
        {
            "type": "http",
            "method": "GET",
            "scheme": "https",
            "path": "/api/v2/operations/op_1",
            "headers": headers,
            "client": (peer, 51000),
            "server": (host, 443),
        }
    )


def test_public_auto_access_is_only_local_environment_peer_and_host(monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "production")
    with_issuer_missing = _request(host="localhost", peer="127.0.0.1")
    try:
        require_public_issuer_boundary(with_issuer_missing)
    except PrincipalBoundaryError as error:
        assert error.code == "public_oidc_issuer_unavailable"
    else:
        raise AssertionError("production must not trust a loopback peer without issuer")

    monkeypatch.setenv("ENVIRONMENT", "local")
    require_public_issuer_boundary(_request(host="localhost", peer="127.0.0.1"))
    try:
        require_public_issuer_boundary(
            _request(host="public.example.test", peer="127.0.0.1")
        )
    except PrincipalBoundaryError as error:
        assert error.code == "public_oidc_issuer_unavailable"
    else:
        raise AssertionError("loopback reverse proxy must not enable remote auto access")


def test_oidc_principal_requires_signed_issuer_audience_tenant_and_scope(
    monkeypatch, tmp_path
):
    private_key = Ed25519PrivateKey.generate()
    public_bytes = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    encoded_key = base64.urlsafe_b64encode(public_bytes).rstrip(b"=").decode("ascii")
    jwks_path = tmp_path / "issuer-jwks.json"
    jwks_path.write_text(
        json.dumps(
            {
                "keys": [
                    {
                        "kty": "OKP",
                        "crv": "Ed25519",
                        "x": encoded_key,
                        "kid": "issuer-key-1",
                        "alg": "EdDSA",
                        "use": "sig",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    issuer = "https://issuer.example.test"
    audience = "presenton-public-api"
    owner_id = uuid.uuid4()
    opaque_subject = "issuer-account:region-a:user-42"
    now = datetime.now(timezone.utc)
    token = jwt.encode(
        {
            "iss": issuer,
            "aud": audience,
            "sub": opaque_subject,
            "tenant_id": "tenant-a",
            "scope": "operations:read artifacts:read",
            "iat": now,
            "exp": now + timedelta(minutes=5),
        },
        private_key,
        algorithm="EdDSA",
        headers={"kid": "issuer-key-1"},
    )
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("PPT_PUBLIC_OIDC_ISSUER", issuer)
    monkeypatch.setenv("PPT_PUBLIC_OIDC_AUDIENCE", audience)
    monkeypatch.setenv("PPT_PUBLIC_OIDC_JWKS_FILE", str(jwks_path))

    user = SimpleNamespace(
        id=owner_id,
        username="external-owner",
        is_active=True,
    )
    binding = build_public_principal_binding(
        issuer=issuer,
        tenant_id="tenant-a",
        subject=opaque_subject,
        user_id=owner_id,
    )

    class Session:
        async def get(self, model, key):
            if model is PublicPrincipalBinding and key == binding.binding_digest:
                return binding
            if model is User and key == owner_id:
                return user
            return None

    request = _request(
        host="public.example.test",
        peer="198.51.100.20",
        authorization=f"Bearer {token}",
    )
    principal, resolved_user = asyncio.run(resolve_request_principal(request, Session()))
    assert resolved_user is user
    assert principal is not None
    assert principal.user_id == owner_id
    assert principal.tenant_id == "tenant-a"
    assert principal.issuer == issuer
    assert principal.audience == audience
    assert principal.scopes == ("artifacts:read", "operations:read")
    assert principal.subject == opaque_subject


def test_oidc_principal_fails_closed_without_explicit_provisioning(
    monkeypatch, tmp_path
):
    private_key = Ed25519PrivateKey.generate()
    public_bytes = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    encoded_key = base64.urlsafe_b64encode(public_bytes).rstrip(b"=").decode("ascii")
    jwks_path = tmp_path / "issuer-jwks.json"
    jwks_path.write_text(
        json.dumps(
            {
                "keys": [
                    {
                        "kty": "OKP",
                        "crv": "Ed25519",
                        "x": encoded_key,
                        "kid": "issuer-key-1",
                        "alg": "EdDSA",
                        "use": "sig",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    issuer = "https://issuer.example.test"
    audience = "presenton-public-api"
    now = datetime.now(timezone.utc)
    token = jwt.encode(
        {
            "iss": issuer,
            "aud": audience,
            "sub": "opaque-unprovisioned-subject",
            "tenant_id": "tenant-a",
            "scope": "operations:read",
            "iat": now,
            "exp": now + timedelta(minutes=5),
        },
        private_key,
        algorithm="EdDSA",
        headers={"kid": "issuer-key-1"},
    )
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("PPT_PUBLIC_OIDC_ISSUER", issuer)
    monkeypatch.setenv("PPT_PUBLIC_OIDC_AUDIENCE", audience)
    monkeypatch.setenv("PPT_PUBLIC_OIDC_JWKS_FILE", str(jwks_path))

    class Session:
        async def get(self, _model, _key):
            return None

    request = _request(
        host="public.example.test",
        peer="198.51.100.20",
        authorization=f"Bearer {token}",
    )
    try:
        asyncio.run(resolve_request_principal(request, Session()))
    except PrincipalBoundaryError as error:
        assert error.code == "public_oidc_principal_not_provisioned"
        assert error.status_code == 401
    else:
        raise AssertionError("unprovisioned external subject must fail closed")
