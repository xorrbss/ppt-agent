from __future__ import annotations

from functools import wraps
import asyncio

from contextlib import asynccontextmanager
from dataclasses import replace
import importlib.util
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from services import authored_admission as gate
from services.authored_admission_checkpoint import prepare_checkpoint_write
from services.authored_admission_producers import author_html, illustrate_html, source_candidate
from services.authored_admission_session import (
    AdmissionSession,
    _SESSION,
    admission_scope,
    check_producer,
    configure_admission,
)
from services.authored_rollout_profile import RolloutProfile, rollout_profile_scope
from tests.unit.test_authored_admission import HTML, MANIFEST, OUTLINE, context, metrics


def sync_async(function):
    @wraps(function)
    def run(*args, **kwargs):
        return asyncio.run(function(*args, **kwargs))

    return run


@pytest.fixture
def session(monkeypatch):
    profile = RolloutProfile(gate_mode="enforce")
    monkeypatch.setattr(gate, "text_step", gate.bind_current_source)
    monkeypatch.setattr(gate, "placeholder_authoring_enabled", lambda: False)
    with rollout_profile_scope(profile):
        value = AdmissionSession(
            mode="enforce", profile=profile, pinned_profile=profile, contexts=(context(),)
        )
        token = _SESSION.set(value)
        try:
            yield value
        finally:
            _SESSION.reset(token)


def test_author_and_illustration_producers_use_gate(session):
    html = author_html(HTML, index=0)
    assert "data-source-block-id" in html
    assert illustrate_html(html, index=0) == html
    assert any(value.receipt.context.finalized for value in session.admissions.values())
    source_candidate(
        html, index=0, metrics=metrics(html), manifest=MANIFEST, outline=OUTLINE, bindings=(("b1", "원문 제목"),)
    )
    with pytest.raises(gate.BrowserSourceReadbackError):
        source_candidate(
            html,
            index=0,
            metrics=metrics(html, renderedText="changed"),
            manifest=MANIFEST,
            outline=OUTLINE,
            bindings=(("b1", "원문 제목"),),
        )


def test_checkpoint_preparation_does_not_publish_rejected_capture(session):
    html = author_html(HTML, index=0)
    prepared = prepare_checkpoint_write(
        dict(html=html, stage="render", slide_index=0, browser_metrics=metrics(html, cssVisible=False), png=b"png")
    )
    assert prepared["admission_receipt"].verdict == "rejected"
    assert prepared["admission_receipt"].as_dict()["publishable"] is False


@sync_async
async def test_author_writer_persists_receipt_with_html(monkeypatch, session):
    from services import authored_checkpoint_state as state

    html = author_html(HTML, index=0)
    receipt = gate.admit_slide_html(context(), html).receipt
    checkpoint = SimpleNamespace(error=None)
    database = SimpleNamespace(add_all=lambda _: None, commit=AsyncMock())

    @asynccontextmanager
    async def transaction(_):
        yield database, True

    monkeypatch.setattr(state, "_checkpoint_mutation_session", transaction)
    monkeypatch.setattr(state, "_owned_job", AsyncMock(return_value=SimpleNamespace()))
    monkeypatch.setattr(state, "_checkpoint", AsyncMock(return_value=checkpoint))
    result = await state.complete_stage(
        job_id="job",
        slide_index=0,
        stage="author",
        attempt_token="attempt-1",
        fencing_token=4,
        html=html,
        admission_receipt=receipt,
    )
    assert result.author_html == html
    assert result.error["admission"]["htmlDigest"] == receipt.html_digest
    database.commit.assert_awaited_once()


def test_publication_reader_ignores_evidence_and_stale_receipts(session):
    from services.authored_checkpoint_html import checkpoint_stage_html

    html = author_html(HTML, index=0)
    receipt = gate.admit_slide_html(context(html_repr="captured"), html, metrics=metrics(html), png=b"png").receipt
    checkpoint = SimpleNamespace(
        illustrated_html=html,
        attempt_token="attempt-1",
        error={"admission": replace(receipt, persistence_class="evidence").as_dict()},
    )
    assert checkpoint_stage_html(checkpoint, publishable=True) is None
    checkpoint.error = {"admission": receipt.as_dict()}
    assert checkpoint_stage_html(checkpoint, publishable=True) == html
    checkpoint.attempt_token = "newer"
    assert checkpoint_stage_html(checkpoint, publishable=True) is None


@sync_async
async def test_a_session_less_producer_is_admitted_at_the_writer(monkeypatch):
    """B1: enforce must not turn "no admission session" into a missing receipt.

    The authored-vNext legacy bridge and the workspace page-remake writers call
    ``complete_stage`` outside ``admission_scope``. The write goes through, and
    the receipt it persists is one the gate minted for those exact bytes.
    """

    from services import authored_checkpoint_state as state
    from services import authored_admission_checkpoint as checkpoint
    from services.authored_admission_context import digest_html

    monkeypatch.setattr(gate, "text_step", gate.bind_current_source)
    monkeypatch.setattr(gate, "placeholder_authoring_enabled", lambda: False)
    monkeypatch.setattr(checkpoint, "enforce_receipts", lambda: True)
    assert checkpoint.current_session() is None
    row = SimpleNamespace(error=None)
    database = SimpleNamespace(add_all=lambda _: None, commit=AsyncMock())

    @asynccontextmanager
    async def transaction(_):
        yield database, True

    monkeypatch.setattr(state, "_checkpoint_mutation_session", transaction)
    monkeypatch.setattr(state, "_owned_job", AsyncMock(return_value=SimpleNamespace()))
    monkeypatch.setattr(state, "_checkpoint", AsyncMock(return_value=row))
    result = await state.complete_stage(
        job_id="job",
        slide_index=0,
        stage="author",
        attempt_token="attempt-1",
        fencing_token=4,
        html=HTML,
    )
    receipt = result.error["admission"]
    # N2: the write is admitted, and the receipt says out loud that its context
    # was defaulted rather than observed, so no publication reader will take it.
    assert receipt["verdict"] == "admitted"
    assert receipt["identity"] == "incomplete" and receipt["publishable"] is False
    assert receipt["htmlRepr"] == "expanded"
    assert receipt["htmlDigest"] == digest_html(result.author_html)
    assert receipt["attemptToken"] == "attempt-1" and receipt["fencedRevision"] == 4
    database.commit.assert_awaited_once()


def _configure_source_reflow_deck(profile):
    """Configure one verbatim source-reflow slide, as the generation owner does."""

    configure_admission(
        request=SimpleNamespace(
            _source_content_authored_reflow=True,
            _source_content_manifest=MANIFEST,
            _source_document_outline=OUTLINE,
        ),
        presentation_id="p",
        generation_job=None,
        profile=profile,
        reference_mode="source_content_restyle",
        slide_count=1,
        bindings=(context().bindings,),
        image_bindings=((),),
    )


@sync_async
async def test_scope_pins_mode_and_configures_source(monkeypatch):
    from services import authored_admission_session as sessions

    monkeypatch.setattr(sessions, "resolve_rollout_profile", lambda: RolloutProfile(gate_mode="shadow"))
    monkeypatch.setenv("AUTHORED_PUBLISH_FLAGGED_SLIDES_SOURCE_REFLOW", "true")

    @admission_scope
    async def run():
        _configure_source_reflow_deck(SimpleNamespace(strict_dom_gate=True))
        return sessions.current_session().contexts[0]

    pinned = await run()
    assert pinned.manifest_digest == context().manifest_digest
    # The attempt's ROLLOUT profile names the policy revision; the caller's
    # COMPOSITION profile decides terminal-fallback eligibility. Pinning the
    # first over the second minted every context ineligible, because a rollout
    # profile carries no ``strict_dom_gate`` -- and this one assertion is what
    # 4afa7a141 got past.
    assert pinned.policy_revision == RolloutProfile(gate_mode="shadow").policy_revision
    assert pinned.terminal_fallback_eligible is True


@sync_async
async def test_enforce_flags_a_visibility_failure_on_an_already_flagged_slide(monkeypatch):
    """An already-flagged verbatim slide publishes flagged; an unflagged one dies.

    Stage-2 enforce killed three 35-slide decks at publication on exactly this
    row: ``terminal_flag_only | rejected | visibility``. The verdict rule was
    right and its context was wrong, so the rule alone cannot be the test.
    """

    from services import authored_admission_session as sessions

    monkeypatch.setattr(gate, "text_step", gate.bind_current_source)
    monkeypatch.setattr(gate, "placeholder_authoring_enabled", lambda: False)
    monkeypatch.setattr(sessions, "resolve_rollout_profile", lambda: RolloutProfile(gate_mode="enforce"))
    monkeypatch.setenv("AUTHORED_PUBLISH_FLAGGED_SLIDES_SOURCE_REFLOW", "true")

    @admission_scope
    async def run():
        _configure_source_reflow_deck(SimpleNamespace(strict_dom_gate=True))
        session = sessions.current_session()
        html = author_html(HTML, index=0)
        hidden = metrics(html, cssVisible=False)
        _published, flagged = check_producer(
            html,
            index=0,
            producer="terminal_flag_only",
            legacy=lambda value: value,
            metrics=hidden,
            flags=("composition_safety:gate_repair_not_admitted",),
        )
        with pytest.raises(gate.BrowserSourceReadbackError) as unflagged:
            check_producer(
                html, index=0, producer="publication", legacy=lambda value: value, metrics=hidden
            )
        return session, flagged, unflagged.value

    session, flagged, rejection = await run()
    assert (flagged.verdict, flagged.predicate) == ("flag_only", "visibility")
    assert flagged.block_ids and rejection.block_ids == flagged.block_ids
    # The enforce evidence rows name the nodes, not only how many there were.
    rows = {row["producer"]: row for row in session.shadow.as_dict()["rows"]}
    assert rows["terminal_flag_only"]["gateVerdict"] == "flag_only"
    assert rows["publication"]["gateVerdict"] == "rejected"
    assert all(
        tuple(rows[producer]["blockIds"]) == flagged.block_ids
        for producer in ("terminal_flag_only", "publication")
    )


def shadow_report():
    root = Path(__file__).resolve().parents[4]
    spec = importlib.util.spec_from_file_location("shadow_report", root / "scripts/authored_admission_shadow_report.py")
    report = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(report)
    return report


def test_shadow_report_aggregates_content_free_lines():
    report = shadow_report()
    rows = report.aggregate(
        [
            'prefix authored_admission_shadow {"producer":"author",'
            '"existingVerdict":"admitted","gateVerdict":"rejected"}\n'
        ]
        * 2
    )
    # ``series_metrics.py`` unpacks this key as a three-tuple; it must not grow.
    assert list(rows) == [("author", "admitted", "rejected")]
    assert "| author | admitted | rejected | 2 |" in report.markdown(rows)


def test_enforce_emits_the_rows_the_stage_is_measured_by(session):
    """Stage 2's deliverable is the shadow/enforce comparison of these rows.

    Enforce returned before recording, so its half of the table was empty by
    construction: the one mode the rollout exists to measure emitted no
    measurements, and a rejected slide left no code, predicate or block count
    anywhere in the log.
    """

    html = author_html(HTML, index=0)
    bindings = (("b1", "원문 제목"),)
    source_candidate(html, index=0, metrics=metrics(html), manifest=MANIFEST, outline=OUTLINE, bindings=bindings)
    with pytest.raises(gate.BrowserSourceReadbackError):
        source_candidate(
            html,
            index=0,
            metrics=metrics(html, renderedText="changed"),
            manifest=MANIFEST,
            outline=OUTLINE,
            bindings=bindings,
        )
    rows = session.shadow.as_dict()["rows"]
    # Enforce runs one path, so there is no existing verdict to compare against.
    assert rows and all(row["existingVerdict"] == "unobserved" for row in rows)
    assert {row["producer"] for row in rows} == {"author", "gate_repair"}
    rejected = [row for row in rows if row["gateVerdict"] == "rejected"]
    assert [(row["producer"], row["predicate"], row["blockCount"]) for row in rejected] == [
        ("gate_repair", "rendered_integrity", 1)
    ]
    assert rejected[0]["gateCode"] == "source_content_browser_readback_failed"


def test_shadow_report_scopes_to_one_operation_and_names_the_predicate():
    """One worker log holds several decks; a parity table is per operation.

    The shadow line carries no operation id of its own -- it is content-free --
    so a row belongs to the most recent operation id the worker logged above it.
    """

    report = shadow_report()
    lines = [
        'workspace_operation {"operation_id":"11111111-1111-4111-8111-111111111111"}\n',
        'x authored_admission_shadow {"producer":"render","existingVerdict":"admitted",'
        '"existingCode":"","gateVerdict":"rejected","gateCode":"source_content_browser_readback_failed",'
        '"predicate":"visibility","blockCount":2,"gateError":"","gateErrorDigest":""}\n',
        'workspace_operation {"operation_id":"22222222-2222-4222-8222-222222222222"}\n',
        'x authored_admission_shadow {"producer":"publication","existingVerdict":"unobserved",'
        '"existingCode":"","gateVerdict":"error","gateCode":"authored_admission_internal_error",'
        '"predicate":"","blockCount":0,"gateError":"TypeError","gateErrorDigest":"0123456789abcdef"}\n',
    ]
    whole_log = report.aggregate(lines, columns=report.COLUMNS)
    assert sum(whole_log.values()) == 2
    scoped = report.aggregate(lines, operation="22222222", columns=report.COLUMNS)
    table = report.markdown(scoped, columns=report.COLUMNS)
    assert sum(scoped.values()) == 1
    assert "| publication | unobserved |  | error | authored_admission_internal_error |" in table
    assert table.rstrip().endswith("|  | 0 | TypeError | 1 |")
    assert "| producer | existing verdict |" in table and "| blocks | gate error | count |" in table


def test_enforce_removes_the_legacy_deck_seal_check(session):
    from unittest.mock import Mock
    from services.authored_admission_producers import require_bound_source_html

    legacy = Mock(side_effect=AssertionError("legacy check ran"))
    require_bound_source_html([HTML], (context().bindings,), stamp=legacy)
    legacy.assert_not_called()
    # The captured gate still rejects unsealed bytes before they can publish.
    assert gate.admit_slide_html(context(html_repr="captured"), HTML, metrics=metrics(HTML)).verdict == "rejected"


def test_provider_output_marker_never_reinterprets_inserted_tokens(session, monkeypatch):
    from services.authored_admission_producers import provider_raw_html, provider_expanded_html

    states = []

    def step(ctx, html):
        states.append(ctx.state)
        return gate.TextStepResult(html.replace("원문 제목", "literal {{q:other}}"))

    monkeypatch.setattr(gate, "text_step", step)
    expanded = provider_raw_html(HTML, index=0)
    expanded = provider_expanded_html(expanded, index=0, validate=gate.validate_authored_slide_html)
    assert author_html(expanded, index=0) == expanded
    assert states == ["token", "expanded", "expanded"]
    # A new model response is token even when its bytes match an old expansion.
    provider_raw_html(expanded, index=0)
    assert states[-1] == "token"
