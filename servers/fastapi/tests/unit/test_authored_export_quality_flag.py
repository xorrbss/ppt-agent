"""Stage 4 export flag: more than two raster fallbacks on a slide is a failure."""

import pytest

from services.authored_export_quality_flag import evaluate_export_fallback_quality


def _slide(number, ids, elements=None):
    payload = {
        "slideNumber": number,
        "editable": True,
        "rasterFallbackElements": len(ids),
        "fallbackElementIds": list(ids),
    }
    if elements is not None:
        payload["fallbackElements"] = elements
    return payload


def test_a_slide_over_the_limit_is_marked_a_quality_failure():
    quality = {
        "slides": [
            _slide(1, ["a", "b"]),
            _slide(2, ["a", "b", "c"]),
            _slide(3, []),
        ]
    }

    result = evaluate_export_fallback_quality(quality)

    assert result.limit == 2
    assert result.failed is True
    assert result.failed_slide_numbers == (2,)
    assert [slide.failed for slide in result.slides] == [False, True, False]


def test_backplate_only_paint_is_not_counted_against_a_slide():
    quality = {
        "slides": [
            _slide(
                4,
                ["fig.a", "fig.b", "fig.c"],
                elements=[
                    {"elementId": "fig.a", "reasons": ["gradient-backplate-only"]},
                    {"elementId": "fig.b", "reasons": ["shadow-backplate-only"]},
                    {"elementId": "fig.c", "reasons": ["complex-diagram"]},
                ],
            )
        ]
    }

    result = evaluate_export_fallback_quality(quality)

    assert result.failed is False
    assert result.slides[0].counted_element_ids == ("fig.c",)
    assert result.slides[0].fallback_element_ids == ("fig.a", "fig.b", "fig.c")


def test_an_element_with_a_real_reason_beside_a_backplate_one_still_counts():
    quality = {
        "slides": [
            _slide(
                1,
                ["fig.a", "fig.b", "fig.c"],
                elements=[
                    {
                        "elementId": "fig.a",
                        "reasons": ["gradient-backplate-only", "svg-text"],
                    },
                    {"elementId": "fig.b", "reasons": ["complex-chart"]},
                    {"elementId": "fig.c", "reasons": ["complex-table"]},
                ],
            )
        ]
    }

    result = evaluate_export_fallback_quality(quality)

    assert result.failed is True
    assert len(result.slides[0].counted_element_ids) == 3


def test_the_limit_comes_from_the_registered_env_knob(monkeypatch):
    quality = {"slides": [_slide(1, ["a", "b", "c"])]}

    monkeypatch.setenv("AUTHORED_EXPORT_FALLBACK_LIMIT", "3")
    assert evaluate_export_fallback_quality(quality).failed is False

    monkeypatch.setenv("AUTHORED_EXPORT_FALLBACK_LIMIT", "1")
    relaxed = evaluate_export_fallback_quality(quality)
    assert relaxed.limit == 1
    assert relaxed.failed is True

    # An empty value is "unset": the registry default governs again.
    monkeypatch.setenv("AUTHORED_EXPORT_FALLBACK_LIMIT", "")
    assert evaluate_export_fallback_quality(quality).limit == 2


@pytest.mark.parametrize(
    "quality",
    [None, {}, {"slides": None}, {"slides": "three"}, {"slides": []}],
)
def test_a_report_without_slides_is_not_a_failure(quality):
    result = evaluate_export_fallback_quality(quality)

    assert result.slides == ()
    assert result.failed is False
    assert result.to_dict()["failedSlideNumbers"] == []


def test_an_older_exporter_without_element_detail_still_counts_the_ids():
    quality = {"slides": [{"slideNumber": 9, "fallbackElementIds": ["a", "b", "c", "d"]}]}

    result = evaluate_export_fallback_quality(quality)

    assert result.failed_slide_numbers == (9,)
    assert result.to_dict()["slides"][0]["countedElementIds"] == ["a", "b", "c", "d"]


def test_the_export_result_carries_the_verdict_to_its_caller(monkeypatch):
    from utils import export_utils

    quality = {
        "status": "partially-editable",
        "editableSlides": 3,
        "slides": [_slide(1, ["a", "b", "c"])],
    }

    verdict = export_utils.evaluate_export_fallback_quality(quality)

    assert verdict.to_dict()["failed"] is True
    model = export_utils.AuthoredHybridPresentationAndPath(
        presentation_id="00000000-0000-0000-0000-000000000001",
        path="deck.pptx",
        coverage_manifest={},
        renderer_source_sha256="sha256:0",
        fallback_quality=verdict.to_dict(),
    )
    assert model.fallback_quality["failedSlideNumbers"] == [1]
    # The flag is reporting only: the export model is still a valid artifact.
    assert model.path == "deck.pptx"
