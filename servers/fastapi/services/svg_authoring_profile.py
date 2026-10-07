"""Trusted SVG authoring selection and immutable snapshot agreement."""

from collections.abc import Mapping
from typing import Literal, TypedDict

from utils.hashing import canonical_json_digest

INTERNAL_SVG_AUTHORING_PROFILE_KEY = "_internal_svg_authoring_profile"


class SvgAuthoringProfile(TypedDict):
    id: Literal["art-direction-v1"]
    version: Literal[1, 2, 3, 4, 5, 6, 7, 8]


ART_DIRECTION_PROFILE: SvgAuthoringProfile = {"id": "art-direction-v1", "version": 1}
ART_DIRECTION_PROFILE_V2: SvgAuthoringProfile = {"id": "art-direction-v1", "version": 2}
ART_DIRECTION_PROFILE_V3: SvgAuthoringProfile = {"id": "art-direction-v1", "version": 3}
ART_DIRECTION_PROFILE_V4: SvgAuthoringProfile = {"id": "art-direction-v1", "version": 4}
ART_DIRECTION_PROFILE_V5: SvgAuthoringProfile = {"id": "art-direction-v1", "version": 5}
ART_DIRECTION_PROFILE_V6: SvgAuthoringProfile = {"id": "art-direction-v1", "version": 6}
ART_DIRECTION_PROFILE_V7: SvgAuthoringProfile = {"id": "art-direction-v1", "version": 7}
ART_DIRECTION_PROFILE_V8: SvgAuthoringProfile = {"id": "art-direction-v1", "version": 8}


def validate_authoring_profile(value: object) -> SvgAuthoringProfile | None:
    if value is None or value == "current-v4":
        return None
    if value == "art-direction-v1":
        return dict(ART_DIRECTION_PROFILE)
    if value == "art-direction-v1@2":
        return dict(ART_DIRECTION_PROFILE_V2)
    if value == "art-direction-v1@3":
        return dict(ART_DIRECTION_PROFILE_V3)
    if value == "art-direction-v1@4":
        return dict(ART_DIRECTION_PROFILE_V4)
    if value == "art-direction-v1@5":
        return dict(ART_DIRECTION_PROFILE_V5)
    if value == "art-direction-v1@6":
        return dict(ART_DIRECTION_PROFILE_V6)
    if value == "art-direction-v1@7":
        return dict(ART_DIRECTION_PROFILE_V7)
    if value == "art-direction-v1@8":
        return dict(ART_DIRECTION_PROFILE_V8)
    if (
        isinstance(value, Mapping)
        and set(value) == {"id", "version"}
        and value.get("id") == ART_DIRECTION_PROFILE["id"]
        and type(value.get("version")) is int
        and value["version"] in {1, 2, 3, 4, 5, 6, 7, 8}
    ):
        return dict({1: ART_DIRECTION_PROFILE, 2: ART_DIRECTION_PROFILE_V2, 3: ART_DIRECTION_PROFILE_V3, 4: ART_DIRECTION_PROFILE_V4, 5: ART_DIRECTION_PROFILE_V5, 6: ART_DIRECTION_PROFILE_V6, 7: ART_DIRECTION_PROFILE_V7, 8: ART_DIRECTION_PROFILE_V8}[value["version"]])
    raise ValueError("svg_authoring_profile_invalid")


def select_svg_authoring_profile(request: object, profile_id: str, *, trusted_internal: bool = False) -> None:
    """Admission-only Python interface; callers must establish runner trust."""
    from utils.get_env_canvas_bench import is_canvas_bench_enabled

    profile = validate_authoring_profile(profile_id)
    if profile is not None and not trusted_internal and not is_canvas_bench_enabled():
        raise ValueError("bench_field_disabled")
    request._svg_authoring_profile = profile_id
    request._svg_authoring_profile_selected = True


def request_authoring_profile(request: object) -> SvgAuthoringProfile | None:
    return validate_authoring_profile(getattr(request, "_svg_authoring_profile", None))


def new_svg_admission_profile(request: object) -> dict[str, object] | None:
    profile = request_authoring_profile(request)
    if profile is not None or getattr(request, "_svg_authoring_profile_selected", False):
        return profile
    profile = validate_authoring_profile("art-direction-v1@8")
    setattr(request, "_svg_authoring_profile", profile)
    return profile


def read_authoring_profile(snapshot: Mapping) -> SvgAuthoringProfile | None:
    value = snapshot.get("authoring_profile")
    if value is None:
        return None
    if not isinstance(value, Mapping):
        raise ValueError("svg_authoring_profile_invalid")
    profile = validate_authoring_profile(value)
    expected = canonical_json_digest(
        {key: item for key, item in snapshot.items() if key != "digest"}, allow_nan=True,
    )
    if snapshot.get("digest") != expected:
        raise ValueError("svg_authoring_profile_snapshot_digest_mismatch")
    return profile


def validate_profile_agreement(request: object, snapshot: Mapping) -> SvgAuthoringProfile | None:
    """The frozen snapshot owns replay; explicit new choices cannot replace it."""
    frozen = read_authoring_profile(snapshot)
    selected = getattr(request, "_svg_authoring_profile", None)
    if selected is not None and validate_authoring_profile(selected) != frozen:
        raise ValueError("svg_authoring_profile_conflict")
    if selected is None and frozen is not None:
        request._svg_authoring_profile = frozen
    return frozen
