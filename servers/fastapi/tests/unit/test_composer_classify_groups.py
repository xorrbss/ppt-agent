"""``flow_step_groups``: order-free (``|``) segmentation of a flow block's runs.

``classify.py``'s single-block arrow-flow path (``_block_component``) builds
one :class:`Paragraph` per projected run via ``bind_paragraphs`` and today
wraps each one in its own group -- ``groups=[[step] for step in steps]``. A
source line that chains several ``조건 → 결과`` pairs with `` | `` between
them (independent items, not a single eight-step sequence) gets flattened
into one card per run instead of one card per pair.

These tests exercise the new grouping helper directly against real projected
runs and real bound paragraphs, without touching ``classify.py``.
"""

from __future__ import annotations

from typing import Any

import pytest

from services.composer.classify_bindings import bind_paragraphs, build_text_block
from services.composer.classify_groups import flow_step_groups
from services.composer.plan import Paragraph

BLOCK_ID = "slide-1:text:2"


def make_block(text: str, *, order: int = 1) -> Any:
    """A minimal stored-manifest text block, projected via ``build_text_block``."""

    manifest_block = {
        "source_block_id": BLOCK_ID,
        "kind": "text",
        "semantic_role": "body",
        "order": order,
        "payload": {"text": text},
    }
    block = build_text_block(manifest_block)
    assert block is not None
    return block


def legacy_groups(steps: list[Paragraph]) -> tuple[list[list[Paragraph]], list[Paragraph | None]]:
    """Reproduce ``classify.py``'s current inline grouping, verbatim, for comparison."""

    return [[step] for step in steps], [None] * len(steps)


# --- No order_free boundary: must match today's grouping exactly -----------------


def test_arrow_chain_without_pipes_matches_legacy_one_group_per_run() -> None:
    """A plain arrow chain (no `` | `` anywhere) has no order_free boundary."""

    block = make_block("영업 → 상담 → 제안")
    steps = bind_paragraphs(block)
    assert len(steps) == 3  # one Paragraph per run: sanity check on the fixture

    groups, headers = flow_step_groups(block, steps)

    assert (groups, headers) == legacy_groups(steps)
    assert [[p.text for p in g] for g in groups] == [["영업"], ["상담"], ["제안"]]
    assert headers == [None, None, None]


def test_single_run_block_matches_legacy_grouping() -> None:
    block = make_block("단일 문장")
    steps = bind_paragraphs(block)
    assert len(steps) == 1

    assert flow_step_groups(block, steps) == legacy_groups(steps)


def test_vertical_arrow_lines_without_pipes_match_legacy_grouping() -> None:
    block = make_block("문제 제시\n↓\n해결 정보")
    steps = bind_paragraphs(block)

    assert flow_step_groups(block, steps) == legacy_groups(steps)


# --- order_free boundaries: pipes partition into segments ------------------------


def test_pipe_separated_condition_result_pairs_become_one_group_each() -> None:
    """Four `` 조건 → 결과 `` pairs joined by `` | `` -> four groups, not eight."""

    block = make_block("합격 → 발급 | 불합격 → 재응시 | 보류 → 서류 보완 | 면제 → 확인 필요")
    steps = bind_paragraphs(block)
    assert len(steps) == 8  # four pairs, two runs each: the flattened baseline

    groups, headers = flow_step_groups(block, steps)

    assert len(groups) == 4
    assert headers == [None, None, None, None]
    assert [[p.text for p in g] for g in groups] == [
        ["합격", "발급"],
        ["불합격", "재응시"],
        ["보류", "서류 보완"],
        ["면제", "확인 필요"],
    ]


def test_pipe_separated_single_items_stay_one_run_per_group() -> None:
    """Independent items with no internal arrow chain still get one group each."""

    block = make_block("사과 | 바나나 | 포도")
    steps = bind_paragraphs(block)

    groups, headers = flow_step_groups(block, steps)

    assert [[p.text for p in g] for g in groups] == [["사과"], ["바나나"], ["포도"]]
    assert headers == [None, None, None]


def test_mixed_chain_and_pipe_groups_runs_only_at_pipe_boundaries() -> None:
    """A chain of three runs beside a lone item: the pipe is the only boundary."""

    block = make_block("입력 → 처리 → 출력 | 예외 처리")
    steps = bind_paragraphs(block)
    assert len(steps) == 4

    groups, headers = flow_step_groups(block, steps)

    assert [[p.text for p in g] for g in groups] == [["입력", "처리", "출력"], ["예외 처리"]]
    assert headers == [None, None]


# --- Partition invariants: every run bound exactly once, in order, unchanged -----


def test_every_run_is_bound_exactly_once_in_original_order() -> None:
    block = make_block("합격 → 발급 | 불합격 → 재응시 | 보류 → 서류 보완")
    steps = bind_paragraphs(block)

    groups, _headers = flow_step_groups(block, steps)

    flattened = [paragraph for group in groups for paragraph in group]
    assert flattened == steps  # same objects, same order, nothing dropped or duplicated
    assert len(flattened) == len(block.projection.runs)


def test_grouping_reuses_paragraph_objects_without_rebuilding_them() -> None:
    """Regrouping must not construct new Paragraphs -- bindings must not drift."""

    block = make_block("합격 → 발급 | 불합격 → 재응시")
    steps = bind_paragraphs(block)

    groups, _headers = flow_step_groups(block, steps)

    for group in groups:
        for paragraph in group:
            assert paragraph in steps
            index = steps.index(paragraph)
            assert group[group.index(paragraph)] is steps[index]


def test_binding_ids_are_unchanged_by_regrouping() -> None:
    block = make_block("합격 → 발급 | 불합격 → 재응시")
    steps = bind_paragraphs(block)
    legacy_binding_ids = [step.source.binding_id for step in steps]

    groups, _headers = flow_step_groups(block, steps)

    grouped_binding_ids = [paragraph.source.binding_id for group in groups for paragraph in group]
    assert grouped_binding_ids == legacy_binding_ids


def test_mismatched_step_count_raises() -> None:
    block = make_block("합격 → 발급 | 불합격 → 재응시")
    steps = bind_paragraphs(block)

    with pytest.raises(ValueError, match="one Paragraph per projected run"):
        flow_step_groups(block, steps[:-1])
