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
from models.compatibility_migration import UpgradeForkRequest
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


def _non_authored_presentation(owner_id: uuid.UUID) -> PresentationModel:
    """A deck that is not authored, but still resolves to a live driver.

    ``svg-v1`` is now the only driver a persisted row can resolve to besides
    ``authored``: Template V2 was removed from the driver registry in
    ``services.presentation_profiles`` (2026-09-25), and the row it used to
    describe (``mode="template"``, ``version="v2-standard"``) can no longer
    even be committed -- ``services.database_owner_scope`` stamps every new
    presentation's profile on flush, and that stamp now raises for such a
    row instead of succeeding. So there is no more "frozen legacy" identity
    a test fixture can construct; this fixture stands in for "any live,
    non-authored driver" in tests that check mutation is blocked for one.
    """
    return PresentationModel(
        owner_id=owner_id,
        content="svg deck",
        n_slides=1,
        language="Korean",
        title="SVG deck",
        mode="svg",
        version="v1-standard",
        theme={"mode": "svg"},
        generation_mode="standard",
    )


def _frozen_slide(owner_id: uuid.UUID, presentation_id: uuid.UUID) -> SlideModel:
    return SlideModel(
        owner_id=owner_id,
        presentation=presentation_id,
        layout_group="native",
        layout="title",
        index=0,
        content={"title": "original-only-value"},
        ui=_native_ui(),
    )


def _native_ui() -> dict:
    """The smallest slide UI the template-v2 canonicaliser accepts."""
    return {
        "id": "title",
        "description": "Native editable title slide",
        "components": [
            {
                "id": "hero",
                "description": "Editable hero title component",
                "position": {"x": 0, "y": 0},
                "elements": [
                    {
                        "type": "text",
                        "position": {"x": 1, "y": 1},
                        "size": {"width": 8, "height": 1},
                        "runs": [{"text": "Title", "font": {"size": 18}}],
                        "decorative": False,
                        "name": "title",
                        "min_length": 1,
                        "max_length": 80,
                    }
                ],
            }
        ],
    }


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


def test_frozen_runtime_is_never_applicable_to_a_live_driver_and_is_owner_scoped(
    tmp_path,
):
    """No driver is ever "frozen" any more; every live profile is not_applicable.

    The registry only ever lists live drivers (``authored``, ``svg-v1``), so a
    profile that resolves at all can never be the retired ``template-v2``
    identity ``FrozenRuntimeDescriptor`` used to describe. Both live drivers
    below hit the same ``compatibility_runtime_not_applicable`` outcome.
    """

    async def runner():
        engine, maker = await _session_maker(tmp_path / "runtime.db")
        owner_id = uuid.uuid4()
        other_owner_id = uuid.uuid4()
        authored = PresentationModel(
            owner_id=owner_id,
            content="authored",
            n_slides=1,
            language="en",
            mode="authored",
            theme={"mode": "authored"},
        )
        svg = _non_authored_presentation(owner_id)
        with _owner(owner_id):
            async with maker() as session:
                session.add_all((authored, svg))
                await session.commit()

                for presentation in (authored, svg):
                    with pytest.raises(
                        FrozenCompatibilityError,
                        match="compatibility_runtime_not_applicable",
                    ):
                        await get_frozen_runtime_descriptor(session, presentation.id)
                    with pytest.raises(
                        FrozenCompatibilityError,
                        match="compatibility_runtime_not_applicable",
                    ):
                        await authorize_frozen_operation(
                            session, presentation.id, "open"
                        )

        with _owner(other_owner_id):
            async with maker() as session:
                with pytest.raises(
                    FrozenCompatibilityError,
                    match="compatibility_presentation_not_found",
                ):
                    await get_frozen_runtime_descriptor(session, svg.id)
        await engine.dispose()

    _run(runner())


def test_workspace_chat_common_context_rejects_frozen_legacy_before_provider(tmp_path):
    async def runner():
        engine, maker = await _session_maker(tmp_path / "workspace-gate.db")
        owner_id = uuid.uuid4()
        source = _non_authored_presentation(owner_id)
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
        legacy_slide = _frozen_slide(owner_id, presentation.id)
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
        source = _non_authored_presentation(owner_id)
        source_slide = SlideModel(
            owner_id=owner_id,
            presentation=source.id,
            layout_group="svg",
            layout="svg",
            index=0,
            content={},
            svg_content="<svg></svg>",
        )
        key = b"archive-signing-key-for-focused-test"
        with _owner(owner_id):
            async with maker() as session:
                session.add(source)
                session.add(source_slide)
                await session.commit()
                # Read the rows back before snapshotting them so the in-memory
                # snapshot matches what is actually stored, and the comparison
                # below checks what this test is actually testing: that the
                # upgrade leaves the source untouched.
                await session.refresh(source)
                await session.refresh(source_slide)
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
        source = _non_authored_presentation(owner_id)
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


def test_mixed_slide_evidence_is_quarantined_from_upgrade(tmp_path):
    """A deck whose slides disagree with its resolved driver cannot be forked.

    ``get_frozen_runtime_descriptor`` no longer performs this slide-evidence
    cross-check itself: every profile that resolves is live, so it always
    answers ``compatibility_runtime_not_applicable`` without needing to look
    at slide evidence (see ``test_frozen_runtime_is_never_applicable_to_a_live_driver_and_is_owner_scoped``).
    ``upgrade_presentation_fork`` still runs the census, and this is the path
    that exercises it.
    """

    async def runner():
        engine, maker = await _session_maker(tmp_path / "quarantine.db")
        owner_id = uuid.uuid4()
        source = _non_authored_presentation(owner_id)
        mixed_slide = SlideModel(
            owner_id=owner_id,
            presentation=source.id,
            layout_group="authored",
            layout="authored:CONTENT",
            index=0,
            content={"__authored__": True},
            html_content="<main>conflicts with the resolved svg profile</main>",
        )
        with _owner(owner_id):
            async with maker() as session:
                session.add_all((source, mixed_slide))
                await session.commit()
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
