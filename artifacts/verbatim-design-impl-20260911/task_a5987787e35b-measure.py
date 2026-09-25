"""Offline T5 table budget and legacy-adapter classification evidence."""

import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "servers/fastapi"))

def main():
    from services.source_content.placeholder.block_table import encode_block_table, table_from_manifest
    from tests.unit.placeholder_fixture_adapter import legacy_incompatibilities

    fixtures = ROOT / "artifacts/verbatim-design-measurements-20260911/fixtures"
    output = Path(__file__).parent
    manifest = json.loads((fixtures / "manifest-43p/manifest.json").read_text(encoding="utf-8"))
    table = table_from_manifest(manifest)
    by_slide = [len(encode_block_table(table_from_manifest(unit))) for unit in manifest["units"]]
    records = []
    for run in ("run-16", "run-20", "run-21"):
        for index in range(35):
            directory = fixtures / run / f"{index:02d}"
            html = (directory / "html-render.txt").read_text(encoding="utf-8")
            slide_table = table_from_manifest(json.loads((directory / "manifest.json").read_text(encoding="utf-8")))
            deviations = legacy_incompatibilities(html, slide_table)
            records.append({"run": run, "slideIndex": index, "deviations": deviations,
                            "htmlSha256": hashlib.sha256(html.encode()).hexdigest()})
    incompatible = [record for record in records if record["deviations"]]
    summary = {
        "manifestFingerprint": "d3839b142c32", "units": len(manifest["units"]), "blocks": len(table),
        "runs": sum(block.projection.run_count for block in table),
        "tableCharacters": len(encode_block_table(table)), "perSlideCharacters": by_slide,
        "perSlideMax": max(by_slide), "perSlideMin": min(by_slide),
        "structuredContextLimit": 64000, "split": "one source unit per author prompt",
        "publishedSlides": len(records), "exactOrderedSlides": len(records) - len(incompatible),
        "legacyIncompatibleSlides": len(incompatible),
        "liveVerification": "not run; no capture metrics in historical fixtures",
    }
    (output / "fixtures").mkdir(exist_ok=True)
    (output / "fixtures/INCOMPATIBLE.json").write_text(
        json.dumps(incompatible, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (output / "task_a5987787e35b-measurements.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False))


if __name__ == "__main__":
    main()
