"""Provenance stays outside recovery identity, including serial repurchases."""

import asyncio
from dataclasses import asdict
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from services.generation_core.runtime import profile as runtime


def test_clamped_environment_request_is_preserved_in_separate_metadata(monkeypatch):
    monkeypatch.setenv("SVG_AUTHOR_CONCURRENCY_SOURCE_REFLOW", "12")
    monkeypatch.setattr(runtime, "_author_executor_capacity", lambda: 4)
    profile, metadata = runtime.resolve_svg_runtime_profile_with_provenance(source_reflow=True)
    assert profile.author_concurrency == 4
    assert metadata["author_concurrency"] == {
        "source": "env", "requested": 12, "executor_capacity": 4,
        "clamped_to_executor": True, "resolved": 4, "effective": 4,
    }
    assert set(asdict(profile)) == {
        "author_concurrency", "render_concurrency", "illustration_budget",
        "illustration_timeout_seconds", "terminal_policy", "vision_advisory",
    }


@pytest.mark.parametrize("source_reflow", [False, True])
def test_provenance_does_not_change_recovery_or_render_environment_binding(
    monkeypatch, tmp_path, source_reflow,
):
    from services.generation_core.drivers.svg import collect, quality_recovery
    from services import public_render_runtime_identity, svg_partial_recovery, svg_trusted_fonts
    from utils import svg_template_fonts

    monkeypatch.setattr(runtime, "_author_executor_capacity", lambda: 8)
    env_name = "SVG_AUTHOR_CONCURRENCY_SOURCE_REFLOW" if source_reflow else "SVG_AUTHOR_CONCURRENCY"
    monkeypatch.delenv(env_name, raising=False)
    default, default_metadata = runtime.resolve_svg_runtime_profile_with_provenance(
        source_reflow=source_reflow,
    )
    monkeypatch.setenv(env_name, str(default.author_concurrency))
    explicit, explicit_metadata = runtime.resolve_svg_runtime_profile_with_provenance(
        source_reflow=source_reflow,
    )
    assert default_metadata != explicit_metadata
    assert asdict(default) == asdict(explicit)

    # Isolate environment identity from installed font assets and worker state.
    font = tmp_path / "font.bin"
    font.write_bytes(b"test font")
    monkeypatch.setattr(svg_template_fonts, "FONT_PATH", font)
    monkeypatch.setattr(svg_trusted_fonts, "BUNDLED_FACES", [])
    worker = {"checkpointReuseEligible": True, "runtime": "test"}
    monkeypatch.setattr(public_render_runtime_identity, "public_render_runtime_binding", lambda: worker)
    monkeypatch.setattr(svg_partial_recovery, "_page_render_code_digest", lambda: "test-code")
    ctx = SimpleNamespace(
        runtime_profile=default, runtime_profile_provenance=default_metadata,
        snapshot={}, quality_review_contents=["content"], roles=["content"],
        manifests=[{}], physical_kwargs={}, source_font_profile=None,
        canvas=SimpleNamespace(width_px=1280, height_px=720), count=1,
        source_reflow=source_reflow, raster_reconstruction=False,
    )
    request = SimpleNamespace(instructions=None, writing_policy=None)
    job = SimpleNamespace(workspace_operation_id="operation")
    binding = quality_recovery.recovery_binding(ctx, request, job)
    environment = collect._page_render_environment(ctx, request, 0)
    assert environment is not None

    # A pre-provenance profile had exactly these six effective values.
    legacy = SimpleNamespace(
        author_concurrency=default.author_concurrency,
        render_concurrency=default.render_concurrency,
        illustration_budget=default.illustration_budget,
        illustration_timeout_seconds=default.illustration_timeout_seconds,
        terminal_policy=default.terminal_policy, vision_advisory=default.vision_advisory,
    )
    for profile in (explicit, legacy):
        ctx.runtime_profile = profile
        ctx.runtime_profile_provenance = explicit_metadata
        assert quality_recovery.recovery_binding(ctx, request, job) == binding
        assert collect._digest(collect._page_render_environment(ctx, request, 0)) == collect._digest(environment)


@pytest.mark.parametrize("append_only", [False, True])
def test_pinning_records_actual_repurchase_override_without_changing_identity(monkeypatch, append_only):
    from services import svg_presentation_prepare, svg_timeout_repurchase

    monkeypatch.setenv("SVG_AUTHOR_CONCURRENCY_SOURCE_REFLOW", "12")
    monkeypatch.setattr(runtime, "_author_executor_capacity", lambda: 4)
    manifest = {"append_only_recovery": True} if append_only else {
        "bounded_completion_recovery": True,
        "execution_profile": {"cached_first_page_indexes": [0]},
    }
    operation = SimpleNamespace(request_payload={"svg_timeout_repurchase_manifest": manifest})
    session = SimpleNamespace(get=AsyncMock(return_value=operation))
    job = SimpleNamespace(workspace_operation_id="operation")
    monkeypatch.setattr(svg_timeout_repurchase, "verify_append_only_child_lineage", AsyncMock())
    monkeypatch.setattr(svg_timeout_repurchase, "_completion_operation_manifest", AsyncMock())

    profile, metadata, cached_first = asyncio.run(svg_presentation_prepare._pin_runtime_profile(
        session, job, source_reflow=True, raster_reconstruction=False,
    ))
    assert profile.author_concurrency == profile.render_concurrency == 1
    assert cached_first == (None if append_only else [0])
    assert metadata["author_concurrency"] == {
        "source": "env", "requested": 12, "executor_capacity": 4,
        "clamped_to_executor": True, "resolved": 4, "effective": 1,
        "override": "repurchase",
    }
    assert "author_concurrency_source" not in asdict(profile)
