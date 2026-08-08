import asyncio
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from models.presentation_from_template import (
    AuthoredQualityReviewRequest,
    parse_review_page_range,
)
from services.authored_quality_routing import hybrid_style_initial_review
from services.authored_quality_review_service import _resolve_positions
from utils.llm_calls.critique_slide import SlideCritique
from utils.authored_review_routing import plan_authored_review_routing


def test_page_range_normalizes_duplicates_and_inclusive_ranges():
    assert parse_review_page_range(" 3, 5-8, 12, 7, 3 ", slide_count=12) == [
        2,
        4,
        5,
        6,
        7,
        11,
    ]


@pytest.mark.parametrize(
    ("value", "message"),
    (
        ("", "required"),
        ("0", "positive"),
        ("-1", "Invalid"),
        ("3-1", "ascending"),
        ("1.5", "Invalid"),
        ("1,,2", "Invalid"),
        ("1-a", "Invalid"),
        ("13", "outside"),
    ),
)
def test_page_range_rejects_invalid_or_out_of_bounds_input(value, message):
    with pytest.raises(ValueError, match=message):
        parse_review_page_range(value, slide_count=12)


def test_review_request_normalizes_subset_and_forces_auto_fix_contract():
    request = AuthoredQualityReviewRequest(
        scope="subset", slide_indices=[7, 2, 7, 4]
    )

    assert request.slide_indices == [2, 4, 7]
    assert request.mode == "analyze_and_fix"

    with pytest.raises(ValidationError, match="analyze_and_fix"):
        AuthoredQualityReviewRequest.model_validate(
            {"scope": "all", "mode": "analyze_only"}
        )


def test_review_request_openapi_schema_exposes_only_supported_contract():
    properties = AuthoredQualityReviewRequest.model_json_schema()["properties"]

    assert set(properties["scope"]["enum"]) == {"all", "current", "subset"}
    assert properties["mode"]["const"] == "analyze_and_fix"


@pytest.mark.parametrize(
    "payload",
    (
        {"scope": "all", "slide_indices": [0]},
        {"scope": "current", "slide_indices": []},
        {"scope": "current", "slide_indices": [0, 1]},
        {"scope": "subset", "slide_indices": []},
        {"scope": "subset", "slide_indices": [-1]},
        {"scope": "subset", "slide_indices": [1.0]},
        {"scope": "subset", "slide_indices": [True]},
        {"scope": "subset", "slide_indices": ["1"]},
    ),
)
def test_review_request_rejects_ambiguous_or_non_integer_selection(payload):
    with pytest.raises(ValidationError):
        AuthoredQualityReviewRequest.model_validate(payload)


def test_subset_positions_are_exact_and_bounds_checked_before_provider_work():
    request = AuthoredQualityReviewRequest(
        scope="subset", slide_indices=[8, 2, 5, 2]
    )
    assert _resolve_positions(request, slide_count=10) == [2, 5, 8]

    plan = plan_authored_review_routing(
        scope=request.scope,
        slide_count=10,
        target_indices=request.slide_indices,
        local_issue_indices=(0, 2, 5, 8, 9),
    )
    assert plan.scope == "subset"
    assert plan.slide_indices == (2, 5, 8)
    assert plan.local_check_indices == (2, 5, 8)
    assert plan.detail_vqa_indices == (2, 5, 8)
    assert plan.suspected_slide_indices == (2, 5, 8)

    out_of_bounds = AuthoredQualityReviewRequest(
        scope="subset", slide_indices=[10]
    )
    with pytest.raises(ValueError, match="outside"):
        _resolve_positions(out_of_bounds, slide_count=10)


def test_subset_routing_requires_explicit_nonempty_targets():
    with pytest.raises(ValueError, match="requires target_indices"):
        plan_authored_review_routing(scope="subset", slide_count=10)
    with pytest.raises(ValueError, match="at least one"):
        plan_authored_review_routing(
            scope="subset", slide_count=10, target_indices=[]
        )


def test_subset_hybrid_review_sends_only_selected_slides_to_provider():
    calls: list[tuple[list[bytes], list[str]]] = []

    async def critique_batch(pngs, *, contexts):
        selected_pngs = list(pngs)
        selected_contexts = list(contexts)
        calls.append((selected_pngs, selected_contexts))
        return [
            SlideCritique(needs_fix=False, issues=[])
            for _ in selected_pngs
        ]

    _, routing, *_ = asyncio.run(
        hybrid_style_initial_review(
            pngs=[b"slide-3", b"slide-6", b"slide-9"],
            contexts=["page 3", "page 6", "page 9"],
            positions=[2, 5, 8],
            slide_count=10,
            local_reports=[
                SimpleNamespace(needs_attention=False),
                SimpleNamespace(needs_attention=False),
                SimpleNamespace(needs_attention=False),
            ],
            sampling_seed="subset-contract",
            scope="subset",
            critique_batch=critique_batch,
            observe_cache=lambda _pngs, _contexts: (0, 0),
            unusable_reason=lambda _critique: None,
        )
    )

    nonempty_calls = [call for call in calls if call[0]]
    assert nonempty_calls == [
        ([b"slide-3", b"slide-6", b"slide-9"], ["page 3", "page 6", "page 9"])
    ]
    assert routing["plan"]["scope"] == "subset"
    assert routing["plan"]["slide_indices"] == (2, 5, 8)
