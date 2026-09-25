"""A line that needed the second (transcription) reader and did not get it stays pixels.

The structured path publishes a line only when two independent readers agree. When
the transcription call fails (provider error, rate limit, timeout, no answer,
invalid JSON), no requested line has its second reading: none is agreed, every
such line keeps its pixels and the page carries one ``transcription_failed``
advisory. Lines that never needed the second reader keep the older rules.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from PIL import Image

from services.source_content import raster_redesign
from services.source_content.raster_captures import PageContext
from services.source_content.raster_llm_transcribe import TranscriptionUnusable, request_transcripts
from services.source_content.raster_page_fallback import robust_plan
from services.source_content.raster_page_structuring import plan_is_lossless, structured_plan
from services.source_content.raster_redesign_ocr import RedesignLine, RedesignWord
from services.source_content.raster_structure_apply import apply_structure

STRUCTURE = {"title": ["L1"], "groups": [{"heading": [], "body": ["L2"]}], "screenshots": []}
AGREED = {"lines": [{"id": "L1", "text": "매출 현황"}, {"id": "L2", "text": "생성형 AI가"}]}


def _word(text, box, confidence=0.95):
    return RedesignWord(text, box, confidence, "test", True)


def _page():
    image = Image.new("RGB", (1672, 941), (246, 248, 252))
    words = (
        _word("매출", (60, 60, 120, 60)),
        _word("현황", (200, 60, 120, 60)),
        _word("생성형", (60, 240, 80, 20)),
        _word("시가", (150, 240, 50, 20)),
    )
    lines = (
        RedesignLine((0, 1), (60, 60, 260, 60), 60.0, 1.0, alternates=("매출 현황", "매출 현황")),
        RedesignLine((2, 3), (60, 240, 140, 20), 20.0, 1.0, alternates=("생성형 시가", "생성형 시가")),
    )
    return image, words, lines


class _Vision:
    """Canned structure; ``transcribe`` answers (or raises) for the transcription call."""

    model = "stub-vision"

    def __init__(self, transcribe, structure=None):
        self.transcribe = transcribe
        self.structure = structure or STRUCTURE
        self.names: list[str] = []

    def __call__(self, system, parts, schema, name):
        self.names.append(name)
        if name == "line_transcripts":
            return self.transcribe()
        return self.structure


def _raise(error):
    def call():
        raise error

    return call


def _editable(plan) -> list[str]:
    texts = [r.text for r in plan.regions if r.role in ("title", "body") and not r.editable_fallback]
    return texts + [str(r.table_cells) for r in plan.regions if r.role == "table" and r.table_cells is not None]


FAILURES = [
    (_raise(TimeoutError("timed out")), "TimeoutError"),
    (_raise(RuntimeError("429 Too Many Requests")), "RuntimeError"),
    (lambda: None, "TranscriptionUnusable"),  # the client's answer to invalid JSON
    (lambda: {"lines": "not a list"}, "TranscriptionUnusable"),
    (lambda: {"lines": []}, "TranscriptionUnusable"),
    (lambda: {"lines": [{"id": "L9", "text": "x"}, {"id": "L1"}]}, "TranscriptionUnusable"),
]


def test_the_working_reader_publishes_agreed_text():
    image, words, lines = _page()
    plan = structured_plan(image, words, lines, _Vision(lambda: AGREED))
    assert plan.structured_by == "stub-vision" and plan.advisories == ()
    assert "매출 현황" in _editable(plan)


@pytest.mark.parametrize(("transcribe", "error"), FAILURES)
def test_a_failed_reader_keeps_every_line_it_should_have_read_as_pixels(transcribe, error):
    image, words, lines = _page()
    vision = _Vision(transcribe)
    plan = structured_plan(image, words, lines, vision)
    assert "line_transcripts" in vision.names
    assert plan.structured_by == "stub-vision"  # the structure itself is kept
    assert _editable(plan) == []  # no line was confirmed by the second reader
    assert plan_is_lossless(plan, plan.words)
    assert plan.advisories == (f"transcription_failed:{error}",)  # one per page, not per line


def test_a_partial_answer_keeps_only_the_missing_lines_as_pixels_without_an_advisory():
    image, words, lines = _page()
    plan = structured_plan(image, words, lines, _Vision(lambda: {"lines": [AGREED["lines"][0]]}))
    assert _editable(plan) == ["매출 현황"]
    assert plan.advisories == ()


def test_the_advisory_reaches_analysis_unresolved_once_per_page(monkeypatch):
    image, words, lines = _page()
    monkeypatch.setattr(raster_redesign, "redesign_ocr", lambda *_args, **_kwargs: (words, lines))
    real = raster_redesign.robust_plan
    # No local OCR binary in unit tests: the page pipeline runs without re-reads.
    monkeypatch.setattr(raster_redesign, "robust_plan", lambda img, ws, ls, s, *_: real(img, ws, ls, s, None, None))
    page = SimpleNamespace(
        page_number=7, media_part="ppt/media/image7.png", media_bytes=b"png", image=image, occurrence_id="occ-7"
    )
    result, _crops, issues = raster_redesign._page_result(
        page,
        (12192000, 6858000),
        tesseract_path=raster_redesign.Path(__file__),
        tessdata_path=raster_redesign.Path(__file__).parent,
        structurer=_Vision(_raise(TimeoutError("timed out"))),
    )
    assert [i for i in issues if "transcription_failed" in i] == ["advisory:page_7:transcription_failed:TimeoutError"]
    assert not [r for r in result["regions"] if r["role"] in ("title", "body") and not r["editable_fallback"]]


def test_robust_plan_reports_the_transcription_failure():
    image, words, lines = _page()
    plan, advisories = robust_plan(image, words, lines, _Vision(lambda: None), None, None)
    assert advisories == ["transcription_failed:TranscriptionUnusable"]
    assert _editable(plan) == []


def test_a_fallback_title_also_needs_the_second_reader():
    image, words, lines = _page()
    plan = structured_plan(image, words, lines, _Vision(_raise(TimeoutError()), structure={"groups": []}))
    # The model named no title: the top-band title is still a line the reader had to confirm.
    assert _editable(plan) == [] and plan.advisories == ("transcription_failed:TimeoutError",)


def test_lines_that_need_no_second_reading_keep_the_older_rules():
    """Every line excluded from transcription (decoration here): no call, no advisory,
    and the older single-reading rules decide exactly as before."""

    image, words, lines = _page()
    structure = {**STRUCTURE, "decoration": ["L1", "L2"]}
    vision = _Vision(_raise(AssertionError("must not be called")), structure=structure)
    plan = structured_plan(image, words, lines, vision)
    assert "line_transcripts" not in vision.names and plan.advisories == ()
    assert "매출 현황" in _editable(plan)  # sound OCR title, unchanged older rule


def test_request_transcripts_says_when_no_line_needs_a_second_reading():
    image, words, lines = _page()
    page = PageContext(image, words, lines)
    called = []
    raw = {"decoration": ["L1"], "screenshots": [{"lines": ["L2"]}]}
    assert request_transcripts(page, raw, lambda *args: called.append(args)) is None and called == []
    with pytest.raises(TranscriptionUnusable):
        request_transcripts(page, STRUCTURE, lambda *_args: None)
    assert request_transcripts(page, STRUCTURE, lambda *_args: AGREED) == {0: "매출 현황", 1: "생성형 AI가"}


def test_apply_structure_distinguishes_no_reader_from_a_failed_reader():
    image, words, lines = _page()
    page = PageContext(image, words, lines)
    older = apply_structure(page, [], STRUCTURE, "stub", {}, {}, None)
    failed = apply_structure(page, [], STRUCTURE, "stub", {}, {}, {})
    agreed = apply_structure(page, [], STRUCTURE, "stub", {}, {}, {0: "매출 현황", 1: "생성형 AI가"})
    assert "매출 현황" in _editable(older)  # no second reader was needed: older rules
    assert _editable(failed) == []  # needed and missing: not agreed
    assert "매출 현황" in _editable(agreed)


def test_the_old_no_transcription_scenario_now_keeps_its_lines_as_pixels():
    """The scenario ``test_raster_redesign_analysis_llm`` used to run with no transcription
    answer (None) reached the older single-reader rules, which published the title and a
    model correction. Without the second reading nothing is confirmed: every line keeps
    its pixels, no correction is applied, and the page says why once."""

    from tests.unit.test_raster_redesign_analysis_llm import _page as llm_page
    from tests.unit.test_raster_redesign_analysis_llm import _Stub as LlmStub

    image, words, lines = llm_page()
    stub = LlmStub(
        {
            "title": ["L1"],
            "groups": [{"heading": ["L2"], "body": ["L3", "L4"], "captures": [], "icons": []}],
            "screenshots": [{"captures": [], "lines": ["L5"]}],
            "corrections": [{"line": "L4", "text": "AI가 지원"}, {"line": "L3", "text": "영업이익은 99%"}],
        }
    )
    plan = structured_plan(image, words, lines, stub)
    assert plan.structured_by == "stub-vision" and plan_is_lossless(plan, plan.words)
    assert _editable(plan) == []
    assert plan.corrections == ()
    assert plan.advisories == ("transcription_failed:TranscriptionUnusable",)
    assert any(r.role == "screenshot" and 8 in r.word_indices for r in plan.regions)  # structure still applied
