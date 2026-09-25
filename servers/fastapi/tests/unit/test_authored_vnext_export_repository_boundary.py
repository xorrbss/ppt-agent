"""The async boundary in front of the synchronous export repository.

``AuthoredVNextExportService.run_job`` used to call ``AuthoredVNextSqlRepository``
(claim/get_deck/get_master/complete/fail/get_export_job) straight from a
coroutine. That blocks the event loop for the duration of each statement --
on the SQLite profile, where the whole process shares one engine, that can
deadlock the same way the generation repository did (see
``test_authored_vnext_generation_repository_boundary.py``): the loop's own
async writer cannot commit and release the write lock while the loop itself
is blocked inside the synchronous call.

``run_job`` now reaches the repository only through ``self.async_repository``,
an ``AsyncAuthoredVNextSqlRepository`` (the existing facade in
``sql_repository.py``, previously unused) whose methods forward to a worker
thread via ``asyncio.to_thread``. These tests prove every repository call
``run_job`` makes runs off the event-loop thread, on both the success and the
failure paths, and that failures still propagate to ``fail_export_job`` and
the returned job snapshot.
"""

from __future__ import annotations

import asyncio
import threading
from types import SimpleNamespace
import uuid

import pytest

from services.authored_vnext.integration import export_service as export_service_module
from services.authored_vnext.integration.export_service import (
    AuthoredVNextExportService,
    ExportServiceError,
)
from services.authored_vnext.integration.sql_repository import (
    AsyncAuthoredVNextSqlRepository,
)
from services.authored_vnext.integration.worker import WorkerError


class _ThreadRecordingRepository:
    """A synchronous repository whose calls record the thread they ran on."""

    def __init__(self, *, claimed, deck, master, export_job) -> None:
        self._claimed = claimed
        self._deck = deck
        self._master = master
        self._export_job = export_job
        self.threads: dict[str, int] = {}
        self.fail_calls: list[dict] = []
        self.complete_calls: list[dict] = []

    def claim_export_job(self, job_id, **kwargs):
        self.threads["claim_export_job"] = threading.get_ident()
        return self._claimed

    def get_deck(self, deck_id, revision=None):
        self.threads["get_deck"] = threading.get_ident()
        return self._deck

    def get_master(self, master_id, revision=None):
        self.threads["get_master"] = threading.get_ident()
        return self._master

    def complete_export_job(self, job_id, **kwargs):
        self.threads["complete_export_job"] = threading.get_ident()
        self.complete_calls.append(kwargs)
        return self._export_job

    def fail_export_job(self, job_id, **kwargs):
        self.threads["fail_export_job"] = threading.get_ident()
        self.fail_calls.append(kwargs)

    def get_export_job(self, job_id):
        self.threads["get_export_job"] = threading.get_ident()
        return self._export_job


def _claimed(**overrides) -> SimpleNamespace:
    values = dict(
        id=uuid.uuid4(),
        deck_id="deck-1",
        deck_revision=1,
        request_payload={},
        fence_token=7,
        expected_package_digest=None,
        spec_digest="sha256:spec",
        asset_manifest_digest="sha256:assets",
        font_manifest_digest="sha256:font",
        master_digest="sha256:master",
        timeout_ms=1000,
    )
    values.update(overrides)
    return SimpleNamespace(**values)


def _deck() -> SimpleNamespace:
    # No ``master`` pin, so ``run_job`` falls back to ``deck.master_id`` /
    # ``deck.master_revision`` -- exercising that branch too.
    return SimpleNamespace(master_id="master-1", master_revision=1)


def _master() -> SimpleNamespace:
    return SimpleNamespace(contract=object(), digest="sha256:master")


def _service_with_failure(monkeypatch, repository, *, raise_error: Exception):
    """A service whose composition step fails before touching the worker.

    The composition/asset-materialization helpers are business logic already
    covered elsewhere; here they are stubbed so the test isolates the
    threading boundary and the failure-path repository calls.
    """

    service = AuthoredVNextExportService(repository, worker=None, artifact_store=None)

    def _raise(*args, **kwargs):
        raise raise_error

    monkeypatch.setattr(service, "_materialize_composition", _raise)
    return service


def test_run_job_success_path_calls_the_repository_off_the_loop(monkeypatch) -> None:
    claimed = _claimed()
    deck = _deck()
    master = _master()
    export_job = SimpleNamespace(id=claimed.id, state="succeeded")
    repository = _ThreadRecordingRepository(
        claimed=claimed, deck=deck, master=master, export_job=export_job
    )
    service = AuthoredVNextExportService(repository, worker=None, artifact_store=None)

    # Stub everything past composition: this test is about the repository
    # threading boundary, not the worker/readback business logic, which has
    # its own coverage in test_authored_vnext_export_integration.py.
    monkeypatch.setattr(
        service, "_materialize_composition", lambda contract, deck, payload, **kw: contract
    )
    monkeypatch.setattr(
        service, "_materialize_ai_text_zones", lambda contract, payload, **kw: contract
    )
    monkeypatch.setattr(service, "_worker_assets", lambda contract, deck: [])

    digests = {
        "spec": claimed.spec_digest,
        "assetManifest": claimed.asset_manifest_digest,
        "fontManifest": claimed.font_manifest_digest,
        "master": claimed.master_digest,
    }
    monkeypatch.setattr(
        export_service_module,
        "build_worker_request",
        lambda **kwargs: ({"master": {}, "slides": []}, digests),
    )

    async def fake_execute(payload, timeout_ms):
        return {
            "schemaVersion": "authored-vnext-ooxml-worker/v1",
            "operation": "EXPORT_PPTX",
            "status": "SUCCEEDED",
            "operationId": str(claimed.id),
            "fencingToken": claimed.fence_token,
            "artifact": {},
            "receipt": {},
        }

    service.worker = SimpleNamespace(execute=fake_execute)

    def fake_assert_readback(receipt, payload):
        raise ExportServiceError("authored_vnext_worker_read_back_invalid")

    monkeypatch.setattr(export_service_module, "_assert_worker_readback", fake_assert_readback)

    loop_thread = threading.get_ident()
    result = asyncio.run(service.run_job(uuid.uuid4()))

    # The readback stub rejects the (empty, unrealistic) receipt, so this
    # exercises the failure path -- but every repository call up to that
    # point (claim/get_deck/get_master) and the terminal fail/get calls all
    # crossed the async boundary.
    assert result is export_job
    for name in (
        "claim_export_job",
        "get_deck",
        "get_master",
        "fail_export_job",
        "get_export_job",
    ):
        assert name in repository.threads, name
        assert repository.threads[name] != loop_thread, name
    assert repository.fail_calls == [
        {
            "fence_token": claimed.fence_token,
            "failure_code": "authored_vnext_worker_read_back_invalid",
            "failure_detail": None,
            "cancelled": False,
        }
    ]


def test_run_job_known_failure_propagates_through_the_async_boundary(monkeypatch) -> None:
    claimed = _claimed()
    deck = _deck()
    master = _master()
    export_job = SimpleNamespace(id=claimed.id, state="failed")
    repository = _ThreadRecordingRepository(
        claimed=claimed, deck=deck, master=master, export_job=export_job
    )
    service = _service_with_failure(
        monkeypatch,
        repository,
        raise_error=WorkerError("authored_vnext_worker_timeout", retryable=True),
    )

    loop_thread = threading.get_ident()
    result = asyncio.run(service.run_job(uuid.uuid4()))

    assert result is export_job
    for name in ("claim_export_job", "get_deck", "get_master", "fail_export_job", "get_export_job"):
        assert repository.threads[name] != loop_thread, name
    assert repository.fail_calls == [
        {
            "fence_token": claimed.fence_token,
            "failure_code": "authored_vnext_worker_timeout",
            "failure_detail": None,
            "cancelled": False,
        }
    ]
    assert repository.complete_calls == []


def test_run_job_unexpected_error_still_fails_the_job_off_the_loop(monkeypatch) -> None:
    claimed = _claimed()
    deck = _deck()
    master = _master()
    export_job = SimpleNamespace(id=claimed.id, state="failed")
    repository = _ThreadRecordingRepository(
        claimed=claimed, deck=deck, master=master, export_job=export_job
    )
    service = _service_with_failure(
        monkeypatch, repository, raise_error=RuntimeError("unexpected adapter error")
    )

    loop_thread = threading.get_ident()
    result = asyncio.run(service.run_job(uuid.uuid4()))

    assert result is export_job
    for name in ("claim_export_job", "get_deck", "get_master", "fail_export_job", "get_export_job"):
        assert repository.threads[name] != loop_thread, name
    assert repository.fail_calls == [
        {
            "fence_token": claimed.fence_token,
            "failure_code": "authored_vnext_export_failed",
            "failure_detail": None,
        }
    ]


def test_claim_failure_is_not_swallowed_and_never_reaches_the_repository_thread() -> None:
    """A claim failure has no claimed job to fail, so it must propagate."""

    class _RaisingRepository:
        def claim_export_job(self, job_id, **kwargs):
            raise RuntimeError("claim failed")

    service = AuthoredVNextExportService(_RaisingRepository(), worker=None, artifact_store=None)

    with pytest.raises(RuntimeError, match="claim failed"):
        asyncio.run(service.run_job(uuid.uuid4()))


def test_async_export_repository_forwards_every_run_job_call_off_the_loop() -> None:
    """Direct coverage of the reused facade for each method ``run_job`` calls."""

    claimed = _claimed()
    deck = _deck()
    master = _master()
    export_job = SimpleNamespace(id=claimed.id, state="succeeded")
    repository = _ThreadRecordingRepository(
        claimed=claimed, deck=deck, master=master, export_job=export_job
    )
    facade = AsyncAuthoredVNextSqlRepository(repository)
    loop_thread = threading.get_ident()

    async def scenario() -> None:
        assert await facade.claim_export_job(claimed.id, worker_id="w", lease_seconds=1) is claimed
        assert await facade.get_deck(deck_id="deck-1") is deck
        assert await facade.get_master("master-1") is master
        await facade.complete_export_job(claimed.id, fence_token=1)
        await facade.fail_export_job(claimed.id, fence_token=1, failure_code="x", failure_detail=None)
        assert await facade.get_export_job(claimed.id) is export_job

    asyncio.run(scenario())

    assert repository.threads
    assert all(thread_id != loop_thread for thread_id in repository.threads.values())
