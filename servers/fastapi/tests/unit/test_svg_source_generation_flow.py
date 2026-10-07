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
from services.generation_core.authoring.author_slide_svg import SvgAuthorResult
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
@pytest.mark.parametrize("free_composition", [True, False])
async def test_source_generation_verifies_before_publish_and_bounds_repair(monkeypatch, geometry_passed, local_success, free_composition):
    manifest = manifest_for([("text", {"text": "원본 내용 125%"})])
    request = SimpleNamespace(_source_content_svg_reflow=True, _source_content_manifest=manifest,
        _source_content_authored_reflow=False, _document_canvas=None, strategy="svg",
        authored_style="default", include_title_slide=False, instructions="body 5px",
        codex_generation_preset=None)
    job = SimpleNamespace(id=uuid.uuid4(), attempt_token="attempt", workspace_operation_id=uuid.uuid4(),
        svg_style_snapshot=None, attempt_number=1)
    snapshot = {"digest": "sha256:style", "contract_version": "svg-static/v1"}
    if free_composition:
        snapshot["source_template"] = {"composition_mode": "free-svg"}
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
    monkeypatch.setattr(
        "services.generation_core.authoring.author_slide_svg.preflight_svg_style_images", lambda *_: None
    )
    monkeypatch.setattr("services.authored_source_images.collect_source_image_candidates", AsyncMock(return_value=[]))
    monkeypatch.setattr("services.artifact_store.configured_public_artifact_service", lambda: object())
    monkeypatch.setattr("services.svg_generation_assets.publication_context", AsyncMock(return_value=(object(), 1)))
    monkeypatch.setattr("services.svg_generation_assets.prepare_source_manifest", AsyncMock(
        return_value=(SimpleNamespace(slots=[], digest="sha256:assets"), {"ref": "test"})))
    monkeypatch.setattr("services.svg_generation_assets.author_images", AsyncMock(return_value={}))
    monkeypatch.setattr("services.svg_generation_assets.screen_placement", AsyncMock(return_value=({}, None)))
    monkeypatch.setattr("services.authored_checkpoint_service.fail_stage", AsyncMock())
    author_mock = AsyncMock(side_effect=author)
    monkeypatch.setattr(
        "services.generation_core.authoring.author_source_svg_layout.author_source_svg_layout", author_mock
    )
    def local(source, index, style, **kwargs):
        assert not free_composition, "Free SVG composition must bypass the historical slot solver"
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
    assert author_mock.await_count == (0 if local_success and not free_composition else 1)
    pages = publish.await_args.args[-1]
    payload, png = pages[0]
    assert png is None  # completed page bytes do not accumulate across the deck
    assert payload["source_preservation"]["rendering_verified"] is True
    assert payload["quality"]["verified"] is True
    assert "system-page-number" not in payload["svg_content"]
    assert payload["inflight"] is None
    assert payload["model_attempt_count"] == (0 if local_success and not free_composition else 1)
    assert payload["quality"]["compositionObservation"]["status"] == "observed"


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


def test_free_source_composition_preserves_text_outside_historical_slots():
    import base64
    from io import BytesIO
    from PIL import Image
    from test_svg_source_layout import _v2_style
    from services.svg_source_layout import SvgLayoutPlan
    from services.svg_source_template_layout import resolve_source_plan, selected_template, repair_template_coordinates
    from utils.hashing import sha256_prefixed

    manifest = manifest_for([("text", {"text": "모든 원문 & 125%"})])
    style = _v2_style()
    template = style["source_template"]
    template.pop("layouts")
    template.update(composition_mode="free-svg")
    picture = BytesIO()
    Image.new("RGB", (2, 2), "red").save(picture, format="PNG")
    template["system_furniture"] = dict(digest="sha256:master", reservations=[dict(
        id="svg-system-master-logo", x=10, y=10, width=20, height=20)], elements=[dict(
        id="svg-system-master-logo", kind="logo", x=10, y=10, width=20, height=20,
        data_url="data:image/png;base64," + base64.b64encode(picture.getvalue()).decode(),
        asset_digest=sha256_prefixed(picture.getvalue()))])
    assert selected_template(style) is template
    data = plan_for(manifest)
    data.update(template_digest=template["digest"], layout_id="free-svg", width=720, height=1280)
    data["placements"][0].update(x=390, y=820, width=280, height=260,
                                 font_family="Noto Sans KR", font_weight="400", font_size=28)
    plan, _ = resolve_source_plan(SvgLayoutPlan.model_validate(data), manifest, 0, 12, template)
    unchanged, moved = repair_template_coordinates(plan, manifest, 0, template)
    assert unchanged is plan and moved == 0
    result = assemble_source_svg(manifest, 0, plan, source_images={}, image_slots={},
                                min_font_size_px=12, style_digest=style["digest"], template_snapshot=template)
    assert result.receipt["bindings"][0]["text"] == "모든 원문 & 125%"
    assert result.receipt["canvas"] == {"width": 720, "height": 1280}
    assert result.receipt["template_application"]["composition_mode"] == "free-svg"
    assert result.receipt["system_furniture"]["element_ids"] == [
        "svg-system-master-furniture", "svg-system-master-logo"]
    collision = plan.model_copy(update={"placements": (plan.placements[0].model_copy(update={"x": 10, "y": 10}),)})
    with pytest.raises(ValueError, match="master_furniture_collision"):
        assemble_source_svg(manifest, 0, collision, source_images={}, image_slots={},
                            min_font_size_px=12, style_digest=style["digest"], template_snapshot=template)
    invalid = plan.model_copy(update={"placements": (plan.placements[0].model_copy(update={"x": 700}),)})
    with pytest.raises(ValueError, match="slot_violation"):
        assemble_source_svg(manifest, 0, invalid, source_images={}, image_slots={},
                            min_font_size_px=12, style_digest=style["digest"], template_snapshot=template)


def test_composition_observes_scaled_content_excluding_chrome_without_aesthetic_gate():
    from services.generation_core.drivers.svg.composition_observation import observe_svg_deck_composition

    def page(scale, colour, footer):
        svg = (f'<svg xmlns="http://www.w3.org/2000/svg" width="{1000 * scale}" height="{1000 * scale}">'
               f'<rect x="0" y="0" width="{1000 * scale}" height="{1000 * scale}" fill="{colour}"/>'
               f'<g transform="translate({100 * scale},{100 * scale})">'
               + ''.join(f'<rect x="{i * 230 * scale}" y="{200 * scale}" width="{180 * scale}" '
                         f'height="{350 * scale}" fill="#234567"/>' for i in range(3))
               + f'</g><text id="svg-system-page-number" x="{950 * scale}" y="{970 * scale}" '
                 f'font-size="{14 * scale}" fill="#FFFFFF">{footer}</text></svg>')
        return {"svg_content": svg}, None

    pages = [page(1, "#FFFFFF", "1"), page(2, "#172033", "22"), page(3, "#234567", "333")]
    observation = observe_svg_deck_composition(pages)
    assert observation["groups"][0]["pages"] == [0, 1, 2]
    assert len({p["geometryDigest"] for p in observation["pages"]}) == 1
    assert observation["advisoryOnly"] and not observation["aestheticsVerified"]
    for payload, _ in pages:
        payload["source_preservation"] = {"template_application": {"source_parallel_groups": [["a", "b"]]}}
    assert observe_svg_deck_composition(pages)["groups"][0]["observation"] == "comparison_structure_repetition"
    assert observe_svg_deck_composition([({"svg_content": "<svg>"}, None)])["observedPages"] == 0
