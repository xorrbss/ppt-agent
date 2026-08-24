import json

from services.presentation_export_projection import (
    COMPOSITION_PIN_REFERENCE,
    project_presentation_for_export,
)


def test_export_projection_deduplicates_only_matching_composition_pins():
    shared_pin = {
        "digest": "a" * 64,
        "retainedPayload": "x" * 4096,
    }
    distinct_pin = {"digest": "b" * 64, "retainedPayload": "different"}
    payload = {
        "theme": {
            "authored_vnext": {
                "compositionPin": shared_pin,
                "scene": {"compositionPin": dict(shared_pin)},
            }
        },
        "slides": [
            {"content": {"compositionPin": dict(shared_pin)}},
            {"content": {"compositionPin": distinct_pin}},
            {"content": {"role": "body"}},
        ],
    }

    original_size = len(json.dumps(payload))
    projected = project_presentation_for_export(payload)

    assert projected["theme"]["authored_vnext"]["compositionPin"] == shared_pin
    assert (
        projected["theme"]["authored_vnext"]["scene"]["compositionPin"]
        == COMPOSITION_PIN_REFERENCE
    )
    assert (
        projected["slides"][0]["content"]["compositionPin"]
        == COMPOSITION_PIN_REFERENCE
    )
    assert projected["slides"][1]["content"]["compositionPin"] == distinct_pin
    assert len(json.dumps(projected)) < original_size / 2
