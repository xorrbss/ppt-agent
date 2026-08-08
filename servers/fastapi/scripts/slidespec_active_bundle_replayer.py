"""Verify the immutable active-bundle fixture used for scoped DOM evidence replay.

This adapter intentionally has no database writes.  It pins the exact HTML read
from the active artifact and exposes the qids/scenarios that the Chrome and
evidence-lattice regressions must replay.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_FIXTURE = ROOT / "tests" / "fixtures" / "slidespec_active_bundle_a6470d97.json"


def load_fixture(path: Path = DEFAULT_FIXTURE) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("schema") != "urn:ppt-agent:slidespec-active-bundle-fixture:1.0":
        raise ValueError("unsupported active-bundle fixture schema")
    return payload


def verify_fixture(payload: dict[str, Any], *, base_directory: Path) -> dict[str, Any]:
    """Return a stable, non-content replay contract or fail closed on drift."""

    html_file = payload.get("html_file")
    expected_hash = payload.get("html_sha256")
    if not isinstance(html_file, str) or not html_file:
        raise ValueError("fixture html_file is required")
    if not isinstance(expected_hash, str) or len(expected_hash) != 64:
        raise ValueError("fixture html_sha256 is required")
    html = (base_directory / html_file).read_bytes()
    actual_hash = hashlib.sha256(html).hexdigest()
    if actual_hash != expected_hash:
        raise ValueError("active-bundle fixture HTML digest changed")

    issue = payload.get("physical_issue")
    unknown = payload.get("unrelated_unknown")
    fresh_footer = payload.get("fresh_v4_footer_measurement")
    if not isinstance(issue, dict) or not isinstance(unknown, dict) or not isinstance(fresh_footer, dict):
        raise ValueError("fixture must define issue and unrelated unknown evidence")
    if unknown.get("state", "unknown") != "unknown" or fresh_footer.get("state") != "proven_clean":
        raise ValueError("historical unknown and fresh footer measurement must remain distinct")
    if unknown.get("qid") != fresh_footer.get("qid"):
        raise ValueError("fresh footer measurement must be scoped to the historical footer qid")
    qids = issue.get("raw_target_qids")
    if not isinstance(qids, list) or len(qids) != 3 or not all(isinstance(qid, str) for qid in qids):
        raise ValueError("fixture must retain all three raw nested target qids")
    required_qids = [*qids, unknown.get("qid")]
    decoded = html.decode("utf-8")
    missing = [qid for qid in required_qids if not isinstance(qid, str) or f'data-qid="{qid}"' not in decoded]
    if missing:
        raise ValueError("fixture HTML is missing required qids: " + ", ".join(str(item) for item in missing))

    scenarios = payload.get("scenarios")
    if not isinstance(scenarios, list) or len(scenarios) != 5:
        raise ValueError("fixture must pin all five scoped-evidence replay scenarios")
    return {
        "bundle_id": payload.get("bundle_id"),
        "presentation_id": payload.get("presentation_id"),
        "slide_id": payload.get("slide_id"),
        "slide_index": payload.get("slide_index"),
        "html_sha256": actual_hash,
        "raw_target_qids": qids,
        "unrelated_unknown_qid": unknown["qid"],
        "fresh_v4_footer_state": fresh_footer["state"],
        "expected_deduplicated_issue_count": issue.get("deduplicated_issue_count"),
        "scenarios": scenarios,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixture", type=Path, default=DEFAULT_FIXTURE)
    args = parser.parse_args()
    payload = load_fixture(args.fixture)
    print(json.dumps(verify_fixture(payload, base_directory=args.fixture.parent), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
