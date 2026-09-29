"""Pooled decision rule of the canvas bench.

Implements docs/canvas-bench-decision-rule-design-20260929.md section 4:
non-inferiority of the page flag rate (design-effect-adjusted Wilson, Newcombe
one-sided 95% difference) plus secondary gates and hard gates, evaluated per
tier on pools of decks that share style, strategy, slide count and metric epoch.
Verdicts are ``pass``, ``fail`` and ``hold`` (run more decks or fix the data).
"""

from __future__ import annotations

import json
from collections import Counter
from collections.abc import Mapping, Sequence
from typing import Any

from scripts import canvas_bench_stats as stats
from scripts.canvas_bench_epoch import receipt_epoch

COMPARE_SCHEMA = "canvas-bench-compare/v2"
STAGE_ORDER = ("disabled", "candidate", "verified")
TIERS: dict[str, dict[str, Any]] = {
    "candidate": {"margin": 0.20, "candidateDecks": 12, "baselineDecks": 18, "manuscripts": 3, "days": 1},
    "verified": {"margin": 0.15, "candidateDecks": 24, "baselineDecks": 24, "manuscripts": 4, "days": 2},
}
MAX_MANUSCRIPT_SHARE = 0.40
MAX_UNMEASURED_SHARE = 0.10
SECONDS_MARGIN = 0.15
SECONDARY_MIN_DECKS = 12
METRIC_CAP_DECKS = 36
# Metrics a strategy never produces; absence there is not a measurement gap.
NOT_APPLICABLE: dict[str, frozenset[str]] = {
    "svg": frozenset({"localFitUnresolved"}),
    "authored": frozenset(),
}


def infra_invalid(receipt: Mapping[str, Any]) -> Mapping[str, Any] | None:
    invalid = receipt.get("invalid")
    if invalid is None:
        return None
    if not isinstance(invalid, Mapping) or invalid.get("reason") != "infra_lost":
        raise ValueError("receipt.invalid must be {'reason': 'infra_lost', 'note': ...}; "
                         "product failures stay in the denominator")
    return invalid


def strategy_key(receipt: Mapping[str, Any]) -> str:
    return str(receipt.get("strategy") or "default")


def _applicable_strategy(receipt: Mapping[str, Any]) -> str:
    if receipt.get("strategy") in NOT_APPLICABLE:
        return str(receipt["strategy"])
    return "svg" if receipt.get("sourceFormats") == ["svg"] else "authored"


def _not_applicable(receipt: Mapping[str, Any]) -> frozenset[str]:
    return NOT_APPLICABLE[_applicable_strategy(receipt)]


def deck_metrics(receipt: Mapping[str, Any]) -> dict[str, Any]:
    """One deck's numbers; ``None`` marks a metric the receipt does not carry."""

    generated = bool((receipt.get("generation") or {}).get("ok"))
    export = receipt.get("export") or {}
    flags = (receipt.get("gate") or {}).get("flags")
    flagged = flags.get("flaggedPages") if isinstance(flags, Mapping) else None
    pages = int(receipt.get("slideCount") or 0)
    timing = receipt.get("timing") or {}
    retries = (receipt.get("retries") or {}).get("total")
    applicable = "localFitUnresolved" not in _not_applicable(receipt)
    return {
        "presentationId": receipt.get("presentationId"),
        "manuscript": receipt.get("manuscript"),
        "day": str(receipt.get("createdAt") or "")[:10] or None,
        "generated": generated,
        "exportOk": bool(export.get("ok")),
        "sizeMismatch": bool(export.get("ok")) and export.get("sizeMatches") is not True,
        "pages": pages,
        "flagged": int(flagged) if generated and pages and isinstance(flagged, (int, float)) else None,
        "retriesPerSlide": retries / pages if generated and pages and retries is not None else None,
        "secondsPerSlide": timing.get("perSlideSeconds") if generated else None,
        "timingSource": timing.get("source"),
        "localFitApplicable": applicable,
        "localFitUnresolved": receipt.get("localFitUnresolved") if applicable else None,
    }


def select_pool(
    receipts: Sequence[Mapping[str, Any]], *, style: str, strategy: str, epoch: str, slides: int | None
) -> tuple[list[Mapping[str, Any]], dict[str, Any]]:
    """Receipts that share the pool key, plus what was left out and why."""

    kept: list[Mapping[str, Any]] = []
    invalid: list[dict[str, Any]] = []
    other: Counter[str] = Counter()
    for receipt in receipts:
        marker = infra_invalid(receipt)
        if marker is not None:
            invalid.append({"presentationId": receipt.get("presentationId"), "manuscript": receipt.get("manuscript"),
                            "note": marker.get("note")})
        elif receipt_epoch(receipt) != epoch:
            other[f"epoch:{receipt_epoch(receipt)}"] += 1
        elif receipt.get("style") != style or strategy_key(receipt) != strategy:
            other["style_or_strategy"] += 1
        elif slides is not None and receipt.get("requestedSlides") != slides:
            other["slide_count"] += 1
        else:
            kept.append(receipt)
    return kept, {"invalid": invalid, "other": dict(other)}


def _arm(decks: list[dict[str, Any]]) -> dict[str, Any]:
    generated = [d for d in decks if d["generated"]]
    measured = [d for d in generated if d["flagged"] is not None]
    pages = sum(d["pages"] for d in measured)
    flagged = sum(d["flagged"] for d in measured)
    manuscripts = Counter(d["manuscript"] for d in measured)
    return {
        "decks": len(decks), "generated": len(generated), "measured": len(measured),
        "unmeasured": len(generated) - len(measured),
        "generationFailures": len(decks) - len(generated),
        "exportFailures": sum(1 for d in generated if not d["exportOk"]),
        "sizeMismatches": sum(1 for d in decks if d["sizeMismatch"]),
        "pages": pages, "flagged": flagged, "rate": flagged / pages if pages else None,
        "manuscripts": dict(sorted(manuscripts.items())),
        "topManuscriptShare": max(manuscripts.values()) / len(measured) if measured else None,
        "days": sorted({d["day"] for d in measured if d["day"]}),
    }


def _mean_check(name: str, base: list[float], cand: list[float], margin: float) -> dict[str, Any]:
    if len(base) < SECONDARY_MIN_DECKS or len(cand) < SECONDARY_MIN_DECKS:
        return {"name": name, "status": "hold", "reason": "insufficient_sample", "margin": round(margin, 4),
                "baselineDecks": len(base), "candidateDecks": len(cand), "needed": SECONDARY_MIN_DECKS}
    diff, lower, upper = stats.mean_diff_bounds(base, cand)
    status = "pass" if upper <= margin else "fail" if lower > margin else "hold"
    return {"name": name, "status": status, "margin": round(margin, 4), "difference": round(diff, 4),
            "lower": round(lower, 4), "upper": round(upper, 4),
            "baselineMean": round(stats.mean(base), 4), "candidateMean": round(stats.mean(cand), 4)}


def _sample_gaps(spec: Mapping[str, Any], base: Mapping[str, Any], cand: Mapping[str, Any]) -> list[str]:
    gaps = []
    if cand["measured"] < spec["candidateDecks"]:
        gaps.append(f"candidate decks {cand['measured']}/{spec['candidateDecks']}")
    if base["measured"] < spec["baselineDecks"]:
        gaps.append(f"baseline decks {base['measured']}/{spec['baselineDecks']}")
    for label, arm in (("candidate", cand), ("baseline", base)):
        if len(arm["manuscripts"]) < spec["manuscripts"]:
            gaps.append(f"{label} manuscripts {len(arm['manuscripts'])}/{spec['manuscripts']}")
        elif (arm["topManuscriptShare"] or 0) > MAX_MANUSCRIPT_SHARE:
            gaps.append(f"{label} one manuscript is {arm['topManuscriptShare']:.0%} of decks (max 40%)")
    if len(cand["days"]) < spec["days"]:
        gaps.append(f"candidate calendar days {len(cand['days'])}/{spec['days']}")
    return gaps


def _primary(base: Mapping[str, Any], cand: Mapping[str, Any], margin: float) -> dict[str, Any]:
    check: dict[str, Any] = {"name": "page_flag_rate", "status": "hold", "margin": margin}
    if not (base["pages"] and cand["pages"]):
        return check
    deff = {label: stats.design_effect(arm["pages"] / arm["measured"])
            for label, arm in (("candidate", cand), ("baseline", base))}
    diff, lower, upper = stats.newcombe_diff_bounds(
        cand["flagged"] / deff["candidate"], cand["pages"] / deff["candidate"],
        base["flagged"] / deff["baseline"], base["pages"] / deff["baseline"])
    status = "pass" if upper <= margin else "fail" if diff >= margin and lower > 0 else "hold"
    check.update(status=status, baselineRate=round(base["rate"], 4), candidateRate=round(cand["rate"], 4),
                 difference=round(diff, 4), lower=round(lower, 4), upper=round(upper, 4),
                 designEffect={k: round(v, 3) for k, v in deff.items()}, rho=stats.RHO)
    return check


def assess_tier(base_decks: list[dict[str, Any]], cand_decks: list[dict[str, Any]], tier: str) -> dict[str, Any]:
    spec = TIERS[tier]
    base, cand = _arm(base_decks), _arm(cand_decks)
    reasons: list[str] = []
    hard = {"pptxSizeMismatches": cand["sizeMismatches"], "generationFailures": cand["generationFailures"],
            "exportFailures": cand["exportFailures"]}
    hard_failed = [name for name, count in hard.items() if count]
    reasons += [f"hard_gate:{name}={hard[name]}" for name in hard_failed]
    gaps = _sample_gaps(spec, base, cand)
    if gaps:
        reasons.append("insufficient_sample: " + "; ".join(gaps))
    unmeasured = False
    for label, arm in (("candidate", cand), ("baseline", base)):
        if arm["generated"] and arm["unmeasured"] / arm["generated"] > MAX_UNMEASURED_SHARE:
            unmeasured = True
            reasons.append(f"too_many_unmeasured:{label} {arm['unmeasured']}/{arm['generated']}")
    checks = [_primary(base, cand, spec["margin"])]
    seconds_base = [d["secondsPerSlide"] for d in base_decks if d["secondsPerSlide"] is not None]
    for name, key, margin in (
        ("retries_per_slide", "retriesPerSlide", spec["margin"]),
        ("seconds_per_slide", "secondsPerSlide", SECONDS_MARGIN * stats.mean(seconds_base) if seconds_base else 0.0),
    ):
        b = [d[key] for d in base_decks if d[key] is not None]
        c = [d[key] for d in cand_decks if d[key] is not None]
        sources = set()
        if key == "secondsPerSlide":
            sources = {d["timingSource"] for d in base_decks + cand_decks if d[key] is not None}
        if len(sources) > 1:
            checks.append({"name": name, "status": "hold", "reason": "timing_source_mismatch",
                           "sources": sorted(map(str, sources))})
        else:
            checks.append(_mean_check(name, b, c, margin))
    statuses = [check["status"] for check in checks]
    if hard_failed or ("fail" in statuses and not gaps and not unmeasured):
        verdict = "fail"
    elif gaps or unmeasured or "hold" in statuses:
        verdict = "hold"
    else:
        verdict = "pass"
    for check in checks:
        if check["status"] != "pass":
            suffix = f" ({check['reason']})" if "reason" in check else ""
            reasons.append(f"{check['name']}:{check['status']}{suffix}")
    return {
        "tier": tier, "verdict": verdict, "reasons": reasons, "hardGates": hard, "checks": checks,
        "sample": {"baseline": base, "candidate": cand, "required": dict(spec),
                   "capReached": cand["measured"] >= METRIC_CAP_DECKS and verdict != "pass"},
    }


def _informational(decks: list[dict[str, Any]]) -> dict[str, Any]:
    applicable = [d for d in decks if d["localFitApplicable"] and d["generated"]]
    return {"localFitUnresolved": {
        "applicableDecks": len(applicable),
        "unmeasured": sum(1 for d in applicable if d["localFitUnresolved"] is None),
        "total": sum(d["localFitUnresolved"] for d in applicable if d["localFitUnresolved"] is not None)}}


def _unique(receipts: Sequence[Mapping[str, Any]], key: str) -> Any:
    values = {r.get(key) for r in receipts}
    if len(values) != 1:
        raise ValueError(f"expected exactly one {key}, got {sorted(map(str, values))}; narrow the selection")
    return next(iter(values))


def compare(
    baseline: Sequence[Mapping[str, Any]], candidate: Sequence[Mapping[str, Any]], *,
    epoch: str | None = None, slides: int | None = None,
) -> dict[str, Any]:
    """Assess both tiers on pooled receipts (one baseline canvas, one candidate canvas)."""

    live = [r for r in candidate if infra_invalid(r) is None]
    if not live:
        raise ValueError("no valid candidate receipts")
    if not baseline:
        raise ValueError("no baseline receipts")
    canvas, baseline_canvas = _unique(candidate, "canvas"), _unique(baseline, "canvas")
    if epoch is None:
        epoch = receipt_epoch(max(live, key=lambda r: str(r.get("createdAt") or "")))
    style, strategy = _unique(live, "style"), _unique([{"s": strategy_key(r)} for r in live], "s")
    if slides is None:
        slides = _unique(live, "requestedSlides")
    kept_base, excluded_base = select_pool(baseline, style=style, strategy=strategy, epoch=epoch, slides=slides)
    kept_cand, excluded_cand = select_pool(candidate, style=style, strategy=strategy, epoch=epoch, slides=slides)
    base_decks, cand_decks = [deck_metrics(r) for r in kept_base], [deck_metrics(r) for r in kept_cand]
    tiers = {tier: assess_tier(base_decks, cand_decks, tier) for tier in TIERS}
    earned = "disabled"
    for tier in ("candidate", "verified"):
        if tiers[tier]["verdict"] == "pass":
            earned = tier
    return {
        "schemaVersion": COMPARE_SCHEMA, "canvas": canvas, "baselineCanvas": [baseline_canvas],
        "style": style, "strategy": None if strategy == "default" else strategy, "slides": slides,
        "metricEpoch": epoch, "earnedStage": earned, "tiers": tiers,
        "excluded": {"baseline": excluded_base, "candidate": excluded_cand},
        "informational": {"baseline": _informational(base_decks), "candidate": _informational(cand_decks)},
        "baselinePresentations": [r.get("presentationId") for r in kept_base],
        "candidatePresentations": [r.get("presentationId") for r in kept_cand],
    }


def compare_markdown(result: Mapping[str, Any]) -> str:
    lines = [f"### Canvas bench: {result['canvas']} vs {', '.join(result['baselineCanvas'])} "
             f"(style `{result['style']}`, strategy `{result['strategy'] or 'default'}`, "
             f"epoch `{result['metricEpoch']}`, {result['slides']} slides) - earned **{result['earnedStage']}**", ""]
    for tier, item in result["tiers"].items():
        lines.append(f"**{tier}: {item['verdict'].upper()}**")
        for label in ("baseline", "candidate"):
            arm = item["sample"][label]
            rate = "n/a" if arm["rate"] is None else f"{arm['rate']:.3f}"
            lines.append(f"- {label}: {arm['measured']} decks, {arm['flagged']}/{arm['pages']} pages flagged "
                         f"(rate {rate}), {len(arm['manuscripts'])} manuscripts, {len(arm['days'])} days, "
                         f"unmeasured {arm['unmeasured']}")
        lines += ["", "| check | margin | difference | lower | upper | status |", "|---|---|---|---|---|---|"]
        for check in item["checks"]:
            lines.append(f"| {check['name']} | {check.get('margin', '')} | {check.get('difference', '')} | "
                         f"{check.get('lower', '')} | {check.get('upper', '')} | {check['status']} |")
        lines += ["", *(f"- {reason}" for reason in item["reasons"]), ""]
    lines.append(f"Excluded: {json.dumps(result['excluded'], ensure_ascii=False)}")
    return "\n".join(lines) + "\n"
