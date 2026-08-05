import asyncio
import uuid
from functools import wraps
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlmodel import SQLModel

from models.presentation_from_template import (
    AuthoredPageRemakePreviewRequest,
    AuthoredPageRemakePublishRequest,
)
from models.presentation_outline_model import (
    PresentationOutlineModel,
    SlideOutlineModel,
)
from models.sql.async_presentation_generation_status import (
    AsyncPresentationGenerationTaskModel,
)
from models.sql.presentation import PresentationModel
from models.sql.slide import SlideModel
from services import authored_page_remake_service as remake
from services.workspace_operation_handler_registry import (
    NonRetryableWorkspaceOperationError,
    WorkspaceOperationClaim,
    WorkspaceOperationHandlerRegistry,
)
from services.workspace_page_remake_operation_handler import (
    WorkspacePageRemakeOperationHandler,
    register_workspace_page_remake_operation_handler,
)
from utils.authored_dom_quality import (
    STYLE_REFERENCE,
    DomQualityIssue,
    DomQualityReport,
)

SOURCE_HTML = "<html><body><main data-qid='source'>Source</main></body></html>"
CANDIDATE_HTML = "<html><body><main data-qid='candidate'>Candidate</main></body></html>"


def async_test(function):
    @wraps(function)
    def run(*args, **kwargs):
        return asyncio.run(function(*args, **kwargs))

    return run


async def _engine(path: Path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{path}")
    async with engine.begin() as connection:
        await connection.run_sync(SQLModel.metadata.create_all)
    return engine


def _presentation() -> PresentationModel:
    outline = PresentationOutlineModel(
        slides=[SlideOutlineModel(content="Keep all facts")]
    )
    return PresentationModel(
        content="Deck",
        n_slides=1,
        language="Korean",
        title="Deck",
        mode="authored",
        outlines=outline.model_dump(mode="json"),
        theme={"style": "default"},
    )


def _slide(presentation_id) -> SlideModel:
    return SlideModel(
        presentation=presentation_id,
        layout_group="authored",
        layout="authored:content",
        index=0,
        content={"__authored__": True, "role": "content", "image": "old.png"},
        html_content=SOURCE_HTML,
        properties={"image": "old.png", "slidespec_active": {"bundle_id": "old"}},
    )


def _report(*, issue: bool, telemetry: str = "available") -> DomQualityReport:
    return DomQualityReport(
        reference_mode=STYLE_REFERENCE,
        issues=(
            (
                DomQualityIssue(
                    category="overlap",
                    severity="high",
                    detail="summary overlaps cards",
                    qids=("summary", "card"),
                ),
            )
            if issue
            else ()
        ),
        compact_dom=(),
        inspected_elements=2,
        telemetry_status=telemetry,
    )


class _SessionFactory:
    """Give the background preview runner the test-owned session."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    def __call__(self) -> "_SessionFactory":
        return self

    async def __aenter__(self) -> AsyncSession:
        return self._session

    async def __aexit__(self, *_exc_info) -> bool:
        return False


async def _seed_preview(session: AsyncSession):
    presentation = _presentation()
    session.add(presentation)
    await session.flush()
    slide = _slide(presentation.id)
    task = AsyncPresentationGenerationTaskModel(status="pending")
    session.add_all((slide, task))
    await session.commit()
    return presentation, slide, task


def _patch_preview(
    monkeypatch,
    *,
    candidate_report: DomQualityReport,
    candidate_render_error: Exception | None = None,
    generation_error: Exception | None = None,
) -> None:
    """Stub rendering, DOM inspection, generation and asset publication."""

    async def fake_context(_session, _presentation, _slides):
        return (
            SimpleNamespace(
                roles=("content",),
                brand=SimpleNamespace(),
                design_system="design",
                reference_images=((),),
            ),
            None,
            STYLE_REFERENCE,
            None,
        )

    renders = {"count": 0}

    async def fake_render(_html):
        renders["count"] += 1
        if candidate_render_error is not None and renders["count"] > 1:
            raise candidate_render_error
        return b"png", {"telemetry_status": "available"}

    async def fake_revise(*_args, **_kwargs):
        if generation_error is not None:
            raise generation_error
        return (
            [CANDIDATE_HTML],
            [b"candidate-png"],
            [0],
            [{"path": "full_fallback", "succeeded": True}],
        )

    reports = iter((_report(issue=True), candidate_report))
    monkeypatch.setattr(remake, "_authoring_context", fake_context)
    monkeypatch.setattr(remake, "_render_review_html", fake_render)
    monkeypatch.setattr(remake, "inspect_authored_dom", lambda *_a, **_k: next(reports))
    monkeypatch.setattr(remake, "revise_authored_deck", fake_revise)
    monkeypatch.setattr(
        remake,
        "publish_reviewed_png",
        lambda *_a, **_k: "/app_data/images/remake.png",
    )


def _preview_task_data(presentation, slide, *, candidate_dom: str) -> dict:
    return {
        "kind": remake.PREVIEW_KIND,
        "phase": "preview_ready",
        "presentation_id": str(presentation.id),
        "slide_id": str(slide.id),
        "slide_index": 0,
        "source_digest": remake.authored_page_source_digest(slide),
        "candidate_digest": "sha256:candidate",
        "candidate_html": CANDIDATE_HTML,
        "candidate_image": "/app_data/images/remake.png",
        "validation": {
            "source_render": "passed",
            "candidate_render": "passed",
            "candidate_dom": candidate_dom,
            "candidate_dom_telemetry": "available",
            "reference_mode": STYLE_REFERENCE,
        },
    }


def test_page_remake_contract_has_no_deck_scope():
    assert AuthoredPageRemakePreviewRequest.model_validate({}).instruction is None
    assert (
        AuthoredPageRemakePublishRequest(preview_task_id="task-preview").preview_task_id
        == "task-preview"
    )
    assert (
        "scope"
        not in AuthoredPageRemakePreviewRequest.model_json_schema()["properties"]
    )
    assert (
        "slide_indices"
        not in AuthoredPageRemakePreviewRequest.model_json_schema()["properties"]
    )
    with pytest.raises(ValidationError):
        AuthoredPageRemakePublishRequest(preview_task_id="x")
    with pytest.raises(ValidationError):
        AuthoredPageRemakePreviewRequest.model_validate({"scope": "all"})


def test_workspace_registration_replaces_generic_slide_regeneration_handler():
    registry = WorkspaceOperationHandlerRegistry()
    registry.register("slide.regenerate", lambda _context: None)
    handler = register_workspace_page_remake_operation_handler(
        registry=registry, replace=True
    )
    assert isinstance(handler, WorkspacePageRemakeOperationHandler)
    assert registry.resolve("slide.regenerate") is handler


@async_test
async def test_workspace_handler_requires_exact_slide_and_preview_task():
    claim = WorkspaceOperationClaim(
        operation_id=uuid.uuid4(),
        owner_id=None,
        owner_scope="anonymous",
        conversation_id=uuid.uuid4(),
        presentation_id=uuid.uuid4(),
        kind="slide.regenerate",
        request_payload={"context": {"slide_indices": [0, 1]}},
        expected_revision=1,
        attempt_number=1,
        attempt_token="attempt",
        fencing_token=1,
    )
    context = SimpleNamespace(claim=claim)
    with pytest.raises(
        NonRetryableWorkspaceOperationError, match="page_remake_request_invalid"
    ):
        await WorkspacePageRemakeOperationHandler().execute(context)


@async_test
async def test_preview_is_detached_and_pins_exact_source(tmp_path, monkeypatch):
    engine = await _engine(tmp_path / "preview.sqlite")
    async with AsyncSession(engine, expire_on_commit=False) as session:
        presentation = _presentation()
        session.add(presentation)
        await session.flush()
        slide = _slide(presentation.id)
        task = AsyncPresentationGenerationTaskModel(status="pending")
        session.add_all((slide, task))
        await session.commit()
        original_digest = remake.authored_page_source_digest(slide)

        async def fake_context(_session, _presentation, _slides):
            return (
                SimpleNamespace(
                    roles=("content",),
                    brand=SimpleNamespace(),
                    design_system="design",
                    reference_images=((),),
                ),
                None,
                STYLE_REFERENCE,
                None,
            )

        render_count = 0

        async def fake_render(_html):
            nonlocal render_count
            render_count += 1
            return b"png", {"telemetry_status": "available"}

        reports = iter((_report(issue=True), _report(issue=False)))

        async def fake_revise(*_args, **kwargs):
            assert kwargs["slide_indices"] == [0]
            assert kwargs["total_slides"] == 1
            assert kwargs["force_full_reauthor_indices"] == [0]
            return (
                [CANDIDATE_HTML],
                [b"candidate-png"],
                [0],
                [{"path": "full_fallback", "succeeded": True}],
            )

        monkeypatch.setattr(remake, "_authoring_context", fake_context)
        monkeypatch.setattr(remake, "_render_review_html", fake_render)
        monkeypatch.setattr(
            remake, "inspect_authored_dom", lambda *_a, **_k: next(reports)
        )
        monkeypatch.setattr(remake, "revise_authored_deck", fake_revise)
        monkeypatch.setattr(
            remake,
            "publish_reviewed_png",
            lambda *_a, **_k: "/app_data/images/remake.png",
        )

        await remake.run_authored_page_remake_preview(
            presentation.id,
            0,
            AuthoredPageRemakePreviewRequest(),
            task,
            session,
        )
        await session.refresh(slide)
        await session.refresh(task)

        assert slide.html_content == SOURCE_HTML
        assert slide.properties["image"] == "old.png"
        assert task.status == "completed"
        assert task.data["phase"] == "preview_ready"
        assert task.data["slide_index"] == 0
        assert task.data["source_digest"] == original_digest
        assert task.data["candidate_html"] == CANDIDATE_HTML
        assert task.data["generation"]["llm_scope"] == "single_page"
        assert render_count == 2
    await engine.dispose()


@async_test
async def test_publish_snapshots_cas_updates_and_invalidates_stale_pointer(
    tmp_path, monkeypatch
):
    engine = await _engine(tmp_path / "publish.sqlite")
    snapshots = []
    async with AsyncSession(engine, expire_on_commit=False) as session:
        presentation = _presentation()
        session.add(presentation)
        await session.flush()
        slide = _slide(presentation.id)
        session.add(slide)
        await session.flush()
        task = AsyncPresentationGenerationTaskModel(
            status="completed",
            data={
                "kind": remake.PREVIEW_KIND,
                "phase": "preview_ready",
                "presentation_id": str(presentation.id),
                "slide_id": str(slide.id),
                "slide_index": 0,
                "source_digest": remake.authored_page_source_digest(slide),
                "candidate_digest": "sha256:candidate",
                "candidate_html": CANDIDATE_HTML,
                "candidate_image": "/app_data/images/remake.png",
            },
        )
        session.add(task)
        await session.commit()

        async def fake_snapshot(_session, _presentation_id, slides, **kwargs):
            snapshots.append((slides, kwargs))

        async def fake_context(_session, _presentation, _slides):
            return SimpleNamespace(), None, STYLE_REFERENCE, None

        monkeypatch.setattr(remake.version_service, "snapshot_slides", fake_snapshot)
        monkeypatch.setattr(remake, "_render_review_html", lambda _html: None)

        async def fake_render(_html):
            return b"png", {"telemetry_status": "available"}

        monkeypatch.setattr(remake, "_render_review_html", fake_render)
        monkeypatch.setattr(remake, "_authoring_context", fake_context)
        monkeypatch.setattr(
            remake, "inspect_authored_dom", lambda *_a, **_k: _report(issue=False)
        )

        result = await remake.publish_authored_page_remake(
            session=session,
            presentation_id=presentation.id,
            slide_index=0,
            preview_task_id=task.id,
        )
        await session.commit()
        await session.refresh(slide)
        await session.refresh(task)

        assert result["status"] == "published"
        assert result["slide_index"] == 0
        assert slide.html_content == CANDIDATE_HTML
        assert slide.content["image"] == "/app_data/images/remake.png"
        assert slide.properties["image"] == "/app_data/images/remake.png"
        assert "slidespec_active" not in slide.properties
        assert task.data["phase"] == "published"
        assert task.data["publish"]["rollback_available"] is True
        assert result["post_verify_status"] == "passed"
        assert task.data["publish"]["post_verify"] == result["post_verify_status"]
        assert len(snapshots) == 1
        assert snapshots[0][0][0]["html_content"] == SOURCE_HTML
    await engine.dispose()


@async_test
async def test_publish_rejects_stale_source_before_snapshot(tmp_path, monkeypatch):
    engine = await _engine(tmp_path / "stale.sqlite")
    async with AsyncSession(engine, expire_on_commit=False) as session:
        presentation = _presentation()
        session.add(presentation)
        await session.flush()
        slide = _slide(presentation.id)
        session.add(slide)
        await session.flush()
        task = AsyncPresentationGenerationTaskModel(
            status="completed",
            data={
                "kind": remake.PREVIEW_KIND,
                "phase": "preview_ready",
                "presentation_id": str(presentation.id),
                "slide_id": str(slide.id),
                "slide_index": 0,
                "source_digest": "sha256:stale",
                "candidate_digest": "sha256:candidate",
                "candidate_html": CANDIDATE_HTML,
                "candidate_image": "/app_data/images/remake.png",
            },
        )
        session.add(task)
        await session.commit()
        snapshot_called = False

        async def fake_snapshot(*_args, **_kwargs):
            nonlocal snapshot_called
            snapshot_called = True

        monkeypatch.setattr(remake.version_service, "snapshot_slides", fake_snapshot)
        with pytest.raises(
            remake.AuthoredPageRemakeError,
            match="authored_page_remake_source_changed",
        ):
            await remake.publish_authored_page_remake(
                session=session,
                presentation_id=presentation.id,
                slide_index=0,
                preview_task_id=task.id,
            )
        assert snapshot_called is False
        await session.refresh(slide)
        assert slide.html_content == SOURCE_HTML
    await engine.dispose()


@async_test
async def test_clean_candidate_reports_passed_dom_validation(tmp_path, monkeypatch):
    engine = await _engine(tmp_path / "clean.sqlite")
    async with AsyncSession(engine, expire_on_commit=False) as session:
        presentation, _slide_row, task = await _seed_preview(session)
        _patch_preview(monkeypatch, candidate_report=_report(issue=False))

        await remake.run_authored_page_remake_preview(
            presentation.id,
            0,
            AuthoredPageRemakePreviewRequest(),
            task,
            session,
        )
        await session.refresh(task)

        assert task.status == "completed"
        assert task.data["validation"]["candidate_dom"] == "passed"
        assert task.data["validation"]["candidate_dom_telemetry"] == "available"
        assert task.data["candidate_issues"] == []
    await engine.dispose()


@async_test
async def test_candidate_dom_issues_warn_without_hiding_the_preview(
    tmp_path, monkeypatch
):
    engine = await _engine(tmp_path / "warning.sqlite")
    async with AsyncSession(engine, expire_on_commit=False) as session:
        presentation, slide, task = await _seed_preview(session)
        _patch_preview(monkeypatch, candidate_report=_report(issue=True))

        await remake.run_authored_page_remake_preview(
            presentation.id,
            0,
            AuthoredPageRemakePreviewRequest(),
            task,
            session,
        )
        await session.refresh(task)

        assert task.status == "completed"
        assert task.error is None
        assert task.data["phase"] == "preview_ready"
        assert task.data["validation"]["candidate_dom"] == "warning"
        assert task.data["validation"]["candidate_render"] == "passed"
        assert task.data["candidate_html"] == CANDIDATE_HTML
        assert task.data["candidate_image"] == "/app_data/images/remake.png"
        assert task.data["candidate_issues"], "issue evidence must be preserved"
        assert task.data["candidate_issues"][0]["category"] == "overlap"
        assert task.data["publish"] == {
            "status": "not_started",
            "rollback_available": False,
        }

        # Requirement: a warned preview stays detached until an explicit publish.
        stored = (
            await session.execute(
                select(SlideModel.html_content, SlideModel.properties).where(
                    SlideModel.id == slide.id
                )
            )
        ).one()
        assert stored.html_content == SOURCE_HTML
        assert stored.properties["image"] == "old.png"
    await engine.dispose()


@pytest.mark.parametrize("telemetry", ("unavailable", "incomplete"))
@async_test
async def test_issue_free_telemetry_warning_stays_explainable(
    tmp_path, monkeypatch, telemetry
):
    engine = await _engine(tmp_path / f"telemetry-{telemetry}.sqlite")
    async with AsyncSession(engine, expire_on_commit=False) as session:
        presentation, _slide_row, task = await _seed_preview(session)
        _patch_preview(
            monkeypatch,
            candidate_report=_report(issue=False, telemetry=telemetry),
        )

        await remake.run_authored_page_remake_preview(
            presentation.id,
            0,
            AuthoredPageRemakePreviewRequest(),
            task,
            session,
        )
        await session.refresh(task)

        # A probe-coverage warning has no issues; the telemetry field is the only
        # thing that can explain it to the user.
        assert task.status == "completed"
        assert task.data["phase"] == "preview_ready"
        assert task.data["validation"]["candidate_dom"] == "warning"
        assert task.data["validation"]["candidate_dom_telemetry"] == telemetry
        assert task.data["candidate_issues"] == []
        assert task.data["candidate_html"] == CANDIDATE_HTML
    await engine.dispose()


@async_test
async def test_candidate_render_failure_still_fails_the_task(tmp_path, monkeypatch):
    engine = await _engine(tmp_path / "render-failed.sqlite")
    async with AsyncSession(engine, expire_on_commit=False) as session:
        presentation, slide, task = await _seed_preview(session)
        _patch_preview(
            monkeypatch,
            candidate_report=_report(issue=False),
            candidate_render_error=RuntimeError("chromium crashed"),
        )
        monkeypatch.setattr(remake, "async_session_maker", _SessionFactory(session))

        await remake._run_preview_background(
            presentation.id,
            0,
            AuthoredPageRemakePreviewRequest(),
            task.id,
        )
        await session.refresh(task)

        assert task.status == "error"
        assert task.error["detail"] == "authored_page_remake_candidate_render_failed"
        assert task.error["stage"] == "candidate_render"
        assert set(task.error) == {"detail", "stage", "retryable"}
        await session.refresh(slide)
        assert slide.html_content == SOURCE_HTML
    await engine.dispose()


@async_test
async def test_generation_failure_still_fails_the_task(tmp_path, monkeypatch):
    engine = await _engine(tmp_path / "generation-failed.sqlite")
    async with AsyncSession(engine, expire_on_commit=False) as session:
        presentation, slide, task = await _seed_preview(session)
        _patch_preview(
            monkeypatch,
            candidate_report=_report(issue=False),
            generation_error=RuntimeError("model unavailable"),
        )
        monkeypatch.setattr(remake, "async_session_maker", _SessionFactory(session))

        await remake._run_preview_background(
            presentation.id,
            0,
            AuthoredPageRemakePreviewRequest(),
            task.id,
        )
        await session.refresh(task)

        assert task.status == "error"
        assert task.error["detail"] == "authored_page_remake_generation_failed"
        assert task.error["stage"] == "generation"
        await session.refresh(slide)
        assert slide.html_content == SOURCE_HTML
    await engine.dispose()


@async_test
async def test_warned_preview_publishes_after_explicit_approval(tmp_path, monkeypatch):
    engine = await _engine(tmp_path / "publish-warning.sqlite")
    async with AsyncSession(engine, expire_on_commit=False) as session:
        presentation = _presentation()
        session.add(presentation)
        await session.flush()
        slide = _slide(presentation.id)
        session.add(slide)
        await session.flush()
        task = AsyncPresentationGenerationTaskModel(
            status="completed",
            data=_preview_task_data(presentation, slide, candidate_dom="warning"),
        )
        session.add(task)
        await session.commit()

        async def fake_snapshot(*_args, **_kwargs):
            return None

        async def fake_context(_session, _presentation, _slides):
            return SimpleNamespace(), None, STYLE_REFERENCE, None

        async def fake_render(_html):
            return b"png", {"telemetry_status": "available"}

        monkeypatch.setattr(remake.version_service, "snapshot_slides", fake_snapshot)
        monkeypatch.setattr(remake, "_render_review_html", fake_render)
        monkeypatch.setattr(remake, "_authoring_context", fake_context)
        monkeypatch.setattr(
            remake, "inspect_authored_dom", lambda *_a, **_k: _report(issue=True)
        )

        result = await remake.publish_authored_page_remake(
            session=session,
            presentation_id=presentation.id,
            slide_index=0,
            preview_task_id=task.id,
        )
        await session.commit()
        await session.refresh(slide)
        await session.refresh(task)

        assert result["status"] == "published"
        assert result["post_verify_status"] == "warning"
        assert slide.html_content == CANDIDATE_HTML
        assert slide.properties["image"] == "/app_data/images/remake.png"
        assert task.data["phase"] == "published"
        assert task.data["publish"]["status"] == "published"
        assert task.data["publish"]["rollback_available"] is True
        # The stored verdict and the returned verdict must never diverge.
        assert task.data["publish"]["post_verify"] == result["post_verify_status"]
        assert task.data["publish"]["post_verify_issues"][0]["category"] == "overlap"
        assert task.data["publish"]["post_verify_telemetry"] == "available"
    await engine.dispose()


@async_test
async def test_warned_preview_still_rejects_a_stale_source_digest(
    tmp_path, monkeypatch
):
    engine = await _engine(tmp_path / "publish-warning-stale.sqlite")
    async with AsyncSession(engine, expire_on_commit=False) as session:
        presentation = _presentation()
        session.add(presentation)
        await session.flush()
        slide = _slide(presentation.id)
        session.add(slide)
        await session.flush()
        data = _preview_task_data(presentation, slide, candidate_dom="warning")
        data["source_digest"] = "sha256:stale"
        task = AsyncPresentationGenerationTaskModel(status="completed", data=data)
        session.add(task)
        await session.commit()

        async def fake_snapshot(*_args, **_kwargs):
            raise AssertionError("stale preview must not snapshot")

        monkeypatch.setattr(remake.version_service, "snapshot_slides", fake_snapshot)
        with pytest.raises(
            remake.AuthoredPageRemakeError,
            match="authored_page_remake_source_changed",
        ):
            await remake.publish_authored_page_remake(
                session=session,
                presentation_id=presentation.id,
                slide_index=0,
                preview_task_id=task.id,
            )
        await session.refresh(slide)
        assert slide.html_content == SOURCE_HTML
    await engine.dispose()
