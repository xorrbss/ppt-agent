"""Public admission and capture setup shared by authored HTTP recovery tests."""

from __future__ import annotations

import importlib
import io
import uuid
from pathlib import Path
from typing import MutableMapping

from fastapi import FastAPI
from PIL import Image
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker
from sqlmodel import SQLModel

from models.authored_header_contract import default_header_contract
from models.sql.public_admission import PublicPolicyProfile
from models.sql.user import User
from services.public_operation_admission import PUBLIC_DEFAULT_POLICY_VERSION
from utils.owner_context import (
    reset_current_owner_id,
    reset_current_tenant_id,
    reset_current_trace_id,
    reset_request_scope_active,
    set_current_owner_id,
    set_current_tenant_id,
    set_current_trace_id,
    set_request_scope_active,
)


OWNER_ID = uuid.UUID("3d41d60a-79b3-49df-88c4-62a315313b3d")


def checkpoint_environment(root: Path) -> dict[str, str]:
    return {
        "APP_DATA_DIRECTORY": str(root / "app-data"),
        "AUTHORED_CHECKPOINT_DIRECTORY": str(root / "checkpoints"),
        "AUTHORED_QUALITY_RECEIPT_DIRECTORY": str(root / "quality-receipts"),
        "DISABLE_IMAGE_GENERATION": "true",
        "AUTHORED_CONTENT_BRIEF_ENABLED": "false",
        "ENVIRONMENT": "local",
        "PUBLIC_ARTIFACT_STORE_BACKEND": "local-development",
        "PUBLIC_WORKER_OBJECT_STORE_ROOT": str((root / "object-store").resolve()),
        "PUBLIC_LOCAL_ARTIFACT_DOWNLOAD_SIGNING_KEY": "a1" * 32,
    }


def set_checkpoint_environment(env: MutableMapping[str, str], root: Path) -> None:
    env.update(checkpoint_environment(root))


async def create_checkpoint_schema(engine: AsyncEngine) -> None:
    # Production migrations load these modules; the small loopback app does not.
    for name in (
        "models.sql.authored_semantic_bundle",
        "models.sql.public_admission",
        "models.sql.public_artifact",
        "models.sql.public_principal_binding",
        "models.sql.provider_usage_receipt",
    ):
        importlib.import_module(name)
    async with engine.begin() as connection:
        await connection.exec_driver_sql("PRAGMA journal_mode=WAL")
        await connection.exec_driver_sql("PRAGMA busy_timeout=30000")
        await connection.run_sync(SQLModel.metadata.create_all)


async def seed_checkpoint_owner(maker: async_sessionmaker, owner_id: uuid.UUID = OWNER_ID) -> None:
    from tests.support.authored_worker_lane_harness import _policy_profile

    async with maker() as session:
        if await session.get(User, owner_id) is None:
            session.add(User(id=owner_id, username=f"checkpoint-{owner_id.hex}", hashed_password="test"))
        if await session.get(PublicPolicyProfile, PUBLIC_DEFAULT_POLICY_VERSION) is None:
            session.add(_policy_profile())
        await session.commit()


def install_checkpoint_principal(app: FastAPI, owner_id: uuid.UUID = OWNER_ID) -> None:
    @app.middleware("http")
    async def principal_context(request, call_next):
        owner_token = set_current_owner_id(owner_id)
        tenant_token = set_current_tenant_id("default")
        trace_token = set_current_trace_id(f"checkpoint-{owner_id.hex}")
        scope_token = set_request_scope_active(True)
        try:
            return await call_next(request)
        finally:
            reset_request_scope_active(scope_token)
            reset_current_trace_id(trace_token)
            reset_current_tenant_id(tenant_token)
            reset_current_owner_id(owner_token)


def semantic_slide(index: int) -> str:
    return (
        "<!DOCTYPE html><html><body>"
        f'<h1 data-qid="slide-{index}-title" data-semantic-role="title">Slide {index + 1}</h1>'
        f'<div data-qid="slide-{index}-body" data-semantic-role="body">Body {index + 1}</div>'
        "</body></html>"
    )


def slide_png(index: int) -> bytes:
    output = io.BytesIO()
    Image.new("RGB", (1280, 720), (35 + index * 20, 100, 180)).save(output, format="PNG")
    return output.getvalue()


async def semantic_capture(html: str, png: bytes, **kwargs):
    from tests.support.authored_worker_lane_capture import capture_for_lane

    return await capture_for_lane(html, png, **kwargs)


async def fixed_header_contract(*_args, **_kwargs):
    return default_header_contract()
