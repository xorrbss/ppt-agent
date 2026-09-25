"""Real-Chrome regression: duplicate fit candidates cost no measurement budget.

The bounded local-fit search tries a fixed ladder of font and spacing factors.
Once a run's floor clamps several factors to the same 9pt target, or a factor
changes no spacing at all, their appended declarations are byte-identical.
Before this regression the duplicate was only recognised after the plan had
been written to the DOM and, for parent-space candidates, after the measured
parent plan had already spent ``_measurements``. Now the declaration set is
compared first, so a duplicate costs neither a DOM write nor a measurement.

Measured on this fixture pair before the change (same Chrome, same policy):
card-growth slide ``measurements`` 26 with 44 duplicates; exhausted slide
``measurements`` 17 with 39 duplicates. Candidate ids, ordering and the
committed outcome are unchanged by the dedupe.
"""

from __future__ import annotations

import asyncio

import pytest

from services.authored_local_fit_policy import html_digest, local_fit_policy
from utils.slide_capture import find_chrome, render_html_to_png_with_metrics

#: One 9.75pt body run inside a fixed-height card; the title stays out of the defect.
BODY_PX, FLOOR_PT, PT_PER_PX = 13, 9.0, 0.75
FLOOR_FACTOR = FLOOR_PT / (BODY_PX * PT_PER_PX)
#: ``[data-qid]`` nodes with an explicit px height; the boundary scan measures each once.
EXPLICIT_HEIGHT_NODES = 2


def _slide(lines: int) -> str:
    body = "<br>".join(
        f"Line {index} of body copy that runs long enough to wrap in the card"
        for index in range(lines)
    )
    return f"""<!doctype html><html><head><meta charset="utf-8"><style>
*{{box-sizing:border-box}}html,body{{margin:0;width:1280px;height:720px;overflow:hidden;font-family:Arial,sans-serif;background:#fff}}
.card{{position:absolute;left:100px;top:100px;width:420px;height:150px;padding:14px 16px;
  background:#f5f7fa;border:1px solid #d0d5dd;border-radius:10px}}
.title{{font-size:18px;line-height:24px;font-weight:700;margin-bottom:8px}}
.body{{font-size:{BODY_PX}px;line-height:18px}}
.other{{position:absolute;left:600px;top:100px;width:300px;height:80px;padding:12px;background:#123;color:#fff;font-size:16px;line-height:22px}}
</style></head><body>
<aside class="other" data-qid="other" data-semantic-role="body">Unrelated box</aside>
<section class="card" data-qid="card" data-semantic-role="decoration">
 <div class="title" data-qid="title" data-semantic-role="heading">Card title</div>
 <div class="body" data-qid="body" data-semantic-role="body">{body}</div>
</section></body></html>"""


def _fit(html: str) -> dict:
    revision = html_digest(html)
    policy = local_fit_policy(author_revision=revision, original_html_digest=revision, slide_index=0)
    capture = asyncio.run(
        render_html_to_png_with_metrics(html, timeout=60, fit=True, fit_policy=policy, fresh=True)
    )
    return dict(capture.fit_report or {})


def _trace(report: dict) -> list[tuple]:
    return [
        (
            item["candidate_id"], item["font_factor"], item["spacing_factor"],
            item["uses_parent_space"], item["outcome"], (item.get("rejection") or {}).get("code"),
        )
        for item in report["candidates"]
    ]


def _is_duplicate(item: dict) -> bool:
    return item["outcome"] == "removed:duplicate_declarations"


def _measured(item: dict) -> bool:
    return item["outcome"] in {"passed", "committed"} or str(item["outcome"]).startswith("rejected:")


def _assert_common(report: dict) -> None:
    candidates, telemetry = report["candidates"], report["telemetry"]
    duplicates = [item for item in candidates if _is_duplicate(item)]
    assert telemetry["candidate_total"] == len(candidates)
    assert telemetry["duplicates"] == len(duplicates) > 0
    assert telemetry["evaluated"] == sum(1 for item in candidates if _measured(item))
    seen_space_bases: set[tuple] = set()
    for item in candidates:
        base = (tuple(item["actual_font_factors"]), item["spacing_factor"])
        if _is_duplicate(item):
            assert item["rejection"] == {"code": "reject.duplicate_declarations", "stage": "deduplicate"}
            # A duplicate of an earlier parent-space attempt on the same base
            # plan is decided from the declarations alone: nothing is measured.
            if not item["uses_parent_space"] or base in seen_space_bases:
                assert "space" not in item and "morph" not in item, item["candidate_id"]
        if item["uses_parent_space"] and "space" in item:
            seen_space_bases.add(base)
    # Ids stay ``<root_qid>:<tier>:<n>`` with ``n`` following trial order.
    assert [item["candidate_id"] for item in candidates] == [
        f"card:{item['phase']}:{index}" for index, item in enumerate(candidates)
    ]


@pytest.mark.skipif(find_chrome() is None, reason="headless Chrome is required")
def test_duplicate_space_candidates_no_longer_spend_parent_plan_measurements() -> None:
    html = _slide(6)
    report = _fit(html)
    _assert_common(report)
    assert report["unresolved_reasons"] == [] and report["status"] == "spacing_adjusted"
    assert [operation["kind"] for operation in report["operations"]] == ["parent_reflow"]

    candidates, telemetry = report["candidates"], report["telemetry"]
    committed = [item for item in candidates if item["outcome"] == "committed"]
    assert len(committed) == 1 and committed[0]["uses_parent_space"]
    # The card is a boundary member, so every candidate that reaches the parent
    # plan measures it once (``space`` records that). Duplicates decided from the
    # declarations contribute nothing; before the change they each cost one.
    parent_plan_attempts = [item for item in candidates if "space" in item]
    expected = (
        telemetry["evaluated"] + len(committed) + EXPLICIT_HEIGHT_NODES + len(parent_plan_attempts)
    )
    assert telemetry["measurements"] == expected == 12, telemetry
    assert telemetry["duplicates"] >= 40
    # The same font-only spacing declaration set recurs across the whole
    # extended ladder: a parent-space retry of an already measured base plan
    # is a duplicate even when the first attempt was rejected by the parent plan.
    assert any(
        item["uses_parent_space"] and item["font_factor"] < 1 and _is_duplicate(item)
        for item in candidates
    )

    again = _fit(html)
    assert _trace(again) == _trace(report)
    assert {k: v for k, v in again["telemetry"].items() if k != "fit_search_ms"} == {
        k: v for k, v in telemetry.items() if k != "fit_search_ms"
    }


@pytest.mark.skipif(find_chrome() is None, reason="headless Chrome is required")
def test_floor_clamped_font_factors_are_deduplicated_without_changing_the_search_outcome() -> None:
    html = _slide(10)
    report = _fit(html)
    _assert_common(report)
    candidates, telemetry = report["candidates"], report["telemetry"]
    assert report["unresolved_reasons"] == ["candidates_exhausted"]
    (termination,) = report["search_termination"]
    assert termination["code"] == "search.candidates_exhausted"
    assert termination["evaluated_candidates"] == telemetry["evaluated"] == 15
    assert termination["unevaluated_candidates"] == 0
    assert termination["rejection_counts"]["reject.duplicate_declarations"] == telemetry["duplicates"] == 39

    # Every extended factor below the floor factor clamps to the same 9pt target.
    for item in candidates:
        if item["font_factor"] < FLOOR_FACTOR - 1e-9:
            assert item["actual_font_factors"] == [pytest.approx(FLOOR_FACTOR)], item["candidate_id"]
    measured = [item for item in candidates if _measured(item)]
    distinct = {
        (tuple(item["actual_font_factors"]), item["spacing_factor"], item["uses_parent_space"])
        for item in measured
    }
    assert len(distinct) == len(measured), "a measured candidate repeated another's plan"
    # No boundary member and no commit: measurements are the boundary scan plus
    # one hard-gate measurement per evaluated candidate, nothing for duplicates.
    assert telemetry["measurements"] == telemetry["evaluated"] + EXPLICIT_HEIGHT_NODES == 17

    again = _fit(html)
    assert _trace(again) == _trace(report)
    assert again["search_termination"] == report["search_termination"]
