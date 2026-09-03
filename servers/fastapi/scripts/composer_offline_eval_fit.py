"""Fit-stage driver and per-slide evidence rows for the Composer offline harness.

File writes are plain synchronous helpers so the asyncio driver never performs
blocking calls itself; the harness is a script, not a request handler.
"""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from typing import Any, Callable, Sequence

#: (plan, result or None, error or None, wall milliseconds)
FitOutcome = tuple[Any, Any, BaseException | None, float]


def write_text(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8")


def write_bytes(path: Path, data: bytes) -> None:
    path.write_bytes(data)


def write_json(path: Path, payload: Any) -> None:
    write_text(path, json.dumps(payload, ensure_ascii=False, indent=2, default=str))


def read_existing(path: str | None) -> bytes | None:
    candidate = Path(path) if path else None
    return candidate.read_bytes() if candidate is not None and candidate.is_file() else None


def slide_file_stem(index: int, count: int) -> str:
    return f"slide-{index + 1:0{max(2, len(str(count)))}d}"


def parameters_payload(params: Any) -> dict[str, Any]:
    return {
        "chromeLevel": params.chrome_level,
        "density": params.density,
        "typeScale": params.type_scale,
        "sourceRelative": params.source_relative,
        "sourceFactor": params.source_factor,
        "sourceGeometry": params.source_geometry,
        "pageScale": params.page_scale,
        "columns": params.columns,
    }


def slide_row(plan: Any, result: Any, error: BaseException | None, fit_ms: float | None) -> dict[str, Any]:
    """One ``report.json`` row: plan facts plus the fit receipt when there is one."""

    row: dict[str, Any] = {
        "slideIndex": plan.slide_index,
        "index": None,
        "chrome": None,
        "sourceUnitId": plan.source_unit_id,
        "archetype": plan.archetype,
        "components": len(plan.components),
        "images": len(plan.images),
        "fitMs": round(fit_ms, 1) if fit_ms is not None else None,
        "rung": None,
        "parameters": {},
        "renders": None,
        "blockingIssues": [],
        "trail": [],
        "error": f"{type(error).__name__}: {error}" if error is not None else None,
    }
    if result is not None:
        receipt = result.receipt
        row.update(
            index=receipt.slide_index,
            chrome=receipt.parameters.chrome_level,
            rung=receipt.rung,
            parameters=parameters_payload(receipt.parameters),
            renders=receipt.renders,
            estimateUsed=receipt.estimate_used,
            blockingIssues=list(receipt.blocking_issues),
            trail=list(receipt.trail),
        )
    return row


async def png_bytes(result: Any, render_timeout_seconds: int) -> bytes | None:
    """PNG of the accepted render: the fit's own file, else the cached capture."""

    existing = read_existing(result.png_path)
    if existing is not None:
        return existing
    from utils.slide_capture import render_html_to_png_with_metrics

    capture = await render_html_to_png_with_metrics(result.html, timeout=render_timeout_seconds)
    return capture.png


async def fit_all(
    fit_slide: Callable[..., Any],
    plans: Sequence[Any],
    tokens: Any,
    deck: Any,
    *,
    render: Callable[..., Any],
    measure: Callable[..., Any],
    estimate: Callable[..., Any] | None,
    concurrency: int,
    max_renders: int | None,
) -> list[FitOutcome]:
    """Fit every plan with at most ``concurrency`` Chromium fits in flight."""

    semaphore = asyncio.Semaphore(concurrency)
    extra: dict[str, Any] = {"max_renders": max_renders} if max_renders else {}

    async def fit_one(plan: Any) -> FitOutcome:
        async with semaphore:
            started = time.perf_counter()
            try:
                result = await fit_slide(plan, tokens, deck, render=render, measure=measure, estimate=estimate, **extra)
            except Exception as error:  # noqa: BLE001 - one slide's failure must not hide the others
                return plan, None, error, (time.perf_counter() - started) * 1000.0
            return plan, result, None, (time.perf_counter() - started) * 1000.0

    return list(await asyncio.gather(*(fit_one(plan) for plan in plans)))


async def persist_outcomes(
    outcomes: Sequence[FitOutcome],
    *,
    out_dir: Path,
    deck_size: int,
    render_timeout_seconds: int,
    notes: list[str],
) -> tuple[list[dict[str, Any]], list[str], list[Path | None]]:
    """Write ``slide-NN.html``/``.png`` and return rows, HTML strings and PNG paths."""

    rows: list[dict[str, Any]] = []
    htmls: list[str] = []
    png_paths: list[Path | None] = []
    for plan, result, error, fit_ms in outcomes:
        row = slide_row(plan, result, error, fit_ms)
        if result is None:
            htmls.append("")
            png_paths.append(None)
            rows.append(row)
            continue
        stem = slide_file_stem(plan.slide_index, deck_size)
        write_text(out_dir / f"{stem}.html", result.html)
        row["html"] = f"{stem}.html"
        htmls.append(result.html)
        try:
            png = await png_bytes(result, render_timeout_seconds)
        except Exception as png_error:  # noqa: BLE001 - PNG is evidence, not a gate
            notes.append(f"slide {plan.slide_index + 1}: PNG capture failed ({png_error})")
            png = None
        if png:
            write_bytes(out_dir / f"{stem}.png", png)
            row["png"] = f"{stem}.png"
            png_paths.append(out_dir / f"{stem}.png")
        else:
            png_paths.append(None)
        rows.append(row)
    return rows, htmls, png_paths


__all__ = [
    "FitOutcome",
    "fit_all",
    "parameters_payload",
    "persist_outcomes",
    "png_bytes",
    "read_existing",
    "slide_file_stem",
    "slide_row",
    "write_bytes",
    "write_json",
    "write_text",
]
