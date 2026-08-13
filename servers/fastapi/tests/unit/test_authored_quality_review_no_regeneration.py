import asyncio
import uuid
from types import SimpleNamespace

from models.presentation_from_template import AuthoredQualityReviewRequest
from services import authored_quality_review_service as quality_service
from services import slidespec_authored_importer
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
    presentation_id = uuid.uuid4()
    presentation = SimpleNamespace(
        id=presentation_id,
        owner_id=None,
        get_presentation_outline=lambda: SimpleNamespace(slides=[object()]),
        is_authored=lambda: True,
    )
    slide = SimpleNamespace(
        html_content="<html><body>kept verbatim</body></html>",
        properties={},
        is_authored=lambda: True,
    )

    class _Rows:
        def scalars(self):
            return self

        def all(self):
            return [slide]

    class _Session:
        async def get(self, _model, _identifier):
            return presentation

    async def fake_execute(_session, _statement):
        return _Rows()

    import_arguments = {}

    async def fake_imports(**kwargs):
        import_arguments.update(kwargs)
        return AuthoredImportExecution(
            slides=(slide,),
            imported_positions=(),
            failures={0: "import.projection_failed"},
        )

    async def forbidden_legacy(*_args, **_kwargs):
        raise AssertionError("quality.review must not invoke full-page reauthoring")

    completed = {}

    async def capture_status(_session, _task, **kwargs):
        completed.update(kwargs)

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
    monkeypatch.setattr(quality_service, "_set_status", capture_status)

    asyncio.run(
        quality_service._run_authored_quality_review_unlocked(
            presentation_id,
            AuthoredQualityReviewRequest(scope="all", slidespec_opt_in=False),
            SimpleNamespace(),
            _Session(),
        )
    )

    assert completed["status"] == "completed"
    assert completed["data"]["fixed_count"] == 0
    assert completed["data"]["remaining_slide_indices"] == [0]
    assert completed["data"]["routing"]["automatic_page_regeneration"] is False
    assert import_arguments["opt_in"] is True
