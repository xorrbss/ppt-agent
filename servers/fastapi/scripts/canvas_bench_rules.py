"""Pure compare/promote rules for the canvas quality bench.

A canvas × style is exposed only through ``constants/canvas_stages.json`` and
only on measured evidence (docs/aspect-ratio-expansion-design-20260928.md §8):
a candidate run passes a compare when no defect count exceeds the baseline,
per-slide seconds and retries stay within ``TOLERANCE`` of the baseline, and
every exported PPTX has the canvas's slide size.  ``promote`` turns one passing
compare into ``candidate`` and two consecutive passing compares into
``verified``.  Nothing here performs I/O beyond reading the two constants.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path
from typing import Any

FASTAPI_ROOT = Path(__file__).resolve().parents[1]
CANVAS_REGISTRY_PATH = FASTAPI_ROOT / "constants" / "canvas_registry.json"
CANVAS_STAGES_PATH = FASTAPI_ROOT / "constants" / "canvas_stages.json"

RECEIPT_SCHEMA = "canvas-bench-receipt/v1"
COMPARE_SCHEMA = "canvas-bench-compare/v1"
TOLERANCE = 1.15
STAGE_ORDER = ("disabled", "candidate", "verified")

# (name, receipt aggregate key, rule)
_DEFECT_CHECKS = (
    ("local_fit_unresolved", "localFitUnresolved"),
    ("gate_blockers", "gateBlockers"),
    ("export_failures", "exportFailures"),
    ("generation_failures", "generationFailures"),
)
_TOLERANCE_CHECKS = (
    ("per_slide_seconds", "perSlideSeconds"),
    ("retries", "retries"),
)


def load_registry(path: Path = CANVAS_REGISTRY_PATH) -> dict[str, dict[str, Any]]:
    document = json.loads(path.read_text(encoding="utf-8"))
    return {entry["id"]: entry for entry in document["canvases"]}


def canvas_emu(canvas_id: str, registry: Mapping[str, Mapping[str, Any]]) -> tuple[int, int]:
    if canvas_id not in registry:
        raise ValueError(f"unknown canvas id {canvas_id!r}; known: {', '.join(sorted(registry))}")
    size = registry[canvas_id]["canvas"]["physicalSize"]
    return int(size["widthEmu"]), int(size["heightEmu"])


def request_fields(canvas_id: str, registry: Mapping[str, Mapping[str, Any]]) -> dict[str, str]:
    """The generate-request selector for a registry canvas (its aliases)."""

    canvas_emu(canvas_id, registry)
    return dict(registry[canvas_id]["aliases"])


def _sum(values: Iterable[Any]) -> int | None:
    items = list(values)
    if not items or any(value is None for value in items):
        return None
    return int(sum(items))


def aggregate(receipts: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Deck-set totals the compare rule reads.

    A metric one deck could not measure makes the whole aggregate unmeasured
    (``None``) instead of silently counting as zero.
    """

    if not receipts:
        raise ValueError("no receipts to aggregate")
    generated = [r for r in receipts if (r.get("generation") or {}).get("ok")]
    slides = sum(int(r.get("slideCount") or 0) for r in generated)
    seconds = [((r.get("timing") or {}).get("totalSeconds")) for r in generated]
    per_slide = None
    if generated and slides and all(value is not None for value in seconds):
        per_slide = round(sum(seconds) / slides, 2)
    return {
        "decks": len(receipts),
        "slides": slides,
        "perSlideSeconds": per_slide,
        "retries": _sum((r.get("retries") or {}).get("total") for r in receipts),
        "localFitUnresolved": _sum(r.get("localFitUnresolved") for r in generated) if generated else None,
        "gateBlockers": _sum(len((r.get("gate") or {}).get("blockers") or []) for r in generated)
        if generated
        else None,
        "exportFailures": sum(1 for r in receipts if not (r.get("export") or {}).get("ok")),
        "generationFailures": len(receipts) - len(generated),
        "sizeMismatches": sum(1 for r in receipts if (r.get("export") or {}).get("sizeMatches") is not True),
        # Informational: flagged pages publish, so they are not a promotion defect.
        "flaggedPages": sum(int((r.get("gate") or {}).get("flagCount") or 0) for r in generated),
    }


def _check(name: str, baseline: Any, candidate: Any, limit: Any) -> dict[str, Any]:
    if baseline is None and candidate is None:
        return {"name": name, "baseline": None, "candidate": None, "limit": None,
                "passed": True, "status": "not_applicable"}
    if baseline is None or candidate is None:
        return {"name": name, "baseline": baseline, "candidate": candidate, "limit": None,
                "passed": False, "status": "unmeasured"}
    passed = candidate <= limit
    return {"name": name, "baseline": baseline, "candidate": candidate, "limit": limit,
            "passed": passed, "status": "pass" if passed else "fail"}


def _identity(receipts: Sequence[Mapping[str, Any]], key: str) -> set[Any]:
    return {receipt.get(key) for receipt in receipts}


def compare(
    baseline: Sequence[Mapping[str, Any]],
    candidate: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Apply the promotion rule to two receipt sets from the same matrix."""

    base, cand = aggregate(baseline), aggregate(candidate)
    checks: list[dict[str, Any]] = []
    for key in ("style", "strategy"):
        same = _identity(baseline, key) == _identity(candidate, key) and len(_identity(candidate, key)) == 1
        checks.append({"name": f"same_{key}", "baseline": sorted(map(str, _identity(baseline, key))),
                       "candidate": sorted(map(str, _identity(candidate, key))), "limit": None,
                       "passed": same, "status": "pass" if same else "fail"})
    same_manuscripts = _identity(baseline, "manuscript") == _identity(candidate, "manuscript")
    checks.append({"name": "same_manuscripts", "baseline": sorted(map(str, _identity(baseline, "manuscript"))),
                   "candidate": sorted(map(str, _identity(candidate, "manuscript"))), "limit": None,
                   "passed": same_manuscripts, "status": "pass" if same_manuscripts else "fail"})
    for name, key in _DEFECT_CHECKS:
        checks.append(_check(name, base[key], cand[key], base[key]))
    for name, key in _TOLERANCE_CHECKS:
        limit = None if base[key] is None else round(base[key] * TOLERANCE, 2)
        checks.append(_check(name, base[key], cand[key], limit))
    size_ok = cand["sizeMismatches"] == 0
    checks.append({"name": "pptx_size_matches_canvas", "baseline": base["sizeMismatches"],
                   "candidate": cand["sizeMismatches"], "limit": 0, "passed": size_ok,
                   "status": "pass" if size_ok else "fail"})
    canvases = _identity(candidate, "canvas")
    return {
        "schemaVersion": COMPARE_SCHEMA,
        "canvas": next(iter(canvases)) if len(canvases) == 1 else sorted(map(str, canvases)),
        "baselineCanvas": sorted(map(str, _identity(baseline, "canvas"))),
        "style": next(iter(_identity(candidate, "style"))),
        "strategy": next(iter(_identity(candidate, "strategy"))),
        "passed": all(check["passed"] for check in checks),
        "baseline": base,
        "candidate": cand,
        "checks": checks,
        "baselinePresentations": [r.get("presentationId") for r in baseline],
        "candidatePresentations": [r.get("presentationId") for r in candidate],
    }


def compare_markdown(result: Mapping[str, Any]) -> str:
    verdict = "PASS" if result["passed"] else "FAIL"
    lines = [
        f"### Canvas bench compare: {result['canvas']} vs {', '.join(result['baselineCanvas'])} "
        f"(style `{result['style']}`, strategy `{result['strategy'] or 'default'}`) - **{verdict}**",
        "",
        "| check | baseline | candidate | limit | result |",
        "|---|---|---|---|---|",
    ]
    for check in result["checks"]:
        lines.append(
            f"| {check['name']} | {check['baseline']} | {check['candidate']} | "
            f"{'' if check['limit'] is None else check['limit']} | {check['status']} |"
        )
    base, cand = result["baseline"], result["candidate"]
    lines += [
        "",
        f"Informational (not gated): decks {base['decks']} / {cand['decks']}, slides {base['slides']} / "
        f"{cand['slides']}, flagged pages {base['flaggedPages']} / {cand['flaggedPages']} (baseline / candidate).",
    ]
    return "\n".join(lines) + "\n"


def gated_ratio(canvas_id: str, registry: Mapping[str, Mapping[str, Any]], stages: Mapping[str, Any]) -> str:
    """The canvas_stages.json key of a registry canvas, or a clear refusal."""

    aliases = request_fields(canvas_id, registry)
    ratio = aliases.get("aspect_ratio")
    if ratio not in stages:
        raise ValueError(
            f"canvas {canvas_id!r} is not stage-gated (canvas_stages.json keys: "
            f"{', '.join(sorted(stages))}); it is exposed by the registry, not by promotion"
        )
    return ratio


def promotion_stage(compares: Sequence[Mapping[str, Any]], canvas_id: str, style_id: str) -> str:
    """Stage earned by the compare history, oldest first, for one canvas × style.

    Two consecutive passes earn ``verified``; the latest pass alone earns
    ``candidate``; a latest failure earns nothing (``disabled``).
    """

    relevant = [c for c in compares if c.get("canvas") == canvas_id and c.get("style") == style_id]
    if not relevant:
        raise ValueError(f"no compare output for canvas {canvas_id!r} and style {style_id!r}")
    foreign = [c for c in compares if c not in relevant]
    if foreign:
        raise ValueError("compare outputs for another canvas or style were supplied; pass only this pair's")
    if not relevant[-1].get("passed"):
        return "disabled"
    if len(relevant) >= 2 and relevant[-2].get("passed"):
        return "verified"
    return "candidate"


def apply_promotion(stages: Mapping[str, Any], ratio: str, style_id: str, earned: str) -> tuple[dict[str, Any], str]:
    """Return the new stages document and an outcome; never lowers a stage."""

    current = stages.get(ratio, {}).get(style_id, "disabled")
    if STAGE_ORDER.index(earned) < STAGE_ORDER.index(current):
        raise ValueError(
            f"refusing to downgrade {ratio}/{style_id} from {current!r} to {earned!r}; "
            "demotion is a deliberate manual decision, not a bench side effect"
        )
    if earned == current:
        return {key: dict(value) for key, value in stages.items()}, "unchanged"
    updated = {key: dict(value) for key, value in stages.items()}
    updated[ratio][style_id] = earned
    return updated, f"{current}->{earned}"
