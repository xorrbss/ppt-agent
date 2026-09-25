from services.generation_core.runtime.profile import (
    DEFAULT_AUTHOR_CONCURRENCY,
    DEFAULT_AUTHOR_CONCURRENCY_SOURCE_REFLOW,
    DEFAULT_ILLUSTRATION_BUDGET,
    DEFAULT_ILLUSTRATION_TIMEOUT_SECONDS,
    DEFAULT_RENDER_CONCURRENCY,
    DEFAULT_RENDER_CONCURRENCY_SOURCE_REFLOW,
    resolve_svg_runtime_profile,
)


def test_defaults_match_the_previous_hard_coded_semaphores(monkeypatch):
    for key in (
        "SVG_AUTHOR_CONCURRENCY",
        "SVG_AUTHOR_CONCURRENCY_SOURCE_REFLOW",
        "SVG_RENDER_CONCURRENCY",
        "SVG_RENDER_CONCURRENCY_SOURCE_REFLOW",
        "SVG_ILLUSTRATION_BUDGET",
        "SVG_ILLUSTRATION_TIMEOUT_SECONDS",
    ):
        monkeypatch.delenv(key, raising=False)

    ordinary = resolve_svg_runtime_profile(source_reflow=False)
    assert ordinary.author_concurrency == DEFAULT_AUTHOR_CONCURRENCY == 3
    assert ordinary.render_concurrency == DEFAULT_RENDER_CONCURRENCY == 3

    reflow = resolve_svg_runtime_profile(source_reflow=True)
    assert reflow.author_concurrency == DEFAULT_AUTHOR_CONCURRENCY_SOURCE_REFLOW == 2
    assert reflow.render_concurrency == DEFAULT_RENDER_CONCURRENCY_SOURCE_REFLOW == 1

    assert ordinary.illustration_budget == DEFAULT_ILLUSTRATION_BUDGET
    assert ordinary.illustration_timeout_seconds == DEFAULT_ILLUSTRATION_TIMEOUT_SECONDS


def test_explicit_env_overrides_win_over_defaults(monkeypatch):
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
