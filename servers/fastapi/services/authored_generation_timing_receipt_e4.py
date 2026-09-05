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
* ``fitSummary`` -- M1 of
  ``docs/authored-fit-magnitude-measurement-design-20260904.md`` (section 3.4):
  one entry per slide that failed the composition safety gate, carrying the
  ``DomQualityReport.fit_summary`` the gate produced for it. It exists so a
  failed deck's misses can be read from the timing receipt without opening the
  slide-quality receipt. A succeeded attempt has no entries and the key is
  absent, exactly like the three fields above.

* ``flaggedSlides`` -- D7 (master design section A4): one entry per slide the
  deck published with a quality flag after its one automatic remake stayed
  terminal (``services/authored_terminal_slide_fallback.py``), each
  ``{"index", "reason", "remakeAttempted", "remakeOutcome"}``. It exists so a
  benchmark can tell a clean publish from a flagged one. A clean publish has
  no entries and the key is absent.

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

#: The fit-summary keys ``DomQualityReport.fit_summary`` carries (M1, 3.3),
#: plus the slide the entry belongs to.
_FIT_SUMMARY_ENTRY_KEYS = frozenset(
    {
        "slideIndex",
        "worstOverflowRatio",
        "worstOffCanvasPx",
        "overflowKinds",
        "contentBboxFill",
    }
)
#: ``fit_summary`` is snake_case (it is a DOM-gate value, not a receipt value);
#: the receipt is camelCase throughout. Translate once, here.
_FIT_SUMMARY_FIELDS = (
    ("worst_overflow_ratio", "worstOverflowRatio"),
    ("worst_off_canvas_px", "worstOffCanvasPx"),
    ("content_bbox_fill", "contentBboxFill"),
)


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


def fit_summary_entries(value: object) -> tuple[dict[str, object], ...]:
    """Validate the per-slide fit summaries of the gate-failed slides (M1).

    Accepts either the already-serialized receipt form (camelCase entries with
    ``slideIndex``) or the gate's own ``{slide_index: fit_summary}`` mapping,
    because the caller reads the latter straight off the failing request. The
    result is deterministic in slide order so the receipt digest is stable.
    """

    if value is None:
        return ()
    if isinstance(value, Mapping):
        pairs: list[tuple[object, object]] = list(value.items())
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        pairs = []
        for entry in value:
            if not isinstance(entry, Mapping) or not set(entry) <= _FIT_SUMMARY_ENTRY_KEYS:
                raise ValueError("generation_attempt_v2_fit_summary_invalid")
            pairs.append((entry.get("slideIndex"), entry))
    else:
        raise ValueError("generation_attempt_v2_fit_summary_invalid")
    output: list[dict[str, object]] = []
    for raw_index, summary in pairs:
        index = _fit_summary_slide_index(raw_index)
        if not isinstance(summary, Mapping):
            raise ValueError("generation_attempt_v2_fit_summary_invalid")
        entry: dict[str, object] = {"slideIndex": index}
        for source, target in _FIT_SUMMARY_FIELDS:
            number = summary.get(source, summary.get(target))
            if isinstance(number, bool) or not isinstance(number, (int, float)):
                raise ValueError("generation_attempt_v2_fit_summary_invalid")
            entry[target] = round(float(number), 4)
        entry["overflowKinds"] = _fit_summary_kinds(
            summary.get("overflow_kinds", summary.get("overflowKinds"))
        )
        output.append(entry)
    output.sort(key=lambda entry: entry["slideIndex"])
    if len({entry["slideIndex"] for entry in output}) != len(output):
        raise ValueError("generation_attempt_v2_fit_summary_invalid")
    return tuple(output)


def _fit_summary_slide_index(value: object) -> int:
    if isinstance(value, str) and value.strip().lstrip("-").isdigit():
        value = int(value)
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError("generation_attempt_v2_fit_summary_invalid")
    return value


def _fit_summary_kinds(value: object) -> dict[str, int]:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise ValueError("generation_attempt_v2_fit_summary_invalid")
    kinds: dict[str, int] = {}
    for raw_kind, raw_count in value.items():
        kind = str(raw_kind)
        if (
            not kind
            or len(kind) > 32
            or isinstance(raw_count, bool)
            or not isinstance(raw_count, int)
            or raw_count < 1
        ):
            raise ValueError("generation_attempt_v2_fit_summary_invalid")
        kinds[kind] = raw_count
    return dict(sorted(kinds.items()))


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


_FLAGGED_SLIDE_KEYS = frozenset(
    {"index", "reason", "remakeAttempted", "remakeOutcome"}
)
#: Only a slide declared terminal by the durable Scene repair claim knows
#: whether the same digest and error came back, so this key is present on
#: those entries and absent everywhere else.
_FLAGGED_SLIDE_OPTIONAL_KEYS = frozenset({"repeated"})


def flagged_slide_entries(value: object) -> tuple[dict[str, object], ...]:
    """Validate the D7 terminal-slide flags a published deck carried.

    One entry per slide that was still terminal after the bounded repair, so a
    benchmark can tell a clean publish from a flagged one
    (``services/authored_terminal_slide_fallback.py``).
    """
    if value is None:
        return ()
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        raise ValueError("generation_attempt_v2_flagged_slides_invalid")
    output: list[dict[str, object]] = []
    for entry in value:
        if not isinstance(entry, Mapping) or not (
            set(entry) >= _FLAGGED_SLIDE_KEYS
            and set(entry) <= _FLAGGED_SLIDE_KEYS | _FLAGGED_SLIDE_OPTIONAL_KEYS
        ):
            raise ValueError("generation_attempt_v2_flagged_slides_invalid")
        index = entry.get("index")
        reason = entry.get("reason")
        attempted = entry.get("remakeAttempted")
        outcome = entry.get("remakeOutcome")
        repeated = entry.get("repeated", False)
        if (
            not isinstance(repeated, bool)
            or isinstance(index, bool)
            or not isinstance(index, int)
            or index < 0
            or not isinstance(reason, str)
            or not reason.strip()
            or len(reason) > 160
            or not isinstance(attempted, bool)
            or not isinstance(outcome, str)
            or not outcome.strip()
            or len(outcome) > 64
        ):
            raise ValueError("generation_attempt_v2_flagged_slides_invalid")
        record: dict[str, object] = {
            "index": index,
            "reason": reason,
            "remakeAttempted": attempted,
            "remakeOutcome": outcome,
        }
        if "repeated" in entry:
            record["repeated"] = bool(entry["repeated"])
        output.append(record)
    return tuple(output)


def optional_top_level_fields(
    *,
    provider_rate_profile: str | None,
    normalizations: Sequence[Mapping[str, object]],
    quality_loop_variant: str | None,
    flagged_slides: Sequence[Mapping[str, object]] = (),
    fit_summaries: Sequence[Mapping[str, object]] = (),
) -> dict[str, object]:
    """The receipt-level keys to splice into ``to_dict()``, only when set."""
    fields: dict[str, object] = {}
    if provider_rate_profile is not None:
        fields["providerRateProfile"] = provider_rate_profile
    if normalizations:
        fields["normalizations"] = [dict(entry) for entry in normalizations]
    if quality_loop_variant is not None:
        fields["qualityLoop"] = {"variant": quality_loop_variant}
    if flagged_slides:
        fields["flaggedSlides"] = [dict(entry) for entry in flagged_slides]
    if fit_summaries:
        fields["fitSummary"] = [dict(entry) for entry in fit_summaries]
    return fields


__all__ = [
    "STAGE_WALL_KEY",
    "fit_summary_entries",
    "flagged_slide_entries",
    "merged_stage_wall_millis",
    "normalization_entries",
    "optional_top_level_fields",
    "provider_rate_profile",
    "quality_loop_variant_field",
    "stage_has_wall",
    "stage_wall_millis",
]
