"""E4 additions to the v2 attempt-timing receipt.

``docs/generation-programme-master-design-20260904.md`` section 8 (E4) and
decision D13. Kept out of ``services/authored_generation_timing_receipt.py``
because that module is already well over the file-size policy target
(``docs/file-size-policy.md``); every function here is a small, pure
validator or serializer the parent module calls with a few lines rather than
carrying inline.

Fields covered:

* ``wallMillis`` per stage, next to the existing summed ``durationMillis``
  (D13): the parent module's ``chromiumReview`` merges ``render`` and
  ``quality`` source-stage observations by summing their durations (work),
  which the design doc's evidence showed being misread as wall time. The
  span helpers here compute the real start-to-end window instead.
* ``providerRateProfile`` -- the batch policy's resolved rate profile name
  ("token" or "api", ``services/authored_provider_rate_profile.py``) in
  force for the attempt.
* ``normalizations`` -- the deck-wide list of A2 admission normalisation
  firings (each ``{"rule", "detail", "count"}``,
  ``utils/authored_html_normalize.py``), merged across every slide's
  ``validate_authored_slide_html`` calls.
* ``qualityLoop.variant`` -- the best-of-N winner's variant id
  (``services/authored_figure_best_of.py`` -> ``FigureSelection.to_dict()``
  -> ``variant_chosen``) for a representative slide the quality loop ran on.

``fitLadder.trail`` (A4, the deterministic fit ladder) is a RESERVED key:
no field or validator exists for it anywhere in this pair of modules on
purpose, so a receipt never fabricates a ladder trail before A4 lands. Add
it here the same way once it does.

All three fields are appended to a receipt's serialized form only when set
(see ``optional_top_level_fields``): every field the v2 receipt has carried
since its introduction is unconditionally present, so its digest is bound to
a fixed key set. These three did not exist when receipts already on disk
were sealed, so adding them unconditionally would change ``to_dict()`` for
every historical receipt and break its digest on the next load.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence

from services.authored_provider_rate_profile import PROFILE_NAMES

#: The optional key added to one stage's ``"observed"`` dict.
STAGE_WALL_KEY = "wallMillis"

_NORMALIZATION_ENTRY_KEYS = frozenset({"rule", "detail", "count"})


def stage_has_wall(raw: Mapping[str, object]) -> bool:
    """Whether one raw stage dict carries the optional wall span."""
    return STAGE_WALL_KEY in raw


def stage_wall_millis(
    raw: Mapping[str, object],
    *,
    stage_name: str,
    bounded_timing_ms: Callable[[object, str], int],
) -> int | None:
    """Validate and return one stage's optional ``wallMillis``, or ``None``."""
    if not stage_has_wall(raw):
        return None
    return bounded_timing_ms(raw.get(STAGE_WALL_KEY), f"{stage_name}_wall")


def merged_stage_wall_millis(start: int, end: int) -> int:
    """The real span of a (possibly merged) stage window, never negative."""
    return max(0, end - start)


def provider_rate_profile(value: object) -> str | None:
    """Validate the resolved rate profile name, or ``None`` when absent."""
    if value is None:
        return None
    if value not in PROFILE_NAMES:
        raise ValueError("generation_attempt_v2_provider_rate_profile_invalid")
    return str(value)


def normalization_entries(value: object) -> tuple[dict[str, object], ...]:
    """Validate the merged-across-the-deck A2 normalisation firings."""
    if value is None:
        return ()
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        raise ValueError("generation_attempt_v2_normalizations_invalid")
    output: list[dict[str, object]] = []
    for entry in value:
        if not isinstance(entry, Mapping) or set(entry) != _NORMALIZATION_ENTRY_KEYS:
            raise ValueError("generation_attempt_v2_normalizations_invalid")
        rule = entry.get("rule")
        detail = entry.get("detail")
        count = entry.get("count")
        if (
            not isinstance(rule, str)
            or not rule.strip()
            or len(rule) > 160
            or not isinstance(detail, str)
            or len(detail) > 500
            or isinstance(count, bool)
            or not isinstance(count, int)
            or count < 1
        ):
            raise ValueError("generation_attempt_v2_normalizations_invalid")
        output.append({"rule": rule, "detail": detail, "count": count})
    return tuple(output)


def quality_loop_variant_field(
    value: object,
    *,
    optional_reason: Callable[[object, str], str | None],
) -> str | None:
    """Validate a raw ``{"variant": ...}`` mapping, or ``None`` when absent."""
    if value is None:
        return None
    if not isinstance(value, Mapping) or set(value) != {"variant"}:
        raise ValueError("generation_attempt_v2_quality_loop_invalid")
    return optional_reason(value.get("variant"), "quality_loop_variant")


def optional_top_level_fields(
    *,
    provider_rate_profile: str | None,
    normalizations: Sequence[Mapping[str, object]],
    quality_loop_variant: str | None,
) -> dict[str, object]:
    """The receipt-level keys to splice into ``to_dict()``, only when set."""
    fields: dict[str, object] = {}
    if provider_rate_profile is not None:
        fields["providerRateProfile"] = provider_rate_profile
    if normalizations:
        fields["normalizations"] = [dict(entry) for entry in normalizations]
    if quality_loop_variant is not None:
        fields["qualityLoop"] = {"variant": quality_loop_variant}
    return fields


__all__ = [
    "STAGE_WALL_KEY",
    "merged_stage_wall_millis",
    "normalization_entries",
    "optional_top_level_fields",
    "provider_rate_profile",
    "quality_loop_variant_field",
    "stage_has_wall",
    "stage_wall_millis",
]
