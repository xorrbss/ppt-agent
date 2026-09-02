"""Stage 4 best-of-N: candidate enumeration, geometry metrics, and selection."""

import re

import pytest

from utils.authored_dom_quality import DomQualityIssue, DomQualityReport
from utils.authored_figure_variants import (
    CandidateMetrics,
    FigureOperation,
    OperationRefused,
    RenderedCandidate,
    apply_operations,
    candidate_score,
    figure_variants,
    find_figure,
    ink_ratio_penalty,
    measure_candidate,
    same_figure_document,
    select_candidate,
    set_density_class,
    set_emphasis,
)
from utils.authored_figure_variants.operations import apply_operation

FIGURE_HTML = (
    '<!doctype html><html><head><style>html,body{margin:0;width:1280px;height:720px}'
    '</style></head><body><main class="deck-slide">'
    '<h1 class="title" data-qid="t1">데이터 파이프라인</h1>'
    '<section class="ig ig-process n4" data-qid="ig.s1.root">'
    '<div class="ig-node is-key" data-qid="ig.s1.n1">'
    '<div class="ig-node-bg" data-qid="ig.s1.n1.bg"></div>'
    '<div class="ig-label" data-qid="ig.s1.n1.label">데이터 수집 단계</div></div>'
    '<div class="ig-node" data-qid="ig.s1.n2">'
    '<div class="ig-node-bg" data-qid="ig.s1.n2.bg"></div>'
    '<div class="ig-label" data-qid="ig.s1.n2.label">정제</div></div>'
    '<div class="ig-node" data-qid="ig.s1.n3">'
    '<div class="ig-node-bg" data-qid="ig.s1.n3.bg"></div>'
    '<div class="ig-label" data-qid="ig.s1.n3.label">분석</div></div>'
    '</section></main></body></html>'
)

QUIET_HTML = (
    '<!doctype html><html><head></head><body><main class="deck-slide">'
    '<h1 data-qid="t1">표지</h1></main></body></html>'
)


def _element(qid, x, y, width, height, *, text="", scroll=None, parent=""):
    scroll_width, scroll_height = scroll or (width, height)
    return {
        "qid": qid,
        "parent_qid": parent,
        "tag": "div",
        "text": text,
        "has_owned_text": bool(text),
        "bbox": {"x": x, "y": y, "width": width, "height": height},
        "client_width": width,
        "client_height": height,
        "scroll_width": scroll_width,
        "scroll_height": scroll_height,
    }


def _metrics(elements):
    return {"width": 1280, "height": 720, "elements": list(elements)}


def _strip_classes(html):
    return re.sub(r'\sclass="[^"]*"', "", html)


def test_find_figure_reads_the_density_class_and_the_emphasised_node():
    figure = find_figure(FIGURE_HTML)

    assert figure is not None
    assert figure.density_class == "n4"
    assert figure.node_count == 3
    assert figure.emphasis_index == 0
    assert find_figure(QUIET_HTML) is None


def test_density_and_emphasis_edits_change_classes_and_nothing_else():
    tighter = set_density_class(FIGURE_HTML, "n6")
    moved = set_emphasis(FIGURE_HTML, 2)

    assert 'class="ig ig-process n6"' in tighter
    assert find_figure(moved).emphasis_index == 2
    assert moved.count("is-key") == 1
    for variant in (tighter, moved):
        assert _strip_classes(variant) == _strip_classes(FIGURE_HTML)
        assert same_figure_document(variant, FIGURE_HTML)


def test_emphasis_can_be_cleared_and_an_unknown_node_is_refused():
    cleared = set_emphasis(FIGURE_HTML, None)

    assert "is-key" not in cleared
    assert find_figure(cleared).emphasis_index is None
    with pytest.raises(ValueError):
        set_emphasis(FIGURE_HTML, 9)


def test_candidates_are_class_only_variants_of_one_authored_document():
    candidates = figure_variants(FIGURE_HTML, max_candidates=3)

    assert [item.variant_id for item in candidates] == [
        "baseline",
        "density-tighter",
        "emphasis-off",
    ]
    assert candidates[0].html == FIGURE_HTML
    assert len({item.html for item in candidates}) == 3
    for candidate in candidates:
        # No re-authoring: identical elements, text and data-qid values.
        assert _strip_classes(candidate.html) == _strip_classes(FIGURE_HTML)


def test_candidate_count_is_clamped_and_a_quiet_page_renders_once():
    assert len(figure_variants(FIGURE_HTML, max_candidates=1)) == 1
    assert len(figure_variants(FIGURE_HTML, max_candidates=9)) == 4

    quiet = figure_variants(QUIET_HTML, max_candidates=3)
    assert len(quiet) == 1
    assert quiet[0].html == QUIET_HTML


def test_a_figure_already_at_the_tightest_density_drops_the_duplicate_step():
    tightest = set_density_class(FIGURE_HTML, "n6")

    ids = [item.variant_id for item in figure_variants(tightest, max_candidates=3)]

    assert ids == ["baseline", "emphasis-off", "density-looser"]


def test_metrics_report_overflow_overlap_balance_and_density():
    clean = measure_candidate(
        _metrics(
            [
                _element("a", 100, 100, 1080, 200, text="상단"),
                _element("b", 100, 420, 1080, 200, text="하단"),
            ]
        )
    )
    assert clean.overflow_px == 0.0
    assert clean.text_overlap_area == 0.0
    assert clean.whitespace_balance == pytest.approx(1.0)
    assert clean.ink_ratio == pytest.approx((1080 * 400) / (1280 * 720), abs=1e-4)

    defective = measure_candidate(
        _metrics(
            [
                _element("a", 100, 100, 200, 100, text="겹침", scroll=(320, 100)),
                _element("b", 200, 150, 200, 100, text="겹침"),
                _element("c", 1200, 100, 200, 100, text="바깥"),
            ]
        )
    )
    assert defective.overflow_px == 120.0
    assert defective.text_overlap_area == 100.0 * 50.0
    assert defective.off_canvas_px == pytest.approx(120.0)
    assert defective.whitespace_balance < clean.whitespace_balance


def test_nested_text_boxes_are_not_counted_as_an_overlap():
    nested = measure_candidate(
        _metrics(
            [
                _element("card", 100, 100, 400, 200, text="카드"),
                _element("label", 120, 120, 200, 60, text="라벨", parent="card"),
            ]
        )
    )

    assert nested.text_overlap_area == 0.0


def test_ink_ratio_penalty_is_zero_inside_the_band():
    assert ink_ratio_penalty(0.4) == 0.0
    assert ink_ratio_penalty(0.1) == pytest.approx(0.18)
    assert ink_ratio_penalty(0.8) == pytest.approx(0.18)


def _candidate(variant, *, overflow=0.0, overlap=0.0, balance=1.0, ink=0.4, report=None):
    return RenderedCandidate(
        variant=variant,
        metrics=CandidateMetrics(
            overflow_px=overflow,
            off_canvas_px=0.0,
            text_overlap_area=overlap,
            whitespace_balance=balance,
            ink_ratio=ink,
            measured_elements=3,
        ),
        report=report,
    )


def _report(issues=()):
    return DomQualityReport(
        reference_mode="style_reference",
        issues=tuple(issues),
        compact_dom=(),
        inspected_elements=3,
        telemetry_status="available",
    )


def test_selection_keeps_the_baseline_unless_a_variant_is_strictly_better():
    variants = figure_variants(FIGURE_HTML, max_candidates=3)
    clean = _report()

    tie = select_candidate(
        [
            _candidate(variants[0], report=clean),
            _candidate(variants[1], report=clean),
        ]
    )
    assert tie.chosen_index == 0
    assert tie.improved is False

    better = select_candidate(
        [
            _candidate(variants[0], overflow=40.0, report=clean),
            _candidate(variants[1], report=clean),
        ]
    )
    assert better.chosen_index == 1
    assert better.chosen.variant.variant_id == "density-tighter"
    assert candidate_score(better.candidates[1].metrics) < candidate_score(
        better.candidates[0].metrics
    )


def test_a_variant_that_regresses_the_dom_gate_is_never_chosen():
    variants = figure_variants(FIGURE_HTML, max_candidates=3)
    regression = _report(
        [DomQualityIssue(category="overlap", severity="high", detail="text overlap")]
    )

    selection = select_candidate(
        [
            _candidate(variants[0], overflow=200.0, report=_report()),
            _candidate(variants[1], report=regression),
        ]
    )

    assert selection.chosen_index == 0
    assert selection.eligible == (True, False)


def test_a_variant_without_a_dom_report_cannot_displace_the_baseline():
    variants = figure_variants(FIGURE_HTML, max_candidates=3)

    selection = select_candidate(
        [
            _candidate(variants[0], overflow=200.0, report=_report()),
            _candidate(variants[1], report=None),
        ]
    )

    assert selection.chosen_index == 0
    assert selection.to_dict()["variant_chosen"] == "baseline"


def test_closed_operations_apply_only_what_they_can_prove():
    density = apply_operation(FIGURE_HTML, FigureOperation(kind="set_density", density="n5"))
    assert 'class="ig ig-process n5"' in density

    moved = apply_operation(
        FIGURE_HTML, FigureOperation(kind="move_emphasis", node_index=1)
    )
    assert find_figure(moved).emphasis_index == 1

    shortened = apply_operation(
        FIGURE_HTML,
        FigureOperation(kind="shorten_label", node_index=0, text="데이터 수집"),
    )
    assert ">데이터 수집<" in shortened


@pytest.mark.parametrize(
    "operation",
    [
        FigureOperation(kind="set_density", density="n9"),
        FigureOperation(kind="move_emphasis", node_index=7),
        FigureOperation(kind="shorten_label", node_index=0, text="완전히 새로운 문구"),
        FigureOperation(kind="shorten_label", node_index=0, text="데이터 수집 단계"),
        FigureOperation(kind="shorten_label", node_index=9, text="정"),
    ],
)
def test_an_operation_that_cannot_be_proven_is_refused(operation):
    with pytest.raises(OperationRefused):
        apply_operation(FIGURE_HTML, operation)


def test_apply_operations_keeps_the_valid_ones_and_reports_the_refusals():
    html, applied, refused = apply_operations(
        FIGURE_HTML,
        [
            FigureOperation(kind="set_density", density="n5"),
            FigureOperation(kind="shorten_label", node_index=0, text="새 문구"),
        ],
    )

    assert [item.kind for item in applied] == ["set_density"]
    assert len(refused) == 1
    assert "shorten_label" in refused[0]
    assert 'class="ig ig-process n5"' in html


def test_a_quiet_page_refuses_every_figure_operation():
    html, applied, refused = apply_operations(
        QUIET_HTML, [FigureOperation(kind="set_density", density="n5")]
    )

    assert html == QUIET_HTML
    assert applied == ()
    assert refused and "no figure" in refused[0]
