import asyncio
from contextlib import contextmanager
from datetime import timedelta
from io import BytesIO
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import uuid
from types import SimpleNamespace

import pytest
from PIL import Image
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlmodel import SQLModel

from models.sql.async_presentation_generation_status import (
    AsyncPresentationGenerationTaskModel,
)
from models.sql.authored_slide_checkpoint import AuthoredSlideCheckpoint
from models.sql.presentation_generation_job import PresentationGenerationJob
from models.sql.presentation import PresentationModel
from models.sql.public_admission import PublicOperationAdmission, PublicPolicyProfile
from models.sql.user import User
from models.sql.workspace import WorkspaceConversation, WorkspaceOperation
from models.presentation_outline_model import (
    PresentationOutlineModel,
    SlideOutlineModel,
)
from services import authored_checkpoint_service as checkpoints
from services import authored_presentation_pipeline as pipeline
from services.authored_checkpoint_html import checkpoint_stage_html
from services.generation_core.authoring.author_slide import Brand
from utils.datetime_utils import get_current_utc_datetime
from utils.runtime_settings import replace_process_runtime_settings
from utils.renderer_isolation import RendererPolicyError


def _png(color):
    stream = BytesIO()
    Image.new("RGB", (1280, 720), color).save(stream, format="PNG")
    return stream.getvalue()


_STALE_PNG = _png("red")
_RENDERED_PNG = _png("blue")


# The complete foreign-key closure of the checkpoint tables. Rows that name a
# parent (operation -> conversation -> presentation/user, admission -> policy)
# are seeded with real parents, so the same fixtures hold on a database that
# enforces every constraint.
_CHECKPOINT_TABLES = [
    AsyncPresentationGenerationTaskModel.__table__,
    PresentationGenerationJob.__table__,
    AuthoredSlideCheckpoint.__table__,
    WorkspaceOperation.__table__,
    # A render checkpoint asks the admission authority whether the
    # generation is public before it writes its artifacts, so the
    # scratch schema needs the table even for a local job.
    PublicOperationAdmission.__table__,
    PublicPolicyProfile.__table__,
    WorkspaceConversation.__table__,
    PresentationModel.__table__,
    User.__table__,
]


async def _checkpoint_database(tmp_path, monkeypatch):
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{tmp_path / 'authored-checkpoints.db'}"
    )
    async with engine.begin() as connection:
        await connection.run_sync(
            SQLModel.metadata.create_all,
            tables=_CHECKPOINT_TABLES,
        )
    maker = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(checkpoints, "async_session_maker", maker)
    monkeypatch.setenv(
        "AUTHORED_CHECKPOINT_DIRECTORY", str(tmp_path / "render-artifacts")
    )
    return engine, maker


async def _running_authored_job(maker, *, total_slides=0):
    status = AsyncPresentationGenerationTaskModel(
        status="running",
        message="Generating authored presentation",
    )
    job = PresentationGenerationJob(
        task_id=status.id,
        presentation_id=uuid.uuid4(),
        request_sha256="a" * 64,
        request_payload={"template": "authored"},
        state="running",
        attempt_number=3,
        attempt_token="attempt-a",
        total_slides=total_slides,
    )
    async with maker() as session:
        # The ORM flushes unrelated mappers alphabetically, not by foreign
        # key, so the parent row is written first on purpose.
        session.add(status)
        await session.flush()
        session.add(job)
        await session.commit()
    return status, job


async def _linked_operation(
    maker, job, *, owner_id=None, admitted=False, fence=9, attempt="workspace-attempt"
):
    """Link ``job`` to a running workspace operation through real parent rows.

    ``admitted`` adds the public v2 contract and its policy/admission rows, so
    the fence also has to agree with the admission authority. Parents are
    flushed one level at a time: the ORM does not order unrelated mappers by
    foreign key. Returns ``(operation_id, admission_id)``.
    """
    deadline = get_current_utc_datetime() + timedelta(hours=1)
    scope = str(owner_id) if owner_id is not None else "anonymous"
    contract = (
        {
            "contract_version": "public-operation/v2",
            "execution_profile_version": "public-generation-export/v2",
            "phase": "active",
            "deadline_at": deadline,
        }
        if admitted
        else {}
    )
    assert owner_id is not None or not admitted, "an admission is bound to a real owner"
    admission_id = None
    async with maker() as session:
        if owner_id is not None:
            session.add(
                User(id=owner_id, username=f"owner-{owner_id}", hashed_password="unused")
            )
        session.add(
            PresentationModel(
                id=job.presentation_id,
                content="checkpoint fence",
                n_slides=1,
                language="en",
                layout={"name": "test"},
                mode="svg",
            )
        )
        await session.flush()
        conversation = WorkspaceConversation(
            presentation_id=job.presentation_id, owner_id=owner_id, owner_scope=scope
        )
        session.add(conversation)
        await session.flush()
        operation = WorkspaceOperation(
            conversation_id=conversation.id,
            presentation_id=job.presentation_id,
            owner_id=owner_id,
            owner_scope=scope,
            anchor_position=1,
            kind="presentation.generate",
            state="running",
            idempotency_key_hash="d" * 64,
            request_hash="e" * 64,
            request_payload={},
            attempt_token=attempt,
            fencing_token=fence,
            **contract,
        )
        session.add(operation)
        await session.flush()
        if admitted:
            policy_values = {
                column.name: 100
                for column in PublicPolicyProfile.__table__.columns
                if not column.nullable
                and column.default is None
                and column.server_default is None
            }
            policy_values["version"] = "checkpoint-fence-policy"
            session.add(PublicPolicyProfile(**policy_values))
            await session.flush()
            admission = PublicOperationAdmission(
                operation_id=operation.id,
                policy_version="checkpoint-fence-policy",
                owner_id=owner_id,
                tenant_id="tenant-a",
                trace_id_hash="b" * 64,
                operation_kind="generation",
                resource_units=1,
                policy_digest="c" * 64,
                deadline_at=deadline,
                state="active",
                fence=fence,
                slide_count=1,
                planned_request_limit=4,
                retry_request_limit=4,
                estimated_cost_limit_microunits=100,
                actual_cost_limit_microunits=100,
            )
            session.add(admission)
            await session.flush()
            admission_id = admission.id
        stored = await session.get(PresentationGenerationJob, job.id)
        stored.workspace_operation_id = operation.id
        stored.attempt_token = attempt
        session.add(stored)
        await session.commit()
        operation_id = operation.id
    job.workspace_operation_id = operation_id
    job.attempt_token = attempt
    return operation_id, admission_id


async def _set_columns(maker, model, row_id, **values):
    async with maker() as session:
        await session.execute(update(model).where(model.id == row_id).values(**values))
        await session.commit()


@contextmanager
def _principal(owner_id, tenant_id="tenant-a"):
    """Run the block as one authenticated owner/tenant, as the request layer does."""
    from utils.owner_context import (
        reset_current_owner_id,
        reset_current_tenant_id,
        set_current_owner_id,
        set_current_tenant_id,
    )

    owner_token = set_current_owner_id(owner_id)
    tenant_token = set_current_tenant_id(tenant_id)
    try:
        yield
    finally:
        reset_current_tenant_id(tenant_token)
        reset_current_owner_id(owner_token)


def _one_slide_outline():
    return PresentationOutlineModel(
        slides=[SlideOutlineModel(content="checkpoint resume")]
    )


def _pipeline_brand():
    return Brand(
        topic="checkpoint",
        language="English",
        primary="#2563EB",
        fonts="Inter",
    )


def _install_one_slide_pipeline_fakes(monkeypatch, *, rendered_html):
    plan = SimpleNamespace(
        contents=("checkpoint resume",),
        design_system="TEST DESIGN SYSTEM",
        reference_mode="style_reference",
    )

    async def unexpected_author(*_args, **_kwargs):
        raise AssertionError("a completed author checkpoint must be resumed")

    async def fake_render(html):
        rendered_html.append(html)
        return _RENDERED_PNG
    async def fake_capture(html: str, **_kwargs):
        from services.authored_local_fit_policy import html_digest
        from utils.slide_capture_result import RenderCapture

        return RenderCapture.from_payload(
            html_digest=html_digest(html),
            png=await fake_render(html),
            browser_metrics={"width": 1280, "height": 720},
            runtime_counters={},
            prepared_html=html,
        )

    async def no_recording(**_kwargs):
        return None

    monkeypatch.setattr(pipeline, "prepare_authored_deck", lambda *_args, **_kwargs: plan)
    monkeypatch.setattr(pipeline, "author_planned_slide", unexpected_author)
    monkeypatch.setattr(pipeline, "render_html_to_png", fake_render)
    monkeypatch.setattr(
        pipeline,
        "render_html_to_png_with_metrics",
        fake_capture,
    )
    monkeypatch.setattr(pipeline, "record_local_stage", no_recording)
    # local_fit_applies() defaults on for an ordinary deck and routes render
    # through render_html_to_png_with_metrics instead of the mocked
    # render_html_to_png seam (real Chrome, unmocked here); see the identical
    # note in tests/unit/test_authored_pipeline.py.
    monkeypatch.setattr(pipeline, "local_fit_applies", lambda **_kwargs: False)


def test_sealed_scene_envelope_recaptures_stale_role_evidence_once(
    tmp_path, monkeypatch
):
    """Stale role evidence is recaptured once, then reused on resume."""

    stale_role_html = (
        "<!doctype html><html><head><meta charset='utf-8'></head><body>"
        '<main data-semantic-role="body">'
        '<div data-semantic-role="body"></div>'
        "<h1>stale roles</h1></main></body></html>"
    )

    async def resume_once(job):
        return await pipeline._author_illustrate_render_pipeline(
            _one_slide_outline(),
            _pipeline_brand(),
            SimpleNamespace(
                reference_mode="style_reference",
                illustration_prompt=None,
            ),
            generation_job=job,
        )

    async def scenario():
        engine, maker = await _checkpoint_database(tmp_path, monkeypatch)
        rendered_html = []
        _install_one_slide_pipeline_fakes(monkeypatch, rendered_html=rendered_html)
        try:
            _, job = await _running_authored_job(maker, total_slides=1)
            await checkpoints.initialize_checkpoints(
                job_id=job.id,
                attempt_token="attempt-a",
                roles=["title"],
            )
            for stage, kwargs in (
                ("author", {"html": stale_role_html}),
                ("illustrate", {"skipped": True}),
                ("render", {"png": _STALE_PNG}),
                ("persist", {}),
            ):
                await checkpoints.start_stage(
                    job_id=job.id,
                    slide_index=0,
                    stage=stage,
                    attempt_token="attempt-a",
                )
                await checkpoints.complete_stage(
                    job_id=job.id,
                    slide_index=0,
                    stage=stage,
                    attempt_token="attempt-a",
                    **kwargs,
                )

            first = await resume_once(job)

            # The stale-role pixels are refused: the sealed projection restamps
            # the obsolete ``body`` roles, so the checkpoint's HTML no longer
            # describes the scene its PNG was taken from.
            assert len(rendered_html) == 1
            assert 'data-semantic-role="body"' not in rendered_html[0]
            assert 'data-semantic-role="decoration"' in rendered_html[0]
            assert first.deck.htmls == rendered_html
            assert first.pngs == [_RENDERED_PNG]

            # The durable evidence is rebound to the sealed scene: the
            # stale-role PNG is gone and the stage HTML is the projection.
            recaptured = await checkpoints.get_checkpoint(job.id, 0)
            assert checkpoints.read_valid_render(recaptured) == _RENDERED_PNG
            assert checkpoint_stage_html(recaptured) == rendered_html[0]

            # Once. A second resume finds the durable evidence already bound to
            # the sealed projection and captures nothing further.
            second = await resume_once(job)
            assert len(rendered_html) == 1
            assert second.deck.htmls == first.deck.htmls
            assert second.pngs == first.pngs
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_exception_metadata_is_bounded_and_redacts_secrets_paths_and_html():
    unsafe = RuntimeError(
        "provider failed api_key=top-secret-value "
        "at https://provider.example/v1/images?token=also-secret "
        r"from C:\private\customer\payload.json"
    )
    sanitized = checkpoints.sanitize_exception_message(unsafe)

    assert "top-secret-value" not in sanitized
    assert "also-secret" not in sanitized
    assert "customer" not in sanitized
    assert sanitized.count("[redacted]") >= 3
    assert len(sanitized) <= 512
    assert checkpoints.sanitize_exception_message(
        RuntimeError("upstream returned <!DOCTYPE html><html>private body</html>")
    ) == "[authored content redacted]"
    standalone = checkpoints.sanitize_exception_message(
        RuntimeError("Bearer bearer-secret sk-standalonesecret")
    )
    assert "bearer-secret" not in standalone
    assert "standalonesecret" not in standalone

    error = checkpoints.AuthoredStageError(
        slide_index=6,
        stage="render",
        code="authored_slide_render_failed",
        cause=unsafe,
        retryable=True,
    )
    safe = error.as_safe_dict(request_id="request-123", attempt_number=3)

    assert safe["slide_index"] == 6
    assert safe["slide_number"] == 7
    assert safe["stage"] == "render"
    assert safe["code"] == "authored_slide_render_failed"
    assert safe["retryable"] is True
    assert safe["request_id"] == "request-123"
    assert safe["attempt_number"] == 3

    custom = checkpoints.AuthoredStageError(
        slide_index=0,
        stage="persist",
        code="custom",
        message="publish failed token=do-not-store",
    ).as_safe_dict()
    assert "do-not-store" not in custom["detail"]
    assert safe["exception_type"] == "RuntimeError"
    assert safe["cause"] == sanitized
    assert len(safe["fingerprint"]) == 16


def test_retry_classifier_keeps_renderer_policy_failures_non_retryable():
    class HttpError(RuntimeError):
        def __init__(self, status_code):
            super().__init__(f"HTTP {status_code}")
            self.status_code = status_code

    assert (
        checkpoints.is_retryable_exception(RendererPolicyError("unsafe HTML"))
        is False
    )
    assert checkpoints.is_retryable_exception(HttpError(429)) is True
    assert checkpoints.is_retryable_exception(HttpError(500)) is True
    assert checkpoints.is_retryable_exception(HttpError(503)) is True
    assert checkpoints.is_retryable_exception(TimeoutError("provider timeout")) is True


def test_final_publish_is_attempt_fenced_and_terminal_in_one_transaction(
    tmp_path, monkeypatch
):
    async def scenario():
        engine, maker = await _checkpoint_database(tmp_path, monkeypatch)
        try:
            _, job = await _running_authored_job(maker)
            async with maker() as session:
                with pytest.raises(checkpoints.AuthoredStageError) as raised:
                    await checkpoints.begin_publish(
                        session,
                        job_id=job.id,
                        attempt_token="stale-attempt",
                    )
                assert raised.value.code == "authored_attempt_lost"

            async with maker() as session:
                await checkpoints.begin_publish(
                    session,
                    job_id=job.id,
                    attempt_token="attempt-a",
                )
                await checkpoints.complete_publish(
                    session,
                    job_id=job.id,
                    attempt_token="attempt-a",
                )
                await session.commit()

            async with maker() as session:
                stored = await session.get(PresentationGenerationJob, job.id)
                assert stored.state == "published"
                assert stored.attempt_token is None
                assert stored.current_stage == "completed"
                assert stored.resume_allowed is False
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_linked_workspace_checkpoint_and_publication_require_matching_fence(
    tmp_path, monkeypatch
):
    async def scenario():
        engine, maker = await _checkpoint_database(tmp_path, monkeypatch)
        try:
            _, job = await _running_authored_job(maker)
            operation_id, _ = await _linked_operation(maker, job)

            with pytest.raises(checkpoints.AuthoredStageError) as missing:
                await checkpoints.initialize_checkpoints(
                    job_id=job.id,
                    attempt_token="workspace-attempt",
                    roles=["title"],
                )
            assert missing.value.code == "authored_workspace_operation_fencing_token_required"

            with pytest.raises(checkpoints.AuthoredStageError) as stale:
                await checkpoints.initialize_checkpoints(
                    job_id=job.id,
                    attempt_token="workspace-attempt",
                    fencing_token=8,
                    roles=["title"],
                )
            assert stale.value.code == "authored_workspace_operation_fence_lost"

            await checkpoints.initialize_checkpoints(
                job_id=job.id,
                attempt_token="workspace-attempt",
                fencing_token=9,
                roles=["title"],
            )
            await checkpoints.start_stage(
                job_id=job.id,
                slide_index=0,
                stage="author",
                attempt_token="workspace-attempt",
                fencing_token=9,
            )
            await checkpoints.start_stage(
                job_id=job.id,
                slide_index=0,
                stage="render",
                attempt_token="workspace-attempt",
                fencing_token=9,
            )

            # This scenario is specific to the unbatched render write, which
            # resolves the public binding in its own session before it opens
            # the fenced transaction.  The batched write fences first and holds
            # the operation row lock across the binding read, so a competing
            # transaction cannot commit a takeover in that window at all; it is
            # exercised separately below.
            monkeypatch.setenv("AUTHORED_CHECKPOINT_BATCHING_ENABLED", "false")
            replace_process_runtime_settings(None)

            async def take_over_before_pointer(*_args, **_kwargs):
                async with maker() as takeover_session:
                    await takeover_session.execute(
                        update(WorkspaceOperation)
                        .where(WorkspaceOperation.id == operation_id)
                        .values(attempt_token="newer-attempt", fencing_token=10)
                    )
                    await takeover_session.commit()
                return None

            monkeypatch.setattr(
                checkpoints._state,
                "_public_checkpoint_binding",
                take_over_before_pointer,
            )
            with pytest.raises(checkpoints.AuthoredStageError) as late_write:
                await checkpoints.complete_stage(
                    job_id=job.id,
                    slide_index=0,
                    stage="render",
                    attempt_token="workspace-attempt",
                    fencing_token=9,
                    html="<section>late render</section>",
                    png=b"late-render",
                    browser_metrics={"visible": True},
                )
            assert late_write.value.code == "authored_workspace_operation_fence_lost"

            # The batched render write refuses the same stale attempt at its
            # first statement, before any artifact bytes are produced.
            monkeypatch.setenv("AUTHORED_CHECKPOINT_BATCHING_ENABLED", "true")
            replace_process_runtime_settings(None)
            with pytest.raises(checkpoints.AuthoredStageError) as batched_write:
                await checkpoints.complete_stage(
                    job_id=job.id,
                    slide_index=0,
                    stage="render",
                    attempt_token="workspace-attempt",
                    fencing_token=9,
                    html="<section>later render</section>",
                    png=b"later-render",
                    browser_metrics={"visible": True},
                )
            assert batched_write.value.code == "authored_workspace_operation_fence_lost"

            async with maker() as session:
                checkpoint = (
                    await session.execute(
                        select(AuthoredSlideCheckpoint).where(
                            AuthoredSlideCheckpoint.job_id == job.id,
                            AuthoredSlideCheckpoint.slide_index == 0,
                        )
                    )
                ).scalar_one()
                assert checkpoint is not None
                assert checkpoint.render_path is None
                assert checkpoint.artifact_refs is None
            assert list((tmp_path / "render-artifacts" / str(job.id)).glob("*.png"))

            unfenced = await checkpoints.get_checkpoint(job.id, 0)
            assert unfenced.render_path is None
            assert unfenced.artifact_refs is None
            newer_attempt = await checkpoints.get_checkpoint(
                job.id,
                0,
                attempt_token="newer-attempt",
                fencing_token=10,
            )
            assert newer_attempt.render_path is None
            assert newer_attempt.artifact_refs is None

            async with maker() as session:
                with pytest.raises(checkpoints.AuthoredStageError) as publish_stale:
                    await checkpoints.begin_publish(
                        session,
                        job_id=job.id,
                        attempt_token="workspace-attempt",
                        fencing_token=8,
                )
                assert publish_stale.value.code == "authored_workspace_operation_fence_lost"

            # An SVG checkpoint read asserts the same fence as a plain read: it
            # takes no row lock and rewrites no heartbeat; writes stay fenced.
            from services import svg_presentation_checkpoint as svg_checkpoints

            monkeypatch.setattr(svg_checkpoints, "async_session_maker", maker)

            def svg_job(attempt, fence):
                return SimpleNamespace(id=job.id, attempt_token=attempt,
                                       workspace_operation_id=operation_id,
                                       _workspace_fencing_token=fence)

            async def heartbeats():
                async with maker() as session:
                    return (await session.scalar(select(WorkspaceOperation.heartbeat_at).where(
                        WorkspaceOperation.id == operation_id)), await session.scalar(
                        select(PresentationGenerationJob.heartbeat_at).where(
                            PresentationGenerationJob.id == job.id)))

            before = await heartbeats()
            for stale, code in ((svg_job("workspace-attempt", 9), "authored_workspace_operation_fence_lost"),
                                (svg_job("newer-attempt", None),
                                 "authored_workspace_operation_fencing_token_required")):
                with pytest.raises(checkpoints.AuthoredStageError) as stale_read:
                    await svg_checkpoints._checkpoint(stale, 0)
                assert stale_read.value.code == code
            with pytest.raises(checkpoints.AuthoredStageError) as stale_fence:
                await svg_checkpoints._fence_checkpoint_authority(svg_job("workspace-attempt", 9), 0)
            assert stale_fence.value.code == "authored_workspace_operation_fence_lost"
            current = svg_job("newer-attempt", 10)
            assert await svg_checkpoints._checkpoint(current, 0) == {}
            with pytest.raises(RuntimeError, match="svg_checkpoint_missing"):
                await svg_checkpoints._checkpoint(current, 7)
            assert await heartbeats() == before
            await svg_checkpoints._fence_checkpoint_authority(current, 0)
            async with maker() as session:
                # Metadata-only reads project payload keys; absent keys are None.
                projected = (await session.execute(select(
                    PresentationGenerationJob.request_payload["template"].label("present"),
                    PresentationGenerationJob.request_payload["absent"].label("absent"),
                ).where(PresentationGenerationJob.id == job.id))).one()
            assert (projected.present, projected.absent) == ("authored", None)
            with pytest.raises(RuntimeError, match="svg_checkpoint_missing"):
                await svg_checkpoints._fence_checkpoint_authority(current, 7)
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_svg_checkpoint_authority_is_owner_scoped_read_only_and_write_fenced(
    tmp_path, monkeypatch
):
    """Owner/tenant context decides what an SVG checkpoint read may see.

    The owner and tenant criteria come from the central ORM hook, so the test
    sets a real principal around real fenced queries. A read stays free of
    heartbeat writes; only a write refreshes (and then maintains) the fence.
    """
    from services import svg_presentation_checkpoint as svg_checkpoints

    lost = "authored_workspace_operation_fence_lost"

    async def scenario():
        engine, maker = await _checkpoint_database(tmp_path, monkeypatch)
        try:
            monkeypatch.setattr(svg_checkpoints, "async_session_maker", maker)
            owner, foreign = uuid.uuid4(), uuid.uuid4()
            me, other_owner, other_tenant = (
                (owner, "tenant-a"), (foreign, "tenant-a"), (owner, "tenant-b"))
            _, job = await _running_authored_job(maker)
            operation_id, admission_id = await _linked_operation(
                maker, job, owner_id=owner, admitted=True)
            async with maker() as session:
                session.add(AuthoredSlideCheckpoint(
                    job_id=job.id, slide_index=0, role="content",
                    svg_checkpoint={"seed": "page-0"}))
                await session.commit()

            def svg_job(attempt="workspace-attempt", fence=9):
                return SimpleNamespace(id=job.id, attempt_token=attempt,
                                       workspace_operation_id=operation_id,
                                       _workspace_fencing_token=fence)

            async def heartbeats():
                async with maker() as session:
                    return (await session.scalar(select(WorkspaceOperation.heartbeat_at).where(
                        WorkspaceOperation.id == operation_id)), await session.scalar(
                        select(PresentationGenerationJob.heartbeat_at).where(
                            PresentationGenerationJob.id == job.id)))

            async def read(who, active=None, index=0):
                with _principal(*who):
                    return await svg_checkpoints._checkpoint(active or svg_job(), index)

            async def refused(code, who, active=None, index=0, payload=None):
                with _principal(*who):
                    with pytest.raises(checkpoints.AuthoredStageError) as error:
                        if payload is None:
                            await svg_checkpoints._checkpoint(active or svg_job(), index)
                        else:
                            await svg_checkpoints._checkpoint(
                                active or svg_job(), index, payload=payload)
                assert error.value.code == code

            before = await heartbeats()
            assert await read(me) == {"seed": "page-0"}
            # A foreign owner or tenant never receives the page, and a lost
            # fence takes precedence over a missing row.
            await refused(lost, other_owner)
            await refused(lost, other_tenant)
            await refused(lost, other_owner, index=7)
            with pytest.raises(RuntimeError, match="svg_checkpoint_missing"):
                await read(me, index=7)
            await refused(lost, me, svg_job(fence=8))
            await refused(lost, me, svg_job("taken-over"))
            await refused("authored_workspace_operation_fencing_token_required",
                          me, svg_job(fence=None))
            # Each revoked control rejects the read; restoring it re-admits.
            for model, row_id, revoke, restore, code in (
                (WorkspaceOperation, operation_id, {"state": "cancelled"},
                 {"state": "running"}, lost),
                (WorkspaceOperation, operation_id, {"attempt_token": "taken-over"},
                 {"attempt_token": "workspace-attempt"}, lost),
                (WorkspaceOperation, operation_id, {"fencing_token": 10},
                 {"fencing_token": 9}, lost),
                (PublicOperationAdmission, admission_id, {"state": "succeeded"},
                 {"state": "active"}, lost),
                (PublicOperationAdmission, admission_id, {"fence": 10},
                 {"fence": 9}, lost),
                (PublicOperationAdmission, admission_id, {"operation_kind": "export"},
                 {"operation_kind": "generation"}, lost),
                (PresentationGenerationJob, job.id, {"state": "cancelled"},
                 {"state": "running"}, "authored_attempt_lost"),
            ):
                await _set_columns(maker, model, row_id, **revoke)
                await refused(code, me)
                await _set_columns(maker, model, row_id, **restore)
                assert await read(me) == {"seed": "page-0"}
            # Not one read above rewrote a heartbeat.
            assert await heartbeats() == before

            # Writes keep the full attempt/fence CAS: a stale fence is refused
            # and leaves neither payload nor heartbeat behind. (The owner hook
            # scopes SELECTs only; a write's principal binding is the admission
            # check in ensure_recovery_authority, tested below, plus database RLS.)
            payload = {"seed": "page-0", "step": 2}
            await refused(lost, me, svg_job(fence=8), payload=payload)
            assert await heartbeats() == before
            assert await read(me) == {"seed": "page-0"}
            with _principal(*me):
                assert await svg_checkpoints._checkpoint(svg_job(), 0, payload=payload) == payload
            written = await heartbeats()
            assert None not in written and written != before
            assert await read(me) == payload
            assert await heartbeats() == written

            # Completion times keep the first transition into completed: a
            # later write of the same completed stage by the same attempt does
            # not move them; leaving the stage clears it, re-entry restamps.
            async def completed_at():
                async with maker() as session:
                    row = await session.scalar(select(AuthoredSlideCheckpoint).where(
                        AuthoredSlideCheckpoint.job_id == job.id,
                        AuthoredSlideCheckpoint.slide_index == 0))
                    return row.author_completed_at, row.render_completed_at

            rendered = {**payload, "svg_content": "<svg/>", "png_sha256": "a" * 64}
            with _principal(*me):
                await svg_checkpoints._checkpoint(svg_job(), 0, payload=rendered)
            first = await completed_at()
            assert None not in first
            with _principal(*me):
                await svg_checkpoints._checkpoint(svg_job(), 0, payload={**rendered, "review": 1})
            assert await completed_at() == first
            with _principal(*me):
                await svg_checkpoints._checkpoint(svg_job(), 0, payload={**rendered, "png_sha256": None})
            assert await completed_at() == (first[0], None)
            with _principal(*me):
                await svg_checkpoints._checkpoint(svg_job(), 0, payload=rendered)
            restamped = await completed_at()
            assert restamped[0] == first[0] and restamped[1] is not None
            async with maker() as session:
                progressed = await session.get(PresentationGenerationJob, job.id)
                assert progressed.completed_slides == 1 and progressed.current_stage == "render"

            # The payload-free fence keeps the same owner and fence contract.
            with _principal(*me):
                await svg_checkpoints._fence_checkpoint_authority(svg_job(), 0)
            for who, active in ((me, svg_job(fence=8)),):
                with _principal(*who):
                    with pytest.raises(checkpoints.AuthoredStageError) as fence:
                        await svg_checkpoints._fence_checkpoint_authority(active, 0)
                assert fence.value.code == lost
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_recovery_authority_runs_the_real_fenced_checkpoint_and_admission_checks(
    tmp_path, monkeypatch
):
    """``ensure_recovery_authority`` against real rows, with nothing stubbed.

    SQLite only: the admission contract reads the PostgreSQL control-plane
    revision on a PostgreSQL bind, which a table-only scratch database lacks.
    """
    from services import svg_presentation_checkpoint as svg_checkpoints
    from services.generation_core.drivers.svg.quality_recovery import (
        ensure_recovery_authority,
    )
    from services.public_operation_admission_models import PublicAdmissionRejected

    lost = "authored_workspace_operation_fence_lost"
    snapshot = {"digest": "sha256:style", "contract_version": "svg-static/v1"}

    async def scenario():
        engine, maker = await _checkpoint_database(tmp_path, monkeypatch)
        try:
            monkeypatch.setattr(svg_checkpoints, "async_session_maker", maker)
            owner, foreign = uuid.uuid4(), uuid.uuid4()
            me = (owner, "tenant-a")
            _, job = await _running_authored_job(maker)
            operation_id, admission_id = await _linked_operation(
                maker, job, owner_id=owner, admitted=True)
            async with maker() as session:
                stored = await session.get(PresentationGenerationJob, job.id)
                stored.request_payload = {
                    "_internal_svg_style_snapshot": snapshot, "common_header_mode": "free"}
                stored.svg_style_snapshot = snapshot
                session.add(stored)
                session.add(AuthoredSlideCheckpoint(
                    job_id=job.id, slide_index=0, role="content", svg_checkpoint={}))
                await session.commit()
            hooks = SimpleNamespace(async_session_maker=maker, recovery_snapshot=snapshot)

            def svg_job(fence=9):
                return SimpleNamespace(id=job.id, attempt_token="workspace-attempt",
                                       workspace_operation_id=operation_id,
                                       _workspace_fencing_token=fence)

            async def check(active=None, who=me, bound=hooks):
                with _principal(*who):
                    await ensure_recovery_authority(active or svg_job(), bound)

            async def refused(code, active=None, who=me):
                with pytest.raises(checkpoints.AuthoredStageError) as error:
                    await check(active, who)
                assert error.value.code == code

            await check()
            with pytest.raises(ValueError, match="svg_quality_recovery_durable_job_required"):
                await ensure_recovery_authority(None, hooks)
            with pytest.raises(ValueError, match="svg_quality_recovery_durable_job_required"):
                await ensure_recovery_authority(SimpleNamespace(workspace_operation_id=None), hooks)

            # The fenced checkpoint authority runs first and is the real one.
            await refused(lost, svg_job(fence=8))
            await refused("authored_workspace_operation_fencing_token_required",
                          svg_job(fence=None))
            # A foreign tenant cannot see the tenant-scoped admission, so the
            # fence itself is lost; a foreign owner passes the attempt/fence CAS
            # but fails the admission principal binding right after it.
            await refused(lost, who=(owner, "tenant-b"))
            with pytest.raises(ValueError, match="svg_quality_recovery_authority_mismatch"):
                await check(who=(foreign, "tenant-a"))
            await _set_columns(maker, WorkspaceOperation, operation_id, state="cancelled")
            await refused(lost)
            await _set_columns(maker, WorkspaceOperation, operation_id, state="running")
            await check()

            # The admitted style snapshot must still bind the current attempt.
            for bound in (
                SimpleNamespace(async_session_maker=maker,
                                recovery_snapshot={**snapshot, "digest": "sha256:other"}),
                SimpleNamespace(async_session_maker=maker),
            ):
                with pytest.raises(
                    ValueError, match="svg_quality_recovery_admission_snapshot_conflict"
                ):
                    await check(bound=bound)
            await _set_columns(maker, PresentationGenerationJob, job.id, request_payload={
                "_internal_svg_style_snapshot": {"digest": "sha256:other"}})
            with pytest.raises(
                ValueError, match="svg_quality_recovery_admission_snapshot_conflict"
            ):
                await check()
            await _set_columns(maker, PresentationGenerationJob, job.id, request_payload={
                "_internal_svg_style_snapshot": snapshot, "common_header_mode": "free"})
            await check()

            # The public contract and the remaining budget are checked last.
            await _set_columns(maker, WorkspaceOperation, operation_id, phase="draining")
            with pytest.raises(PublicAdmissionRejected) as contract:
                await check()
            assert contract.value.code == "public_operation_workspace_contract_conflict"
            await _set_columns(maker, WorkspaceOperation, operation_id, phase="active")
            await _set_columns(
                maker, PublicOperationAdmission, admission_id,
                estimated_consumed_microunits=101)
            with pytest.raises(ValueError, match="svg_quality_recovery_budget_exhausted"):
                await check()
            await _set_columns(
                maker, PublicOperationAdmission, admission_id,
                estimated_consumed_microunits=0)
            await check()
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_checkpoint_transitions_preserve_outputs_and_report_actual_progress(
    tmp_path, monkeypatch
):
    async def scenario():
        engine, maker = await _checkpoint_database(tmp_path, monkeypatch)
        try:
            status, job = await _running_authored_job(maker)
            await checkpoints.initialize_checkpoints(
                job_id=job.id,
                attempt_token="attempt-a",
                roles=["title", "content"],
            )

            progress, error = await checkpoints.progress_for_task(status.id)
            assert error is None
            assert progress == {
                "completed_slides": 0,
                "total_slides": 2,
                "percent": 0,
                "current_slide": None,
                "current_stage": "author",
                "stage_counts": {
                    "author": 0,
                    "illustrate": 0,
                    "render": 0,
                    "persist": 0,
                },
                "finalizing": False,
            }

            for slide_index in range(2):
                await checkpoints.start_stage(
                    job_id=job.id,
                    slide_index=slide_index,
                    stage="author",
                    attempt_token="attempt-a",
                )
                await checkpoints.complete_stage(
                    job_id=job.id,
                    slide_index=slide_index,
                    stage="author",
                    attempt_token="attempt-a",
                    html=f"<section>slide {slide_index + 1}</section>",
                )
                await checkpoints.start_stage(
                    job_id=job.id,
                    slide_index=slide_index,
                    stage="illustrate",
                    attempt_token="attempt-a",
                )
                await checkpoints.complete_stage(
                    job_id=job.id,
                    slide_index=slide_index,
                    stage="illustrate",
                    attempt_token="attempt-a",
                    skipped=True,
                )
                await checkpoints.start_stage(
                    job_id=job.id,
                    slide_index=slide_index,
                    stage="render",
                    attempt_token="attempt-a",
                )
                await checkpoints.complete_stage(
                    job_id=job.id,
                    slide_index=slide_index,
                    stage="render",
                    attempt_token="attempt-a",
                    png=f"png-{slide_index}".encode(),
                )
                await checkpoints.start_stage(
                    job_id=job.id,
                    slide_index=slide_index,
                    stage="persist",
                    attempt_token="attempt-a",
                )
                await checkpoints.complete_stage(
                    job_id=job.id,
                    slide_index=slide_index,
                    stage="persist",
                    attempt_token="attempt-a",
                )

                progress, _ = await checkpoints.progress_for_task(status.id)
                assert progress["completed_slides"] == slide_index + 1
                assert progress["percent"] == (50 if slide_index == 0 else 99)

            stored = await checkpoints.get_checkpoint(job.id, 0)
            assert stored.author_html == "<section>slide 1</section>"
            assert stored.illustrate_status == "skipped"
            assert checkpoints.read_valid_render(stored) == b"png-0"

            progress, error = await checkpoints.progress_for_task(status.id)
            assert error is None
            assert progress["stage_counts"] == {
                "author": 2,
                "illustrate": 2,
                "render": 2,
                "persist": 2,
            }
            assert progress["finalizing"] is True

            # Re-running an upstream stage invalidates downstream persistence and
            # therefore immediately lowers the durable completion count.
            await checkpoints.start_stage(
                job_id=job.id,
                slide_index=0,
                stage="render",
                attempt_token="attempt-a",
            )
            invalidated = await checkpoints.get_checkpoint(job.id, 0)
            assert invalidated.render_status == "running"
            assert invalidated.persist_status == "pending"
            progress, _ = await checkpoints.progress_for_task(status.id)
            assert progress["completed_slides"] == 1
            assert progress["percent"] == 50

            await checkpoints.complete_stage(
                job_id=job.id,
                slide_index=0,
                stage="render",
                attempt_token="attempt-a",
                png=b"png-0-rebuilt",
            )
            await checkpoints.start_stage(
                job_id=job.id,
                slide_index=0,
                stage="persist",
                attempt_token="attempt-a",
            )
            await checkpoints.complete_stage(
                job_id=job.id,
                slide_index=0,
                stage="persist",
                attempt_token="attempt-a",
            )

            async with maker() as session:
                published = await session.get(PresentationGenerationJob, job.id)
                published.state = "published"
                session.add(published)
                await session.commit()
            progress, _ = await checkpoints.progress_for_task(status.id)
            assert progress["completed_slides"] == 2
            assert progress["percent"] == 100
            assert progress["finalizing"] is False
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_filesystem_evidence_recovers_db_pointer_and_clears_render_failure(
    tmp_path, monkeypatch
):
    async def scenario():
        engine, maker = await _checkpoint_database(tmp_path, monkeypatch)
        try:
            _, job = await _running_authored_job(maker)
            await checkpoints.initialize_checkpoints(
                job_id=job.id,
                attempt_token="attempt-a",
                roles=["content"],
            )
            await checkpoints.start_stage(
                job_id=job.id,
                slide_index=0,
                stage="render",
                attempt_token="attempt-a",
            )
            async with maker() as session:
                stored = (
                    await session.execute(
                        select(AuthoredSlideCheckpoint).where(
                            AuthoredSlideCheckpoint.job_id == job.id,
                            AuthoredSlideCheckpoint.slide_index == 0,
                        )
                    )
                ).scalar_one()
                stored.render_status = "failed"
                stored.failed_stage = "render"
                stored.error = {"code": "render_pointer_commit_interrupted"}
                session.add(stored)
                await session.commit()

            attempt_id = checkpoints.evidence_attempt_id("attempt-a")
            base_path = checkpoints.render_artifact_path(job.id, 0, "attempt-a")
            render_path = base_path.with_name(
                f"{base_path.stem}-r0-crash-window.png"
            )
            html = "<section>durable html</section>"
            png = b"durable-png"
            metrics = {"revision": 0, "visible": True}
            crash_writer = (
                Path(__file__).parents[1]
                / "support"
                / "authored_checkpoint_crash_writer.py"
            )
            fastapi_root = Path(__file__).parents[2]
            subprocess_env = os.environ.copy()
            # The crash writer imports ``services`` (resolved via the editable
            # install regardless of cwd) which now transitively imports the
            # top-level ``migration_revision_contract`` module -- a loose
            # script at the fastapi root, not part of that editable package,
            # so it is only importable when the root itself is on sys.path.
            subprocess_env["PYTHONPATH"] = os.pathsep.join(
                filter(None, [str(fastapi_root), subprocess_env.get("PYTHONPATH")])
            )
            crashed = subprocess.run(
                [
                    sys.executable,
                    str(crash_writer),
                    "--render-path",
                    str(render_path),
                    "--attempt-id",
                    attempt_id,
                    "--slide-index",
                    "0",
                    "--revision",
                    "0",
                ],
                cwd=fastapi_root,
                env=subprocess_env,
                capture_output=True,
                text=True,
                check=False,
                timeout=30,
            )
            assert crashed.returncode == 91, crashed.stderr
            assert render_path.read_bytes() == png

            recovered = await checkpoints.get_checkpoint(job.id, 0)
            assert recovered.render_status == "completed"
            assert recovered.render_completed_at is not None
            assert recovered.failed_stage is None
            assert recovered.error is None
            bundle = checkpoints.read_valid_evidence(recovered)
            assert (bundle.html, bundle.png, bundle.metrics, bundle.revision) == (
                html,
                png,
                metrics,
                0,
            )

            # DB -> filesystem mismatch blocks the recovered pointer.
            recovered.render_sha256 = "0" * 64
            with pytest.raises(checkpoints.SlideCheckpointCorrupt):
                checkpoints.read_valid_evidence(recovered)
            recovered.render_sha256 = hashlib.sha256(png).hexdigest()

            # Filesystem current -> immutable revision mismatch also blocks.
            sidecar = render_path.with_name(f".{render_path.name}.evidence.json")
            current_payload = json.loads(sidecar.read_text("utf-8"))
            current_payload["metrics"]["visible"] = False
            current_payload["digest"] = checkpoints.SlideCheckpointBundle._digest_for_payload(
                current_payload["html"],
                png,
                current_payload["metrics"],
            )
            sidecar.write_text(
                json.dumps(current_payload, ensure_ascii=False),
                encoding="utf-8",
            )
            with pytest.raises(checkpoints.SlideCheckpointCorrupt):
                checkpoints.read_valid_evidence(recovered)

        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_failed_stage_is_durable_sanitized_and_injected_only_once(
    tmp_path, monkeypatch
):
    async def scenario():
        engine, maker = await _checkpoint_database(tmp_path, monkeypatch)
        try:
            status, job = await _running_authored_job(maker)
            await checkpoints.initialize_checkpoints(
                job_id=job.id,
                attempt_token="attempt-a",
                roles=["title", "content"],
            )
            monkeypatch.setenv("AUTHORED_TEST_FAILURES_ENABLED", "true")
            monkeypatch.setenv("ENVIRONMENT", "test")
            monkeypatch.setenv("AUTHORED_TEST_FAILURE_SLIDE", "2")
            monkeypatch.setenv("AUTHORED_TEST_FAILURE_STAGE", "author")

            checkpoint = await checkpoints.get_checkpoint(job.id, 1)
            assert checkpoints.should_inject_failure(
                checkpoint, slide_index=1, stage="author"
            )
            error = checkpoints.AuthoredStageError(
                slide_index=1,
                stage="author",
                code="authored_injected_failure",
                cause=RuntimeError("api_key=must-not-leak"),
            )
            safe = await checkpoints.fail_stage(
                job_id=job.id,
                slide_index=1,
                stage="author",
                attempt_token="attempt-a",
                error=error,
                request_id="request-456",
            )

            assert safe["slide_number"] == 2
            assert safe["stage"] == "author"
            assert "must-not-leak" not in safe["cause"]
            failed = await checkpoints.get_checkpoint(job.id, 1)
            assert failed.author_status == "failed"
            assert failed.failed_stage == "author"
            assert not checkpoints.should_inject_failure(
                failed, slide_index=1, stage="author"
            )

            progress, api_error = await checkpoints.progress_for_task(status.id)
            assert progress["current_slide"] == 2
            assert progress["current_stage"] == "author"
            assert api_error == safe
            async with maker() as session:
                stored_job = await session.get(PresentationGenerationJob, job.id)
                assert stored_job.resume_allowed is True
                assert stored_job.last_error == safe

            # The error marker remains while the failed stage is restarted, so
            # the test-only injection cannot charge/fail the resumed attempt twice.
            await checkpoints.start_stage(
                job_id=job.id,
                slide_index=1,
                stage="author",
                attempt_token="attempt-a",
            )
            restarting = await checkpoints.get_checkpoint(job.id, 1)
            assert restarting.author_status == "running"
            assert not checkpoints.should_inject_failure(
                restarting, slide_index=1, stage="author"
            )
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_checkpoint_root_defaults_under_app_data_directory(tmp_path, monkeypatch):
    monkeypatch.delenv("AUTHORED_CHECKPOINT_DIRECTORY", raising=False)
    monkeypatch.setenv("APP_DATA_DIRECTORY", str(tmp_path / "app-data"))

    root = checkpoints.checkpoint_root()

    assert root == (tmp_path / "app-data" / "authored-checkpoints").resolve()
    assert root.is_dir()


def test_clear_checkpoint_html_keeps_stage_history_and_render_pointer(
    tmp_path, monkeypatch
):
    async def scenario():
        engine, maker = await _checkpoint_database(tmp_path, monkeypatch)
        try:
            _, job = await _running_authored_job(maker)
            await checkpoints.initialize_checkpoints(
                job_id=job.id,
                attempt_token="attempt-a",
                roles=["title"],
            )
            for stage, kwargs in (
                ("author", {"html": "<section>authored</section>"}),
                ("illustrate", {"html": "<section>illustrated</section>"}),
                ("render", {"png": b"png-0"}),
                ("persist", {}),
            ):
                await checkpoints.start_stage(
                    job_id=job.id,
                    slide_index=0,
                    stage=stage,
                    attempt_token="attempt-a",
                )
                await checkpoints.complete_stage(
                    job_id=job.id,
                    slide_index=0,
                    stage=stage,
                    attempt_token="attempt-a",
                    **kwargs,
                )

            await checkpoints.clear_checkpoint_html(job.id)

            cleared = await checkpoints.get_checkpoint(job.id, 0)
            assert cleared.author_html is None
            assert cleared.illustrated_html is None
            assert cleared.author_status == "completed"
            assert cleared.persist_status == "completed"
            assert cleared.author_completed_at is not None
            assert cleared.render_path is not None
            assert checkpoints.read_valid_render(cleared) == b"png-0"
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_attempt_token_fences_stale_checkpoint_writes(tmp_path, monkeypatch):
    async def scenario():
        engine, maker = await _checkpoint_database(tmp_path, monkeypatch)
        try:
            _, job = await _running_authored_job(maker)
            await checkpoints.initialize_checkpoints(
                job_id=job.id,
                attempt_token="attempt-a",
                roles=["title"],
            )
            async with maker() as session:
                stored_job = await session.get(PresentationGenerationJob, job.id)
                stored_job.attempt_token = "attempt-b"
                session.add(stored_job)
                await session.commit()

            with pytest.raises(checkpoints.AuthoredStageError) as raised:
                await checkpoints.start_stage(
                    job_id=job.id,
                    slide_index=0,
                    stage="author",
                    attempt_token="attempt-a",
                )
            assert raised.value.code == "authored_attempt_lost"
            checkpoint = await checkpoints.get_checkpoint(job.id, 0)
            assert checkpoint.author_status == "pending"
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_invalid_resumed_html_fails_before_render_and_is_durably_non_retryable(
    tmp_path, monkeypatch
):
    async def scenario():
        engine, maker = await _checkpoint_database(tmp_path, monkeypatch)
        rendered_html = []
        _install_one_slide_pipeline_fakes(
            monkeypatch,
            rendered_html=rendered_html,
        )
        try:
            _, job = await _running_authored_job(maker, total_slides=1)
            await checkpoints.initialize_checkpoints(
                job_id=job.id,
                attempt_token="attempt-a",
                roles=["title"],
            )
            await checkpoints.start_stage(
                job_id=job.id,
                slide_index=0,
                stage="author",
                attempt_token="attempt-a",
            )
            unsafe = (
                "<!doctype html><html><head>"
                '<meta http-equiv="refresh" content="0;url=https://attacker.test">'
                "</head><body><main>unsafe checkpoint</main></body></html>"
            )
            await checkpoints.complete_stage(
                job_id=job.id,
                slide_index=0,
                stage="author",
                attempt_token="attempt-a",
                html=unsafe,
            )

            with pytest.raises(checkpoints.AuthoredStageError) as raised:
                await pipeline._author_illustrate_render_pipeline(
                    _one_slide_outline(),
                    _pipeline_brand(),
                    SimpleNamespace(
                        reference_mode="style_reference",
                        illustration_prompt=None,
                    ),
                    generation_job=job,
                )

            assert raised.value.stage == "author"
            assert raised.value.code == "authored_slide_author_failed"
            assert raised.value.retryable is False
            assert rendered_html == []
            failed = await checkpoints.get_checkpoint(job.id, 0)
            assert failed.author_status == "failed"
            assert failed.render_status == "pending"
            assert failed.failed_stage == "author"
            assert failed.error["retryable"] is False
            async with maker() as session:
                stored_job = await session.get(PresentationGenerationJob, job.id)
                assert stored_job.resume_allowed is False
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_resumed_completed_html_is_canonicalized_then_rendered_normally(
    tmp_path, monkeypatch
):
    async def scenario():
        engine, maker = await _checkpoint_database(tmp_path, monkeypatch)
        rendered_html = []
        _install_one_slide_pipeline_fakes(
            monkeypatch,
            rendered_html=rendered_html,
        )
        try:
            _, job = await _running_authored_job(maker, total_slides=1)
            await checkpoints.initialize_checkpoints(
                job_id=job.id,
                attempt_token="attempt-a",
                roles=["title"],
            )
            await checkpoints.start_stage(
                job_id=job.id,
                slide_index=0,
                stage="author",
                attempt_token="attempt-a",
            )
            resumable = (
                "<!doctype html><html><head><meta charset='utf-8'>"
                "<META CONTENT='width=device-width' NAME='VIEWPORT'>"
                "</head><body><main>safe checkpoint</main></body></html>"
            )
            await checkpoints.complete_stage(
                job_id=job.id,
                slide_index=0,
                stage="author",
                attempt_token="attempt-a",
                html=resumable,
            )

            result = await pipeline._author_illustrate_render_pipeline(
                _one_slide_outline(),
                _pipeline_brand(),
                SimpleNamespace(
                    reference_mode="style_reference",
                    illustration_prompt=None,
                ),
                generation_job=job,
            )

            assert len(rendered_html) == 1
            assert "viewport" not in rendered_html[0].lower()
            assert '<meta charset="utf-8">' in rendered_html[0]
            assert result.deck.htmls == rendered_html
            assert resumable != rendered_html[0]
            resumed = await checkpoints.get_checkpoint(job.id, 0)
            assert checkpoint_stage_html(resumed) == rendered_html[0]
            assert resumed.author_status == "completed"
            assert resumed.illustrate_status == "skipped"
            assert resumed.render_status == "completed"
            assert resumed.persist_status == "completed"
            assert resumed.failed_stage is None
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_budget_exhaustion_keeps_the_first_attempt_s_reason() -> None:
    """A retry must not replace *why* a slide failed with *that it ran out*.

    The second attempt only knows the slide already spent its provider calls.
    Recording that alone overwrites the one field that says where to look, so
    the first attempt's bounded diagnostic is carried forward beside it.
    """

    from services.authored_checkpoint_errors import AuthoredStageError

    first_attempt = {
        "code": "authored_slide_author_failed",
        "exception_type": "ValueError",
        "cause": "authored_batch_slide_missing",
        "fingerprint": "4f4d2e88899a67f8",
        "slide_index": 34,
        "html": "<!doctype html><html>...</html>",
    }
    exhausted = AuthoredStageError(
        slide_index=34,
        stage="author",
        code="authored_provider_attempt_budget_exhausted",
        retryable=False,
        provider_attempt_count=2,
        prior_failure=first_attempt,
    )

    recorded = exhausted.as_safe_dict()

    assert recorded["code"] == "authored_provider_attempt_budget_exhausted"
    assert recorded["prior_failure"] == {
        "code": "authored_slide_author_failed",
        "exception_type": "ValueError",
        "cause": "authored_batch_slide_missing",
        "fingerprint": "4f4d2e88899a67f8",
    }


def test_a_carried_reason_is_bounded_and_never_widens_the_record() -> None:
    from services.authored_checkpoint_errors import AuthoredStageError

    carried = AuthoredStageError(
        slide_index=0,
        stage="author",
        code="authored_provider_attempt_budget_exhausted",
        prior_failure={"cause": "x" * 900, "session_token": "secret", "code": 12},
    ).as_safe_dict()

    assert set(carried["prior_failure"]) == {"cause"}
    assert len(carried["prior_failure"]["cause"]) == 200


def test_no_prior_failure_leaves_the_record_unchanged() -> None:
    from services.authored_checkpoint_errors import AuthoredStageError

    plain = AuthoredStageError(
        slide_index=0, stage="author", code="authored_slide_author_failed"
    ).as_safe_dict()

    assert "prior_failure" not in plain
