from dataclasses import dataclass
import os
import re
from typing import Literal
import uuid

from fastapi import HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.v1.auth.users import UsernameUserDatabase, UserManager, get_jwt_strategy
from models.sql.access_token import AccessToken
from models.sql.user import User
from api.v1.auth.config import SESSION_COOKIE_NAME


@dataclass(frozen=True)
class AuthPrincipal:
    user_id: uuid.UUID
    username: str
    is_admin: bool
    method: Literal["jwt", "api_key"]
    tenant_id: str
    trace_id: str


_SAFE_PRINCIPAL_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}$")


def _trusted_tenant_id() -> str:
    tenant_id = (os.getenv("PPT_SERVICE_TENANT_ID") or "default").strip()
    if not _SAFE_PRINCIPAL_ID.fullmatch(tenant_id):
        raise RuntimeError("ppt_service_tenant_id_invalid")
    return tenant_id


def _request_trace_id(request: Request) -> str:
    trace_id = str(getattr(request.state, "request_id", "") or uuid.uuid4())
    if not _SAFE_PRINCIPAL_ID.fullmatch(trace_id):
        return str(uuid.uuid4())
    return trace_id


async def resolve_request_principal(
    request: Request, session: AsyncSession
) -> tuple[AuthPrincipal | None, User | None]:
    cookie_token = request.cookies.get(SESSION_COOKIE_NAME)
    if cookie_token:
        user_db = UsernameUserDatabase(session)
        user = await get_jwt_strategy().read_token(cookie_token, UserManager(user_db))
        if user:
            return (
                AuthPrincipal(
                    user_id=user.id,
                    username=user.username,
                    is_admin=user.is_superuser,
                    method="jwt",
                    tenant_id=_trusted_tenant_id(),
                    trace_id=_request_trace_id(request),
                ),
                user,
            )

    authorization = request.headers.get("Authorization", "")
    if authorization.lower().startswith("bearer "):
        token = authorization.split(" ", 1)[1].strip()
        if not token.startswith("sk-presenton-"):
            return None, None
        access_token = await session.get(AccessToken, token)
        if access_token is None:
            return None, None
        user = await session.get(User, access_token.user_id)
        if user is None or not user.is_active or not user.is_superuser:
            return None, None
        return (
            AuthPrincipal(
                user_id=user.id,
                username=user.username,
                is_admin=True,
                method="api_key",
                tenant_id=_trusted_tenant_id(),
                trace_id=_request_trace_id(request),
            ),
            user,
        )

    return None, None


def principal_from_request(request: Request) -> AuthPrincipal:
    principal = getattr(request.state, "auth_principal", None)
    if principal is None:
        raise HTTPException(status_code=401, detail="Unauthorized")
    return principal


def require_browser_admin_principal(request: Request) -> AuthPrincipal:
    principal = principal_from_request(request)
    if principal.method != "jwt" or not principal.is_admin:
        raise HTTPException(status_code=403, detail="Admin browser session required")
    return principal
