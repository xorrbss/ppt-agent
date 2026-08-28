from __future__ import annotations

import asyncio
import json

import pytest

from services.authored_evidence_ledger import (
    DeckEvidenceLedger,
    EvidenceLedgerSealedError,
    EvidenceReuseError,
    capture_slide_evidence,
    digest_browser_metrics,
)


def _record(ledger: DeckEvidenceLedger, index: int, *, attempt: str = "attempt-1"):
    return ledger.record_slide(
        attempt_id=attempt,
        slide_index=index,
        html=f"<html><body><div data-qid='q{index}'>Slide {index}</div></body></html>",
        png=b"\x89PNG\r\n\x1a\n" + bytes([index % 251]),
        browser_metrics={"elements": [{"qid": f"q{index}", "bbox": {"x": 0, "y": 0}}]},
    )


def test_attempt_retains_all_evidence_beyond_renderer_lru_size():
    ledger = DeckEvidenceLedger(attempt_id="attempt-1")
    for index in range(40):
        _record(ledger, index)

    assert len(ledger.slides("attempt-1")) == 40
    assert ledger.latest("attempt-1", 0) is not None
    assert ledger.latest("attempt-1", 39) is not None
    projection = ledger.as_dict("attempt-1")
    assert projection["deckHtmlDigest"].startswith("sha256:")
    assert projection["ledgerDigest"].startswith("sha256:")


def test_unchanged_reuse_has_no_browser_or_render_capture_and_checks_digest():
    ledger = DeckEvidenceLedger(attempt_id="attempt-1")
    original = _record(ledger, 0)

    reused = ledger.reuse_unchanged_slide(
        attempt_id="attempt-1", slide_index=0, html_digest=original.html_digest
    )
    assert reused.reused_unchanged is True
    assert reused.capture_policy == "reused_unchanged"
    assert reused.browser_capture_count == reused.render_capture_count == 0
    assert reused.png == original.png
    with pytest.raises(EvidenceReuseError):
        ledger.reuse_unchanged_slide(attempt_id="attempt-1", slide_index=0, html="changed")

    retried = ledger.reuse_unchanged_slide(
        attempt_id="attempt-2",
        source_attempt_id="attempt-1",
        slide_index=0,
        html_digest=original.html_digest,
    )
    assert retried.attempt_id == "attempt-2"
    assert retried.reused_unchanged is True


def test_metrics_digest_is_mapping_order_independent_and_attempt_revision_is_bound():
    assert digest_browser_metrics({"a": 1, "b": [2, 3]}) == digest_browser_metrics(
        {"b": [2, 3], "a": 1}
    )
    ledger = DeckEvidenceLedger(attempt_id="attempt-1")
    first = _record(ledger, 0)
    second = ledger.record_slide(
        attempt_id="attempt-1",
        slide_index=0,
        html=first.html,
        png=first.png,
        browser_metrics=first.browser_metrics,
        checkpoint_revision=7,
    )
    assert second.revision > first.revision
    assert second.checkpoint_revision == 7
    assert second.evidence_digest != first.evidence_digest


def test_capture_adapter_retains_png_and_metrics_as_one_record(monkeypatch):
    async def fake_capture(html: str, timeout: int = 60):
        return b"png", {"source_digest": "sha256:source", "elements": []}

    monkeypatch.setattr(
        "utils.slide_capture.render_html_to_png_with_metrics", fake_capture
    )
    ledger = DeckEvidenceLedger()
    evidence = asyncio.run(
        capture_slide_evidence(
            ledger, attempt_id="attempt-2", slide_index=3, html="<p>ok</p>"
        )
    )
    assert evidence.png == b"png"
    assert evidence.browser_metrics is not None
    assert ledger.latest("attempt-2", 3) == evidence


def test_sealed_attempt_rejects_mutation():
    ledger = DeckEvidenceLedger(attempt_id="attempt-1")
    _record(ledger, 0)
    ledger.seal_attempt("attempt-1")
    with pytest.raises(EvidenceLedgerSealedError):
        _record(ledger, 1)


def test_hydrate_checkpoint_has_zero_capture_counts():
    ledger = DeckEvidenceLedger(attempt_id="attempt-1")
    hydrated = ledger.hydrate_slide(
        attempt_id="attempt-1",
        slide_index=2,
        html="<p>restored</p>",
        png=b"png",
        browser_metrics={"elements": []},
        checkpoint_revision=4,
    )
    assert hydrated.capture_policy == "hydrated"
    assert hydrated.browser_capture_count == hydrated.render_capture_count == 0
    assert hydrated.checkpoint_revision == 4


def test_retention_bytes_count_unique_payloads_across_unchanged_revisions():
    ledger = DeckEvidenceLedger(attempt_id="attempt-1", retention_byte_budget=10**9)
    original = _record(ledger, 0)
    ledger.reuse_unchanged_slide(
        attempt_id="attempt-1", slide_index=0, html_digest=original.html_digest
    )
    stats = ledger.retention_stats("attempt-1")["attempt-1"]
    expected = len(original.html.encode("utf-8")) + len(original.png)
    expected += len(json.dumps(
        {"elements": [{"bbox": {"x": 0, "y": 0}, "qid": "q0"}]},
        sort_keys=True, separators=(",", ":"), ensure_ascii=False,
    ).encode("utf-8"))
    assert stats["bytes"] == expected
