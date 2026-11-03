"""The common-header decision flow end to end on a real checkpoint database.

A live vector deck failed outright because both header candidates' representative
page lost on body checks only. These tests drive prepare_document_header with
stubbed design and selection calls and representative pages, and check what the
deck ends with: the designed header when the header itself is sound, the reduced
header when no candidate's header is.
"""

import asyncio
import struct
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from services import svg_common_header_design_plan as policy
from services import svg_common_header_generation as generation
from services.svg_common_header_design_checkpoint import read_update_header_design

from test_svg_header_capacity_policy import _inputs

_PNG = b"\x89PNG\r\n\x1a\n" + b"\0\0\0\rIHDR" + struct.pack(">II", 1280, 720) + b"\0" * 16


def _candidate(candidate_id):
    field = {"node_id": "heading", "role": "title", "value_ref": "title",
             "box": {"x": 64, "y": 25, "width": 1152, "height": 60},
             "font_family": "Noto Sans KR", "font_weight": 700, "font_size_px": 32, "line_height_px": 44.8,
             "baseline_offset_px": 36, "max_lines": 1, "alignment": "left", "optional": False}
    return {"candidate_id": candidate_id, "revision": 1,
            "svg": ('<svg xmlns="http://www.w3.org/2000/svg" width="1280" height="720" viewBox="0 0 1280 720">'
                    '<text id="heading" fill="#111111"/></svg>'),
            "body_box": {"x": 64, "y": 170, "width": 1152, "height": 500}, "fields": [field]}


def _page(header_passed, *, body_failures):
    quality = {"passed": not body_failures and header_passed, "failures": list(body_failures),
               "common_header": {"version": "svg-common-header-render/v2", "passed": header_passed}}
    if not header_passed:
        quality["failures"].append({"kind": "svg_common_header_title_overflow"})
    result = {"quality": quality, "attempts": [{"receipt": {}}], "png_sha256": "png",
              "author_body_svg": '<svg xmlns="http://www.w3.org/2000/svg" width="1280" height="720"/>'}
    if body_failures:
        result["terminal"] = {"index": 0, "reason": "svg:svg_repair_budget_exhausted",
                              "remakeAttempted": False, "remakeOutcome": "verbatim_flag_only"}
    return result


def _run(tmp_path, monkeypatch, *, header_passed):
    from test_authored_checkpoint_service import _checkpoint_database, _running_authored_job
    from models.sql.presentation_generation_job import PresentationGenerationJob

    async def scenario():
        engine, maker = await _checkpoint_database(tmp_path, monkeypatch)
        try:
            _, job = await _running_authored_job(maker, total_slides=1)
            request, _plan, snapshot, outline, canvas = _inputs()
            async with maker() as session:
                row = await session.get(PresentationGenerationJob, job.id)
                row.svg_style_snapshot = deepcopy(snapshot)
                row.authored_outline = outline.model_dump(mode="json")
                session.add(row)
                await session.commit()
            from services.svg_canvas import svg_canvas_for_aspect_ratio
            ctx = SimpleNamespace(snapshot=snapshot, canvas=svg_canvas_for_aspect_ratio("16:9"),
                                  header_pages=None, count=1,
                                  source_reflow=False, raster_reconstruction=False, manifests=[{}])
            monkeypatch.setattr(generation, "preflight_header_selector", lambda: {"model": "fixture"})
            monkeypatch.setattr(generation, "_design_input", lambda *args: {"context": "bound"})
            monkeypatch.setattr(generation, "_design_reference_images", lambda *args: [])
            stages = []

            def build(payload, *, plan, stage, ordinal, **kwargs):
                async def execute():
                    stages.append(stage)
                    if stage == "design":
                        return {"result": {"candidates": [_candidate("first"), _candidate("second")]}}
                    return {"result": {"selection_input_digest": payload["selection_input_digest"],
                        "candidate_selection": {"selected_id": "choice-1", "candidate_revision": 1,
                                                "reason": "clear hierarchy"},
                        "representative_visual_review": {"candidate_id": "choice-1", "passed": True,
                                                         "harmony": "header and body agree", "known_defects": []}}}
                return execute

            monkeypatch.setattr(generation, "build_header_call", build)
            seen = []

            async def page_runner(candidate_ctx, request_, presentation_id, outline_, language, job_, index,
                                  state, hooks):
                result = _page(header_passed, body_failures=[{"kind": "asset_icon_omitted"},
                                                             {"kind": "source_text_paint_occluded"}])
                result["input_digest"] = generation._page_input_digest(candidate_ctx, request_, outline_,
                                                                       language, index)
                result["header_author_attempt_identity"] = dict(candidate_ctx.header_author_attempt_identity)
                await hooks.checkpoint(job_, index, payload=result)
                seen.append(candidate_ctx.header_author_attempt_identity["candidate_id"])
                return result, _PNG

            main_checkpoint = AsyncMock(return_value={})
            result = await generation.prepare_document_header(ctx, request, job.presentation_id, outline, "en", job,
                checkpoint=main_checkpoint, async_session_maker=maker, read_png=lambda payload: _PNG,
                write_png=None, page_runner=page_runner,
                page_hooks_factory=lambda checkpoint, **kwargs: SimpleNamespace(checkpoint=checkpoint),
                state_factory=lambda: None)
            document = await read_update_header_design(job, session_maker=maker)
            return result, document, stages, sorted(seen), main_checkpoint
        finally:
            await engine.dispose()
    return asyncio.run(scenario())


def test_body_defects_keep_the_designed_header_and_promote_the_body(tmp_path, monkeypatch):
    result, document, stages, seen, main_checkpoint = _run(tmp_path, monkeypatch, header_passed=True)
    assert seen == ["first", "second"] and stages == ["design", "selection"]
    assert result.snapshot["common_header_selection"]["version"] == "svg-common-header-selection/v2"
    assert document["status"] == "published"
    promoted = main_checkpoint.await_args_list[-1].kwargs["payload"]
    # Only the author's body is promoted; quality, render and rescue are redone by the page loop.
    assert promoted["svg_content"].startswith("<svg") and "quality" not in promoted and "terminal" not in promoted
    assert promoted["common_header_candidate_lineage"]["candidate_id"] in {"first", "second"}


def test_header_defects_on_every_candidate_reduce_the_header_instead_of_failing(tmp_path, monkeypatch):
    result, document, stages, _seen, _main = _run(tmp_path, monkeypatch, header_passed=False)
    assert stages == ["design"]  # no selection is bought without an eligible candidate
    assert document["fallback"]["reason"] == "svg_common_header_representative_pages_ineligible"
    assert result.snapshot["common_header_selection"]["mode"] in policy.require_header_design_plan(
        _inputs()[0])["policy"]["fallbacks"]
