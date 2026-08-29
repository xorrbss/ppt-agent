"""Per-request owner context shared across all layers.

Canonical home of the owner contextvars. Lives in utils so models/services can
depend on it without importing the api layer (set by api.middlewares per request).
"""
from contextvars import ContextVar, Token
import uuid


_CURRENT_OWNER_ID: ContextVar[uuid.UUID | None] = ContextVar(
    "presenton_current_owner_id", default=None
)
_CURRENT_OWNER_IS_ADMIN: ContextVar[bool] = ContextVar(
    "presenton_current_owner_is_admin", default=False
)
_CURRENT_TENANT_ID: ContextVar[str | None] = ContextVar(
    "presenton_current_tenant_id", default=None
)
_CURRENT_TRACE_ID: ContextVar[str | None] = ContextVar(
    "presenton_current_trace_id", default=None
)


def get_current_owner_id() -> uuid.UUID | None:
    return _CURRENT_OWNER_ID.get()


def get_current_owner_is_admin() -> bool:
    return _CURRENT_OWNER_IS_ADMIN.get()


def get_current_tenant_id() -> str | None:
    return _CURRENT_TENANT_ID.get()


def get_current_trace_id() -> str | None:
    return _CURRENT_TRACE_ID.get()


def set_current_owner_id(owner_id: uuid.UUID | None) -> Token:
    return _CURRENT_OWNER_ID.set(owner_id)


def set_current_owner_is_admin(is_admin: bool) -> Token:
    return _CURRENT_OWNER_IS_ADMIN.set(is_admin)


def set_current_tenant_id(tenant_id: str | None) -> Token:
    return _CURRENT_TENANT_ID.set(tenant_id)


def set_current_trace_id(trace_id: str | None) -> Token:
    return _CURRENT_TRACE_ID.set(trace_id)


def reset_current_owner_id(token: Token) -> None:
    _CURRENT_OWNER_ID.reset(token)


def reset_current_owner_is_admin(token: Token) -> None:
    _CURRENT_OWNER_IS_ADMIN.reset(token)


def reset_current_tenant_id(token: Token) -> None:
    _CURRENT_TENANT_ID.reset(token)


def reset_current_trace_id(token: Token) -> None:
    _CURRENT_TRACE_ID.reset(token)
