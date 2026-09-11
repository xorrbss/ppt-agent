"""Attempt-scoped producer integration; writers validate, never mint receipts."""

from __future__ import annotations

from services.authored_admission_policy import INTERNAL_ERROR, RENDERER_POLICY_FAILED, VERBATIM_FLAG_ONLY

from contextvars import ContextVar
from contextlib import contextmanager
from dataclasses import dataclass, field, replace
from functools import wraps
import json
from typing import Any, Callable, Mapping

from services.authored_admission import Admission, admit_slide_html, reused_admission
from services.authored_admission_context import (
    AdmissionContext,
    canonical_json,
    context_from_source,
    digest_bytes,
    digest_html,
)
from services.authored_admission_policy import POLICY_REVISION, admission_gate_mode
from services.authored_admission_shadow import AdmissionShadow, bounded_code
from services.authored_terminal_slide_policy import terminal_fallback_active


@dataclass
class AdmissionSession:
    mode: str
    contexts: tuple[AdmissionContext, ...] = ()
    shadow: AdmissionShadow = field(default_factory=AdmissionShadow)
    admissions: dict[tuple[str, str, str], Admission] = field(default_factory=dict)
    captures: dict = field(default_factory=dict)
    capture_receipts: dict = field(default_factory=dict)
    capture_reuses: int = 0
    expanded_outputs: set[tuple[int, str]] = field(default_factory=set)

    def context(
        self, index: int, *, captured: bool = False, flags: tuple[str, ...] = (), slots: bool = False
    ) -> AdmissionContext:
        return replace(
            self.contexts[index],
            state="captured" if captured else "expanded",
            already_admitted_flags=flags,
            allow_illustration_slots=slots,
        )

    def admit(self, context: AdmissionContext, html: str, *, metrics=None, png=None) -> Admission:
        captured = self.capture_receipts.get((context.slide_index, digest_html(html)))
        same_capture = (
            context.state == "captured"
            and captured is not None
            and captured.metrics_digest == digest_html(canonical_json(metrics))
            and (png is None or captured.png_digest == digest_bytes(png))
        )
        if same_capture:
            context = replace(context, capture_profile=captured.context.capture_profile)
        key = (context.identity_digest, digest_html(html), digest_html(canonical_json(metrics)))
        previous = self.admissions.get(key)
        if previous is not None and context.state == "captured":
            if png is None or previous.receipt.png_digest == digest_bytes(png):
                return reused_admission(previous)
        result = admit_slide_html(context, html, metrics=metrics, png=png)
        if same_capture:
            result = reused_admission(result)
        self.admissions[key] = result
        return result


_SESSION: ContextVar[AdmissionSession | None] = ContextVar("authored_admission_session", default=None)
_PRODUCER: ContextVar[str | None] = ContextVar("authored_admission_producer", default=None)


@contextmanager
def producer_scope(producer: str):
    token = _PRODUCER.set(producer)
    try:
        yield
    finally:
        _PRODUCER.reset(token)


def admission_scope(function):
    @wraps(function)
    async def run(*args, **kwargs):
        token = _SESSION.set(AdmissionSession(mode=admission_gate_mode()))
        try:
            return await function(*args, **kwargs)
        finally:
            session = current_session()
            pending = [task for task in session.captures.values() if not task.done()]
            if pending:
                import asyncio

                for task in pending:
                    task.cancel()
                await asyncio.gather(*pending, return_exceptions=True)
            _SESSION.reset(token)

    return run


def current_session() -> AdmissionSession | None:
    return _SESSION.get()


def configure_admission(
    *,
    request: Any,
    presentation_id: Any,
    generation_job: Any,
    profile: Any,
    reference_mode: str,
    slide_count: int,
    bindings: Any,
    image_bindings: Any,
) -> None:
    session = current_session()
    if session is None:
        return
    source_reflow = bool(getattr(request, "_source_content_authored_reflow", False))
    manifest = getattr(request, "_source_content_manifest", None)
    projection = getattr(request, "_source_document_outline", None)
    projection_value = json.loads(canonical_json(projection))
    slides = projection_value.get("slides", ()) if isinstance(projection_value, dict) else ()
    contexts = []
    for index in range(slide_count):
        slide = slides[index] if index < len(slides) else {}
        contexts.append(
            context_from_source(
                manifest=manifest,
                projection=projection,
                presentation_id=str(presentation_id),
                operation_id=str(getattr(generation_job, "workspace_operation_id", None) or ""),
                slide_index=index,
                source_unit_id=str(slide.get("sourceUnitId") or slide.get("source_unit_id") or ""),
                policy_revision=POLICY_REVISION,
                fenced_revision=int(getattr(generation_job, "_workspace_fencing_token", None) or 0),
                attempt_token=str(getattr(generation_job, "attempt_token", None) or ""),
                reference_mode=reference_mode,
                source_reflow=source_reflow,
                terminal_fallback_eligible=terminal_fallback_active(
                    profile=profile,
                    reference_mode=reference_mode,
                    source_reflow=source_reflow,
                ),
                bindings=tuple(bindings[index]) if bindings is not None else (),
                image_bindings=tuple(image_bindings[index]) if image_bindings is not None else (),
            )
        )
    session.contexts = tuple(contexts)


def check_producer(
    html: str,
    *,
    index: int,
    producer: str,
    legacy: Callable[[str], str],
    metrics: Mapping[str, object] | None = None,
    png: bytes | None = None,
    flags: tuple[str, ...] = (),
    slots: bool = False,
    evidence: bool = False,
    representation: str | None = None,
    finalized: bool = False,
) -> tuple[str, Admission | None]:
    """Shadow calls both on the same input; enforce calls only the common gate."""
    session = current_session()
    if session is None or not session.contexts:
        return legacy(html), None
    producer = _PRODUCER.get() or producer
    context = session.context(index, captured=metrics is not None, flags=flags, slots=slots)
    context = replace(context, finalized=finalized)
    if representation is not None:
        context = context.for_state(representation)
    elif metrics is None and producer in {"author", "author_batch", "author_rescue"}:
        context = context.for_state("token")
    try:
        result = session.admit(context, html, metrics=metrics, png=png)
    except Exception as error:
        # A shadow instrumentation bug must not change existing producer behavior.
        if session.mode == "enforce":
            raise
        result = None
        gate_code = bounded_code(getattr(error, "code", INTERNAL_ERROR))
    else:
        gate_code = result.code
    if session.mode == "enforce":
        if not evidence:
            result.require()
        elif context.state == "captured":
            result = replace(result, receipt=replace(result.receipt, persistence_class="evidence"))
        return result.html, result
    try:
        existing = legacy(html)
    except Exception as error:
        session.shadow.record(
            producer=producer,
            existing_verdict="rejected",
            existing_code=bounded_code(getattr(error, "code", RENDERER_POLICY_FAILED)),
            gate_verdict=result.verdict if result else "error",
            gate_code=gate_code,
        )
        raise
    legacy_flagged = isinstance(existing, dict) and existing.get("outcome") == VERBATIM_FLAG_ONLY
    session.shadow.record(
        producer=producer,
        existing_verdict="flag_only" if flags or legacy_flagged else "admitted",
        existing_code="",
        gate_verdict=result.verdict if result else "error",
        gate_code=gate_code,
    )
    return existing, result


def admission_shadow_receipt() -> dict | None:
    session = current_session()
    return session.shadow.as_dict() if session is not None else None
