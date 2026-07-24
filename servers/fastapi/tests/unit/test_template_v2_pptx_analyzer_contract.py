from __future__ import annotations

from copy import deepcopy
import json
import math
from pathlib import Path

from jsonschema import Draft202012Validator
from pydantic import ValidationError
import pytest

from templates.v2.pptx.analyzer import (
    LOCAL_STATIC_ANALYZER,
    analyze_ooxml_candidates,
)
from templates.v2.pptx.analyzer_contract import (
    CandidateAnalysis,
    CandidateAnalyzer,
)
from templates.v2.pptx.models import PresentationCandidates


FIXTURE_PATH = (
    Path(__file__).parents[1]
    / "fixtures"
    / "template_v2"
    / "pptx-analyzer-local-static-v1.json"
)


@pytest.fixture
def analyzer_fixture() -> dict:
    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))


def test_local_static_analysis_is_deterministic_and_schema_validated(
    analyzer_fixture: dict,
) -> None:
    first = analyze_ooxml_candidates(analyzer_fixture["input"])
    second = analyze_ooxml_candidates(analyzer_fixture["input"])

    assert first == second
    serialized = first.model_dump(mode="json")
    assert CandidateAnalysis.model_validate(serialized, strict=True) == first
    schema = CandidateAnalysis.model_json_schema()
    Draft202012Validator.check_schema(schema)
    Draft202012Validator(schema).validate(serialized)

    assert first.provider.model_dump(mode="json") == {
        "id": "deterministic-ooxml-static",
        "version": "1",
        "capability": "ooxml-structure",
        "execution": "local",
        "status": "available",
        "network_access": False,
        "external_ai": False,
    }
    assert first.status == "completed"
    assert first.source.status == first.candidate.status == "available"
    assert first.preview.model_dump(mode="json") == {
        "role": "preview",
        "status": "not_provided",
        "media_type": None,
        "sha256": None,
        "reason": "preview_artifact_not_supplied_to_static_analyzer",
    }
    assert first.render.model_dump(mode="json") == {
        "role": "render",
        "status": "not_run",
        "media_type": None,
        "sha256": None,
        "reason": "rendering_outside_static_analyzer_contract",
    }
    assert first.summary.visual_fidelity_status == "not_evaluated"
    assert first.summary.review_required is True


def test_core_candidates_and_mapping_share_the_same_contract(
    analyzer_fixture: dict,
) -> None:
    candidate_model = PresentationCandidates.model_validate(
        analyzer_fixture["input"]
    )

    assert analyze_ooxml_candidates(candidate_model) == analyze_ooxml_candidates(
        analyzer_fixture["input"]
    )


def test_local_static_implementation_satisfies_provider_protocol(
    analyzer_fixture: dict,
) -> None:
    assert isinstance(LOCAL_STATIC_ANALYZER, CandidateAnalyzer)
    assert LOCAL_STATIC_ANALYZER.provider.model_dump(mode="json") == {
        "id": "deterministic-ooxml-static",
        "version": "1",
        "capability": "ooxml-structure",
        "execution": "local",
        "status": "available",
        "network_access": False,
        "external_ai": False,
    }
    assert LOCAL_STATIC_ANALYZER.analyze(
        analyzer_fixture["input"]
    ) == analyze_ooxml_candidates(analyzer_fixture["input"])


@pytest.mark.parametrize(
    ("path", "value", "message"),
    [
        (("slides", 0, "shapes", 0, "x"), math.nan, "finite_number"),
        (("slides", 0, "shapes", 0, "rotation"), math.inf, "finite_number"),
        (("slides", 0, "shapes", 0, "width"), -0.1, "greater_than_equal"),
        (("slides", 0, "shapes", 0, "confidence"), True, "float_type"),
        (("slides", 0, "width"), "1280", "float_type"),
    ],
)
def test_non_finite_and_wrong_numeric_types_are_rejected(
    analyzer_fixture: dict,
    path: tuple[object, ...],
    value: object,
    message: str,
) -> None:
    invalid = deepcopy(analyzer_fixture["input"])
    target = invalid
    for segment in path[:-1]:
        target = target[segment]
    target[path[-1]] = value

    with pytest.raises(ValidationError, match=message):
        analyze_ooxml_candidates(invalid)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("x", 1279.0),
        ("y", 719.0),
    ],
)
def test_out_of_bounds_candidates_are_rejected(
    analyzer_fixture: dict,
    field: str,
    value: float,
) -> None:
    invalid = deepcopy(analyzer_fixture["input"])
    invalid["slides"][0]["shapes"][0][field] = value

    with pytest.raises(ValidationError, match="shape_out_of_canvas_bounds"):
        analyze_ooxml_candidates(invalid)


@pytest.mark.parametrize(
    "mutation",
    [
        lambda value: value.update({"unexpected": "field"}),
        lambda value: value.update({"source_sha256": "not-a-digest"}),
        lambda value: value["slides"][0]["shapes"][1].update(
            {"unsupported_reason": None}
        ),
        lambda value: value["slides"].append(deepcopy(value["slides"][0])),
    ],
)
def test_invalid_candidate_schema_is_rejected(
    analyzer_fixture: dict,
    mutation,
) -> None:
    invalid = deepcopy(analyzer_fixture["input"])
    mutation(invalid)

    with pytest.raises(ValidationError):
        analyze_ooxml_candidates(invalid)


@pytest.mark.parametrize(
    "mutate",
    [
        lambda value: value["candidate"].update({"sha256": "0" * 64}),
        lambda value: value["canvas"]["slides"][0].update({"width": 1279.0}),
        lambda value: value["summary"].update({"shape_count": 99}),
        lambda value: value["source"].update({"role": "candidate"}),
        lambda value: value["provider"].update({"execution": "remote"}),
        lambda value: value["provider"].update({"network_access": True}),
        lambda value: value["provider"].update({"external_ai": True}),
    ],
)
def test_serialized_analysis_rejects_cross_field_tampering(
    analyzer_fixture: dict,
    mutate,
) -> None:
    serialized = analyze_ooxml_candidates(
        analyzer_fixture["input"]
    ).model_dump(mode="json")
    mutate(serialized)

    with pytest.raises(ValidationError):
        CandidateAnalysis.model_validate(serialized, strict=True)
