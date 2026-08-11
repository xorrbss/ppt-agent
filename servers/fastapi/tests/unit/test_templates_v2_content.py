from __future__ import annotations

import json
from pathlib import Path

from templates.v2.content import hydrate_layout_content


ROOT = Path(__file__).resolve().parents[4]
FIXTURE = (
    ROOT
    / "contracts"
    / "template_v2"
    / "fixtures"
    / "repeatable-grouped-content.a3352f3c.json"
)


def test_canonical_hydration_applies_regular_and_irregular_repeat_content() -> None:
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
    source = payload["layouts"][0]

    hydrated = hydrate_layout_content(
        source,
        {
            "regular_cards": {
                "cards": [
                    {"card_title": "First card"},
                    {"card_title": "Second card"},
                ]
            },
            "positioned_callouts": {
                "items": [
                    {"item_title": "Center callout"},
                    {"item_title": "Upper callout"},
                ]
            },
        },
    )

    regular = hydrated["components"][0]["elements"][0]["children"]
    positioned = hydrated["components"][1]["elements"]

    assert [item["children"][0]["runs"][0]["text"] for item in regular] == [
        "First card",
        "Second card",
    ]
    assert positioned[0]["type"] == "vector"
    assert [item["position"]["x"] for item in positioned[1:]] == [320, 120]
    assert [item["children"][0]["runs"][0]["text"] for item in positioned[1:]] == [
        "Center callout",
        "Upper callout",
    ]
    assert source["components"][0]["elements"][0]["children"][0].get("runs") is None
