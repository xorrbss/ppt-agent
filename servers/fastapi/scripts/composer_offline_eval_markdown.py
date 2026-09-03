"""Markdown rendering of the Composer offline harness ``report.json``."""

from __future__ import annotations

from typing import Any, Mapping

from scripts.composer_offline_eval_report import acceptance

STAGE_KEYS = ("extractMs", "classifyMs", "fitTotalMs", "readbackMs", "sheetsMs", "totalMs")


def _fmt(value: Any, digits: int = 1) -> str:
    if value is None:
        return "-"
    if isinstance(value, float):
        return f"{value:.{digits}f}"
    return str(value)


def _pct(share: Any) -> str:
    return _fmt(100.0 * float(share or 0.0))


def slide_table_row(row: Mapping[str, Any]) -> str:
    params = row.get("parameters") or {}
    scale = " / ".join(
        _fmt(params.get(key), 2) for key in ("typeScale", "sourceFactor", "pageScale")
    )
    issues = "; ".join(row.get("blockingIssues") or ())
    if not issues:
        issues = f"ERROR: {row['error']}" if row.get("error") else "-"
    cells = (
        str(int(row["slideIndex"]) + 1),
        str(row.get("archetype", "-")),
        _fmt(row.get("rung")),
        _fmt(params.get("chromeLevel")),
        scale,
        _fmt(row.get("renders")),
        _fmt(row.get("fitMs"), 0),
        issues,
    )
    return "| " + " | ".join(cells) + " |"


def _header(report: Mapping[str, Any]) -> list[str]:
    source = report.get("source") or {}
    aggregate = report.get("aggregate") or {}
    return [
        f"# Composer offline evaluation: {source.get('label', 'deck')}",
        "",
        f"- Mode: `{source.get('mode')}`; manifest `{source.get('manifestId')}`;"
        f" snapshot `{source.get('referenceSnapshotId')}`",
        f"- Source PPTX: {source.get('pptxPath') or 'not reachable'};"
        f" typography from **{source.get('typographySource')}**",
        f"- Pages: {source.get('pageCount')} (fitted {aggregate.get('slides', 0)});"
        f" dry run: {report.get('dryRun', False)}",
        f"- Provider calls: {report.get('providerCalls', 0)}",
    ]


def _timings(report: Mapping[str, Any]) -> list[str]:
    timings = report.get("timings") or {}
    aggregate = report.get("aggregate") or {}
    lines = ["", "## Wall time", "", "| Stage | ms |", "|---|---|"]
    lines += [f"| {key} | {_fmt(timings[key], 0)} |" for key in STAGE_KEYS if key in timings]
    lines += [
        f"| per-slide renders p50 / p90 | {_fmt(aggregate.get('rendersP50'))} / {_fmt(aggregate.get('rendersP90'))} |",
        f"| per-slide fit ms p50 / p90 | {_fmt(aggregate.get('fitMsP50'), 0)} / {_fmt(aggregate.get('fitMsP90'), 0)} |",
        "",
        "## Rung histogram",
        "",
        "| Rung | Slides |",
        "|---|---|",
    ]
    lines += [f"| {rung} | {count} |" for rung, count in (aggregate.get("rungHistogram") or {}).items()]
    return lines


def _slides(report: Mapping[str, Any]) -> list[str]:
    lines = [
        "",
        "## Slides",
        "",
        "| # | Archetype | Rung | Chrome | Type / source / page scale | Renders | Fit ms | Blocking issues |",
        "|---|---|---|---|---|---|---|---|",
    ]
    lines += [slide_table_row(row) for row in report.get("slides") or ()]
    return lines


def _readback(report: Mapping[str, Any]) -> list[str]:
    readback = report.get("readback") or {}
    stamps = readback.get("stamping") or []
    failed = [row for row in stamps if not row.get("passed")]
    rebound = [row for row in stamps if row.get("rebound")]
    deck = readback.get("deck") or {}
    failing = ", ".join(
        str(int(row["slideIndex"]) + 1) + (f" [{row['error']}]" if row.get("error") else "") for row in failed
    )
    lines = [
        "",
        "## Readback assertions",
        "",
        f"- `stamp_source_block_ids` passed: {len(stamps) - len(failed)}/{len(stamps)} slides"
        + (f" (failing: {failing})" if failed else "")
        + (f"; rebound: {len(rebound)}" if rebound else ""),
        f"- `verify_source_verbatim_readback`: {'passed' if deck.get('passed') else 'FAILED'}"
        f" (blocks {deck.get('checkedBlockCount', '-')}, missing {len(deck.get('missingBlockIds') or ())},"
        f" order violations {deck.get('orderViolations', '-')})"
        + (f"; {deck.get('code')}: {deck.get('detail')}" if deck.get("code") else ""),
    ]
    if deck.get("missingBlockIds"):
        lines.append("  - missing: " + ", ".join(deck["missingBlockIds"][:40]))
    return lines


def _enrichment(report: Mapping[str, Any]) -> list[str]:
    coverage = report.get("enrichment") or {}
    size_line = f"- `slideSize` present: {coverage.get('slideSizePresent')}"
    if coverage.get("slideSizePresent"):
        size_line += f" at `{coverage.get('slideSizeLocation')}` = {coverage.get('sourcePageEmu')}"
    return [
        "",
        "## Enrichment coverage",
        "",
        f"- Text runs with `sizePt`: {coverage.get('textRunsWithSizePt', 0)}/{coverage.get('textRuns', 0)}"
        f" ({_pct(coverage.get('textRunSizeShare'))}%)",
        f"- Blocks with geometry: {coverage.get('blocksWithGeometry', 0)}/{coverage.get('blocks', 0)}"
        f" ({_pct(coverage.get('blockGeometryShare'))}%)",
        size_line,
    ]


def _verdict(report: Mapping[str, Any]) -> list[str]:
    aggregate = report.get("aggregate") or {}
    lines = ["", "## Slides with blocking issues", ""]
    lines += [f"- slide {index + 1}" for index in aggregate.get("blockingSlides") or []] or ["- none"]
    lines += ["", "## Acceptance", ""]
    if report.get("dryRun"):
        lines.append("- not evaluated (dry run: classification only)")
    else:
        verdict = acceptance(report)
        lines += [f"- {name}: {'pass' if ok else 'FAIL'}" for name, ok in verdict["checks"].items()]
        lines.append(f"- **overall: {'pass' if verdict['passed'] else 'FAIL'}**")
    notes = report.get("notes") or []
    if notes:
        lines += ["", "## Notes", ""] + [f"- {note}" for note in notes]
    return lines


def render_markdown(report: Mapping[str, Any]) -> str:
    lines = _header(report) + _timings(report) + _slides(report)
    lines += _readback(report) + _enrichment(report) + _verdict(report)
    return "\n".join(lines) + "\n"


__all__ = ["render_markdown", "slide_table_row"]
