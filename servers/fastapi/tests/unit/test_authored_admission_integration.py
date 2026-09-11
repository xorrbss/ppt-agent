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
from services.authored_admission_publication import final_readback
from services.authored_admission_session import AdmissionSession, _SESSION, admission_scope, configure_admission
from tests.unit.test_authored_admission import HTML, MANIFEST, OUTLINE, context, metrics


def sync_async(function):
    @wraps(function)
    def run(*args, **kwargs):
        return asyncio.run(function(*args, **kwargs))

    return run


@pytest.fixture
def session(monkeypatch):
    monkeypatch.setattr(gate, "text_step", gate.bind_current_source)
    monkeypatch.setattr(gate, "placeholder_authoring_enabled", lambda: False)
    value = AdmissionSession(mode="enforce", contexts=(context(),))
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
    receipt = gate.admit_slide_html(context(state="captured"), html, metrics=metrics(html), png=b"png").receipt
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
@pytest.mark.parametrize("flagged", [False, True])
async def test_final_publication_is_strict_except_already_flagged_visibility(session, flagged):
    session.contexts = (replace(context(), terminal_fallback_eligible=True),)
    html = author_html(HTML, index=0)
    request = SimpleNamespace(_source_document_outline=OUTLINE, _source_content_manifest=MANIFEST)
    kwargs = dict(
        pngs=[b"png"],
        generation_job=None,
        request=request,
        htmls=[html],
        source_reflow=True,
        composition_profile=SimpleNamespace(text_mode="verbatim"),
        terminal_fallback_enabled=True,
        evidence_ledger=object(),
        final_browser_metrics=[metrics(html, cssVisible=False)],
        flagged_slides=[{"index": 0, "reason": "source_readback:visibility"}] if flagged else [],
        _merge_flagged_slide_entries=lambda entries: list(entries),
    )
    if not flagged:
        with pytest.raises(Exception, match="visibility"):
            await final_readback(**kwargs)
    else:
        _, payload, _ = await final_readback(**kwargs)
        assert payload["integrityVerified"] and payload["flaggedVisibilityFailures"] == ["b1"]


@sync_async
@pytest.mark.parametrize("mode", ["shadow", "enforce"])
async def test_a_deck_missing_a_source_page_cannot_publish(session, mode):
    """B2: the deck-level cardinality assertion holds in shadow and in enforce.

    Each surviving slide validates against its own one-unit manifest, so only a
    whole-deck predicate can see that a source page was dropped. It runs before
    any per-slide work, on the same implementation the slide inspector uses.
    """

    session.mode = mode
    html = author_html(HTML, index=0)
    two_units = {"units": [*MANIFEST["units"], {**MANIFEST["units"][0], "sourceUnitId": "u2", "sourceIndex": 1}]}
    two_slides = {"slides": [*OUTLINE["slides"], {**OUTLINE["slides"][0], "sourceUnitId": "u2", "sourceIndex": 1}]}
    kwargs = dict(
        pngs=[b"png"],
        generation_job=None,
        request=SimpleNamespace(_source_document_outline=two_slides, _source_content_manifest=two_units),
        htmls=[html],
        source_reflow=True,
        composition_profile=SimpleNamespace(text_mode="verbatim"),
        terminal_fallback_enabled=False,
        evidence_ledger=object(),
        final_browser_metrics=[metrics(html)],
        flagged_slides=[],
        _merge_flagged_slide_entries=lambda entries: list(entries),
    )
    with pytest.raises(Exception, match="cardinality") as raised:
        await final_readback(**kwargs)
    assert raised.value.code == "source_content_browser_readback_failed"


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
    monkeypatch.setattr(checkpoint, "admission_gate_mode", lambda: "enforce")
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


@sync_async
async def test_rescue_cannot_mint_a_new_flag_in_enforce(session):
    from services.authored_author_stage_rescue import source_reflow_rescue_readback

    html = author_html(HTML, index=0)
    result = source_reflow_rescue_readback(
        index=0,
        html=html,
        metrics=metrics(html, cssVisible=False),
        bindings=context().bindings,
        profile=SimpleNamespace(strict_dom_gate=True),
        reference_mode="source_content_restyle",
    )
    assert result is None
    assert any(item.verdict == "rejected" for item in session.admissions.values())


@sync_async
async def test_vqa_producer_uses_same_rejection_and_label(session):
    from services.authored_vqa_source_integrity import source_vqa_candidate_integrity_passes

    html = author_html(HTML, index=0)

    async def validator(index, value, sample):
        source_candidate(
            value, index=index, metrics=sample, manifest=MANIFEST, outline=OUTLINE, bindings=context().bindings
        )

    assert not await source_vqa_candidate_integrity_passes(0, html, metrics(html, renderedText="changed"), validator)


@sync_async
async def test_scope_pins_mode_and_configures_source(monkeypatch):
    from services import authored_admission_session as sessions

    monkeypatch.setattr(sessions, "admission_gate_mode", lambda: "shadow")

    @admission_scope
    async def run():
        configure_admission(
            request=SimpleNamespace(
                _source_content_authored_reflow=True,
                _source_content_manifest=MANIFEST,
                _source_document_outline=OUTLINE,
            ),
            presentation_id="p",
            generation_job=None,
            profile=SimpleNamespace(strict_dom_gate=True),
            reference_mode="source_content_restyle",
            slide_count=1,
            bindings=(context().bindings,),
            image_bindings=((),),
        )
        return sessions.current_session().contexts[0].manifest_digest

    assert await run() == context().manifest_digest


def test_shadow_report_aggregates_content_free_lines():
    root = Path(__file__).resolve().parents[4]
    spec = importlib.util.spec_from_file_location("shadow_report", root / "scripts/authored_admission_shadow_report.py")
    report = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(report)
    rows = report.aggregate(
        [
            'prefix authored_admission_shadow {"producer":"author",'
            '"existingVerdict":"admitted","gateVerdict":"rejected"}\n'
        ]
        * 2
    )
    assert "| author | admitted | rejected | 2 |" in report.markdown(rows)


@sync_async
async def test_vqa_correction_reuses_captured_evidence(session, monkeypatch):
    from unittest.mock import AsyncMock
    from utils.llm_calls import author_vision_qa as vqa
    from utils.slide_capture_result import RenderCapture
    from services.authored_admission_context import digest_html

    html = author_html(HTML, index=0, producer="vqa_repair")
    capture = AsyncMock(
        return_value=RenderCapture.from_payload(
            html_digest=digest_html(html),
            png=b"png",
            browser_metrics=metrics(html),
            runtime_counters={},
        )
    )
    monkeypatch.setattr(vqa, "render_html_to_png_with_metrics", capture)
    assert await vqa._render_correction_html(html, index=0) == b"png"
    assert await vqa._render_correction_html(html, index=0) == b"png"
    assert capture.await_count == 1


def test_enforce_removes_the_legacy_deck_seal_check(session):
    from unittest.mock import Mock
    from services.authored_admission_producers import require_bound_source_html

    legacy = Mock(side_effect=AssertionError("legacy check ran"))
    require_bound_source_html([HTML], (context().bindings,), stamp=legacy)
    legacy.assert_not_called()
    # The captured gate still rejects unsealed bytes before they can publish.
    assert gate.admit_slide_html(context(state="captured"), HTML, metrics=metrics(HTML)).verdict == "rejected"


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


def test_batch_provider_raw_diagnostics_precede_legacy_rewrites(session, monkeypatch):
    from unittest.mock import Mock
    from utils.llm_calls import author_batch as batch

    rewrite = Mock(side_effect=AssertionError("rewrote malformed raw HTML"))
    monkeypatch.setattr(batch, "_strip_nonvisual_pointer_events", rewrite)
    request = batch.AuthoredBatchSlideRequest(index=0, content="", role="body")
    prepared = SimpleNamespace(request=request, source_slot_candidates={})
    with pytest.raises(gate.BrowserSourceReadbackError):
        batch._postprocess_slide(prepared, HTML.replace("<h1>", '<h1 id="a" id="b">'), brand=None)
    rewrite.assert_not_called()
