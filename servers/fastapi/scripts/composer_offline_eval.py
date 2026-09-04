"""Offline evaluation harness for the Composer (``source_content_restyle``).

Renders a stored or freshly extracted source manifest through the Composer
stages with the real Chromium measurer, no database writes and no provider
calls, and writes per-slide HTML/PNG, contact sheets, ``report.json`` and
``report.md`` with the phase 0 acceptance verdict
(docs/generation-quality-root-cause-and-composer-architecture-20260904.md, 6).

Run from ``servers/fastapi``::

    .venv\\Scripts\\python.exe scripts/composer_offline_eval.py --target deck-35
    .venv\\Scripts\\python.exe scripts/composer_offline_eval.py --pptx path/to/deck.pptx --limit 3

Exit codes: 0 acceptance passed (or dry run completed), 1 acceptance failed,
2 a Composer stage module or function is missing, 3 input error.
"""

from __future__ import annotations

import argparse
import asyncio
import importlib
import json
import os
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Sequence

FASTAPI_ROOT = Path(__file__).resolve().parents[1]
if str(FASTAPI_ROOT) not in sys.path:
    sys.path.insert(0, str(FASTAPI_ROOT))

from scripts import composer_offline_eval_fit as fit_lib  # noqa: E402
from scripts import composer_offline_eval_report as report_lib  # noqa: E402
from scripts.composer_offline_eval_db import bundle_from_database  # noqa: E402
#: The style the generation lane requests for a verbatim restyle run; also the
#: id ``--style default`` names.
PRODUCT_STYLE_ID = "default"

from scripts.composer_offline_eval_inputs import (  # noqa: E402
    SourceBundle,
    SourceLookupError,
    build_deck_context,
    build_tokens,
    bundle_from_pptx,
    deck_title_from_units,
    load_env_json,
)
from scripts.composer_offline_eval_markdown import render_markdown  # noqa: E402
from scripts.composer_offline_eval_sheets import write_contact_sheets  # noqa: E402

DEFAULT_ENV_JSON = Path(
    r"C:\Users\ibiz\AppData\Local\Temp\claude\C--project-PPT-agent-ppt-agent"
    r"\d00593c7-ad45-4bdf-beac-e2ddf0bd032e\scratchpad\env-47392.json"
)
DEFAULT_OWNERS = (
    "d74ac154-738d-4c58-bade-daf2f8d703d3",
    "1e9e5b04-69d4-4c87-9e03-effb9a9e078f",
)
TARGETS_PATH = Path(__file__).with_name("composer_offline_eval_targets.json")
DEFAULT_OUT_ROOT = FASTAPI_ROOT.parents[1] / "artifacts" / "composer-offline-eval"

STAGE_IMPORTS: tuple[tuple[str, str], ...] = (
    ("services.composer.classify", "classify_unit"),
    ("services.composer.render", "render_slide"),
    ("services.composer.fit", "fit_slide"),
    ("services.composer.measure", "measure_slide"),
    ("services.composer.estimate", "estimate_fit"),
)
#: A dry run classifies only, so the other stages may still be missing.
DRY_RUN_STAGES = frozenset({"classify_unit"})

EXIT_OK, EXIT_FAILED, EXIT_MISSING_STAGE, EXIT_INPUT = 0, 1, 2, 3


@dataclass(frozen=True)
class ComposerStages:
    classify_unit: Callable[..., Any]
    render_slide: Callable[..., Any] | None = None
    fit_slide: Callable[..., Any] | None = None
    measure_slide: Callable[..., Any] | None = None
    estimate_fit: Callable[..., Any] | None = None


# --- Arguments -----------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    source = parser.add_argument_group("source (exactly one)")
    source.add_argument("--pptx", type=Path, help="extract this PPTX directly; no database")
    source.add_argument("--snapshot", help="reference snapshot id of a stored manifest (PostgreSQL)")
    source.add_argument("--manifest-id", help="stored manifest id (PostgreSQL)")
    source.add_argument("--target", help=f"named deck from {TARGETS_PATH.name}")
    parser.add_argument("--env-json", type=Path, default=DEFAULT_ENV_JSON, help="captured environment (database mode)")
    parser.add_argument("--owner", action="append", dest="owners", help="owner id to try (repeatable)")
    parser.add_argument("--tenant", help="tenant id; default PPT_SERVICE_TENANT_ID from the environment")
    # The harness defaults to the style the product actually generates with, so
    # a run measures the tokens the deck renders from: the neutral placeholder
    # palette once hid an invisible-text regression because the default run
    # never reached ``services.source_content.pipeline._style_tokens`` at all.
    # "--style none" opts back out to that placeholder palette.
    parser.add_argument(
        "--style",
        default=PRODUCT_STYLE_ID,
        help=(
            "authored style id whose real derived tokens to evaluate "
            f"(default: {PRODUCT_STYLE_ID!r}, the style the product generates "
            "with; 'none' evaluates the neutral placeholder palette instead)"
        ),
    )
    parser.add_argument("--out", type=Path, help="output directory (default: under artifacts/composer-offline-eval/)")
    parser.add_argument("--label", help="deck label used in file names and the report")
    parser.add_argument("--concurrency", type=int, default=2, help="parallel Chromium fits (default 2)")
    parser.add_argument("--limit", type=int, help="fit only the first N pages")
    parser.add_argument("--max-renders", type=int, help="fit ladder render budget per slide (default: fit_slide's)")
    parser.add_argument("--render-timeout", type=int, default=60, help="seconds per Chromium capture")
    parser.add_argument("--dry-run", action="store_true", help="stop after classification; write plans.json")
    return parser


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.target:
        apply_target(args, load_targets(TARGETS_PATH))
    if not any((args.pptx, args.snapshot, args.manifest_id)):
        parser.error("one of --pptx, --snapshot, --manifest-id or --target is required")
    if args.pptx and (args.snapshot or args.manifest_id):
        parser.error("--pptx cannot be combined with --snapshot/--manifest-id")
    if args.concurrency < 1:
        parser.error("--concurrency must be at least 1")
    if args.limit is not None and args.limit < 1:
        parser.error("--limit must be at least 1")
    # "--style none" is the one documented opt-out spelling to the neutral
    # placeholder palette; an empty string used to reach the same branch
    # through build_tokens's own "if not style_id" check, giving the opt-out
    # two spellings. Reject it here instead so exactly one spelling exists.
    if args.style is not None and not str(args.style).strip():
        parser.error("--style must not be empty; use --style none to opt out of the derived palette")
    args.owners = tuple(args.owners or DEFAULT_OWNERS)
    return args


def load_targets(path: Path) -> dict[str, dict[str, Any]]:
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


def apply_target(args: argparse.Namespace, targets: dict[str, dict[str, Any]]) -> None:
    target = targets.get(args.target)
    if target is None:
        known = ", ".join(sorted(name for name in targets if not name.startswith("_")))
        raise SystemExit(f"unknown target {args.target!r}; known: {known}")
    args.snapshot = args.snapshot or target.get("referenceSnapshotId")
    args.manifest_id = args.manifest_id or target.get("manifestId")
    args.label = args.label or target.get("label") or args.target


# --- Stage imports -------------------------------------------------------------


def import_composer_stages(*, dry_run: bool = False) -> tuple[ComposerStages | None, list[str]]:
    """Import every sibling stage lazily; report exactly what is missing."""

    resolved: dict[str, Callable[..., Any]] = {}
    missing: list[str] = []
    for module_name, function_name in STAGE_IMPORTS:
        if dry_run and function_name not in DRY_RUN_STAGES:
            continue
        try:
            module = importlib.import_module(module_name)
        except ModuleNotFoundError as error:
            missing.append(f"missing module: {error.name} (while importing {module_name} for {function_name})")
            continue
        function = getattr(module, function_name, None)
        if not callable(function):
            missing.append(f"missing function: {module_name}.{function_name}")
            continue
        resolved[function_name] = function
    if missing:
        return None, missing
    return ComposerStages(**resolved), []


# --- Run -------------------------------------------------------------------------


async def acquire_source(args: argparse.Namespace) -> SourceBundle:
    if args.pptx:
        return bundle_from_pptx(args.pptx, label=args.label)
    if args.env_json and args.env_json.is_file():
        load_env_json(args.env_json)
    tenant = args.tenant or os.environ.get("PPT_SERVICE_TENANT_ID") or None
    label = args.label or f"snapshot-{(args.snapshot or args.manifest_id or '')[:8]}"
    return await bundle_from_database(
        snapshot_id=args.snapshot, manifest_id=args.manifest_id, owners=args.owners, tenant_id=tenant, label=label
    )


def _readback_section(bundle: SourceBundle, htmls: Sequence[str], fitted: int) -> dict[str, Any]:
    from services.source_content.contracts import SourceContentManifestPin
    from services.source_content.outline import build_source_document_outline

    pin = SourceContentManifestPin(
        manifest_id=str(bundle.manifest["manifestId"]), digest=str(bundle.manifest["manifestDigest"])
    )
    outline = build_source_document_outline(manifest=bundle.manifest, manifest_pin=pin)
    manifest_slice, outline_slice = report_lib.truncate_for_readback(
        bundle.manifest, outline.model_dump(mode="json", by_alias=True), fitted
    )
    bindings = report_lib.source_block_bindings(manifest_slice, outline_slice)
    return {
        "stamping": report_lib.stamp_assertions(htmls, bindings),
        "deck": report_lib.deck_readback(manifest_slice, outline_slice, htmls),
    }


async def run(args: argparse.Namespace) -> int:
    started = time.perf_counter()
    stages, missing = import_composer_stages(dry_run=args.dry_run)
    if stages is None:
        for line in missing:
            print(line)
        return EXIT_MISSING_STAGE
    from services.composer.contracts import source_px_scale
    from services.composer.pipeline_inputs import deck_title_px
    from services.composer.plan import ComposerDeckPlan

    timings: dict[str, float] = {}
    clock = time.perf_counter()
    try:
        bundle = await acquire_source(args)
    except SourceLookupError as error:
        print(f"input error: {error}")
        return EXIT_INPUT
    timings["extractMs"] = (time.perf_counter() - clock) * 1000.0
    out_dir = args.out or DEFAULT_OUT_ROOT / f"{bundle.label}-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
    out_dir.mkdir(parents=True, exist_ok=True)
    notes = list(bundle.notes)
    units = bundle.units
    deck_title = deck_title_from_units(units)
    style_id = (
        None
        if args.style is None or str(args.style).strip().casefold() == "none"
        else args.style
    )
    tokens, tokens_source = build_tokens(style_id, deck_title=deck_title)
    notes.append(f"tokens: {tokens_source}")
    context = build_deck_context(units, bundle.manifest, deck_title=deck_title)

    clock = time.perf_counter()
    plans = [
        stages.classify_unit(unit=unit, slide_index=index, deck_context=context.as_mapping())
        for index, unit in enumerate(units)
    ]
    timings["classifyMs"] = (time.perf_counter() - clock) * 1000.0
    deck = ComposerDeckPlan(
        slides=plans, tokens=tokens, source_page_emu=context.source_page_emu,
        source_px_scale=source_px_scale(context.source_page_emu),
        title_px=deck_title_px(plans, tokens),
    )
    fit_lib.write_json(out_dir / "plans.json", [plan.model_dump(mode="json") for plan in plans])
    fitted = plans[: args.limit] if args.limit else plans

    report: dict[str, Any] = {
        "generatedAt": datetime.now().astimezone().isoformat(timespec="seconds"),
        "dryRun": bool(args.dry_run),
        "providerCalls": 0,
        "source": bundle.summary(),
        "deckContext": context.as_mapping(),
        "tokens": tokens.model_dump(mode="json"),
        "enrichment": report_lib.enrichment_coverage(bundle.manifest),
        "slides": [fit_lib.slide_row(plan, None, None, None) for plan in fitted],
        "notes": notes,
    }
    if not args.dry_run:
        # Resolve image markers the way the pipeline does: every Chromium call
        # sees admitted HTML, and the accepted HTML is re-inlined afterwards so
        # the readback runs on exactly what was measured.
        from services.composer.assets import (
            AssetInliningMeasurer,
            ComposerAssetError,
            in_memory_asset_resolver,
            inline_result_assets,
        )

        resolver = in_memory_asset_resolver(bundle.assets)
        measure = AssetInliningMeasurer(stages.measure_slide, resolver)
        clock = time.perf_counter()
        outcomes = await fit_lib.fit_all(
            stages.fit_slide, fitted, tokens, deck,
            render=stages.render_slide, measure=measure, estimate=stages.estimate_fit,
            concurrency=args.concurrency, max_renders=args.max_renders,
        )
        inlined: list[Any] = []
        for plan, result, error, elapsed in outcomes:
            if result is not None:
                try:
                    result = (await inline_result_assets([result], resolver))[0]
                except ComposerAssetError as asset_error:
                    result, error = None, asset_error
            inlined.append((plan, result, error, elapsed))
        outcomes = inlined
        timings["fitTotalMs"] = (time.perf_counter() - clock) * 1000.0
        rows, htmls, png_paths = await fit_lib.persist_outcomes(
            outcomes, out_dir=out_dir, deck_size=len(plans), render_timeout_seconds=args.render_timeout, notes=notes
        )
        report["slides"] = rows
        clock = time.perf_counter()
        report["readback"] = _readback_section(bundle, htmls, len(fitted))
        timings["readbackMs"] = (time.perf_counter() - clock) * 1000.0
        clock = time.perf_counter()
        labels = [f"{row['slideIndex'] + 1} {row.get('archetype', '')} r{row.get('rung')}" for row in rows]
        report["contactSheets"] = [sheet.name for sheet in write_contact_sheets(out_dir, png_paths, labels)]
        timings["sheetsMs"] = (time.perf_counter() - clock) * 1000.0
    report["aggregate"] = report_lib.aggregate_slides(
        report["slides"], (report.get("readback") or {}).get("stamping")
    )
    timings["totalMs"] = (time.perf_counter() - started) * 1000.0
    report["timings"] = {key: round(value, 1) for key, value in timings.items()}
    report["acceptance"] = None if args.dry_run else report_lib.acceptance(report)
    fit_lib.write_json(out_dir / "report.json", report)
    fit_lib.write_text(out_dir / "report.md", render_markdown(report))
    print(f"wrote {out_dir}")
    if args.dry_run:
        return EXIT_OK
    return EXIT_OK if report["acceptance"]["passed"] else EXIT_FAILED


def main(argv: Sequence[str] | None = None) -> int:
    return asyncio.run(run(parse_args(argv)))


if __name__ == "__main__":
    raise SystemExit(main())
