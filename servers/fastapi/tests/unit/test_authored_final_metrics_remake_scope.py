"""The final gate must judge the HTML being published, not a pre-remake capture.

Bench run 24: a 24-slide deck flagged slides 4, 5, 9 and 15, remade all four,
logged ``metrics_readback_from_checkpoint=24 source=ledger`` and then refused to
publish on ``authored_quality_receipt_gate_failed``. The readback trusted the
sealed ledger on LENGTH alone, so any slide whose ledger entry predated its
remake would have been gated on the bytes it no longer has.

These tests pin the identity rule instead: a readback is used only while it
carries the ``source_digest`` of the exact HTML about to be gated, whichever
source it came from, and everything else is re-collected.
"""

from __future__ import annotations

import asyncio
import hashlib

from services import authored_final_metrics as metrics_mod
from services.authored_admission_capture import canonical_capture_html
from services import authored_quality_gate_diagnostics as diagnostics_mod


DECK_SLIDES = 24
REMADE = (4, 5, 9, 15)


def _run(coro):
    return asyncio.run(coro)


def _digest(html: str) -> str:
    return "sha256:" + hashlib.sha256(html.encode("utf-8")).hexdigest()


def _html(index, *, remade=False):
    label = f"slide {index} remade" if remade else f"slide {index}"
    return canonical_capture_html(f"<div>{label}</div>", index=index if isinstance(index, int) else 0)


def _bench_run_24_deck() -> tuple[list[str], list[dict], dict[int, dict]]:
    """The published deck, its sealed ledger, and the remake round's metrics."""

    published = [
        _html(index, remade=index in REMADE)
        for index in range(DECK_SLIDES)
    ]
    # The ledger entry for a remade slide was measured from the slide as it was
    # BEFORE the remake round replaced it.
    ledger = [
        {
            "source_digest": _digest(
                _html(index) if index in REMADE else published[index]
            ),
            "elements": [],
            "from": "ledger",
        }
        for index in range(DECK_SLIDES)
    ]
    round_metrics = {
        index: {
            "source_digest": _digest(published[index]),
            "elements": [],
            "from": "remake",
        }
        for index in REMADE
    }
    return published, ledger, round_metrics


def test_a_remade_slide_is_never_gated_on_its_pre_remake_ledger_entry(monkeypatch):
    published, ledger, round_metrics = _bench_run_24_deck()
    gathered: list[str] = []

    async def fake_ensure(html, *args, **kwargs):
        gathered.append(html)
        return {"source_digest": _digest(html), "from": "rendered"}

    from utils import slide_capture

    monkeypatch.setattr(slide_capture, "ensure_browser_metrics", fake_ensure)

    resolved = _run(
        metrics_mod.resolve_final_browser_metrics(
            published, retained=ledger, overrides=round_metrics
        )
    )

    assert [metrics["from"] for metrics in resolved] == [
        "remake" if index in REMADE else "ledger" for index in range(DECK_SLIDES)
    ]
    # The remake round already measured those four slides, so nothing re-renders.
    assert gathered == []


def test_a_stale_ledger_entry_with_no_round_metrics_is_re_rendered(monkeypatch):
    """The remake's own metrics being lost must cost a render, never a lie."""

    published, ledger, _round_metrics = _bench_run_24_deck()
    gathered: list[str] = []

    async def fake_ensure(html, *args, **kwargs):
        gathered.append(html)
        return {"source_digest": _digest(html), "from": "rendered"}

    from utils import slide_capture

    monkeypatch.setattr(slide_capture, "ensure_browser_metrics", fake_ensure)

    resolved = _run(
        metrics_mod.resolve_final_browser_metrics(
            published, retained=ledger, overrides=None
        )
    )

    assert gathered == [published[index] for index in REMADE]
    for index in REMADE:
        assert resolved[index]["from"] == "rendered"
        assert resolved[index]["source_digest"] == _digest(published[index])


def test_metrics_of_the_wrong_html_are_rejected_from_either_source(monkeypatch):
    """A digest mismatch is no metrics at all, ledger or repair round alike."""

    async def fake_ensure(html, *args, **kwargs):
        return {"source_digest": _digest(html), "from": "rendered"}

    from utils import slide_capture

    monkeypatch.setattr(slide_capture, "ensure_browser_metrics", fake_ensure)

    resolved = _run(
        metrics_mod.resolve_final_browser_metrics(
            [_html("a")],
            retained=[{"source_digest": _digest(_html("old-a"))}],
            overrides={0: {"source_digest": _digest(_html("older-a"))}},
        )
    )

    assert resolved[0]["from"] == "rendered"


# ---------------------------------------------------------------------------
# The gate refusal has to say what it refused
# ---------------------------------------------------------------------------


class _BlockedReceipt:
    blockers = ("capacity.browser_observation_missing", "capacity.failed")
    statuses = {"safety": "passed", "capacity": "blocked", "publication": "pending"}


def test_the_blocked_receipt_names_its_checks_and_slides(caplog):
    capacity_checks = {
        0: {"browserObserved": True, "checks": {"text": True, "image": True}},
        3: {"browserObserved": False, "checks": {"text": True, "image": True}},
        7: {"browserObserved": True, "checks": {"text": True, "image": False}},
    }

    summary = diagnostics_mod.quality_receipt_gate_failure(
        _BlockedReceipt(),
        capacity_checks=capacity_checks,
        violations=["image:semantic_role_missing:q-img-1"],
    )

    assert summary["blockers"] == list(_BlockedReceipt.blockers)
    assert summary["blockedSlides"] == [3, 7]
    # A domain that passed is not a reason to read the line.
    assert "safety" not in summary["statuses"]

    with caplog.at_level("ERROR"):
        message = diagnostics_mod.log_quality_receipt_gate_failure(summary)

    assert "authored_quality_receipt_gate_failed" in caplog.text
    assert "capacity.browser_observation_missing" in caplog.text
    assert "[3, 7]" in caplog.text
    assert "capacity.browser_observation_missing" in message
    assert "[3, 7]" in message
