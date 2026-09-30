"""Request-scoped content-brief experiment, independent of style identity."""

from collections.abc import Mapping
from typing import Literal

BenchSpeedArm = Literal["baseline", "brief_off"]
INTERNAL_BENCH_SPEED_ARM_KEY = "_internal_bench_speed_arm"


def validate_speed_arm(value: object) -> BenchSpeedArm | None:
    if value is None:
        return None
    if value == "baseline":
        return "baseline"
    if value == "brief_off":
        return "brief_off"
    raise ValueError("bench_speed_arm_invalid")


def resolve_speed_arm(request: object) -> BenchSpeedArm | None:
    """A restored private pin wins over the admission-only public field."""
    pinned = getattr(request, "_bench_speed_arm", None)
    return validate_speed_arm(pinned if pinned is not None else getattr(request, "bench_speed_arm", None))


def content_brief_enabled(request: object, configured_enabled: bool) -> bool:
    return resolve_speed_arm(request) != "brief_off" and configured_enabled


def speed_receipt_kwargs(request: object) -> dict:
    arm = resolve_speed_arm(request)
    return {"provenance": {"benchSpeedArm": arm}} if arm is not None else {}


def speed_provenance(value: object) -> dict[str, object] | None:
    """Validate content-free provenance when sealing and reading receipts."""
    if value is None:
        return None
    if not isinstance(value, Mapping) or set(value) != {"benchSpeedArm"}:
        raise ValueError("generation_attempt_speed_provenance_invalid")
    arm = validate_speed_arm(value["benchSpeedArm"])
    if arm is None:
        raise ValueError("generation_attempt_speed_provenance_invalid")
    return {"benchSpeedArm": arm}
