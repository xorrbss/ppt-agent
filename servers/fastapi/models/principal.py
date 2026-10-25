"""Dependency-neutral request identity and local-development boundary helpers."""

from dataclasses import dataclass
import ipaddress
from typing import Literal
import uuid

from fastapi import Request

from utils.get_env import get_environment_name


_LOCAL_DEVELOPMENT_TENANT = "local-development"


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


