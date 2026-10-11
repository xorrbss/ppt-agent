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
QUALITY_RECOVERY_POLICY_V2 = {"id": "svg-quality-first", "version": 2}


def validate_quality_recovery_policy(value: object) -> dict | None:
    if value is None:
        return None
    if (not isinstance(value, Mapping) or set(value) != {"id", "version"}
            or value.get("id") != QUALITY_RECOVERY_POLICY["id"]
            or type(value.get("version")) is not int or value["version"] not in (1, 2)):
        raise ValueError("svg_quality_recovery_policy_invalid")
    return dict(value)


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
    policy = request_quality_recovery_policy(request) or dict(QUALITY_RECOVERY_POLICY_V2)
    plan = {"schema": "svg-quality-recovery-plan/v2" if single_page else "svg-quality-recovery-plan/v1",
            "review_round_limit": 1 if single_page else 2,
            "page_attempt_limit": 3, "slide_count": slide_count, "review_group_size": 5,
            "review_call_limit": (1 if single_page else
                2 * (((slide_count + 4) // 5) + 1) if slide_count > 5 else 2)}
    if policy["version"] == 2 and not single_page:
        from services.svg_final_selected_continuation import final_review_control
        physical_offset = plan["review_call_limit"] + (plan["review_round_limit"] if slide_count > 5 else 0)
        plan["final_selected_review_control"] = final_review_control(slide_count, physical_offset=physical_offset)
    request._svg_quality_recovery_plan = plan
    request._svg_quality_recovery_policy = policy
    return plan


def read_quality_recovery_plan(snapshot: Mapping) -> dict | None:
    policy = read_quality_recovery_policy(snapshot)
    if policy is None:
        return None
    plan = snapshot.get("quality_recovery_plan")
    single_page = (isinstance(plan, Mapping)
                   and plan.get("schema") == "svg-quality-recovery-plan/v2"
                   and type(plan.get("slide_count")) is int and plan["slide_count"] == 1)
    expected_keys = {"schema", "review_round_limit", "page_attempt_limit", "slide_count",
                     "review_group_size", "review_call_limit"}
    if policy["version"] == 2 and not single_page:
        expected_keys.add("final_selected_review_control")
    if (not isinstance(plan, Mapping)
            or set(plan) != expected_keys
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
    if policy["version"] == 2 and not single_page:
        from services.svg_final_selected_continuation import verify_review_control
        physical_offset = plan["review_call_limit"] + (plan["review_round_limit"] if plan["slide_count"] > 5 else 0)
        final_control = plan["final_selected_review_control"]
        integer_fields = ("page_count", "local_group_size", "local_group_limit", "global_format_retry_limit",
                          "review_call_limit", "physical_offset", "run_limit")
        if not isinstance(final_control, Mapping) or any(type(final_control.get(key)) is not int for key in integer_fields):
            raise ValueError("svg_quality_recovery_plan_invalid")
        try:
            verify_review_control(final_control, plan["slide_count"], physical_offset)
        except ValueError as error:
            raise ValueError("svg_quality_recovery_plan_invalid") from error
    return dict(plan)


def global_format_retry_control(snapshot: Mapping) -> dict | None:
    """Plan extra format slots outside the immutable authored style snapshot."""
    plan = read_quality_recovery_plan(snapshot)
    if plan is None or plan["slide_count"] <= plan["review_group_size"]:
        return None
    rounds = plan["review_round_limit"]
    calls = plan["review_call_limit"] + rounds
    if calls > plan["slide_count"] and read_quality_recovery_policy(snapshot)["version"] != 2:
        return None
    return {
        "schema": "svg-global-format-retry/v1",
        "slide_count": plan["slide_count"],
        "source_plan_digest": canonical_json_digest(plan, allow_nan=True),
        "base_review_call_limit": plan["review_call_limit"],
        "review_round_limit": rounds,
        "global_retry_limit": 1,
        "phase_group_limit": plan["review_call_limit"] // rounds + 1,
        "review_call_limit": calls,
    }


def read_quality_recovery_execution_plan(snapshot: Mapping, *, manifest=None) -> dict | None:
    """Read only admission-verified recovery control; absent control stays legacy."""
    if manifest is not None and not isinstance(manifest, Mapping):
        raise ValueError("svg_quality_recovery_execution_plan_invalid")
    plan = read_quality_recovery_plan(snapshot)
    if plan is None:
        if isinstance(manifest, Mapping) and "global_format_retry_control" in manifest:
            raise ValueError("svg_quality_recovery_execution_plan_invalid")
        return None
    result = dict(plan)
    result["global_format_retry_limit"] = 0
    result["phase_group_limit"] = plan["review_call_limit"] // plan["review_round_limit"]
    policy = read_quality_recovery_policy(snapshot)
    if policy["version"] == 2:
        from services.svg_final_selected_continuation import verify_review_control
        control = (manifest.get("global_format_retry_control") if isinstance(manifest, Mapping)
                   and "global_format_retry_control" in manifest else global_format_retry_control(snapshot))
        expected = global_format_retry_control(snapshot)
        if canonical_json_digest(control, allow_nan=True) != canonical_json_digest(expected, allow_nan=True):
            raise ValueError("svg_quality_recovery_execution_plan_invalid")
        if control is not None:
            result["review_call_limit"] = control["review_call_limit"]
            result["phase_group_limit"] = control["phase_group_limit"]
            result["global_format_retry_limit"] = control["global_retry_limit"]
        final_control = plan.get("final_selected_review_control")
        if final_control is not None:
            result["final_selected_review"] = verify_review_control(
                final_control, plan["slide_count"], result["review_call_limit"],
            )
            if (isinstance(manifest, Mapping) and manifest.get("final_selected_review_control") is not None
                    and canonical_json_digest(manifest["final_selected_review_control"], allow_nan=True)
                    != canonical_json_digest(final_control, allow_nan=True)):
                raise ValueError("svg_quality_recovery_execution_plan_invalid")
        return result
    if not isinstance(manifest, Mapping) or "global_format_retry_control" not in manifest:
        return result
    control = manifest["global_format_retry_control"]
    expected = global_format_retry_control(snapshot)
    integer_fields = ("slide_count", "base_review_call_limit", "review_round_limit",
                      "global_retry_limit", "phase_group_limit", "review_call_limit")
    if (not isinstance(control, Mapping) or expected is None
            or set(control) != set(expected)
            or any(type(control.get(key)) is not int for key in integer_fields)
            or dict(control) != expected):
        raise ValueError("svg_quality_recovery_execution_plan_invalid")
    result["review_call_limit"] = control["review_call_limit"]
    result["phase_group_limit"] = control["phase_group_limit"]
    result["global_format_retry_limit"] = control["global_retry_limit"]
    if isinstance(manifest, dict) and manifest.get("final_selected_review_control") is not None:
        from services.svg_final_selected_continuation import verify_review_control
        result["final_selected_review"] = verify_review_control(
            manifest["final_selected_review_control"], result["slide_count"],
            result["review_call_limit"],
        )
    return result


def svg_quality_review_admission_budget(request: object, slide_count: int) -> int:
    """Reserve the sealed review plan without changing legacy or global caps."""
    frozen = getattr(request, "_svg_style_snapshot", None)
    if frozen is not None:
        snapshot = frozen
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
        if read_quality_recovery_policy(snapshot)["version"] == 2:
            execution = read_quality_recovery_execution_plan(snapshot)
            final_control = execution.get("final_selected_review")
            return execution["review_call_limit"] + (final_control["review_call_limit"] if final_control else 0)
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
