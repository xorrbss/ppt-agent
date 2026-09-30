"""Replay static admission on offline checkpoint fixtures, without Chromium."""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "servers" / "fastapi"))

def replay(fixtures: Path) -> dict:
    from services.authored_admission import admit_slide_html
    from services.authored_admission_context import context_from_source
    from services.source_content.text_stamping import stamp_source_blocks
    from services.generation_core.authoring.author_slide import validate_authored_slide_html

    rows = []
    counts = Counter()
    wanted = {"run-13", "run-14", "run-16", "run-17", "run-18", "run-19", "run-20", "run-23"}
    for path in sorted(fixtures.glob("*/*/manifest.json")):
        run = path.parent.parent.name
        if run not in wanted:
            continue
        unit = json.loads(path.read_text(encoding="utf-8-sig"))
        blocks = sorted(unit.get("blocks", ()), key=lambda block: block.get("order", 0))
        bindings = tuple(
            (block["sourceBlockId"], block["payload"]["text"])
            for block in blocks
            if block.get("kind") == "text" and block.get("payload", {}).get("text", "").strip()
        )
        outline = {
            "slides": [
                {
                    "sourceUnitId": unit["sourceUnitId"],
                    "sourceIndex": unit.get("sourceIndex", 0),
                    "readingOrder": [
                        block["sourceBlockId"] for block in blocks if block.get("kind") in {"text", "image"}
                    ],
                }
            ]
        }
        for html_path in sorted(path.parent.glob("html-*.txt")):
            html = html_path.read_text(encoding="utf-8-sig")
            stage = html_path.stem.removeprefix("html-")
            ctx = context_from_source(
                manifest={"units": [unit]},
                projection=outline,
                presentation_id="offline",
                operation_id="offline",
                slide_index=0,
                source_unit_id=unit["sourceUnitId"],
                policy_revision="authored-admission/v1",
                fenced_revision=0,
                attempt_token="offline",
                reference_mode="source_content_restyle",
                source_reflow=True,
                terminal_fallback_eligible=False,
                bindings=bindings,
                state="token" if stage == "author" else "expanded",
                allow_illustration_slots=stage == "author",
            )
            # This compares the current old static checks on the identical bytes;
            # a job's historical failure is not a per-candidate static verdict.
            existing, code = "admitted", ""
            try:
                checked = validate_authored_slide_html(html, allow_illustration_slots=stage == "author")
                stamp_source_blocks(checked, bindings)
            except ValueError as error:
                existing, code = "rejected", getattr(error, "code", "authored_slide_author_failed")
            admission = admit_slide_html(ctx, html)
            counts[(run, stage, existing, admission.verdict)] += 1
            rows.append(
                {
                    "run": run,
                    "slide": int(path.parent.name),
                    "producer": stage,
                    "existingVerdict": existing,
                    "existingCode": code,
                    "gateVerdict": admission.verdict,
                    "gateCode": admission.code,
                    "predicate": admission.predicate,
                    "blockIds": list(admission.block_ids),
                    "htmlDigest": admission.receipt.html_digest,
                }
            )
    return {
        "schemaVersion": 1,
        "comparison": "static same bytes; current legacy versus gate",
        "capturedHistoricalParity": "unavailable: no persisted metrics in supplied fixtures",
        "rows": rows,
        "parity": [
            {"run": key[0], "producer": key[1], "existing": key[2], "gate": key[3], "count": count}
            for key, count in sorted(counts.items())
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("fixtures", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = replay(args.fixtures)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print("| run | producer | existing | gate | count |")
    print("| --- | --- | --- | --- | ---: |")
    for row in result["parity"]:
        print("| {run} | {producer} | {existing} | {gate} | {count} |".format(**row))


if __name__ == "__main__":
    main()
