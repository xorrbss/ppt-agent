"""Summary assembly and rendering for the authored fit replay (design 5.3)."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from scripts import authored_fit_replay_buckets as buckets


def _table(header: Sequence[str], rows: Sequence[Sequence[Any]]) -> list[str]:
    lines = ["| " + " | ".join(header) + " |"]
    lines.append("|" + "|".join("---" for _ in header) + "|")
    for row in rows:
        lines.append("| " + " | ".join(str(cell) for cell in row) + " |")
    return lines


def _percent(value: Any) -> str:
    return "n/a" if value is None else f"{float(value) * 100:.0f} %"


def _counts_table(title: str, counts: Mapping[str, int]) -> list[str]:
    lines = [f"### {title}", ""]
    if not counts:
        lines.extend(["(none)", ""])
        return lines
    total = sum(counts.values()) or 1
    lines.extend(
        _table(
            ["value", "slides", "share"],
            [
                [key, value, f"{value / total * 100:.0f} %"]
                for key, value in counts.items()
            ],
        )
    )
    lines.append("")
    return lines


def render_markdown(
    summary: Mapping[str, Any],
    *,
    scales: Sequence[float],
    generated_at: str,
    jobs: Sequence[Mapping[str, Any]],
    skipped: Sequence[Mapping[str, Any]],
    parity: Mapping[str, Any] | None,
) -> str:
    lines: list[str] = [
        "# Authored fit replay",
        "",
        f"Generated {generated_at}. Scale steps: "
        + ", ".join(f"{scale:.2f}" for scale in scales)
        + ".",
        "",
        "M3 of `docs/authored-fit-magnitude-measurement-design-20260904.md`. "
        "Every verdict below comes from a real Chromium render of stored HTML "
        "through the pipeline's own `capacity_violations` and "
        "`inspect_authored_dom`; no provider call was made.",
        "",
        "## Jobs",
        "",
    ]
    lines.extend(
        _table(
            ["job", "state", "code", "slides", "profile", "reference mode", "deviations"],
            [
                [
                    job["job_short"],
                    job["state"],
                    job["code_version"],
                    job["slides"],
                    job["profile_id"],
                    job["reference_mode"],
                    ", ".join(job["replay_deviations"]) or "-",
                ]
                for job in jobs
            ],
        )
    )
    lines.append("")

    if skipped:
        lines.extend(["## Skipped slides", ""])
        lines.extend(
            _table(
                ["job", "slide", "reason", "detail"],
                [
                    [item["job_short"], item["slide_index"], item["reason"], item["detail"]]
                    for item in skipped
                ],
            )
        )
        lines.append("")

    lines.extend(["## Population", ""])
    lines.extend(_counts_table("By code version", summary["by_code_version"]))
    lines.extend(_counts_table("By source", summary["by_source"]))

    lines.extend(["## Buckets", ""])
    lines.extend(_counts_table("Current code", summary["buckets_current"]))
    lines.extend(_counts_table("Legacy (excluded from the decision)", summary["buckets_legacy"]))

    lines.extend(
        _counts_table(
            "Worst overflow kind, current-code ladder population",
            summary["overflow_kinds_current"],
        )
    )

    fill = summary["under_fill"]
    lines.extend(
        [
            "## Under-fill",
            "",
            f"`content_bbox_fill` over the {fill['population']} slides that pass "
            "the geometry gate at scale 1.0. M1 computes it as the union of the "
            "*owned-content* boxes clipped to the canvas — the ink the browser "
            "attributes to a measured target, not the area a layout panel "
            "occupies — so read the 0.55 threshold against that definition.",
            "",
        ]
    )
    lines.extend(
        _table(
            ["bin", "slides"],
            [[item["bin"], item["count"]] for item in fill["histogram"]],
        )
    )
    lines.extend(
        [
            "",
            f"Below 0.55: {fill['below_0.55']} "
            f"({_percent(fill['below_0.55_share'])}). `grow_step` indicated "
            f"(design section 7, >= 25 %): **{fill['grow_step_indicated']}**.",
            "",
        ]
    )

    decision = summary["decision_inputs_current"]
    lines.extend(
        [
            "## M5 decision inputs (current code)",
            "",
            f"Ladder population (a capacity violation or a geometry defect at "
            f"scale 1.0): **{decision['ladder_population']}** slides.",
            "",
        ]
    )
    lines.extend(
        _table(
            ["quantity", "slides", "share"],
            [
                ["fits by scale, not floor-bound (S)", decision["fits_by_scale"], _percent(decision["S"])],
                ["non_text (N)", decision["non_text"], _percent(decision["N"])],
                ["floor_bound", decision["floor_bound"], "-"],
                ["unfixable_by_scale", decision["unfixable_by_scale"], "-"],
            ],
        )
    )
    lines.append("")

    if parity is not None:
        lines.extend(["## Verdict parity (design section 10, risk 1)", ""])
        lines.append(parity["narrative"])
        lines.append("")
        if parity.get("rows"):
            lines.extend(
                _table(
                    ["job", "evidence", "recorded flagged slides", "replay blocking slides", "agreement"],
                    [
                        [
                            row["job_short"],
                            row["evidence"],
                            row["recorded"],
                            row["replayed"],
                            row["agreement"],
                        ]
                        for row in parity["rows"]
                    ],
                )
            )
            lines.append("")
    return "\n".join(lines) + "\n"


def job_rows(
    contexts: Sequence[Any], rows: Sequence[Mapping[str, Any]]
) -> list[dict[str, Any]]:
    return [
        {
            "job_id": str(context.job_id),
            "job_short": context.short,
            "state": context.state,
            "code_version": context.code_version,
            "profile_id": context.profile.profile_id,
            "reference_mode": context.reference_mode,
            "slides": sum(1 for row in rows if row["job_id"] == str(context.job_id)),
            "replay_deviations": list(context.deviations),
        }
        for context in contexts
    ]


def write_outputs(state: Any) -> Path:
    """Write ``slides.jsonl``, ``summary.json`` and ``summary.md``; return the md."""

    summary = buckets.build_summary(state.rows)
    summary["jobs"] = job_rows(state.contexts, state.rows)
    summary["skipped"] = list(state.skipped)
    summary["scales"] = list(state.scales)
    summary["generated_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    if state.parity is not None:
        summary["parity"] = state.parity

    out_dir = Path(state.out_dir)
    with (out_dir / "slides.jsonl").open("w", encoding="utf-8") as handle:
        for row in state.rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    (out_dir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    markdown = render_markdown(
        summary,
        scales=state.scales,
        generated_at=summary["generated_at"],
        jobs=summary["jobs"],
        skipped=summary["skipped"],
        parity=state.parity,
    )
    summary_path = out_dir / "summary.md"
    summary_path.write_text(markdown, encoding="utf-8")
    return summary_path


__all__ = ["job_rows", "render_markdown", "write_outputs"]
