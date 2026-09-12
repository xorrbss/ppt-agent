"""Attempt-scoped producer integration; writers validate, never mint receipts."""

from __future__ import annotations

from services.authored_admission_policy import INTERNAL_ERROR, RENDERER_POLICY_FAILED, VERBATIM_FLAG_ONLY

from contextvars import ContextVar
from contextlib import contextmanager
from dataclasses import dataclass, field, replace
from functools import wraps
import json
from typing import Any, Callable, Mapping, Sequence

from services.authored_admission import Admission, admit_slide_html, reused_admission
from services.authored_admission_context import (
    AdmissionContext,
    canonical_json,
    context_from_source,
    digest_bytes,
    digest_html,
)
from services.authored_admission_shadow import AdmissionShadow, bounded_code
from services.authored_rollout_profile import (
    ROLLOUT_PROFILE_PAYLOAD_KEY,
    RolloutProfile,
    pin_rollout_profile,
    require_rollout_profile,
    resolve_rollout_profile,
    rollout_profile_scope,
)
from services.authored_terminal_slide_policy import terminal_fallback_active


@dataclass
class AdmissionSession:
    mode: str
    #: The five rollout switches, resolved once when this attempt started.  The
    #: session never re-reads them; ``mode`` above is this profile's gate mode.
    profile: RolloutProfile = field(default_factory=RolloutProfile)
    #: The profile restored from the job record, once ``configure_admission``
    #: has seen one.  A second, disagreeing pin is a coded rejection.
    pinned_profile: RolloutProfile | None = None
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
            html_repr="captured" if captured else "expanded",
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
        result = admit_slide_html(
            context,
            html,
            metrics=metrics,
            png=png,
            normalization_mode=self.profile.html_normalization,
        )
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
    """An operation attempt: the rollout switches are resolved here, once."""

    @wraps(function)
    async def run(*args, **kwargs):
        profile = resolve_rollout_profile()
        with rollout_profile_scope(profile):
            token = _SESSION.set(AdmissionSession(mode=profile.gate_mode, profile=profile))
            try:
                return await function(*args, **kwargs)
            finally:
                session = current_session()
                pending = [task for task in session.captures.values() if not task.done()]
                ledger = getattr(session, "authored_capture_ledger", None)
                draining = list(ledger.draining) if ledger is not None else []
                if pending or draining:
                    import asyncio

                    for task in pending:
                        task.cancel()
                    await asyncio.gather(*pending, *draining, return_exceptions=True)
                _SESSION.reset(token)

    return run


def current_session() -> AdmissionSession | None:
    return _SESSION.get()


def persisted_rollout_profile(request: Any, generation_job: Any) -> Any:
    """The rollout profile this job was staged with, if the record carries one."""
    persisted = getattr(request, "_authored_rollout_profile", None)
    if persisted is None:
        payload = getattr(generation_job, "request_payload", None)
        if isinstance(payload, Mapping):
            persisted = payload.get(ROLLOUT_PROFILE_PAYLOAD_KEY)
    return persisted


def _pin_attempt_profile(session: AdmissionSession, *, request: Any, generation_job: Any) -> RolloutProfile:
    """Pin the attempt to its job record's profile, resolving live only once.

    A resumed attempt runs the switches its earlier attempts ran: the record is
    authority over the live environment, because the deck's already-published
    slides were produced under it.
    """

    profile = require_rollout_profile(persisted_rollout_profile(request, generation_job))
    pin_rollout_profile(profile, replacing=session.pinned_profile)
    session.pinned_profile = profile
    session.profile = profile
    session.mode = profile.gate_mode
    return profile


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
    # Two different profiles meet here and must keep two different names: the
    # ``profile`` argument is the deck's COMPOSITION profile (it carries
    # ``strict_dom_gate``, which decides terminal-fallback eligibility), while
    # the pinned one is the attempt's ROLLOUT profile (the five switches, and
    # the policy revision every receipt is minted under). Binding the pin over
    # the argument made every context eligibility-false, because a rollout
    # profile has no ``strict_dom_gate`` -- see 4afa7a141 and the stage-2
    # enforce analysis in artifacts/authored-live-series-20260915.
    rollout = _pin_attempt_profile(session, request=request, generation_job=generation_job)
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
                policy_revision=rollout.policy_revision,
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


def record_enforced_admission(
    session: AdmissionSession,
    *,
    producer: str,
    gate_verdict: str,
    gate_code: str,
    predicate: str = "",
    block_count: int = 0,
    block_ids: Sequence[str] | None = None,
    error: BaseException | None = None,
) -> None:
    """Record one enforce-mode admission as its own evidence row.

    Enforce never runs the legacy comparator, so the existing verdict is
    ``unobserved`` -- the same word the publication adapter already uses for a
    row it could not compare. Every other field is the shadow row's, so one
    parser and one report read both modes.

    ``block_ids`` is the sample that makes an enforce rejection diagnosable:
    this is the only per-call evidence enforce emits, and the raised
    ``AuthoredStageError`` carries the predicate, not the nodes.
    """

    session.shadow.record(
        producer=producer,
        existing_verdict="unobserved",
        existing_code="",
        gate_verdict=gate_verdict,
        gate_code=gate_code,
        predicate=predicate,
        block_count=block_count,
        block_ids=block_ids,
        error=error,
    )


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
    gate_error: Exception | None = None
    try:
        result = session.admit(context, html, metrics=metrics, png=png)
    except Exception as error:
        gate_code = bounded_code(getattr(error, "code", INTERNAL_ERROR))
        # A shadow instrumentation bug must not change existing producer behavior.
        if session.mode == "enforce":
            record_enforced_admission(
                session, producer=producer, gate_verdict="error", gate_code=gate_code, error=error
            )
            raise
        result, gate_error = None, error
    else:
        gate_code = result.code
    if session.mode == "enforce":
        # Enforce runs one path, so there is no existing verdict to compare
        # against -- but the row is this mode's only per-call evidence, and
        # without it the rollout's own comparison has nothing to read on the
        # enforce side. Recorded before ``require`` so a rejection is logged.
        record_enforced_admission(
            session,
            producer=producer,
            gate_verdict=result.verdict,
            gate_code=gate_code,
            predicate=result.predicate,
            block_count=len(result.block_ids),
            block_ids=result.block_ids,
        )
        if not evidence:
            result.require()
        elif context.state == "captured":
            result = replace(result, receipt=replace(result.receipt, persistence_class="evidence"))
        return result.html, result
    evidence_fields = {
        "predicate": result.predicate if result else "",
        "block_count": len(result.block_ids) if result else 0,
        "error": gate_error,
    }
    try:
        existing = legacy(html)
    except Exception as error:
        session.shadow.record(
            producer=producer,
            existing_verdict="rejected",
            existing_code=bounded_code(getattr(error, "code", RENDERER_POLICY_FAILED)),
            gate_verdict=result.verdict if result else "error",
            gate_code=gate_code,
            **evidence_fields,
        )
        raise
    legacy_flagged = isinstance(existing, dict) and existing.get("outcome") == VERBATIM_FLAG_ONLY
    session.shadow.record(
        producer=producer,
        existing_verdict="flag_only" if flags or legacy_flagged else "admitted",
        existing_code="",
        gate_verdict=result.verdict if result else "error",
        gate_code=gate_code,
        **evidence_fields,
    )
    return existing, result


def admission_shadow_receipt() -> dict | None:
    """The deck-level receipt, stating which switches produced it."""
    session = current_session()
    if session is None:
        return None
    return dict(
        session.shadow.as_dict(),
        rolloutProfile=session.profile.as_dict(),
        policyRevision=session.profile.policy_revision,
    )
