import asyncio
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from utils.owner_context import (
    reset_current_owner_id,
    reset_current_tenant_id,
    set_current_owner_id,
    set_current_tenant_id,
)
from models.sql.presentation import PresentationModel, PresentationVersion
from models.sql.public_artifact import PublicArtifact
from models.sql.template_v2 import TemplateV2
from models.sql.user import User
from services import database as _database_events  # noqa: F401


def test_owned_queries_include_catalog_default_even_when_database_flag_is_false(
    monkeypatch,
):
    async def run():
        engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        session_maker = async_sessionmaker(engine, expire_on_commit=False)
        async with engine.begin() as connection:
            await connection.run_sync(User.__table__.create)
            await connection.run_sync(PresentationModel.__table__.create)
            await connection.run_sync(TemplateV2.__table__.create)

        first_id, second_id = uuid.uuid4(), uuid.uuid4()
        async with session_maker() as session:
            session.add_all(
                [
                    User(
                        id=first_id,
                        username="first",
                        hashed_password="unused",
                        is_active=True,
                        is_superuser=False,
                        is_verified=True,
                    ),
                    User(
                        id=second_id,
                        username="second",
                        hashed_password="unused",
                        is_active=True,
                        is_superuser=False,
                        is_verified=True,
                    ),
                    PresentationModel(
                        owner_id=first_id,
                        version=PresentationVersion.V2_STANDARD,
                        content="first",
                        n_slides=1,
                        language="English",
                    ),
                    PresentationModel(
                        owner_id=second_id,
                        version=PresentationVersion.V2_STANDARD,
                        content="second",
                        n_slides=1,
                        language="English",
                    ),
                    TemplateV2(
                        id="shared",
                        owner_id=None,
                        name="Shared",
                        is_default=True,
                    ),
                    TemplateV2(
                        id="unowned-private",
                        owner_id=None,
                        name="Unowned private",
                        is_default=False,
                    ),
                    TemplateV2(
                        id="catalog-default",
                        owner_id=None,
                        name="Catalog default",
                        is_default=False,
                    ),
                    TemplateV2(
                        id="first-private",
                        owner_id=first_id,
                        name="First private",
                        is_default=False,
                    ),
                    TemplateV2(
                        id="second-private",
                        owner_id=second_id,
                        name="Second private",
                        is_default=False,
                    ),
                ]
            )
            await session.commit()

        monkeypatch.setattr(
            _database_events,
            "get_template_v2_default_template_id",
            lambda: "catalog-default",
        )
        context_token = set_current_owner_id(first_id)
        try:
            async with session_maker() as session:
                presentations = (
                    await session.scalars(select(PresentationModel))
                ).all()
                templates = (await session.scalars(select(TemplateV2))).all()
        finally:
            reset_current_owner_id(context_token)
            await engine.dispose()

        assert [item.content for item in presentations] == ["first"]
        assert {item.id for item in templates} == {
            "shared",
            "catalog-default",
            "first-private",
        }

    asyncio.run(run())


def test_public_authority_queries_keep_tenant_scope_when_owner_scope_is_skipped():
    async def run():
        engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        session_maker = async_sessionmaker(engine, expire_on_commit=False)
        async with engine.begin() as connection:
            await connection.run_sync(PublicArtifact.__table__.create)

        owner_id = uuid.uuid4()
        operation_id = uuid.uuid4()
        tenant_b_artifact_id = uuid.uuid4()
        async with session_maker() as session:
            session.add_all(
                [
                    PublicArtifact(
                        id=(tenant_b_artifact_id if tenant_id == "tenant-b" else uuid.uuid4()),
                        owner_id=owner_id,
                        tenant_id=tenant_id,
                        kind="checkpoint",
                        sha256=digest,
                        size_bytes=1,
                        media_type="application/octet-stream",
                        storage_key=f"tenant/{tenant_id}/{digest}",
                        created_by_operation_id=operation_id,
                        retention_class="checkpoint",
                    )
                    for tenant_id, digest in (
                        ("tenant-a", "a" * 64),
                        ("tenant-b", "b" * 64),
                    )
                ]
            )
            await session.commit()

        owner_token = set_current_owner_id(owner_id)
        tenant_token = set_current_tenant_id("tenant-a")
        try:
            async with session_maker() as session:
                visible = (
                    await session.scalars(
                        select(PublicArtifact).execution_options(skip_owner_scope=True)
                    )
                ).all()
                hidden_by_primary_key = await session.get(
                    PublicArtifact, tenant_b_artifact_id
                )
        finally:
            reset_current_tenant_id(tenant_token)
            reset_current_owner_id(owner_token)
            await engine.dispose()

        assert [item.tenant_id for item in visible] == ["tenant-a"]
        assert hidden_by_primary_key is None

    asyncio.run(run())
