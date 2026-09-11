"""Export the 43-page / 96-image contract manifest and its run projection (T5).

`source-content.template-restyle-contract@7` binds 43 pages and 96 image
occurrences; 3.4 found the stored manifest that matches both exactly. T5 needs the
manifest itself plus the projected runs of every text block.

Read-only: one stored manifest row. No generation, no provider call, no database
write, no stack contact. The output carries no credential: a manifest holds source
ids, text, geometry and digests only, and the export asserts that before writing.
"""
from __future__ import annotations
import hashlib, json, re, sys
from pathlib import Path
from typing import Any

sys.path.insert(0, "C:/project/PPT-agent/ppt-agent/servers/fastapi")
from services.source_content.source_runs import project_source_runs  # noqa: E402

DB = Path(__file__).resolve().parent / "dburl.txt"
DIGEST_PREFIX = "sha256:32b098831a847"
SECRET_HINT = re.compile(
    r"postgresql://|password|api[_-]?key|secret|authorization|bearer\s|@127\.0\.0\.1:", re.I
)


def projection_of(manifest: dict[str, Any]) -> tuple[dict[str, Any], dict[str, int]]:
    out: dict[str, Any] = {}
    stats = {"text_blocks": 0, "projected": 0, "failed": 0, "runs": 0, "multi_run": 0}
    for unit in manifest.get("units") or []:
        for block in (unit or {}).get("blocks") or []:
            if not isinstance(block, dict) or block.get("kind") != "text":
                continue
            stats["text_blocks"] += 1
            block_id = block.get("sourceBlockId")
            raw = (block.get("payload") or {}).get("text")
            if not isinstance(block_id, str) or not isinstance(raw, str):
                continue
            try:
                projection = project_source_runs(raw)
            except ValueError as error:
                out[block_id] = {"error": str(error), "raw": raw}
                stats["failed"] += 1
                continue
            stats["projected"] += 1
            stats["runs"] += projection.run_count
            stats["multi_run"] += projection.run_count > 1
            out[block_id] = {
                "sourceUnitId": unit.get("sourceUnitId"),
                "sourceIndex": unit.get("sourceIndex"),
                "text": projection.text,
                "run_count": projection.run_count,
                "lines": [list(span) for span in projection.lines],
                "runs": [
                    {
                        "index": run.index, "line": run.line, "marker": run.marker,
                        "body": run.body, "begin": run.begin, "body_begin": run.body_begin,
                        "end": run.end, "order_free_before": run.order_free_before,
                    }
                    for run in projection.runs
                ],
            }
    return out, stats


def main(out_dir: str) -> int:
    import psycopg

    url = re.sub(r"^postgresql\+\w+://", "postgresql://", DB.read_text().strip())
    with psycopg.connect(url, connect_timeout=15, options="-c default_transaction_read_only=on") as c:
        c.execute("set statement_timeout='90s'")
        rows = c.execute(
            "select reference_snapshot_id, source_digest, manifest_digest, contract_version,"
            " source_type, manifest_json, counts_json, created_at"
            " from source_content_manifests where source_digest like %s",
            (DIGEST_PREFIX + "%",),
        ).fetchall()
    if not rows:
        print(f"no manifest for {DIGEST_PREFIX}")
        return 1
    # The same source was analysed 35 times and every row carries its own
    # manifest_digest, so one must be chosen deliberately. Group by the text
    # payload -- block ids and their text -- because that is what a fixture is.
    groups: dict[str, list[tuple]] = {}
    for row in rows:
        payload = row[5] if not isinstance(row[5], str) else json.loads(row[5])
        fingerprint = hashlib.sha256(
            json.dumps(
                [
                    (block.get("sourceBlockId"), (block.get("payload") or {}).get("text"))
                    for unit in payload.get("units") or []
                    for block in (unit or {}).get("blocks") or []
                    if isinstance(block, dict) and block.get("kind") == "text"
                ],
                ensure_ascii=False,
            ).encode("utf-8")
        ).hexdigest()[:12]
        groups.setdefault(fingerprint, []).append((row, payload))
    ordered = sorted(groups.items(), key=lambda item: -len(item[1]))
    current_fp, current_rows = ordered[0]
    superseded = [
        {"fingerprint": fp, "rows": len(items),
         "created": [str(item[0][7] if len(item[0]) > 7 else "") for item in items][:1]}
        for fp, items in ordered[1:]
    ]
    chosen_row, manifest = max(current_rows, key=lambda item: item[0][7])
    snapshot, digest, manifest_digest, contract, source_type, _json, counts, created = chosen_row

    units = manifest.get("units") or []
    images = sum(
        1 for u in units for b in (u.get("blocks") or [])
        if isinstance(b, dict) and b.get("kind") == "image"
    )
    if len(units) != 43 or images != 96:
        print(f"contract mismatch: units={len(units)} image_blocks={images}; expected 43/96")
        return 1

    projection, stats = projection_of(manifest)
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(manifest, ensure_ascii=False, indent=1)
    proj = json.dumps(projection, ensure_ascii=False, indent=1)
    for name, text in (("manifest.json", payload), ("projection.json", proj)):
        hit = SECRET_HINT.search(text)
        if hit:
            print(f"refusing to write {name}: secret-shaped content at {hit.start()}")
            return 1
        (out / name).write_text(text, encoding="utf-8")
    (out / "README.json").write_text(
        json.dumps(
            {
                "contract": "source-content.template-restyle-contract@7",
                "referenceSnapshotId": str(snapshot),
                "sourceDigest": str(digest),
                "manifestDigest": str(manifest_digest),
                "contractVersion": contract,
                "sourceType": source_type,
                "units": len(units),
                "imageBlocks": images,
                "counts": counts,
                "projection": stats,
                "chosenCreatedAt": str(created),
                "textPayloadFingerprint": current_fp,
                "equivalentRows": len(current_rows),
                "supersededGroups": superseded,
                "selection": (
                    "The same source_digest has 35 manifest rows, each with its own"
                    " manifest_digest. Rows were grouped by text payload (block ids and"
                    " their text); the largest group is the current id assignment and is"
                    " identical across its rows, so the latest row of that group was taken."
                    " The smaller group is an older id assignment, superseded on 2026-08-27:"
                    " 450 ids exist in only one side and 425 shared ids carry different text."
                ),
                "note": (
                    "Exported read-only from source_content_manifests for T5. "
                    "projection.json is project_source_runs() per text block, keyed by "
                    "sourceBlockId, with the unit it belongs to."
                ),
            },
            ensure_ascii=False,
            indent=1,
        ),
        encoding="utf-8",
    )
    total = sum((out / n).stat().st_size for n in ("manifest.json", "projection.json", "README.json"))
    print(f"units={len(units)} image_blocks={images} | projection {stats}")
    print(f"wrote {out} ({total / 1_048_576:.1f} MiB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1]))
