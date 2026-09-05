"""Unit tests for the authored-run-contact-sheet CLI's pure helpers.

No Chromium, no real multi-slide artifact folders: the metrics-extraction,
grid-layout and gap-handling logic are all pure functions over small dicts /
lists, and a single tiny generated PNG stands in for a real slide render
where the render module needs an actual image to open.
"""

from __future__ import annotations

import re

import pytest
from PIL import Image

from scripts.authored_run_contact_sheet_discovery import (
    derive_run_label,
    discover_reference_dir,
    discover_run_dir,
    extract_run_label,
    index_by_pattern,
)
from scripts.authored_run_contact_sheet_grid import (
    compute_row_height,
    plan_pages,
    scale_to_width,
)
from scripts.authored_run_contact_sheet_metrics import (
    SlideMetricsSummary,
    extract_slide_metrics,
    median,
    render_metrics_markdown,
    render_per_slide_table,
    render_summary_table,
    summarize_run,
)
from scripts.authored_run_contact_sheet_render import render_contact_sheet_pages


# ---------------------------------------------------------------------------
# Metrics extraction
# ---------------------------------------------------------------------------


def test_extract_slide_metrics_missing_returns_missing_summary():
    summary = extract_slide_metrics(None)
    assert summary.missing is True
    assert summary.text_owners == 0
    assert summary.visible_chars == 0
    assert summary.min_font_px is None


def test_extract_slide_metrics_counts_owned_text_and_font():
    metrics = {
        "elements": [
            {"tag": "h1", "has_owned_text": True, "owned_text": "Title", "font_size": 40},
            {"tag": "span", "has_owned_text": True, "owned_text": "body copy", "font_size": 14},
            {"tag": "div", "has_owned_text": False, "owned_text": "", "font_size": 16},
        ]
    }
    summary = extract_slide_metrics(metrics)
    assert summary.missing is False
    assert summary.text_owners == 2
    assert summary.visible_chars == len("Title") + len("body copy")
    assert summary.min_font_px == 14


def test_extract_slide_metrics_clipped_count():
    metrics = {
        "elements": [
            {"tag": "div", "clipped": True},
            {"tag": "div", "clipped": False},
            {"tag": "div", "clipped": True},
        ]
    }
    assert extract_slide_metrics(metrics).clipped_count == 2


def test_extract_slide_metrics_image_count_prefers_tag_img():
    metrics = {"elements": [{"tag": "img"}, {"tag": "img"}, {"tag": "div", "owned_text": "<img "}]}
    assert extract_slide_metrics(metrics).image_count == 2


def test_extract_slide_metrics_image_count_falls_back_to_substring():
    metrics = {"elements": [{"tag": "div", "rendered_text": "inline <img markup"}]}
    assert extract_slide_metrics(metrics).image_count == 1


def test_extract_slide_metrics_ignores_non_dict_elements():
    # Defensive: a malformed elements list must not raise.
    metrics = {"elements": [None, "oops", {"tag": "div"}]}
    summary = extract_slide_metrics(metrics)
    assert summary.missing is False
    assert summary.text_owners == 0


def test_median_even_and_odd():
    assert median([1.0, 2.0, 3.0]) == 2.0
    assert median([1.0, 2.0, 3.0, 4.0]) == 2.5
    assert median([]) is None


def test_summarize_run_only_uses_present_slides_for_medians():
    per_slide = {
        0: SlideMetricsSummary(text_owners=4, visible_chars=100, min_font_px=12, missing=False),
        1: SlideMetricsSummary(missing=True),  # no metrics file -- a gap
        2: SlideMetricsSummary(text_owners=2, visible_chars=40, min_font_px=10, missing=False),
    }
    run = summarize_run("run3", per_slide)
    assert run.slide_count == 3  # every discovered index counts
    assert run.median_text_owners == 3.0
    assert run.median_visible_chars == 70.0
    assert run.min_font_px == 10


def test_summarize_run_all_missing_has_no_medians():
    per_slide = {0: SlideMetricsSummary(missing=True), 1: SlideMetricsSummary(missing=True)}
    run = summarize_run("run-no-metrics", per_slide)
    assert run.slide_count == 2
    assert run.median_text_owners is None
    assert run.min_font_px is None
    assert run.slides_with_clipped == 0


# ---------------------------------------------------------------------------
# Markdown rendering
# ---------------------------------------------------------------------------


def test_render_summary_table_has_one_row_per_run():
    runs = [summarize_run("run3", {}), summarize_run("run7", {0: SlideMetricsSummary(missing=True)})]
    table = render_summary_table(runs)
    assert "| run3 |" in table
    assert "| run7 |" in table


def test_render_per_slide_table_marks_gap_vs_no_metrics():
    per_slide = {0: SlideMetricsSummary(text_owners=1, visible_chars=5, missing=False)}
    table = render_per_slide_table("run3", per_slide, num_rows=3)
    assert "s00" in table and "5" in table
    assert "(no slide)" in table  # index 2 was never discovered at all
    # index 1 wasn't in per_slide either -> also renders as a gap row.


def test_render_metrics_markdown_includes_all_runs():
    runs = [summarize_run("run3", {0: SlideMetricsSummary(missing=True)})]
    by_label = {"run3": {0: SlideMetricsSummary(missing=True)}}
    doc = render_metrics_markdown(runs, by_label, num_rows=1)
    assert "# Authored run contact sheet metrics" in doc
    assert "### run3" in doc


# ---------------------------------------------------------------------------
# Grid layout arithmetic
# ---------------------------------------------------------------------------


def test_scale_to_width_preserves_aspect_ratio():
    assert scale_to_width(1280, 720, 320) == 180
    assert scale_to_width(0, 720, 320) == 320  # degenerate width never crashes


def test_compute_row_height_uses_tallest_present_cell():
    assert compute_row_height([100, 150, None], placeholder_height=90, label_padding=8) == 158


def test_compute_row_height_all_gaps_uses_placeholder():
    assert compute_row_height([None, None], placeholder_height=90, label_padding=8) == 98


def test_plan_pages_single_page_when_under_budget():
    assert plan_pages([100, 100, 100], header_height=50, max_height=1000) == [(0, 3)]


def test_plan_pages_splits_when_over_budget():
    # header(50) + 5*200 = 1050 > 1000, so it must split.
    pages = plan_pages([200, 200, 200, 200, 200], header_height=50, max_height=1000)
    assert len(pages) > 1
    # Every row must appear in exactly one page, in order, none dropped.
    covered = []
    for start, end in pages:
        covered.extend(range(start, end))
    assert covered == list(range(5))


def test_plan_pages_oversized_single_row_gets_its_own_page():
    pages = plan_pages([5000, 10, 10], header_height=50, max_height=1000)
    assert pages[0] == (0, 1)


def test_plan_pages_empty_rows_returns_one_empty_page():
    assert plan_pages([], header_height=50, max_height=1000) == [(0, 0)]


# ---------------------------------------------------------------------------
# Discovery / gap handling
# ---------------------------------------------------------------------------


def test_index_by_pattern_skips_missing_indices():
    pattern = re.compile(r"^s(\d+)\.png$")
    result = index_by_pattern(["s00.png", "s01.png", "s03.png", "notaslide.txt"], pattern)
    assert result == {0: "s00.png", 1: "s01.png", 3: "s03.png"}
    assert 2 not in result  # the gap


def test_extract_run_label_finds_run_token():
    assert extract_run_label("authored-fixture-run7-20260905") == "run7"


def test_extract_run_label_falls_back_to_whole_name():
    assert extract_run_label("august-reference") == "august-reference"


def test_derive_run_label_looks_at_parent_when_dir_is_slides(tmp_path):
    run_dir = tmp_path / "authored-fixture-run7-20260905" / "slides"
    run_dir.mkdir(parents=True)
    assert derive_run_label(run_dir) == "run7"


def test_discover_run_dir_handles_missing_metrics_file(tmp_path):
    run_dir = tmp_path / "slides"
    run_dir.mkdir()
    (run_dir / "s00.png").write_bytes(b"fake")
    (run_dir / "s01.png").write_bytes(b"fake")
    (run_dir / "s01.metrics.json").write_text("{}", encoding="utf-8")

    entries = discover_run_dir(run_dir)
    assert entries[0] == (run_dir / "s00.png", None)
    assert entries[1] == (run_dir / "s01.png", run_dir / "s01.metrics.json")


def test_discover_run_dir_leaves_a_gap_for_a_missing_slide_index(tmp_path):
    run_dir = tmp_path / "slides"
    run_dir.mkdir()
    (run_dir / "s00.png").write_bytes(b"fake")
    (run_dir / "s02.png").write_bytes(b"fake")  # s01 missing entirely

    entries = discover_run_dir(run_dir)
    assert set(entries.keys()) == {0, 2}


def test_discover_reference_dir_orders_by_name(tmp_path):
    ref_dir = tmp_path / "reference"
    ref_dir.mkdir()
    (ref_dir / "slide-b.png").write_bytes(b"fake")
    (ref_dir / "slide-a.png").write_bytes(b"fake")

    entries = discover_reference_dir(ref_dir)
    assert entries[0].name == "slide-a.png"
    assert entries[1].name == "slide-b.png"


def test_discover_reference_dir_missing_directory_returns_empty(tmp_path):
    assert discover_reference_dir(tmp_path / "does-not-exist") == {}


# ---------------------------------------------------------------------------
# Rendering (one tiny real PNG, no full pipeline)
# ---------------------------------------------------------------------------


class _FakeAsset:
    def __init__(self, image_path):
        self.image_path = image_path


class _FakeColumn:
    def __init__(self, label, slides):
        self.label = label
        self.slides = slides


@pytest.fixture()
def tiny_png(tmp_path):
    path = tmp_path / "tiny.png"
    Image.new("RGB", (40, 20), "blue").save(path)
    return path


def test_render_contact_sheet_pages_writes_one_file_per_page(tmp_path, tiny_png):
    columns = [_FakeColumn("run3", {0: _FakeAsset(tiny_png)})]
    row_heights = [30]
    pages = [(0, 1)]

    written = render_contact_sheet_pages(
        out_dir=tmp_path,
        columns=columns,
        row_heights=row_heights,
        pages=pages,
        thumb_width=32,
        header_height=20,
        index_col_width=10,
        cell_padding=4,
    )
    assert len(written) == 1
    assert written[0].name == "contact-sheet.png"
    assert written[0].is_file()


def test_render_contact_sheet_pages_names_split_files(tmp_path, tiny_png):
    columns = [_FakeColumn("run3", {0: _FakeAsset(tiny_png), 1: _FakeAsset(tiny_png)})]
    written = render_contact_sheet_pages(
        out_dir=tmp_path,
        columns=columns,
        row_heights=[30, 30],
        pages=[(0, 1), (1, 2)],
        thumb_width=32,
        header_height=20,
        index_col_width=10,
        cell_padding=4,
    )
    assert [p.name for p in written] == ["contact-sheet-1.png", "contact-sheet-2.png"]


def test_render_contact_sheet_pages_handles_missing_cell(tmp_path):
    columns = [_FakeColumn("run3", {})]  # no slide at row 0 at all -- a gap
    written = render_contact_sheet_pages(
        out_dir=tmp_path,
        columns=columns,
        row_heights=[30],
        pages=[(0, 1)],
        thumb_width=32,
        header_height=20,
        index_col_width=10,
        cell_padding=4,
    )
    assert written[0].is_file()
