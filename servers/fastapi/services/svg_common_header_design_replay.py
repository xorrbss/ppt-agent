"""Compare caller intent across fresh seed pinning without rewriting old jobs."""

from copy import deepcopy

from utils.hashing import canonical_json_digest


def header_design_idempotency_payload(payload: dict) -> dict:
    result = deepcopy(payload)
    result.pop("_internal_svg_header_design_plan", None)
    snapshot = result.get("_internal_svg_style_snapshot")
    if isinstance(snapshot, dict) and "common_header_design_plan" in snapshot:
        snapshot.pop("common_header_design_plan")
        snapshot["digest"] = canonical_json_digest(
            {key: value for key, value in snapshot.items() if key != "digest"},
            allow_nan=True,
        )
    return result


def matches_header_design_replay(existing_payload: dict, submitted_payload: dict) -> bool:
    key = "_internal_svg_header_design_plan"
    if key not in existing_payload and key not in submitted_payload:
        return False
    # User instructions/references/canvas and all other public intent stay in
    # this comparison. Only server-chosen execution policy and seed are omitted.
    return header_design_idempotency_payload(existing_payload) == header_design_idempotency_payload(submitted_payload)

