import pytest

from utils.authored_review_routing import (
    apply_audit_results,
    plan_authored_review_routing,
)


def test_current_slide_always_uses_full_resolution_detail_vqa():
    plan = plan_authored_review_routing(
        scope="current",
        slide_count=12,
        current_slide_index=7,
        local_issue_indices=(),
    )

    assert plan.local_check_indices == (7,)
    assert plan.detail_vqa_indices == (7,)
    assert plan.detail_vqa_is_full_resolution is True
    assert plan.montage_batches == ()
    assert plan.clean_audit_indices == ()


def test_all_scope_routes_every_slide_locally_and_balances_montages():
    plan = plan_authored_review_routing(
        scope="all",
        slide_count=17,
        local_issue_indices=(12, 2, 2),
        sampling_seed="deck-42",
        clean_audit_rate=0.2,
    )

    assert plan.local_check_indices == tuple(range(17))
    assert plan.suspected_slide_indices == (2, 12)
    assert [batch.slide_indices for batch in plan.montage_batches] == [
        tuple(range(8)),
        tuple(range(8, 17)),
    ]
    assert all(6 <= len(batch.slide_indices) <= 9 for batch in plan.montage_batches)
    assert set(plan.suspected_slide_indices).issubset(plan.detail_vqa_indices)
    assert set(plan.clean_audit_indices).isdisjoint(plan.suspected_slide_indices)


def test_clean_audit_sampling_is_deterministic_and_seed_sensitive():
    first = plan_authored_review_routing(
        scope="all", slide_count=20, sampling_seed="stable", clean_audit_rate=0.2
    )
    second = plan_authored_review_routing(
        scope="all", slide_count=20, sampling_seed="stable", clean_audit_rate=0.2
    )
    changed_seed = plan_authored_review_routing(
        scope="all", slide_count=20, sampling_seed="other", clean_audit_rate=0.2
    )

    assert first.clean_audit_indices == second.clean_audit_indices
    assert len(first.clean_audit_indices) == 4
    assert first.clean_audit_indices != changed_seed.clean_audit_indices


def test_small_decks_keep_full_detail_quality_gate():
    plan = plan_authored_review_routing(scope="all", slide_count=5)

    assert plan.montage_batches == ()
    assert plan.clean_audit_indices == ()
    assert plan.detail_vqa_indices == (0, 1, 2, 3, 4)


@pytest.mark.parametrize(
    ("slide_count", "expected_batches"),
    (
        (10, (tuple(range(6)), tuple(range(4, 10)))),
        (11, (tuple(range(6)), tuple(range(5, 11)))),
    ),
)
def test_boundary_decks_use_overlapping_montages(slide_count, expected_batches):
    plan = plan_authored_review_routing(scope="all", slide_count=slide_count)

    assert (
        tuple(batch.slide_indices for batch in plan.montage_batches) == expected_batches
    )
    assert {
        index for batch in plan.montage_batches for index in batch.slide_indices
    } == set(range(slide_count))
    assert all(6 <= len(batch.slide_indices) <= 9 for batch in plan.montage_batches)
    assert plan.clean_audit_indices
    assert len(plan.detail_vqa_indices) < slide_count


@pytest.mark.parametrize(
    ("slide_count", "overlap_index", "second_batch_only_index"),
    ((10, 4, 6), (11, 5, 6)),
)
def test_overlap_audit_failure_counts_as_one_batch(
    slide_count, overlap_index, second_batch_only_index
):
    plan = plan_authored_review_routing(
        scope="all", slide_count=slide_count, clean_audit_rate=1
    )

    one_failure = apply_audit_results(plan, failed_audit_indices=(overlap_index,))
    assert one_failure.level == "batch"
    assert one_failure.affected_batch_indices == (0,)

    independent_failures = apply_audit_results(
        plan, failed_audit_indices=(overlap_index, second_batch_only_index)
    )
    assert independent_failures.level == "full"
    assert independent_failures.detail_vqa_indices == tuple(range(slide_count))


@pytest.mark.parametrize("slide_count", range(6, 26))
def test_deck_routing_properties_across_montage_boundaries(slide_count):
    """Every normal deck has deterministic, complete, auditable VQA routing."""

    local_issues = tuple(index for index in range(slide_count) if index % 5 == 0)
    first = plan_authored_review_routing(
        scope="all",
        slide_count=slide_count,
        local_issue_indices=local_issues,
        sampling_seed="boundary-property",
        clean_audit_rate=0.5,
    )
    second = plan_authored_review_routing(
        scope="all",
        slide_count=slide_count,
        local_issue_indices=local_issues,
        sampling_seed="boundary-property",
        clean_audit_rate=0.5,
    )

    assert first == second
    assert first.local_check_indices == tuple(range(slide_count))
    assert all(6 <= len(batch.slide_indices) <= 9 for batch in first.montage_batches)
    assert {
        index for batch in first.montage_batches for index in batch.slide_indices
    } == set(first.slide_indices)
    assert set(first.suspected_slide_indices).issubset(first.detail_vqa_indices)
    assert set(first.clean_audit_indices).isdisjoint(first.suspected_slide_indices)

    no_failure = apply_audit_results(first)
    assert no_failure.level == "none"
    assert no_failure.detail_vqa_indices == first.detail_vqa_indices

    failed_audit = first.clean_audit_indices[:1]
    escalation = apply_audit_results(first, failed_audit_indices=failed_audit)
    assert escalation.level in ("batch", "full")
    assert set(first.detail_vqa_indices).issubset(escalation.detail_vqa_indices)
    assert set(failed_audit).issubset(escalation.detail_vqa_indices)
    if escalation.level == "full":
        assert escalation.detail_vqa_indices == first.slide_indices
    else:
        affected_indices = {
            index
            for batch in first.montage_batches
            if batch.batch_index in escalation.affected_batch_indices
            for index in batch.slide_indices
        }
        assert affected_indices.issubset(escalation.detail_vqa_indices)


def test_audit_failure_escalates_affected_batch_then_full_deck():
    plan = plan_authored_review_routing(
        scope="all", slide_count=16, sampling_seed="audit", clean_audit_rate=0.5
    )
    first_batch_audit = next(
        index
        for index in plan.clean_audit_indices
        if index in plan.montage_batches[0].slide_indices
    )
    second_batch_audit = next(
        index
        for index in plan.clean_audit_indices
        if index in plan.montage_batches[1].slide_indices
    )

    batch = apply_audit_results(plan, failed_audit_indices=(first_batch_audit,))
    assert batch.level == "batch"
    assert batch.affected_batch_indices == (0,)
    assert set(plan.montage_batches[0].slide_indices).issubset(batch.detail_vqa_indices)

    full = apply_audit_results(
        plan, failed_audit_indices=(first_batch_audit, second_batch_audit)
    )
    assert full.level == "full"
    assert full.detail_vqa_indices == tuple(range(16))
    assert full.reason == "clean_audit_failed_in_multiple_batches"


def test_target_indices_allow_service_to_keep_deck_relative_indices():
    plan = plan_authored_review_routing(
        scope="current", slide_count=12, target_indices=(9,), local_issue_indices=(9,)
    )
    assert plan.slide_indices == (9,)
    assert plan.suspected_slide_indices == (9,)


def test_rejects_unselected_audit_failures_and_invalid_policy():
    plan = plan_authored_review_routing(scope="all", slide_count=12)
    with pytest.raises(ValueError, match="Only selected"):
        apply_audit_results(plan, failed_audit_indices=(11,))
    with pytest.raises(ValueError, match="between 6 and 9"):
        plan_authored_review_routing(scope="all", slide_count=12, batch_size=5)
