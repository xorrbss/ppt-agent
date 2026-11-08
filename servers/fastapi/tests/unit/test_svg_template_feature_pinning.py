"""Integration contracts for frozen SVG template experiments and measurements."""

from copy import deepcopy
from types import SimpleNamespace

import pytest

from services.svg_templates import generation
from utils import get_env, get_env_concurrency


@pytest.mark.parametrize(
    "accessor",
    ["get_svg_template_content_sizing_env", "get_svg_template_pictures_env",
     "get_svg_native_chart_integrity_v5_env"],
)
@pytest.mark.parametrize("value, expected", [(None, False), ("on", True), (" TRUE ", True),
                                            ("0", False), ("unknown", False)])
def test_experiment_settings_are_explicitly_default_off(monkeypatch, accessor, value, expected):
    monkeypatch.setattr(get_env, "get_setting_env", lambda name, settings=None: value)
    assert getattr(get_env_concurrency, accessor)() is expected


def test_fresh_content_sizing_pin_changes_only_its_policy_and_digest(monkeypatch):
    selection = {"mode": "template-redesign", "template_pin": {"templateId": "test-template"},
                 "style_projection": {"fonts": {}}}
    request = SimpleNamespace(svg_template_pin={}, _svg_template_selection=selection)
    monkeypatch.setattr(generation, "template_package", lambda selected: {
        "font_manifest": {"assets": [{"family": "Test Sans"}]},
    })
    monkeypatch.setattr(generation, "template_composition_guidance", lambda: ["existing guidance"])
    monkeypatch.setattr(get_env_concurrency, "get_svg_template_body_renders_env", lambda: False)
    monkeypatch.setattr(get_env_concurrency, "get_svg_template_pictures_env", lambda: False)
    monkeypatch.setattr(get_env_concurrency, "get_svg_template_content_sizing_env", lambda: False)
    base = {"tokens": {}, "provenance": {}}
    legacy = deepcopy(base)
    generation.apply_template_snapshot(legacy, request)
    assert "template_content_sizing" not in legacy
    assert "template_content_sizing_revision" not in legacy

    monkeypatch.setattr(get_env_concurrency, "get_svg_template_content_sizing_env", lambda: True)
    fresh = deepcopy(base)
    generation.apply_template_snapshot(fresh, request)
    assert fresh["template_content_sizing"] is True
    assert fresh["template_content_sizing_revision"] == "svg-template-content-sizing/v1"
    assert fresh["digest"] != legacy["digest"]
    for key, value in legacy.items():
        if key != "digest":
            assert fresh[key] == value


@pytest.mark.parametrize("snapshot, expected", [
    ({}, False),
    ({"template_content_sizing": True}, False),
    ({"template_content_sizing": "true", "template_content_sizing_revision": "svg-template-content-sizing/v1"}, False),
    ({"template_content_sizing": True, "template_content_sizing_revision": "unknown"}, False),
    ({"template_content_sizing": True, "template_content_sizing_revision": "svg-template-content-sizing/v1"}, True),
])
def test_frozen_content_sizing_never_reads_a_later_environment(monkeypatch, snapshot, expected):
    def unexpected_live_read():
        raise AssertionError("frozen authoring consulted the process setting")

    monkeypatch.setattr(get_env_concurrency, "get_svg_template_content_sizing_env", unexpected_live_read)
    assert generation.snapshot_has_content_sizing(snapshot) is expected


def test_non_template_snapshot_is_untouched(monkeypatch):
    def unexpected_live_read():
        raise AssertionError("non-template path consulted a template experiment")

    monkeypatch.setattr(get_env_concurrency, "get_svg_template_content_sizing_env", unexpected_live_read)
    snapshot = {"existing": {"driver": "unchanged"}}
    generation.apply_template_snapshot(snapshot, SimpleNamespace(svg_template_pin=None))
    assert snapshot == {"existing": {"driver": "unchanged"}}


class _MeasureConnection:
    def __init__(self, content, checkpoint):
        self.content, self.checkpoint, self.query = content, checkpoint, ""

    def cursor(self):
        return self

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def execute(self, query, parameters):
        self.query = query

    def fetchone(self):
        return ({},) if "select theme" in self.query else ("test-job",)

    def fetchall(self):
        if "from slides " in self.query:
            return [(0, self.content)]
        return [(0, self.checkpoint)]


@pytest.mark.parametrize("body", [None, {"x": 10, "y": 10, "width": 160, "height": 80}])
def test_evaluation_forwards_saved_body_bounds_and_preserves_provenance(body):
    from scripts.svg_template_eval.run_eval import measure_deck

    checkpoint = {"svg_template_page": {"body_region": body}} if body else {}
    svg = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 200 100"><text x="20" y="30">Test</text></svg>'
    result = measure_deck(_MeasureConnection(svg, checkpoint), "test-presentation")
    measured = result["content_sizing"][0]
    assert measured["page_index"] == 0
    assert measured["policy_revision"] == "svg-content-sizing-v1"
    assert measured["card_unused_envelope_share"] is None
    if body:
        assert tuple(measured["context"]["body_bounds"]) == (10.0, 10.0, 160.0, 80.0)
        assert measured["context"]["body_bounds_source"] != "page_fallback"
    else:
        assert measured["context"]["body_bounds_source"] == "page_fallback"


def test_missing_svg_measurement_remains_unavailable():
    from scripts.svg_template_eval.run_eval import measure_deck

    result = measure_deck(_MeasureConnection(None, {}), "test-presentation")
    measured = result["content_sizing"][0]
    assert measured["status"] == "unavailable"
    assert measured["card_unused_envelope_share"] is None
    assert measured["reasons"]


def test_fresh_pictures_pin_changes_only_its_policy_and_digest(monkeypatch):
    selection = {"mode": "template-redesign", "template_pin": {"templateId": "test-template"},
                 "style_projection": {"fonts": {}}}
    request = SimpleNamespace(svg_template_pin={}, _svg_template_selection=selection)
    monkeypatch.setattr(generation, "template_package", lambda selected: {
        "font_manifest": {"assets": [{"family": "Test Sans"}]},
    })
    monkeypatch.setattr(generation, "template_composition_guidance", lambda: ["existing guidance"])
    monkeypatch.setattr(get_env_concurrency, "get_svg_template_body_renders_env", lambda: False)
    monkeypatch.setattr(get_env_concurrency, "get_svg_template_content_sizing_env", lambda: False)
    monkeypatch.setattr(get_env_concurrency, "get_svg_template_pictures_env", lambda: False)
    base = {"tokens": {}, "provenance": {}}
    legacy = deepcopy(base)
    generation.apply_template_snapshot(legacy, request)
    assert "template_pictures" not in legacy
    assert "template_pictures_revision" not in legacy

    monkeypatch.setattr(get_env_concurrency, "get_svg_template_pictures_env", lambda: True)
    fresh = deepcopy(base)
    generation.apply_template_snapshot(fresh, request)
    assert fresh["template_pictures"] is True
    assert fresh["template_pictures_revision"] == "svg-template-pictures/v1"
    assert fresh["digest"] != legacy["digest"]
    for key, value in legacy.items():
        if key != "digest":
            assert fresh[key] == value


@pytest.mark.parametrize("component_style", [False, True])
@pytest.mark.parametrize(("flag", "revision", "enabled"), [
    (None, None, False),
    (True, "svg-template-pictures/v1", True),
    ("true", "svg-template-pictures/v1", False),
    (False, "svg-template-pictures/v1", False),
    (True, "future-unknown", False),
    (True, None, False),
])
def test_picture_guidance_uses_only_frozen_pin_and_preserves_component_rules(
        monkeypatch, component_style, flag, revision, enabled):
    from services.svg_templates import component_style as component_module
    from services.svg_templates import picture_policy

    monkeypatch.setattr(generation, "template_selection", lambda _: {"mode": "template-redesign"})
    monkeypatch.setattr(generation, "template_package", lambda _: {})
    monkeypatch.setattr(get_env_concurrency, "get_svg_template_component_style_env", lambda: component_style)
    monkeypatch.setattr(get_env_concurrency, "get_svg_template_body_exemplars_env", lambda: "off")
    monkeypatch.setattr(component_module, "component_style_rules", lambda *a, **k: "legacy-components")
    def unexpected_env():
        raise AssertionError("frozen picture guidance must not read the current environment")
    monkeypatch.setattr(get_env_concurrency, "get_svg_template_pictures_env", unexpected_env)
    calls = []
    def guidance(snapshot, body_bbox=None):
        calls.append(snapshot)
        return "pinned-picture-guidance"
    monkeypatch.setattr(picture_policy, "template_picture_guidance", guidance)
    baseline = {"composition_guidance": []}
    expected = generation.body_author_rules("base", baseline)
    frozen = {**baseline, "template_pictures": flag, "template_pictures_revision": revision}
    actual = generation.body_author_rules("base", frozen)
    assert actual == expected + ("\npinned-picture-guidance" if enabled else "")
    assert len(calls) == int(enabled)
    if component_style:
        assert "legacy-components" in actual
