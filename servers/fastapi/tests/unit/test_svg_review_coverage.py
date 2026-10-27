"""Coverage is an identity map; ambiguous or absent PNG evidence fails closed."""
from copy import deepcopy
from types import SimpleNamespace

import pytest

from services.generation_core.quality.review_coverage import review_coverage
from services.generation_core.quality.vision_advisory_prompt import (
    AXIS_REVIEW_PROMPT_VERSION, DESIGN_REVIEW_PROMPT_VERSION,
    DESIGN_REVIEW_PROMPT_VERSIONS, is_design_review_version,
)
from services.svg_export_visual_review import split_page_advisories, current_page_advisories
from services.svg_quality_recovery_review import (
    LEGACY_SAVED_REVIEW_PROMPT_LABEL, labelled_review_runtime, saved_review_runtime_identity,
)
from tests.unit.test_svg_page_quality_publication import _measured, PNG
from utils.hashing import sha256_hex


def test_design_review_version_predicate_accepts_only_producer_versions():
    assert DESIGN_REVIEW_PROMPT_VERSIONS == {
        DESIGN_REVIEW_PROMPT_VERSION, AXIS_REVIEW_PROMPT_VERSION,
    }
    assert is_design_review_version(DESIGN_REVIEW_PROMPT_VERSION)
    assert is_design_review_version(AXIS_REVIEW_PROMPT_VERSION)
    assert not is_design_review_version("svg-quality-first-design-review/v3")
    assert not is_design_review_version(None)


def test_saved_review_runtime_identity_binds_legacy_or_true_prompt_once():
    code = {"renderer": "fixture", "worker": {"checkpointReuseEligible": True}}
    v1 = labelled_review_runtime(code, DESIGN_REVIEW_PROMPT_VERSION)
    v2 = labelled_review_runtime(code, AXIS_REVIEW_PROMPT_VERSION)

    assert saved_review_runtime_identity(v1, DESIGN_REVIEW_PROMPT_VERSION, code=code) == "true"
    assert saved_review_runtime_identity(v1, AXIS_REVIEW_PROMPT_VERSION, code=code) == "legacy"
    assert saved_review_runtime_identity(v2, AXIS_REVIEW_PROMPT_VERSION, code=code) == "true"
    assert saved_review_runtime_identity(v2, DESIGN_REVIEW_PROMPT_VERSION, code=code) is None
    assert saved_review_runtime_identity(
        {**v1, "promptVersion": LEGACY_SAVED_REVIEW_PROMPT_LABEL},
        AXIS_REVIEW_PROMPT_VERSION,
        code=code,
    ) == "legacy"
    assert saved_review_runtime_identity(v1, AXIS_REVIEW_PROMPT_VERSION,
                                         code={"renderer": "changed"}) is None


def test_noncontiguous_and_reordered_coverage_uses_identity():
    vision = dict(slideCount=5, reviewedPageIndices=[3, 0, 2],
                  reviewedPngDigests=[str(i) * 64 for i in [3, 0, 2]], unreviewedPages=[1, 4])
    before = deepcopy(vision)
    coverage = review_coverage(vision)
    assert [coverage.digest(i) for i in range(5)] == ["0" * 64, None, "2" * 64, "3" * 64, None]
    assert coverage.projection(range(5)) == dict(reviewedPages=[0, 2, 3], unreviewedPages=[1, 4], totalPages=5)
    assert vision == before


@pytest.mark.parametrize("change", [
    {"reviewedPageIndices": [0, 0]}, {"reviewedPageIndices": [True, 1]},
    {"reviewedPageIndices": [-1, 1]}, {"reviewedPageIndices": [0]},
    {"reviewedPngDigests": ["bad", "1" * 64]}, {"reviewedPngDigests": None},
    {"unreviewedPages": [1]}, {"unreviewedPages": [False]}, {"unreviewedPages": None},
])
def test_ambiguous_coverage_is_unreviewed(change):
    coverage = review_coverage(dict(reviewedPageIndices=[0, 1], reviewedPngDigests=["0" * 64, "1" * 64]) | change)
    assert not coverage.valid
    assert coverage.unreviewed(range(2)) == [0, 1]
    assert not coverage.matches([0, 1], ["0" * 64, "1" * 64])


def test_missing_coverage_is_not_inferred_from_slide_count():
    assert review_coverage(dict(slideCount=30, slides=[])).digest(25) is None
    assert not review_coverage(dict(slideCount=30, slides=[])).explicit


def test_local_subset_maps_once_to_durable_publication_indices():
    png = sha256_hex(PNG)
    slides = [SimpleNamespace(svg_content=f"<svg>{i}</svg>", content={"svgReceipt": {
        "png_sha256": png, "quality": _measured(f"<svg>{i}</svg>")}}) for i in range(5)]
    vision = dict(slideCount=2, reviewedPageIndices=[0], reviewedPngDigests=[png],
                  unreviewedPages=[1], slides=[dict(index=0, issues=["cramped"])])
    records = split_page_advisories(vision, slides, [2, 4])
    assert records[0]["vision"]["reviewedPageIndices"] == [2]
    assert records[0]["vision"]["slides"] == [dict(index=2, issues=["cramped"])]
    assert records[1]["vision"]["reviewedPngDigests"] == []
    assert records[1]["vision"]["unreviewedPages"] == [4]
    assert list(current_page_advisories(slides, records)) == [2]
