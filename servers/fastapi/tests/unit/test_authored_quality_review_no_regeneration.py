from tests.support.existing_authored_rows import add_existing_authored_presentation
import asyncio
import uuid
from types import SimpleNamespace
import pytest

from sqlalchemy import inspect
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from models.presentation_from_template import AuthoredQualityReviewRequest
from models.sql.async_presentation_generation_status import (
    AsyncPresentationGenerationTaskModel,
)
from models.sql.presentation import PresentationModel
from services import authored_quality_review_service as quality_service
from services import slidespec_authored_executor
from services import slidespec_authored_importer
from services.slidespec_artifact_store import ACTIVE_POINTER_KEY
from services.slidespec_authored_importer import AuthoredImportExecution


def test_unclaimed_slides_are_explained_without_page_regeneration() -> None:
    data = quality_service._unclaimed_slidespec_review_data(
        [4, 1],
        {1: "import.projection_failed", 4: "system.kill_switch_enabled"},
    )

    assert data["fixed_count"] == 0
    assert data["version_saved"] is False
    assert data["remaining_slide_indices"] == [1, 4]
    assert data["remaining_issue_count"] == 2
    assert data["routing"]["automatic_page_regeneration"] is False
    assert data["metrics"]["automatic_page_regeneration_calls"] == 0
    assert all(
        "quality_review.automatic_page_regeneration_disabled"
        in result["reason_codes"]
        for result in data["slide_results"]
    )


def test_review_import_failure_never_calls_legacy_page_reauthor(monkeypatch) -> None:
    from services.workspace_quality_operation_handler import (
        release_review_checker_policy,
        release_review_checker_policy_digest,
    )
    from services.authored_quality_review_service import _bind_canonical_review_policy

    runtime = {"model": "paired-review-model", "change_during_import": False}
    monkeypatch.setattr(quality_service.author_vision_qa, "get_model", lambda: runtime["model"])
    monkeypatch.setattr(quality_service.author_vision_qa, "STYLE_COMPARISON_VQA_PROMPT_VERSION", "paired-comparison/v-test")
    monkeypatch.setattr(quality_service.author_vision_qa, "CRITIQUE_PROMPT_VERSION", "legacy-critique/v-test")
    monkeypatch.setattr(quality_service.author_vision_qa, "_VQA_PROMPT_VERSION", "legacy-cache/v-test")
    presentation_id = uuid.uuid4()
    owner_id = uuid.uuid4()
    presentation = PresentationModel(
        id=presentation_id,
        owner_id=owner_id,
        content="Preserve authored slides",
        n_slides=2,
        language="Korean",
        mode="authored",
        revision=7,
        outlines={"slides": [{"content": "Import failure"}, {"content": "Sibling"}]},
    )
    slide = SimpleNamespace(
        html_content="<html><body>kept verbatim</body></html>",
        properties={},
        is_authored=lambda: True,
    )
    sibling = SimpleNamespace(
        html_content="<html><body>also kept verbatim</body></html>",
        properties={},
        is_authored=lambda: True,
    )

    class _Rows:
        def scalars(self):
            return self

        def all(self):
            return [slide, sibling]

    async def fake_execute(_session, _statement):
        return _Rows()

    import_arguments = {}
    executor_arguments = {}
    release_base = {
        "presentation_revision": 7,
        "source_render_identity_digest": "a" * 64,
        "checker_policy_digest": "b" * 64,
    }
    task = AsyncPresentationGenerationTaskModel(
        id=f"workspace-quality-{uuid.uuid4()}",
        owner_id=owner_id,
        status="pending",
        data={"_workspace_release_review_base": release_base},
    )

    async def fake_imports(**kwargs):
        import_arguments.update(kwargs)
        if runtime["change_during_import"]:
            runtime["model"] = "changed-during-review"
        await kwargs["session"].rollback()
        assert inspect(task).persistent
        assert "data" in inspect(task).expired_attributes
        assert "owner_id" in inspect(presentation).expired_attributes
        return AuthoredImportExecution(
            slides=(slide, sibling),
            imported_positions=(),
            failures={0: "import.publish.failed", 1: "import.publish.failed"},
        )

    async def fake_canonical_review(**kwargs):
        executor_arguments.update(kwargs)
        assert kwargs["owner_id"] == owner_id
        # Accessing the ORM owner here would still require implicit async IO.
        assert "owner_id" in inspect(presentation).expired_attributes
        data = quality_service._unclaimed_slidespec_review_data(
            [1], {1: "repair.typed_pipeline_unavailable"}
        )
        data["remaining_issue_indices"] = data.pop("remaining_slide_indices")
        return SimpleNamespace(claimed_positions=(1,), data=data)

    async def forbidden_legacy(*_args, **_kwargs):
        raise AssertionError("quality.review must not invoke full-page reauthoring")

    monkeypatch.setattr(quality_service, "_execute_sqlalchemy", fake_execute)
    monkeypatch.setattr(
        slidespec_authored_importer,
        "ensure_authored_slidespec_imports",
        fake_imports,
    )
    monkeypatch.setattr(
        quality_service,
        "_run_legacy_authored_quality_review_unlocked",
        forbidden_legacy,
    )
    monkeypatch.setattr(
        slidespec_authored_executor,
        "execute_slidespec_authored_review",
        fake_canonical_review,
    )

    async def run_review_after_rollback():
        nonlocal presentation
        engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        try:
            async with engine.begin() as connection:
                for model in (PresentationModel, AsyncPresentationGenerationTaskModel):
                    await connection.run_sync(model.__table__.create)
            async with AsyncSession(engine, expire_on_commit=False) as session:
                await add_existing_authored_presentation(session, presentation)
                session.add_all([task])
                await session.commit()
                presentation = await session.get(PresentationModel, presentation_id)
                for partial_claim, change_during_import in ((False, False), (True, False), (True, True)):
                    runtime.update(model="paired-review-model", change_during_import=change_during_import)
                    before_policy = quality_service._style_comparison_observation_metadata()
                    before_digest = release_review_checker_policy_digest()
                    assert release_review_checker_policy()["critiquePromptVersion"] == "paired-comparison/v-test"
                    assert before_policy != quality_service._review_observation_metadata()
                    await session.refresh(presentation)
                    await session.refresh(task)
                    task.status = "pending"
                    await session.commit()
                    sibling.properties = {ACTIVE_POINTER_KEY: {}} if partial_claim else {}
                    # Even expire_on_commit=False cannot prevent rollback expiry.
                    await session.begin()
                    await quality_service._run_authored_quality_review_unlocked(
                        presentation_id,
                        AuthoredQualityReviewRequest(scope="all", slidespec_opt_in=False),
                        task,
                        session,
                    )
                    await session.refresh(task)
                    assert task.status == "completed"
                    assert task.error is None
                    assert task.owner_id == owner_id
                    assert task.data["_workspace_release_review_base"] == release_base
                    assert task.data["fixed_count"] == 0
                    assert task.data["incomplete_measurement_count"] == 2
                    assert task.data["clean_slide_count"] == 0
                    assert task.data["remaining_slide_indices"] == [0, 1]
                    assert task.data["remaining_issues"][0]["reason_code"] == "import.publish.failed"
                    assert task.data["routing"]["automatic_page_regeneration"] is False
                    assert task.data["review_policy"] == before_policy
                    assert (release_review_checker_policy_digest() != before_digest) is change_during_import
                    await session.refresh(presentation)
                    assert presentation.revision == 7
        finally:
            await engine.dispose()

    asyncio.run(run_review_after_rollback())
    assert import_arguments["opt_in"] is True
    assert import_arguments["owner_id"] == owner_id
    assert executor_arguments["positions"] == [1]
    paired = {"model": "paired", "prompt_version": "paired/v1", "cache_version": "paired/v1"}
    legacy = {"model": "legacy", "prompt_version": "legacy/v1", "cache_version": "legacy/v1"}
    with pytest.raises(ValueError, match="checker_policy_conflict"):
        quality_service._merge_slidespec_review_data(
            presentation_id=presentation_id, request=AuthoredQualityReviewRequest(),
            legacy={"review_policy": legacy}, canonical={"review_policy": paired})
    with pytest.raises(ValueError, match="checker_policy_conflict"):
        _bind_canonical_review_policy({"review_policy": legacy}, paired)
