"""Generate the authored style sample decks (docs/authored-style-samples-generation-plan-20260928.md §2,
docs/authored-style-samples-design-20260928.md §8.2).

For every requested style: run ``scripts/ppt-agent.mjs`` against a running
local stack to generate a 12-slide deck in that style, accept it only if the
exported PDF has exactly 12 pages, every page is 16:9, and every order-guard
keyword from ``scripts/authored_style_samples_topic.md`` appears on its page;
rasterise the accepted deck to WebP stage/thumbnail images; and write
``manifest.json`` plus the images into ``<samples-root>/<style-id>/``. A
failed or rejected deck is retried once, then reported and skipped.

The samples OUTPUT root (``--samples-root``, this checkout's own
``servers/fastapi/authored_style_samples``) and the CODE tree the running
backend executes (``--code-tree``) are different directories: this script
never writes into ``--code-tree``, and refuses to generate unless that tree's
``servers/`` path is committed clean, so ``generatorCommit`` (its
``git rev-parse HEAD``) is trustworthy.

Usage:
    python scripts/generate_authored_style_samples.py \\
        --code-tree C:/project/PPT-agent/ppt-agent --scratch <dir> \\
        [--samples-root <dir>] [--styles a,b,c] [--parallel 1|2] \\
        [--base http://127.0.0.1:8000] [--dry-run]
"""
from __future__ import annotations

import argparse
import json
import re
import shlex
import shutil
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

FASTAPI_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(FASTAPI_ROOT))

from scripts.authored_style_samples_contact_sheet import (  # noqa: E402
    ContactSheetRow,
    write_contact_sheet,
)
from scripts.authored_style_samples_pdf import (  # noqa: E402
    RasterResult,
    check_deck_acceptance,
    rasterize_page,
    read_pdf_pages,
)
from scripts.authored_style_samples_runner import (  # noqa: E402
    DECK_SLIDES,
    build_ppt_agent_command,
    find_deck_pdf,
    run_ppt_agent,
)
from scripts.authored_style_samples_topic_parser import FixedParams, load_topic  # noqa: E402
from scripts.check_authored_style_samples import run_check  # noqa: E402
from utils.authored_style_catalogue import authored_style_catalogue  # noqa: E402
from utils.authored_style_samples import (  # noqa: E402
    AUTHORED_STYLE_SAMPLES_DIRECTORY,
    AUTHORED_STYLE_SAMPLES_TOPIC_FILE,
    style_render_digest,
    topic_prompt_digest,
)
from utils.authored_styles import (  # noqa: E402
    AUTHORED_STYLE_ASSETS_DIRECTORY,
    AUTHORED_STYLES_DIRECTORY,
    load_authored_styles,
)

DEMO_TOPIC_ID = "business-plan-v1"
MANIFEST_SCHEMA = "authored-style-samples/v1"
CANVAS = (1280, 720)
DEFAULT_BASE = "http://127.0.0.1:8000"
# One deck is ~10-12 min, but the server runs one owner's decks one at a time,
# so a deck can also wait for others ahead of it in the queue.
PPT_AGENT_TIMEOUT_SECONDS = 3600
MAX_ATTEMPTS = 2  # one retry per deck (plan §2)
_COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")


class GeneratorRefused(RuntimeError):
    """Raised when the code tree is not safe to generate from (dirty / no HEAD)."""


@dataclass
class DeckResult:
    style_id: str
    ok: bool
    presentation_id: Optional[str] = None
    problems: tuple[str, ...] = ()
    attempts: int = 0
    thumbnails: tuple[str, ...] = ()


# ---------------------------------------------------------------------------
# Code-tree safety (plan §1.1): every deck requires a clean, resolvable HEAD.
# ---------------------------------------------------------------------------


def _git_status(code_tree: Path, untracked: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(code_tree), "status", "--porcelain", f"--untracked-files={untracked}", "--", "servers"],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise GeneratorRefused(f"git status failed for {code_tree}: {result.stderr.strip()}")
    return result.stdout


def ensure_clean_code_tree(code_tree: Path) -> list[str]:
    """Refuse if a tracked file under ``servers/`` differs from HEAD.

    A modified or deleted tracked file changes what the running backend
    executes, so ``generatorCommit`` would be false. An untracked new file
    cannot: code at HEAD does not reference a module HEAD lacks. Such files
    are returned so each deck records what else was present in the tree.
    """
    tracked = _git_status(code_tree, "no")
    if tracked.strip():
        raise GeneratorRefused(
            f"{code_tree} has modified tracked files under servers/; refusing to generate "
            f"(generatorCommit would not be trustworthy):\n{tracked}"
        )
    return sorted(
        line[3:] for line in _git_status(code_tree, "all").splitlines() if line.startswith("?? ")
    )


def get_generator_commit(code_tree: Path) -> str:
    result = subprocess.run(
        ["git", "-C", str(code_tree), "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0 or not result.stdout.strip():
        raise GeneratorRefused(f"could not resolve HEAD for {code_tree}: {result.stderr.strip()}")
    commit = result.stdout.strip()
    if not _COMMIT_RE.fullmatch(commit):
        raise GeneratorRefused(f"unexpected commit hash from {code_tree}: {commit!r}")
    return commit


# ---------------------------------------------------------------------------
# Manifest
# ---------------------------------------------------------------------------


def build_manifest(
    style_id: str,
    titles: list[str],
    canvas: tuple[int, int],
    generator_commit: str,
    style_digest: str,
    topic_digest: str,
) -> dict:
    return {
        "schema": MANIFEST_SCHEMA,
        "styleId": style_id,
        "styleDigest": style_digest,
        "topicDigest": topic_digest,
        "demoTopicId": DEMO_TOPIC_ID,
        "canvas": {"width": canvas[0], "height": canvas[1]},
        "generatedAt": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "generatorCommit": generator_commit,
        "slides": [{"title": title} for title in titles],
    }


def write_json_lf(path: Path, data: dict) -> None:
    text = json.dumps(data, indent=2, ensure_ascii=False) + "\n"
    path.write_text(text, encoding="utf-8", newline="\n")


# ---------------------------------------------------------------------------
# One style's deck: generate, accept, rasterise, write.
# ---------------------------------------------------------------------------


def _attempt_once(
    style_id: str,
    prompt: str,
    fixed: FixedParams,
    args: argparse.Namespace,
    repo_root: Path,
    attempt_scratch: Path,
) -> tuple[Optional[list[RasterResult]], Optional[str], list[str]]:
    """One generate+accept+rasterise attempt.

    Returns ``(rasters_or_None, presentation_id, problems)``. ``rasters`` is
    ``None`` unless the deck was fully accepted and rasterised.
    """
    result = run_ppt_agent(
        repo_root, prompt, style_id, attempt_scratch, args.base, args.timeout_seconds
    )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip()[-2000:]
        return None, result.presentation_id, [f"ppt-agent.mjs exited {result.returncode}: {detail}"]

    pdf_path = find_deck_pdf(attempt_scratch, result.downloaded_path)
    if pdf_path is None:
        return None, result.presentation_id, ["no exported PDF found in the ppt-agent.mjs output directory"]

    pages = read_pdf_pages(pdf_path)
    problems = check_deck_acceptance(pages, DECK_SLIDES, fixed.guard_keywords)
    if problems:
        return None, result.presentation_id, problems

    rasters: list[RasterResult] = []
    raster_problems: list[str] = []
    for page in pages:
        raster = rasterize_page(pdf_path, page.index, CANVAS)
        if raster is None:
            raster_problems.append(f"slide {page.index}: could not fit WebP within the byte caps")
        else:
            rasters.append(raster)
    if raster_problems:
        return None, result.presentation_id, raster_problems

    return rasters, result.presentation_id, []


def _write_deck(samples_root: Path, style_id: str, manifest: dict, rasters: list[RasterResult]) -> None:
    """Write into a temp dir under samples_root, then rename over any existing directory."""
    target = samples_root / style_id
    temp = samples_root / f".tmp-{style_id}"
    if temp.exists():
        shutil.rmtree(temp)
    temp.mkdir(parents=True)
    write_json_lf(temp / "manifest.json", manifest)
    for index, raster in enumerate(rasters, start=1):
        (temp / f"{index:02d}.webp").write_bytes(raster.stage_bytes)
        (temp / f"{index:02d}.thumb.webp").write_bytes(raster.thumb_bytes)
    if target.exists():
        shutil.rmtree(target)
    temp.rename(target)


def generate_one_style(
    style_id: str,
    prompt: str,
    fixed: FixedParams,
    args: argparse.Namespace,
    repo_root: Path,
    samples_root: Path,
    scratch: Path,
) -> DeckResult:
    style_scratch = scratch / style_id
    problems: list[str] = []
    presentation_id: Optional[str] = None
    attempts = 0
    started = time.monotonic()

    for attempt in range(1, MAX_ATTEMPTS + 1):
        attempts = attempt
        # Checked before AND after each attempt: a deck takes minutes, and a
        # tracked edit landing mid-generation would taint it just the same.
        untracked = set(ensure_clean_code_tree(args.code_tree))
        generator_commit = get_generator_commit(args.code_tree)

        attempt_scratch = style_scratch / f"attempt-{attempt}"
        rasters, presentation_id, problems = _attempt_once(
            style_id, prompt, fixed, args, repo_root, attempt_scratch
        )
        untracked |= set(ensure_clean_code_tree(args.code_tree))
        if get_generator_commit(args.code_tree) != generator_commit:
            raise GeneratorRefused(
                f"{args.code_tree} HEAD moved during generation of {style_id}; the deck's code version is unknown"
            )
        if rasters is not None:
            titles = list(fixed.titles)
            if len(titles) != len(rasters):
                problems = [f"topic lists {len(titles)} titles but the deck has {len(rasters)} slides"]
                break
            style_digest = style_render_digest(
                AUTHORED_STYLES_DIRECTORY / f"{style_id}.yaml", AUTHORED_STYLE_ASSETS_DIRECTORY
            )
            topic_digest = topic_prompt_digest(AUTHORED_STYLE_SAMPLES_TOPIC_FILE)
            manifest = build_manifest(style_id, titles, CANVAS, generator_commit, style_digest, topic_digest)
            _write_deck(samples_root, style_id, manifest, rasters)

            elapsed = round(time.monotonic() - started, 1)
            result_json = {
                "styleId": style_id,
                "presentationId": presentation_id,
                "attempts": attempt,
                "elapsedSeconds": elapsed,
                "stageQualities": [r.stage_quality for r in rasters],
                "thumbQualities": [r.thumb_quality for r in rasters],
                "stageBytes": [len(r.stage_bytes) for r in rasters],
                "thumbBytes": [len(r.thumb_bytes) for r in rasters],
                "untrackedCodeTreeFiles": sorted(untracked),
            }
            style_scratch.mkdir(parents=True, exist_ok=True)
            write_json_lf(style_scratch / "result.json", result_json)

            thumbnails = tuple(f"{style_id}/{index:02d}.thumb.webp" for index in range(1, len(rasters) + 1))
            return DeckResult(
                style_id=style_id,
                ok=True,
                presentation_id=presentation_id,
                attempts=attempt,
                thumbnails=thumbnails,
            )
        # Rejected or failed: fall through and retry (once).

    style_scratch.mkdir(parents=True, exist_ok=True)
    write_json_lf(
        style_scratch / "result.json",
        {
            "styleId": style_id,
            "presentationId": presentation_id,
            "attempts": attempts,
            "elapsedSeconds": round(time.monotonic() - started, 1),
            "problems": problems,
        },
    )
    return DeckResult(
        style_id=style_id,
        ok=False,
        presentation_id=presentation_id,
        problems=tuple(problems),
        attempts=attempts,
    )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _default_style_ids() -> list[str]:
    catalogue = authored_style_catalogue(load_authored_styles(AUTHORED_STYLES_DIRECTORY))
    return sorted(style["id"] for style in catalogue)


def parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--code-tree", type=Path, required=True, help="checkout the running backend executes")
    parser.add_argument(
        "--samples-root",
        type=Path,
        default=AUTHORED_STYLE_SAMPLES_DIRECTORY,
        help="where accepted decks are written (default: this checkout's authored_style_samples)",
    )
    parser.add_argument("--styles", type=str, default=None, help="comma-separated style ids (default: all public)")
    parser.add_argument("--parallel", type=int, choices=(1, 2), default=1)
    parser.add_argument("--scratch", type=Path, required=True, help="working directory for PDFs and result.json")
    parser.add_argument("--dry-run", action="store_true", help="print the plan; generate nothing")
    parser.add_argument("--base", type=str, default=DEFAULT_BASE)
    parser.add_argument("--timeout-seconds", type=int, default=PPT_AGENT_TIMEOUT_SECONDS)
    return parser.parse_args(argv)


def _print_dry_run(style_ids: list[str], prompt: str, args: argparse.Namespace, repo_root: Path) -> None:
    for style_id in style_ids:
        out_dir = args.scratch / style_id / "attempt-1"
        command = build_ppt_agent_command(repo_root, prompt, style_id, out_dir, args.base, args.timeout_seconds)
        print(f"style: {style_id}")
        print(f"  command: {shlex.join(command)}")
        print(f"  target:  {args.samples_root / style_id}")


def main(argv: Optional[list[str]] = None) -> int:
    args = parse_args(argv)
    repo_root = FASTAPI_ROOT.parent.parent

    prompt, fixed = load_topic(AUTHORED_STYLE_SAMPLES_TOPIC_FILE)
    style_ids = sorted(s.strip() for s in args.styles.split(",") if s.strip()) if args.styles else _default_style_ids()
    if not style_ids:
        print("no styles requested", file=sys.stderr)
        return 1

    if args.dry_run:
        _print_dry_run(style_ids, prompt, args, repo_root)
        return 0

    args.samples_root.mkdir(parents=True, exist_ok=True)
    args.scratch.mkdir(parents=True, exist_ok=True)

    results: dict[str, DeckResult] = {}
    if args.parallel == 1:
        for style_id in style_ids:
            results[style_id] = generate_one_style(
                style_id, prompt, fixed, args, repo_root, args.samples_root, args.scratch
            )
    else:
        with ThreadPoolExecutor(max_workers=args.parallel) as executor:
            futures = {
                executor.submit(
                    generate_one_style, style_id, prompt, fixed, args, repo_root, args.samples_root, args.scratch
                ): style_id
                for style_id in style_ids
            }
            for future, style_id in futures.items():
                results[style_id] = future.result()

    rows = [
        ContactSheetRow(
            style_id=style_id,
            ok=result.ok,
            presentation_id=result.presentation_id,
            thumbnails=result.thumbnails,
            problems=result.problems,
        )
        for style_id, result in ((sid, results[sid]) for sid in style_ids)
    ]
    write_contact_sheet(args.scratch / "contact-sheet.html", rows)

    failed = [style_id for style_id, result in results.items() if not result.ok]
    for style_id in failed:
        print(f"FAILED {style_id}: {'; '.join(results[style_id].problems)}", file=sys.stderr)
    print(f"{len(style_ids) - len(failed)}/{len(style_ids)} decks accepted")

    problems, report = run_check(samples_root=args.samples_root)
    for line in problems:
        print(line)
    for line in report:
        print(line)

    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
