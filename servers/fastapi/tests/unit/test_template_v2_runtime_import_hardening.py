"""Defects found by rehearsing the runtime analyzer rather than unit-testing it.

Each one produced a plausible-looking result instead of an error, which is why the
green suite did not catch any of them.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import event, func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlmodel import SQLModel

from models.sql.async_presentation_generation_status import (
    AsyncPresentationGenerationTaskModel,
)
from models.sql.presentation import PresentationModel
from models.sql.slide import SlideModel
from models.sql.template_v2 import TemplateV2
from models.sql.template_v2_local_state import TemplateV2LocalState
from models.sql.template_v2_pptx_import import TemplateV2PptxImport
from services import template_v2_pptx_ingestion_service as ingestion
from services import template_v2_pptx_retention_service as retention
from services.template_v2_pptx_storage import (
    private_asset_reference,
    relocate_runtime_assets,
    resolve_private_asset,
)
from templates.v2.pptx.runtime_layouts import build_runtime_slide_layouts


def _runtime_job(*, deleted: bool, cleanup_token: str | None = None) -> SimpleNamespace:
    return SimpleNamespace(
        analysis_result={"analyzer": ingestion.RUNTIME_ANALYSIS_MARKER},
        source_deleted_at=datetime.now(timezone.utc) if deleted else None,
        source_cleanup_token=cleanup_token,
    )


def test_confirm_is_refused_once_retention_reclaimed_the_media():
    """Otherwise the template persists references to files retention already deleted."""
    assert ingestion._runtime_assets_reclaimed(_runtime_job(deleted=True)) is True


def test_confirm_stays_open_while_the_media_is_intact():
    assert ingestion._runtime_assets_reclaimed(_runtime_job(deleted=False)) is False


def test_confirm_is_refused_while_retention_holds_a_cleanup_claim():
    """`source_deleted_at` is written after the files are gone, not before."""
    claimed = _runtime_job(deleted=False, cleanup_token="cleanup-owner")

    assert ingestion._runtime_assets_reclaimed(claimed) is True


def test_a_deterministic_import_still_confirms_after_its_source_is_reclaimed():
    """Its analysis is self-contained, so losing the source costs only the audit copy."""
    job = SimpleNamespace(
        analysis_result={"candidates": {}},
        source_deleted_at=datetime.now(timezone.utc),
        source_cleanup_token="cleanup-owner",
    )

    assert ingestion._runtime_assets_reclaimed(job) is False


@pytest.mark.parametrize("elements", [None, "not-a-list", 7])
def test_a_layout_without_an_elements_array_is_left_for_the_validator(elements):
    """Defaulting to [] here turned a broken slide into a silently blank one."""
    rewritten = ingestion._with_private_asset_references(
        [{"id": "slide_1", "elements": elements}], SimpleNamespace(reference_for=lambda _u: None)
    )

    assert rewritten[0].get("elements") == elements


def test_a_layout_missing_elements_entirely_is_left_for_the_validator():
    rewritten = ingestion._with_private_asset_references(
        [{"id": "slide_1"}], SimpleNamespace(reference_for=lambda _u: None)
    )

    assert "elements" not in rewritten[0]


def _runtime_output(root: Path, *names: str) -> Path:
    run_directory = root / "pptx-to-json" / uuid.uuid4().hex
    media = run_directory / "images"
    media.mkdir(parents=True)
    for name in names:
        (media / name).write_bytes(b"\x89PNG\r\n\x1a\n" + name.encode())
    (run_directory / "presentation.json").write_text('{"layouts": []}', encoding="utf-8")
    return run_directory


def test_relocation_discards_the_converter_run_directory(tmp_path, monkeypatch):
    """It holds presentation.json -- the deck's full text -- outside retention's tree."""
    monkeypatch.setenv("APP_DATA_DIRECTORY", str(tmp_path / "app-data"))
    import_id = uuid.uuid4()
    run_directory = _runtime_output(tmp_path / "app-data", "image1.png")

    relocate_runtime_assets(run_directory, import_id=import_id)

    assert not run_directory.exists(), "the run directory must not survive the import"
    asset = resolve_private_asset(private_asset_reference(import_id, "image1.png"))
    assert asset.is_file(), "the media itself must have been taken over"


def test_relocation_leaves_no_temporary_files_behind(tmp_path, monkeypatch):
    """The temp name is per-call, so a leftover would mean an unfinished publish."""
    monkeypatch.setenv("APP_DATA_DIRECTORY", str(tmp_path / "app-data"))
    import_id = uuid.uuid4()
    run_directory = _runtime_output(tmp_path / "app-data", "a.png", "b.png")

    relocate_runtime_assets(run_directory, import_id=import_id)

    assets = resolve_private_asset(private_asset_reference(import_id, "a.png")).parent
    assert sorted(p.name for p in assets.iterdir()) == ["a.png", "b.png"]


async def _database(path: Path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{path}")

    @event.listens_for(engine.sync_engine, "connect")
    def enable_sqlite_foreign_keys(dbapi_connection, _connection_record) -> None:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(
            lambda sync_connection: SQLModel.metadata.create_all(
                sync_connection,
                tables=[
                    PresentationModel.__table__,
                    SlideModel.__table__,
                    TemplateV2.__table__,
                    TemplateV2LocalState.__table__,
                    AsyncPresentationGenerationTaskModel.__table__,
                    TemplateV2PptxImport.__table__,
                ],
            )
        )
    return engine, maker


def _runtime_analysis_result(import_id: uuid.UUID) -> dict:
    """The payload `_build_runtime_analysis` stores: layouts pointing at private media."""
    imported = build_runtime_slide_layouts(
        [
            {
                "id": "slide_1",
                "description": "Full slide layout converted from PPTX slide 1.",
                "elements": [
                    {
                        "type": "image",
                        "position": {"x": 144.0, "y": 115.2},
                        "size": {"width": 576.01, "height": 324.01},
                        "rotation": 0.0,
                        "data": private_asset_reference(import_id, "image1.png"),
                        "fit": "fill",
                        "decorative": True,
                        "name": "picture_1",
                        "is_icon": False,
                    }
                ],
            }
        ]
    )
    return {
        "analyzer": ingestion.RUNTIME_ANALYSIS_MARKER,
        "raw_layouts": imported.raw_layouts.model_dump(mode="json"),
        "layouts": imported.layouts.model_dump(mode="json"),
    }


async def _insert_expired_runtime_review(
    maker,
    *,
    now: datetime,
    source_deleted_at: datetime | None = None,
) -> tuple[uuid.UUID, str]:
    import_id = uuid.uuid4()
    task_id = f"task-{uuid.uuid4().hex}"
    async with maker() as session:
        session.add(
            AsyncPresentationGenerationTaskModel(
                id=task_id,
                status="completed",
                data={
                    "kind": ingestion.IMPORT_TASK_KIND,
                    "import_id": str(import_id),
                    "state": "review_required",
                    "attempt_number": 1,
                },
            )
        )
        session.add(
            TemplateV2PptxImport(
                id=import_id,
                task_id=task_id,
                requested_template_id=f"template-{uuid.uuid4().hex}",
                state="review_required",
                revision=2,
                attempt_number=1,
                analysis_result=_runtime_analysis_result(import_id),
                repeat_suggestions=[],
                source_retention_expires_at=now - timedelta(days=1),
                source_deleted_at=source_deleted_at,
                source_filename="source.pptx",
                source_media_type=(
                    "application/vnd.openxmlformats-officedocument."
                    "presentationml.presentation"
                ),
                source_size_bytes=100,
                source_sha256="a" * 64,
                source_storage_key=f"private/{import_id}.pptx",
                manifest={"schema_version": 1},
            )
        )
        await session.commit()
    return import_id, task_id


def test_confirm_refuses_an_import_whose_media_retention_already_deleted(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """The finished-cleanup case, which the DB gate is not the one guarding.

    Retention has already run to completion here: the media is gone and
    `source_deleted_at` is written, while `review_required` stays an eligible
    state, so the row still looks confirmable. `confirm` itself has to refuse --
    before it rebuilds the draft -- or it persists a template whose every image
    reference points at a deleted file.
    """

    async def scenario() -> None:
        engine, maker = await _database(tmp_path / "reclaimed-media.sqlite")
        now = datetime.now(timezone.utc)
        assembled: list[uuid.UUID] = []
        real_assemble = ingestion._assemble_confirmed_candidate

        def recording_assemble(import_job, accepted_repeat_suggestions):
            assembled.append(import_job.id)
            return real_assemble(import_job, accepted_repeat_suggestions)

        monkeypatch.setattr(
            ingestion, "_assemble_confirmed_candidate", recording_assemble
        )
        try:
            import_id, task_id = await _insert_expired_runtime_review(
                maker,
                now=now,
                source_deleted_at=now - timedelta(hours=1),
            )
            async with maker() as session:
                outcome = await ingestion.confirm_template_v2_pptx_import(
                    session,
                    import_id,
                    task_id,
                    owner_scope="local-disabled-auth-scope-v1",
                    expected_revision=2,
                )

            assert outcome == "assets_reclaimed"
            # The row's own columns already say the media is gone, so the refusal
            # must come from the up-front guard rather than from the write gate
            # that exists for the cleanup race.
            assert assembled == [], "a reclaimed import must be refused before any work"
            async with maker() as session:
                job = await session.get(TemplateV2PptxImport, import_id)
                assert job is not None
                assert job.state == "review_required"
                assert job.draft_template_id is None
                assert job.revision == 2
                assert (
                    await session.scalar(
                        select(func.count()).select_from(TemplateV2)
                    )
                    == 0
                ), "no template may point at files retention already deleted"
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_confirm_loses_to_a_cleanup_claim_taken_after_it_read_the_import(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """The claim, not `source_deleted_at`, is the moment the media stops existing.

    Retention claims an expired `review_required` row without touching `state` or
    `revision`, deletes the source and the relocated media, and writes
    `source_deleted_at` only afterwards. A confirm that read the row before the claim
    therefore sees an intact import, so the gate has to be what refuses -- otherwise
    it persists a template whose every image points at files that are already gone.
    """

    async def scenario() -> None:
        engine, maker = await _database(tmp_path / "cleanup-race.sqlite")
        monkeypatch.setattr(retention, "async_session_maker", maker)
        now = datetime.now(timezone.utc)
        try:
            import_id, task_id = await _insert_expired_runtime_review(maker, now=now)
            async with maker() as session:
                # The confirm request loads its row here, before retention claims it;
                # SQLAlchemy hands the same identity-mapped instance back inside
                # `confirm_template_v2_pptx_import`, so its in-memory guard is stale
                # exactly as it is in the real interleaving.
                preloaded = await session.get(TemplateV2PptxImport, import_id)
                assert preloaded is not None
                assert preloaded.source_cleanup_token is None

                claim = await retention._claim_source_for_cleanup(import_id, now=now)
                assert claim is not None, "retention must own the expired source"

                outcome = await ingestion.confirm_template_v2_pptx_import(
                    session,
                    import_id,
                    task_id,
                    owner_scope="local-disabled-auth-scope-v1",
                    expected_revision=2,
                )

            assert outcome == "assets_reclaimed"
            async with maker() as session:
                job = await session.get(TemplateV2PptxImport, import_id)
                assert job is not None
                assert job.state == "review_required"
                assert job.draft_template_id is None
                assert job.revision == 2
                assert (
                    await session.scalar(
                        select(func.count()).select_from(TemplateV2)
                    )
                    == 0
                )
        finally:
            await engine.dispose()

    asyncio.run(scenario())
