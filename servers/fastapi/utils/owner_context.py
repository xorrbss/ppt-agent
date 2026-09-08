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
# True only while api.middlewares dispatches an authenticated request.  Worker
# and maintenance tasks never set it, so their unscoped reads stay legal.
_REQUEST_SCOPE_ACTIVE: ContextVar[bool] = ContextVar(
    "presenton_request_scope_active", default=False
)

OWNER_CONTEXT_REQUIRED_CODE = "owner_context_required"


class OwnerContextRequiredError(RuntimeError):
    """An authenticated request reached an owned table without an identity."""

    def __init__(self) -> None:
        super().__init__(OWNER_CONTEXT_REQUIRED_CODE)


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


def get_request_scope_active() -> bool:
    return _REQUEST_SCOPE_ACTIVE.get()


def set_request_scope_active(active: bool) -> Token:
    return _REQUEST_SCOPE_ACTIVE.set(active)


def reset_request_scope_active(token: Token) -> None:
    _REQUEST_SCOPE_ACTIVE.reset(token)


def owner_context_enforced() -> bool:
    """Fail closed only inside an authenticated request with auth enabled.

    ``DISABLE_AUTH=true`` (local runs, focused harnesses) keeps the historical
    fail-open behaviour; so does every code path that never set the request
    flag, which is how workers and maintenance loops read across owners.
    """
    if not _REQUEST_SCOPE_ACTIVE.get():
        return False
    # Imported lazily: this module is a leaf that models import, while the
    # settings layer pulls in the registry and runtime-settings machinery.
    from utils.get_env import is_disable_auth_enabled

    return not is_disable_auth_enabled()


def require_owner_context() -> None:
    """Raise when an enforced request would otherwise run an unscoped query.

    The owner is the only identity that can stand in here.  ``api.middlewares``
    sets the tenant on every authenticated request from a non-optional field
    that falls back to ``"default"``, so accepting a tenant as a substitute
    made the guard unreachable; and the tenant criteria in
    ``services.database_owner_scope`` cover only the three public authority
    models, not the owner-scoped tables this guard protects.
    """
    if not owner_context_enforced():
        return
    if _CURRENT_OWNER_ID.get() is None:
        raise OwnerContextRequiredError()
