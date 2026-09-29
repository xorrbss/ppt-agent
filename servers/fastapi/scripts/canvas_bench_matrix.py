"""Unattended, resumable, serial pooled-matrix runs for the canvas bench.

A matrix is ``decks`` per canvas spread round-robin over the manuscripts
(deck j uses manuscript ``j % M``, repeat ``j // M``), interleaved across
canvases in seeded random order so time drift is shared between arms.  One deck
runs at a time.  A deck whose receipt already exists in the out dir is skipped,
so a killed or rebooted run is resumed by re-issuing the same command.  A deck
lost to the environment (backend unreachable) is recorded as
``invalid: infra_lost`` and stops the run (exit 3); the next invocation reruns
it and keeps the record under ``previousInvalid``.
"""

from __future__ import annotations

import json
import random
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

INFRA_ERRORS = (httpx.TransportError, ConnectionError)
EXIT_INFRA_LOST = 3


@dataclass(frozen=True)
class Deck:
    canvas: str
    manuscript: Path
    repeat: int

    @property
    def stem(self) -> str:
        return f"{self.manuscript.stem}-r{self.repeat:02d}"


def parse_canvas_decks(items: Sequence[str]) -> dict[str, int]:
    """``["wide-16x9=18", "square-1x1=12"]`` -> ``{"wide-16x9": 18, ...}``."""

    decks: dict[str, int] = {}
    for item in items:
        canvas, _, count = item.partition("=")
        if not canvas or not count.isdigit() or int(count) < 1 or canvas in decks:
            raise ValueError(f"--canvas-decks expects unique CANVAS=N (N >= 1), got {item!r}")
        decks[canvas] = int(count)
    if not decks:
        raise ValueError("no canvases in the matrix")
    return decks


def plan_matrix(canvas_decks: Mapping[str, int], manuscripts: Sequence[Path], seed: int) -> list[Deck]:
    if not manuscripts:
        raise ValueError("no manuscripts")
    blocks: dict[tuple[int, int], list[Deck]] = {}
    for canvas, count in canvas_decks.items():
        for j in range(count):
            index = j % len(manuscripts)
            repeat = j // len(manuscripts) + 1
            blocks.setdefault((repeat, index), []).append(Deck(canvas, manuscripts[index], repeat))
    rng = random.Random(seed)
    plan: list[Deck] = []
    for key in sorted(blocks):
        block = sorted(blocks[key], key=lambda d: d.canvas)
        rng.shuffle(block)
        plan += block
    return plan


def receipt_path(out_dir: Path, deck: Deck) -> Path:
    return out_dir / deck.canvas / f"{deck.stem}.receipt.json"


def _read(path: Path) -> dict[str, Any] | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def is_done(path: Path) -> bool:
    """A receipt exists, parses and is not an environment-lost stub."""

    receipt = _read(path)
    return receipt is not None and not receipt.get("invalid")


def pending(plan: Sequence[Deck], out_dir: Path) -> list[Deck]:
    return [deck for deck in plan if not is_done(receipt_path(out_dir, deck))]


def infra_lost_receipt(deck: Deck, base: Mapping[str, Any], error: BaseException) -> dict[str, Any]:
    return {
        **base, "createdAt": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "manuscript": deck.manuscript.name, "canvas": deck.canvas, "presentationId": None, "slideCount": 0,
        "generation": {"ok": False, "status": "infra_lost", "error": f"{type(error).__name__}: {error}"[:400]},
        "timing": {"totalSeconds": None, "perSlideSeconds": None}, "retries": {"total": None},
        "gate": {"blockers": [], "flags": []}, "export": {"ok": False, "error": "infra_lost", "sizeMatches": None},
        "invalid": {"reason": "infra_lost", "note": f"{type(error).__name__}: {error}"[:400]},
    }


def run_matrix(
    plan: Sequence[Deck], out_dir: Path, run_deck: Callable[[Deck], dict[str, Any]], base_receipt: Mapping[str, Any],
    write: Callable[[Path, Any], None], log: Callable[[str], None] = print,
) -> int:
    """Run the pending decks serially; 0 when all are done, 3 after an infra loss."""

    todo = pending(plan, out_dir)
    log(f"matrix: {len(plan)} decks planned, {len(plan) - len(todo)} already done, {len(todo)} to run")
    for number, deck in enumerate(todo, 1):
        path = receipt_path(out_dir, deck)
        previous = _read(path)
        log(f"[{number}/{len(todo)}] {deck.canvas} {deck.stem} ...")
        try:
            receipt = run_deck(deck)
        except INFRA_ERRORS as error:
            write(path, infra_lost_receipt(deck, base_receipt, error))
            log(f"  infra_lost: {type(error).__name__}: {error}; recorded, stopping (rerun to resume)")
            return EXIT_INFRA_LOST
        if previous and previous.get("invalid"):
            receipt["previousInvalid"] = [*(previous.get("previousInvalid") or []), previous["invalid"]]
        write(path, receipt)
        log(f"  -> {path.name} generation={receipt['generation']['ok']} export={receipt['export']['ok']}")
    return 0
