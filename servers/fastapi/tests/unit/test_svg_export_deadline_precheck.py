"""An SVG export whose pinned admission deadline has passed does no preparation.

Before this check the SVG path loaded the deadline grant only after deriving
every asset, persisting the bundle and staging it into the worker cache, so an
expired operation spent minutes preparing on every retry while the exports
queued behind it waited.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from datetime import timedelta
from types import SimpleNamespace
import uuid

import pytest

from services import svg_export_operation
from services.document_canvas import document_canvas_for_orientation
from services.public_export_deadline import PublicExportDeadlineGrant
from services.public_export_errors import PublicExportOperationError
from utils.datetime_utils import get_current_utc_datetime

DEADLINE_CODE = "public_export_admission_deadline_exhausted"
OWNER = uuid.uuid4()


def _expired_grant() -> PublicExportDeadlineGrant:
    return PublicExportDeadlineGrant(deadline_at=get_current_utc_datetime() - timedelta(seconds=5))


def _canvas() -> dict:
    return document_canvas_for_orientation("landscape").model_dump(mode="json", by_alias=True)


def _claim() -> SimpleNamespace:
    return SimpleNamespace(
        operation_id=uuid.uuid4(), owner_id=OWNER, owner_scope="owner", presentation_id=uuid.uuid4(),
        kind="export.pptx", request_payload={}, expected_revision=3, attempt_number=1,
        attempt_token="attempt", fencing_token=1,
    )


def _forbid(name: str):
    async def forbidden(*_args, **_kwargs):
        raise AssertionError(f"{name} ran after the deadline had passed")

    return forbidden


def _session_factory(slides):
    class _Rows:
        def scalars(self):
            return self

        def all(self):
            return slides

    class _Session:
        async def execute(self, _statement):
            return _Rows()

        async def get(self, _model, _key):
            return SimpleNamespace(request_hash="r" * 64, idempotency_key_hash="i" * 64)

        async def commit(self):
            return None

    @asynccontextmanager
    async def factory():
        yield _Session()

    return factory


def test_expired_admission_fails_before_asset_preparation(monkeypatch) -> None:
    import services.presentation_export_attempt_service as attempts
    import services.svg_export_assets as svg_assets

    slide = SimpleNamespace(
        index=0, source_format="svg", svg_content="<svg/>", speaker_note="",
        content={"svgAssetManifest": {"sourceManifest": {}}},
    )
    presentation = SimpleNamespace(owner_id=OWNER, revision=3, n_slides=1, theme={}, mode="svg")
    admission = SimpleNamespace(state="active", owner_id=OWNER, tenant_id="tenant")
    claim = _claim()
    terminalized: list[str] = []

    async def begin_export_attempt(*_args, **_kwargs):
        return SimpleNamespace(id=uuid.uuid4())

    async def terminalize(_session, **kwargs):
        terminalized.append(kwargs["failure_code"])

    async def load_grant(*_args, **_kwargs):
        return _expired_grant()

    async def progress(**_kwargs):
        raise AssertionError("progress reported after the deadline had passed")

    monkeypatch.setattr(attempts, "begin_export_attempt", begin_export_attempt)
    monkeypatch.setattr(attempts, "terminalize_export_attempt_failed_if_current_workspace_claim", terminalize)
    monkeypatch.setattr(svg_export_operation, "load_public_export_deadline_grant", load_grant)
    monkeypatch.setattr(svg_assets, "build_slide_export_assets", _forbid("build_slide_export_assets"))
    monkeypatch.setattr(svg_export_operation, "render_and_export_svgs", _forbid("render_and_export_svgs"))
    monkeypatch.setattr(svg_export_operation, "configured_public_artifact_service", _forbid("artifact service"))
    context = SimpleNamespace(claim=claim, progress=progress, raise_if_cancelled=_forbid("raise_if_cancelled"))

    with pytest.raises(PublicExportOperationError, match=DEADLINE_CODE):
        asyncio.run(svg_export_operation.execute_svg_workspace_export(
            context, presentation=presentation, admission=admission,
            session_factory=_session_factory([slide]),
        ))
    # The attempt row keeps the real code for the operator.
    assert terminalized == [DEADLINE_CODE]


def test_render_and_export_rejects_an_expired_grant_before_persisting(monkeypatch) -> None:
    monkeypatch.setattr(svg_export_operation, "configured_public_artifact_service", _forbid("artifact service"))
    monkeypatch.setattr(svg_export_operation, "configured_worker_artifact_cache", _forbid("worker cache"))
    monkeypatch.setattr(svg_export_operation, "execute_isolated_export_operation", _forbid("export worker"))

    with pytest.raises(PublicExportOperationError, match=DEADLINE_CODE):
        asyncio.run(svg_export_operation.render_and_export_svgs(
            _claim(), tenant_id="tenant", svgs=["<svg/>"], notes=[""], export_format="pptx",
            canvas=_canvas(), session_factory=_session_factory([]), asset_bindings=[{"bound": True}],
            deadline_grant=_expired_grant(),
        ))


class _ExpiringGrant:
    """A grant that runs out after ``budget`` checks, i.e. mid-preparation."""

    def __init__(self, budget: int) -> None:
        self.budget = budget

    def export_worker_seconds(self) -> int:
        self.budget -= 1
        if self.budget < 0:
            raise PublicExportOperationError(DEADLINE_CODE)
        return 60


def test_deadline_passing_between_staged_objects_stops_staging(monkeypatch) -> None:
    persisted: list[str] = []
    staged: list[str] = []

    class _Service:
        async def persist_bytes(self, _session, *, kind, **_kwargs):
            persisted.append(kind)
            return SimpleNamespace(kind=kind, sha256="a" * 64)

        async def stream_ref_to_object_store(self, reference, _cache):
            staged.append(reference.kind)

    class _Cache:
        async def maintain(self):
            return None

        @asynccontextmanager
        async def maintenance_batch(self):
            yield

    monkeypatch.setattr(svg_export_operation, "configured_public_artifact_service", lambda: _Service())
    monkeypatch.setattr(svg_export_operation, "configured_worker_artifact_cache", lambda: _Cache())
    monkeypatch.setattr(svg_export_operation, "_worker_ref", lambda ref: SimpleNamespace(to_wire=lambda: {}))
    monkeypatch.setattr(svg_export_operation, "execute_isolated_export_operation", _forbid("export worker"))
    import services.svg_export_assets as svg_assets

    async def prepare_export_assets(*_args, **_kwargs):
        return {"sourceManifestDigest": "s", "placementManifestDigest": "p"}, []

    monkeypatch.setattr(svg_assets, "prepare_export_assets", prepare_export_assets)
    canvas = _canvas()

    # Checks: entry, 2 per-slide, pre-staging, then one per staged object.
    with pytest.raises(PublicExportOperationError, match=DEADLINE_CODE):
        asyncio.run(svg_export_operation.render_and_export_svgs(
            _claim(), tenant_id="tenant", svgs=["<svg/>", "<svg/>"], notes=["", ""], export_format="pptx",
            canvas=canvas, session_factory=_session_factory([]), asset_bindings=[{"a": 1}, {"b": 2}],
            deadline_grant=_ExpiringGrant(budget=5),
        ))
    assert persisted == ["slide-svg", "slide-svg", "export-bundle"]
    assert staged == ["slide-svg"]
