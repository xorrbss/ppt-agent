"""Composer publication: the generation lane's durable CAS versus the presentation-only lane.

``publish_composer_deck`` runs inside ``public-operation/v2`` generation
operations whose handler requires the generation to complete the operation
itself (``require_public_generation_publication``).  These tests replace every
external helper with fakes and check which CAS the Composer drives in each
lane, and how helper failures map onto ``ComposerPublicationError``.
"""

from __future__ import annotations

import asyncio
import uuid
from types import SimpleNamespace
from typing import Any

import pytest

from models.sql.presentation import PresentationModel
from models.sql.workspace import WorkspaceOperation
from services.composer import publish as publish_module
from services.composer.plan import (
    ComposerDeckPlan,
    ComposerSlidePlan,
    ComposerSlideResult,
    ComposerTokens,
    FitParameters,
    FitReceipt,
)
from services.composer.publish import publish_composer_deck
from services.composer.receipt import ComposerPublicationError
from services.workspace_artifact_publication_service import WorkspaceArtifactPublicationError

EXPECTED_REVISION = 3
ATTEMPT_TOKEN = "attempt-1"
FENCING_TOKEN = 7


class FakeSession:
    def __init__(self, *, presentation: Any, operation: Any) -> None:
        self.presentation = presentation
        self.operation = operation
        self.added: list[Any] = []
        self.commits = 0
        self.rollbacks = 0
        self.flushes = 0

    async def get(self, model: Any, identity: Any) -> Any:
        if model is PresentationModel:
            return self.presentation
        if model is WorkspaceOperation:
            return self.operation
        raise AssertionError(f"unexpected get({model!r})")

    async def scalar(self, statement: Any) -> Any:
        return None

    def add(self, item: Any) -> None:
        self.added.append(item)

    def add_all(self, items: Any) -> None:
        self.added.extend(items)

    async def flush(self) -> None:
        self.flushes += 1

    async def commit(self) -> None:
        self.commits += 1

    async def rollback(self) -> None:
        self.rollbacks += 1


class Calls:
    """Every fake records its keyword arguments here, keyed by helper name."""

    def __init__(self) -> None:
        self.by_name: dict[str, list[dict[str, Any]]] = {}

    def record(self, name: str, **kwargs: Any) -> None:
        self.by_name.setdefault(name, []).append(kwargs)

    def __getitem__(self, name: str) -> list[dict[str, Any]]:
        return self.by_name.get(name, [])


def _deck(count: int) -> tuple[ComposerDeckPlan, list[ComposerSlideResult]]:
    plans = [
        ComposerSlidePlan(slide_index=index, source_unit_id=f"u{index + 1}", archetype="text")
        for index in range(count)
    ]
    tokens = ComposerTokens(
        background="#FFFFFF", surface="#F4F6F8", text_primary="#111827", text_secondary="#4B5563",
        accent="#0F62FE", accent_soft="#D0E2FF", rule="#E5E7EB", deck_title="Composer deck",
    )
    results = [
        ComposerSlideResult(
            slide_index=index,
            html=f"<html><body>slide {index}</body></html>",
            receipt=FitReceipt(
                slide_index=index, rung=1, parameters=FitParameters(rung=1), renders=2, estimate_used=False
            ),
        )
        for index in range(count)
    ]
    return ComposerDeckPlan(slides=plans, tokens=tokens), results


def _presentation(owner_id: uuid.UUID) -> SimpleNamespace:
    return SimpleNamespace(
        owner_id=owner_id, lifecycle_status="staging", revision=EXPECTED_REVISION,
        title=None, theme={}, language="ko",
    )


def _install_fakes(monkeypatch: pytest.MonkeyPatch, calls: Calls, *, publish_error: Exception | None = None) -> Any:
    import services.authored_checkpoint_service as checkpoint
    import services.authored_presentation_artifacts as artifacts
    import services.authored_vnext.deck_ir.product_adapter as adapter
    import services.authored_vnext.semantic.publication as semantic_publication
    import services.presentation_publication_service as publication_service
    import services.workspace_artifact_publication_service as artifact_service

    artifact = SimpleNamespace(
        artifact_id=uuid.uuid4(), public_path="/app_data/workspace-artifacts/users/x/op/a.pptx",
        source_revision=EXPECTED_REVISION + 1,
    )

    def save_pngs(presentation_id: uuid.UUID, pngs: list[bytes], token: str | None = None) -> list[str]:
        calls.record("save_pngs", presentation_id=presentation_id, count=len(pngs), token=token)
        return [f"/app_data/images/{presentation_id}/{index}.png" for index in range(len(pngs))]

    def remove_pngs(presentation_id: uuid.UUID, pngs: list[bytes], token: str | None = None) -> None:
        calls.record("remove_pngs", presentation_id=presentation_id, token=token)

    def build_export(request: Any, presentation_id: uuid.UUID, pngs: list[bytes], title: str) -> str:
        calls.record("build_export", presentation_id=presentation_id, count=len(pngs), title=title)
        return f"C:/app_data/exports/{presentation_id}.pptx"

    async def begin_publish(session: Any, **kwargs: Any) -> None:
        calls.record("begin_publish", **kwargs)

    async def complete_publish(session: Any, **kwargs: Any) -> None:
        calls.record("complete_publish", **kwargs)

    async def font_materials() -> tuple[dict[str, Any], dict[str, bytes], str, str]:
        return {"fonts": []}, {}, "Pretendard", "@font-face {}"

    async def bundle_graph(session: Any, **kwargs: Any) -> SimpleNamespace:
        calls.record("bundle_graph", **kwargs)
        return SimpleNamespace(canonical_digest="sha256:" + "b" * 64)

    async def presentation_revision(session: Any, **kwargs: Any) -> int:
        calls.record("publish_presentation_revision", **kwargs)
        return int(kwargs["expected_revision"]) + 1

    async def prepare_artifact(session: Any, **kwargs: Any) -> Any:
        calls.record("prepare_artifact", **kwargs)
        return artifact

    async def fence_artifact(session: Any, **kwargs: Any) -> None:
        calls.record("fence_artifact", **kwargs)

    async def publish_artifact(session: Any, **kwargs: Any) -> int:
        calls.record("publish_artifact", **kwargs)
        if publish_error is not None:
            raise publish_error
        return kwargs["artifact"].source_revision

    async def compensate(session: Any, prepared: Any) -> None:
        calls.record("compensate", artifact=prepared)

    monkeypatch.setattr(artifacts, "_save_slide_pngs", save_pngs)
    monkeypatch.setattr(artifacts, "_remove_partial_slide_pngs", remove_pngs)
    monkeypatch.setattr(artifacts, "_build_authored_export", build_export)
    monkeypatch.setattr(checkpoint, "begin_publish", begin_publish)
    monkeypatch.setattr(checkpoint, "complete_publish", complete_publish)
    monkeypatch.setattr(adapter, "_publication_font_materials", font_materials)
    monkeypatch.setattr(semantic_publication, "publish_authored_semantic_bundle_graph", bundle_graph)
    monkeypatch.setattr(publication_service, "publish_presentation_revision", presentation_revision)
    monkeypatch.setattr(artifact_service, "prepare_workspace_generation_artifact", prepare_artifact)
    monkeypatch.setattr(artifact_service, "fence_workspace_generation_publication", fence_artifact)
    monkeypatch.setattr(artifact_service, "publish_workspace_generation_artifact", publish_artifact)
    monkeypatch.setattr(artifact_service, "compensate_workspace_artifact", compensate)
    monkeypatch.setattr(
        publish_module, "semantic_draft_from_html", lambda html, **kwargs: {"html": html, **kwargs}
    )
    return artifact


def _publish(session: FakeSession, job: Any, async_status: Any = None) -> dict[str, Any]:
    deck, results = _deck(3)
    return asyncio.run(
        publish_composer_deck(
            sql_session=session,
            request=SimpleNamespace(language="ko", authored_style="style-a", export_as="pptx"),
            presentation_id=uuid.uuid4(),
            generation_job=job,
            async_status=async_status,
            htmls=[result.html for result in results],
            pngs=[b"\x89PNG" + bytes([index]) for index in range(len(results))],
            results=results,
            deck=deck,
            receipt={"rungHistogram": {"1": 3}},
        )
    )


def _workspace_job() -> tuple[SimpleNamespace, SimpleNamespace]:
    operation_id = uuid.uuid4()
    job = SimpleNamespace(
        id=uuid.uuid4(), workspace_operation_id=operation_id, attempt_token=ATTEMPT_TOKEN,
        _workspace_fencing_token=FENCING_TOKEN,
    )
    operation = SimpleNamespace(
        id=operation_id, state="running", attempt_token=ATTEMPT_TOKEN, expected_revision=EXPECTED_REVISION
    )
    return job, operation


def test_workspace_lane_publishes_through_the_artifact_cas_only(monkeypatch) -> None:
    calls = Calls()
    artifact = _install_fakes(monkeypatch, calls)
    job, operation = _workspace_job()
    presentation = _presentation(uuid.uuid4())
    session = FakeSession(presentation=presentation, operation=operation)
    async_status = SimpleNamespace(status="running", message=None, data=None, error=None, updated_at=None)

    result = _publish(session, job, async_status)

    assert calls["publish_presentation_revision"] == []
    assert calls["complete_publish"] == []
    assert len(calls["begin_publish"]) == 1
    assert calls["begin_publish"][0] == {
        "job_id": job.id, "attempt_token": ATTEMPT_TOKEN, "fencing_token": FENCING_TOKEN,
    }
    [prepared] = calls["prepare_artifact"]
    assert prepared["operation_id"] == job.workspace_operation_id
    assert prepared["source_revision"] == EXPECTED_REVISION + 1
    assert prepared["source_path"].endswith(".pptx")
    assert calls["build_export"][0]["title"] == "Composer deck"
    assert calls["fence_artifact"] == [{"artifact": artifact}]
    [published] = calls["publish_artifact"]
    assert published["artifact"] is artifact
    assert published["expected_revision"] == EXPECTED_REVISION
    assert published["attempt_token"] == ATTEMPT_TOKEN
    evidence = published["result_evidence"]
    assert evidence["generationRoute"] == "composer"
    assert evidence["rungHistogram"] == {"1": 3}
    assert evidence["publication"]["revision"] == EXPECTED_REVISION + 1
    assert evidence["publication"]["bundleCount"] == 3
    # The artifact helper flips lifecycle/revision under its own fence and commits.
    assert presentation.lifecycle_status == "staging"
    assert presentation.mode == "authored"
    assert presentation.theme["generationComposer"]["rungHistogram"] == {"1": 3}
    assert session.commits == 0
    assert session.rollbacks == 0
    assert calls["compensate"] == []
    assert calls["remove_pngs"] == []
    assert async_status.status == "completed"
    assert async_status.data["path"] == artifact.public_path
    assert async_status.data["edit_path"].startswith("/presentation?id=")
    assert result["revision"] == EXPECTED_REVISION + 1
    assert result["path"] == artifact.public_path
    assert result["generationRoute"] == "composer"
    assert len(result["imageRefs"]) == 3


@pytest.mark.parametrize("code", ["operation_lease_lost", "revision_conflict"])
def test_workspace_lane_lease_loss_is_a_retryable_fence_loss(monkeypatch, code: str) -> None:
    calls = Calls()
    _install_fakes(monkeypatch, calls, publish_error=WorkspaceArtifactPublicationError(code))
    job, operation = _workspace_job()
    session = FakeSession(presentation=_presentation(uuid.uuid4()), operation=operation)

    with pytest.raises(ComposerPublicationError) as raised:
        _publish(session, job)

    assert raised.value.code == "composer_operation_fence_lost"
    assert raised.value.retryable is True
    assert isinstance(raised.value.__cause__, WorkspaceArtifactPublicationError)
    assert session.rollbacks == 1
    assert session.commits == 0
    # Once handed to the helper, compensation is the helper's job, not ours.
    assert calls["compensate"] == []
    assert len(calls["remove_pngs"]) == 1


def test_workspace_lane_other_helper_codes_stay_non_retryable(monkeypatch) -> None:
    calls = Calls()
    _install_fakes(
        monkeypatch, calls, publish_error=WorkspaceArtifactPublicationError("workspace_artifact_intent_conflict")
    )
    job, operation = _workspace_job()
    session = FakeSession(presentation=_presentation(uuid.uuid4()), operation=operation)

    with pytest.raises(ComposerPublicationError) as raised:
        _publish(session, job)

    assert raised.value.code == "workspace_artifact_intent_conflict"
    assert raised.value.retryable is False


def test_workspace_lane_compensates_a_staged_artifact_when_publication_never_starts(monkeypatch) -> None:
    calls = Calls()
    artifact = _install_fakes(monkeypatch, calls)
    import services.authored_checkpoint_service as checkpoint

    async def failing_begin_publish(session: Any, **kwargs: Any) -> None:
        raise RuntimeError("database gone")

    monkeypatch.setattr(checkpoint, "begin_publish", failing_begin_publish)
    job, operation = _workspace_job()
    session = FakeSession(presentation=_presentation(uuid.uuid4()), operation=operation)

    with pytest.raises(ComposerPublicationError) as raised:
        _publish(session, job)

    assert raised.value.code == "composer_publication_failed"
    assert calls["compensate"] == [{"artifact": artifact}]
    assert calls["publish_artifact"] == []
    assert len(calls["remove_pngs"]) == 1


def test_workspace_lane_lost_fence_fails_before_any_external_write(monkeypatch) -> None:
    calls = Calls()
    _install_fakes(monkeypatch, calls)
    job, operation = _workspace_job()
    operation.attempt_token = "someone-else"
    session = FakeSession(presentation=_presentation(uuid.uuid4()), operation=operation)

    with pytest.raises(ComposerPublicationError) as raised:
        _publish(session, job)

    assert raised.value.code == "composer_operation_fence_lost"
    assert raised.value.retryable is True
    assert calls["build_export"] == []
    assert calls["prepare_artifact"] == []
    assert calls["save_pngs"] == []


def test_non_workspace_lane_keeps_complete_publish_and_presentation_cas(monkeypatch) -> None:
    calls = Calls()
    _install_fakes(monkeypatch, calls)
    job = SimpleNamespace(id=uuid.uuid4(), workspace_operation_id=None, attempt_token=ATTEMPT_TOKEN)
    presentation = _presentation(uuid.uuid4())
    session = FakeSession(presentation=presentation, operation=None)

    result = _publish(session, job)

    assert calls["prepare_artifact"] == []
    assert calls["fence_artifact"] == []
    assert calls["publish_artifact"] == []
    assert calls["build_export"] == []
    assert len(calls["begin_publish"]) == 1
    assert len(calls["complete_publish"]) == 1
    [revision_call] = calls["publish_presentation_revision"]
    assert revision_call["operation_id"] is None
    assert revision_call["attempt_token"] is None
    assert revision_call["expected_revision"] == EXPECTED_REVISION
    assert revision_call["result"]["publication"]["generationRoute"] == "composer"
    assert presentation.lifecycle_status == "published"
    assert session.commits == 1
    assert result["revision"] == EXPECTED_REVISION + 1
    assert result["path"] == result["editPath"]
