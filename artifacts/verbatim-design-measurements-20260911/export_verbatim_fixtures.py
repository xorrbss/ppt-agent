"""Export per-slide fixtures for the verbatim placeholder-authoring gate work (T2).

One directory per slide under ``<out>/<run>/<slide-index>/`` containing only what
is actually stored:

* ``manifest.json``   — that slide's source unit and its blocks, from the stored manifest
* ``projection.json`` — ``project_source_runs`` output per text block of that unit
* ``html-render.txt`` — the published bytes (``slides.html_content``), when the deck published
* ``html-author.txt`` / ``html-illustrated.txt`` — checkpoint stages, when present
* ``verdict.json``    — what each path recorded for this slide at the time, the flag it
  carried, and an explicit note for every artefact that does not exist

Read-only: stored manifests, stored slide rows, stored checkpoints, stored run
artefacts. No generation, no provider call, no database write. No secret value is
read or written; the database URL stays in the caller's file.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, "C:/project/PPT-agent/ppt-agent/servers/fastapi")
from services.source_content.source_runs import project_source_runs  # noqa: E402

HERE = Path(__file__).resolve().parent
DB = HERE / "dburl.txt"
BASE = Path("C:/project/PPT-agent/ppt-agent/artifacts/authored-full-product-validation-20260910")

#: run -> (job id, basis). Runs 11-15 are the job ids recorded in
#: ``repeatability-20260911.md``; 18 and 19 come from their own failure artefacts;
#: 17 and 23 sit between confirmed neighbours in ``created_at`` order for the same
#: 35-slide input, which is why the basis is recorded per run rather than assumed.
#: run -> workspace operation id, read from each run's ``05-admission.json``. This
#: joins to ``presentation_generation_jobs.workspace_operation_id``, which is what
#: makes the run-to-job mapping checkable rather than inferred from timestamps.
OPERATIONS: dict[str, str] = {
    "run-11": "97267adb-db55-436a-afaf-02a31192a7fa",
    "run-13": "19a08008-3cbd-4cab-80dd-e76ec25a1144",
    "run-14": "8b55257f-9b08-4b68-9319-acdf405275c1",
    "run-15": "efebf8d1-f189-458b-8966-022a843eec44",
    "run-16": "38cc3aa5",
    "run-17": "72e0a06e-bb7f-4c84-90a8-87fe4d537c5f",
    "run-18": "2123782f-8b01-486f-b540-8a1815ee2976",
    "run-19": "ce3d44bf-4e36-4f89-8698-c312db29ca3a",
    "run-20": "5ce102a8",
    "run-21": "2298a9c5",
    "run-23": "9c046b62-7266-4ff7-8622-3f55365943c0",
}
JOB_ID_BASIS = (
    "05-admission.json operation_id -> "
    "presentation_generation_jobs.workspace_operation_id (authoritative join; "
    "agrees with the created_at ordering it replaces for all 11 runs)"
)

JOBS: dict[str, tuple[str, str]] = {
    "run-11": ("d185a6d0", "repeatability-20260911.md"),
    "run-13": ("88c1e3e6", "repeatability-20260911.md"),
    "run-14": ("2661e54f", "repeatability-20260911.md"),
    "run-15": ("89cf6fc9", "repeatability-20260911.md"),
    "run-16": ("9bc815e1", "created_at order, between run-15 and run-17"),
    "run-17": ("ce953f11", "created_at order, between run-16 and run-18"),
    "run-18": ("fca17341", "run-18/08-author-failure.json"),
    "run-19": ("0e66d677", "run-19/08-rescue-readback.json"),
    "run-20": ("999f04be", "created_at order, between run-19 and run-21"),
    "run-21": ("58fe371f", "created_at order, after run-20"),
    "run-23": ("32de70c4", "created_at order, last 35-slide job of the series"),
}
PUBLISHED = ("run-11", "run-15", "run-16", "run-20", "run-21")
FAILED = ("run-13", "run-14", "run-17", "run-18", "run-19", "run-23")
#: Runs exported in full rather than only at their failing slides: every slide that
#: kept authoring output. These are the only stored model output for the
#: "model output -> static checks" path, because a published deck's author_html is
#: cleared once it publishes.
FULL_AUTHOR_EXPORT = ("run-18", "run-19")


def walk(obj: Any, key: str, acc: list | None = None) -> list:
    acc = [] if acc is None else acc
    if isinstance(obj, dict):
        for name, value in obj.items():
            if name == key:
                acc.append(value)
            walk(value, key, acc)
    elif isinstance(obj, list):
        for value in obj:
            walk(value, key, acc)
    return acc


def run_artifacts(run: str) -> dict[str, Any]:
    """Everything the run directory recorded, without the large payloads."""

    out: dict[str, Any] = {"files": sorted(p.name for p in (BASE / run).glob("*"))}
    for name in ("06-final-status.json", "08-audit.json", "09-readback-failure.json",
                 "08-integrity-readback.json", "08-author-failure.json",
                 "08-rescue-readback.json"):
        path = BASE / run / name
        if not path.exists():
            continue
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
        out[name] = {
            key: (walk(payload, key) or [None])[0]
            for key in ("code", "detail", "blockIds", "block_ids", "visibilityFailures",
                        "missingBlockIds", "integrityFailures", "orderViolations",
                        "flaggedSlides", "reviewPages", "residualIssues")
            if walk(payload, key)
        }
    return out


def slide_index_of_blocks(manifest: dict[str, Any], block_ids: set[str]) -> set[int]:
    """Slide indices owning any of these source block ids, from the manifest."""

    wanted, found = set(block_ids), set()
    for position, unit in enumerate(manifest.get("units") or []):
        if not isinstance(unit, dict):
            continue
        index = unit.get("sourceIndex")
        index = position if not isinstance(index, int) else index
        for block in unit.get("blocks") or []:
            if isinstance(block, dict) and block.get("sourceBlockId") in wanted:
                found.add(int(index))
    return found


def unit_for_slide(manifest: dict[str, Any], index: int) -> dict[str, Any] | None:
    units = manifest.get("units") or []
    for unit in units:
        if isinstance(unit, dict) and unit.get("sourceIndex") == index:
            return unit
    return units[index] if 0 <= index < len(units) else None


def projection_of(unit: dict[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for block in unit.get("blocks") or []:
        if not isinstance(block, dict) or block.get("kind") != "text":
            continue
        block_id = block.get("sourceBlockId")
        raw = (block.get("payload") or {}).get("text")
        if not isinstance(block_id, str) or not isinstance(raw, str):
            continue
        try:
            projection = project_source_runs(raw)
        except ValueError as error:
            result[block_id] = {"error": str(error), "raw": raw}
            continue
        result[block_id] = {
            "text": projection.text,
            "run_count": projection.run_count,
            "lines": [list(span) for span in projection.lines],
            "runs": [
                {
                    "index": run.index,
                    "line": run.line,
                    "marker": run.marker,
                    "body": run.body,
                    "begin": run.begin,
                    "end": run.end,
                    "order_free_before": run.order_free_before,
                }
                for run in projection.runs
            ],
        }
    return result


def main(out_dir: str) -> int:
    import psycopg

    out = Path(out_dir)
    url = re.sub(r"^postgresql\+\w+://", "postgresql://", DB.read_text().strip())
    written = {"dirs": 0, "files": 0, "bytes": 0}
    index_rows: list[dict[str, Any]] = []

    def write(path: Path, text: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        written["files"] += 1
        written["bytes"] += len(text.encode("utf-8"))

    with psycopg.connect(url, connect_timeout=15, options="-c default_transaction_read_only=on") as c:
        c.execute("set statement_timeout='120s'")
        row = c.execute(
            "select manifest_json from source_content_manifests where source_digest like %s limit 1",
            ("sha256:7746aaed%",),
        ).fetchone()
        manifest = row[0] if not isinstance(row[0], str) else json.loads(row[0])

        for run, (job_prefix, basis) in JOBS.items():
            if run not in PUBLISHED and run not in FAILED:
                continue
            job = c.execute(
                "select id, state, presentation_id, completed_slides, created_at"
                " from presentation_generation_jobs where id::text like %s",
                (job_prefix + "%",),
            ).fetchone()
            if job is None:
                print(f"{run}: job {job_prefix} not found", flush=True)
                continue
            job_id, state, presentation_id, completed, created = job
            artefacts = run_artifacts(run)

            slides = {}
            if presentation_id:
                for index, html in c.execute(
                    "select index, html_content from slides where presentation = %s",
                    (str(presentation_id),),
                ).fetchall():
                    if html:
                        slides[int(index)] = html
            checkpoints = {
                int(index): {
                    "author_html": author,
                    "illustrated_html": illustrated,
                    "render_sha256": render_sha,
                    "failed_stage": failed_stage,
                    "error": error,
                    "author_status": author_status,
                    "render_status": render_status,
                }
                for index, author, illustrated, render_sha, failed_stage, error,
                    author_status, render_status in c.execute(
                    "select slide_index, author_html, illustrated_html, render_sha256,"
                    " failed_stage, error, author_status, render_status"
                    " from authored_slide_checkpoints where job_id = %s",
                    (job_id,),
                ).fetchall()
            }

            failing: set[int] = set()
            if run in PUBLISHED:
                targets = sorted(slides)
            elif run in FULL_AUTHOR_EXPORT:
                with_output = {
                    index for index, data in checkpoints.items()
                    if data["author_html"] or data["illustrated_html"]
                }
                failing = {
                    index for index, data in checkpoints.items() if data["failed_stage"]
                } | slide_index_of_blocks(
                    manifest,
                    {
                        str(value)
                        for payload in artefacts.values()
                        if isinstance(payload, dict)
                        for group in walk(payload, "blockIds") + walk(payload, "block_ids")
                        + walk(payload, "visibilityFailures") + walk(payload, "integrityFailures")
                        for value in (group if isinstance(group, list) else [])
                        if isinstance(value, str)
                    },
                )
                unauthored = {
                    index for index, data in checkpoints.items()
                    if not data["author_html"] and not data["illustrated_html"]
                }
                # The slide that never authored is the negative fixture: it has no
                # HTML by definition, so selecting only slides with output drops it.
                failing |= unauthored
                targets = sorted(with_output | failing)
            else:
                # Every way the record names a failing slide, unioned: an explicit
                # slide index, the slide owning a failing source block, a checkpoint
                # that recorded a failed stage, and a slide that never authored while
                # its neighbours did.
                named = {
                    int(value)
                    for payload in artefacts.values()
                    if isinstance(payload, dict)
                    for value in walk(payload, "slide_indices") + walk(payload, "slideIndices")
                    if isinstance(value, int)
                }
                blocks = {
                    str(value)
                    for payload in artefacts.values()
                    if isinstance(payload, dict)
                    for group in walk(payload, "blockIds") + walk(payload, "block_ids")
                    + walk(payload, "visibilityFailures") + walk(payload, "integrityFailures")
                    + walk(payload, "missingBlockIds")
                    for value in (group if isinstance(group, list) else [])
                    if isinstance(value, str)
                }
                by_block = slide_index_of_blocks(manifest, blocks)
                failing = {index for index, data in checkpoints.items() if data["failed_stage"]}
                unauthored = {
                    index for index, data in checkpoints.items()
                    if data["author_html"] is None
                } if any(data["author_html"] for data in checkpoints.values()) else set()
                failing = named | by_block | failing | unauthored
                targets = sorted(failing)
            print(f"{run}: job={str(job_id)[:8]} {state} slides={len(targets)}", flush=True)

            for index in targets:
                directory = out / run / f"{index:02d}"
                unit = unit_for_slide(manifest, index)
                missing: list[str] = []
                if unit is not None:
                    write(directory / "manifest.json", json.dumps(unit, ensure_ascii=False, indent=1))
                    write(
                        directory / "projection.json",
                        json.dumps(projection_of(unit), ensure_ascii=False, indent=1),
                    )
                else:
                    missing.append("manifest.json: no source unit for this index")
                    missing.append("projection.json: no source unit for this index")
                if index in slides:
                    write(directory / "html-render.txt", slides[index])
                else:
                    missing.append("html-render.txt: deck did not publish, no slides row")
                data = checkpoints.get(index, {})
                if data.get("author_html"):
                    write(directory / "html-author.txt", data["author_html"])
                else:
                    missing.append(
                        "html-author.txt: checkpoint author_html is null"
                        " (cleared after publication on published decks)"
                    )
                if data.get("illustrated_html"):
                    write(directory / "html-illustrated.txt", data["illustrated_html"])
                else:
                    missing.append("html-illustrated.txt: checkpoint illustrated_html is null")
                missing.append(
                    "metrics.json: browser metrics are not persisted anywhere"
                    " (authored_slide_checkpoints has no metrics column, artifact_refs is null,"
                    " authored_evidence_*_v3 are empty)"
                )
                data_for_flags = checkpoints.get(index, {})
                verdict = {
                    "run": run,
                    "slide_index": index,
                    # Why this slide is in the fixture set, so a consumer need not
                    # re-derive it: a slide the record names as failing, or one whose
                    # authoring output survived and is therefore static-check input.
                    "fixture_role": (
                        "failing_slide" if index in failing
                        else "authoring_passed" if (
                            data_for_flags.get("author_html")
                            or data_for_flags.get("illustrated_html")
                        ) else "published_slide"
                    ),
                    "is_failing_slide": index in failing,
                    "authoring_passed": bool(
                        data_for_flags.get("author_html")
                        or data_for_flags.get("illustrated_html")
                    ) and index not in failing,
                    "job_id": str(job_id),
                    "job_id_basis": JOB_ID_BASIS,
                    "job_id_basis_note": basis,
                    "workspace_operation_id": OPERATIONS.get(run),
                    "job_state": state,
                    "presentation_id": str(presentation_id) if presentation_id else None,
                    "completed_slides": completed,
                    "created_at": str(created),
                    "checkpoint": {
                        key: value
                        for key, value in data.items()
                        if key not in {"author_html", "illustrated_html"}
                    },
                    "run_artifacts": artefacts,
                    "absent": missing,
                }
                write(directory / "verdict.json", json.dumps(verdict, ensure_ascii=False, indent=1))
                written["dirs"] += 1
                index_rows.append(
                    {
                        "run": run,
                        "slide": index,
                        "job": str(job_id)[:8],
                        "state": state,
                        "has_render_html": index in slides,
                        "has_author_html": bool(data.get("author_html")),
                        "has_illustrated_html": bool(data.get("illustrated_html")),
                        "failed_stage": data.get("failed_stage"),
                        "fixture_role": verdict["fixture_role"],
                    }
                )

    write(out / "INDEX.json", json.dumps(index_rows, ensure_ascii=False, indent=1))
    print(
        f"\n{written['dirs']} slide dirs, {written['files']} files,"
        f" {written['bytes'] / 1_048_576:.1f} MiB"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1]))
