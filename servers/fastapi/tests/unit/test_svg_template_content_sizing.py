"""Content-sized template guidance is a strict opt-in with frozen legacy output."""

import hashlib

import pytest

from services.svg_templates.body_composition import composition_rules


@pytest.fixture
def composition():
    return {
        "grammar": {
            "pages_sampled": 20,
            "sections": (2, 3, 4),
            "numbered_headings": 0.9,
            "key_message_bar": 0.8,
            "content_kinds_per_page": (2, 3, 4),
            "kind_shares": (("table", 0.5), ("flow", 0.4)),
            "text_items": (30, 50, 70),
            "ink_coverage": 0.6,
            "bottom_reach": 0.95,
            "text_sizes": (18.0, 14.5, 12.0),
        },
        "exemplars": [],
    }


@pytest.mark.parametrize(
    ("visual_guidance", "exemplars", "digest"),
    [
        (False, False, "cef778f70a886b83ab2ec5676d923b92f3ada2626923a1645b15bc6f0770b184"),
        (False, True, "dc77c6bf2f42e939d245b006d2700144bccaf682bd3129c7bb942990300f8133"),
        (True, False, "9ac561f5c67167b36837f21d5f1012c42edb5c98efbe6720a04e873520f57302"),
        (True, True, "fc87008f5e75d191076dafaf0a3430dce5d8296f5c4af22c4a8bdddd896313b1"),
    ],
)
def test_legacy_prompt_bytes(composition, visual_guidance, exemplars, digest):
    kwargs = {"body_floor": 14, "visual_guidance": visual_guidance, "exemplars": exemplars}
    for value in (False, None, 0, 1, "true"):
        result = composition_rules(composition, content_sizing=value, **kwargs)
        assert hashlib.sha256(result.encode("utf-8")).hexdigest() == digest
    assert hashlib.sha256(composition_rules(composition, **kwargs).encode("utf-8")).hexdigest() == digest


@pytest.mark.parametrize("visual_guidance", [False, True])
def test_enabled_guidance_sizes_content_and_replaces_legacy_density(composition, visual_guidance):
    rules = composition_rules(composition, body_floor=14, visual_guidance=visual_guidance, content_sizing=True)
    assert "Size cards, tables and other components to their actual content" in rules
    assert "readable typography" in rules
    assert "table column widths according to their content" in rules
    assert "end card heights after the last line with appropriate padding" in rules
    assert "minimum body font floor; never shrink type to fit" in rules
    assert "Preserve every fact, label and figure in reading order" in rules
    assert "whitespace inside" in rules and "across the whole body" in rules
    assert "without stretching components" in rules
    assert "Consolidate unavoidable leftover body whitespace" in rules
    assert "Fill body_region from top to bottom" not in rules
    assert "enlarge the components" not in rules
    assert "Compose freely" in rules


def test_missing_composition_remains_empty():
    assert composition_rules(None, content_sizing=True) == ""
