"""Pure helpers of ``run_eval.py`` for resuming and recovering a matrix (no network, no database)."""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path


def atomic_write_json(path: Path, data) -> None:
    """Write ``data`` as JSON to ``path`` through a temp file in the same directory and a rename, so a reader
    (or a restart) never sees a half-written result."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temp = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            json.dump(data, stream, ensure_ascii=False, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp, path)
    except BaseException:
        try:
            os.unlink(temp)
        except OSError:
            pass
        raise


def load_existing(path: Path, label: str) -> dict | None:
    """The result file at ``path`` when it exists and has ``label``; ``None`` when absent. A file of another
    label is an error (it would be overwritten)."""
    path = Path(path)
    if not path.exists():
        return None
    result = json.loads(path.read_text(encoding="utf-8"))
    if result.get("label") != label:
        raise SystemExit(f"{path} holds label {result.get('label')!r}, not {label!r}; refusing to resume over it")
    return result


def plan_resume(existing_runs: list[dict], cell_ids: list[str], runs_per_cell: int
                ) -> tuple[list[dict], dict[str, tuple[int, int]]]:
    """``(kept, todo)``: the completed runs to keep (at most ``runs_per_cell`` per cell; failed runs and runs of
    cells outside the matrix are dropped) and, per cell still short, ``(first_run_number, how_many)``."""
    kept: list[dict] = []
    todo: dict[str, tuple[int, int]] = {}
    for cell in cell_ids:
        done = [item for item in existing_runs if item.get("cell") == cell and item.get("completed")]
        done = sorted(done, key=lambda item: item.get("run", 0))[:runs_per_cell]
        kept += done
        if len(done) < runs_per_cell:
            todo[cell] = (max([item.get("run", 0) for item in done], default=0) + 1, runs_per_cell - len(done))
    return kept, todo


def template_id_of(payload: dict | None) -> str | None:
    """The template id a stored generation request pins (snake or camel case)."""
    pin = (payload or {}).get("svg_template_pin") or (payload or {}).get("svgTemplatePin") or {}
    return pin.get("template_id") or pin.get("templateId")


def map_job_to_cell(payload: dict | None, cells: list[dict], inputs: dict[str, str]) -> str | None:
    """The matrix cell id of a stored request: the template by pin id and the input by its exact content
    (``inputs`` maps input name to text). ``None`` when it is not one of the matrix cells."""
    template = template_id_of(payload)
    content = (payload or {}).get("content")
    if not template or not content:
        return None
    for cell in cells:
        pinned = template_id_of(cell["payload"])
        if pinned == template and inputs.get(cell["input"]) == content:
            return cell["cell"]
    return None


def newest_per_cell(candidates: list[dict], after=None) -> dict[str, dict]:
    """From ``[{"cell", "created_at", ...}]`` the newest candidate per cell, ignoring those created at or before
    ``after`` (the newest presentation of the database this one was cloned from)."""
    best: dict[str, dict] = {}
    for item in candidates:
        if item.get("cell") is None:
            continue
        if after is not None and item["created_at"] <= after:
            continue
        if item["cell"] not in best or item["created_at"] > best[item["cell"]]["created_at"]:
            best[item["cell"]] = item
    return best
