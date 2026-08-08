"""Unit tests for composer-stability metrics (G8). Pure logic — no LLM."""
from utils.composer_metrics import evaluate_archetypes, summarize, passes


def test_distinct_and_adjacent_dups():
    m = evaluate_archetypes(
        ["cover", "agenda", "stat-hero", "stat-hero", "closing"], requested_n=5
    )
    assert m["n_slides"] == 5
    assert m["n_match"] is True
    assert m["distinct_archetypes"] == 4
    assert m["adjacent_dups"] == 1
    assert m["no_adjacent_dups"] is False


def test_perfect_variety():
    m = evaluate_archetypes(
        ["cover", "agenda", "stat-hero", "timeline", "closing"], requested_n=5
    )
    assert m["distinct_archetypes"] == 5
    assert m["adjacent_dups"] == 0
    assert m["no_adjacent_dups"] is True
    assert m["variety_ratio"] == 1.0


def test_n_match_false_and_none():
    assert evaluate_archetypes(["cover", "closing"], requested_n=5)["n_match"] is False
    # requested_n=None means "don't check count"
    assert evaluate_archetypes(["cover", "closing"], requested_n=None)["n_match"] is True


def test_empty_deck_is_safe():
    m = evaluate_archetypes([], requested_n=None)
    assert m["n_slides"] == 0
    assert m["variety_ratio"] == 0.0
    assert m["adjacent_dups"] == 0


def test_summarize_aggregates():
    r1 = evaluate_archetypes(["cover", "agenda", "closing"], 3)  # 3 distinct, 0 dup
    r2 = evaluate_archetypes(["cover", "cover", "closing"], 3)   # 2 distinct, 1 dup
    s = summarize([r1, r2], schema_valid_count=2, total_attempts=2)
    assert s["valid_decks"] == 2
    assert s["schema_valid_rate"] == 1.0
    assert s["n_match_rate"] == 1.0
    assert s["total_adjacent_dups"] == 1
    assert s["no_adjacent_dup_rate"] == 0.5
    assert 0.0 <= s["mean_variety_ratio"] <= 1.0


def test_passes_gate():
    good = [evaluate_archetypes(["cover", "agenda", "stat-hero", "timeline", "closing"], 5)] * 3
    s = summarize(good, schema_valid_count=3, total_attempts=3)
    assert passes(s) is True
    # an all-identical deck fails the variety/adjacency thresholds
    bad = [evaluate_archetypes(["cover", "cover", "cover"], 3)]
    s2 = summarize(bad, schema_valid_count=1, total_attempts=1)
    assert passes(s2) is False
