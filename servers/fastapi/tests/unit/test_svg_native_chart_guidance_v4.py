"""Admission-pinned chart guidance keeps earlier native and author prompt bytes."""
from __future__ import annotations

from functools import partial
from hashlib import sha256

import pytest

from services.generation_core.authoring import author_slide_svg as author
from services.generation_core.drivers.svg import native_declarations as declarations
from services.generation_core.drivers.svg.native_requirements import (
    REQUIREMENTS_VERSION, REQUIREMENTS_VERSION_V3, REQUIREMENTS_VERSION_V4, SNAPSHOT_REVISION_KEY,
)
from services.svg_authoring_profile import (
    ART_DIRECTION_PROFILE_V9, ART_DIRECTION_PROFILE_V10, QUALITY_RECOVERY_POLICY_V2,
)
from utils.hashing import canonical_json_digest


# Captured before v4 guidance from the existing standard, source-grounded and layered rules.
LEGACY_NATIVE_RULES = [
    (declarations.native_author_rules, "111269c2be70f0685d3c261ca59c2966f2abd12a6ef58ad624439e59cb40f74d"),
    (partial(declarations.native_author_rules, source_grounded=True),
     "5d366a88426f41f4e63c9ddfad62cd534b88755baa6e090aaca6b9da4b8e2d06"),
    (declarations.native_author_rules_v10, "c85e5d736f8304ede9ff5acf51d2e0ad1cfa82ec4d2995e9e4f4af3281703298"),
]


def _snapshot(revision=None, *, layered=False, source_grounded=False):
    snapshot = {
        "canvas": {"width": 1280, "height": 720},
        "authoring_profile": dict(ART_DIRECTION_PROFILE_V10 if layered else ART_DIRECTION_PROFILE_V9),
    }
    if revision is not None:
        snapshot[SNAPSHOT_REVISION_KEY] = revision
    if source_grounded:
        snapshot["quality_recovery_policy"] = dict(QUALITY_RECOVERY_POLICY_V2)
    snapshot["digest"] = canonical_json_digest(snapshot, allow_nan=True)
    return snapshot


@pytest.mark.parametrize("factory,expected_digest", LEGACY_NATIVE_RULES)
@pytest.mark.parametrize("revision", [None, REQUIREMENTS_VERSION, REQUIREMENTS_VERSION_V3])
def test_legacy_native_rule_bytes_are_unchanged(factory, expected_digest, revision):
    rules = factory(requirements_revision=revision)
    assert sha256(rules.encode()).hexdigest() == expected_digest
    assert declarations.NATIVE_CHART_GUIDANCE_V4 not in rules


@pytest.mark.parametrize("factory,_digest", LEGACY_NATIVE_RULES)
def test_v4_native_rules_add_composition_title_and_visible_data_guidance(factory, _digest):
    rules = factory(requirements_revision=REQUIREMENTS_VERSION_V4)
    guidance = declarations.NATIVE_CHART_GUIDANCE_V4
    assert guidance in rules
    assert rules.replace(guidance, "") == factory(requirements_revision=REQUIREMENTS_VERSION_V3)
    for requirement in ("donut or pie", "parts of a whole", "bar or line", "trends and comparisons",
                        "single-series", "one unique drawn title naming that series", "instead of a separate legend",
                        "every category, series name and required value", "metadata alone never counts"):
        assert requirement in guidance


@pytest.mark.parametrize("new_copy", [False, True])
@pytest.mark.parametrize("source_grounded", [False, True])
def test_standard_snapshot_pins_guidance_and_preserves_legacy_rule_bytes(new_copy, source_grounded):
    def rules(revision):
        return author.svg_author_rules(_snapshot(revision, source_grounded=source_grounded), 18,
                                       new_copy=new_copy)

    legacy = rules(None)
    assert rules(REQUIREMENTS_VERSION) == rules(REQUIREMENTS_VERSION_V3) == legacy
    assert rules(REQUIREMENTS_VERSION_V4).replace(declarations.NATIVE_CHART_GUIDANCE_V4, "") == legacy
    assert declarations.NATIVE_CHART_GUIDANCE_V4 in rules(REQUIREMENTS_VERSION_V4)


@pytest.mark.parametrize("header_applied", [False, True])
def test_layered_profile_ten_snapshot_pins_guidance_and_preserves_legacy_rule_bytes(header_applied):
    def rules(revision):
        return author._layered_author_rules(_snapshot(revision, layered=True), "{}", 18,
                                            language="Korean", header_applied=header_applied)

    legacy = rules(None)
    assert rules(REQUIREMENTS_VERSION) == rules(REQUIREMENTS_VERSION_V3) == legacy
    assert rules(REQUIREMENTS_VERSION_V4).replace(declarations.NATIVE_CHART_GUIDANCE_V4, "") == legacy
    assert declarations.NATIVE_STACKED_CHART_RULE in rules(REQUIREMENTS_VERSION_V4)
    assert declarations.NATIVE_CHART_GUIDANCE_V4 in rules(REQUIREMENTS_VERSION_V4)


def test_withdrawing_native_charts_also_withdraws_v4_chart_guidance(monkeypatch):
    monkeypatch.setattr(declarations, "NATIVE_CHARTS_OFFERED", False)
    for factory, _digest in LEGACY_NATIVE_RULES:
        rules = factory(requirements_revision=REQUIREMENTS_VERSION_V4)
        assert "data-native-table" in rules
        assert "data-native-chart" not in rules
        assert declarations.NATIVE_CHART_GUIDANCE_V4 not in rules
