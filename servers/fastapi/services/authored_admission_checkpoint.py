"""Receipt plumbing at the producer boundary and checks at checkpoint writers."""

from __future__ import annotations

from dataclasses import replace
from typing import Any

from services.authored_admission_policy import admission_gate_mode
from services.authored_admission_receipt import AdmissionReceipt, require_admission_receipt
from services.authored_admission_session import check_producer, current_session


def enforce_receipts() -> bool:
    session = current_session()
    return (session.mode if session else admission_gate_mode()) == "enforce"


def prepare_checkpoint_write(kwargs: dict[str, Any]) -> dict[str, Any]:
    """The external-success producer explicitly obtains a receipt before writing."""
    if kwargs.get("html") is None or kwargs.get("admission_receipt") is not None:
        return kwargs
    session = current_session()
    if session is None or not session.contexts:
        return kwargs
    stage = kwargs["stage"]
    html, admission = check_producer(
        kwargs["html"],
        index=kwargs["slide_index"],
        producer={"illustrate": "illustration", "render": "render"}.get(stage, "author"),
        legacy=lambda value: value,
        metrics=kwargs.get("browser_metrics"),
        png=kwargs.get("png"),
        slots=stage == "author",
        evidence=stage == "render",
        representation="captured" if stage == "render" else "expanded",
    )
    result = dict(kwargs, html=html)
    if admission is not None:
        receipt = admission.receipt
        if stage == "render":
            receipt = replace(receipt, persistence_class="evidence")
        result["admission_receipt"] = receipt
    return result


def validate_checkpoint_write(
    *,
    admission_receipt: AdmissionReceipt | None,
    html: str | None,
    stage: str,
    slide_index: int,
    attempt_token: str,
    fencing_token: int | None,
    browser_metrics=None,
    png=None,
) -> None:
    if html is None or not enforce_receipts():
        return
    session = current_session()
    expected = None
    if admission_receipt is not None and session is not None and session.contexts:
        pinned = admission_receipt.context
        expected = replace(
            session.contexts[slide_index],
            state=pinned.state,
            already_admitted_flags=pinned.already_admitted_flags,
            capture_profile=pinned.capture_profile,
            allow_illustration_slots=pinned.allow_illustration_slots,
            finalized=pinned.finalized,
        )
    require_admission_receipt(
        admission_receipt,
        html=html,
        stage=stage,
        slide_index=slide_index,
        attempt_token=attempt_token,
        fenced_revision=int(fencing_token or 0),
        metrics=browser_metrics,
        png=png,
        context=expected,
    )


def checkpoint_receipt_metadata(
    previous: dict | None, retained: dict | None, receipt: AdmissionReceipt | None
) -> dict | None:
    payload = receipt.as_dict() if receipt is not None else (previous or {}).get("admission")
    if payload is None:
        return retained
    return dict(retained or {}, admission=payload)
