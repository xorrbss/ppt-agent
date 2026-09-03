"""Pure-helper tests for the Composer offline evaluation harness.

No Chromium, no database: argument parsing, report aggregation, readback
binding projection, enrichment coverage, Markdown and contact-sheet layout.
"""

from __future__ import annotations

import json

import pytest

from scripts import composer_offline_eval as cli
from scripts import composer_offline_eval_report as report_lib
from scripts.composer_offline_eval_db import existing_pptx
from scripts.composer_offline_eval_fit import slide_file_stem, slide_row
from scripts.composer_offline_eval_inputs import (
    NEUTRAL_TOKENS,
    blend_hex,
    deck_title_from_units,
    sorted_units,
)
from scripts.composer_offline_eval_markdown import render_markdown
from scripts.composer_offline_eval_sheets import sheet_file_name, sheet_layout, thumbnail_size

SIZED = {"sizePt": 18.0, "bold": False, "italic": False, "color": None}
UNSIZED = {"sizePt": None, "bold": False, "italic": False, "color": None}


def _text_block(block_id: str, text: str, *, role: str, order: int, paragraphs: list, **extra) -> dict:
    payload = {"text": text, "paragraphs": paragraphs, "geometry": {"x": 1, "y": 2, "cx": 300, "cy": 40}, **extra}
    return {"sourceBlockId": block_id, "kind": "text", "semanticRole": role, "order": order, "payload": payload}


def _manifest() -> dict:
    return {
        "manifestId": "scm_test",
        "manifestDigest": "sha256:abc",
        "slideSize": {"cx": 12192000, "cy": 6858000},
        "units": [
            {
                "sourceUnitId": "source-slide:2",
                "sourceIndex": 1,
                "blocks": [
                    _text_block(
                        "source-slide:2:text:1", "Second page body", role="body", order=0,
                        paragraphs=[{"runs": ["Second page body"], "runStyles": [SIZED]}],
                        dominantSizePt=18.0, placeholderType="body",
                    )
                ],
            },
            {
                "sourceUnitId": "source-slide:1",
                "sourceIndex": 0,
                "blocks": [
                    _text_block(
                        "source-slide:1:text:1", "Deck title\nsubtitle line", role="title", order=1,
                        paragraphs=[
                            {"runs": ["Deck title", "\n", "subtitle line"], "runStyles": [SIZED, UNSIZED, UNSIZED]}
                        ],
                        geometry={}, dominantSizePt=None, placeholderType=None,
                    ),
                    {
                        "sourceBlockId": "source-slide:1:image:2",
                        "kind": "image",
                        "semanticRole": "image",
                        "order": 0,
                        "payload": {"assetId": "scma_1", "geometry": {"x": 0, "y": 0, "cx": 10, "cy": 10}},
                    },
                    _text_block("source-slide:1:text:3", "   ", role="body", order=2, paragraphs=[]),
                ],
            },
        ],
    }


def _outline() -> dict:
    return {
        "slides": [
            {
                "sourceUnitId": "source-slide:1",
                "readingOrder": ["source-slide:1:image:2", "source-slide:1:text:1", "source-slide:1:text:3"],
            },
            {"sourceUnitId": "source-slide:2", "readingOrder": ["source-slide:2:text:1"]},
        ]
    }


# --- Arguments -----------------------------------------------------------------


def test_parse_args_requires_a_source() -> None:
    with pytest.raises(SystemExit):
        cli.parse_args([])


def test_parse_args_rejects_pptx_with_database_flags(tmp_path) -> None:
    with pytest.raises(SystemExit):
        cli.parse_args(["--pptx", str(tmp_path / "a.pptx"), "--snapshot", "x"])


def test_parse_args_defaults() -> None:
    args = cli.parse_args(["--snapshot", "56d0e946-11e4-489d-94af-955384efb871"])
    assert args.concurrency == 2
    assert args.owners == cli.DEFAULT_OWNERS
    assert args.env_json == cli.DEFAULT_ENV_JSON
    assert args.limit is None and args.dry_run is False and args.max_renders is None


def test_parse_args_owner_repeatable_and_limit_validation() -> None:
    args = cli.parse_args(["--manifest-id", "scm_x", "--owner", "a", "--owner", "b", "--limit", "3"])
    assert args.owners == ("a", "b") and args.limit == 3
    with pytest.raises(SystemExit):
        cli.parse_args(["--manifest-id", "scm_x", "--limit", "0"])


def test_apply_target_fills_ids_and_label() -> None:
    args = cli.build_parser().parse_args(["--target", "deck-35"])
    cli.apply_target(args, {"deck-35": {"referenceSnapshotId": "snap", "manifestId": "scm_a", "label": "deck-35"}})
    assert (args.snapshot, args.manifest_id, args.label) == ("snap", "scm_a", "deck-35")
    with pytest.raises(SystemExit):
        cli.apply_target(args, {})


def test_checked_in_targets_are_complete() -> None:
    targets = cli.load_targets(cli.TARGETS_PATH)
    for name in ("deck-43", "deck-35"):
        assert targets[name]["referenceSnapshotId"] and targets[name]["manifestId"].startswith("scm_")
        assert targets[name]["pageCount"] == int(name.split("-")[1])


def test_import_composer_stages_reports_missing(monkeypatch) -> None:
    monkeypatch.setattr(
        cli, "STAGE_IMPORTS", (("services.composer.definitely_missing", "f"), ("services.composer.plan", "nope"))
    )
    stages, missing = cli.import_composer_stages()
    assert stages is None
    assert missing == [
        "missing module: services.composer.definitely_missing"
        " (while importing services.composer.definitely_missing for f)",
        "missing function: services.composer.plan.nope",
    ]


def test_import_composer_stages_dry_run_needs_only_the_classifier(monkeypatch) -> None:
    monkeypatch.setattr(
        cli, "STAGE_IMPORTS", (("services.composer.contracts", "source_px_scale"), ("services.composer.missing", "x"))
    )
    monkeypatch.setattr(cli, "DRY_RUN_STAGES", frozenset({"source_px_scale"}))
    monkeypatch.setattr(cli, "ComposerStages", lambda **kwargs: dict(kwargs))
    stages, missing = cli.import_composer_stages(dry_run=True)
    assert missing == [] and set(stages) == {"source_px_scale"}
    stages, missing = cli.import_composer_stages(dry_run=False)
    assert stages is None and missing[0].startswith("missing module: services.composer.missing")


def test_slide_file_stem_pads_to_deck_size() -> None:
    assert slide_file_stem(0, 43) == "slide-01"
    assert slide_file_stem(99, 120) == "slide-100"


def test_slide_row_populates_index_and_chrome_from_the_receipt() -> None:
    from services.composer.plan import ComposerSlidePlan, ComposerSlideResult, FitParameters, FitReceipt

    plan = ComposerSlidePlan(slide_index=2, source_unit_id="unit-2", archetype="text")
    receipt = FitReceipt(
        slide_index=2, rung=1, parameters=FitParameters(chrome_level=2), renders=3, estimate_used=True,
    )
    result = ComposerSlideResult(slide_index=2, html="<html></html>", receipt=receipt)
    row = slide_row(plan, result, None, 12.5)
    assert row["index"] == 2 and row["chrome"] == 2


def test_slide_row_leaves_index_and_chrome_none_without_a_result() -> None:
    from services.composer.plan import ComposerSlidePlan

    plan = ComposerSlidePlan(slide_index=0, source_unit_id="unit-0", archetype="text")
    row = slide_row(plan, None, None, None)
    assert row["index"] is None and row["chrome"] is None


def test_existing_pptx_rejects_traversal_and_missing(tmp_path) -> None:
    (tmp_path / "a").mkdir()
    target = tmp_path / "a" / "deck.pptx"
    target.write_bytes(b"x")
    assert existing_pptx(tmp_path, "a/deck.pptx", "deck.pptx") == target
    assert existing_pptx(tmp_path, "../a/deck.pptx", "deck.pptx") is None
    assert existing_pptx(tmp_path, "a\\deck.pptx", "deck.pptx") is None
    assert existing_pptx(tmp_path, "a/missing.pptx", "deck.pptx") is None


# --- Bindings, readback slices, coverage -----------------------------------------


def test_source_block_bindings_keeps_text_in_reading_order_and_drops_blank() -> None:
    bindings = report_lib.source_block_bindings(_manifest(), _outline())
    assert bindings == (
        (("source-slide:1:text:1", "Deck title\nsubtitle line"),),
        (("source-slide:2:text:1", "Second page body"),),
    )


def test_truncate_for_readback_sorts_units_and_limits_both_sides() -> None:
    manifest, outline = report_lib.truncate_for_readback(_manifest(), _outline(), 1)
    assert [unit["sourceUnitId"] for unit in manifest["units"]] == ["source-slide:1"]
    assert [slide["sourceUnitId"] for slide in outline["slides"]] == ["source-slide:1"]
    full, _ = report_lib.truncate_for_readback(_manifest(), _outline(), None)
    assert len(full["units"]) == 2


def test_stamp_assertions_passes_on_a_byte_identical_stamp(monkeypatch) -> None:
    import services.source_content.text_stamping as text_stamping

    monkeypatch.setattr(text_stamping, "stamp_source_block_ids", lambda html, bindings: html)
    rows = report_lib.stamp_assertions(["<html>a</html>"], [(("b1", "a"),)])
    assert rows == [{"slideIndex": 0, "bindings": 1, "passed": True, "rebound": False, "error": None}]


def test_stamp_assertions_reports_a_coarser_rebind_as_passed_not_failed(monkeypatch) -> None:
    import services.source_content.text_stamping as text_stamping

    monkeypatch.setattr(text_stamping, "stamp_source_block_ids", lambda html, bindings: html + "<!-- rebound -->")
    rows = report_lib.stamp_assertions(["<html>a</html>"], [(("b1", "a"),)])
    assert rows == [{"slideIndex": 0, "bindings": 1, "passed": True, "rebound": True, "error": None}]


def test_stamp_assertions_fails_only_when_the_stamper_raises(monkeypatch) -> None:
    import services.source_content.text_stamping as text_stamping
    from services.source_content.source_stamp_dom import BrowserSourceReadbackError

    def _raise(html, bindings):
        raise BrowserSourceReadbackError("source_content_browser_readback_failed", detail="no owner")

    monkeypatch.setattr(text_stamping, "stamp_source_block_ids", _raise)
    (row,) = report_lib.stamp_assertions(["<html>a</html>"], [(("b1", "a"),)])
    assert row["passed"] is False and row["rebound"] is False
    assert row["error"] == "source_content_browser_readback_failed: no owner"


def test_enrichment_coverage_uses_parallel_run_styles() -> None:
    coverage = report_lib.enrichment_coverage(_manifest())
    assert coverage["blocks"] == 4 and coverage["blocksWithGeometry"] == 3
    # "\n" break runs are not counted; sizePt comes from runStyles[j] aligned with runs[j]
    assert coverage["textRuns"] == 3 and coverage["textRunsWithSizePt"] == 2
    assert coverage["textRunSizeShare"] == pytest.approx(2 / 3, abs=1e-4)
    assert coverage["textBlocks"] == 3
    assert coverage["textBlocksWithDominantSizePt"] == 1
    assert coverage["textBlocksWithPlaceholderType"] == 1
    assert coverage["slideSizePresent"] is True
    assert coverage["slideSizeLocation"] == "manifest.slideSize"
    assert coverage["sourcePageEmu"] == [12192000, 6858000]


def test_enrichment_coverage_on_legacy_manifest_without_styles() -> None:
    manifest = _manifest()
    del manifest["slideSize"]
    for unit in manifest["units"]:
        for block in unit["blocks"]:
            for paragraph in block["payload"].get("paragraphs") or ():
                paragraph.pop("runStyles", None)
    coverage = report_lib.enrichment_coverage(manifest)
    assert coverage["textRuns"] == 3 and coverage["textRunsWithSizePt"] == 0
    assert coverage["slideSizePresent"] is False and coverage["sourcePageEmu"] is None


def test_find_slide_size_prefers_manifest_then_unit() -> None:
    assert report_lib.find_slide_size({"slideSize": {"cx": 10, "cy": 5}}) == ("manifest.slideSize", (10, 5))
    unit_level = {"units": [{"pageSize": [9144000, 6858000]}]}
    assert report_lib.find_slide_size(unit_level) == ("unit.pageSize", (9144000, 6858000))
    assert report_lib.find_slide_size({"slideSize": {"cx": "bad", "cy": 1}}) == (None, None)


# --- Aggregation and markdown ----------------------------------------------------


def _rows() -> list[dict]:
    params = {"chromeLevel": 0, "typeScale": 1.0, "sourceFactor": 1.0, "pageScale": 1.0}
    return [
        {"slideIndex": 0, "archetype": "text", "rung": 0, "renders": 1, "fitMs": 800.0, "blockingIssues": [],
         "parameters": params},
        {"slideIndex": 1, "archetype": "list", "rung": 2, "renders": 3, "fitMs": 2400.0,
         "blockingIssues": ["overflow: body"], "parameters": {}},
        {"slideIndex": 2, "archetype": "table", "rung": 5, "renders": 2, "fitMs": 1500.0, "blockingIssues": [],
         "parameters": {}},
        {"slideIndex": 3, "archetype": "mixed", "rung": None, "renders": None, "fitMs": 50.0, "blockingIssues": [],
         "error": "RuntimeError: boom", "parameters": {}},
    ]


def test_percentile_nearest_rank() -> None:
    assert report_lib.percentile([], 0.5) is None
    assert report_lib.percentile([1, 2, 3, 4], 0.5) == 2.0
    assert report_lib.percentile([1, 2, 3, 4], 0.9) == 4.0
    assert report_lib.percentile([7], 0.9) == 7.0


def test_aggregate_slides_histogram_percentiles_and_lists() -> None:
    aggregate = report_lib.aggregate_slides(_rows())
    assert aggregate["slides"] == 4
    assert aggregate["rungHistogram"] == {"0": 1, "2": 1, "5": 1, "error": 1}
    assert aggregate["rendersTotal"] == 6
    assert aggregate["rendersP50"] == 2.0 and aggregate["rendersP90"] == 3.0
    assert aggregate["fitMsP50"] == 800.0 and aggregate["fitMsP90"] == 2400.0
    assert aggregate["blockingSlides"] == [1] and aggregate["errorSlides"] == [3]
    assert "stampingRebound" not in aggregate
    dry = report_lib.aggregate_slides([{"slideIndex": 0, "rung": None, "fitMs": None}])
    assert dry["rungHistogram"] == {"unfitted": 1} and dry["fitMsP50"] is None


def test_aggregate_slides_counts_rebound_stamps_when_given() -> None:
    stamps = [{"passed": True, "rebound": False}, {"passed": True, "rebound": True}, {"passed": False}]
    aggregate = report_lib.aggregate_slides(_rows(), stamps)
    assert aggregate["stampingRebound"] == 1


def test_acceptance_checks() -> None:
    report = {
        "aggregate": {"slides": 2, "blockingSlides": [], "errorSlides": []},
        "readback": {"stamping": [{"passed": True}, {"passed": True}], "deck": {"passed": True}},
        "timings": {"totalMs": 120000.0},
        "providerCalls": 0,
    }
    assert report_lib.acceptance(report)["passed"] is True
    report["timings"]["totalMs"] = 400000.0
    verdict = report_lib.acceptance(report)
    assert verdict["passed"] is False and verdict["checks"]["underFiveMinutes"] is False


def test_acceptance_stamping_passed_ignores_a_rebound() -> None:
    report = {
        "aggregate": {"slides": 1, "blockingSlides": [], "errorSlides": []},
        "readback": {"stamping": [{"passed": True, "rebound": True}], "deck": {"passed": True}},
        "timings": {"totalMs": 1000.0},
        "providerCalls": 0,
    }
    verdict = report_lib.acceptance(report)
    assert verdict["checks"]["stampingPassed"] is True and verdict["passed"] is True


def test_render_markdown_contains_required_sections() -> None:
    rows = _rows()
    report = {
        "source": {"label": "deck-x", "mode": "database", "manifestId": "scm_x", "referenceSnapshotId": "snap",
                   "pptxPath": None, "typographySource": "stored", "pageCount": 4},
        "timings": {"extractMs": 10.0, "classifyMs": 5.0, "fitTotalMs": 4750.0, "readbackMs": 3.0, "sheetsMs": 2.0,
                    "totalMs": 4800.0},
        "aggregate": report_lib.aggregate_slides(rows),
        "slides": rows,
        "readback": {
            "stamping": [{"slideIndex": 0, "passed": True}, {"slideIndex": 1, "passed": False, "error": "x"}],
            "deck": {"passed": False, "checkedBlockCount": 9, "missingBlockIds": ["b1"], "orderViolations": 0,
                     "code": "c", "detail": "d"},
        },
        "enrichment": report_lib.enrichment_coverage(_manifest()),
        "providerCalls": 0,
        "notes": ["tokens: neutral palette"],
    }
    markdown = render_markdown(report)
    for heading in ("## Wall time", "## Rung histogram", "## Slides", "## Readback assertions",
                    "## Enrichment coverage", "## Slides with blocking issues", "## Acceptance", "## Notes"):
        assert heading in markdown
    assert "| 2 | list | 2 |" in markdown and "overflow: body" in markdown
    assert "ERROR: RuntimeError: boom" in markdown
    assert "1/2 slides (failing: 2 [x])" in markdown
    assert "- slide 2" in markdown and "**overall: FAIL**" in markdown
    assert "Text runs with `sizePt`: 2/3 (66.7%)" in markdown
    assert json.dumps(report["aggregate"])  # serializable


def test_render_markdown_dry_run_skips_acceptance() -> None:
    markdown = render_markdown({"dryRun": True, "slides": [], "aggregate": {}, "source": {}})
    assert "not evaluated (dry run" in markdown


# --- Inputs helpers ------------------------------------------------------------------


def test_sorted_units_and_deck_title() -> None:
    units = sorted_units(_manifest())
    assert [unit["sourceUnitId"] for unit in units] == ["source-slide:1", "source-slide:2"]
    assert deck_title_from_units(units) == "Deck title"
    assert deck_title_from_units([]) == ""


def test_blend_hex_and_neutral_tokens_are_valid_colors() -> None:
    assert blend_hex("#000000", "#FFFFFF", 0.5) == "#808080"
    assert blend_hex("#2563EB", "#FFFFFF", 0.0) == "#2563EB"
    assert blend_hex("#2563EB", "#FFFFFF", 1.0) == "#FFFFFF"
    from services.composer.plan import ComposerTokens

    tokens = ComposerTokens(**NEUTRAL_TOKENS, deck_title="t")
    assert tokens.accent == "#2563EB"


# --- Contact sheets -------------------------------------------------------------------


def test_thumbnail_size_at_sixty_percent() -> None:
    assert thumbnail_size(0.6) == (768, 432)
    with pytest.raises(ValueError):
        thumbnail_size(0)


def test_sheet_layout_four_columns_three_rows_and_remainder() -> None:
    sheets = sheet_layout(14, columns=4, scale=0.6, rows_per_sheet=3, gutter=16, label_height=28)
    assert len(sheets) == 2
    first, second = sheets
    assert len(first.cells) == 12 and len(second.cells) == 2
    assert first.width == 16 + 4 * (768 + 16) == second.width
    assert first.height == 16 + 3 * (432 + 28 + 16)
    assert second.height == 16 + 1 * (432 + 28 + 16)
    cell = first.cells[5]  # row 1, column 1
    assert (cell.slide_index, cell.x, cell.y) == (5, 16 + 1 * (768 + 16), 16 + 1 * (432 + 28 + 16))
    assert cell.label_y == cell.y + 432
    assert [cell.slide_index for cell in second.cells] == [12, 13]


def test_sheet_layout_edge_cases() -> None:
    assert sheet_layout(0) == []
    with pytest.raises(ValueError):
        sheet_layout(-1)
    with pytest.raises(ValueError):
        sheet_layout(3, columns=0)
    assert sheet_file_name(0, 2) == "sheet-01.png"
    assert sheet_file_name(9, 120) == "sheet-010.png"
