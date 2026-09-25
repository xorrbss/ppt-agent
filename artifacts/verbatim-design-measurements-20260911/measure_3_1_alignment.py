"""3.1, decisive half — does partial emphasis align with projected run boundaries?

Partial emphasis alone does not decide the token form. A styled span that
coincides with a contiguous span of projected runs is expressible with run
tokens; only a span that cuts *inside* a run needs
``{{q:<id>@<start>-<end>}}``. The design states the expectation ("projected runs
follow delimiters, not style boundaries") and leaves it to this measurement.

For every bound element that carries emphasis over part of its text, the block's
raw text is taken from the stored source manifest, projected with the pipeline's
own ``project_source_runs``, and the emphasised substring is tested against every
contiguous run span of that projection.

Read-only: stored manifests and published slide HTML. No generation, no provider
call, no database write.
"""

from __future__ import annotations

import json
import re
import sys
from collections import Counter
from pathlib import Path
from typing import Any

sys.path.insert(0, "C:/project/PPT-agent/ppt-agent/servers/fastapi")

from services.source_content.source_runs import (  # noqa: E402
    normalize_source_text,
    project_source_runs,
)

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from measure_3_1_emphasis import BoundElementScanner, parse_binding  # noqa: E402

DB = HERE / "dburl.txt"
BASE = Path("C:/project/PPT-agent/ppt-agent/artifacts/authored-full-product-validation-20260910")
RUNS = ("run-11", "run-15", "run-16", "run-20", "run-21")


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


def squash(value: str) -> str:
    return re.sub(r"\s+", "", value or "")


def block_texts(manifest_json: Any) -> dict[str, str]:
    """Every text block id in a manifest with its raw text.

    Shape: ``units[].blocks[]`` with ``sourceBlockId`` and ``payload.text``
    (``services/source_content/contracts.py``).
    """

    texts: dict[str, str] = {}
    for unit in manifest_json.get("units") or [] if isinstance(manifest_json, dict) else []:
        for block in (unit or {}).get("blocks") or []:
            if not isinstance(block, dict) or block.get("kind") != "text":
                continue
            block_id = block.get("sourceBlockId")
            raw = (block.get("payload") or {}).get("text")
            if isinstance(block_id, str) and isinstance(raw, str) and raw.strip():
                texts[block_id] = raw
    return texts


def run_spans(projection) -> set[str]:
    """Squashed text of every contiguous run span, plus body-only variants."""

    spans: set[str] = set()
    count = projection.run_count
    for start in range(count):
        for end in range(start, count):
            text = projection.span_text(start, end)
            spans.add(squash(text))
            for accepted in projection.accepted_texts(start, end):
                spans.add(squash(accepted))
    return spans


def main(out_path: str) -> int:
    import psycopg

    url = re.sub(r"^postgresql\+\w+://", "postgresql://", DB.read_text().strip())
    findings: list[dict[str, Any]] = []
    missing_manifest: Counter[str] = Counter()

    with psycopg.connect(url, connect_timeout=15, options="-c default_transaction_read_only=on") as c:
        c.execute("set statement_timeout='90s'")
        manifests = {
            str(snapshot): payload
            for snapshot, payload in c.execute(
                "select reference_snapshot_id, manifest_json from source_content_manifests"
            ).fetchall()
        }
        print(f"manifests available: {len(manifests)}", flush=True)
        texts_by_snapshot = {key: block_texts(value) for key, value in manifests.items()}
        pooled: dict[str, str] = {}
        for mapping in texts_by_snapshot.values():
            pooled.update(mapping)
        print(f"pooled text blocks: {len(pooled)}", flush=True)

        for run in RUNS:
            status = json.loads((BASE / run / "06-final-status.json").read_text(encoding="utf-8-sig"))
            pid = (walk(status, "presentation_id") or walk(status, "presentationId") or [None])[0]
            if not pid:
                continue
            slides = c.execute(
                "select index, html_content from slides where presentation = %s order by index",
                (pid,),
            ).fetchall()
            for index, html in slides:
                if not html:
                    continue
                scanner = BoundElementScanner()
                scanner.feed(html)
                scanner.close()
                for element in scanner.bound:
                    block, start, _end = parse_binding(element["binding"])
                    whole = squash(element["text"])
                    raw = pooled.get(block)
                    if raw is None:
                        missing_manifest[block] += 1
                        continue
                    try:
                        projection = project_source_runs(raw)
                    except ValueError:
                        missing_manifest[block] += 1
                        continue
                    spans = run_spans(projection)
                    for span in element["emphasis"]:
                        emphasised = squash(span["text"])
                        if not emphasised or emphasised == whole:
                            continue
                        findings.append(
                            {
                                "run": run,
                                "slide": int(index),
                                "block": block,
                                "run_binding": start,
                                "run_count": projection.run_count,
                                "why": span["why"],
                                "emphasised_chars": len(emphasised),
                                "block_chars": len(squash(normalize_source_text(raw))),
                                "aligns_with_run_span": emphasised in spans,
                                "sample": span["text"].strip()[:40],
                            }
                        )
            print(f"{run}: scanned", flush=True)

    Path(out_path).write_text(json.dumps(findings, indent=1, ensure_ascii=False), encoding="utf-8")

    aligned = [item for item in findings if item["aligns_with_run_span"]]
    inside = [item for item in findings if not item["aligns_with_run_span"]]
    print(f"\n=== 3.1 decisive test over {len(RUNS)} published decks ===")
    print(f"partial-emphasis spans examined: {len(findings)}")
    print(f"  aligned with a contiguous run span (run tokens suffice): {len(aligned)}")
    print(f"  cutting inside a run (needs a character range):          {len(inside)}")
    if inside:
        print("  reasons:", dict(Counter(r for item in inside for r in item["why"])))
        print("  blocks affected:", len({item['block'] for item in inside}))
        print("  single-run blocks among them:",
              sum(1 for item in inside if item["run_count"] == 1))
        for item in inside[:6]:
            print(
                f"    {item['run']} s{item['slide']} {item['block']}"
                f" runs={item['run_count']} {item['emphasised_chars']}/{item['block_chars']}자"
                f" {item['why']} · {item['sample']!r}"
            )
    if missing_manifest:
        print(f"\nblocks with no manifest text (excluded): {len(missing_manifest)}")
    verdict = (
        "character-range tokens ARE needed"
        if inside
        else "run tokens suffice; character-range tokens are NOT needed"
    )
    print(f"\nVERDICT: {verdict}")
    print("wrote", out_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1] if len(sys.argv) > 1 else "measure-3-1-alignment.json"))
