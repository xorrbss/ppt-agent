"""Chart integrity v5 is selected once at fresh SVG admission."""
from copy import deepcopy

import pytest

from models.generate_presentation_request import GeneratePresentationRequest
from services.generation_core.drivers.svg import native_requirements
from services.svg_authoring_profile import select_svg_authoring_profile
from services.svg_generation_admission import pin_svg_style
from utils import get_env_concurrency
from utils.hashing import canonical_json_digest


def _request():
    return GeneratePresentationRequest(
        content="Fixture message", template="authored", strategy="svg",
        common_header_mode="free", n_slides=2,
    )


def _seal(snapshot):
    snapshot["digest"] = canonical_json_digest(
        {key: value for key, value in snapshot.items() if key != "digest"}, allow_nan=True,
    )


@pytest.mark.parametrize("enabled", [False, True])
def test_fresh_admission_selects_only_the_opted_in_chart_revision(monkeypatch, enabled):
    calls = []

    def flag():
        calls.append(True)
        return enabled

    monkeypatch.setattr(get_env_concurrency, "get_svg_native_chart_integrity_v5_env", lambda: False)
    baseline = deepcopy(pin_svg_style(_request()))
    monkeypatch.setattr(get_env_concurrency, "get_svg_native_chart_integrity_v5_env", flag)
    snapshot = pin_svg_style(_request())
    key = native_requirements.SNAPSHOT_REVISION_KEY
    assert calls == [True]
    assert native_requirements.ADMITTED_REQUIREMENTS_REVISION == native_requirements.REQUIREMENTS_VERSION_V4
    assert baseline[key] == native_requirements.REQUIREMENTS_VERSION_V4
    assert snapshot[key] == (native_requirements.REQUIREMENTS_VERSION_V5 if enabled
                             else native_requirements.REQUIREMENTS_VERSION_V4)
    assert {key: value for key, value in snapshot.items() if key not in {native_requirements.SNAPSHOT_REVISION_KEY, "digest"}} == {
        key: value for key, value in baseline.items() if key not in {native_requirements.SNAPSHOT_REVISION_KEY, "digest"}
    }
    assert (snapshot["digest"] != baseline["digest"]) is enabled
    expected = deepcopy(snapshot)
    _seal(expected)
    assert snapshot == expected


@pytest.mark.parametrize("revision", [None, native_requirements.REQUIREMENTS_VERSION_V3,
                                      native_requirements.REQUIREMENTS_VERSION_V4])
@pytest.mark.parametrize("later_flag", ["on", "raise"])
def test_frozen_legacy_snapshot_never_reselects_chart_revision(monkeypatch, revision, later_flag):
    monkeypatch.setattr(get_env_concurrency, "get_svg_native_chart_integrity_v5_env", lambda: False)
    request = _request()
    snapshot = pin_svg_style(request)
    if revision is None:
        snapshot.pop(native_requirements.SNAPSHOT_REVISION_KEY)
    else:
        snapshot[native_requirements.SNAPSHOT_REVISION_KEY] = revision
    _seal(snapshot)
    before = deepcopy(snapshot)
    calls = []

    def later_environment():
        calls.append(True)
        if later_flag == "raise":
            raise AssertionError("Frozen admission read the current chart flag")
        return True

    monkeypatch.setattr(get_env_concurrency, "get_svg_native_chart_integrity_v5_env", later_environment)
    assert pin_svg_style(request) is snapshot
    assert snapshot == before
    assert calls == []


def test_selected_authoring_profile_preserves_absent_chart_pin_without_reading_flag(monkeypatch):
    request = _request()
    select_svg_authoring_profile(request, "current-v4", trusted_internal=True)

    def unexpected_environment():
        raise AssertionError("Selected authoring profile read the current chart flag")

    monkeypatch.setattr(get_env_concurrency, "get_svg_native_chart_integrity_v5_env", unexpected_environment)
    snapshot = pin_svg_style(request)
    assert native_requirements.SNAPSHOT_REVISION_KEY not in snapshot
