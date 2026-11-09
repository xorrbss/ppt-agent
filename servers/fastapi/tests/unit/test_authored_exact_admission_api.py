"""API admission for authored requests after the authored-generation removal.

Exact-reconstruction presets used to be admitted (or refused with a stable
4xx) on the authored path. Authored generation was removed on 2026-11-09, so
every entry point now refuses an authored request with
``authored_strategy_removed`` before any style, preset or durable work, and a
job staged with authored before the removal fails the same way in the worker.
"""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock
import uuid

import pytest
from fastapi import HTTPException

from api.v1.ppt.endpoints import presentation_generate as endpoint
from services import presentation_generation_pipeline as generation_pipeline
from models.generate_presentation_request import GeneratePresentationRequest
from utils.authored_styles import AuthoredExactReconstructionUnavailableError


def _authored_request(*, style: str = "private-exact", template: str = "authored") -> GeneratePresentationRequest:
    return GeneratePresentationRequest(
        content="Generate an authored deck",
        strategy="authored",
        template=template,
        authored_style=style,
    )


def _refuse_style_resolution(monkeypatch):
    """Fail the test if anything resolves the authored style (exact preset)."""

    def resolve(_style_id):
        raise AssertionError("authored style resolution must not run for a removed strategy")

    monkeypatch.setattr(generation_pipeline, "resolve_authored_style", resolve)


class _RecordingSession:
    def __init__(self):
        self.added = []
        self.commits = 0

    def add(self, value):
        self.added.append(value)

    async def commit(self):
        self.commits += 1


class _RecordingBackgroundTasks:
    def __init__(self):
        self.calls = []

    def add_task(self, *args, **kwargs):
        self.calls.append((args, kwargs))


def _assert_removed(error: HTTPException) -> None:
    assert error.status_code == 422
    assert error.detail == "authored_strategy_removed"


@pytest.mark.parametrize(
    "request_factory",
    [
        lambda: _authored_request(),
        lambda: _authored_request(style="typo-or-legacy-style"),
        lambda: _authored_request(style="default", template="adaptive"),
    ],
)
def test_validation_rejects_authored_before_any_style_or_preset_admission(monkeypatch, request_factory):
    _refuse_style_resolution(monkeypatch)
    with pytest.raises(HTTPException) as exc:
        asyncio.run(generation_pipeline.check_if_api_request_is_valid(request_factory(), None))
    _assert_removed(exc.value)


def test_async_admission_rejects_authored_before_any_job_or_background_task(monkeypatch):
    _refuse_style_resolution(monkeypatch)
    session = _RecordingSession()
    tasks = _RecordingBackgroundTasks()

    with pytest.raises(HTTPException) as exc:
        asyncio.run(
            generation_pipeline.queue_presentation_generation(
                _authored_request(),
                tasks,
                session,
            )
        )

    _assert_removed(exc.value)
    assert session.added == []
    assert session.commits == 0
    assert tasks.calls == []


def test_async_route_rejects_explicit_authored_with_the_removal_code(monkeypatch):
    _refuse_style_resolution(monkeypatch)
    monkeypatch.setattr(endpoint, "_require_local_legacy_async_generation", lambda *_args: None)
    session = _RecordingSession()
    session.get_bind = lambda: None
    request_http = SimpleNamespace(state=SimpleNamespace(request_id="authored-async"), headers={}, cookies={})

    with pytest.raises(HTTPException) as exc:
        asyncio.run(
            endpoint.generate_presentation_async(
                request_http, _authored_request(), _RecordingBackgroundTasks(),
                idempotency_key=None, sql_session=session,
            )
        )

    _assert_removed(exc.value)
    assert session.added == []


def test_sync_endpoint_rejects_authored_before_validation_or_generation(monkeypatch):
    validate = AsyncMock()
    handler = AsyncMock()
    monkeypatch.setattr(generation_pipeline, "check_if_api_request_is_valid", validate)
    monkeypatch.setattr(generation_pipeline, "generate_presentation_handler", handler)
    request_http = SimpleNamespace(state=SimpleNamespace(request_id=None), headers={}, cookies={})

    with pytest.raises(HTTPException) as exc:
        asyncio.run(
            endpoint.generate_presentation_sync(
                request_http, _authored_request(), _RecordingSession()
            )
        )

    _assert_removed(exc.value)
    validate.assert_not_awaited()
    handler.assert_not_awaited()


class _WorkerSession:
    def __init__(self, job, status):
        self.job = job
        self.status = status
        self.added = []
        self.commits = 0

    async def get(self, model, _id):
        if model is generation_pipeline.PresentationGenerationJob:
            return self.job
        if model is generation_pipeline.AsyncPresentationGenerationTaskModel:
            return self.status
        raise AssertionError(f"unexpected model {model}")

    def add(self, value):
        self.added.append(value)

    async def commit(self):
        self.commits += 1


class _SessionFactory:
    def __init__(self, session):
        self.session = session

    async def __aenter__(self):
        return self.session

    async def __aexit__(self, *_args):
        return False


def test_worker_fails_a_staged_authored_exact_job_with_the_removal_code(monkeypatch):
    job_id = uuid.uuid4()
    task_id = "exact-task"
    job = SimpleNamespace(
        presentation_id=uuid.uuid4(),
        request_payload=_authored_request().model_dump(mode="json"),
        request_id=None,
        template_v2_target=None,
    )
    status = SimpleNamespace(status="pending", message=None, error=None, updated_at=None)
    session = _WorkerSession(job, status)
    monkeypatch.setattr(generation_pipeline, "async_session_maker", lambda: _SessionFactory(session))
    monkeypatch.setattr(generation_pipeline, "claim_generation_job", AsyncMock(return_value=(task_id, "lease")))
    finalize = AsyncMock()
    monkeypatch.setattr(generation_pipeline, "finalize_generation_job", finalize)
    monkeypatch.setattr(generation_pipeline, "release_generation_job", AsyncMock())
    monkeypatch.setattr(
        generation_pipeline,
        "resolve_authored_style",
        lambda _style_id: (_ for _ in ()).throw(
            AuthoredExactReconstructionUnavailableError(
                "Exact reconstruction style 'private-exact' is disabled or not allow-listed"
            )
        ),
    )

    async def heartbeat(*_args):
        await asyncio.Event().wait()

    monkeypatch.setattr(generation_pipeline, "heartbeat_generation_job_loop", heartbeat)
    handler = AsyncMock()
    monkeypatch.setattr(generation_pipeline, "generate_presentation_handler", handler)

    asyncio.run(generation_pipeline.run_generation_job(job_id, None))

    handler.assert_not_awaited()
    assert status.status == "error"
    assert status.error["status_code"] == 422
    assert status.error["code"] == "authored_strategy_removed"
    assert status.error["detail"] == "authored_strategy_removed"
    assert "private-exact" not in str(status.error)
    assert finalize.await_args.kwargs == {"succeeded": False}


@pytest.mark.parametrize("strategy", ["svg", None])
def test_sync_endpoint_refuses_svg_before_validation_or_provider_work(monkeypatch, strategy):
    # The svg driver needs the durable generation job the inline endpoint
    # lacks; an omitted strategy is svg too.
    validate = AsyncMock()
    handler = AsyncMock()
    monkeypatch.setattr(generation_pipeline, "check_if_api_request_is_valid", validate)
    monkeypatch.setattr(generation_pipeline, "generate_presentation_handler", handler)
    request_http = SimpleNamespace(state=SimpleNamespace(request_id=None), headers={}, cookies={})
    request = GeneratePresentationRequest(content="Generate an svg deck", template="authored", strategy=strategy)

    with pytest.raises(HTTPException) as exc:
        asyncio.run(endpoint.generate_presentation_sync(request_http, request, _RecordingSession()))

    assert exc.value.status_code == 422
    assert exc.value.detail == "svg_generation_requires_async_route"
    validate.assert_not_awaited()
    handler.assert_not_awaited()
