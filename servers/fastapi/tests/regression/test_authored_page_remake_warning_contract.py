"""Contract regression for the one-page remake DOM-quality *warning* path.

A candidate that generates and renders successfully must reach the user even
when the deterministic DOM sensor flags something.  Only generation, render and
integrity violations may end the task as ``error``.  These tests are written
against that contract independently of the service implementation.
"""

import asyncio
import json
import uuid
from functools import wraps
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import BackgroundTasks
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlmodel import SQLModel

from models.presentation_from_template import AuthoredPageRemakePreviewRequest
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
from services.workspace_operation_runner import sanitize_workspace_event_payload
from utils.authored_dom_quality import (
    STYLE_REFERENCE,
    DomQualityIssue,
    DomQualityReport,
)

SOURCE_HTML = "<html><body><main data-qid='source'>Source</main></body></html>"
CANDIDATE_HTML = "<html><body><main data-qid='candidate'>Candidate</main></body></html>"
CANDIDATE_IMAGE = "/app_data/images/remake-candidate.png"


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


def _presentation(slide_count: int = 1) -> PresentationModel:
    outline = PresentationOutlineModel(
        slides=[
            SlideOutlineModel(content=f"Keep all facts {index}")
            for index in range(slide_count)
        ]
    )
    return PresentationModel(
        content="Deck",
        n_slides=slide_count,
        language="Korean",
        title="Deck",
        mode="authored",
        outlines=outline.model_dump(mode="json"),
        theme={"style": "default"},
    )


def _slide(presentation_id, index: int = 0) -> SlideModel:
    return SlideModel(
        presentation=presentation_id,
        layout_group="authored",
        layout="authored:content",
        index=index,
        content={"__authored__": True, "role": "content", "image": f"old-{index}.png"},
        html_content=f"{SOURCE_HTML}<!--{index}-->",
        properties={
            "image": f"old-{index}.png",
            "slidespec_active": {"bundle_id": f"old-{index}"},
        },
    )


def _issue(category: str = "overlap", detail: str = "summary overlaps cards"):
    return DomQualityIssue(
        category=category,
        severity="high",
        detail=detail,
        qids=("summary", "card"),
    )


def _report(*, issues=(), telemetry: str = "available") -> DomQualityReport:
    return DomQualityReport(
        reference_mode=STYLE_REFERENCE,
        issues=tuple(issues),
        compact_dom=(),
        inspected_elements=2,
        telemetry_status=telemetry,
    )


class _BoundSessionMaker:
    """Run the service's background boundary on the open test session."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    def __call__(self) -> "_BoundSessionMaker":
        return self

    async def __aenter__(self) -> AsyncSession:
        return self._session

    async def __aexit__(self, *_exc) -> bool:
        return False


async def _seed(session: AsyncSession, slide_count: int = 1):
    presentation = _presentation(slide_count)
    session.add(presentation)
    await session.flush()
    slides = [_slide(presentation.id, index) for index in range(slide_count)]
    task = AsyncPresentationGenerationTaskModel(status="pending")
    session.add_all([*slides, task])
    await session.commit()
    return presentation, slides, task


def _install_preview_stubs(
    monkeypatch,
    session: AsyncSession,
    *,
    reports,
    render=None,
    revise=None,
    slide_count: int = 1,
):
    """Deterministic doubles for every remote dependency of one preview run."""

    async def fake_context(_session, _presentation, _slides):
        return (
            SimpleNamespace(
                roles=tuple("content" for _ in range(slide_count)),
                brand=SimpleNamespace(),
                design_system="design",
                reference_images=tuple(() for _ in range(slide_count)),
            ),
            None,
            STYLE_REFERENCE,
            None,
        )

    async def default_render(_html):
        return b"png", {"telemetry_status": "available"}

    async def default_revise(*_args, **_kwargs):
        return (
            [CANDIDATE_HTML],
            [b"candidate-png"],
            [0],
            [{"path": "full_fallback", "succeeded": True}],
        )

    snapshots: list = []

    async def fake_snapshot(_session, _presentation_id, slides, **kwargs):
        snapshots.append((slides, kwargs))

    pending = iter(reports)
    monkeypatch.setattr(remake, "_authoring_context", fake_context)
    monkeypatch.setattr(remake, "_render_review_html", render or default_render)
    monkeypatch.setattr(remake, "revise_authored_deck", revise or default_revise)
    monkeypatch.setattr(remake, "inspect_authored_dom", lambda *_a, **_k: next(pending))
    monkeypatch.setattr(
        remake, "publish_reviewed_png", lambda *_a, **_k: CANDIDATE_IMAGE
    )
    monkeypatch.setattr(remake.version_service, "snapshot_slides", fake_snapshot)
    monkeypatch.setattr(remake, "async_session_maker", _BoundSessionMaker(session))
    return snapshots


async def _run_preview(
    session: AsyncSession,
    presentation: PresentationModel,
    task: AsyncPresentationGenerationTaskModel,
    slide_index: int = 0,
) -> None:
    await remake._run_preview_background(
        presentation.id,
        slide_index,
        AuthoredPageRemakePreviewRequest(),
        task.id,
    )
    await session.refresh(task)


def _preview_task_data(slide, *, candidate_dom: str, issues) -> dict:
    return {
        "kind": remake.PREVIEW_KIND,
        "phase": "preview_ready",
        "presentation_id": str(slide.presentation),
        "slide_id": str(slide.id),
        "slide_index": slide.index,
        "source_digest": remake.authored_page_source_digest(slide),
        "candidate_digest": "sha256:candidate",
        "candidate_html": CANDIDATE_HTML,
        "candidate_image": CANDIDATE_IMAGE,
        "baseline_issues": [],
        "candidate_issues": issues,
        "validation": {
            "source_render": "passed",
            "candidate_render": "passed",
            "candidate_dom": candidate_dom,
            "candidate_dom_telemetry": "available",
            "reference_mode": STYLE_REFERENCE,
        },
        "publish": {"status": "not_started", "rollback_available": False},
    }


# --------------------------------------------------------------------------
# Preview: quality findings are advisory
# --------------------------------------------------------------------------


@async_test
async def test_clean_candidate_completes_with_passed_dom(tmp_path, monkeypatch):
    engine = await _engine(tmp_path / "clean.sqlite")
    async with AsyncSession(engine, expire_on_commit=False) as session:
        presentation, _slides, task = await _seed(session)
        _install_preview_stubs(
            monkeypatch,
            session,
            reports=(_report(issues=(_issue(),)), _report()),
        )

        await _run_preview(session, presentation, task)

        assert task.status == "completed"
        assert task.data["phase"] == "preview_ready"
        assert task.data["validation"]["candidate_dom"] == "passed"
        assert task.data["candidate_issues"] == []
    await engine.dispose()


@async_test
async def test_validation_reports_candidate_dom_telemetry_status(tmp_path, monkeypatch):
    engine = await _engine(tmp_path / "telemetry.sqlite")
    async with AsyncSession(engine, expire_on_commit=False) as session:
        presentation, _slides, task = await _seed(session)
        _install_preview_stubs(
            monkeypatch, session, reports=(_report(), _report())
        )

        await _run_preview(session, presentation, task)

        assert task.status == "completed"
        assert task.data["validation"]["candidate_dom_telemetry"] == "available"
    await engine.dispose()


@async_test
async def test_candidate_with_dom_issues_completes_as_warning(tmp_path, monkeypatch):
    engine = await _engine(tmp_path / "warning.sqlite")
    async with AsyncSession(engine, expire_on_commit=False) as session:
        presentation, _slides, task = await _seed(session)
        candidate_issue = _issue(detail="card overlaps the summary block")
        _install_preview_stubs(
            monkeypatch,
            session,
            reports=(_report(issues=(_issue(),)), _report(issues=(candidate_issue,))),
        )

        await _run_preview(session, presentation, task)

        assert task.status == "completed", task.error
        assert task.data["kind"] == remake.PREVIEW_KIND
        assert task.data["phase"] == "preview_ready"
        assert task.data["validation"]["candidate_dom"] == "warning"
        assert task.data["validation"]["candidate_render"] == "passed"
        assert task.data["candidate_issues"] == [candidate_issue.to_dict()]
        assert task.data["baseline_issues"][0]["category"] == "overlap"
    await engine.dispose()


@async_test
async def test_warning_candidate_carries_html_and_image(tmp_path, monkeypatch):
    engine = await _engine(tmp_path / "warning-payload.sqlite")
    async with AsyncSession(engine, expire_on_commit=False) as session:
        presentation, _slides, task = await _seed(session)
        _install_preview_stubs(
            monkeypatch,
            session,
            reports=(_report(), _report(issues=(_issue(category="clipping"),))),
        )

        await _run_preview(session, presentation, task)

        assert task.status == "completed", task.error
        assert task.data["candidate_html"] == CANDIDATE_HTML
        assert task.data["candidate_image"] == CANDIDATE_IMAGE
        assert task.data["candidate_digest"].startswith("sha256:")
        assert task.data["publish"] == {
            "status": "not_started",
            "rollback_available": False,
        }
    await engine.dispose()


@async_test
async def test_warning_preview_does_not_mutate_source_slide(tmp_path, monkeypatch):
    engine = await _engine(tmp_path / "warning-detached.sqlite")
    async with AsyncSession(engine, expire_on_commit=False) as session:
        presentation, slides, task = await _seed(session)
        source_html = slides[0].html_content
        source_properties = dict(slides[0].properties)
        snapshots = _install_preview_stubs(
            monkeypatch,
            session,
            reports=(_report(), _report(issues=(_issue(category="overflow"),))),
        )

        await _run_preview(session, presentation, task)
        await session.refresh(slides[0])

        assert slides[0].html_content == source_html
        assert slides[0].properties == source_properties
        assert slides[0].content["image"] == "old-0.png"
        assert snapshots == []
        assert task.status == "completed", task.error
    await engine.dispose()


@async_test
async def test_unavailable_telemetry_without_issues_is_warning(tmp_path, monkeypatch):
    engine = await _engine(tmp_path / "telemetry-unavailable.sqlite")
    async with AsyncSession(engine, expire_on_commit=False) as session:
        presentation, _slides, task = await _seed(session)
        _install_preview_stubs(
            monkeypatch,
            session,
            reports=(_report(), _report(telemetry="unavailable")),
        )

        await _run_preview(session, presentation, task)

        assert task.status == "completed", task.error
        assert task.data["validation"]["candidate_dom"] == "warning"
        assert task.data["validation"]["candidate_dom_telemetry"] == "unavailable"
        # A telemetry-only warning must stay explainable: no synthetic issue is
        # invented, so the UI has to read the telemetry field to explain itself.
        assert task.data["candidate_issues"] == []
    await engine.dispose()


@async_test
async def test_incomplete_telemetry_without_issues_is_warning(tmp_path, monkeypatch):
    engine = await _engine(tmp_path / "telemetry-incomplete.sqlite")
    async with AsyncSession(engine, expire_on_commit=False) as session:
        presentation, _slides, task = await _seed(session)
        _install_preview_stubs(
            monkeypatch,
            session,
            reports=(_report(), _report(telemetry="incomplete")),
        )

        await _run_preview(session, presentation, task)

        assert task.status == "completed", task.error
        assert task.data["validation"]["candidate_dom"] == "warning"
        assert task.data["validation"]["candidate_dom_telemetry"] == "incomplete"
    await engine.dispose()


# --------------------------------------------------------------------------
# Preview: real failures stay fatal
# --------------------------------------------------------------------------


@async_test
async def test_candidate_render_failure_remains_fatal(tmp_path, monkeypatch):
    engine = await _engine(tmp_path / "render-fail.sqlite")
    async with AsyncSession(engine, expire_on_commit=False) as session:
        presentation, slides, task = await _seed(session)
        calls = {"n": 0}

        async def flaky_render(_html):
            calls["n"] += 1
            if calls["n"] == 1:
                return b"png", {"telemetry_status": "available"}
            raise RuntimeError("chromium crashed")

        _install_preview_stubs(
            monkeypatch, session, reports=(_report(),), render=flaky_render
        )

        await _run_preview(session, presentation, task)
        await session.refresh(slides[0])

        assert task.status == "error"
        assert task.error["detail"] == "authored_page_remake_candidate_render_failed"
        assert task.error["stage"] == "candidate_render"
        assert task.error["retryable"] is True
        assert task.data is None
        assert slides[0].html_content.startswith(SOURCE_HTML)
    await engine.dispose()


@async_test
async def test_generation_failure_remains_fatal(tmp_path, monkeypatch):
    engine = await _engine(tmp_path / "generation-fail.sqlite")
    async with AsyncSession(engine, expire_on_commit=False) as session:
        presentation, _slides, task = await _seed(session)

        async def broken_revise(*_args, **_kwargs):
            raise RuntimeError("provider refused")

        _install_preview_stubs(
            monkeypatch, session, reports=(_report(),), revise=broken_revise
        )

        await _run_preview(session, presentation, task)

        assert task.status == "error"
        assert task.error["detail"] == "authored_page_remake_generation_failed"
        assert task.error["stage"] == "generation"
    await engine.dispose()


@async_test
async def test_identical_candidate_is_reported_not_hidden(tmp_path, monkeypatch):
    """An already-remade page can legitimately re-author to the same HTML.

    That is a result the user should see and judge, not a failure: the flow must
    complete and label the outcome instead of discarding the candidate.
    """

    engine = await _engine(tmp_path / "identical.sqlite")
    async with AsyncSession(engine, expire_on_commit=False) as session:
        presentation, slides, task = await _seed(session)
        source_html = slides[0].html_content

        async def echo_revise(*_args, **_kwargs):
            return ([source_html], [b"png"], [0], [{"attempted": True,
                                                    "succeeded": True}])

        _install_preview_stubs(
            monkeypatch,
            session,
            reports=(_report(), _report()),
            revise=echo_revise,
        )

        await _run_preview(session, presentation, task)

        assert task.status == "completed"
        assert task.data["phase"] == "preview_ready"
        assert task.data["validation"]["candidate_change"] == "unchanged"
        assert task.data["candidate_html"] == source_html
        assert task.data["candidate_image"]
    await engine.dispose()


@async_test
async def test_changed_candidate_is_labelled_changed(tmp_path, monkeypatch):
    engine = await _engine(tmp_path / "changed.sqlite")
    async with AsyncSession(engine, expire_on_commit=False) as session:
        presentation, _slides, task = await _seed(session)
        _install_preview_stubs(
            monkeypatch, session, reports=(_report(), _report())
        )

        await _run_preview(session, presentation, task)

        assert task.status == "completed"
        assert task.data["validation"]["candidate_change"] == "changed"
    await engine.dispose()


@async_test
async def test_swallowed_model_failure_is_retryable_not_invalid(
    tmp_path, monkeypatch
):
    """A transport/model outage must not read as a permanently bad candidate.

    revise_authored_deck catches the failure and returns the source unchanged
    with changed=[]; that is transient and the user should be told to retry.
    """

    engine = await _engine(tmp_path / "outage.sqlite")
    async with AsyncSession(engine, expire_on_commit=False) as session:
        presentation, slides, task = await _seed(session)
        source_html = slides[0].html_content

        async def failed_revise(*_args, **_kwargs):
            return (
                [source_html],
                [b"png"],
                [],
                [{"attempted": True, "succeeded": False,
                  "failure_detail": "LLMConnectionError: 503"}],
            )

        _install_preview_stubs(
            monkeypatch, session, reports=(_report(),), revise=failed_revise
        )

        await _run_preview(session, presentation, task)

        assert task.status == "error"
        assert task.error["detail"] == "authored_page_remake_generation_failed"
        assert task.error["stage"] == "generation"
        assert task.error["retryable"] is True
        # The upstream failure text must never reach the client payload.
        assert "LLMConnectionError" not in json.dumps(task.error)
    await engine.dispose()


@async_test
async def test_error_payload_never_carries_candidate_html(tmp_path, monkeypatch):
    engine = await _engine(tmp_path / "error-leak.sqlite")
    async with AsyncSession(engine, expire_on_commit=False) as session:
        presentation, _slides, task = await _seed(session)

        async def leaky_revise(*_args, **_kwargs):
            raise RuntimeError(f"model emitted {CANDIDATE_HTML}")

        _install_preview_stubs(
            monkeypatch, session, reports=(_report(),), revise=leaky_revise
        )

        await _run_preview(session, presentation, task)

        assert task.status == "error"
        assert set(task.error) == {"detail", "stage", "retryable"}
        assert "<html" not in str(task.error)
        assert "<html" not in (task.message or "")
    await engine.dispose()


# --------------------------------------------------------------------------
# Index / page-number conversion
# --------------------------------------------------------------------------


@async_test
async def test_preview_page_number_is_one_based(tmp_path, monkeypatch):
    engine = await _engine(tmp_path / "index.sqlite")
    async with AsyncSession(engine, expire_on_commit=False) as session:
        presentation, slides, task = await _seed(session, slide_count=3)
        _install_preview_stubs(
            monkeypatch, session, reports=(_report(), _report()), slide_count=3
        )

        background = BackgroundTasks()
        request = AuthoredPageRemakePreviewRequest()
        queued = await remake.queue_authored_page_remake_preview(
            presentation.id, 2, request, background, session
        )
        assert "3페이지" in queued.message
        assert background.tasks[0].args[1] == 2

        await _run_preview(session, presentation, task, slide_index=2)

        assert task.status == "completed", task.error
        assert task.data["slide_index"] == 2
        assert task.data["slide_id"] == str(slides[2].id)
        assert "3페이지" in task.message
    await engine.dispose()


# --------------------------------------------------------------------------
# Publish
# --------------------------------------------------------------------------


async def _publish(session, presentation, task, slide_index, monkeypatch, report):
    async def fake_context(_session, _presentation, _slides):
        return SimpleNamespace(), None, STYLE_REFERENCE, None

    async def fake_render(_html):
        return b"png", {"telemetry_status": "available"}

    snapshots: list = []

    async def fake_snapshot(_session, _presentation_id, slides, **kwargs):
        snapshots.append((slides, kwargs))

    monkeypatch.setattr(remake, "_authoring_context", fake_context)
    monkeypatch.setattr(remake, "_render_review_html", fake_render)
    monkeypatch.setattr(remake, "inspect_authored_dom", lambda *_a, **_k: report)
    monkeypatch.setattr(remake.version_service, "snapshot_slides", fake_snapshot)
    result = await remake.publish_authored_page_remake(
        session=session,
        presentation_id=presentation.id,
        slide_index=slide_index,
        preview_task_id=task.id,
    )
    await session.commit()
    return result, snapshots


@async_test
async def test_warning_candidate_publishes_only_target_slide(tmp_path, monkeypatch):
    engine = await _engine(tmp_path / "publish-warning.sqlite")
    async with AsyncSession(engine, expire_on_commit=False) as session:
        presentation, slides, _pending = await _seed(session, slide_count=2)
        issues = [_issue(category="contrast", detail="low contrast label").to_dict()]
        task = AsyncPresentationGenerationTaskModel(
            status="completed",
            data=_preview_task_data(slides[1], candidate_dom="warning", issues=issues),
        )
        session.add(task)
        await session.commit()
        untouched_html = slides[0].html_content

        result, snapshots = await _publish(
            session,
            presentation,
            task,
            1,
            monkeypatch,
            _report(issues=(_issue(category="contrast", detail="low contrast label"),)),
        )
        await session.refresh(slides[0])
        await session.refresh(slides[1])
        await session.refresh(task)

        assert result["status"] == "published"
        assert result["slide_index"] == 1
        assert result["canvas_refresh_slide_index"] == 1
        assert slides[1].html_content == CANDIDATE_HTML
        assert slides[1].properties["image"] == CANDIDATE_IMAGE
        assert "slidespec_active" not in slides[1].properties
        assert slides[0].html_content == untouched_html
        assert slides[0].properties["image"] == "old-0.png"
        assert task.data["phase"] == "published"
        assert task.data["publish"]["post_verify"] == "warning"
        assert task.data["publish"]["rollback_available"] is True
        # The result card and the task must never disagree about post-verify.
        assert result["post_verify_status"] == task.data["publish"]["post_verify"]
        assert len(snapshots) == 1
        assert snapshots[0][1]["label"] == "페이지 다시 만들기: 2"
    await engine.dispose()


@async_test
async def test_publish_post_verify_render_failure_still_blocks(tmp_path, monkeypatch):
    engine = await _engine(tmp_path / "publish-render-fail.sqlite")
    async with AsyncSession(engine, expire_on_commit=False) as session:
        presentation, slides, _pending = await _seed(session)
        task = AsyncPresentationGenerationTaskModel(
            status="completed",
            data=_preview_task_data(slides[0], candidate_dom="warning", issues=[]),
        )
        session.add(task)
        await session.commit()
        snapshot_called = False

        async def broken_render(_html):
            raise RuntimeError("chromium crashed")

        async def fake_snapshot(*_args, **_kwargs):
            nonlocal snapshot_called
            snapshot_called = True

        monkeypatch.setattr(remake, "_render_review_html", broken_render)
        monkeypatch.setattr(remake.version_service, "snapshot_slides", fake_snapshot)

        with pytest.raises(
            remake.AuthoredPageRemakeError,
            match="authored_page_remake_post_verify_failed",
        ):
            await remake.publish_authored_page_remake(
                session=session,
                presentation_id=presentation.id,
                slide_index=0,
                preview_task_id=task.id,
            )
        await session.rollback()
        await session.refresh(slides[0])

        assert snapshot_called is False
        assert slides[0].html_content.startswith(SOURCE_HTML)
    await engine.dispose()


@async_test
async def test_publish_rejects_stale_source_digest(tmp_path, monkeypatch):
    engine = await _engine(tmp_path / "publish-stale.sqlite")
    async with AsyncSession(engine, expire_on_commit=False) as session:
        presentation, slides, _pending = await _seed(session)
        data = _preview_task_data(slides[0], candidate_dom="warning", issues=[])
        data["source_digest"] = "sha256:stale"
        task = AsyncPresentationGenerationTaskModel(status="completed", data=data)
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
        await session.rollback()
        await session.refresh(slides[0])

        assert snapshot_called is False
        assert slides[0].html_content.startswith(SOURCE_HTML)
    await engine.dispose()


@async_test
async def test_publish_rejects_slide_index_mismatch(tmp_path, monkeypatch):
    engine = await _engine(tmp_path / "publish-mismatch.sqlite")
    async with AsyncSession(engine, expire_on_commit=False) as session:
        presentation, slides, _pending = await _seed(session, slide_count=2)
        task = AsyncPresentationGenerationTaskModel(
            status="completed",
            data=_preview_task_data(slides[0], candidate_dom="warning", issues=[]),
        )
        session.add(task)
        await session.commit()

        with pytest.raises(
            remake.AuthoredPageRemakeError,
            match="authored_page_remake_preview_target_mismatch",
        ):
            await remake.publish_authored_page_remake(
                session=session,
                presentation_id=presentation.id,
                slide_index=1,
                preview_task_id=task.id,
            )
        await session.rollback()
        await session.refresh(slides[1])

        assert slides[1].html_content.startswith(SOURCE_HTML)
    await engine.dispose()


@async_test
async def test_published_preview_task_cannot_be_replayed(tmp_path, monkeypatch):
    engine = await _engine(tmp_path / "publish-replay.sqlite")
    async with AsyncSession(engine, expire_on_commit=False) as session:
        presentation, slides, _pending = await _seed(session)
        data = _preview_task_data(slides[0], candidate_dom="warning", issues=[])
        data["phase"] = "published"
        task = AsyncPresentationGenerationTaskModel(status="completed", data=data)
        session.add(task)
        await session.commit()

        with pytest.raises(
            remake.AuthoredPageRemakeError,
            match="authored_page_remake_preview_target_mismatch",
        ):
            await remake.publish_authored_page_remake(
                session=session,
                presentation_id=presentation.id,
                slide_index=0,
                preview_task_id=task.id,
            )
    await engine.dispose()


# --------------------------------------------------------------------------
# Workspace event leak surface
# --------------------------------------------------------------------------


def test_workspace_event_payload_drops_candidate_material():
    safe = sanitize_workspace_event_payload(
        {
            "kind": "authored_page_remake",
            "status": "published",
            "presentation_id": str(uuid.uuid4()),
            "slide_index": 0,
            "candidate_html": CANDIDATE_HTML,
            "candidate_image": CANDIDATE_IMAGE,
            "candidate_digest": "sha256:candidate",
            "source_digest": "sha256:source",
            "post_verify_status": "warning",
            "result_revision": 4,
        },
        operation_kind="slide.regenerate",
    )

    assert "candidate_html" not in safe
    assert "candidate_image" not in safe
    assert "candidate_digest" not in safe
    assert "source_digest" not in safe
    assert safe["status"] == "published"
    assert safe["result_revision"] == 4
