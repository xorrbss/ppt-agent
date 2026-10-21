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
@pytest.mark.parametrize("preparation_only_resume", [False, True])
async def test_source_generation_verifies_before_publish_and_bounds_repair(monkeypatch, geometry_passed, local_success, free_composition, preparation_only_resume):
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
    from services.generation_core.drivers.svg.visual_intent import build_deck_visual_intents

    seed = ({"deck_visual_intents": build_deck_visual_intents(["원본 내용 125%"], ["content"])}
            if preparation_only_resume else None)
    saved = {0: deepcopy(seed)} if seed else {}

    class Session:
        async def __aenter__(self): return self
        async def __aexit__(self, *_): pass
        async def scalar(self, *_): return deepcopy(seed)
        async def get(self, *_): return None
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
    # This fixture isolates storage; operation-bound recovery is covered by
    # its own tests and must not query a real session here.
    async def runtime_profile(_session, _job, profile):
        return profile, None
    monkeypatch.setattr("services.svg_timeout_repurchase.sample_completion_runtime_profile", runtime_profile)
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
            await generation._generate(request, uuid.uuid4(), outline, "ko", Session(), job)
        assert failed.value.code == "svg_source_quality_failed"
        assert author_mock.await_count == 2
        publish.assert_not_awaited()
        return
    assert await generation._generate(request, uuid.uuid4(), outline, "ko", Session(), job) == "published"
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
@pytest.mark.parametrize("scenario,expected_calls", [
    ("structural_visual_pass", 3), ("structural_twice", 2), ("visual_twice", 2),
    ("structural_visual_twice", 3), ("wrong_ordinal", 2), ("wrong_source", 2),
    ("legacy_source", 2), ("durable_source", 3),
    ("incremental_page_render_reuse", 0),
])
async def test_source_structural_visual_repair_uses_existing_attempt_evidence(monkeypatch, scenario, expected_calls):
    from services.generation_core.drivers.svg import author, collect, render

    if scenario == "incremental_page_render_reuse":
        import json
        from models.document_canvas import DocumentCanvasMetadata
        from services import svg_partial_recovery as recovery
        from services.generation_core.drivers.svg import partial_quality_recovery, quality_recovery
        from services.svg_generation_inputs import svg_page_input_digest
        from utils.hashing import sha256_hex

        # Simulate code bytes without changing product files or the hash recipe.
        code_changes = {}
        source_root = recovery.Path(recovery.__file__).resolve().parents[1]
        read_code_bytes = recovery.Path.read_bytes

        def code_bytes(path):
            return read_code_bytes(path) + code_changes.get(path, b"")

        monkeypatch.setattr(recovery.Path, "read_bytes", code_bytes)
        worker = {"checkpointReuseEligible": True, "imageDigest": "a" * 64}
        monkeypatch.setattr("services.public_render_runtime_identity.public_render_runtime_binding",
                            lambda: deepcopy(worker))
        images, rendered, authored, saved = {}, [], [], {}
        reader = lambda value: images.get(value.get("png_sha256"))
        monkeypatch.setattr("services.svg_presentation_checkpoint._read_png", reader)
        monkeypatch.setattr(partial_quality_recovery, "current_quality_repair_authorized",
                            AsyncMock(return_value=False))
        authority_check = AsyncMock()
        monkeypatch.setattr(quality_recovery, "ensure_recovery_authority", authority_check)
        verified_pages = []
        verify_cached_page = recovery.verify_cached_page

        def verify_page(*args, **kwargs):
            verified_pages.append(args[2])
            return verify_cached_page(*args, **kwargs)

        monkeypatch.setattr(recovery, "verify_cached_page", verify_page)
        monkeypatch.setattr(collect, "_provider_attempt_limit", lambda *args, **kwargs: 2)
        request = SimpleNamespace(instructions=None, _document_canvas=None,
                                  codex_generation_preset="quality", writing_policy=None)
        physical_canvas = DocumentCanvasMetadata.model_validate({
            "contractVersion": "document-canvas/v1",
            "logicalSize": {"width": 1280, "height": 720},
            "physicalSize": {"widthEmu": 12192000, "heightEmu": 6858000},
            "safeArea": {"x": 0, "y": 0, "width": 1280, "height": 720},
        })
        request._document_canvas = physical_canvas
        ctx = SimpleNamespace(
            snapshot={"digest": "style", "readability_policy": {"digest": "policy"}},
            roles=["content", "content"], count=2, manifests=[{}, {}],
            source_reflow=False, raster_reconstruction=False, canonical_assets=False,
            source_font_profile=None, physical_kwargs={"physical_canvas": physical_canvas},
            canvas=physical_canvas,
            semaphore=asyncio.Semaphore(1), runtime_profile=SimpleNamespace(font_floor=7),
            quality_recovery_checkpoint_reuse_eligible=True,
            quality_recovery_validation_context={
                "contract_version": "svg-static/v2", "renderer_digest": "sha256:" + "b" * 64,
                "fonts_digest": "sha256:" + "c" * 64,
                "runtime_digest": "sha256:" + collect._digest(worker)},
        )
        original_physical = deepcopy(ctx.physical_kwargs)
        job = SimpleNamespace(id=uuid.uuid4(), workspace_operation_id=uuid.uuid4(),
                              presentation_id=uuid.uuid4(), attempt_number=1, request_payload={})
        outline = SimpleNamespace(slides=[SimpleNamespace(content="old"), SimpleNamespace(content="new")])
        ctx.quality_recovery_input_binding = collect._digest({
            "operation": str(job.workspace_operation_id), "style": ctx.snapshot,
            "contents": [slide.content for slide in outline.slides], "assets": ctx.manifests})

        def input_for(index):
            return svg_page_input_digest(content=outline.slides[index].content,
                style=ctx.snapshot, index=index, count=2, instructions=None,
                language="ko", preset="quality", assets={})

        def measured_payload(value, index):
            png = ("png-" + str(index) + "-" + value["svg_content"]).encode()
            png_digest = sha256_hex(png)
            images[png_digest] = png
            value.update(source_digest=sha256_hex(value["svg_content"].encode()),
                         png_sha256=png_digest, png_path=png_digest + ".png",
                         readability_policy=deepcopy(ctx.snapshot["readability_policy"]),
                         quality={"passed": True, "geometry_passed": True, "measurement_complete": True,
                             "verified": False, "source_digest": sha256_hex(value["svg_content"].encode()),
                             "png_digest": png_digest, "wrapper_digest": "d" * 64,
                             "metrics_digest": "e" * 64, "browser_metrics": {"elements": []},
                             "trusted_font_materialization": {"font_digests": ["c" * 64]}})

        source = {"svg_content": "<svg>old</svg>", "input_digest": input_for(0),
                  "attempts": [{"kind": "provider", "raw_response": "<svg>old</svg>",
                                "receipt": {"contract_version": "svg-static/v2"}}], "inflight": None}
        measured_payload(source, 0)
        source["quality_recovery_local_evidence"] = quality_recovery.local_evidence_binding(
            collect._digest({"operation": "historical-producer"}), source)
        collect._record_page_render_evidence(source, collect._page_render_environment(ctx, request, 0))
        assert recovery._verified_page_render_evidence(source) is not None
        original = deepcopy(source)

        def plan_for_source(value, row_digest):
            page = {"slide_index": 0, "category": "known", "row_digest": row_digest,
                    "svg_sha256": sha256_hex(value["svg_content"].encode()),
                    "input_digest": value["input_digest"], "author_receipt_digest": recovery.author_witness(value)}
            if value.get("page_render_evidence"):
                page["page_render_evidence_digest"] = recovery.digest(value["page_render_evidence"])
            manifest = {"known_indices": [0], "new_indices": [1], "unknown_indices": [],
                        "code_digest": recovery._code_digest(),
                        "pages": [page, {"slide_index": 1, "category": "unstarted", "row_digest": "new-row"}]}
            return SimpleNamespace(manifest=manifest, manifest_hash=recovery.digest(manifest))

        def clone(value, plan, operation_id):
            return recovery.cached_page_payload(SimpleNamespace(slide_index=0, svg_checkpoint=value), plan, operation_id)

        async def checkpoint(active_job, index, *, payload=None):
            if payload is not None:
                saved[index] = json.loads(json.dumps(payload, ensure_ascii=False))
            return saved.get(index)

        async def authored_page(**kwargs):
            index, value = kwargs["index"], kwargs["payload"]
            authored.append(index)
            svg = "<svg>new</svg>"
            value.setdefault("attempts", []).append({"kind": "provider", "raw_response": svg,
                                                   "receipt": {"contract_version": "svg-static/v2"}})
            return author.AuthorAttempt(svg=svg)

        async def render_page(**kwargs):
            index, value = kwargs["index"], kwargs["payload"]
            rendered.append(index)
            value["svg_content"] = kwargs["svg"]
            measured_payload(value, index)
            return value, None

        monkeypatch.setattr(collect.author, "attempt_author", authored_page)
        monkeypatch.setattr(collect.render, "attempt_render", render_page)
        hooks = SimpleNamespace(checkpoint=checkpoint, read_png=reader,
                                async_session_maker=None, write_png=lambda _: {})
        state = SimpleNamespace(fatal_error=None, cached_author_render_manifest_hash=None,
                                cache_only_approval=None)
        plan = plan_for_source(source, "source-row")
        ctx.partial_recovery_manifest = plan.manifest
        saved[0] = clone(source, plan, job.workspace_operation_id)
        assert "quality_recovery_local_evidence" not in saved[0]
        saved[1] = {"partial_recovery": {"schema": "svg-partial-recovery-page/v1",
            "operation_id": str(job.workspace_operation_id), "manifest_hash": plan.manifest_hash,
            "slide_index": 1, "source_row_digest": "new-row", "category": "unstarted"}}
        for index in range(2):
            await collect._page(ctx, request, job.presentation_id, outline, "ko", job, index, state, hooks)
        assert rendered == authored == [1]
        assert saved[0]["quality_recovery_local_evidence"] == quality_recovery.local_evidence_binding(
            ctx.quality_recovery_input_binding, saved[0])
        assert saved[0]["quality_recovery_local_evidence"] != source["quality_recovery_local_evidence"]
        assert source == original
        assert saved[0]["quality"] == source["quality"] and saved[0]["png_sha256"] == source["png_sha256"]
        assert saved[0]["page_render_evidence"]["environment"]["physical"]["physical_canvas"] == physical_canvas.model_dump(mode="json")
        assert recovery._verified_page_render_evidence(saved[1]) is not None

        # Full authority pin changes independently of measured-page equivalence.
        authority_code = recovery._code_digest()
        render_code = recovery._page_render_code_digest()
        for path, should_render in (("services/svg_partial_recovery.py", False),
                                    ("services/workspace_service.py", False),
                                    ("services/provider_durable_admission.py", False),
                                    ("services/provider_usage_instrumentation.py", False),
                                    ("services/provider_runtime_job_registration.py", False),
                                    ("services/generation_core/quality/vision_advisory.py", False),
                                    ("services/generation_core/quality/vision_advisory_prompt.py", False),
                                    ("services/generation_core/drivers/svg/quality.py", False),
                                    ("services/generation_core/drivers/svg/render.py", True)):
            code_changes[source_root / path] = b"\n# changed code fixture\n"
            assert recovery._code_digest() != authority_code
            assert (recovery._page_render_code_digest() != render_code) is should_render
            changed_plan = plan_for_source(source, "source-row")
            ctx.partial_recovery_manifest = changed_plan.manifest
            before = len(rendered)
            before_authority, before_verified = authority_check.await_count, len(verified_pages)
            reused_payload, _ = await collect._remeasure_partial_cached_page(ctx, request, job.presentation_id, 0,
                "old", clone(source, changed_plan, job.workspace_operation_id),
                source["input_digest"], job, hooks)
            assert len(rendered) == before + int(should_render) and source == original
            assert len(verified_pages) > before_verified
            if not should_render:
                assert authority_check.await_count > before_authority
                if path == "services/generation_core/drivers/svg/quality.py":
                    assert reused_payload["quality_recovery_local_evidence"] == quality_recovery.local_evidence_binding(
                        ctx.quality_recovery_input_binding, reused_payload)
                    assert reused_payload["quality_recovery_local_evidence"] != source["quality_recovery_local_evidence"]
            code_changes.clear()
        ctx.partial_recovery_manifest = plan.manifest

        # A v1 full-pipeline proof is insufficient for the v2 render contract.
        v1 = deepcopy(source)
        v1_environment = v1["page_render_evidence"]["environment"]
        v1_environment.pop("page_render_contract")
        v1_environment.pop("render_code")
        v1_environment["pipeline_code"] = authority_code
        v1_original = deepcopy(v1)
        v1_plan = plan_for_source(v1, "v1-row")
        ctx.partial_recovery_manifest = v1_plan.manifest
        before = len(rendered)
        result, _ = await collect._remeasure_partial_cached_page(ctx, request, job.presentation_id, 0,
            "old", clone(v1, v1_plan, job.workspace_operation_id), v1["input_digest"], job, hooks)
        assert len(rendered) == before + 1 and v1 == v1_original and source == original
        assert result["page_render_evidence"]["environment"]["page_render_contract"] == "svg-page-render-environment/v2"
        ctx.partial_recovery_manifest = plan.manifest

        # Every stored rendering input/result mutation invalidates the old result.
        for key, changed in (("svg_content", "<svg>changed</svg>"),
                             ("readability_policy", {"digest": "changed"}),
                             ("source_manifest_digest", "changed"),
                             ("source_preservation", {"rendering_verified": False}),
                             ("nativeDeclarations", {"changed": True})):
            candidate = deepcopy(source)
            candidate[key] = changed
            assert recovery._verified_page_render_evidence(candidate) is None
        for field in ("wrapper_digest", "metrics_digest", "trusted_font_materialization"):
            candidate = deepcopy(source)
            candidate["quality"][field] = {"changed": True}
            assert recovery._verified_page_render_evidence(candidate) is None
        images[source["png_sha256"]] = b"changed-png"
        assert recovery._verified_page_render_evidence(source) is None
        images[source["png_sha256"]] = ("png-0-" + source["svg_content"]).encode()

        # Policy/font/runtime changes bypass reuse and perform one real measure.
        for change in ("policy", "fonts", "runtime"):
            with monkeypatch.context() as scope:
                scope.setattr(ctx, "snapshot", deepcopy(ctx.snapshot))
                scope.setattr(ctx, "quality_recovery_validation_context",
                              deepcopy(ctx.quality_recovery_validation_context))
                if change == "policy":
                    ctx.snapshot["readability_policy"]["digest"] = "changed"
                elif change == "fonts":
                    ctx.quality_recovery_validation_context["fonts_digest"] = "sha256:" + "f" * 64
                else:
                    worker["imageDigest"] = "f" * 64
                    ctx.quality_recovery_validation_context["runtime_digest"] = "sha256:" + collect._digest(worker)
                before = len(rendered)
                await collect._remeasure_partial_cached_page(ctx, request, job.presentation_id, 0,
                    "old", clone(source, plan, job.workspace_operation_id), source["input_digest"], job, hooks)
                assert len(rendered) == before + 1 and source == original
                worker["imageDigest"] = "a" * 64

        # A legacy page is measured once; its next mixed recovery reuses the new proof.
        legacy = deepcopy(source)
        legacy.pop("page_render_evidence")
        legacy_plan = plan_for_source(legacy, "legacy-row")
        ctx.partial_recovery_manifest = legacy_plan.manifest
        before = len(rendered)
        result, _ = await collect._remeasure_partial_cached_page(ctx, request, job.presentation_id, 0,
            "old", clone(legacy, legacy_plan, job.workspace_operation_id), legacy["input_digest"], job, hooks)
        assert len(rendered) == before + 1 and recovery._verified_page_render_evidence(result) is not None
        assert result == saved[0]
        result = deepcopy(saved[0])
        next_job = SimpleNamespace(**{**vars(job), "id": uuid.uuid4(), "workspace_operation_id": uuid.uuid4()})
        ctx.quality_recovery_input_binding = collect._digest({
            "operation": str(next_job.workspace_operation_id), "style": ctx.snapshot,
            "contents": [slide.content for slide in outline.slides], "assets": ctx.manifests})
        next_plan = plan_for_source(result, "next-row")
        ctx.partial_recovery_manifest = next_plan.manifest
        await collect._remeasure_partial_cached_page(ctx, request, next_job.presentation_id, 0,
            "old", clone(result, next_plan, next_job.workspace_operation_id), result["input_digest"], next_job, hooks)
        assert len(rendered) == before + 1 and source == original
        assert saved[0]["quality_recovery_local_evidence"] == quality_recovery.local_evidence_binding(
            ctx.quality_recovery_input_binding, saved[0])
        assert ctx.physical_kwargs == original_physical
        assert ctx.physical_kwargs["physical_canvas"] is request._document_canvas is physical_canvas
        return
    actual_attempt_author = author.attempt_author
    from services.svg_source_layout import source_pages

    manifest = manifest_for([("text", {"text": "원본 내용 125%"})])
    contract = {"version": "svg-source-whole-page/v1"}
    snapshot = {"digest": "sha256:style", "source_authoring_contract": contract}
    if scenario == "legacy_source":
        snapshot.pop("source_authoring_contract")
    assembly = assemble_source_svg(manifest, 0, plan_for(manifest), source_images={}, image_slots={},
        min_font_size_px=7, style_digest=snapshot["digest"])
    ctx = SimpleNamespace(snapshot=snapshot, roles=["content"], count=1, source_reflow=True,
        raster_reconstruction=False, canonical_assets=False, source_document=manifest,
        source_units=source_pages(manifest), source_font_profile=None,
        canvas=SimpleNamespace(width_px=1280, height_px=720), render_semaphore=asyncio.Semaphore(1),
        physical_kwargs={})
    authority_counts = []
    async def authority(*args, **kwargs):
        authority_counts.append(len(payload["attempts"]))
    if scenario == "durable_source":
        ctx.quality_recovery_input_binding = "bound-source-policy"
    monkeypatch.setattr("services.generation_core.drivers.svg.quality_recovery.ensure_recovery_authority", authority)
    monkeypatch.setattr("services.generation_core.drivers.svg.quality_recovery.local_evidence_binding",
        lambda binding, payload: {"binding": binding})
    steps = (["visual", "visual"] if scenario == "visual_twice" else
             ["structural", "structural"] if scenario == "structural_twice" else
             ["structural", "visual", "visual"] if scenario == "structural_visual_twice" else
             ["structural", "visual", "pass"])
    payload, calls, verified = {"attempts": []}, [], []

    async def attempt(**kwargs):
        ordinal, provider_ordinal = kwargs["ordinal"], kwargs["provider_ordinal"]
        assert ordinal == len(calls) and provider_ordinal == ordinal + 1
        calls.append((ordinal, provider_ordinal))
        receipt = {"source_authoring_contract": contract, "attempt_ordinal": provider_ordinal,
            "validation_error": "svg_source_receipt_invalid" if steps[ordinal] == "structural" else None}
        if steps[ordinal] != "structural":
            payload["source_preservation"] = deepcopy(assembly.receipt)
            receipt["source_preservation"] = deepcopy(assembly.receipt)
        if ordinal == 1 and scenario == "wrong_ordinal":
            receipt["attempt_ordinal"] = 3
        if ordinal == 1 and scenario == "wrong_source":
            receipt["source_preservation"]["source_page_id"] = "another-page"
        payload["attempts"].append({"kind": "provider", "receipt": receipt,
            "raw_response": f"response-{provider_ordinal}"})
        return author.AuthorAttempt(svg=assembly.svg_content)

    async def measured(svg, **kwargs):
        assert svg == assembly.svg_content
        assert kwargs["required_text"] == ["원본 내용 125%"]
        assert kwargs["minimum_font_px"] == 7 and kwargs["instructions"] is None
        passed = steps[len(calls) - 1] == "pass"
        return SimpleNamespace(png=b"png", quality={"passed": passed, "verified": False,
            "failures": [] if passed else [{"kind": "text_overlap"}]})

    from services.svg_source_layout import verify_source_receipt
    def verify(svg, receipt, source, index):
        assert source is manifest and index == 0
        verified.append(index)
        return verify_source_receipt(svg, receipt, source, index)

    async def checkpoint(*args, **kwargs):
        pass

    monkeypatch.setattr(collect.author, "attempt_author", attempt)
    monkeypatch.setattr("services.svg_renderer.render_svg_with_metrics", measured)
    monkeypatch.setattr("services.svg_source_layout.verify_source_receipt", verify)
    monkeypatch.setattr("services.svg_master_furniture.master_furniture_receipt", lambda *args, **kwargs: None)
    hooks = SimpleNamespace(checkpoint=checkpoint, async_session_maker=None, write_png=lambda _: {})
    request = SimpleNamespace(instructions="body 5px", _document_canvas=None)
    run = collect._attempt_loop(ctx, request, uuid.uuid4(), 0, "원본 내용 125%", payload,
        None, SimpleNamespace(fatal_error=None), hooks)
    if scenario in {"structural_visual_pass", "durable_source"}:
        result, png = await run
        assert result is payload and png is None
        assert payload["quality"]["passed"] is True
        assert payload["source_preservation"]["rendering_verified"] is True
        saved = deepcopy(payload)
        payload["attempts"].pop()
        assert render.source_structural_visual_retry(ctx, payload, index=0)
        for key, value in (("provider_failures", [{"ordinal": 1}]), ("inflight", 3),
                           ("append_only_replacement", {"witness": {}}),
                           ("partial_recovery", {"category": "known"})):
            original = payload.get(key)
            payload[key] = value
            assert not render.source_structural_visual_retry(ctx, payload, index=0)
            if original is None:
                payload.pop(key)
            else:
                payload[key] = original
        ctx.source_reflow = False
        assert not render.source_structural_visual_retry(ctx, payload, index=0)
        ctx.source_reflow = True
        assert not render.source_structural_visual_retry(ctx, payload, index=1)
        payload.clear()
        payload.update(saved)
    else:
        with pytest.raises(AuthoredStageError) as failed:
            await run
        assert failed.value.code == ("svg_source_receipt_invalid" if scenario == "structural_twice"
                                     else "svg_source_quality_failed")
    assert len(calls) == expected_calls
    assert len(payload["attempts"]) == expected_calls
    assert len(verified) == expected_calls - (2 if scenario == "structural_twice" else
                                            0 if scenario == "visual_twice" else 1)
    assert all(attempt["raw_response"] == f"response-{number}"
               for number, attempt in enumerate(payload["attempts"], 1))
    assert authority_counts == ([0, 1, 2] if scenario == "durable_source" else [])

    if scenario == "visual_twice":
        import json
        import math
        import xml.etree.ElementTree as ET
        from services.generation_core.authoring.author_source_svg_layout import _whole_page_facts
        from services.svg_source_layout import bind_whole_page_source_svg
        from services.svg_text_overlap import text_overlaps, OVERLAP_THRESHOLD_PX
        from services.svg_contract import source_digest
        from services.svg_partial_recovery import cached_page_payload, verify_cached_page, digest
        from utils.slide_capture_text_ink import TEXT_INK_PROBE_VERSION

        monkeypatch.setattr("services.svg_source_layout.verify_source_receipt", verify_source_receipt)
        table_source = manifest_for([("table", {"rows": [[{"text": "A\nB", "sourceCellId": "cell-original"}]]})])
        facts = _whole_page_facts(table_source.units[0], {})
        table = facts[0]
        raw = ('<svg xmlns="http://www.w3.org/2000/svg" width="1280" height="720" viewBox="0 0 1280 720">'
            f'<g id="{table["element_id"]}"><rect x="20" y="20" width="200" height="100" fill="#ffffff"/>'
            f'<text id="{table["cells"][0]["element_id"]}" x="30" y="60" font-family="Pretendard" font-size="20" fill="#111111">'
            '<tspan x="30" y="60">A\n</tspan><tspan x="30" y="80">B</tspan></text></g></svg>')
        svg, bound_receipt = bind_whole_page_source_svg(raw, table_source, 0, facts=facts, width=1280, height=720,
            style_snapshot=snapshot, minimum_font_px=7)
        nodes = list(ET.fromstring(svg).iter())
        qids = [f"svg-{i}" for i, node in enumerate(nodes) if node.tag.endswith("}tspan")]
        parent_qid = next(f"svg-{i}" for i, node in enumerate(nodes) if node.tag.endswith("}text"))
        elements = [{"qid": qid, "parent_qid": parent_qid, "tag": "tspan", "has_owned_text": True,
            "text_fragments": [{"x": 30, "y": 40 + i * 20, "width": 40, "height": 21}],
            "text_ink_fragments": [{"x": 30, "y": 40 + i * 20, "width": 40, "height": 21, "cellClusters": 1}]}
            for i, qid in enumerate(qids)]
        pairs, basis = text_overlaps(elements)
        quality = {"passed": False, "verified": False, "measurement_complete": True, "geometry_passed": False,
            "source_digest": source_digest(svg), "failures": [{"kind": "text_overlap", "pairs": pairs}],
            "overlap_basis": basis, "browser_metrics": {"elements": elements}}
        before = deepcopy((quality, bound_receipt, svg))
        diagnostic = render.native_table_line_pitch_hint(quality, svg, bound_receipt)
        assert diagnostic["measurement_version"] == TEXT_INK_PROBE_VERSION
        witness = diagnostic["witnesses"][0]
        assert (witness["row"], witness["column"]) == (0, 0)
        assert witness["measured_fragment_top_gap_px"] == 20
        assert witness["suggested_minimum_line_pitch_px"] == math.ceil(21 + OVERLAP_THRESHOLD_PX + witness["breathing_room_px"])
        assert (quality, bound_receipt, svg) == before
        for change in ("source", "basis", "parent", "cell", "nonoverlap"):
            bad_quality, bad_receipt = deepcopy(quality), deepcopy(bound_receipt)
            if change == "source":
                bad_quality["source_digest"] = "sha256:" + "0" * 64
            elif change == "basis":
                bad_quality["overlap_basis"]["pairs"][0]["basis"] = "ink"
            elif change == "parent":
                bad_quality["browser_metrics"]["elements"][1]["parent_qid"] = "another-owner"
            elif change == "cell":
                bad_receipt["bindings"][0]["kind"] = "text"
            else:
                bad_quality["browser_metrics"]["elements"][1]["text_ink_fragments"][0]["y"] = 100
            assert render.native_table_line_pitch_hint(bad_quality, svg, bad_receipt) is None

        ink_quality = deepcopy(quality)
        ink_quality["wrapper_digest"] = "sha256:" + "a" * 64
        ink_quality["browser_metrics"].update(fonts_ready=True, source_digest=ink_quality["wrapper_digest"])
        for item in ink_quality["browser_metrics"]["elements"]:
            item.update(font_loaded=True, font_resolution_source="loaded-font-face")
            fragment = item["text_ink_fragments"][0]
            fragment.update(cellClusters=0,
                glyphPaintCoverage={"expected": 1, "isolated": 1, "pair": 0, "cell": 0, "complete": True},
                glyphInkCoverage={"expected": 1, "measured": 1, "complete": True},
                glyphInkClusters=[{key: fragment[key] for key in ("x", "y", "width", "height")}
                    | {"index": 0, "length": 1, "basis": "isolated-matching-advance"}])
        ink_pairs, ink_basis = text_overlaps(ink_quality["browser_metrics"]["elements"])
        ink_quality.update(overlap_basis=ink_basis, failures=[{"kind": "text_overlap", "pairs": ink_pairs}])
        ink_diagnostic = render.native_table_line_pitch_hint(ink_quality, svg, bound_receipt)
        assert ink_diagnostic is not None and ink_diagnostic["witnesses"][0]["basis"] == "ink"
        assert diagnostic["witnesses"][0]["basis"] == "ink_cell"
        assert "every multiline cell" in ink_diagnostic["instruction"]
        assert "shared grid/borders" in ink_diagnostic["instruction"]
        assert ink_diagnostic["total_overlap_pairs"] == len(ink_pairs)
        for change in ("line_box", "mixed", "glyph_incomplete", "paint_incomplete", "missing_cluster",
                "font_unloaded", "font_untrusted", "fonts_not_ready", "wrapper", "different_cell"):
            altered = deepcopy(ink_quality)
            member = altered["browser_metrics"]["elements"][1]
            if change == "line_box": member["text_ink_fragments"] = [None]
            elif change == "mixed": altered["overlap_basis"]["pairs"][0]["basis"] = "mixed"
            elif change == "glyph_incomplete": member["text_ink_fragments"][0]["glyphInkCoverage"]["complete"] = False
            elif change == "paint_incomplete": member["text_ink_fragments"][0]["glyphPaintCoverage"]["complete"] = False
            elif change == "missing_cluster": member["text_ink_fragments"][0]["glyphInkClusters"] = []
            elif change == "font_unloaded": member["font_loaded"] = False
            elif change == "font_untrusted": member["font_resolution_source"] = "system-fallback"
            elif change == "fonts_not_ready": altered["browser_metrics"]["fonts_ready"] = False
            elif change == "wrapper": altered["browser_metrics"]["source_digest"] = "sha256:" + "b" * 64
            else: member["parent_qid"] = "another-cell-parent"
            assert render.native_table_line_pitch_hint(altered, svg, bound_receipt) is None
        # Two distinct canonical cell bindings must also be excluded even if
        # forged sibling telemetry claims a common parent.
        separate = ET.fromstring(svg)
        second_span = [node for node in separate.iter() if node.tag.endswith("}tspan")][1]
        second_span.set("id", "another-cell-binding")
        separate_svg = ET.tostring(separate, encoding="unicode")
        separate_receipt = deepcopy(bound_receipt)
        separate_receipt["bindings"].append({"kind": "table_cell", "element_id": "another-cell-binding",
            "source_id": "another-source-cell", "row": 0, "column": 1})
        separate_quality = deepcopy(ink_quality)
        separate_quality["source_digest"] = source_digest(separate_svg)
        assert render.native_table_line_pitch_hint(separate_quality, separate_svg, separate_receipt) is None
        quality, diagnostic = ink_quality, ink_diagnostic

        diagnostic_hint = json.dumps({"kind": "svg_readability_repair", "native_table_line_pitch": diagnostic},
            ensure_ascii=False, separators=(",", ":"))
        page = {"slide_index": 0, "category": "known_failed_output", "row_digest": "sealed-row",
            "completed_output_failure": {"reason": "completed_source_quality_failed", "diagnostic_hint": diagnostic_hint}}
        manifest = {"pages": [page]}
        recovery = SimpleNamespace(manifest=manifest, manifest_hash=digest(manifest))
        child = cached_page_payload(SimpleNamespace(slide_index=0), recovery, uuid.uuid4())
        assert child["repair_hint"] == diagnostic_hint and set(child) == {"partial_recovery", "repair_hint"}
        assert verify_cached_page(child, manifest, 0, "fresh-input") is page
        legacy_manifest = deepcopy(manifest)
        legacy_manifest["pages"][0]["completed_output_failure"].pop("diagnostic_hint")
        legacy_recovery = SimpleNamespace(manifest=legacy_manifest, manifest_hash=digest(legacy_manifest))
        assert set(cached_page_payload(SimpleNamespace(slide_index=0), legacy_recovery, uuid.uuid4())) == {"partial_recovery"}
        unknown_manifest = deepcopy(manifest)
        unknown_manifest["pages"][0]["category"] = "unknown"
        assert set(cached_page_payload(SimpleNamespace(slide_index=0),
            SimpleNamespace(manifest=unknown_manifest, manifest_hash=digest(unknown_manifest)), uuid.uuid4())) == {"partial_recovery"}

        ctx.source_document, ctx.source_units = table_source, source_pages(table_source)
        table_calls, seen_hints = [], []
        async def table_attempt(**kwargs):
            seen_hints.append(child.get("repair_hint"))
            table_calls.append(kwargs["provider_ordinal"])
            child["source_preservation"] = deepcopy(bound_receipt)
            child.setdefault("attempts", []).append({"kind": "provider", "raw_response": svg,
                "receipt": {"source_authoring_contract": contract, "attempt_ordinal": kwargs["provider_ordinal"],
                            "validation_error": None, "source_preservation": deepcopy(bound_receipt)}})
            return author.AuthorAttempt(svg=svg)
        async def table_measured(rendered_svg, **kwargs):
            assert rendered_svg == svg and kwargs["required_text"] == ["A\nB"]
            value = deepcopy(quality)
            if len(table_calls) == 2:
                value.update(passed=True, geometry_passed=True, failures=[])
            return SimpleNamespace(png=b"png", quality=value)
        monkeypatch.setattr(collect.author, "attempt_author", table_attempt)
        monkeypatch.setattr("services.svg_source_layout.verify_source_receipt", verify_source_receipt)
        monkeypatch.setattr("services.svg_renderer.render_svg_with_metrics", table_measured)
        result, _ = await collect._attempt_loop(ctx, request, uuid.uuid4(), 0, "source table", child,
            None, SimpleNamespace(fatal_error=None), hooks)
        assert table_calls == [1, 2] and result["quality"]["passed"] is True
        assert seen_hints[0] == diagnostic_hint
        assert json.loads(seen_hints[1])["native_table_line_pitch"] == diagnostic
        assert result["svg_content"] == svg and len(result["attempts"]) == 2


    if scenario == "visual_twice":
        import threading
        from datetime import datetime, timedelta, timezone
        from utils.provider_overload import BoundedProviderCall, current_provider_call_remaining_seconds, ProviderOutcomeError
        from utils.provider_terminal_outcome import ProviderTerminalOutcomeRecorder
        from utils.codex_stream_adapter import _ObservedResponses, _PROVIDER_EVENT_OBSERVER

        async def observer_case(mode):
            clock_value, waits, futures, observed = [0.0], [], [], {}
            original = TimeoutError("observer_wait_deadline")

            class EventFuture:
                def __init__(self):
                    self.cancelled = False
                def result(self, timeout):
                    waits.append(timeout)
                    if mode == "cause" or timeout < 60.0:
                        raise original
                    clock_value[0] += 60.0
                def cancel(self):
                    self.cancelled = True

            def schedule(coroutine, loop):
                coroutine.close()
                future = EventFuture()
                futures.append(future)
                return future

            async def sdk(source_document, page_index, style_snapshot, **kwargs):
                await kwargs["before_dispatch"]()
                observer = _PROVIDER_EVENT_OBSERVER.get()
                assert observer is not None
                raw = '<svg xmlns="http://www.w3.org/2000/svg" width="1280" height="720"/>'
                response_id = "resp_" + "a" * 48
                events = [{"type": "response.created", "response": {"id": response_id}},
                    {"type": "response.completed", "response": {"id": response_id,
                    "status": "completed", "output": [{"type": "message", "content": [
                    {"type": "output_text", "text": raw}]}],
                    "usage": {"input_tokens": 1, "output_tokens": 1, "total_tokens": 2}}}]
                def run(remaining):
                    clock_value[0] = 361.0 if mode == "provider_expired" else 80.0
                    list(_ObservedResponses._events(iter(events), observer=observer))
                    return SimpleNamespace(svg_content=raw, raw_response=raw,
                        receipt={}, native_declarations=None)
                def bounded():
                    try:
                        return BoundedProviderCall(run, threading.Event(),
                            ProviderTerminalOutcomeRecorder(), clock=lambda: clock_value[0],
                            deadline_seconds=360.0)()
                    finally:
                        observed["context_cleared"] = current_provider_call_remaining_seconds() is None
                return await asyncio.to_thread(bounded)

            event_ctx = SimpleNamespace(manifests=[{}], snapshot={"source_authoring_contract":
                {"version": "svg-source-whole-page/v1"}}, canonical_assets=False,
                source_reflow=True, raster_reconstruction=False, source_document=None,
                source_units=[{}], source_font_profile=None, physical_kwargs={}, planned=[{}],
                canvas=SimpleNamespace(width_px=1280, height_px=720))
            event_state = SimpleNamespace(fatal_error=None, terminal_drain_enabled=True,
                hard_deadline_at=datetime.now(timezone.utc) + timedelta(
                    seconds=-1 if mode == "operation_expired" else 45 if mode == "operation_short" else 900))
            event_payload = {"attempts": []}
            with monkeypatch.context() as events_scope:
                events_scope.setattr(asyncio, "run_coroutine_threadsafe", schedule)
                events_scope.setattr(
                    "services.generation_core.authoring.author_source_svg_layout.author_source_svg_layout", sdk)
                events_scope.setattr(author, "bind_native_declarations", lambda *args: None)
                event_checkpoint = AsyncMock()
                async def invoke():
                    return await actual_attempt_author(ctx=event_ctx,
                        request=SimpleNamespace(instructions="", _document_canvas=None),
                        index=0, content={}, role="body", count=1, payload=event_payload,
                        ordinal=1, provider_ordinal=1, job=SimpleNamespace(id=uuid.uuid4(),
                        presentation_id=uuid.uuid4(), attempt_token="test-attempt", attempt_number=1),
                        state=event_state, checkpoint=event_checkpoint, async_session_maker=None)
                if mode == "success":
                    await invoke()
                    assert len(waits) == 2 and waits == [280.0, 220.0]
                    assert all(value > 30.0 for value in waits)
                    assert all(not future.cancelled for future in futures)
                elif mode == "operation_expired":
                    with pytest.raises(AuthoredStageError):
                        await invoke()
                    assert futures == []
                else:
                    with pytest.raises(ProviderOutcomeError) as rejected:
                        await invoke()
                    assert rejected.value.__cause__ is original
                    assert futures and futures[-1].cancelled
                    if mode == "provider_expired":
                        assert waits == [0.0]
                    if mode == "operation_short":
                        assert 0.0 < waits[0] <= 45.0
                    assert event_payload["provider_diagnostics"][-1]["diagnostics"]["causeExceptionType"] == "TimeoutError"
            assert observed["context_cleared"]

        for observer_mode in ("success", "operation_short", "provider_expired", "operation_expired", "cause"):
            await observer_case(observer_mode)


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
