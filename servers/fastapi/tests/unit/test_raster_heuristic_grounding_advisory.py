"""A page planned without the vision structurer is visible in the analysis, once."""

from __future__ import annotations

from PIL import Image

from services.source_content import raster_page_structuring
from services.source_content.raster_page_fallback import robust_plan
from services.source_content.raster_page_structuring import structured_plan
from services.source_content.raster_redesign_ocr import RedesignLine, RedesignWord


def _page():
    image = Image.new("RGB", (1000, 560), "white")
    words = (
        RedesignWord("매출", (40, 30, 120, 60), 0.95, "test", True),
        RedesignWord("현황", (180, 30, 120, 60), 0.95, "test", True),
        RedesignWord("본문", (40, 300, 80, 20), 0.95, "test", True),
    )
    lines = (
        RedesignLine((0, 1), (40, 30, 260, 60), 60.0, 1.0),
        RedesignLine((2,), (40, 300, 80, 20), 20.0, 1.0),
    )
    return image, words, lines


def _failing(*_args):
    raise TimeoutError("provider down")


class _Unusable:
    model = "stub"

    def __call__(self, system, parts, schema, name):
        return {"title": [], "groups": []} if name != "line_transcripts" else None


def test_no_structurer_adds_no_page_advisory():
    image, words, lines = _page()
    assert structured_plan(image, words, lines, None).advisories == ()
    plan, advisories = robust_plan(image, words, lines, None, None, None)
    assert advisories == [] and plan.structured_by is None


def test_provider_failure_marks_the_page_as_heuristic():
    image, words, lines = _page()
    plan = structured_plan(image, words, lines, _failing)
    assert plan.structured_by is None
    assert plan.advisories == ("heuristic_plan:structuring_failed:TimeoutError",)
    _, advisories = robust_plan(image, words, lines, _failing, None, None)
    assert advisories == ["heuristic_plan:structuring_failed:TimeoutError"]


def test_retry_is_the_existing_single_one():
    image, words, lines = _page()
    calls = []

    def flaky(system, parts, schema, name):
        calls.append(name)
        raise TimeoutError("down")

    structured_plan(image, words, lines, flaky)
    assert len(calls) == 2  # the first request plus one retry, no second layer


def test_rejected_structure_marks_the_page(monkeypatch):
    image, words, lines = _page()
    monkeypatch.setattr(raster_page_structuring, "apply_structure", lambda *args, **kwargs: None)
    plan = structured_plan(image, words, lines, _Unusable())
    assert plan.advisories == ("heuristic_plan:structuring_rejected:unusable_structure",)


def test_a_raising_structured_plan_reports_only_the_failure(monkeypatch):
    image, words, lines = _page()
    real = raster_page_structuring.structured_plan

    def broken_with_structurer(image, words, lines, structurer, *rest):
        if structurer is not None:
            raise ValueError("boom")
        return real(image, words, lines, structurer, *rest)

    monkeypatch.setattr(raster_page_structuring, "structured_plan", broken_with_structurer)
    monkeypatch.setenv("RASTER_STRICT_PAGE_ERRORS", "0")
    _, advisories = robust_plan(image, words, lines, _failing, None, None)
    assert advisories == ["page_structuring_failed:ValueError"]
