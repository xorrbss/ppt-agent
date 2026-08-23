from __future__ import annotations

import asyncio
import uuid

import pytest
from fastapi import HTTPException

from api.v1.ppt.endpoints.authored_vnext.semantic import router
from models.authored_vnext.semantic.release_profile_v3 import (
    WorkbenchCapabilityProjectionV3,
)
from services.authored_vnext.semantic.canonical import SemanticContractError


class _Session:
    def __init__(self) -> None:
        self.commits = 0
        self.rollbacks = 0

    async def commit(self) -> None:
        self.commits += 1

    async def rollback(self) -> None:
        self.rollbacks += 1


def test_v3_routes_delegate_with_owner_authority_and_commit(monkeypatch) -> None:
    presentation_id = uuid.uuid4()
    owner_id = uuid.uuid4()
    request = object()
    projection = object()
    receipt = object()
    session = _Session()
    calls: dict[str, object] = {}

    async def project(passed_session, **kwargs):
        calls["projection"] = (passed_session, kwargs)
        return projection

    async def issue(passed_session, **kwargs):
        calls["issue"] = (passed_session, kwargs)
        return receipt

    monkeypatch.setattr(router, "_owner", lambda: owner_id)
    monkeypatch.setattr(router, "_admin_owner", lambda: owner_id)
    monkeypatch.setattr(router, "get_workbench_capability_projection_v3", project)
    monkeypatch.setattr(router, "issue_capability_receipt_v3", issue)
    monkeypatch.setenv("AUTHORED_VNEXT_TENANT_ID", "tenant-router-v3")

    assert (
        asyncio.run(
            router.read_workbench_capability_projection_v3(
                presentation_id=presentation_id,
                session=session,
            )
        )
        is projection
    )
    assert (
        asyncio.run(router.create_capability_receipt_v3(request=request, session=session))
        is receipt
    )

    assert calls["projection"] == (
        session,
        {
            "presentation_id": presentation_id,
            "owner_id": owner_id,
            "tenant_id": "tenant-router-v3",
        },
    )
    assert calls["issue"] == (
        session,
        {
            "request": request,
            "issuer_principal_id": owner_id,
            "issuer_is_admin": True,
            "tenant_id": "tenant-router-v3",
        },
    )
    assert session.commits == 1
    assert session.rollbacks == 0

    routes = {route.path: route for route in router.AUTHORED_SEMANTIC_ROUTER.routes}
    assert routes[
        "/presentation/{presentation_id}/authored-emission/capability-projection-v3"
    ].response_model is WorkbenchCapabilityProjectionV3
    assert "/authored-emission/capability-receipts-v3" in routes
    assert (
        "/presentation/{presentation_id}/authored-emission/capability-projection-v2"
        in routes
    )
    assert "/authored-emission/capability-receipts-v2" in routes
    assert (
        "/presentation/{presentation_id}/authored-emission/capability-projection"
        in routes
    )


def test_v3_receipt_error_rolls_back_with_stable_code(monkeypatch) -> None:
    session = _Session()

    async def reject(*args, **kwargs):
        raise SemanticContractError("authored_capability_receipt_scope_mismatch")

    monkeypatch.setattr(router, "_admin_owner", uuid.uuid4)
    monkeypatch.setattr(router, "issue_capability_receipt_v3", reject)

    with pytest.raises(HTTPException) as caught:
        asyncio.run(router.create_capability_receipt_v3(request=object(), session=session))

    assert caught.value.status_code == 422
    assert caught.value.detail == {
        "code": "authored_capability_receipt_scope_mismatch"
    }
    assert session.commits == 0
    assert session.rollbacks == 1
