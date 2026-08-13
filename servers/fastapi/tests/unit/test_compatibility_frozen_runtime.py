import asyncio
import json
import uuid
from contextlib import contextmanager

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlmodel import SQLModel, select

from api.v1.ppt.endpoints.compatibility_migration import (
    COMPATIBILITY_MIGRATION_ROUTER,
)
from models.compatibility_migration import (
    FrozenCompatibilityOperation,
    UpgradeForkRequest,
)
from models.sql.presentation import PresentationModel
from models.sql.slide import SlideModel
from services.compatibility_migration.archive import (
    ArchiveManifestError,
    build_signed_archive_manifest,
    verify_signed_archive_manifest,
)
from services.compatibility_migration.runtime import (
    FrozenCompatibilityError,
    authorize_frozen_operation,
    get_frozen_runtime_descriptor,
    require_authored_mutation_evidence,
    require_authored_mutation_target,
)
from services.compatibility_migration.upgrade import (
    CompatibilityUpgradeError,
    upgrade_presentation_fork,
)
from services.presentation_profiles import resolve_presentation_profile
from services.workspace_chat_operation_handler import _build_run_context
from services.workspace_operation_handler_registry import (
    NonRetryableWorkspaceOperationError,
)
from utils.owner_context import reset_current_owner_id, set_current_owner_id


def _run(coroutine):
    return asyncio.run(coroutine)


@contextmanager
def _owner(owner_id: uuid.UUID | None):
    token = set_current_owner_id(owner_id)
    try:
        yield
    finally:
        reset_current_owner_id(token)


async def _session_maker(path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{path}")
    async with engine.begin() as connection:
        await connection.run_sync(
            lambda sync_connection: SQLModel.metadata.create_all(
                sync_connection,
                tables=[PresentationModel.__table__, SlideModel.__table__],
            )
        )
    return engine, async_sessionmaker(engine, expire_on_commit=False)


def _legacy_presentation(owner_id: uuid.UUID) -> PresentationModel:
    return PresentationModel(
        owner_id=owner_id,
        content="private legacy source",
        n_slides=1,
        language="Korean",
        title="Legacy deck",
        mode=None,
        version="v1-standard",
        generation_mode="standard",
    )


def _legacy_slide(owner_id: uuid.UUID, presentation_id: uuid.UUID) -> SlideModel:
    return SlideModel(
        owner_id=owner_id,
        presentation=presentation_id,
        layout_group="general",
        layout="title",
        index=0,
        content={"title": "original-only-value"},
    )


def test_archive_manifest_hashes_payloads_and_detects_tampering():
    key = b"k" * 32
    manifest = build_signed_archive_manifest(
        source_presentation_id=uuid.uuid4(),
        target_presentation_id=uuid.uuid4(),
        owner_id=uuid.uuid4(),
        source_payload=b"source-private-body",
        runtime_payload=b"runtime-contract",
        artifact_payload=b"artifact-private-body",
        signing_key=key,
        signing_key_id="compatibility-test-v1",
    )

    assert verify_signed_archive_manifest(manifest, signing_key=key)
    serialized = manifest.model_dump_json()
    assert "source-private-body" not in serialized
    assert "artifact-private-body" not in serialized
    assert key.decode() not in serialized
    assert [digest.role for digest in manifest.payload.digests] == [
        "source",
        "runtime",
        "artifact",
    ]

    tampered = manifest.model_copy(update={"signature": "0" * 64})
    assert not verify_signed_archive_manifest(tampered, signing_key=key)


def test_archive_manifest_rejects_weak_signing_material():
    with pytest.raises(
        ArchiveManifestError,
        match="compatibility_archive_signing_key_invalid",
    ):
        build_signed_archive_manifest(
            source_presentation_id=uuid.uuid4(),
            target_presentation_id=uuid.uuid4(),
            owner_id=None,
            source_payload=b"source",
            runtime_payload=b"runtime",
            artifact_payload=b"artifact",
            signing_key=b"too-short",
            signing_key_id="v1",
        )


def test_frozen_runtime_allows_only_declared_operations_and_is_owner_scoped(tmp_path):
    async def runner():
        engine, maker = await _session_maker(tmp_path / "runtime.db")
        owner_id = uuid.uuid4()
        other_owner_id = uuid.uuid4()
        source = _legacy_presentation(owner_id)
        with _owner(owner_id):
            async with maker() as session:
                session.add(source)
                await session.commit()

                descriptor = await get_frozen_runtime_descriptor(session, source.id)
                assert descriptor.source_driver_id == "standard"
                assert set(descriptor.allowed_operations) == set(
                    FrozenCompatibilityOperation
                )
                for operation in FrozenCompatibilityOperation:
                    decision = await authorize_frozen_operation(
                        session, source.id, operation.value
                    )
                    assert decision.allowed is True

                with pytest.raises(
                    FrozenCompatibilityError, match="legacy_presentation_frozen"
                ):
                    await authorize_frozen_operation(session, source.id, "update")

        with _owner(other_owner_id):
            async with maker() as session:
                with pytest.raises(
                    FrozenCompatibilityError,
                    match="compatibility_presentation_not_found",
                ):
                    await get_frozen_runtime_descriptor(session, source.id)
        await engine.dispose()

    _run(runner())


def test_workspace_chat_common_context_rejects_frozen_legacy_before_provider(tmp_path):
    async def runner():
        engine, maker = await _session_maker(tmp_path / "workspace-gate.db")
        owner_id = uuid.uuid4()
        source = _legacy_presentation(owner_id)
        claim = type(
            "Claim",
            (),
            {
                "presentation_id": source.id,
                "operation_id": uuid.uuid4(),
                "conversation_id": uuid.uuid4(),
                "expected_revision": 0,
                "owner_id": owner_id,
                "request_payload": {},
            },
        )()
        artifact_scope = type("Scope", (), {})()
        with _owner(owner_id):
            async with maker() as session:
                session.add(source)
                await session.commit()
                with pytest.raises(
                    NonRetryableWorkspaceOperationError,
                    match="legacy_presentation_frozen",
                ):
                    await _build_run_context(
                        session,
                        claim=claim,
                        command="mutate",
                        artifact_scope=artifact_scope,
                        cancellation_check=lambda: None,
                    )
        await engine.dispose()

    _run(runner())


def test_authored_deck_remains_a_valid_mutation_target():
    presentation = PresentationModel(
        content="authored",
        n_slides=1,
        language="en",
        mode="authored",
        theme={"mode": "authored"},
    )
    assert require_authored_mutation_target(presentation).driver_id == "authored"


def test_authored_profile_with_legacy_slide_evidence_is_quarantined(tmp_path):
    async def runner():
        engine, maker = await _session_maker(tmp_path / "authored-mixed.db")
        owner_id = uuid.uuid4()
        presentation = PresentationModel(
            owner_id=owner_id,
            content="authored",
            n_slides=1,
            language="en",
            mode="authored",
            theme={"mode": "authored"},
        )
        legacy_slide = _legacy_slide(owner_id, presentation.id)
        with _owner(owner_id):
            async with maker() as session:
                session.add_all((presentation, legacy_slide))
                await session.commit()
                with pytest.raises(
                    FrozenCompatibilityError,
                    match="compatibility_presentation_quarantined",
                ):
                    await require_authored_mutation_evidence(session, presentation)
        await engine.dispose()

    _run(runner())


def test_upgrade_fork_is_authored_new_id_and_preserves_original(tmp_path):
    async def runner():
        engine, maker = await _session_maker(tmp_path / "upgrade.db")
        owner_id = uuid.uuid4()
        source = _legacy_presentation(owner_id)
        source_slide = _legacy_slide(owner_id, source.id)
        key = b"archive-signing-key-for-focused-test"
        with _owner(owner_id):
            async with maker() as session:
                session.add(source)
                session.add(source_slide)
                await session.commit()
                original_presentation = source.model_dump(
                    mode="json",
                    exclude={"created_at", "updated_at", "profile_backfilled_at"},
                )
                original_slide = source_slide.model_dump(mode="json", exclude_none=True)

                response = await upgrade_presentation_fork(
                    session,
                    source_presentation_id=source.id,
                    request=UpgradeForkRequest(
                        expected_source_revision=source.revision,
                        slides=[
                            {
                                "index": 0,
                                "html_content": "<main>authored fork</main>",
                                "properties": {"image": "derived.png"},
                            }
                        ],
                    ),
                    archive_signing_key=key,
                    archive_signing_key_id="test-key-v1",
                )

                assert response.presentation_id != source.id
                target = await session.get(PresentationModel, response.presentation_id)
                assert target is not None
                assert target.owner_id == owner_id
                assert resolve_presentation_profile(target).driver_id == "authored"
                target_slides = list(
                    (
                        await session.scalars(
                            select(SlideModel).where(
                                SlideModel.presentation == response.presentation_id
                            )
                        )
                    ).all()
                )
                assert len(target_slides) == 1
                assert target_slides[0].html_content == "<main>authored fork</main>"
                assert target_slides[0].is_authored()

                await session.refresh(source)
                await session.refresh(source_slide)
                assert source.model_dump(
                    mode="json",
                    exclude={"created_at", "updated_at", "profile_backfilled_at"},
                ) == original_presentation
                assert source_slide.model_dump(
                    mode="json", exclude_none=True
                ) == original_slide
                assert verify_signed_archive_manifest(
                    response.archive_manifest, signing_key=key
                )
                manifest_json = json.dumps(
                    response.archive_manifest.model_dump(mode="json"), sort_keys=True
                )
                assert "private legacy source" not in manifest_json
                assert "authored fork" not in manifest_json
        await engine.dispose()

    _run(runner())


def test_upgrade_fork_rejects_stale_revision_and_other_owner(tmp_path):
    async def runner():
        engine, maker = await _session_maker(tmp_path / "upgrade-errors.db")
        owner_id = uuid.uuid4()
        other_owner_id = uuid.uuid4()
        source = _legacy_presentation(owner_id)
        with _owner(owner_id):
            async with maker() as session:
                session.add(source)
                await session.commit()
                with pytest.raises(
                    CompatibilityUpgradeError,
                    match="compatibility_source_revision_conflict",
                ):
                    await upgrade_presentation_fork(
                        session,
                        source_presentation_id=source.id,
                        request=UpgradeForkRequest(
                            expected_source_revision=source.revision + 1,
                            slides=[{"index": 0, "html_content": "<main>x</main>"}],
                        ),
                        archive_signing_key=b"k" * 32,
                        archive_signing_key_id="test-v1",
                    )

        with _owner(other_owner_id):
            async with maker() as session:
                with pytest.raises(
                    CompatibilityUpgradeError,
                    match="compatibility_presentation_not_found",
                ):
                    await upgrade_presentation_fork(
                        session,
                        source_presentation_id=source.id,
                        request=UpgradeForkRequest(
                            expected_source_revision=source.revision,
                            slides=[{"index": 0, "html_content": "<main>x</main>"}],
                        ),
                        archive_signing_key=b"k" * 32,
                        archive_signing_key_id="test-v1",
                    )
        await engine.dispose()

    _run(runner())


def test_mixed_slide_evidence_is_quarantined_from_runtime_and_upgrade(tmp_path):
    async def runner():
        engine, maker = await _session_maker(tmp_path / "quarantine.db")
        owner_id = uuid.uuid4()
        source = _legacy_presentation(owner_id)
        mixed_slide = SlideModel(
            owner_id=owner_id,
            presentation=source.id,
            layout_group="authored",
            layout="authored:CONTENT",
            index=0,
            content={"__authored__": True},
            html_content="<main>conflicts with legacy profile</main>",
        )
        with _owner(owner_id):
            async with maker() as session:
                session.add_all((source, mixed_slide))
                await session.commit()
                with pytest.raises(
                    FrozenCompatibilityError,
                    match="compatibility_presentation_quarantined",
                ):
                    await get_frozen_runtime_descriptor(session, source.id)
                with pytest.raises(
                    CompatibilityUpgradeError,
                    match="compatibility_presentation_quarantined",
                ):
                    await upgrade_presentation_fork(
                        session,
                        source_presentation_id=source.id,
                        request=UpgradeForkRequest(
                            expected_source_revision=source.revision,
                            slides=[{"index": 0, "html_content": "<main>x</main>"}],
                        ),
                        archive_signing_key=b"k" * 32,
                        archive_signing_key_id="test-v1",
                    )
        await engine.dispose()

    _run(runner())


def test_router_contract_is_mountable_without_touching_shared_router():
    paths = {route.path for route in COMPATIBILITY_MIGRATION_ROUTER.routes}
    assert "/compatibility/capabilities" in paths
    assert "/compatibility/presentations/{presentation_id}/runtime" in paths
    assert (
        "/compatibility/presentations/{presentation_id}/runtime/{operation}" in paths
    )
    assert "/compatibility/presentations/{presentation_id}/upgrade-fork" in paths
