from __future__ import annotations

import asyncio
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy import event, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlmodel import SQLModel

from api.v1.ppt.endpoints import presentation_generate as endpoint
from models.sql.image_asset import ImageAsset
from models.sql.presentation import (
    PRESENTATION_LIFECYCLE_PUBLISHED,
    PRESENTATION_LIFECYCLE_STAGING,
    PresentationModel,
)
from models.sql.presentation_generation_job import PresentationGenerationJob
from models.sql.slide import SlideModel
from models.sql.user import User
from services.workspace_artifact_publication_service import (
    WorkspaceArtifactPublicationError,
)


TABLES = [
    User.__table__,
    PresentationModel.__table__,
    SlideModel.__table__,
    ImageAsset.__table__,
]


async def _database(path: Path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{path}")

    @event.listens_for(engine.sync_engine, "connect")
    def _sqlite_safety(connection, _record):
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA busy_timeout=5000")

    async with engine.begin() as connection:
        await connection.run_sync(
            lambda sync_connection: SQLModel.metadata.create_all(
                sync_connection,
                tables=TABLES,
            )
        )
    return engine, async_sessionmaker(engine, expire_on_commit=False)


async def _seed_existing_deck(maker):
    target_id = uuid.uuid4()
    render_id = uuid.uuid4()
    share_token = "existing-share-token"
    async with maker() as session, session.begin():
        session.add_all(
            [
                PresentationModel(
                    id=target_id,
                    content="old content",
                    n_slides=1,
                    language="en",
                    title="Old published title",
                    mode="adaptive",
                    lifecycle_status=PRESENTATION_LIFECYCLE_PUBLISHED,
                    revision=7,
                    share_token=share_token,
                ),
                PresentationModel(
                    id=render_id,
                    content="new content",
                    n_slides=1,
                    language="ko",
                    title="New staged title",
                    mode="template",
                    lifecycle_status=PRESENTATION_LIFECYCLE_STAGING,
                    revision=0,
                ),
                SlideModel(
                    presentation=target_id,
                    layout_group="old",
                    layout="old-layout",
                    index=0,
                    content={"title": "old slide"},
                ),
                SlideModel(
                    presentation=render_id,
                    layout_group="new",
                    layout="new-layout",
                    index=0,
                    content={"title": "new slide"},
                ),
            ]
        )
    return target_id, render_id, share_token


async def _assert_old_deck(maker, target_id, share_token):
    async with maker() as session:
        target = await session.get(PresentationModel, target_id)
        slides = (
            await session.execute(
                select(SlideModel).where(SlideModel.presentation == target_id)
            )
        ).scalars().all()
        assert target is not None
        assert target.title == "Old published title"
        assert target.content == "old content"
        assert target.lifecycle_status == PRESENTATION_LIFECYCLE_PUBLISHED
        assert target.revision == 7
        assert target.share_token == share_token
        assert [slide.content for slide in slides] == [{"title": "old slide"}]


def test_export_failure_discards_only_isolated_render_snapshot(tmp_path: Path):
    async def scenario():
        engine, maker = await _database(tmp_path / "export-failure.db")
        try:
            target_id, render_id, share_token = await _seed_existing_deck(maker)
            async with maker() as session:
                # This is the compensation invoked when the external exporter
                # raises after reading its committed render snapshot.
                await endpoint._discard_render_staging(
                    session,
                    render_id,
                    target_presentation_id=target_id,
                )

            await _assert_old_deck(maker, target_id, share_token)
            async with maker() as session:
                assert await session.get(PresentationModel, render_id) is None
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_final_publication_fault_rolls_back_existing_deck_snapshot(
    tmp_path: Path,
    monkeypatch,
):
    async def scenario():
        engine, maker = await _database(tmp_path / "publication-failure.db")
        try:
            target_id, render_id, share_token = await _seed_existing_deck(maker)
            async with maker() as session:
                target = await session.get(PresentationModel, target_id)
                render = await session.get(PresentationModel, render_id)
                assert target is not None and render is not None
                job = PresentationGenerationJob(
                    task_id=uuid.uuid4(),
                    presentation_id=target_id,
                    workspace_operation_id=uuid.uuid4(),
                    request_sha256="a" * 64,
                    request_payload={},
                    state="running",
                    attempt_token="attempt-fault",
                )

                async def prepare(*_args, **_kwargs):
                    return SimpleNamespace(public_path="/app_data/staged.pptx")

                async def fail_after_flush(session, **_kwargs):
                    # Exercise the dangerous point: target fields and slide
                    # replacement have reached SQL, but final publication has
                    # not committed. The real publisher rolls this UoW back on
                    # any fence/artifact failure.
                    await session.flush()
                    await session.rollback()
                    raise WorkspaceArtifactPublicationError("operation_lease_lost")

                staged_images = []

                async def stage_images(session, *, staging, artifacts):
                    staged_images.append((staging, artifacts))
                    await session.flush()

                generated_image = ImageAsset(
                    owner_id=None,
                    path="/app_data/images/immutable/generated.png",
                    extras={"source": "workspace-chat"},
                )
                image_staging = SimpleNamespace(artifact_id=uuid.uuid4())
                image_artifacts = (SimpleNamespace(artifact_id=uuid.uuid4()),)

                monkeypatch.setattr(
                    endpoint, "prepare_workspace_generation_artifact", prepare
                )
                monkeypatch.setattr(
                    endpoint,
                    "stage_workspace_chat_artifacts_for_publication",
                    stage_images,
                )
                monkeypatch.setattr(
                    endpoint, "publish_workspace_generation_artifact", fail_after_flush
                )
                with pytest.raises(HTTPException) as caught:
                    await endpoint._publish_staged_presentation(
                        session,
                        target,
                        "C:/exports/deck.pptx",
                        job,
                        render_presentation=render,
                        generated_assets=(generated_image,),
                        image_staging=image_staging,
                        image_artifacts=image_artifacts,
                    )
                assert caught.value.detail == "operation_lease_lost"
                assert staged_images == [(image_staging, image_artifacts)]

            await _assert_old_deck(maker, target_id, share_token)
            async with maker() as session:
                assert await session.get(ImageAsset, generated_image.id) is None
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_successful_publication_exposes_snapshot_once_and_preserves_share_identity(
    tmp_path: Path,
    monkeypatch,
):
    async def scenario():
        engine, maker = await _database(tmp_path / "publication-success.db")
        try:
            target_id, render_id, share_token = await _seed_existing_deck(maker)
            async with maker() as session:
                target = await session.get(PresentationModel, target_id)
                render = await session.get(PresentationModel, render_id)
                assert target is not None and render is not None
                job = PresentationGenerationJob(
                    task_id=uuid.uuid4(),
                    presentation_id=target_id,
                    workspace_operation_id=uuid.uuid4(),
                    request_sha256="b" * 64,
                    request_payload={},
                    state="running",
                    attempt_token="attempt-success",
                )

                async def prepare(*_args, **_kwargs):
                    return SimpleNamespace(
                        public_path="/app_data/final.pptx",
                        source_revision=8,
                    )

                async def publish(session, *, artifact, expected_revision, **_kwargs):
                    assert artifact.source_revision == expected_revision + 1 == 8
                    # The render promotion has issued SQL, yet another reader
                    # still observes the complete old publication until commit.
                    await _assert_old_deck(maker, target_id, share_token)
                    target.lifecycle_status = PRESENTATION_LIFECYCLE_PUBLISHED
                    target.revision = artifact.source_revision
                    session.add(target)
                    await session.commit()
                    return artifact.source_revision

                publication_order = []

                async def stage_images(session, *, staging, artifacts):
                    publication_order.append("images")
                    assert staging.artifact_id == image_staging.artifact_id
                    assert artifacts == image_artifacts
                    await session.flush()

                generated_image = ImageAsset(
                    owner_id=None,
                    path="/app_data/images/immutable/generated.png",
                    extras={"source": {"kind": "workspace-chat"}},
                )
                image_staging = SimpleNamespace(artifact_id=uuid.uuid4())
                image_artifacts = (SimpleNamespace(artifact_id=uuid.uuid4()),)

                async def publish_with_order(*args, **kwargs):
                    publication_order.append("presentation")
                    return await publish(*args, **kwargs)

                monkeypatch.setattr(
                    endpoint, "prepare_workspace_generation_artifact", prepare
                )
                monkeypatch.setattr(
                    endpoint,
                    "stage_workspace_chat_artifacts_for_publication",
                    stage_images,
                )
                monkeypatch.setattr(
                    endpoint,
                    "publish_workspace_generation_artifact",
                    publish_with_order,
                )
                path = await endpoint._publish_staged_presentation(
                    session,
                    target,
                    "C:/exports/deck.pptx",
                    job,
                    render_presentation=render,
                    generated_assets=(generated_image,),
                    image_staging=image_staging,
                    image_artifacts=image_artifacts,
                )
                assert path == "/app_data/final.pptx"
                assert publication_order == ["images", "presentation"]

            async with maker() as session:
                published = await session.get(PresentationModel, target_id)
                slides = (
                    await session.execute(
                        select(SlideModel).where(SlideModel.presentation == target_id)
                    )
                ).scalars().all()
                assert published is not None
                assert published.title == "New staged title"
                assert published.content == "new content"
                assert published.lifecycle_status == PRESENTATION_LIFECYCLE_PUBLISHED
                assert published.revision == 8
                assert published.share_token == share_token
                assert [slide.content for slide in slides] == [{"title": "new slide"}]
                assert await session.get(PresentationModel, render_id) is None
                persisted_image = await session.get(ImageAsset, generated_image.id)
                assert persisted_image is not None
                assert persisted_image.path == generated_image.path
                assert persisted_image.extras == generated_image.extras
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_image_publication_fault_compensates_prepared_export_before_publish(
    tmp_path: Path,
    monkeypatch,
):
    async def scenario():
        engine, maker = await _database(tmp_path / "image-publication-failure.db")
        try:
            target_id, render_id, share_token = await _seed_existing_deck(maker)
            async with maker() as session:
                target = await session.get(PresentationModel, target_id)
                render = await session.get(PresentationModel, render_id)
                assert target is not None and render is not None
                job = PresentationGenerationJob(
                    task_id=uuid.uuid4(),
                    presentation_id=target_id,
                    workspace_operation_id=uuid.uuid4(),
                    request_sha256="c" * 64,
                    request_payload={},
                    state="running",
                    attempt_token="attempt-image-fault",
                )
                prepared_export = SimpleNamespace(
                    artifact_id=uuid.uuid4(),
                    public_path="/app_data/prepared.pptx",
                )
                generated_image = ImageAsset(
                    owner_id=None,
                    path="/app_data/images/immutable/generated.png",
                )
                compensated = []
                publisher_called = False

                async def prepare(*_args, **_kwargs):
                    return prepared_export

                async def fail_image_stage(session, **_kwargs):
                    await session.flush()
                    raise WorkspaceArtifactPublicationError(
                        "workspace_chat_artifact_intent_conflict"
                    )

                async def publish(*_args, **_kwargs):
                    nonlocal publisher_called
                    publisher_called = True

                async def compensate(_session, artifact):
                    compensated.append(artifact)

                monkeypatch.setattr(
                    endpoint, "prepare_workspace_generation_artifact", prepare
                )
                monkeypatch.setattr(
                    endpoint,
                    "stage_workspace_chat_artifacts_for_publication",
                    fail_image_stage,
                )
                monkeypatch.setattr(
                    endpoint, "publish_workspace_generation_artifact", publish
                )
                monkeypatch.setattr(
                    endpoint, "compensate_workspace_artifact", compensate
                )

                with pytest.raises(HTTPException) as caught:
                    await endpoint._publish_staged_presentation(
                        session,
                        target,
                        "C:/exports/deck.pptx",
                        job,
                        render_presentation=render,
                        generated_assets=(generated_image,),
                        image_staging=SimpleNamespace(artifact_id=uuid.uuid4()),
                    )
                assert caught.value.detail == (
                    "workspace_chat_artifact_intent_conflict"
                )
                assert compensated == [prepared_export]
                assert publisher_called is False

            await _assert_old_deck(maker, target_id, share_token)
            async with maker() as session:
                assert await session.get(ImageAsset, generated_image.id) is None
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_workspace_image_path_replacement_is_recursive_and_longest_first():
    staging_root = "/app_data/images/workspace-chat/attempt/staging/generated.png"
    immutable_root = "/app_data/images/workspace-chat/attempt/artifacts/hash.png"
    value = {
        "src": staging_root,
        "nested": [
            {"url": f"https://localhost{staging_root}?download=1"},
            (staging_root, 7),
        ],
        "untouched": None,
    }

    assert endpoint._replace_workspace_image_paths(
        value,
        {
            "/app_data/images/workspace-chat/attempt": "/wrong-prefix",
            staging_root: immutable_root,
        },
    ) == {
        "src": immutable_root,
        "nested": [
            {"url": f"https://localhost{immutable_root}?download=1"},
            (immutable_root, 7),
        ],
        "untouched": None,
    }
