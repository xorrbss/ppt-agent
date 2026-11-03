"""A header candidate is judged by its header, not by its representative body.

The failed live deck of 2026-11-03: both candidates' representative page lost
only on body checks (``asset_icon_omitted``, ``source_text_paint_occluded``),
and the whole deck failed with ``svg_common_header_representative_pages_ineligible``.
"""

import pytest

from services import svg_common_header_design_plan as policy
from services import svg_common_header_generation as generation
from services.svg_common_header_design_calls import SELECTION_RULES
from services.generation_core.drivers.svg.terminal import FALLBACK_OUTCOME, FLAG_ONLY_OUTCOME, PLACEHOLDER_OUTCOME

from test_svg_header_capacity_policy import _inputs


def _page(*, failures=(), header_passed=True, terminal=None, body="<svg/>", validation_error=None):
    quality = {"passed": not failures, "failures": list(failures)}
    if header_passed is not None:
        quality["common_header"] = {"version": "svg-common-header-render/v2", "passed": header_passed}
    result = {"quality": quality, "attempts": [{"receipt": {"validation_error": validation_error}}]}
    if body is not None:
        result["author_body_svg"] = body
    if terminal is not None:
        result["terminal"] = {"index": 1, "reason": "author:attempts_exhausted", "remakeAttempted": False,
                              "remakeOutcome": terminal}
    return result


BODY_ONLY = ({"kind": "asset_icon_omitted"}, {"kind": "source_text_paint_occluded"})


@pytest.mark.parametrize("terminal", [None, FLAG_ONLY_OUTCOME, FALLBACK_OUTCOME])
def test_body_only_failures_leave_the_header_proven(terminal):
    assert generation.header_scoped_eligible(_page(failures=BODY_ONLY, terminal=terminal))


@pytest.mark.parametrize("page", [
    _page(failures=({"kind": "svg_common_header_body_intrusion"},), header_passed=False),
    _page(failures=({"kind": "svg_common_header_title_overflow"},)),
    _page(failures=BODY_ONLY, header_passed=False),
    _page(failures=BODY_ONLY, header_passed=None),
    _page(failures=BODY_ONLY, body=None),
    _page(failures=BODY_ONLY, validation_error="svg_contract_unsupported_attribute"),
    _page(failures=BODY_ONLY, terminal=PLACEHOLDER_OUTCOME),
    _page(failures=({"kind": None},)),
    {**_page(failures=BODY_ONLY), "inflight": {"call": "running"}},
], ids=["intrusion", "title-overflow", "header-failed", "header-unmeasured", "no-body", "invalid-drawing",
        "placeholder", "malformed-failure", "inflight"])
def test_a_header_defect_or_unpromotable_page_is_not_admitted(page):
    assert not generation.header_scoped_eligible(page)


def test_header_scoped_admission_only_when_no_candidate_passed_outright():
    pages = {"a": {"eligible": True, "header_eligible": True}, "b": {"eligible": False, "header_eligible": True}}
    generation.admit_header_scoped_candidates(pages)
    assert pages["b"]["eligible"] is False and "eligibility" not in pages["b"]

    pages = {"a": {"eligible": False, "header_eligible": True}, "b": {"eligible": False, "header_eligible": False}}
    generation.admit_header_scoped_candidates(pages)
    assert pages["a"] == {"eligible": True, "header_eligible": True, "eligibility": "header"}
    assert pages["b"]["eligible"] is False


def test_selection_rules_explain_body_checks():
    assert "body_checks" in SELECTION_RULES


def test_ineligible_representative_pages_reduce_the_header_instead_of_failing_the_deck():
    _, plan, _, _, _ = _inputs()
    document = {"steps": {"design-1": {"status": "succeeded"}}}
    assert policy.permits_header_fallback(plan, "svg_common_header_representative_pages_ineligible", document)
    document["steps"]["design-2"] = {"status": "unknown"}
    assert not policy.permits_header_fallback(plan, "svg_common_header_representative_pages_ineligible",
                                              document)
