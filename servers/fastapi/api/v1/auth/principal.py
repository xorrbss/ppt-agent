from collections.abc import Mapping
from dataclasses import dataclass
import ipaddress
import json
import os
from pathlib import Path
import re
from typing import Any, Literal
from urllib.parse import urlsplit
import uuid

from fastapi import HTTPException, Request
import jwt
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.v1.auth.users import UsernameUserDatabase, UserManager, get_jwt_strategy
from models.sql.access_token import AccessToken, hash_access_token
from models.sql.public_principal_binding import (
    PublicPrincipalBinding,
    external_principal_binding_digest,
    external_principal_component_digest,
)
from models.sql.user import User
from api.v1.auth.config import SESSION_COOKIE_NAME
from utils.env_settings.public_service import get_public_process_identity_settings
from utils.get_env import get_environment_name


@dataclass(frozen=True, slots=True)
class PrincipalEnvelope:
    """Verified request identity; never constructed from command payload fields."""

    user_id: uuid.UUID
    username: str
    is_admin: bool
    method: Literal["jwt", "api_key", "oidc"]
    tenant_id: str
    trace_id: str
    subject: str
    issuer: str
    audience: str
    scopes: tuple[str, ...] = ()


class PrincipalBoundaryError(RuntimeError):
    """Content-free public auth failure safe to project at the HTTP boundary."""

    def __init__(self, code: str, *, status_code: int = 503) -> None:
        super().__init__(code)
        self.code = code
        self.status_code = status_code


_SAFE_PRINCIPAL_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}$")
_OIDC_ALLOWED_ALGORITHMS = frozenset({"RS256", "ES256", "EdDSA"})
_LOCAL_DEVELOPMENT_TENANT = "local-development"
_LOCAL_ISSUER = "presenton-local-session"
_PUBLIC_API_PREFIX = "/api/v2/"
_LOCAL_PUBLIC_SCOPES = (
    "artifacts:read",
    "operations:cancel",
    "operations:read",
    "presentations:write",
)


def _trusted_tenant_id() -> str:
    tenant_id = get_public_process_identity_settings().service_tenant_id
    if not _SAFE_PRINCIPAL_ID.fullmatch(tenant_id):
        raise RuntimeError("ppt_service_tenant_id_invalid")
    return tenant_id


def _is_public_api_request(request: Request) -> bool:
    return request.url.path.startswith(_PUBLIC_API_PREFIX)


def is_loopback_development_request(request: Request) -> bool:
    environment = get_environment_name("production")
    if environment not in {"development", "dev", "local", "test"}:
        return False
    client = request.client
    if client is None:
        return False
    try:
        peer_loopback = ipaddress.ip_address(client.host).is_loopback
    except ValueError:
        return False
    host = (request.url.hostname or "").strip().lower()
    try:
        host_loopback = ipaddress.ip_address(host).is_loopback
    except ValueError:
        host_loopback = host == "localhost"
    return peer_loopback and host_loopback


def local_development_tenant_id() -> str:
    return _LOCAL_DEVELOPMENT_TENANT


def _public_oidc_configuration() -> tuple[str, str, Path] | None:
    identity = get_public_process_identity_settings()
    issuer = identity.oidc_issuer
    audience = identity.oidc_audience
    jwks_path_value = identity.oidc_jwks_file
    supplied = (bool(issuer), bool(audience), bool(jwks_path_value))
    if not any(supplied):
        return None
    if not all(supplied):
        raise PrincipalBoundaryError("public_oidc_configuration_incomplete")
    parsed_issuer = urlsplit(issuer)
    if (
        parsed_issuer.scheme != "https"
        or not parsed_issuer.hostname
        or parsed_issuer.username is not None
        or parsed_issuer.password is not None
        or parsed_issuer.query
        or parsed_issuer.fragment
    ):
        raise PrincipalBoundaryError("public_oidc_issuer_invalid")
    jwks_path = Path(jwks_path_value)
    if not jwks_path.is_absolute() or not jwks_path.is_file():
        raise PrincipalBoundaryError("public_oidc_trust_bundle_unavailable")
    return issuer, audience, jwks_path


def require_public_issuer_boundary(request: Request) -> None:
    """Reject public-service auth unless an issuer or local loopback proves it."""

    if not _is_public_api_request(request) or is_loopback_development_request(request):
        return
    if _public_oidc_configuration() is None:
        raise PrincipalBoundaryError("public_oidc_issuer_unavailable")


def _read_oidc_jwks(path: Path) -> jwt.PyJWKSet:
    try:
        payload = path.read_bytes()
        if not payload or len(payload) > 1024 * 1024:
            raise ValueError
        document = json.loads(payload)
        if not isinstance(document, Mapping):
            raise ValueError
        return jwt.PyJWKSet.from_dict(dict(document))
    except (OSError, ValueError, json.JSONDecodeError, jwt.PyJWTError) as error:
        raise PrincipalBoundaryError("public_oidc_trust_bundle_invalid") from error


def _verified_oidc_claims(
    token: str,
    *,
    issuer: str,
    audience: str,
    jwks_path: Path,
) -> Mapping[str, Any]:
    if not token or len(token) > 16 * 1024:
        raise PrincipalBoundaryError("public_oidc_token_invalid", status_code=401)
    try:
        header = jwt.get_unverified_header(token)
        algorithm = header.get("alg")
        key_id = header.get("kid")
        if algorithm not in _OIDC_ALLOWED_ALGORITHMS or not isinstance(key_id, str):
            raise jwt.InvalidTokenError
        keys = [key for key in _read_oidc_jwks(jwks_path).keys if key.key_id == key_id]
        if len(keys) != 1 or keys[0].algorithm_name != algorithm:
            raise jwt.InvalidTokenError
        claims = jwt.decode(
            token,
            key=keys[0].key,
            algorithms=[algorithm],
            audience=audience,
            issuer=issuer,
            options={"require": ["exp", "iat", "iss", "sub", "aud", "tenant_id"]},
        )
        if not isinstance(claims, Mapping):
            raise jwt.InvalidTokenError
        return claims
    except PrincipalBoundaryError:
        raise
    except jwt.PyJWTError as error:
        raise PrincipalBoundaryError("public_oidc_token_invalid", status_code=401) from error


async def _resolve_oidc_principal(
    request: Request,
    session: AsyncSession,
    token: str,
) -> tuple[PrincipalEnvelope | None, User | None]:
    configuration = _public_oidc_configuration()
    if configuration is None:
        return None, None
    issuer, audience, jwks_path = configuration
    claims = _verified_oidc_claims(
        token,
        issuer=issuer,
        audience=audience,
        jwks_path=jwks_path,
    )
    subject = claims.get("sub")
    tenant_id = claims.get("tenant_id")
    if (
        not isinstance(subject, str)
        or not isinstance(tenant_id, str)
        or not _SAFE_PRINCIPAL_ID.fullmatch(tenant_id)
    ):
        raise PrincipalBoundaryError("public_oidc_principal_claims_invalid", status_code=401)
    # External issuer subjects are opaque identifiers, not internal UUIDs.
    # They become owner authority only through an explicit issuer+tenant
    # provisioning record.  This prevents cross-issuer subject collisions and
    # accidental just-in-time account creation from an untrusted claim.
    try:
        binding_digest = external_principal_binding_digest(
            issuer=issuer,
            tenant_id=tenant_id,
            subject=subject,
        )
    except ValueError as error:
        raise PrincipalBoundaryError(
            "public_oidc_principal_claims_invalid", status_code=401
        ) from error
    binding = await session.get(PublicPrincipalBinding, binding_digest)
    if (
        binding is None
        or binding.issuer_digest != external_principal_component_digest(issuer)
        or binding.tenant_id != tenant_id
        or binding.subject_digest != external_principal_component_digest(subject)
    ):
        raise PrincipalBoundaryError(
            "public_oidc_principal_not_provisioned", status_code=401
        )
    user = await session.get(User, binding.user_id)
    if user is None or not user.is_active:
        return None, None
    raw_scope = claims.get("scope", "")
    scopes = (
        tuple(sorted(set(raw_scope.split())))
        if isinstance(raw_scope, str)
        else ()
    )
    return (
        PrincipalEnvelope(
            user_id=user.id,
            username=user.username,
            is_admin=False,
            method="oidc",
            tenant_id=tenant_id,
            trace_id=_request_trace_id(request),
            subject=subject,
            issuer=issuer,
            audience=audience,
            scopes=scopes,
        ),
        user,
    )


def _request_trace_id(request: Request) -> str:
    trace_id = str(getattr(request.state, "request_id", "") or uuid.uuid4())
    if not _SAFE_PRINCIPAL_ID.fullmatch(trace_id):
        return str(uuid.uuid4())
    return trace_id


async def resolve_request_principal(
    request: Request, session: AsyncSession
) -> tuple[PrincipalEnvelope | None, User | None]:
    is_public_api = _is_public_api_request(request)
    authorization = request.headers.get("Authorization", "")
    if authorization.lower().startswith("bearer "):
        token = authorization.split(" ", 1)[1].strip()
        if is_public_api and not token.startswith("sk-presenton-"):
            return await _resolve_oidc_principal(request, session, token)

    public_local_development = is_public_api and is_loopback_development_request(request)
    if is_public_api and not public_local_development:
        # Production/remote public APIs accept only the external issuer above.
        return None, None

    tenant_id = (
        _LOCAL_DEVELOPMENT_TENANT if public_local_development else _trusted_tenant_id()
    )
    issuer = _LOCAL_ISSUER
    audience = "presenton-public-api" if is_public_api else "presenton-api"
    cookie_token = request.cookies.get(SESSION_COOKIE_NAME)
    if cookie_token:
        user_db = UsernameUserDatabase(session)
        user = await get_jwt_strategy().read_token(cookie_token, UserManager(user_db))
        if user:
            return (
                PrincipalEnvelope(
                    user_id=user.id,
                    username=user.username,
                    is_admin=user.is_superuser,
                    method="jwt",
                    tenant_id=tenant_id,
                    trace_id=_request_trace_id(request),
                    subject=str(user.id),
                    issuer=issuer,
                    audience=audience,
                    scopes=(_LOCAL_PUBLIC_SCOPES if is_public_api else ()),
                ),
                user,
            )

    if authorization.lower().startswith("bearer "):
        token = authorization.split(" ", 1)[1].strip()
        if not token.startswith("sk-presenton-"):
            return None, None
        try:
            token_hash = hash_access_token(token)
        except ValueError:
            return None, None
        access_token = await session.get(AccessToken, token_hash)
        if access_token is None:
            return None, None
        user = await session.get(User, access_token.user_id)
        if user is None or not user.is_active or not user.is_superuser:
            return None, None
        return (
            PrincipalEnvelope(
                user_id=user.id,
                username=user.username,
                is_admin=True,
                method="api_key",
                tenant_id=tenant_id,
                trace_id=_request_trace_id(request),
                subject=str(user.id),
                issuer=issuer,
                audience=audience,
                scopes=(_LOCAL_PUBLIC_SCOPES if is_public_api else ()),
            ),
            user,
        )

    return None, None


def principal_from_request(request: Request) -> PrincipalEnvelope:
    principal = getattr(request.state, "auth_principal", None)
    if principal is None:
        raise HTTPException(status_code=401, detail="Unauthorized")
    return principal
