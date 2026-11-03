"""An over-bound evidence string is normalised; it never discards the review's verdicts.

Providers without strict structured output ignore the schema's ``maxLength``.
Before this contract a single long ``visibleEvidence`` rejected the whole
response (``evidence_bounds``) and every page ended ``design:unreviewed``.
Semantic checks (verdict/severity/repairScope, axis keys, index coverage,
blank or non-string evidence) still reject.
"""
import asyncio
from copy import deepcopy

import pytest

from services import svg_authoring_profile as profiles
from services.generation_core.quality import vision_advisory as vision
from services.generation_core.quality import vision_advisory_prompt as prompt

LONG_WORDS = " ".join(f"word{index}" for index in range(30))  # 30 words, over the word bound
LONG_CHARS = " ".join(["abcdefghij"] * 13) + " " + "k" * 40  # 14 words, 183 chars: over the character bound only
UNBROKEN = "w" * 400


def _axis(verdict="passed", severity="none", scope="none", evidence="Clear and readable."):
    return dict(verdict=verdict, severity=severity, repairScope=scope, visibleEvidence=evidence)


def _axes(names, evidence="Clear and readable."):
    return {name: _axis(evidence=evidence) for name in names}


def _within_bounds(text, max_words=14):
    return (0 < len(text) <= 160 and len(text.split()) <= max_words)


# --- the normalisation rule itself -------------------------------------------------

def test_in_bounds_text_is_returned_untouched_and_not_counted():
    notes = []
    odd = "  keeps   its  spacing  "
    assert vision._bounded_text(odd, "visibleEvidence", notes) == odd
    assert vision._bounded_text(" ".join(["w"] * 14), "visibleEvidence", notes) == " ".join(["w"] * 14)
    assert vision._bounded_text("x" * 160, "visibleEvidence", notes) == "x" * 160
    assert notes == []


def test_over_word_bound_keeps_first_fourteen_words():
    notes = []
    result = vision._bounded_text(LONG_WORDS, "visibleEvidence", notes)
    assert result == " ".join(f"word{index}" for index in range(14))
    assert notes == ["visibleEvidence"]


def test_over_character_bound_cuts_on_a_word_boundary():
    notes = []
    text = LONG_CHARS
    assert len(text) > 160 and len(text.split()) == 14
    result = vision._bounded_text(text, "cleanEvidence", notes)
    assert result == " ".join(["abcdefghij"] * 13)
    assert _within_bounds(result) and text.startswith(result)
    assert notes == ["cleanEvidence"]


def test_single_unbroken_token_is_hard_cut_to_the_character_bound():
    notes = []
    assert vision._bounded_text(UNBROKEN, "visibleEvidence", notes) == "w" * 160
    assert notes == ["visibleEvidence"]


def test_source_evidence_keeps_only_its_character_bound_and_may_be_blank():
    notes = []
    many_words = " ".join(["w"] * 40)  # 79 chars, 40 words: legal for sourceEvidence
    assert vision._bounded_text(many_words, "sourceEvidence", notes, max_words=None, allow_blank=True) == many_words
    assert vision._bounded_text("", "sourceEvidence", notes, max_words=None, allow_blank=True) == ""
    assert notes == []
    long_source = " ".join(["source"] * 40)  # 279 chars
    cut = vision._bounded_text(long_source, "sourceEvidence", notes, max_words=None, allow_blank=True)
    assert len(cut) <= 160 and cut == " ".join(["source"] * 23) and notes == ["sourceEvidence"]


@pytest.mark.parametrize("value", [None, 7, ["text"], {"a": 1}, "", "   \n\t"])
def test_missing_or_blank_required_evidence_is_not_normalisable(value):
    notes = []
    assert vision._bounded_text(value, "visibleEvidence", notes) is None
    assert notes == []


# --- axes: verdicts survive, semantics still reject ---------------------------------

@pytest.mark.parametrize("evidence", [LONG_WORDS, LONG_CHARS, UNBROKEN])
def test_long_visible_evidence_keeps_every_axis_verdict(evidence):
    names = prompt.DESIGN_AXES
    raw = _axes(names)
    raw[names[0]] = _axis("failed", "major", "design_rewrite", evidence)
    raw[names[1]] = _axis("advisory", "minor", "none", evidence)
    notes, reasons = [], []
    axes = vision._parse_design_axes(raw, names, rejection_reasons=reasons, evidence_notes=notes)
    assert axes is not None and reasons == []
    assert (axes[names[0]]["verdict"], axes[names[0]]["severity"], axes[names[0]]["repairScope"]) == (
        "failed", "major", "design_rewrite")
    assert (axes[names[1]]["verdict"], axes[names[1]]["severity"]) == ("advisory", "minor")
    assert axes[names[2]]["visibleEvidence"] == "Clear and readable."
    assert _within_bounds(axes[names[0]]["visibleEvidence"]) and _within_bounds(axes[names[1]]["visibleEvidence"])
    assert notes == ["visibleEvidence", "visibleEvidence"]


@pytest.mark.parametrize("evidence", [None, 5, "", "  "])
def test_blank_or_non_string_axis_evidence_still_rejects(evidence):
    names = prompt.DESIGN_AXES
    raw = _axes(names)
    raw[names[0]]["visibleEvidence"] = evidence
    reasons = []
    assert vision._parse_design_axes(raw, names, rejection_reasons=reasons) is None
    assert reasons == ["evidence_bounds"]


def test_missing_evidence_key_still_rejects():
    names = prompt.DESIGN_AXES
    raw = _axes(names)
    del raw[names[0]]["visibleEvidence"]
    reasons = []
    assert vision._parse_design_axes(raw, names, rejection_reasons=reasons) is None
    assert reasons == ["evidence_bounds"]


@pytest.mark.parametrize("mutation, reason", [
    (lambda axes, n: axes[n[0]].update(verdict="failed", severity="minor", repairScope="local_repair"), "axis_semantics"),
    (lambda axes, n: axes[n[0]].update(verdict="failed", severity="major", repairScope="none"), "axis_semantics"),
    (lambda axes, n: axes[n[0]].update(verdict="passed", severity="none", repairScope="local_repair"), "axis_semantics"),
    (lambda axes, n: axes[n[0]].update(verdict="bogus"), "axis_semantics"),
    (lambda axes, n: axes.pop(n[0]), "axis_keys"),
])
def test_semantic_violations_still_reject_even_with_long_evidence(mutation, reason):
    names = prompt.DESIGN_AXES
    raw = _axes(names, evidence=LONG_WORDS)
    mutation(raw, names)
    reasons = []
    assert vision._parse_design_axes(raw, names, rejection_reasons=reasons) is None
    assert reasons == [reason]


# --- local and full reviews ---------------------------------------------------------

def _local_context():
    return dict(policy={"version": 2}, reviewScope="local", priorCandidates={}, deckComplete=False)


def _local_payload(evidence):
    axes = _axes(prompt.DESIGN_LOCAL_AXES, evidence="Clear and readable.")
    axes[prompt.DESIGN_LOCAL_AXES[0]] = _axis("failed", "major", "local_repair", evidence)
    return dict(slides=[dict(index=1, evaluated=True, issues=[], axes=axes, designTargets=[
        dict(axis=prompt.DESIGN_LOCAL_AXES[0], evidenceRegion=evidence, visibleEvidence=evidence,
             sourceEvidence=evidence, improvementGoal=evidence)])])


def test_local_review_with_long_axis_and_target_text_keeps_verdicts_and_targets():
    notes = []
    review = vision._parse_baseline_review(_local_payload(LONG_WORDS), slide_count=1,
        context=_local_context(), evidence_notes=notes)
    assert review is not None
    page = review["pages"][0]
    first = prompt.DESIGN_LOCAL_AXES[0]
    assert page["axes"][first]["verdict"] == "failed" and page["axes"][first]["repairScope"] == "local_repair"
    (target,) = page["designTargets"]
    assert target["axis"] == first
    for key in ("evidenceRegion", "visibleEvidence", "sourceEvidence", "improvementGoal"):
        assert target[key] == " ".join(f"word{index}" for index in range(14))
    assert sorted(set(notes)) == ["evidenceRegion", "improvementGoal", "sourceEvidence", "visibleEvidence"]
    assert len(notes) == 5  # four target fields plus the axis


def test_blank_target_text_still_rejects_the_review():
    payload = _local_payload("Fine evidence.")
    payload["slides"][0]["designTargets"][0]["improvementGoal"] = " "
    reasons = []
    assert vision._parse_baseline_review(payload, slide_count=1, context=_local_context(),
        rejection_reasons=reasons) is None
    assert reasons == ["design_target_fields"]


def _full_context(prior=False):
    return dict(policy={"version": 2}, reviewScope="full", deckComplete=True,
        priorCandidates={0: {}} if prior else {}, blindVariantLabel="A",
        composition={"pages": [{}], "groups": [{"pages": [0]}]})


def _full_payload(evidence, *, source=""):
    axes = _axes(prompt.DESIGN_AXES)
    axes[prompt.DESIGN_AXES[0]] = _axis("failed", "critical", "design_rewrite", evidence)
    return dict(slides=[dict(index=1, evaluated=True, issues=[], axes=axes, designTargets=[])],
        deckFlow=dict(evaluated=True, visibleEvidence=evidence, repetitionGroups=[
            dict(pages=[1], intentional=False, sourceEvidence=source, visibleEvidence=evidence)]))


def test_full_review_normalises_flow_and_group_evidence_without_losing_coverage():
    source = " ".join(["source"] * 40)
    notes = []
    review = vision._parse_baseline_review(_full_payload(LONG_WORDS, source=source), slide_count=1,
        context=_full_context(), evidence_notes=notes)
    assert review is not None
    first = prompt.DESIGN_AXES[0]
    assert review["pages"][0]["axes"][first]["verdict"] == "failed"
    flow = review["deckFlow"]
    assert flow["evaluated"] is True and _within_bounds(flow["visibleEvidence"])
    (group,) = flow["repetitionGroups"]
    assert group["pages"] == [0] and _within_bounds(group["visibleEvidence"])
    assert 0 < len(group["sourceEvidence"]) <= 160 and source.startswith(group["sourceEvidence"])
    assert sorted(set(notes)) == ["sourceEvidence", "visibleEvidence"]


def test_full_review_prior_comparison_and_axis_changes_are_normalised():
    payload = _full_payload("Clear and readable.")
    payload["pairComparisons"] = [dict(index=1, evaluated=True, alternativeAxes=_axes(prompt.DESIGN_AXES, LONG_WORDS),
        visibleEvidence=LONG_WORDS, axisChanges={name: dict(preferred="A", visibleEvidence=LONG_WORDS)
                                                   for name in prompt.DESIGN_AXES})]
    notes = []
    review = vision._parse_baseline_review(payload, slide_count=1, context=_full_context(prior=True),
        evidence_notes=notes)
    assert review is not None
    (comparison,) = review["candidateComparisons"]
    assert _within_bounds(comparison["visibleEvidence"])
    assert all(_within_bounds(axis["visibleEvidence"]) for axis in comparison["priorAxes"].values())
    assert all(change["change"] == "improved" and _within_bounds(change["visibleEvidence"])
               for change in comparison["axisChanges"].values())
    assert len(notes) == 1 + 2 * len(prompt.DESIGN_AXES)


def test_full_review_semantic_violation_rejects_despite_long_evidence():
    payload = _full_payload(LONG_WORDS)
    payload["slides"][0]["axes"][prompt.DESIGN_AXES[0]]["repairScope"] = "none"  # failed without a scope
    reasons = []
    assert vision._parse_baseline_review(payload, slide_count=1, context=_full_context(),
        rejection_reasons=reasons) is None
    assert reasons == ["axis_semantics"]
    payload = _full_payload(LONG_WORDS)
    payload["slides"][0]["index"] = 2
    reasons = []
    assert vision._parse_baseline_review(payload, slide_count=1, context=_full_context(),
        rejection_reasons=reasons) is None
    assert reasons == ["index_coverage"]


# --- global reviews -----------------------------------------------------------------

def _legacy_global_context():
    return dict(policy={"version": 2}, reviewScope="global", priorCandidates={},
        composition={"groups": [], "pages": [{}, {}, {}]})


def _legacy_global_payload(evidence, source):
    return dict(findings=[dict(id=1, intentional=False, sourceEvidence=source,
        axis=_axis("failed", "major", "design_rewrite", evidence))],
        variantReview=dict(cleanPages=[3], unreviewedPages=[], cleanEvidence=evidence,
            groups=[dict(findingId=1, pages=[1, 2], retainedPages=[1])]),
        designTargets=[])


def test_global_finding_source_evidence_and_clean_evidence_are_normalised():
    source = " ".join(["source"] * 40)
    notes = []
    review = vision._parse_baseline_review(_legacy_global_payload(LONG_WORDS, source), slide_count=3,
        context=_legacy_global_context(), evidence_notes=notes)
    assert review is not None
    by_index = {page["index"]: page["axes"]["repetition_deck_flow"] for page in review["pages"]}
    assert by_index[1]["verdict"] == "failed" and by_index[1]["repairScope"] == "design_rewrite"
    assert by_index[0]["verdict"] == "passed" and by_index[2]["verdict"] == "passed"
    assert all(_within_bounds(axis["visibleEvidence"]) for axis in by_index.values())
    flow = review["deckFlow"]
    assert _within_bounds(flow["visibleEvidence"])
    (group,) = flow["repetitionGroups"]
    assert 0 < len(group["sourceEvidence"]) <= 160 and source.startswith(group["sourceEvidence"])
    assert sorted(set(notes)) == ["cleanEvidence", "sourceEvidence", "visibleEvidence"]


def test_global_semantic_and_blank_evidence_failures_still_reject():
    context = _legacy_global_context()
    blank_clean = _legacy_global_payload("Clear and readable.", "")
    blank_clean["variantReview"]["cleanEvidence"] = "  "
    reasons = []
    assert vision._parse_baseline_review(blank_clean, slide_count=3, context=context,
        rejection_reasons=reasons) is None
    assert reasons == ["global_review_fields"]
    non_string_source = _legacy_global_payload("Clear and readable.", 12)
    reasons = []
    assert vision._parse_baseline_review(non_string_source, slide_count=3, context=context,
        rejection_reasons=reasons) is None
    assert reasons == ["global_finding_fields"]
    # failed verdict but every page of the group retained: a retained-coverage violation.
    over_retained = _legacy_global_payload(LONG_WORDS, "")
    over_retained["variantReview"]["groups"][0]["retainedPages"] = [1, 2]
    reasons = []
    assert vision._parse_baseline_review(over_retained, slide_count=3, context=context,
        rejection_reasons=reasons) is None
    assert reasons == ["global_retained_coverage"]
    # intentional finding with blank source evidence stays a semantic rejection.
    blank_intentional = _legacy_global_payload(LONG_WORDS, "")
    blank_intentional["findings"][0]["intentional"] = True
    reasons = []
    assert vision._parse_baseline_review(blank_intentional, slide_count=3, context=context,
        rejection_reasons=reasons) is None
    assert reasons == ["global_intentional_evidence"]


def _server_global_context():
    return dict(policy=profiles.QUALITY_RECOVERY_POLICY_V2, reviewScope="global",
        reviewContractVersion=profiles.REVIEW_CONTRACT_VERSION, priorCandidates={},
        composition={"groups": [{"pages": [0, 2]}], "pages": [{}, {}, {}]})


def _server_global_payload(context, evidence, source):
    return {"variantReview": {group: {key: dict(axis=_axis(evidence=evidence), intentional=False,
            sourceEvidence=source) for key in keys}
            for group, keys in prompt.server_review_partition(context, 3).items()}, "designTargets": []}


def test_server_global_review_normalises_page_evidence_and_keeps_every_verdict():
    context = _server_global_context()
    source = " ".join(["source"] * 40)
    payload = _server_global_payload(context, LONG_WORDS, source)
    payload["variantReview"]["group-2"]["page-2"]["axis"] = _axis("failed", "major", "design_rewrite", LONG_WORDS)
    notes = []
    review = vision._parse_baseline_review(payload, slide_count=3, context=context, evidence_notes=notes)
    assert review is not None
    verdicts = {page["index"]: page["axes"]["repetition_deck_flow"]["verdict"] for page in review["pages"]}
    assert verdicts == {0: "passed", 1: "failed", 2: "passed"}
    assert all(_within_bounds(page["axes"]["repetition_deck_flow"]["visibleEvidence"]) for page in review["pages"])
    assert all(0 < len(group["sourceEvidence"]) <= 160 for group in review["deckFlow"]["repetitionGroups"])
    assert len(notes) == 6 and sorted(set(notes)) == ["sourceEvidence", "visibleEvidence"]


def test_server_global_review_partition_mismatch_still_rejects_with_long_evidence():
    context = _server_global_context()
    payload = _server_global_payload(context, LONG_WORDS, "")
    del payload["variantReview"]["group-1"]["page-3"]
    reasons = []
    assert vision._parse_baseline_review(payload, slide_count=3, context=context,
        rejection_reasons=reasons) is None
    assert reasons == ["server_partition_mismatch"]


# --- run_vision_advisory: end to end and the receipt diagnostic ---------------------

PNG = b"\x89PNG\r\n\x1a\n" + b"\0" * 32


def _run_advisory(monkeypatch, payload, context):
    async def request(*args, **kwargs):
        return "fixture-model", deepcopy(payload)

    monkeypatch.setattr(vision, "_request_advisory", request)
    return asyncio.run(vision.run_vision_advisory([PNG], slide_count=1, required=True, design_context=context))


def test_run_vision_advisory_keeps_the_review_and_records_a_normalisation_diagnostic(monkeypatch):
    result = _run_advisory(monkeypatch, _full_payload(LONG_WORDS), _full_context())
    assert "skipped" not in result
    first = prompt.DESIGN_AXES[0]
    assert result["designReview"]["pages"][0]["axes"][first]["verdict"] == "failed"
    diagnostic = result["evidenceNormalization"]
    assert diagnostic == dict(count=3, fieldNames=["visibleEvidence"])  # axis, deckFlow, group
    assert LONG_WORDS not in repr(diagnostic)


def test_run_vision_advisory_omits_the_diagnostic_when_nothing_was_normalised(monkeypatch):
    result = _run_advisory(monkeypatch, _full_payload("Clear and readable."), _full_context())
    assert "skipped" not in result and "evidenceNormalization" not in result


def test_run_vision_advisory_still_reports_semantic_rejections(monkeypatch):
    payload = _full_payload(LONG_WORDS)
    payload["slides"][0]["axes"][prompt.DESIGN_AXES[0]]["repairScope"] = "none"
    result = _run_advisory(monkeypatch, payload, _full_context())
    assert result["skipped"] == "design_review_schema_or_coverage_invalid"
    assert result["schemaRejectionReason"] == "axis_semantics"
    assert "evidenceNormalization" not in result


def test_run_vision_advisory_blank_evidence_still_reports_evidence_bounds(monkeypatch):
    payload = _full_payload("Clear and readable.")
    payload["slides"][0]["axes"][prompt.DESIGN_AXES[0]]["visibleEvidence"] = ""
    result = _run_advisory(monkeypatch, payload, _full_context())
    assert result["skipped"] == "design_review_schema_or_coverage_invalid"
    assert result["schemaRejectionReason"] == "evidence_bounds"
    assert result["schemaRejectionFields"] == {"fieldNames": ["visibleEvidence"]}
