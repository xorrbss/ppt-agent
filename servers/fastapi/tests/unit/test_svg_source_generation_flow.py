"""Exercise the real SVG page loop with isolated provider/storage boundaries."""
from copy import deepcopy
import asyncio
from functools import wraps
from types import SimpleNamespace
from unittest.mock import AsyncMock
import uuid

import pytest

from test_svg_source_layout import manifest_for, plan_for
from services import svg_presentation_service as generation
from services.authored_checkpoint_errors import AuthoredStageError
from services.svg_source_layout import assemble_source_svg
from utils.llm_calls.author_slide_svg import SvgAuthorResult
from workers.public_render_worker import _admit_html
from workers.public_worker_runtime import PublicWorkerExecutionError


def run_async(function):
    @wraps(function)
    def run(*args, **kwargs):
        return asyncio.run(function(*args, **kwargs))
    return run


@run_async
@pytest.mark.parametrize("geometry_passed", [True, False])
@pytest.mark.parametrize("local_success", [True, False])
async def test_source_generation_verifies_before_publish_and_bounds_repair(monkeypatch, geometry_passed, local_success):
    manifest = manifest_for([("text", {"text": "원본 내용 125%"})])
    request = SimpleNamespace(_source_content_svg_reflow=True, _source_content_manifest=manifest,
        _source_content_authored_reflow=False, _document_canvas=None, strategy="svg",
        authored_style="default", include_title_slide=False, instructions="body 5px",
        codex_generation_preset=None)
    job = SimpleNamespace(id=uuid.uuid4(), attempt_token="attempt", workspace_operation_id=uuid.uuid4(),
        svg_style_snapshot=None, attempt_number=1)
    snapshot = {"digest": "sha256:style", "contract_version": "svg-static/v1"}
    saved = {}

    class Session:
        async def __aenter__(self): return self
        async def __aexit__(self, *_): pass
        async def scalar(self, *_): return None
        async def commit(self): pass
        def add(self, *_): pass

    async def checkpoint(_job, index, *, payload=None):
        if payload is not None:
            saved[index] = deepcopy(payload)
        return deepcopy(saved.get(index, {}))

    async def author(source, index, style, **kwargs):
        assert kwargs["min_font_size_px"] == 7
        await kwargs["before_dispatch"]()
        result = assemble_source_svg(source, index, plan_for(source),
            source_images=kwargs["source_images"], image_slots=kwargs["image_slots"],
            min_font_size_px=kwargs["min_font_size_px"], style_digest=style["digest"])
        return SvgAuthorResult(result.svg_content, "layout-plan", {
            "validation_error": None, "source_preservation": result.receipt})

    monkeypatch.setattr(generation, "require_svg_creation_admission", lambda _: None)
    monkeypatch.setattr(generation, "pin_svg_style", lambda _: snapshot)
    monkeypatch.setattr("utils.svg_template_fonts.source_font_profile", lambda _: "test-font-profile/v2")
    monkeypatch.setattr(generation, "request_canvas_contract", lambda _: SimpleNamespace(width_px=1280, height_px=720))
    monkeypatch.setattr(generation, "async_session_maker", Session)
    monkeypatch.setattr(generation, "owned_job", AsyncMock(return_value=job))
    monkeypatch.setattr(generation, "initialize_checkpoints", AsyncMock())
    monkeypatch.setattr(generation, "_checkpoint", checkpoint)
    monkeypatch.setattr(generation, "_read_png", lambda _: None)
    monkeypatch.setattr(generation, "_write_png", lambda _: {"png_sha256": "test", "png_path": "test"})
    publish = AsyncMock(return_value="published")
    monkeypatch.setattr(generation, "_publish", publish)
    monkeypatch.setattr("utils.authored_styles.resolve_authored_style", lambda _: SimpleNamespace())
    monkeypatch.setattr("utils.llm_calls.author_slide_svg.preflight_svg_style_images", lambda *_: None)
    monkeypatch.setattr("services.authored_source_images.collect_source_image_candidates", AsyncMock(return_value=[]))
    monkeypatch.setattr("services.artifact_store.configured_public_artifact_service", lambda: object())
    monkeypatch.setattr("services.svg_generation_assets.publication_context", AsyncMock(return_value=(object(), 1)))
    monkeypatch.setattr("services.svg_generation_assets.prepare_source_manifest", AsyncMock(
        return_value=(SimpleNamespace(slots=[], digest="sha256:assets"), {"ref": "test"})))
    monkeypatch.setattr("services.svg_generation_assets.author_images", AsyncMock(return_value={}))
    monkeypatch.setattr("services.svg_generation_assets.screen_placement", AsyncMock(return_value=({}, None)))
    monkeypatch.setattr("services.authored_checkpoint_service.fail_stage", AsyncMock())
    author_mock = AsyncMock(side_effect=author)
    monkeypatch.setattr("utils.llm_calls.author_source_svg_layout.author_source_svg_layout", author_mock)
    def local(source, index, style, **kwargs):
        assert kwargs["min_font_size_px"] == 7
        if not local_success:
            return {"escalation_reason": "layout_candidate_unavailable", "model_calls": 0,
                    "model_layout_allowed": True}
        result = assemble_source_svg(source, index, plan_for(source),
            source_images=kwargs["source_images"], image_slots=kwargs["image_slots"],
            min_font_size_px=kwargs["min_font_size_px"], style_digest=style["digest"])
        return SvgAuthorResult(result.svg_content, "", {
            "validation_error": None, "source_preservation": result.receipt})
    monkeypatch.setattr("services.svg_source_template_layout.try_template_source_layout", local)
    def render(*args, **kwargs):
        assert kwargs["minimum_font_px"] == 7
        assert kwargs["instructions"] is None
        return SimpleNamespace(png=b"png", quality={"passed": geometry_passed, "verified": False, "failures": []})
    monkeypatch.setattr("services.svg_renderer.render_svg_with_metrics", AsyncMock(side_effect=render))
    outline = SimpleNamespace(slides=[SimpleNamespace(content="source")])

    if not geometry_passed:
        with pytest.raises(AuthoredStageError) as failed:
            await generation._generate(request, uuid.uuid4(), outline, "ko", None, job)
        assert failed.value.code == "svg_source_quality_failed"
        assert author_mock.await_count == 2
        publish.assert_not_awaited()
        return
    assert await generation._generate(request, uuid.uuid4(), outline, "ko", None, job) == "published"
    assert author_mock.await_count == (0 if local_success else 1)
    pages = publish.await_args.args[-1]
    payload, png = pages[0]
    assert png is None  # completed page bytes do not accumulate across the deck
    assert payload["source_preservation"]["rendering_verified"] is True
    assert payload["quality"]["verified"] is True
    assert "system-page-number" not in payload["svg_content"]
    assert payload["inflight"] is None
    assert payload["model_attempt_count"] == (0 if local_success else 1)


@run_async
async def test_source_publish_rejects_missing_receipt_before_database_access():
    request = SimpleNamespace(_source_content_svg_reflow=True,
        _source_content_manifest=manifest_for([("text", {"text": "원문"})]))
    with pytest.raises(ValueError, match="svg_source_receipt"):
        await generation._publish(request, uuid.uuid4(), None, "ko", None, None, None, None,
            [({"svg_content": "<svg/>"}, None)])


@run_async
async def test_terminal_page_failure_cancels_waiters_without_accepting_late_results():
    waiting = asyncio.Event()
    cancelled = asyncio.Event()

    async def slow_provider():
        waiting.set()
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    async def failed_page():
        await waiting.wait()
        raise AuthoredStageError(slide_index=0, stage="author",
            code="svg_source_plan_input_mismatch", retryable=False)

    tasks = [asyncio.create_task(slow_provider()), asyncio.create_task(failed_page())]
    with pytest.raises(AuthoredStageError):
        await asyncio.wait_for(generation._collect_pages_or_fail(tasks), timeout=1)
    assert cancelled.is_set()
    assert all(task.done() for task in tasks)


def test_source_svg_worker_materializes_only_fixed_bundled_fonts():
    from services.svg_renderer import TRUSTED_FONT_PROFILE, svg_preview_html

    manifest = manifest_for([("text", {"text": "신뢰된 글꼴"})])
    assembled = assemble_source_svg(manifest, 0, plan_for(manifest), source_images={}, image_slots={},
        min_font_size_px=9.5, style_digest="sha256:style")
    admitted = _admit_html(svg_preview_html(assembled.svg_content))

    assert f'data-presenton-trusted-font-profile="{TRUSTED_FONT_PROFILE}"' in admitted.html
    assert admitted.html.count("@font-face") == 2
    assert len([digest for digest in admitted.asset_digests if digest.startswith("sha256:")]) >= 2

    arbitrary = ("<!DOCTYPE html><html><head><style>"
        "@font-face{font-family:Untrusted;src:url(data:font/ttf;base64,AA==)}"
        "</style></head><body><p>blocked</p></body></html>")
    with pytest.raises(PublicWorkerExecutionError, match="public_render_policy_rejected"):
        _admit_html(arbitrary)
