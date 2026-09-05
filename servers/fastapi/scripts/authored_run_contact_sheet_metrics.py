"""Per-slide metric extraction and per-run rollups for the authored-run
contact sheet.

Reads the browser-metrics JSON emitted alongside each slide PNG (produced by
the authored-slide render/measure path, e.g. ``utils/slide_capture.py``) and
reduces it to the handful of numbers the coordinator actually looks at when
comparing runs: how much owned text survived, how many distinct elements own
it, how small the smallest font got, whether anything got clipped, and a
best-effort image count. Also renders the reduced numbers as the two kinds of
markdown table ``metrics.md`` is made of.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SlideMetricsSummary:
    """Reduced per-slide numbers. ``missing`` is True when there was no
    ``sNN.metrics.json`` next to the PNG at all (e.g. an older run that only
    has renders)."""

    text_owners: int = 0
    visible_chars: int = 0
    min_font_px: float | None = None
    image_count: int = 0
    clipped_count: int = 0
    missing: bool = True


def extract_slide_metrics(metrics: dict | None) -> SlideMetricsSummary:
    """Reduce one slide's raw browser-metrics dict (or ``None`` if the
    ``.metrics.json`` file was absent) to a :class:`SlideMetricsSummary`."""
    if not metrics:
        return SlideMetricsSummary(missing=True)

    elements = metrics.get("elements") or []
    text_owners = 0
    visible_chars = 0
    min_font_px: float | None = None
    tagged_images = 0
    substring_images = 0
    clipped_count = 0

    for element in elements:
        if not isinstance(element, dict):
            continue

        if element.get("has_owned_text"):
            text_owners += 1
            visible_chars += len(element.get("owned_text") or "")

        font_size = element.get("font_size")
        if isinstance(font_size, (int, float)):
            min_font_px = font_size if min_font_px is None else min(min_font_px, font_size)

        tag = (element.get("tag") or "").lower()
        if tag == "img":
            tagged_images += 1
        elif "<img" in (element.get("owned_text") or "") or "<img" in (
            element.get("rendered_text") or ""
        ):
            # Fallback for metrics dumps that don't isolate <img> as its own
            # element -- e.g. it shows up inline inside a captured text run.
            substring_images += 1

        if element.get("clipped"):
            clipped_count += 1

    return SlideMetricsSummary(
        text_owners=text_owners,
        visible_chars=visible_chars,
        min_font_px=min_font_px,
        image_count=tagged_images if tagged_images else substring_images,
        clipped_count=clipped_count,
        missing=False,
    )


def median(values: list[float]) -> float | None:
    """Standard median; ``None`` on an empty list."""
    if not values:
        return None
    ordered = sorted(values)
    n = len(ordered)
    mid = n // 2
    if n % 2:
        return float(ordered[mid])
    return (ordered[mid - 1] + ordered[mid]) / 2.0


@dataclass(frozen=True)
class RunSummary:
    label: str
    slide_count: int
    median_text_owners: float | None
    median_visible_chars: float | None
    min_font_px: float | None
    image_count: int
    slides_with_clipped: int


def summarize_run(label: str, per_slide: dict[int, SlideMetricsSummary]) -> RunSummary:
    """Roll a run's per-slide summaries up into one :class:`RunSummary`.
    ``slide_count`` counts every discovered slide index (gaps included via
    the caller); the medians/min-font/clip counts only look at slides that
    actually had a metrics file."""
    present = [s for s in per_slide.values() if not s.missing]
    fonts = [s.min_font_px for s in present if s.min_font_px is not None]
    return RunSummary(
        label=label,
        slide_count=len(per_slide),
        median_text_owners=median([float(s.text_owners) for s in present]),
        median_visible_chars=median([float(s.visible_chars) for s in present]),
        min_font_px=min(fonts) if fonts else None,
        image_count=sum(s.image_count for s in present),
        slides_with_clipped=sum(1 for s in present if s.clipped_count > 0),
    )


def _fmt(value: float | int | None) -> str:
    if value is None:
        return "-"
    if isinstance(value, float):
        return str(int(value)) if value == int(value) else f"{value:.1f}"
    return str(value)


def render_summary_table(runs: list[RunSummary]) -> str:
    lines = [
        "| run | slides | owners/slide (median) | chars/slide (median) "
        "| min font px | image count | slides w/ clipped |",
        "|---|---|---|---|---|---|---|",
    ]
    for r in runs:
        lines.append(
            f"| {r.label} | {r.slide_count} | {_fmt(r.median_text_owners)} | "
            f"{_fmt(r.median_visible_chars)} | {_fmt(r.min_font_px)} | "
            f"{r.image_count} | {r.slides_with_clipped} |"
        )
    return "\n".join(lines)


def render_per_slide_table(
    label: str, per_slide: dict[int, SlideMetricsSummary], num_rows: int
) -> str:
    lines = [f"### {label}", "", "| slide | chars | owners | clipped |", "|---|---|---|---|"]
    for idx in range(num_rows):
        s = per_slide.get(idx)
        if s is None:
            lines.append(f"| s{idx:02d} | (no slide) | (no slide) | (no slide) |")
        elif s.missing:
            lines.append(f"| s{idx:02d} | (no metrics) | (no metrics) | (no metrics) |")
        else:
            lines.append(f"| s{idx:02d} | {s.visible_chars} | {s.text_owners} | {s.clipped_count} |")
    return "\n".join(lines)


def render_metrics_markdown(
    runs: list[RunSummary],
    per_slide_by_label: dict[str, dict[int, SlideMetricsSummary]],
    num_rows: int,
) -> str:
    parts = ["# Authored run contact sheet metrics", "", render_summary_table(runs), ""]
    for r in runs:
        parts.append(render_per_slide_table(r.label, per_slide_by_label.get(r.label, {}), num_rows))
        parts.append("")
    return "\n".join(parts)
