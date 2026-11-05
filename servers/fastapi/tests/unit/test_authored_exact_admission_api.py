"""API admission coverage for the private exact-reconstruction rollout."""

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


def _authored_request(*, style: str = "private-exact") -> GeneratePresentationRequest:
    return GeneratePresentationRequest(
        content="Generate an authored deck",
        # An omitted strategy resolves to svg (CLAUDE.md, "Two generation
        # pipelines"); exact-preset admission is an authored-path concern.
        strategy="authored",
        template="authored",
        authored_style=style,
    )


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


@pytest.mark.parametrize(
    ("reason", "status", "detail"),
    [
        ("Exact reconstruction style 'private-exact' is disabled or not allow-listed", 403, "authored_exact_reconstruction_unavailable"),
        ("Exact reconstruction style 'private-exact' failed preset validation", 422, "authored_exact_reconstruction_invalid"),
    ],
)
def test_exact_admission_returns_stable_non_private_4xx(monkeypatch, reason, status, detail):
    monkeypatch.setattr(
        generation_pipeline,
        "resolve_authored_style",
        lambda _style_id: (_ for _ in ()).throw(
            AuthoredExactReconstructionUnavailableError(reason)
        ),
    )

    with pytest.raises(HTTPException) as exc:
        asyncio.run(generation_pipeline.check_if_api_request_is_valid(_authored_request(), None))

    assert exc.value.status_code == status
    assert exc.value.detail == detail
    assert "private-exact" not in exc.value.detail
    assert "source" not in exc.value.detail


def test_exact_admission_happens_before_async_job_or_background_task(monkeypatch):
    """An unadmissible bare generate must not mint a deck, job or task.

    The seam this protects used to be the removed ``force_legacy_dispatch``
    parameter. It is now the ordering inside ``queue_presentation_generation``
    itself: a request that carries no ``presentation_id`` has no workspace
    operation to project a rejection onto, so exact-preset admission runs
    before the workspace bootstrap rather than after it. A request that does
    carry one keeps the deferred ordering, so its rejection lands on the
    operation's timeline card.
    """

    monkeypatch.setattr(
        generation_pipeline,
        "resolve_authored_style",
        lambda _style_id: (_ for _ in ()).throw(
            AuthoredExactReconstructionUnavailableError(
                "Exact reconstruction style 'private-exact' is disabled or not allow-listed"
            )
        ),
    )
    session = _RecordingSession()
    tasks = _RecordingBackgroundTasks()

    with pytest.raises(HTTPException) as exc:
        asyncio.run(
            generation_pipeline.queue_presentation_generation(
                # No ``presentation_id``: nothing durable exists for this
                # request yet, which is exactly the case that must fail first.
                _authored_request(),
                tasks,
                session,
            )
        )

    assert exc.value.status_code == 403
    assert exc.value.detail == "authored_exact_reconstruction_unavailable"
    assert session.added == []
    assert session.commits == 0
    assert tasks.calls == []


def test_sync_endpoint_rejects_exact_before_generation_handler(monkeypatch):
    monkeypatch.setattr(
        generation_pipeline,
        "resolve_authored_style",
        lambda _style_id: (_ for _ in ()).throw(
            AuthoredExactReconstructionUnavailableError(
                "Exact reconstruction style 'private-exact' is disabled or not allow-listed"
            )
        ),
    )
    handler = AsyncMock()
    monkeypatch.setattr(generation_pipeline, "generate_presentation_handler", handler)
    request_http = SimpleNamespace(state=SimpleNamespace(request_id=None), headers={}, cookies={})

    with pytest.raises(HTTPException) as exc:
        asyncio.run(
            endpoint.generate_presentation_sync(
                request_http, _authored_request(), _RecordingSession()
            )
        )

    assert exc.value.status_code == 403
    assert exc.value.detail == "authored_exact_reconstruction_unavailable"
    handler.assert_not_awaited()


def test_unknown_ordinary_authored_style_keeps_default_compatibility(monkeypatch):
    resolved = []

    def fake_resolve(style_id):
        resolved.append(style_id)
        return SimpleNamespace(reference_mode="style_reference")

    monkeypatch.setattr(generation_pipeline, "resolve_authored_style", fake_resolve)
    request = _authored_request(style="typo-or-legacy-style")

    asyncio.run(generation_pipeline.check_if_api_request_is_valid(request, None))

    assert resolved == ["typo-or-legacy-style"]
    assert request.template == "authored"


def test_style_reference_request_does_not_expose_or_change_reference_contract(monkeypatch):
    resolved = []
    monkeypatch.setattr(
        generation_pipeline,
        "resolve_authored_style",
        lambda style_id: resolved.append(style_id)
        or SimpleNamespace(reference_mode="style_reference"),
    )
    request = GeneratePresentationRequest(
        content="Generate a standard authored deck",
        strategy="authored",
        template="adaptive",
        authored_style="default",
    )

    asyncio.run(generation_pipeline.check_if_api_request_is_valid(request, None))

    assert request.template == "authored"
    assert resolved == ["default"]
    assert "reference_mode" not in request.model_dump(mode="json")


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


def test_worker_revalidation_sets_safe_exact_error_status(monkeypatch):
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
    monkeypatch.setattr(generation_pipeline, "finalize_generation_job", AsyncMock())
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
    assert status.error["status_code"] == 403
    assert status.error["detail"] == "authored_exact_reconstruction_unavailable"
    assert "private-exact" not in str(status.error)


def test_sync_endpoint_refuses_svg_before_validation_or_provider_work(monkeypatch):
    # The svg driver needs the durable generation job the inline endpoint lacks.
    validate = AsyncMock()
    handler = AsyncMock()
    monkeypatch.setattr(generation_pipeline, "check_if_api_request_is_valid", validate)
    monkeypatch.setattr(generation_pipeline, "generate_presentation_handler", handler)
    request_http = SimpleNamespace(state=SimpleNamespace(request_id=None), headers={}, cookies={})
    request = GeneratePresentationRequest(content="Generate an svg deck", template="authored", strategy="svg")

    with pytest.raises(HTTPException) as exc:
        asyncio.run(endpoint.generate_presentation_sync(request_http, request, _RecordingSession()))

    assert exc.value.status_code == 422
    assert exc.value.detail == "svg_generation_requires_async_route"
    validate.assert_not_awaited()
    handler.assert_not_awaited()
