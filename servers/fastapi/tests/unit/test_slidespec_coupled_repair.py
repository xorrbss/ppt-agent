"""Focused replay checks for coupled maximum-quality candidate preflight."""

from __future__ import annotations

import json
from pathlib import Path


_FIXTURE = (
    Path(__file__).parents[1]
    / "fixtures"
    / "maximum_quality_coupled_repair_cases.json"
)
_HASH = "sha256:" + "a" * 64
_CONTENT_HASH = "sha256:" + "c" * 64


def _cases() -> list[dict]:
    return json.loads(_FIXTURE.read_text(encoding="utf-8"))["cases"]


def _context(candidate: dict) -> dict:
    return {
        "slideId": f"slide-{candidate['qid'].split('-')[0]}",
        "baseSpecRevision": 2,
        "baseSpecHash": _HASH,
        "expectedContentDigest": _CONTENT_HASH,
        "coupledRepairs": [candidate],
        "limits": {"maxOperations": 16, "maxAffectedBlocks": 8},
    }


def test_fixture_is_read_only_sanitized_replay_not_a_publication_recipe() -> None:
    payload = json.loads(_FIXTURE.read_text(encoding="utf-8"))

    assert payload["revision"] == 2
    assert {item["slide_index"] for item in payload["cases"]} == {11, 14, 24, 45, 73, 77}
    assert {
        (item["slide_index"], item["page_number"])
        for item in payload["cases"]
        if "page_number" in item
    } == {(14, 15), (24, 25), (45, 46)}
    encoded = json.dumps(payload, ensure_ascii=False).lower()
    assert "html" not in encoded
    assert "ocr_text" not in encoded
    assert "publish" not in encoded
