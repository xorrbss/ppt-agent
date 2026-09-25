"""Stage timing recorder: exclusive stages, thread propagation, vision calls, digest invariance."""

from __future__ import annotations

import json
import threading
import time
import typing
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest

from services.source_content import raster_stage_timing as timing
from services.source_content.raster_contracts import (
    RasterAnalysis,
    RasterStageTimings,
    TimingStage,
    raster_digest,
    require_analysis_digest,
    seal_analysis,
)
from services.source_content.raster_llm_client import VisionJsonClient

SHA = "a" * 64


def test_stage_names_match_the_contract():
    assert set(typing.get_args(TimingStage)) == {*timing.STAGES, timing.RESIDUAL}
    assert timing.vision_stage("slide_structure") == "structure_calls"
    assert timing.vision_stage("table_structure") == "tables"
    assert timing.vision_stage("line_transcripts") == "transcription"
    assert timing.vision_stage("anything_else") == "vision_other"
    with pytest.raises(ValueError):
        with timing.stage(1, "not_a_stage"):
            pass


def test_everything_is_a_no_op_without_an_active_run():
    with timing.page(1), timing.stage(None, "ocr"), timing.stage(3, "plan"):
        timing.record_call(0.1, 0.2, "slide_structure")
    assert timing.current_run() is None


def test_nested_stages_are_exclusive_and_the_rest_is_other():
    with timing.activate() as run:
        with timing.page(1):
            with timing.stage(None, "plan"):
                time.sleep(0.03)
                with timing.stage(None, "number_units"):
                    time.sleep(0.05)
                time.sleep(0.03)
            time.sleep(0.02)
    payload = run.payload()
    stages = payload["pages"][0]["stages"]
    assert 0.05 <= stages["number_units"] < 0.1
    assert 0.06 <= stages["plan"] < 0.1  # the nested stage's time is not counted twice
    assert 0.02 <= stages["other"] < 0.06
    wall = payload["pages"][0]["wall_seconds"]
    assert abs(sum(stages.values()) - wall) < 1e-6
    assert payload["wall_seconds"] >= wall


def test_page_tasks_carry_the_run_into_pool_threads_with_one_context_per_task():
    def work(page):
        with timing.stage(None, "ocr"):
            time.sleep(0.01 * page.page_number)
        with timing.stage(None, "rereads"):
            timing.record_call(0.001, 0.002, "slide_structure")
        return threading.current_thread().name

    pages = [SimpleNamespace(page_number=n) for n in range(1, 7)]
    with timing.activate() as run:
        with ThreadPoolExecutor(max_workers=3) as pool:
            threads = set(pool.map(timing.page_task(work), pages))
        with ThreadPoolExecutor(max_workers=3) as pool:
            list(pool.map(work, pages))  # without page_task the workers see no run
    assert len(threads) > 1
    payload = RasterStageTimings.model_validate(run.payload())
    assert [page.page_number for page in payload.pages] == list(range(1, 7))
    for page in payload.pages:
        assert set(page.stages) == {"ocr", "rereads", "other"}
        assert page.stages["ocr"] >= 0.01 * page.page_number * 0.9
        assert len(page.vision_calls) == 1
    assert payload.stage_totals["ocr"] == pytest.approx(sum(p.stages["ocr"] for p in payload.pages))
    assert payload.stage_page_max["ocr"] == max(p.stages["ocr"] for p in payload.pages)
    assert payload.vision_call_count == 6 and payload.unpaged_vision_calls == ()
    assert payload.vision_wait_seconds == pytest.approx(0.006)
    assert payload.vision_latency_seconds == pytest.approx(0.012)


class _TimedClient(VisionJsonClient):
    def __init__(self, delay: float):
        super().__init__(settings=None, provider="stub", model="stub-vision")
        self._gate = threading.BoundedSemaphore(1)
        self.delay = delay

    def _generate(self, system, parts, schema, name):
        time.sleep(self.delay)
        return SimpleNamespace(content={"name": name}, usage=None)


def test_vision_client_records_gate_wait_and_latency_per_call():
    client = _TimedClient(0.05)
    with timing.activate() as run:
        def call(page_number: int):
            with timing.page(page_number):
                return client("system", ["text"], {}, "slide_structure")

        bound = timing.bind(call)
        with ThreadPoolExecutor(max_workers=2) as pool:
            answers = list(pool.map(bound, (1, 2)))
    assert answers == [{"name": "slide_structure"}] * 2
    payload = RasterStageTimings.model_validate(run.payload())
    calls = [call for page in payload.pages for call in page.vision_calls]
    assert [call.name for call in calls] == ["slide_structure"] * 2
    assert all(0.045 <= call.latency_seconds < 0.2 for call in calls)
    assert max(call.wait_seconds for call in calls) >= 0.04  # one call waited for the other at the gate
    for page in payload.pages:
        assert page.stages["structure_calls"] >= 0.045


def _analysis() -> RasterAnalysis:
    return seal_analysis({
        "source": {"source_sha256": SHA, "page_count": 1, "canvas_width_emu": 1000, "canvas_height_emu": 500},
        "target_canvas": {"orientation": "landscape", "width_units": 1188, "height_units": 840},
        "pages": [{
            "page_number": 1,
            "regions": [{
                "region_id": "p1-r1", "page_number": 1, "role": "body",
                "bbox": {"x": 0, "y": 0, "width": 100, "height": 50}, "z_order": 0, "text": "매출 40%",
            }],
        }],
        "unresolved": ["page_1:human_review_required"],
    })


def _timings() -> RasterStageTimings:
    with timing.activate() as run:
        with timing.page(1), timing.stage(None, "ocr"):
            timing.record_call(0.25, 1.5, "slide_structure")
    return RasterStageTimings.model_validate(run.payload())


def test_digest_is_identical_with_and_without_stage_timings():
    plain = _analysis()
    timed = plain.model_copy(update={"stage_timings": _timings()})
    resealed = seal_analysis(timed)
    assert resealed.stage_timings is not None and resealed.stage_timings.pages[0].vision_calls[0].latency_seconds == 1.5
    assert resealed.digest == plain.digest
    other = seal_analysis(plain.model_copy(update={"stage_timings": _timings()}))
    assert other.digest == plain.digest  # a different run's timings never move the digest
    require_analysis_digest(resealed)
    stored = RasterAnalysis.model_validate_json(resealed.model_dump_json())
    require_analysis_digest(stored)
    assert stored.stage_timings == resealed.stage_timings
    # Content changes still move the digest.
    page = plain.pages[0]
    edited = page.model_copy(update={"regions": (page.regions[0].model_copy(update={"text": "매출 41%"}),)})
    assert seal_analysis(timed.model_copy(update={"pages": (edited,)})).digest != plain.digest


def test_changing_stage_timings_never_changes_the_digest():
    plain = _analysis()
    first = _timings()
    changed = first.model_copy(update={"wall_seconds": first.wall_seconds + 99, "vision_call_count": 7})
    for value in (first, changed, None):
        resealed = seal_analysis(plain.model_copy(update={"stage_timings": value}))
        assert resealed.digest == plain.digest
        require_analysis_digest(resealed)
    # A stored record whose timings were edited after sealing still verifies.
    require_analysis_digest(seal_analysis(plain.model_copy(update={"stage_timings": first})).model_copy(
        update={"stage_timings": changed}
    ))


def test_record_sealed_before_stage_timings_existed_verifies():
    """Literal JSON of a record sealed by the previous code: no "stage_timings" key at all."""

    payload = _analysis().model_dump(mode="json", exclude={"digest", "stage_timings"})
    assert "stage_timings" not in payload
    legacy_json = json.dumps({**payload, "digest": raster_digest(payload)}, ensure_ascii=False)
    stored = RasterAnalysis.model_validate_json(legacy_json)
    assert stored.stage_timings is None
    require_analysis_digest(stored)
    assert seal_analysis(stored).digest == stored.digest
    timed = stored.model_copy(update={"stage_timings": _timings()})
    require_analysis_digest(timed)
    assert seal_analysis(timed).digest == stored.digest


def test_pre_redesign_record_with_stage_timings_verifies_through_the_legacy_branch():
    payload = _analysis().model_dump(mode="json", exclude={"digest", "stage_timings"})
    for page in payload["pages"]:
        for key in ("reference_region", "unpromoted_ocr_indices"):
            page.pop(key)
        for region in page["regions"]:
            for key in (
                "confidence", "reading_order", "table_cells", "review_reason",
                "ocr_token_indices", "editable_fallback", "group_id", "heading_line_count",
            ):
                region.pop(key)
    legacy = RasterAnalysis.model_validate({**payload, "digest": raster_digest(payload)})
    require_analysis_digest(legacy)
    require_analysis_digest(legacy.model_copy(update={"stage_timings": _timings()}))
    tampered = legacy.model_copy(update={"stage_timings": _timings(), "unresolved": ("changed",)})
    with pytest.raises(ValueError, match="raster_analysis_digest_mismatch"):
        require_analysis_digest(tampered)
