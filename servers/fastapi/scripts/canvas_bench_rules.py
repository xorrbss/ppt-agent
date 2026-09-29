"""Registry helpers and promotion rules for the canvas quality bench.

A canvas x style is exposed only through ``constants/canvas_stages.json`` and
only on measured evidence (docs/aspect-ratio-expansion-design-20260928.md
section 8).  The pass/fail/hold decision is the pooled statistical rule in
``canvas_bench_assess`` (docs/canvas-bench-decision-rule-design-20260929.md);
this module maps its earned stage onto the stages file and never lowers one.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from scripts.canvas_bench_assess import COMPARE_SCHEMA, STAGE_ORDER, compare, compare_markdown  # noqa: F401

FASTAPI_ROOT = Path(__file__).resolve().parents[1]
CANVAS_REGISTRY_PATH = FASTAPI_ROOT / "constants" / "canvas_registry.json"
CANVAS_STAGES_PATH = FASTAPI_ROOT / "constants" / "canvas_stages.json"

RECEIPT_SCHEMA = "canvas-bench-receipt/v2"
# v1 receipts (pilot, sq-uw) carry no metricEpoch/batch; they read as the legacy epoch.
ACCEPTED_RECEIPT_SCHEMAS = ("canvas-bench-receipt/v1", RECEIPT_SCHEMA)


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
    """Stage earned by the pooled evaluation for one canvas x style.

    The newest compare is the pooled assessment, so it alone decides: the
    highest tier whose verdict is ``pass`` (``verified`` over ``candidate``),
    otherwise ``disabled``.  ``apply_promotion`` keeps a fail or hold from
    lowering a stage that was earned before.
    """

    relevant = [c for c in compares if c.get("canvas") == canvas_id and c.get("style") == style_id]
    if not relevant:
        raise ValueError(f"no compare output for canvas {canvas_id!r} and style {style_id!r}")
    if len(relevant) != len(compares):
        raise ValueError("compare outputs for another canvas or style were supplied; pass only this pair's")
    earned = relevant[-1].get("earnedStage")
    if earned not in STAGE_ORDER:
        raise ValueError(f"compare output has no valid earnedStage ({earned!r})")
    return str(earned)


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
