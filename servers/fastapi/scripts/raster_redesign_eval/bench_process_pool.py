"""Offline raster page-pool comparison; no provider or product stack required.

Runs the previous thread dispatch and the spawned-process dispatch against the
same source pages, then compares canonical analysis and crop bytes per page.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import threading
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from services.source_content import raster_page_pool
from services.source_content.raster_analysis import analyze_redesign_pptx
from services.source_content.raster_stage_timing import page_task


class VisionCache:
    """Read-only replay of the prior diagnostic harness's real model answers."""

    model = "gpt-5.6-sol"

    def __init__(self, directory: Path):
        self.directory = directory
        self.hits = 0
        self.misses = 0
        self.lock = threading.Lock()

    def __call__(self, system, parts, schema, name):
        digest = hashlib.sha256()
        values = (system, name, json.dumps(schema, sort_keys=True), *(
            (b"B" + part) if isinstance(part, bytes) else ("S" + part) for part in parts
        ))
        for part in values:
            digest.update(part if isinstance(part, bytes) else str(part).encode("utf-8"))
            digest.update(b"\x00")
        path = self.directory / f"{digest.hexdigest()}.json"
        with self.lock:
            if path.is_file():
                self.hits += 1
            else:
                self.misses += 1
                raise RuntimeError("llm_cache_miss")
        return json.loads(path.read_text(encoding="utf-8"))


def _threaded(pages, slide_size, tesseract, tessdata, structurer, max_workers):
    from services.source_content.raster_redesign import _page_result

    workers = max(1, min(len(pages), max_workers or min(12, os.cpu_count() or 2)))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        return list(pool.map(page_task(lambda page: _page_result(
            page, slide_size, tesseract_path=tesseract,
            tessdata_path=tessdata, structurer=structurer,
        )), pages))


def _canonical(draft):
    data = draft.analysis.model_dump(mode="json", exclude={"stage_timings"})
    pages = [json.dumps(page, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
             for page in data["pages"]]
    crops = {key: hashlib.sha256(value).hexdigest() for key, value in draft.crop_png_by_region.items()}
    return data, [hashlib.sha256(page).hexdigest() for page in pages], crops


def _metrics(data, truth_path):
    pages = data["pages"]
    editable = sum(len(region.get("text") or "") for page in pages for region in page["regions"]
                   if region["role"] in ("title", "body"))
    ocr = sum(len(word["text"]) for page in pages for word in page["ocr_inventory"])
    result = {"editable_ratio": round(editable / ocr, 5) if ocr else None}
    if truth_path is not None:
        import re

        truth = json.loads(truth_path.read_text(encoding="utf-8"))
        wrong = 0
        for page, source in zip(pages, truth["pages"], strict=True):
            text = " ".join(region.get("text") or "" for region in page["regions"]
                            if region["role"] in ("title", "body"))
            output = Counter(re.findall(r"\d[\d,.]*%?", text))
            wrong += sum((output - Counter(source["numeric_tokens"])).values())
        result["wrong_numbers_raw"] = wrong
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--decks", default="deck1,deck2,deck3,deck4")
    parser.add_argument("--llm-cache", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    config = json.loads(args.config.read_text(encoding="utf-8"))
    selected = set(args.decks.split(","))
    report = []
    original = raster_page_pool.analyze_pages
    try:
        for deck in config["decks"]:
            if deck["id"] not in selected:
                continue
            source = Path(deck["source_pptx"])
            if not source.is_absolute():
                source = args.config.parent / source
            truth = deck.get("ground_truth")
            truth_path = args.config.parent / truth if truth else None
            if not source.is_file():
                report.append({"deck": deck["id"], "error": "source_missing"})
                continue
            modes = []
            for mode, dispatcher in (("threads", _threaded), ("processes", original)):
                raster_page_pool.analyze_pages = dispatcher
                vision = VisionCache(args.llm_cache) if args.llm_cache else None
                started = time.perf_counter()
                draft = analyze_redesign_pptx(source, structurer=vision)
                elapsed = time.perf_counter() - started
                data, page_hashes, crops = _canonical(draft)
                modes.append({"mode": mode, "wall_seconds": round(elapsed, 3),
                              "digest": data["digest"], "pages": page_hashes, "crops": crops,
                              "cache_hits": vision.hits if vision else 0,
                              "cache_misses": vision.misses if vision else 0,
                              **_metrics(data, truth_path)})
            before, after = modes
            report.append({
                "deck": deck["id"], "pages": len(before["pages"]),
                "thread_seconds": before["wall_seconds"], "process_seconds": after["wall_seconds"],
                "page_equal": before["pages"] == after["pages"],
                "crop_equal": before["crops"] == after["crops"],
                "digest_equal": before["digest"] == after["digest"],
                "cache_hits": [before["cache_hits"], after["cache_hits"]],
                "cache_misses": [before["cache_misses"], after["cache_misses"]],
                "before": {key: before[key] for key in ("editable_ratio", "wrong_numbers_raw") if key in before},
                "after": {key: after[key] for key in ("editable_ratio", "wrong_numbers_raw") if key in after},
            })
            args.output.write_text(json.dumps(report, indent=2), encoding="utf-8")
    finally:
        raster_page_pool.analyze_pages = original
    print(json.dumps(report))


if __name__ == "__main__":
    sys.exit(main())
