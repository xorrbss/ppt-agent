"""Per-slide record, deck roll-up arithmetic, and the three output files."""

from __future__ import annotations

import json
import statistics
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


@dataclass(frozen=True, slots=True)
class SlideMetrics:
    deck_id: str
    deck_label: str
    source: str
    slide_index: int
    html_bytes: int
    uses_library: bool
    image_count: int
    inline_image_bytes: int
    has_chart: bool
    chart_evidence: tuple[str, ...]
    has_table: bool
    css_rule_count: int
    distinct_block_count: int
    max_nesting_depth: int
    structure_source: str  # "browser_metrics" | "html_fallback"
    ink_fill: float
    bottom_dead_band: float
    geometry_defects: dict[str, Any]
    telemetry_status: str
    render_error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["chart_evidence"] = list(self.chart_evidence)
        return payload


@dataclass(frozen=True, slots=True)
class DeckReport:
    deck_id: str
    deck_label: str
    source: str
    slide_count: int
    metrics: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "deck_id": self.deck_id,
            "deck_label": self.deck_label,
            "source": self.source,
            "slide_count": self.slide_count,
            **self.metrics,
        }


_NUMERIC_FIELDS = (
    "html_bytes",
    "image_count",
    "inline_image_bytes",
    "css_rule_count",
    "distinct_block_count",
    "max_nesting_depth",
    "ink_fill",
    "bottom_dead_band",
)
_SHARE_FIELDS = ("uses_library", "has_chart", "has_table")


def _mean(values: list[float]) -> float | None:
    return round(statistics.mean(values), 4) if values else None


def _median(values: list[float]) -> float | None:
    return round(statistics.median(values), 4) if values else None


def rollup_deck(slides: list[SlideMetrics]) -> dict[str, Any]:
    """Deck-level arithmetic: mean/median per numeric metric, boolean shares,
    and total geometry defects.

    Pulled out of ``build_deck_reports`` so it has an obvious, directly
    testable seam for synthetic slide lists.
    """

    metrics: dict[str, Any] = {}
    for field_name in _NUMERIC_FIELDS:
        values = [float(getattr(slide, field_name)) for slide in slides]
        metrics[f"mean_{field_name}"] = _mean(values)
        metrics[f"median_{field_name}"] = _median(values)
    for field_name in _SHARE_FIELDS:
        hits = sum(1 for slide in slides if getattr(slide, field_name))
        metrics[f"share_{field_name}"] = (
            round(hits / len(slides), 4) if slides else None
        )
    metrics["share_with_image"] = (
        round(sum(1 for slide in slides if slide.image_count > 0) / len(slides), 4)
        if slides
        else None
    )
    total_defects = sum(slide.geometry_defects.get("total", 0) for slide in slides)
    metrics["total_geometry_defects"] = total_defects
    metrics["mean_geometry_defects"] = (
        round(total_defects / len(slides), 4) if slides else None
    )
    render_failures = sum(1 for slide in slides if slide.render_error)
    metrics["render_failures"] = render_failures
    metrics["telemetry_unavailable"] = sum(
        1 for slide in slides if slide.telemetry_status != "available"
    )
    return metrics


def build_deck_reports(all_slides: list[SlideMetrics]) -> list[DeckReport]:
    by_deck: dict[str, list[SlideMetrics]] = {}
    order: list[str] = []
    for slide in all_slides:
        if slide.deck_id not in by_deck:
            by_deck[slide.deck_id] = []
            order.append(slide.deck_id)
        by_deck[slide.deck_id].append(slide)

    reports: list[DeckReport] = []
    for deck_id in order:
        slides = by_deck[deck_id]
        reports.append(
            DeckReport(
                deck_id=deck_id,
                deck_label=slides[0].deck_label,
                source=slides[0].source,
                slide_count=len(slides),
                metrics=rollup_deck(slides),
            )
        )
    return reports


def write_slides_jsonl(path: Path, all_slides: list[SlideMetrics]) -> None:
    with path.open("w", encoding="utf-8") as handle:
        for slide in all_slides:
            handle.write(json.dumps(slide.to_dict(), ensure_ascii=False))
            handle.write("\n")


def write_deck_json(path: Path, decks: list[DeckReport]) -> None:
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "decks": [deck.to_dict() for deck in decks],
    }
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def _fmt(value: Any) -> str:
    if value is None:
        return "n/a"
    if isinstance(value, float):
        return f"{value:.3f}"
    return str(value)


def render_summary_md(decks: list[DeckReport]) -> str:
    lines = ["# Authored deck richness comparison", ""]
    lines.append(
        "| deck | source | slides | chart% | image% | library% | "
        "img/slide | inline KB/slide | blocks | nesting | css rules | html KB |"
    )
    lines.append("|---|---|---|---|---|---|---|---|---|---|---|---|")
    for deck in decks:
        m = deck.metrics
        lines.append(
            "| {deck} | {source} | {n} | {chart} | {image} | {lib} | "
            "{img} | {kb} | {blocks} | {nest} | {css} | {html_kb} |".format(
                deck=deck.deck_label,
                source=deck.source,
                n=deck.slide_count,
                chart=_fmt(m.get("share_has_chart")),
                image=_fmt(m.get("share_with_image")),
                lib=_fmt(m.get("share_uses_library")),
                img=_fmt(m.get("mean_image_count")),
                kb=_fmt(
                    None
                    if m.get("mean_inline_image_bytes") is None
                    else m["mean_inline_image_bytes"] / 1024
                ),
                blocks=_fmt(m.get("mean_distinct_block_count")),
                nest=_fmt(m.get("mean_max_nesting_depth")),
                css=_fmt(m.get("mean_css_rule_count")),
                html_kb=_fmt(
                    None
                    if m.get("mean_html_bytes") is None
                    else m["mean_html_bytes"] / 1024
                ),
            )
        )

    lines.append("")
    lines.append(
        "| deck | ink fill | bottom dead band | geometry defects (total) | "
        "defects/slide | high/med/low | render failures | telemetry n/a |"
    )
    lines.append("|---|---|---|---|---|---|---|---|")
    for deck in decks:
        m = deck.metrics
        # Per-severity counts are per-slide detail (see slides.jsonl); this
        # table stays at the deck-comparison altitude the brief asked for.
        lines.append(
            "| {deck} | {ink} | {dead} | {defects} | {rate} | {sev} | "
            "{fail} | {tel} |".format(
                deck=deck.deck_label,
                ink=_fmt(m.get("mean_ink_fill")),
                dead=_fmt(m.get("mean_bottom_dead_band")),
                defects=_fmt(m.get("total_geometry_defects")),
                rate=_fmt(m.get("mean_geometry_defects")),
                sev="see slides.jsonl",
                fail=_fmt(m.get("render_failures")),
                tel=_fmt(m.get("telemetry_unavailable")),
            )
        )
    return "\n".join(lines) + "\n"


def write_summary_md(path: Path, decks: list[DeckReport]) -> None:
    path.write_text(render_summary_md(decks), encoding="utf-8")
