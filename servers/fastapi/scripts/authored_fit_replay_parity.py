"""Verdict parity between the replay and the recorded quality-gate evidence.

Design section 10, risk 1: before any bucket is trusted, the offline gate must
agree with the gate that actually ran. The only per-slide evidence a durable
row carries is ``presentation.theme["authoredQualityGate"]``
(``build_authored_quality_gate_receipt``): its ``residualIssues`` list the
slides that still had a blocking safety defect on the exact published bytes,
and its ``htmlDigest`` binds the receipt to those bytes.

A failed job never reaches publication, so it has no such receipt; this module
says so instead of inventing a comparison.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping, Sequence
from typing import Any

from scripts.authored_fit_replay_inputs import PUBLISHED_FINAL, JobContext

_RECEIPT_KEY = "authoredQualityGate"


async def _theme_receipt(session: Any, presentation_id: uuid.UUID) -> Mapping[str, Any] | None:
    from sqlmodel import select

    from models.sql.presentation import PresentationModel

    row = (
        await session.execute(
            select(PresentationModel).where(PresentationModel.id == presentation_id)
        )
    ).scalars().first()
    theme = getattr(row, "theme", None)
    if not isinstance(theme, Mapping):
        return None
    receipt = theme.get(_RECEIPT_KEY)
    return receipt if isinstance(receipt, Mapping) else None


async def _published_html_digest(session: Any, presentation_id: uuid.UUID) -> str | None:
    from sqlmodel import select

    from models.sql.slide import SlideModel
    from services.presentation_export_attempt_service import sha256_canonical_json

    rows = (
        await session.execute(
            select(SlideModel)
            .where(SlideModel.presentation == presentation_id)
            .order_by(SlideModel.index)
        )
    ).scalars().all()
    htmls = [row.html_content for row in rows if row.html_content]
    if not htmls:
        return None
    return "sha256:" + sha256_canonical_json(htmls)


def _recorded_slides(receipt: Mapping[str, Any]) -> list[int]:
    residual = receipt.get("residualIssues")
    if not isinstance(residual, Sequence):
        return []
    return sorted(
        {
            int(item["slide_index"])
            for item in residual
            if isinstance(item, Mapping) and isinstance(item.get("slide_index"), int)
        }
    )


def _replayed_slides(rows: Sequence[Mapping[str, Any]], job_id: str) -> list[int]:
    return sorted(
        {
            int(row["slide_index"])
            for row in rows
            if row.get("job_id") == job_id
            and row.get("source") == PUBLISHED_FINAL
            and row.get("geometry_blocked")
        }
    )


def _has_published_rows(rows: Sequence[Mapping[str, Any]], job_id: str) -> bool:
    return any(
        row.get("job_id") == job_id and row.get("source") == PUBLISHED_FINAL
        for row in rows
    )


#: The terminal code a deck gets when a slide's blocking geometry defect
#: survived the bounded repair (``_evaluate_slide_quality``).
_GATE_FAILURE_CODE = "authored_composition_safety_gate_failed"


def _circumstantial_row(
    context: JobContext, rows: Sequence[Mapping[str, Any]]
) -> dict[str, Any]:
    """A deck-level cross-check for a job with no per-slide receipt.

    A failed job never publishes, so it stores no quality-gate receipt. What it
    does store is the terminal error code, and that code says whether the gate
    that ran found a blocking geometry defect somewhere in the deck. Comparing
    it with the slides the replay blocks is weaker than a per-slide comparison
    and is reported as such: it is never counted in the agreement tally.
    """

    job_id = str(context.job_id)
    blocked = sorted(
        {
            int(row["slide_index"])
            for row in rows
            if row.get("job_id") == job_id and row.get("geometry_blocked")
        }
    )
    recorded_gate_failure = context.last_error_code == _GATE_FAILURE_CODE
    if recorded_gate_failure:
        agreement = "consistent" if blocked else "inconsistent"
    elif not blocked:
        agreement = "consistent"
    else:
        # The deck died of something else (a deadline, a transport error), so
        # these slides never received a gate verdict at all. A blocking defect
        # in their stored pre-render HTML contradicts nothing.
        agreement = "not comparable (deck failed before a gate verdict)"
    return {
        "job_short": context.short,
        "evidence": f"circumstantial: job.last_error={context.last_error_code}",
        "recorded": _GATE_FAILURE_CODE if recorded_gate_failure else "no gate failure",
        "replayed": blocked or "-",
        "agreement": agreement,
    }


async def check_parity(
    session: Any,
    contexts: Sequence[JobContext],
    rows: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Compare replay verdicts against the recorded gate receipt, per job."""

    result_rows: list[dict[str, Any]] = []
    unreachable: list[str] = []
    agree = 0
    compared = 0
    for context in contexts:
        job_id = str(context.job_id)
        receipt = await _theme_receipt(session, context.presentation_id)
        if receipt is None:
            unreachable.append(
                f"{context.short}: no authoredQualityGate receipt on the "
                f"presentation theme (job state {context.state})"
            )
            result_rows.append(_circumstantial_row(context, rows))
            continue
        if not _has_published_rows(rows, job_id):
            unreachable.append(
                f"{context.short}: a receipt exists but no published slide HTML "
                "was replayed (pass --include-published)"
            )
            continue
        recorded = _recorded_slides(receipt)
        replayed = _replayed_slides(rows, job_id)
        digest = await _published_html_digest(session, context.presentation_id)
        bound = digest == receipt.get("htmlDigest")
        compared += 1
        if recorded == replayed:
            agree += 1
        result_rows.append(
            {
                "job_short": context.short,
                "evidence": (
                    "receipt bound to the replayed bytes"
                    if bound
                    else "receipt htmlDigest does NOT match the stored slide HTML"
                ),
                "recorded": recorded or "-",
                "replayed": replayed or "-",
                "agreement": "match" if recorded == replayed else "differs",
            }
        )
    if compared:
        narrative = (
            f"Compared {compared} deck(s) with a recorded quality-gate receipt; "
            f"{agree} agreed with the replay on which slides carry a blocking "
            "geometry defect."
        )
    else:
        narrative = (
            "No recorded per-slide gate evidence was reachable from the durable "
            "rows, so verdict parity could not be checked directly; the table "
            "below carries the deck-level circumstantial cross-check instead."
        )
    if unreachable:
        narrative += " Not reachable: " + "; ".join(unreachable) + "."
    return {
        "compared": compared,
        "agreed": agree,
        "unreachable": unreachable,
        "rows": result_rows,
        "narrative": narrative,
    }


__all__ = ["check_parity"]
