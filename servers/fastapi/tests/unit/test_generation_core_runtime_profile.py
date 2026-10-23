from services.generation_core.runtime.profile import (
    DEFAULT_AUTHOR_CONCURRENCY,
    DEFAULT_AUTHOR_CONCURRENCY_SOURCE_REFLOW,
    DEFAULT_ILLUSTRATION_BUDGET,
    DEFAULT_RENDER_CONCURRENCY,
    DEFAULT_RENDER_CONCURRENCY_SOURCE_REFLOW,
    resolve_svg_runtime_profile,
    resolve_svg_runtime_profile_with_provenance,
)


def test_defaults_match_the_source_reflow_runtime_defaults(monkeypatch):
    import services.generation_core.runtime.profile as profile

    for key in (
        "SVG_AUTHOR_CONCURRENCY",
        "SVG_AUTHOR_CONCURRENCY_SOURCE_REFLOW",
        "SVG_RENDER_CONCURRENCY",
        "SVG_RENDER_CONCURRENCY_SOURCE_REFLOW",
        "SVG_ILLUSTRATION_BUDGET",
        "SVG_ILLUSTRATION_TIMEOUT_SECONDS",
        "IMAGE_GENERATION_TIMEOUT_SECONDS",
    ):
        monkeypatch.delenv(key, raising=False)
    # Check the source-reflow setting default independently of the provider pool cap.
    monkeypatch.setattr(profile, "_author_executor_capacity", lambda: 8)

    ordinary = resolve_svg_runtime_profile(source_reflow=False)
    assert ordinary.author_concurrency == DEFAULT_AUTHOR_CONCURRENCY == 3
    assert ordinary.render_concurrency == DEFAULT_RENDER_CONCURRENCY == 3

    reflow, provenance = resolve_svg_runtime_profile_with_provenance(source_reflow=True)
    assert reflow.author_concurrency == DEFAULT_AUTHOR_CONCURRENCY_SOURCE_REFLOW == 6
    assert reflow.render_concurrency == DEFAULT_RENDER_CONCURRENCY_SOURCE_REFLOW == 2
    assert provenance["author_concurrency"]["source"] == "default"

    assert ordinary.illustration_budget == DEFAULT_ILLUSTRATION_BUDGET
    # No svg override: the authored image knob's default (180 s) applies.
    assert ordinary.illustration_timeout_seconds == 180


def test_illustration_timeout_follows_the_authored_image_knob(monkeypatch):
    monkeypatch.delenv("SVG_ILLUSTRATION_TIMEOUT_SECONDS", raising=False)
    monkeypatch.setenv("IMAGE_GENERATION_TIMEOUT_SECONDS", "90")
    assert resolve_svg_runtime_profile(source_reflow=False).illustration_timeout_seconds == 90
    monkeypatch.setenv("SVG_ILLUSTRATION_TIMEOUT_SECONDS", "30")
    assert resolve_svg_runtime_profile(source_reflow=False).illustration_timeout_seconds == 30


def test_explicit_env_overrides_win_over_defaults(monkeypatch):
    import services.generation_core.runtime.profile as profile

    monkeypatch.setattr(profile, "_author_executor_capacity", lambda: 8)
    monkeypatch.setenv("SVG_AUTHOR_CONCURRENCY", "8")
    monkeypatch.setenv("SVG_RENDER_CONCURRENCY_SOURCE_REFLOW", "5")
    monkeypatch.setenv("SVG_ILLUSTRATION_BUDGET", "6")

    ordinary = resolve_svg_runtime_profile(source_reflow=False)
    assert ordinary.author_concurrency == 8
    assert ordinary.illustration_budget == 6

    reflow = resolve_svg_runtime_profile(source_reflow=True)
    assert reflow.render_concurrency == 5
    # The non-reflow override must not leak into the reflow branch.
    assert reflow.author_concurrency == DEFAULT_AUTHOR_CONCURRENCY_SOURCE_REFLOW


def test_source_reflow_author_concurrency_clamps_to_executor(monkeypatch):
    import services.generation_core.runtime.profile as profile

    monkeypatch.delenv("SVG_AUTHOR_CONCURRENCY_SOURCE_REFLOW", raising=False)
    monkeypatch.setattr(profile, "_author_executor_capacity", lambda: 4)
    resolved, provenance = resolve_svg_runtime_profile_with_provenance(source_reflow=True)
    assert resolved.author_concurrency == 4
    assert provenance["author_concurrency"] == {
        "source": "default", "requested": 6, "executor_capacity": 4,
        "clamped_to_executor": True, "resolved": 4, "effective": 4,
    }


def test_source_reflow_author_concurrency_records_env_source(monkeypatch):
    import services.generation_core.runtime.profile as profile

    monkeypatch.setenv("SVG_AUTHOR_CONCURRENCY_SOURCE_REFLOW", "3")
    monkeypatch.setattr(profile, "_author_executor_capacity", lambda: 8)
    resolved, provenance = resolve_svg_runtime_profile_with_provenance(source_reflow=True)
    assert resolved.author_concurrency == 3
    assert provenance["author_concurrency"]["source"] == "env"
