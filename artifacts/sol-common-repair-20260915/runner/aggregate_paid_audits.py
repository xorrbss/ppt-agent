"""Join independent, hash-bound verdicts without treating missing audits as passes."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ARMS = ("baseline", "dense-layout-supplement-v1")
SAMPLES = ("sol-p02", "sol-p28", "astra-p17", "astra-p25", "sol-p06", "astra-p19")


def read(path: Path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> None:
    mapping = read(ROOT / "private/neutral-packet-map.json")["items"]
    indexed = {}
    for item_id, meta in mapping.items():
        if meta["arm"] not in ARMS:
            continue
        key = (meta["arm"], meta["sampleId"], meta["branch"])
        if key in indexed:
            raise ValueError(f"Duplicate authoritative result for {key}")
        indexed[key] = item_id
    rows = []
    for arm in ARMS:
        for sample in SAMPLES:
            for branch in ("off", "on"):
                row = {"arm": arm, "sampleId": sample, "branch": branch,
                       "status": "pending", "contentPass": None, "visualPass": None,
                       "geometryPass": None, "jointPass": None}
                item_id = indexed.get((arm, sample, branch))
                row["itemId"] = item_id
                if item_id:
                    folder = ROOT / "neutral-packets" / item_id
                    packet = read(folder / "packet.json")
                    for path_key, hash_key in (("htmlPath", "htmlSha256"), ("pngPath", "pngSha256")):
                        expected = packet[hash_key].removeprefix("sha256:")
                        if digest(folder / packet[path_key]) != expected:
                            raise ValueError(f"Packet hash changed: {item_id}/{path_key}")
                    cp, vp = folder / "content-audit.json", folder / "visual-review.json"
                    if cp.exists():
                        content = read(cp)
                        if content.get("contentVerdict") not in ("pass", "fail"):
                            raise ValueError(f"Unknown content verdict: {item_id}")
                        row["contentPass"] = content["contentVerdict"] == "pass"
                    if vp.exists():
                        visual = read(vp)
                        if visual.get("verdict") not in ("pass", "visual_pass", "fail", "partial"):
                            raise ValueError(f"Unknown visual verdict: {item_id}")
                        row["visualPass"] = visual["verdict"] in ("pass", "visual_pass")
                        row["visualVerdictRaw"] = visual["verdict"]
                        row["geometryPass"] = visual["geometry"]["pass"]
                        row["readabilityScore"] = visual.get("readability_score", visual.get("readability", {}).get("score"))
                        row["styleScore"] = visual.get("style_score", visual.get("style", {}).get("score"))
                    if row["contentPass"] is not None and row["visualPass"] is not None:
                        row["status"] = "reviewed"
                        row["jointPass"] = row["contentPass"] and row["visualPass"]
                rows.append(row)
    cells = []
    for arm in ARMS:
        for branch in ("off", "on"):
            subset = [r for r in rows if r["arm"] == arm and r["branch"] == branch]
            cells.append({"arm": arm, "branch": branch, "planned": 6,
                          "reviewed": sum(r["status"] == "reviewed" for r in subset),
                          "contentPass": sum(r["contentPass"] is True for r in subset),
                          "geometryPass": sum(r["geometryPass"] is True for r in subset),
                          "visualPass": sum(r["visualPass"] is True for r in subset),
                          "jointPass": sum(r["jointPass"] is True for r in subset)})
    result = {"schemaVersion": "sol-common-repair-independent-join/v1", "planned": 24,
              "reviewed": sum(r["status"] == "reviewed" for r in rows),
              "missingAuditIsNotPass": True, "dryRunExcluded": True,
              "cells": cells, "rows": rows}
    output = ROOT / "evaluation/paid-trial-independent-join.json"
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
