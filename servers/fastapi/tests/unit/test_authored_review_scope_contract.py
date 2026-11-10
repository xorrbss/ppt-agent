
import pytest
from pydantic import ValidationError

from models.presentation_from_template import AuthoredQualityReviewRequest


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
