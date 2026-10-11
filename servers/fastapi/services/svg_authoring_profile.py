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

QUALITY_RECOVERY_POLICY = {"id": "svg-quality-first", "version": 1}


def validate_quality_recovery_policy(value: object) -> dict | None:
    if value is None:
        return None
    if (not isinstance(value, Mapping) or set(value) != {"id", "version"}
            or value.get("id") != QUALITY_RECOVERY_POLICY["id"]
            or type(value.get("version")) is not int or value["version"] != 1):
        raise ValueError("svg_quality_recovery_policy_invalid")
    return dict(QUALITY_RECOVERY_POLICY)


def request_quality_recovery_policy(request: object) -> dict | None:
    return validate_quality_recovery_policy(getattr(request, "_svg_quality_recovery_policy", None))


def read_quality_recovery_policy(snapshot: Mapping | None) -> dict | None:
    if snapshot is None:
        return None
    policy = validate_quality_recovery_policy(snapshot.get("quality_recovery_policy"))
    if policy is not None and read_authoring_profile(snapshot) != ART_DIRECTION_PROFILE_V8:
        raise ValueError("svg_quality_recovery_profile_invalid")
    return policy


def validate_quality_recovery_agreement(request: object, snapshot: Mapping) -> dict | None:
    frozen = read_quality_recovery_policy(snapshot)
    selected = request_quality_recovery_policy(request)
    if selected is not None and selected != frozen:
        raise ValueError("svg_quality_recovery_policy_conflict")
    request._svg_quality_recovery_policy = frozen
    plan = read_quality_recovery_plan(snapshot) if frozen else None
    selected_plan = getattr(request, "_svg_quality_recovery_plan", None)
    if selected_plan is not None and selected_plan != plan:
        raise ValueError("svg_quality_recovery_plan_conflict")
    request._svg_quality_recovery_plan = plan
    return frozen


def seal_new_quality_recovery_plan(request: object, slide_count: int) -> dict | None:
    """Seal only fresh supported requests; never alter an existing admission."""
    frozen = getattr(request, "_svg_style_snapshot", None)
    if frozen is not None:
        plan = read_quality_recovery_plan(frozen)
        if plan and plan["slide_count"] != slide_count:
            raise ValueError("svg_quality_recovery_plan_conflict")
        return plan
    if (getattr(request, "_svg_authoring_profile_selected", False)
            or request_authoring_profile(request) is not None
            or getattr(request, "_svg_stage_plan", None) is not None
            or getattr(request, "source_mode", None) == "raster_reconstruction"
            or type(slide_count) is not int or slide_count < 1):
        return None
    single_page = slide_count == 1
    plan = {"schema": "svg-quality-recovery-plan/v2" if single_page else "svg-quality-recovery-plan/v1",
            "review_round_limit": 1 if single_page else 2,
            "page_attempt_limit": 3, "slide_count": slide_count, "review_group_size": 5,
            "review_call_limit": (1 if single_page else
                2 * (((slide_count + 4) // 5) + 1) if slide_count > 5 else 2)}
    request._svg_quality_recovery_plan = plan
    request._svg_quality_recovery_policy = dict(QUALITY_RECOVERY_POLICY)
    return plan


def read_quality_recovery_plan(snapshot: Mapping) -> dict | None:
    if read_quality_recovery_policy(snapshot) is None:
        return None
    plan = snapshot.get("quality_recovery_plan")
    single_page = (isinstance(plan, Mapping)
                   and plan.get("schema") == "svg-quality-recovery-plan/v2"
                   and type(plan.get("slide_count")) is int and plan["slide_count"] == 1)
    if (not isinstance(plan, Mapping)
            or set(plan) != {"schema", "review_round_limit", "page_attempt_limit", "slide_count",
                            "review_group_size", "review_call_limit"}
            or (not single_page and plan.get("schema") != "svg-quality-recovery-plan/v1")
            or type(plan.get("slide_count")) is not int or plan["slide_count"] < (1 if single_page else 2)
            or type(plan.get("review_round_limit")) is not int
            or plan["review_round_limit"] != (1 if single_page else 2)
            or type(plan.get("page_attempt_limit")) is not int or plan["page_attempt_limit"] not in (2, 3)
            or type(plan.get("review_group_size")) is not int or plan["review_group_size"] != 5
            or type(plan.get("review_call_limit")) is not int
            or plan["review_call_limit"] != (1 if single_page else
                                           2 * (((plan["slide_count"] + 4) // 5) + 1)
                                           if plan["slide_count"] > 5 else 2)
            or plan["review_call_limit"] > plan["slide_count"]):
        raise ValueError("svg_quality_recovery_plan_invalid")
    return dict(plan)


def svg_quality_review_admission_budget(request: object, slide_count: int) -> int:
    """Reserve the sealed review plan without changing legacy or global caps."""
    frozen = getattr(request, "_svg_style_snapshot", None)
    if frozen is not None:
        plan = read_quality_recovery_plan(frozen)
    else:
        plan = getattr(request, "_svg_quality_recovery_plan", None)
        if plan is not None:
            if request_quality_recovery_policy(request) is None:
                raise ValueError("svg_quality_recovery_plan_invalid")
            # Validate the same immutable representation admission will freeze.
            snapshot = {"authoring_profile": dict(ART_DIRECTION_PROFILE_V8),
                        "quality_recovery_policy": request_quality_recovery_policy(request),
                        "quality_recovery_plan": dict(plan)}
            snapshot["digest"] = canonical_json_digest(snapshot, allow_nan=True)
            plan = read_quality_recovery_plan(snapshot)
    if plan is not None:
        if plan["slide_count"] != slide_count:
            raise ValueError("svg_quality_recovery_plan_conflict")
        return plan["review_call_limit"]
    return slide_count if getattr(request, "vision_qa", False) else 0


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
