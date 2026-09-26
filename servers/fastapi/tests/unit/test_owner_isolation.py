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
from models.sql.public_artifact import PublicArtifact
from services import database as _database_events  # noqa: F401


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
