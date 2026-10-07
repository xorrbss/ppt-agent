"""release-v1 receipt gate and the font_embedding admission guards.

These replace two blanket guards in ``execute_svg_workspace_export``:
``quality_policy == "release-v1"`` used to always raise
``svg_release_quality_review_not_supported``, and any ``font_embedding``
request used to always raise ``svg_font_embedding_not_supported`` -- neither
looked at the actual request or receipt content. See C1-quality.md (session
design folder) for the ``visual_review`` field contract this gate reads.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from types import SimpleNamespace
import uuid

import pytest

from services import svg_export_operation
from services.document_canvas import document_canvas_for_orientation
from services.public_export_errors import PublicExportOperationError
from services.svg_export_release_gate import require_release_v1_receipt as _require_release_v1_receipt
from services.workspace_export_quality_evidence import _legacy_fidelity_public_quality_receipt
from services.workspace_operation_handler_registry import NonRetryableWorkspaceOperationError

OWNER = uuid.uuid4()


def _passing_receipt(**overrides) -> dict:
    # The public wire dump: camelCase keys, advisory digest inside measurements.
    base = {
        "visualReview": {
            "required": True,
            "status": "passed",
            "evidence": {"measurements": {"advisoryDigest": "sha256:" + "a" * 64}},
        },
        "releaseReadiness": "ready",
    }
    base.update(overrides)
    return base


def test_require_release_v1_receipt_accepts_a_passed_advisory_with_digest():
    _require_release_v1_receipt(_passing_receipt())  # must not raise


def test_require_release_v1_receipt_rejects_missing_visual_review():
    with pytest.raises(NonRetryableWorkspaceOperationError, match="svg_release_quality_receipt_invalid"):
        _require_release_v1_receipt({"releaseReadiness": "ready"})


def test_require_release_v1_receipt_rejects_a_status_other_than_passed():
    receipt = _passing_receipt(visualReview={"required": True, "status": "failed",
                                             "reasonCode": "svg_visual_review_flagged"})
    with pytest.raises(NonRetryableWorkspaceOperationError,
                        match="svg_release_quality_visual_review_not_passed"):
        _require_release_v1_receipt(receipt)


def test_require_release_v1_receipt_rejects_a_passed_status_without_advisory_digest():
    receipt = _passing_receipt(visualReview={"required": True, "status": "passed"})
    with pytest.raises(NonRetryableWorkspaceOperationError,
                        match="svg_release_quality_advisory_evidence_missing"):
        _require_release_v1_receipt(receipt)


def test_require_release_v1_receipt_rejects_a_blocked_release_readiness():
    with pytest.raises(NonRetryableWorkspaceOperationError, match="svg_release_quality_not_release_ready"):
        _require_release_v1_receipt(_passing_receipt(releaseReadiness="blocked"))


def test_require_release_v1_receipt_reads_a_real_receipt_without_a_shape_error():
    """Regression: a hand-typed snake_case fixture (visual_review/
    release_readiness) previously matched this gate's own bug instead of
    the real wire shape, so the unit tests passed while every live request
    failed with svg_release_quality_receipt_invalid before ever reading the
    advisory status. Exercise the actual receipt builder instead."""
    receipt = _legacy_fidelity_public_quality_receipt(
        quality_receipt_id="quality:test:test",
        operation_id=str(uuid.uuid4()),
        presentation_id=str(uuid.uuid4()),
        artifact_digest="sha256:" + "a" * 64,
        source_revision=1,
        scene_digest="sha256:" + "b" * 64,
        evaluated_at=datetime.now(timezone.utc),
    )
    # The legacy-fidelity receipt never carries a passed advisory -- it must
    # reach that check, not fail the shape check first.
    with pytest.raises(NonRetryableWorkspaceOperationError) as caught:
        _require_release_v1_receipt(receipt)
    assert caught.value.code != "svg_release_quality_receipt_invalid"


def _canvas() -> dict:
    return document_canvas_for_orientation("landscape").model_dump(mode="json", by_alias=True)


def _claim(*, kind="export.pptx", request_payload=None) -> SimpleNamespace:
    return SimpleNamespace(
        operation_id=uuid.uuid4(), owner_id=OWNER, owner_scope="owner", presentation_id=uuid.uuid4(),
        kind=kind, request_payload=request_payload or {}, expected_revision=3, attempt_number=1,
        attempt_token="attempt", fencing_token=1,
    )


def _forbid(name: str):
    async def forbidden(*_args, **_kwargs):
        raise AssertionError(f"{name} ran after an early admission guard should have stopped it")

    return forbidden


def _untouchable_session_factory():
    @asynccontextmanager
    async def factory():
        raise AssertionError("no database session should open before the admission guards run")
        yield  # pragma: no cover - never reached

    return factory


def _presentation_and_admission():
    presentation = SimpleNamespace(owner_id=OWNER, revision=3, n_slides=1, theme={}, mode="svg")
    admission = SimpleNamespace(state="active", owner_id=OWNER, tenant_id="tenant")
    return presentation, admission


def test_unknown_quality_policy_is_rejected_before_any_db_access(monkeypatch):
    presentation, admission = _presentation_and_admission()
    claim = _claim(request_payload={"quality_policy": "not-a-real-policy"})
    context = SimpleNamespace(
        claim=claim, progress=_forbid("progress"), raise_if_cancelled=_forbid("raise_if_cancelled"),
    )

    with pytest.raises(NonRetryableWorkspaceOperationError, match="svg_quality_policy_invalid"):
        asyncio.run(svg_export_operation.execute_svg_workspace_export(
            context, presentation=presentation, admission=admission,
            session_factory=_untouchable_session_factory(),
        ))


@pytest.mark.parametrize("policy", [None, "interactive-v1", "release-v1"])
def test_supported_quality_policies_reach_svg_source_validation(policy):
    presentation, admission = _presentation_and_admission()
    claim = _claim(request_payload={"quality_policy": policy})
    context = SimpleNamespace(claim=claim)

    @asynccontextmanager
    async def empty_slides():
        class Session:
            async def execute(self, _statement):
                return SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: []))
        yield Session()

    # Unknown policies fail at admission; supported policies continue to the
    # actual source gate. Existing release receipt tests retain stricter release QA.
    with pytest.raises(NonRetryableWorkspaceOperationError, match="svg_export_source_required"):
        asyncio.run(svg_export_operation.execute_svg_workspace_export(
            context, presentation=presentation, admission=admission, session_factory=empty_slides))


def test_font_embedding_on_a_pdf_export_is_rejected_before_any_db_access(monkeypatch):
    presentation, admission = _presentation_and_admission()
    claim = _claim(kind="export.pdf", request_payload={"font_embedding": True})
    context = SimpleNamespace(
        claim=claim, progress=_forbid("progress"), raise_if_cancelled=_forbid("raise_if_cancelled"),
    )

    with pytest.raises(NonRetryableWorkspaceOperationError, match="svg_font_embedding_pptx_only"):
        asyncio.run(svg_export_operation.execute_svg_workspace_export(
            context, presentation=presentation, admission=admission,
            session_factory=_untouchable_session_factory(),
        ))


def test_render_and_export_svgs_rejects_font_embedding_on_pdf():
    with pytest.raises(PublicExportOperationError, match="svg_font_embedding_pptx_only"):
        asyncio.run(svg_export_operation.render_and_export_svgs(
            _claim(kind="export.pdf"), tenant_id="tenant", svgs=["<svg/>"], notes=[""],
            export_format="pdf", canvas=_canvas(), session_factory=_untouchable_session_factory(),
            font_embedding=True,
        ))
