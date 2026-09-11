"""Receipt plumbing at the producer boundary and checks at checkpoint writers."""

from __future__ import annotations

from dataclasses import replace
from typing import Any

from services.authored_admission import admit_slide_html
from services.authored_admission_context import context_from_source
from services.authored_admission_policy import POLICY_REVISION, admission_gate_mode
from services.authored_admission_receipt import AdmissionReceipt, require_admission_receipt
from services.authored_admission_session import check_producer, current_session


def enforce_receipts() -> bool:
    session = current_session()
    return (session.mode if session else admission_gate_mode()) == "enforce"


def _admission_without_session(kwargs: dict[str, Any]) -> dict[str, Any]:
    """Admit a producer that has no attempt session, on the same predicate.

    The authored-vNext legacy bridge and the workspace page-remake writers reach
    ``complete_stage`` outside ``admission_scope``.  They are producers like any
    other, so the writer admits their HTML here rather than exempting them: the
    context pins the checkpoint's own attempt identity and declares no source
    reflow, which is exactly the gate's ordinary-authored column.
    """

    stage = kwargs["stage"]
    context = context_from_source(
        manifest=None,
        projection=None,
        presentation_id="",
        operation_id="",
        slide_index=int(kwargs["slide_index"]),
        source_unit_id="",
        policy_revision=POLICY_REVISION,
        fenced_revision=int(kwargs.get("fencing_token") or 0),
        attempt_token=str(kwargs.get("attempt_token") or ""),
        reference_mode="",
        source_reflow=False,
        terminal_fallback_eligible=False,
        state="captured" if stage == "render" else "expanded",
        allow_illustration_slots=stage == "author",
    )
    admission = admit_slide_html(
        context,
        kwargs["html"],
        metrics=kwargs.get("browser_metrics"),
        png=kwargs.get("png"),
    )
    receipt = admission.receipt
    if stage == "render":
        receipt = replace(receipt, persistence_class="evidence")
    return dict(kwargs, html=admission.html, admission_receipt=receipt)


def prepare_checkpoint_write(kwargs: dict[str, Any]) -> dict[str, Any]:
    """The producer boundary: HTML reaching a checkpoint carries a receipt."""
    if kwargs.get("html") is None or kwargs.get("admission_receipt") is not None:
        return kwargs
    session = current_session()
    if session is None or not session.contexts:
        # No session means no per-slide context to check against, not a licence
        # to write unadmitted HTML; enforce admits the bytes here instead.
        return _admission_without_session(kwargs) if enforce_receipts() else kwargs
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


def admit_checkpoint_write(
    *,
    admission_receipt: AdmissionReceipt | None,
    html: str | None,
    stage: str,
    slide_index: int,
    attempt_token: str,
    fencing_token: int | None,
    browser_metrics=None,
    png=None,
) -> tuple[str | None, AdmissionReceipt | None]:
    """Obtain-then-validate: the one place a checkpoint write gets its receipt.

    Every writer calls this before it mutates anything.  A producer that already
    holds a receipt hands it over; one that does not is routed through the same
    producer adapter, session or no session, so ``enforce`` can never turn a
    missing session into a missing receipt for a live subsystem.  The admitted
    (possibly canonicalised) HTML is returned and is what the writer persists.
    """

    if html is None or not enforce_receipts():
        return html, admission_receipt
    if admission_receipt is None:
        prepared = prepare_checkpoint_write(
            dict(
                html=html,
                stage=stage,
                slide_index=slide_index,
                attempt_token=attempt_token,
                fencing_token=fencing_token,
                browser_metrics=browser_metrics,
                png=png,
            )
        )
        html = prepared["html"]
        admission_receipt = prepared.get("admission_receipt")
    _validate_checkpoint_write(
        admission_receipt=admission_receipt,
        html=html,
        stage=stage,
        slide_index=slide_index,
        attempt_token=attempt_token,
        fencing_token=fencing_token,
        browser_metrics=browser_metrics,
        png=png,
    )
    return html, admission_receipt


def _validate_checkpoint_write(
    *,
    admission_receipt: AdmissionReceipt | None,
    html: str,
    stage: str,
    slide_index: int,
    attempt_token: str,
    fencing_token: int | None,
    browser_metrics=None,
    png=None,
) -> None:
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
