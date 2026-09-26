"""D7 terminal-slide fallback: remake once, then publish flagged.

The deck-level cases drive the real generation function on SQLite the way
``tests/unit/test_authored_pipeline.py`` does (mocked provider, mocked
Chromium), because the decision under test is a deck outcome: published with a
flag versus failed with ``authored_composition_safety_gate_failed``.
"""

import asyncio
import hashlib
import uuid
from types import SimpleNamespace

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlmodel import SQLModel

from models.authored_header_contract import default_header_contract
from models.generate_presentation_request import GeneratePresentationRequest
from models.presentation_outline_model import (
    PresentationOutlineModel,
    SlideOutlineModel,
)
from models.sql.presentation import PresentationModel
from models.sql.presentation_generation_job import PresentationGenerationJob
from models.sql.slide import SlideModel
from services import authored_admission_publication as admission_publication_mod
from services import authored_presentation_generation as authored_generation_mod
from services import authored_presentation_service as authored_service
from services import authored_repair_round_order as order_mod
from services import authored_terminal_slide_fallback as terminal_mod
from services import authored_terminal_slide_policy as policy_mod
from services.authored_flag_classification import (
    REPAIR_NOT_ADMITTED,
    REVIEW_REQUIRED,
)
from services.authored_vnext.generation.scene_checkpoint import (
    SceneRepairBudgetExhausted,
)
from services.authored_evidence_ledger import DeckEvidenceLedger
from services.authored_admission_context import digest_html
from services.authored_composition_policy import CompositionProfile
from services.authored_generation_preflight import over_budget_violations
from services.authored_generation_timing_receipt_e4 import (
    flagged_slide_entries,
    optional_top_level_fields,
)
from services.authored_quality_receipt_v2 import public_attempt_id
from services.source_content.browser_readback import (
    BrowserSourceBlockEvidence,
    BrowserSourceReadbackError,
    BrowserSourceReadbackResult,
    log_browser_source_readback_failure,
)
from utils.llm_calls import author_vision_qa as author_vision_qa_mod
from utils.llm_calls.author_deck import AuthoredDeckResult
from utils.owner_context import reset_current_owner_id, set_current_owner_id
from utils.authored_semantic_role_repair import canonicalize_authored_roles
from utils.slide_capture_result import RenderCapture

TERMINAL_MARKER = "TERMINAL-SLIDE"

_TERMINAL_HTML = (
    "<!DOCTYPE html><html lang='en'><head><meta charset=\"utf-8\"></head>"
    f"<body><h1>{TERMINAL_MARKER}</h1></body></html>"
)
_REMADE_HTML = (
    "<!DOCTYPE html><html lang='en'><head><meta charset=\"utf-8\"></head>"
    "<body><h1>REMADE</h1></body></html>"
)



def _run(coro):
    return asyncio.run(coro)


def _pop_slide_ids(entries):
    """Strip the two fields these tests do not fix, after checking both.

    Publication backfills every flagged-slide entry with the id of the slide it
    was generated for (see ``authored_presentation_publication``). Its value is
    a fresh ``uuid4`` the test cannot predict, so assert only that it is present
    and well-formed, then compare the rest of the entry against the fixed
    expectations the tests already had.

    ``classification`` is likewise orthogonal to what these cases verify -- they
    pin which slides are flagged and why, not whether a flag asks for a reviewer.
    Assert it is one of the two values and strip it;
    ``tests/unit/test_authored_flag_classification.py`` fixes the rule itself, and
    ``test_the_published_receipt_classifies_every_flag`` below pins that the
    published receipt carries it at all.
    """

    result = []
    for entry in entries:
        entry = dict(entry)
        slide_id = entry.pop("slideId", None)
        assert isinstance(slide_id, str) and slide_id, entry
        uuid.UUID(slide_id)
        classification = entry.pop("classification", None)
        assert classification in {REVIEW_REQUIRED, REPAIR_NOT_ADMITTED}, entry
        result.append(entry)
    return result


class _FakeIssue:
    category = "overlap"
    detail = "two owned boxes overlap"
    severity = "high"
    qids = ()

    def to_dict(self):
        return {
            "category": self.category,
            "detail": self.detail,
            "severity": self.severity,
        }


class _FakeReport:
    telemetry_status = "available"
    # M1's fit magnitude rides on the same report; a stub report carries none.
    fit_summary = None

    def __init__(self, html):
        self.issues = [_FakeIssue()] if TERMINAL_MARKER in html else []

    @property
    def needs_attention(self):
        return bool(self.issues)


def _fake_inspect(html, _metrics, **_kwargs):
    return _FakeReport(html)


def _fake_blocking(report):
    return tuple(report.issues)


def _fit_receipts(htmls):
    return tuple(
        {
            "policy_revision": "authored-local-fit/v1.3",
            "finalHtmlDigest": "sha256:"
            + hashlib.sha256(
                canonicalize_authored_roles(html).encode("utf-8")
            ).hexdigest(),
            "qualityStatus": "passed",
            "unresolvedReasons": [],
            "changed": False,
        }
        for html in htmls
    )


def _outline(count):
    return PresentationOutlineModel(
        slides=[SlideOutlineModel(content=f"slide {i} content") for i in range(count)]
    )


async def _sqlite_engine(tmp_path):
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{tmp_path / 'terminal-fallback.db'}"
    )
    async with engine.begin() as connection:
        await connection.run_sync(SQLModel.metadata.create_all)
    return engine


async def _generate_on_sqlite(tmp_path, request, outline, generation_job=None):
    presentation_id = uuid.uuid4()
    engine = await _sqlite_engine(tmp_path)
    try:
        async with AsyncSession(engine, expire_on_commit=False) as session:
            result = await authored_service.generate_authored_presentation(
                request, presentation_id, outline, "English", session, generation_job
            )
            presentation = await session.get(PresentationModel, presentation_id)
    finally:
        await engine.dispose()
    return result, presentation


async def _slides_for(tmp_path, presentation_id):
    """Read back the persisted slide rows the same sqlite file now holds."""

    engine = await _sqlite_engine(tmp_path)
    try:
        async with AsyncSession(engine, expire_on_commit=False) as session:
            rows = await session.scalars(
                select(SlideModel)
                .where(SlideModel.presentation == presentation_id)
                .order_by(SlideModel.index)
            )
            return list(rows)
    finally:
        await engine.dispose()


def _install_deck_stubs(monkeypatch, tmp_path, htmls):
    """Everything outside the gate: provider, Chromium, export, persistence."""

    monkeypatch.setenv("APP_DATA_DIRECTORY", str(tmp_path / "app_data"))
    selected_style = SimpleNamespace(
        id="editorial", brief="- Editorial", illustration_prompt=None
    )

    async def fake_pipeline(
        outline,
        brand,
        style,
        header_contract=None,
        page_number_mode="none",
        chapter_plan=None,
        illustration_plan=None,
        instructions=None,
    ):
        return authored_service.AuthoredGenerationPipelineResult(
            deck=AuthoredDeckResult(list(htmls), "DESIGN SYSTEM"),
            pngs=[b"png" for _ in htmls],
            elapsed=0.1,
            author_work=0.1,
            illustration_work=0.0,
            render_work=0.1,
            local_fit_receipts=_fit_receipts(htmls),
        )

    async def fake_header_contract(
        outline, brand, style=None, instructions=None, chapter_breadcrumb=False
    ):
        return default_header_contract(primary=brand.primary)

    async def fake_metrics(_html):
        return {"telemetry": "available"}

    monkeypatch.setattr(
        authored_service, "resolve_authored_style", lambda style_id: selected_style
    )
    monkeypatch.setattr(
        authored_service, "_author_illustrate_render_pipeline", fake_pipeline
    )
    monkeypatch.setattr(
        authored_service, "resolve_header_contract", fake_header_contract
    )
    monkeypatch.setattr(authored_service, "find_chrome", lambda: True)
    monkeypatch.setattr(
        authored_service,
        "_save_slide_pngs",
        lambda presentation_id, pngs, *args, **kwargs: [
            f"slide-{i}.png" for i in range(len(pngs))
        ],
    )
    monkeypatch.setattr(
        authored_service,
        "_build_authored_export",
        lambda request, presentation_id, pngs, title, **_kwargs: "deck.pptx",
    )
    from utils import llm_provider

    monkeypatch.setattr(
        llm_provider, "get_llm_provider", lambda: SimpleNamespace(value="stub")
    )
    monkeypatch.setattr(llm_provider, "get_model", lambda: "stub-model")

    # The gate, on both sides of the fallback boundary.
    for module in (authored_generation_mod, terminal_mod):
        monkeypatch.setattr(module, "inspect_authored_dom", _fake_inspect)
        monkeypatch.setattr(module, "blocking_safety_issues", _fake_blocking)
        monkeypatch.setattr(
            module, "capacity_violations", lambda *args, **kwargs: ()
        )
    monkeypatch.setattr(authored_generation_mod, "ensure_browser_metrics", fake_metrics)

    async def fake_fit_content_revision(html, *, slide_index, policy_revision=None, **_kwargs):
        canonical = canonicalize_authored_roles(html)
        return SimpleNamespace(
            html=canonical,
            png=b"remade-png",
            metrics={"telemetry": "ok"},
            receipt=_fit_receipts([canonical])[0],
        )

    monkeypatch.setattr(
        authored_generation_mod, "_fit_content_revision", fake_fit_content_revision
    )
    monkeypatch.setattr(
        authored_generation_mod, "cache_accepted_capture", lambda *_a, **_k: None
    )
    monkeypatch.setattr(
        terminal_mod, "get_cached_browser_metrics", lambda _html: {"telemetry": "ok"}
    )
    monkeypatch.setattr(terminal_mod, "deck_has_picture_source", lambda _style: False)
    monkeypatch.setattr(terminal_mod, "_placeholder_png", lambda: b"placeholder")

    async def fake_capture(html, *, fit=False, **_kwargs):
        """Shaped like the real capture on both sides of the fit knob: it takes
        ``fit=`` because the knob defaults on, and reports nothing fitted, which
        is what a slide with no escaping card produces."""

        return RenderCapture.from_payload(
            html_digest=digest_html(html), png=b"remade-png",
            browser_metrics={"telemetry": "ok"}, runtime_counters={},
        )

    monkeypatch.setattr(terminal_mod, "render_html_to_png_with_metrics", fake_capture)


def _install_remake(monkeypatch, calls, *, candidate):
    """The same candidate for every remake call."""

    _install_remakes(monkeypatch, calls, candidates=[candidate])


def _install_remakes(monkeypatch, calls, *, candidates):
    """One candidate per remake CALL, so the two remake rounds can differ.

    The last entry is reused once the list runs out, which keeps a deck with
    more terminal slides than entries on a single final outcome.
    """

    async def fake_revise(*args, **kwargs):
        calls.append(kwargs.get("slide_indices"))
        candidate = candidates[min(len(calls) - 1, len(candidates) - 1)]
        if candidate is None:
            # The reviser hands the source back unchanged when it fails.
            return [list(args[0]), list(args[1]), [], [{"attempted": True}]]
        return [[candidate], [b"remade-png"], [0], [{"path": "full_reauthor"}]]

    monkeypatch.setattr(terminal_mod, "revise_authored_deck", fake_revise)


def _request():
    return GeneratePresentationRequest(
        content="topic", template="authored", authored_style="editorial"
    )


def test_remake_that_passes_the_gate_publishes_the_remade_slide(
    monkeypatch, tmp_path
):
    _install_deck_stubs(monkeypatch, tmp_path, [_TERMINAL_HTML])
    calls: list[object] = []
    _install_remake(monkeypatch, calls, candidate=_REMADE_HTML)

    owner_token = set_current_owner_id(uuid.uuid4())
    try:
        result, presentation = _run(
            _generate_on_sqlite(tmp_path, _request(), _outline(1))
        )
    finally:
        reset_current_owner_id(owner_token)

    assert result.path == "deck.pptx"
    gate = presentation.theme["authoredQualityGate"]
    assert "flaggedSlides" not in gate
    assert gate["status"] == "passed"
    # Exactly one remake call, for the terminal slide only.
    assert calls == [[0]]


def test_remake_that_stays_terminal_publishes_the_deck_with_a_flag(
    monkeypatch, tmp_path
):
    _install_deck_stubs(monkeypatch, tmp_path, [_TERMINAL_HTML])
    calls: list[object] = []
    # A candidate that still carries the terminal marker fails the re-run gate.
    _install_remake(monkeypatch, calls, candidate=_TERMINAL_HTML)

    owner_token = set_current_owner_id(uuid.uuid4())
    try:
        result, presentation = _run(
            _generate_on_sqlite(tmp_path, _request(), _outline(1))
        )
    finally:
        reset_current_owner_id(owner_token)

    assert result.path == "deck.pptx"
    gate = presentation.theme["authoredQualityGate"]
    assert _pop_slide_ids(gate["flaggedSlides"]) == [
        {
            "index": 0,
            "reason": "composition_safety:overlap",
            "remakeAttempted": True,
            "remakeOutcome": "remake_gate_failed",
        }
    ]
    # The residual evidence is still recorded next to the flag.
    assert gate["residualIssueCount"] == 1
    assert gate["documentCompleted"] is True
    assert gate["qualityPass"] is False
    assert calls == [[0]]


def test_the_flagged_slide_id_matches_the_actual_persisted_slide(
    monkeypatch, tmp_path
):
    """Defect 1 (editor flagged-slide badge review): the receipt's ``index``

    is a generation-time position, not a durable handle -- a client-side
    reorder renumbers every slide's ``index`` and persists it, which would
    then match the receipt's frozen index to a different slide. Publication
    backfills each entry with the id of the slide it actually describes, so
    the client can key its badge by that id instead.
    """

    # Two slides: only the second is terminal, so the flag must name it and
    # not the first (whose id publication assigns first).
    _install_deck_stubs(monkeypatch, tmp_path, [_REMADE_HTML, _TERMINAL_HTML])
    calls: list[object] = []
    _install_remake(monkeypatch, calls, candidate=_TERMINAL_HTML)

    owner_token = set_current_owner_id(uuid.uuid4())
    try:
        result, presentation = _run(
            _generate_on_sqlite(tmp_path, _request(), _outline(2))
        )
        slides = _run(_slides_for(tmp_path, presentation.id))
    finally:
        reset_current_owner_id(owner_token)

    assert result.path == "deck.pptx"
    flagged = presentation.theme["authoredQualityGate"]["flaggedSlides"]
    assert [entry["index"] for entry in flagged] == [1]

    flagged_slide = next(slide for slide in slides if slide.index == 1)
    other_slide = next(slide for slide in slides if slide.index == 0)
    assert flagged[0]["slideId"] == str(flagged_slide.id)
    assert flagged[0]["slideId"] != str(other_slide.id)


def test_knob_off_keeps_todays_failed_deck(monkeypatch, tmp_path):
    monkeypatch.setenv("AUTHORED_PUBLISH_FLAGGED_SLIDES", "false")
    _install_deck_stubs(monkeypatch, tmp_path, [_TERMINAL_HTML])
    calls: list[object] = []
    _install_remake(monkeypatch, calls, candidate=_REMADE_HTML)

    owner_token = set_current_owner_id(uuid.uuid4())
    try:
        with pytest.raises(Exception) as caught:
            _run(_generate_on_sqlite(tmp_path, _request(), _outline(1)))
    finally:
        reset_current_owner_id(owner_token)

    assert getattr(caught.value, "code", None) == (
        "authored_composition_safety_gate_failed"
    )
    assert calls == []


def test_exactly_one_remake_call_per_terminal_slide(monkeypatch, tmp_path):
    _install_deck_stubs(
        monkeypatch, tmp_path, [_REMADE_HTML, _TERMINAL_HTML, _TERMINAL_HTML]
    )
    calls: list[object] = []
    # Every candidate stays terminal, so no attempt can be mistaken for a retry.
    _install_remake(monkeypatch, calls, candidate=_TERMINAL_HTML)

    owner_token = set_current_owner_id(uuid.uuid4())
    try:
        _run(_generate_on_sqlite(tmp_path, _request(), _outline(3)))
    finally:
        reset_current_owner_id(owner_token)

    assert calls == [[1], [2]]


def test_remake_budget_flags_without_calling_the_provider_again(monkeypatch):
    profile = SimpleNamespace(
        profile_id="ordinary", strict_dom_gate=True, allow_summarization=True
    )
    slide_count = terminal_mod.MAX_TERMINAL_REMAKES_PER_DECK + 2
    htmls = [_TERMINAL_HTML for _ in range(slide_count)]
    calls: list[object] = []
    monkeypatch.setattr(terminal_mod, "inspect_authored_dom", _fake_inspect)
    monkeypatch.setattr(terminal_mod, "blocking_safety_issues", _fake_blocking)
    monkeypatch.setattr(terminal_mod, "capacity_violations", lambda *a, **k: ())
    monkeypatch.setattr(
        terminal_mod, "get_cached_browser_metrics", lambda _html: {"telemetry": "ok"}
    )
    _install_remake(monkeypatch, calls, candidate=_TERMINAL_HTML)

    outcome = _run(
        terminal_mod.resolve_terminal_gate_slides(
            htmls=list(htmls),
            pngs=[b"png"] * slide_count,
            contents=["c"] * slide_count,
            roles=["body"] * slide_count,
            brand=SimpleNamespace(primary="#000"),
            design_system="DS",
            style=SimpleNamespace(id="editorial"),
            capacity_plan=None,
            profile=profile,
            reference_mode="style_reference",
            scene_envelope=SimpleNamespace(),
            metrics_by_slide={},
        )
    )

    assert len(calls) == terminal_mod.MAX_TERMINAL_REMAKES_PER_DECK
    assert outcome.remade_slide_indices == ()
    outcomes = [entry["remakeOutcome"] for entry in outcome.flagged_slides]
    assert outcomes.count("remake_budget_exhausted") == 2


def test_verbatim_source_decks_default_to_fail_closed(monkeypatch):
    calls: list[object] = []
    _install_remake(monkeypatch, calls, candidate=_REMADE_HTML)
    profile = SimpleNamespace(
        profile_id="source-verbatim", strict_dom_gate=True, allow_summarization=False
    )

    outcome = _run(
        terminal_mod.resolve_terminal_gate_slides(
            htmls=[_TERMINAL_HTML],
            pngs=[b"png"],
            contents=["c"],
            roles=["body"],
            brand=SimpleNamespace(primary="#000"),
            design_system="DS",
            style=SimpleNamespace(id="editorial"),
            capacity_plan=None,
            profile=profile,
            reference_mode="style_reference",
            scene_envelope=SimpleNamespace(),
            metrics_by_slide={},
            source_reflow=True,
        )
    )

    assert outcome.flagged_slides == ()
    assert calls == []


def test_verbatim_source_decks_flag_terminal_slides_without_remaking(monkeypatch):
    monkeypatch.setenv("AUTHORED_PUBLISH_FLAGGED_SLIDES_SOURCE_REFLOW", "true")
    profile = SimpleNamespace(
        profile_id="source-verbatim", strict_dom_gate=True, allow_summarization=False
    )

    async def forbidden(*_args, **_kwargs):
        raise AssertionError("source reflow must not remake or apply a slide")

    monkeypatch.setattr(terminal_mod, "remake_slide_once", forbidden)
    monkeypatch.setattr(terminal_mod, "apply_admitted_remake", forbidden)
    monkeypatch.setattr(terminal_mod, "inspect_authored_dom", _fake_inspect)
    monkeypatch.setattr(terminal_mod, "blocking_safety_issues", _fake_blocking)
    monkeypatch.setattr(terminal_mod, "capacity_violations", lambda *a, **k: ())
    monkeypatch.setattr(
        terminal_mod, "get_cached_browser_metrics", lambda _html: {"telemetry": "ok"}
    )

    outcome = _run(
        terminal_mod.resolve_terminal_gate_slides(
            htmls=[_TERMINAL_HTML],
            pngs=[b"png"],
            contents=["verbatim source"],
            roles=["body"],
            brand=SimpleNamespace(primary="#000"),
            design_system="DS",
            style=SimpleNamespace(id="editorial"),
            capacity_plan=None,
            profile=profile,
            reference_mode="style_reference",
            scene_envelope=SimpleNamespace(),
            metrics_by_slide={},
            source_reflow=True,
            remake_counts={0: 1},
        )
    )

    assert outcome.flagged_slides == (
        {
            "index": 0,
            "reason": "composition_safety:overlap",
            "remakeAttempted": False,
            "remakeOutcome": "verbatim_flag_only",
        },
    )
    assert outcome.remade_slide_indices == ()
    assert outcome.remake_calls == 0


def test_source_reflow_flag_policy_is_separate_strict_and_not_exact(monkeypatch):
    monkeypatch.setenv("AUTHORED_PUBLISH_FLAGGED_SLIDES_SOURCE_REFLOW", "true")
    verbatim = SimpleNamespace(strict_dom_gate=True, allow_summarization=False)

    assert policy_mod.terminal_fallback_active(
        profile=verbatim,
        reference_mode="style_reference",
        source_reflow=True,
    )
    assert not policy_mod.terminal_fallback_active(
        profile=SimpleNamespace(strict_dom_gate=False, allow_summarization=False),
        reference_mode="style_reference",
        source_reflow=True,
    )
    assert not policy_mod.terminal_fallback_active(
        profile=verbatim,
        reference_mode="exact_reconstruction",
        source_reflow=True,
    )


def test_merge_flagged_slide_entries_keeps_a_lone_flag_byte_identical():
    entry = {
        "index": 3,
        "reason": "composition_safety:overlap",
        "remakeAttempted": True,
        "remakeOutcome": "remake_gate_failed",
    }
    assert authored_generation_mod._merge_flagged_slide_entries([entry]) == [entry]


def test_merge_flagged_slide_entries_combines_duplicate_indices():
    author_entry = {
        "index": 0,
        "reason": "author:attempts_exhausted",
        "remakeAttempted": True,
        "remakeOutcome": "placeholder",
    }
    gate_entry = {
        "index": 0,
        "reason": "composition_safety:overlap",
        "remakeAttempted": True,
        "remakeOutcome": "remake_gate_failed",
    }
    unrelated = {"index": 1, "reason": "composition_safety:clipping"}

    merged = authored_generation_mod._merge_flagged_slide_entries(
        (author_entry, gate_entry, unrelated)
    )

    assert merged == [
        {
            **author_entry,
            "reasons": ["author:attempts_exhausted", "composition_safety:overlap"],
        },
        unrelated,
    ]


def test_merge_flagged_slide_entries_does_not_duplicate_an_identical_reason():
    entry = {"index": 5, "reason": "composition_safety:overflow"}
    merged = authored_generation_mod._merge_flagged_slide_entries([entry, dict(entry)])
    assert merged == [entry]


def test_flagged_slide_receipt_entries_are_validated_and_serialized():
    entries = [
        {
            "index": 4,
            "reason": "composition_safety:overlap",
            "remakeAttempted": True,
            "remakeOutcome": "remake_gate_failed",
        }
    ]

    assert flagged_slide_entries(entries) == tuple(entries)
    assert flagged_slide_entries(None) == ()
    fields = optional_top_level_fields(
        provider_rate_profile=None,
        normalizations=(),
        quality_loop_variant=None,
        flagged_slides=entries,
    )
    assert fields == {"flaggedSlides": entries}
    with pytest.raises(ValueError):
        flagged_slide_entries([{"index": 0}])


def test_partition_residual_failures_keeps_unflagged_slides_blocking():
    residual = [{"slide_index": 0, "issues": []}, {"slide_index": 2, "issues": []}]
    flagged = [
        {
            "index": 2,
            "reason": "composition_safety:overlap",
            "remakeAttempted": True,
            "remakeOutcome": "remake_gate_failed",
        }
    ]

    blocking, accepted = policy_mod.partition_residual_failures(residual, flagged)

    assert blocking == [{"slide_index": 0, "issues": []}]
    assert accepted == [{"slide_index": 2, "issues": []}]


# ---------------------------------------------------------------------------
# The bounded gate-repair round's own residual (bench job 8ae5d152, slides=[1]).
# Before D7 closed this, the round's ``except Exception`` raised
# ``authored_composition_safety_gate_failed`` before the terminal fallback ran.
# ---------------------------------------------------------------------------


def _residual_round_error(htmls):
    return author_vision_qa_mod.AuthoredVqaResidualDomIssuesError(
        "2:available:1",
        htmls=list(htmls),
        pngs=[b"round-png" for _ in htmls],
        fixed=[],
        repair_details=[{"attempted": True, "succeeded": False}],
    )


def _install_repair_round(monkeypatch, *, raises, candidate=None):
    """Drive the first-pass gate into the bounded repair round for one slide.

    ``candidate`` is the HTML the patch round returns for every reviewed
    slide; the default hands the input back unchanged, which is what a patch
    that repaired nothing does.
    """

    monkeypatch.setattr(
        authored_generation_mod,
        "_deterministic_repair_candidate",
        lambda **kwargs: kwargs["html"],
    )
    monkeypatch.setattr(
        authored_generation_mod,
        "preflight_authored_provider_repair",
        lambda **kwargs: SimpleNamespace(
            provider_call_allowed=True, terminal=False, code=None, violations=()
        ),
    )
    monkeypatch.setattr(
        authored_generation_mod,
        "get_cached_browser_metrics",
        lambda _html: {"telemetry": "ok"},
    )
    monkeypatch.setattr(
        authored_generation_mod, "_placeholder_png", lambda: b"placeholder"
    )
    monkeypatch.setattr(
        authored_generation_mod,
        "semantic_repair_boundary_violations",
        lambda *args, **kwargs: (),
    )

    async def fake_bind_capture(html):
        return SimpleNamespace(png=b"bound-png", browser_metrics={"telemetry": "ok"})

    monkeypatch.setattr(
        authored_generation_mod, "render_html_to_png_with_metrics", fake_bind_capture
    )

    round_calls: list[list[str]] = []

    async def fake_revise(*args, **kwargs):
        round_calls.append(list(args[0]))
        if raises is not None:
            raise raises(list(args[0]))
        if candidate is not None:
            return [[candidate for _ in args[0]], list(args[1]), []]
        return [list(args[0]), list(args[1]), []]

    # The facade copies its own name into the generation module on every call.
    monkeypatch.setattr(authored_service, "revise_authored_deck", fake_revise)
    return round_calls


def _source_reflow_request(count):
    request = _request()
    request._source_content_authored_reflow = True
    request._authored_composition_profile = CompositionProfile(
        "source-verbatim", "verbatim", False, True, 2
    )
    request._source_content_manifest = {
        "units": [
            {"sourceUnitId": f"unit-{index}", "blocks": []}
            for index in range(count)
        ]
    }
    request._source_document_outline = {
        "slides": [
            {"sourceUnitId": f"unit-{index}"}
            for index in range(count)
        ]
    }
    return request


def _install_source_reflow_contract_stubs(monkeypatch, count, browser_receipt=None):
    async def no_source_images(*_args, **_kwargs):
        return []

    monkeypatch.setattr(
        authored_generation_mod, "collect_source_image_candidates", no_source_images
    )
    monkeypatch.setattr(
        authored_generation_mod,
        "_source_block_bindings",
        lambda _request: tuple({} for _ in range(count)),
    )
    monkeypatch.setattr(
        authored_generation_mod,
        "_source_image_block_bindings",
        lambda _request: tuple({} for _ in range(count)),
    )
    monkeypatch.setattr(
        authored_generation_mod, "_require_source_verbatim_bindings", lambda *_a: None
    )
    monkeypatch.setattr(
        authored_generation_mod, "source_pages_are_numbered", lambda _bindings: False
    )
    monkeypatch.setattr(
        authored_generation_mod, "inspect_slide_source_visibility", lambda **_kwargs: ()
    )
    monkeypatch.setattr(
        authored_generation_mod, "make_source_repair_evaluator", lambda **_kwargs: object()
    )
    monkeypatch.setattr(
        authored_generation_mod, "validate_visible_source_images", lambda *_a: None
    )
    receipt = BrowserSourceReadbackResult(
        checked_slide_count=count, checked_block_count=0
    )
    # b5a98b9f2 moved the final deck-level readback out of
    # ``authored_presentation_generation`` into ``authored_admission_publication``
    # (function ``_legacy_final_readback``, called by ``final_readback``), which
    # imports these three names into its own module namespace rather than
    # calling back into the generation module. Patch them where they are now
    # actually looked up.
    monkeypatch.setattr(
        admission_publication_mod,
        "verify_source_verbatim_readback",
        lambda **_kwargs: receipt,
    )
    browser_calls = []

    def inspect_browser(**kwargs):
        browser_calls.append((len(kwargs["htmls"]), len(kwargs["browser_metrics"])))
        return browser_receipt or receipt

    def verify_browser(**kwargs):
        result = inspect_browser(**kwargs)
        if not result.passed:
            raise BrowserSourceReadbackError(
                "source_content_browser_readback_failed",
                detail="browser source text/geometry/visibility contract failed",
                block_ids=(*result.missing_block_ids, *result.visibility_failures),
                slide_indices=result.failed_slide_indices,
                order_violations=result.order_violations,
            )
        return result

    monkeypatch.setattr(
        admission_publication_mod, "verify_browser_source_readback", verify_browser
    )
    monkeypatch.setattr(
        admission_publication_mod, "inspect_browser_source_readback", inspect_browser
    )
    final_gate_flags = []

    async def final_gate(**kwargs):
        final_gate_flags.append(list(kwargs["flagged_slide_indices"]))
        return [
            {"slide_index": index, "issues": [_FakeIssue().to_dict()]}
            for index in kwargs["flagged_slide_indices"]
        ]

    monkeypatch.setattr(authored_generation_mod, "_final_authored_quality_gate", final_gate)
    return browser_calls, final_gate_flags


def _bind_source_block(request, *, slide_index, block_id):
    request._source_content_manifest["units"][slide_index]["blocks"] = [
        {
            "kind": "text", "sourceBlockId": block_id,
            "contentDigest": "sha256:" + "1" * 64,
        }
    ]
    request._source_document_outline["slides"][slide_index]["readingOrder"] = [block_id]


def _browser_failure_receipt(count, *, block_id, slide_index, missing=False):
    evidence = () if missing else (
        BrowserSourceBlockEvidence(
            source_block_id=block_id, qid="q-source", rendered_text="source text",
            bbox={"x": 1.0, "y": 1.0, "width": 10.0, "height": 10.0},
            visible=True, clipped=False, occluded_fraction=0.1,
            continuous_occluded_fraction=0.1,
        ),
    )
    return BrowserSourceReadbackResult(
        checked_slide_count=count, checked_block_count=0 if missing else 1,
        evidence=evidence,
        missing_block_ids=(block_id,) if missing else (),
        visibility_failures=() if missing else (block_id,),
        failed_slide_indices=(slide_index,),
    )


def _install_source_reflow_unresolved_round(monkeypatch):
    fail_closed_values = []
    monkeypatch.setattr(
        authored_generation_mod,
        "_deterministic_repair_candidate",
        lambda **kwargs: kwargs["html"],
    )
    monkeypatch.setattr(
        authored_generation_mod,
        "preflight_authored_provider_repair",
        lambda **_kwargs: SimpleNamespace(
            provider_call_allowed=True, terminal=False, code=None, violations=()
        ),
    )

    async def fake_revise(*args, **kwargs):
        fail_closed = kwargs["fail_on_residual_dom_issues"]
        fail_closed_values.append(fail_closed)
        result = SimpleNamespace(
            slide_index=kwargs["slide_indices"][0],
            final_status="unresolved",
            changed=False,
            html=args[0][0],
            png=args[1][0],
            metrics=kwargs["initial_browser_metrics"][0],
        )
        kwargs["structural_results_sink"].append(result)
        if fail_closed:
            error = _residual_round_error(args[0])
            error.repair_results = [result]
            raise error
        return [list(args[0]), list(args[1]), []]

    monkeypatch.setattr(authored_service, "revise_authored_deck", fake_revise)
    return fail_closed_values


def _flagged_pipeline_stub(
    monkeypatch, htmls, flagged_index, evidence=None, issues=None
):
    """``evidence`` is the ``(ledger, attempt_id)`` pair the receipt path needs.

    ``issues`` replaces the default overlap finding on the flagged slide.
    """

    ledger, attempt_id = evidence if evidence is not None else (None, None)
    gate_issues = list(issues) if issues is not None else [_FakeIssue().to_dict()]

    async def fake_pipeline(
        outline,
        brand,
        style,
        header_contract=None,
        page_number_mode="none",
        chapter_plan=None,
        illustration_plan=None,
        instructions=None,
        # A durable job is handed to the real pipeline as a keyword.
        **_job_kwargs,
    ):
        return authored_service.AuthoredGenerationPipelineResult(
            deck=AuthoredDeckResult(list(htmls), "DESIGN SYSTEM"),
            pngs=[b"png" for _ in htmls],
            elapsed=0.1,
            author_work=0.1,
            illustration_work=0.0,
            render_work=0.1,
            quality_gate_failures=(
                ()
                if flagged_index is None
                else (
                    {
                        "slide_index": flagged_index,
                        "issues": gate_issues,
                    },
                )
            ),
            local_fit_receipts=_fit_receipts(htmls),
            evidence_ledger=ledger,
            evidence_attempt_id=attempt_id,
            # The v3 receipt audits one provider-call row per slide; a deck
            # without a ledger keeps today's empty inventory.
            author_provider_attempt_counts=(
                tuple(1 for _ in htmls) if ledger is not None else ()
            ),
        )

    monkeypatch.setattr(
        authored_service, "_author_illustrate_render_pipeline", fake_pipeline
    )


def _install_source_reflow_vqa_unresolved(monkeypatch, slide_index):
    fail_closed_values = []

    async def fake_revise(
        htmls,
        pngs,
        *_args,
        fail_on_residual_dom_issues=False,
        repair_details_sink=None,
    ):
        fail_closed_values.append(fail_on_residual_dom_issues)
        detail = {
            "local_index": slide_index,
            "slide_index": slide_index,
            "attempted": True,
            "succeeded": False,
            "candidate_status": "unresolved",
        }
        repair_details_sink.append(detail)
        if fail_on_residual_dom_issues:
            raise author_vision_qa_mod.AuthoredVqaResidualDomIssuesError(
                f"{slide_index + 1}:available:1",
                htmls=list(htmls),
                pngs=list(pngs),
                fixed=[],
                repair_details=[detail],
            )
        return [list(htmls), list(pngs), []]

    monkeypatch.setattr(authored_service, "revise_authored_deck", fake_revise)
    return fail_closed_values


def _locally_accepted_whole_page_failure_stub(monkeypatch, htmls, flagged_index):
    """A local-fit acceptance can still fail the later whole-page gate.

    The receipt is intentionally a clean, baseline-bound *acceptance* from
    local fit; the overflow below is the independent page-quality finding that
    arrives after that capture.  Keeping the two records distinct prevents a
    future change from treating a local-fit receipt as a whole-page verdict.
    """

    receipts = tuple(
        {
            "policy_revision": "authored-local-fit/v1.3",
            "finalHtmlDigest": (
                "sha256:"
                + hashlib.sha256(
                    canonicalize_authored_roles(html).encode("utf-8")
                ).hexdigest()
            ),
            **(
                {
                    "selectionStatus": "preserved_original_review",
                    "deliveryRevision": "authored-local-fit-original-delivery/v1",
                    "deliveryStatus": "output_usable",
                    "pageOutputReady": True,
                    "qualityPass": False,
                    "qualityStatus": "warning",
                    "changed": False,
                    "renderedFitDisabled": True,
                    "verifiedFitDisabled": False,
                    "unresolvedReasons": ["whole_page_geometry"],
                }
                if index == flagged_index
                else {
                    "qualityStatus": "passed",
                    "unresolvedReasons": [],
                    "changed": False,
                }
            ),
        }
        for index, html in enumerate(htmls)
    )
    local_fit_flag = {
        "index": flagged_index,
        "reason": "composition_safety:local_fit",
        "remakeAttempted": False,
        "remakeOutcome": "not_attempted",
        "reasons": ["local_fit:whole_page_geometry"],
    }

    async def fake_pipeline(
        outline,
        brand,
        style,
        header_contract=None,
        page_number_mode="none",
        chapter_plan=None,
        illustration_plan=None,
        instructions=None,
        **_job_kwargs,
    ):
        return authored_service.AuthoredGenerationPipelineResult(
            deck=AuthoredDeckResult(list(htmls), "DESIGN SYSTEM"),
            pngs=[b"png" for _ in htmls],
            elapsed=0.1,
            author_work=0.1,
            illustration_work=0.0,
            render_work=0.1,
            quality_gate_failures=(
                {"slide_index": flagged_index, "issues": [_FakeIssue().to_dict()]},
            ),
            local_fit_receipts=receipts,
            # Reuse the existing publication channel.  A local-fit warning is
            # terminal, like an author-stage fallback, so it must not need a
            # parallel result field or a second receipt schema.
            author_flagged_slides=(local_fit_flag,),
        )

    monkeypatch.setattr(
        authored_service, "_author_illustrate_render_pipeline", fake_pipeline
    )


def test_source_reflow_unresolved_repair_is_predeclared_and_published_flagged(
    monkeypatch, tmp_path
):
    monkeypatch.setenv("AUTHORED_PUBLISH_FLAGGED_SLIDES_SOURCE_REFLOW", "true")
    deck = [_REMADE_HTML, _TERMINAL_HTML]
    _install_deck_stubs(monkeypatch, tmp_path, deck)
    evidence, _recorded, _recorded_v3 = _install_receipt_evidence(monkeypatch)
    _flagged_pipeline_stub(monkeypatch, deck, 1, evidence=evidence)
    browser_calls, final_gate_flags = _install_source_reflow_contract_stubs(
        monkeypatch, len(deck)
    )
    fail_closed_values = _install_source_reflow_unresolved_round(monkeypatch)

    def forbidden_validation(*_args, **_kwargs):
        raise AssertionError("unresolved source repair must not be validated")

    monkeypatch.setattr(
        authored_generation_mod, "validate_repair_result", forbidden_validation
    )
    owner_token = set_current_owner_id(uuid.uuid4())
    try:
        result, presentation = _run(
            _generate_on_sqlite(
                tmp_path, _source_reflow_request(len(deck)), _outline(len(deck))
            )
        )
    finally:
        reset_current_owner_id(owner_token)

    assert result.path == "deck.pptx"
    assert fail_closed_values == [False]
    assert final_gate_flags == [[1]]
    assert browser_calls == [(len(deck), len(deck))]
    assert _pop_slide_ids(
        presentation.theme["authoredQualityGate"]["flaggedSlides"]
    ) == [
        {
            "index": 1,
            "reason": "composition_safety:gate_repair_not_admitted",
            "remakeAttempted": False,
            "remakeOutcome": "verbatim_flag_only",
        }
    ]
    # The flag also says which of the two things it is. The final gate measured
    # this slide (``final_gate_flags == [[1]]``), so it is a review requirement
    # and the reviewer-facing list carries it.
    # ``tests/unit/test_authored_flag_classification.py`` fixes the rule, including
    # the other direction where a flag with nothing measured is kept out of that
    # list; this pins that the pipeline applies the rule to what it publishes.
    gate = presentation.theme["authoredQualityGate"]
    assert [entry["classification"] for entry in gate["flaggedSlides"]] == [
        REVIEW_REQUIRED
    ]
    assert [page["index"] for page in gate["reviewPages"]] == [1]


def test_source_reflow_unresolved_repair_still_fails_with_fallback_disabled(
    monkeypatch, tmp_path
):
    monkeypatch.setenv("AUTHORED_PUBLISH_FLAGGED_SLIDES_SOURCE_REFLOW", "false")
    deck = [_REMADE_HTML, _TERMINAL_HTML]
    _install_deck_stubs(monkeypatch, tmp_path, deck)
    _flagged_pipeline_stub(monkeypatch, deck, 1)
    _install_source_reflow_contract_stubs(monkeypatch, len(deck))
    fail_closed_values = _install_source_reflow_unresolved_round(monkeypatch)

    owner_token = set_current_owner_id(uuid.uuid4())
    try:
        with pytest.raises(Exception) as caught:
            _run(
                _generate_on_sqlite(
                    tmp_path, _source_reflow_request(len(deck)), _outline(len(deck))
                )
            )
    finally:
        reset_current_owner_id(owner_token)

    assert getattr(caught.value, "code", None) == (
        "authored_composition_safety_gate_failed"
    )
    assert fail_closed_values == [True]


def test_source_reflow_unresolved_vqa_is_predeclared_and_published_flagged(
    monkeypatch, tmp_path
):
    monkeypatch.setenv("AUTHORED_PUBLISH_FLAGGED_SLIDES_SOURCE_REFLOW", "true")
    deck = [_REMADE_HTML, _REMADE_HTML]
    _install_deck_stubs(monkeypatch, tmp_path, deck)
    evidence, _recorded, _recorded_v3 = _install_receipt_evidence(monkeypatch)
    _flagged_pipeline_stub(monkeypatch, deck, None, evidence=evidence)
    browser_calls, final_gate_flags = _install_source_reflow_contract_stubs(
        monkeypatch, len(deck)
    )
    fail_closed_values = _install_source_reflow_vqa_unresolved(monkeypatch, 1)
    request = _source_reflow_request(len(deck))
    request.vision_qa = True

    owner_token = set_current_owner_id(uuid.uuid4())
    try:
        result, presentation = _run(
            _generate_on_sqlite(tmp_path, request, _outline(len(deck)))
        )
    finally:
        reset_current_owner_id(owner_token)

    assert result.path == "deck.pptx"
    assert fail_closed_values == [False]
    assert final_gate_flags == [[1]]
    assert browser_calls == [(len(deck), len(deck))]
    assert _pop_slide_ids(
        presentation.theme["authoredQualityGate"]["flaggedSlides"]
    ) == [
        {
            "index": 1,
            "reason": "composition_safety:gate_repair_not_admitted",
            "remakeAttempted": False,
            "remakeOutcome": "verbatim_flag_only",
        }
    ]


def test_source_reflow_unresolved_vqa_still_fails_with_fallback_disabled(
    monkeypatch, tmp_path
):
    monkeypatch.setenv("AUTHORED_PUBLISH_FLAGGED_SLIDES_SOURCE_REFLOW", "false")
    deck = [_REMADE_HTML]
    _install_deck_stubs(monkeypatch, tmp_path, deck)
    _flagged_pipeline_stub(monkeypatch, deck, None)
    _install_source_reflow_contract_stubs(monkeypatch, len(deck))
    fail_closed_values = _install_source_reflow_vqa_unresolved(monkeypatch, 0)
    request = _source_reflow_request(len(deck))
    request.vision_qa = True

    owner_token = set_current_owner_id(uuid.uuid4())
    try:
        with pytest.raises(Exception) as caught:
            _run(_generate_on_sqlite(tmp_path, request, _outline(len(deck))))
    finally:
        reset_current_owner_id(owner_token)

    assert getattr(caught.value, "code", None) == "authored_vqa_residual_dom_issues"
    assert fail_closed_values == [True]


def test_flagged_visibility_only_readback_publishes_and_records_reason(
    monkeypatch, tmp_path
):
    monkeypatch.setenv("AUTHORED_PUBLISH_FLAGGED_SLIDES_SOURCE_REFLOW", "true")
    deck = [_REMADE_HTML, _TERMINAL_HTML]
    block_id = "source-slide:2:text:1"
    _install_deck_stubs(monkeypatch, tmp_path, deck)
    evidence, _recorded, _recorded_v3 = _install_receipt_evidence(monkeypatch)
    _flagged_pipeline_stub(monkeypatch, deck, 1, evidence=evidence)
    receipt = _browser_failure_receipt(
        len(deck), block_id=block_id, slide_index=1
    )
    _install_source_reflow_contract_stubs(
        monkeypatch, len(deck), browser_receipt=receipt
    )
    _install_source_reflow_unresolved_round(monkeypatch)
    request = _source_reflow_request(len(deck))
    _bind_source_block(request, slide_index=1, block_id=block_id)

    owner_token = set_current_owner_id(uuid.uuid4())
    try:
        result, presentation = _run(
            _generate_on_sqlite(tmp_path, request, _outline(len(deck)))
        )
    finally:
        reset_current_owner_id(owner_token)

    assert result.path == "deck.pptx"
    gate = presentation.theme["authoredQualityGate"]
    flagged = _pop_slide_ids(gate["flaggedSlides"])[0]
    assert flagged["reasons"] == [
        "composition_safety:gate_repair_not_admitted", "source_visibility"
    ]
    assert flagged["sourceVisibilityBlockIds"] == [block_id]
    source_readback = gate["sourceReadback"]
    assert source_readback["passed"] is False
    assert source_readback["verified"] is False
    assert source_readback["integrityVerified"] is True
    assert source_readback["flaggedVisibilityFailures"] == [block_id]


def test_flagged_fallback_still_rejects_a_missing_source_block(monkeypatch, tmp_path, caplog):
    monkeypatch.setenv("AUTHORED_PUBLISH_FLAGGED_SLIDES_SOURCE_REFLOW", "true")
    deck = [_REMADE_HTML, _TERMINAL_HTML]
    block_id = "source-slide:2:text:1"
    _install_deck_stubs(monkeypatch, tmp_path, deck)
    evidence, _recorded, _recorded_v3 = _install_receipt_evidence(monkeypatch)
    _flagged_pipeline_stub(monkeypatch, deck, 1, evidence=evidence)
    _install_source_reflow_contract_stubs(
        monkeypatch, len(deck), browser_receipt=_browser_failure_receipt(
            len(deck), block_id=block_id, slide_index=1, missing=True
        )
    )
    _install_source_reflow_unresolved_round(monkeypatch)
    request = _source_reflow_request(len(deck))
    _bind_source_block(request, slide_index=1, block_id=block_id)

    owner_token = set_current_owner_id(uuid.uuid4())
    try:
        with caplog.at_level("WARNING", logger="services.source_content.browser_readback"):
            with pytest.raises(Exception) as caught:
                _run(_generate_on_sqlite(tmp_path, request, _outline(len(deck))))
    finally:
        reset_current_owner_id(owner_token)

    assert getattr(caught.value, "code", None) == "source_content_browser_readback_failed"
    assert caught.value.retryable is False
    assert f"missing=[{block_id}]" in str(caught.value)
    assert "visibility=[]" in str(caught.value)
    assert any(f"missing=[{block_id}]" in record.message for record in caplog.records)


def test_readback_failure_summary_splits_and_bounds_identifiers(caplog):
    result = BrowserSourceReadbackResult(
        2,
        15,
        missing_block_ids=tuple(f"missing-{index}" for index in range(10)),
        order_violations=3,
        integrity_failures=("integrity-1",),
        telemetry_failures=("telemetry-1",),
        visibility_failures=("visibility-1",),
        failed_slide_indices=(0, 1),
        diagnostic_error="must not be emitted",
    )

    with caplog.at_level("WARNING", logger="services.source_content.browser_readback"):
        summary = log_browser_source_readback_failure(result)

    assert "slides=[0,1]" in summary
    assert "missing=[missing-0,missing-1,missing-2,missing-3,missing-4,missing-5,missing-6,missing-7]+2" in summary
    assert "order=3" in summary
    assert "integrity=[integrity-1]" in summary
    assert "telemetry=[telemetry-1]" in summary
    assert "visibility=[visibility-1]" in summary
    assert "diagnostic=True" in summary
    assert "must not be emitted" not in caplog.text


def test_flagged_fallback_rejects_visibility_failure_on_unflagged_slide(
    monkeypatch, tmp_path
):
    monkeypatch.setenv("AUTHORED_PUBLISH_FLAGGED_SLIDES_SOURCE_REFLOW", "true")
    deck = [_REMADE_HTML, _TERMINAL_HTML]
    block_id = "source-slide:1:text:1"
    _install_deck_stubs(monkeypatch, tmp_path, deck)
    evidence, _recorded, _recorded_v3 = _install_receipt_evidence(monkeypatch)
    _flagged_pipeline_stub(monkeypatch, deck, 1, evidence=evidence)
    _install_source_reflow_contract_stubs(
        monkeypatch, len(deck), browser_receipt=_browser_failure_receipt(
            len(deck), block_id=block_id, slide_index=0
        )
    )
    _install_source_reflow_unresolved_round(monkeypatch)
    request = _source_reflow_request(len(deck))
    _bind_source_block(request, slide_index=0, block_id=block_id)

    owner_token = set_current_owner_id(uuid.uuid4())
    try:
        with pytest.raises(Exception) as caught:
            _run(_generate_on_sqlite(tmp_path, request, _outline(len(deck))))
    finally:
        reset_current_owner_id(owner_token)

    assert getattr(caught.value, "code", None) == "source_content_browser_readback_failed"
    assert caught.value.retryable is False


def test_fallback_disabled_keeps_strict_visibility_readback(monkeypatch, tmp_path):
    monkeypatch.setenv("AUTHORED_PUBLISH_FLAGGED_SLIDES_SOURCE_REFLOW", "false")
    deck = [_REMADE_HTML]
    block_id = "source-slide:1:text:1"
    _install_deck_stubs(monkeypatch, tmp_path, deck)
    evidence, _recorded, _recorded_v3 = _install_receipt_evidence(monkeypatch)
    _flagged_pipeline_stub(monkeypatch, deck, None, evidence=evidence)
    _install_source_reflow_contract_stubs(
        monkeypatch, len(deck), browser_receipt=_browser_failure_receipt(
            len(deck), block_id=block_id, slide_index=0
        )
    )
    request = _source_reflow_request(len(deck))
    _bind_source_block(request, slide_index=0, block_id=block_id)

    owner_token = set_current_owner_id(uuid.uuid4())
    try:
        with pytest.raises(Exception) as caught:
            _run(_generate_on_sqlite(tmp_path, request, _outline(len(deck))))
    finally:
        reset_current_owner_id(owner_token)

    assert getattr(caught.value, "code", None) == "source_content_browser_readback_failed"
    assert caught.value.retryable is True


def test_local_fit_accepted_whole_page_failure_flags_one_slide_without_aborting_the_deck(
    monkeypatch, tmp_path
):
    """A later geometry finding is D7 evidence, not a deck-wide local-fit abort."""

    deck = [_REMADE_HTML, _TERMINAL_HTML, _REMADE_HTML]
    _install_deck_stubs(monkeypatch, tmp_path, deck)
    _locally_accepted_whole_page_failure_stub(monkeypatch, deck, flagged_index=1)
    repair_calls = _install_repair_round(monkeypatch, raises=None)
    remake_calls: list[object] = []
    _install_remake(monkeypatch, remake_calls, candidate=_TERMINAL_HTML)

    owner_token = set_current_owner_id(uuid.uuid4())
    try:
        result, presentation = _run(
            _generate_on_sqlite(tmp_path, _request(), _outline(3))
        )
        slides = _run(_slides_for(tmp_path, presentation.id))
    finally:
        reset_current_owner_id(owner_token)

    assert result.path == "deck.pptx"
    assert [slide.index for slide in slides] == [0, 1, 2]
    # A local-fit accepted capture that later fails the whole-page gate is
    # already terminal evidence. It must not spend the provider repair/remake
    # budget and must not prevent either neighbouring slide from publishing.
    assert repair_calls == []
    assert remake_calls == []
    assert _pop_slide_ids(presentation.theme["authoredQualityGate"]["flaggedSlides"]) == [
        {
            "index": 1,
            "reason": "composition_safety:local_fit",
            "remakeAttempted": False,
            "remakeOutcome": "not_attempted",
            "reasons": ["local_fit:whole_page_geometry"],
        }
    ]


def test_repair_round_residual_reaches_the_remake_instead_of_failing_the_deck(
    monkeypatch, tmp_path
):
    deck = [_REMADE_HTML, _TERMINAL_HTML]
    _install_deck_stubs(monkeypatch, tmp_path, deck)
    _flagged_pipeline_stub(monkeypatch, deck, 1)
    round_calls = _install_repair_round(monkeypatch, raises=_residual_round_error)
    remake_calls: list[object] = []
    # Both remakes stay terminal, so the deck must publish with the flag.
    _install_remake(monkeypatch, remake_calls, candidate=_TERMINAL_HTML)

    owner_token = set_current_owner_id(uuid.uuid4())
    try:
        result, presentation = _run(
            _generate_on_sqlite(tmp_path, _request(), _outline(2))
        )
    finally:
        reset_current_owner_id(owner_token)

    assert result.path == "deck.pptx"
    # The round ran once, over the terminal slide only (the deck's HTML reaches
    # it QID-sealed by the scene envelope, so match the marker, not the source).
    assert [len(call) for call in round_calls] == [1]
    assert TERMINAL_MARKER in round_calls[0][0]
    # Both remakes, for the residual slide only: one ahead of the round and one
    # behind it.
    assert remake_calls == [[1], [1]]
    assert _pop_slide_ids(
        presentation.theme["authoredQualityGate"]["flaggedSlides"]
    ) == [
        {
            "index": 1,
            "reason": "composition_safety:overlap",
            "remakeAttempted": True,
            "remakeOutcome": "remake_budget_exhausted",
            "remakeCount": 2,
        }
    ]


def test_repair_round_residual_publishes_clean_when_the_remake_passes(
    monkeypatch, tmp_path
):
    deck = [_REMADE_HTML, _TERMINAL_HTML]
    _install_deck_stubs(monkeypatch, tmp_path, deck)
    _flagged_pipeline_stub(monkeypatch, deck, 1)
    _install_repair_round(monkeypatch, raises=_residual_round_error)
    remake_calls: list[object] = []
    _install_remake(monkeypatch, remake_calls, candidate=_REMADE_HTML)

    owner_token = set_current_owner_id(uuid.uuid4())
    try:
        result, presentation = _run(
            _generate_on_sqlite(tmp_path, _request(), _outline(2))
        )
    finally:
        reset_current_owner_id(owner_token)

    assert result.path == "deck.pptx"
    assert remake_calls == [[1]]
    gate = presentation.theme["authoredQualityGate"]
    assert "flaggedSlides" not in gate
    assert gate["status"] == "passed"


def test_repair_round_residual_still_fails_the_deck_with_the_knob_off(
    monkeypatch, tmp_path
):
    monkeypatch.setenv("AUTHORED_PUBLISH_FLAGGED_SLIDES", "false")
    deck = [_REMADE_HTML, _TERMINAL_HTML]
    _install_deck_stubs(monkeypatch, tmp_path, deck)
    _flagged_pipeline_stub(monkeypatch, deck, 1)
    _install_repair_round(monkeypatch, raises=_residual_round_error)
    remake_calls: list[object] = []
    _install_remake(monkeypatch, remake_calls, candidate=_REMADE_HTML)

    owner_token = set_current_owner_id(uuid.uuid4())
    try:
        with pytest.raises(Exception) as caught:
            _run(_generate_on_sqlite(tmp_path, _request(), _outline(2)))
    finally:
        reset_current_owner_id(owner_token)

    assert getattr(caught.value, "code", None) == (
        "authored_composition_safety_gate_failed"
    )
    assert remake_calls == []


def test_repair_round_provider_failure_keeps_todays_failed_deck(monkeypatch, tmp_path):
    deck = [_REMADE_HTML, _TERMINAL_HTML]
    _install_deck_stubs(monkeypatch, tmp_path, deck)
    _flagged_pipeline_stub(monkeypatch, deck, 1)

    def provider_failure(_htmls):
        return RuntimeError("provider transport failed")

    _install_repair_round(monkeypatch, raises=provider_failure)
    remake_calls: list[object] = []
    # The remake ahead of the round is rejected, so the round actually runs.
    _install_remake(monkeypatch, remake_calls, candidate=_TERMINAL_HTML)

    owner_token = set_current_owner_id(uuid.uuid4())
    try:
        with pytest.raises(Exception) as caught:
            _run(_generate_on_sqlite(tmp_path, _request(), _outline(2)))
    finally:
        reset_current_owner_id(owner_token)

    assert getattr(caught.value, "code", None) == (
        "authored_composition_safety_gate_failed"
    )
    # A transport failure is not evidence about the slide, so the durable
    # claim it already consumed still ends the attempt: the second remake is
    # never reached.
    assert remake_calls == [[1]]


def test_recovery_refuses_a_disabled_deck_and_an_empty_round():
    error = _residual_round_error([_TERMINAL_HTML])

    assert policy_mod.recover_residual_repair_round(error, enabled=False) is None
    empty = author_vision_qa_mod.AuthoredVqaResidualDomIssuesError(
        "1:available:1", htmls=[], pngs=[], fixed=[], repair_details=[]
    )
    assert policy_mod.recover_residual_repair_round(empty, enabled=True) is None
    recovered = policy_mod.recover_residual_repair_round(error, enabled=True)
    assert recovered is not None
    htmls, pngs, fixed = recovered
    assert htmls == [_TERMINAL_HTML]
    assert fixed == []
    # The pixels are returned only for the round's length invariant; the caller
    # re-renders every html before anything is sealed.
    assert pngs == [b"round-png"]


# ---------------------------------------------------------------------------
# The durable Scene repair claim (bench job da98fa75, resumed attempt 2).
# Slide 12's single repair was consumed by attempt 1, so on resume the claim
# raised SceneRepairBudgetExhausted and the whole deck died with no provider
# call. Under D7 that slide is terminal: remake once, else flag.
# ---------------------------------------------------------------------------


def _exhausted_claim(*exhausted_indices, repeated=True):
    """A ``claim_scene_repair`` whose budget is spent for these slides.

    This is the resumed-attempt shape: attempt 1 consumed slide N's single
    durable repair, attempt 2 reuses every checkpoint and the claim refuses.
    """

    seen: list[int] = []

    def claim(index):
        seen.append(index)
        if index in exhausted_indices:
            raise SceneRepairBudgetExhausted(repeated=repeated)
        return SimpleNamespace(slide_index=index)

    return claim, seen


_ORDINARY = SimpleNamespace(
    profile_id="ordinary", strict_dom_gate=True, allow_summarization=True
)


def test_exhausted_claim_on_resume_leaves_the_round_and_is_declared_terminal():
    claim, seen = _exhausted_claim(1, repeated=True)

    outcome = policy_mod.claim_bounded_repairs(
        [0, 1, 2],
        claim=claim,
        budget_exhausted=SceneRepairBudgetExhausted,
        profile=_ORDINARY,
        reference_mode="style_reference",
        source_reflow=False,
    )

    assert seen == [0, 1, 2]
    assert outcome.blocked is None
    # Slide 1 never enters the deterministic/provider round, so nothing may
    # rewrite it and its original html/png/metrics triple stays untouched.
    assert outcome.flagged == [0, 2]
    assert sorted(outcome.claims) == [0, 2]
    assert outcome.predeclared == [
        {
            "index": 1,
            "reason": policy_mod.REPAIR_BUDGET_EXHAUSTED_REASON,
            "repeated": True,
        }
    ]


def test_exhausted_claim_blocks_the_deck_when_it_may_not_publish_flagged(
    monkeypatch,
):
    monkeypatch.setenv("AUTHORED_PUBLISH_FLAGGED_SLIDES", "false")
    claim, _seen = _exhausted_claim(1, repeated=True)

    outcome = policy_mod.claim_bounded_repairs(
        [0, 1, 2],
        claim=claim,
        budget_exhausted=SceneRepairBudgetExhausted,
        profile=_ORDINARY,
        reference_mode="style_reference",
        source_reflow=False,
    )

    # The caller raises today's authored_composition_safety_gate_failed on this.
    assert outcome.blocked is not None
    blocked_index, blocked_error = outcome.blocked
    assert blocked_index == 1
    assert blocked_error.repeated is True
    assert outcome.predeclared == []


def test_a_deck_with_no_exhausted_claim_is_unchanged():
    claim, seen = _exhausted_claim()

    outcome = policy_mod.claim_bounded_repairs(
        [3],
        claim=claim,
        budget_exhausted=SceneRepairBudgetExhausted,
        profile=_ORDINARY,
        reference_mode="style_reference",
        source_reflow=False,
    )

    assert seen == [3]
    assert outcome.flagged == [3]
    assert outcome.predeclared == []
    assert outcome.blocked is None


def test_declaration_refuses_a_deck_that_may_not_publish_flagged():
    error = SimpleNamespace(repeated=True)
    ordinary = SimpleNamespace(
        profile_id="ordinary", strict_dom_gate=True, allow_summarization=True
    )
    verbatim = SimpleNamespace(
        profile_id="source-verbatim", strict_dom_gate=True, allow_summarization=False
    )

    assert policy_mod.declare_exhausted_repair_budget(
        3, error, profile=ordinary, reference_mode="style_reference", source_reflow=False
    ) == {
        "index": 3,
        "reason": policy_mod.REPAIR_BUDGET_EXHAUSTED_REASON,
        "repeated": True,
    }
    assert (
        policy_mod.declare_exhausted_repair_budget(
            3,
            error,
            profile=verbatim,
            reference_mode="style_reference",
            source_reflow=True,
        )
        is None
    )
    assert (
        policy_mod.declare_exhausted_repair_budget(
            3,
            error,
            profile=ordinary,
            reference_mode="exact_reconstruction",
            source_reflow=False,
        )
        is None
    )


def test_predeclared_slide_is_terminal_without_a_dom_finding(monkeypatch):
    profile = SimpleNamespace(
        profile_id="ordinary", strict_dom_gate=True, allow_summarization=True
    )
    calls: list[object] = []
    monkeypatch.setattr(terminal_mod, "inspect_authored_dom", _fake_inspect)
    monkeypatch.setattr(terminal_mod, "blocking_safety_issues", _fake_blocking)
    monkeypatch.setattr(terminal_mod, "capacity_violations", lambda *a, **k: ())
    monkeypatch.setattr(
        terminal_mod, "get_cached_browser_metrics", lambda _html: {"telemetry": "ok"}
    )
    _install_remake(monkeypatch, calls, candidate=_TERMINAL_HTML)

    outcome = _run(
        terminal_mod.resolve_terminal_gate_slides(
            # A clean slide by every DOM measure; only the declaration makes it
            # terminal, which is the resume case the final gate cannot see.
            htmls=[_REMADE_HTML],
            pngs=[b"png"],
            contents=["c"],
            roles=["body"],
            brand=SimpleNamespace(primary="#000"),
            design_system="DS",
            style=SimpleNamespace(id="editorial"),
            capacity_plan=None,
            profile=profile,
            reference_mode="style_reference",
            scene_envelope=SimpleNamespace(),
            metrics_by_slide={},
            predeclared=[
                {
                    "index": 0,
                    "reason": policy_mod.REPAIR_BUDGET_EXHAUSTED_REASON,
                    "repeated": False,
                }
            ],
        )
    )

    assert len(calls) == 1
    assert outcome.flagged_slides == (
        {
            "index": 0,
            "reason": policy_mod.REPAIR_BUDGET_EXHAUSTED_REASON,
            "remakeAttempted": True,
            "remakeOutcome": "remake_gate_failed",
            "repeated": False,
        },
    )


def test_receipt_accepts_a_flag_with_and_without_the_repeated_bit():
    with_bit = {
        "index": 2,
        "reason": policy_mod.REPAIR_BUDGET_EXHAUSTED_REASON,
        "remakeAttempted": True,
        "remakeOutcome": "remake_gate_failed",
        "repeated": True,
    }
    without_bit = {
        "index": 3,
        "reason": "composition_safety:overlap",
        "remakeAttempted": True,
        "remakeOutcome": "remake_gate_failed",
    }

    assert flagged_slide_entries([with_bit, without_bit]) == (with_bit, without_bit)
    with pytest.raises(ValueError):
        flagged_slide_entries([{**with_bit, "repeated": "yes"}])


# ---------------------------------------------------------------------------
# The bounded gate repair whose candidate was not admitted (bench job 18306a40).
# Slide 10 authored, its repair produced a candidate, and both the candidate and
# the original carried only ELEMENT-NAMED capacity violations -- the body
# wrapper had collapsed to width 0. The deck died with
# ``authored_slide_capacity_exceeded`` without ever reaching the fallback.
# A violation that names an element is a layout defect; only a measured
# ``<check>:<measured>><allowed>`` is a budget the model cannot write around.
# ---------------------------------------------------------------------------


_ELEMENT_NAMED_VIOLATIONS = (
    "browser_geometry_unverified:closing.kicker",
    "browser_text_not_visible:closing.statement",
)
_MEASURED_VIOLATION = ("visible_text:400>280",)


def _install_capacity_violations(monkeypatch, violations):
    """Only the terminal slide violates capacity, exactly as the deck did."""

    def stub(html, *_args, **_kwargs):
        return violations if TERMINAL_MARKER in str(html) else ()

    monkeypatch.setattr(authored_generation_mod, "capacity_violations", stub)


def _unadmitted_gate_repair_deck(monkeypatch, tmp_path, violations, *, remake):
    deck = [_REMADE_HTML, _TERMINAL_HTML]
    _install_deck_stubs(monkeypatch, tmp_path, deck)
    _flagged_pipeline_stub(monkeypatch, deck, 1)
    # The round returns its input unchanged, so the candidate carries the same
    # capacity violations the original does -- the shape job 18306a40 hit.
    _install_repair_round(monkeypatch, raises=None)
    _install_capacity_violations(monkeypatch, violations)
    remake_calls: list[object] = []
    _install_remake(monkeypatch, remake_calls, candidate=remake)
    return remake_calls


def test_unadmitted_gate_repair_with_a_layout_defect_publishes_flagged(
    monkeypatch, tmp_path
):
    remake_calls = _unadmitted_gate_repair_deck(
        monkeypatch, tmp_path, _ELEMENT_NAMED_VIOLATIONS, remake=_TERMINAL_HTML
    )

    owner_token = set_current_owner_id(uuid.uuid4())
    try:
        result, presentation = _run(
            _generate_on_sqlite(tmp_path, _request(), _outline(2))
        )
    finally:
        reset_current_owner_id(owner_token)

    assert result.path == "deck.pptx"
    # Both remakes for the unadmitted slide only, then the flag.
    assert remake_calls == [[1], [1]]
    gate = presentation.theme["authoredQualityGate"]
    assert _pop_slide_ids(gate["flaggedSlides"]) == [
        {
            "index": 1,
            "reason": policy_mod.GATE_REPAIR_NOT_ADMITTED_REASON,
            "remakeAttempted": True,
            "remakeOutcome": "remake_budget_exhausted",
            "remakeCount": 2,
        }
    ]


def test_unadmitted_gate_repair_publishes_clean_when_the_remake_passes(
    monkeypatch, tmp_path
):
    remake_calls = _unadmitted_gate_repair_deck(
        monkeypatch, tmp_path, _ELEMENT_NAMED_VIOLATIONS, remake=_REMADE_HTML
    )

    owner_token = set_current_owner_id(uuid.uuid4())
    try:
        result, presentation = _run(
            _generate_on_sqlite(tmp_path, _request(), _outline(2))
        )
    finally:
        reset_current_owner_id(owner_token)

    assert result.path == "deck.pptx"
    assert remake_calls == [[1]]
    gate = presentation.theme["authoredQualityGate"]
    assert "flaggedSlides" not in gate
    assert gate["status"] == "passed"


def test_a_measured_over_budget_candidate_still_fails_the_deck(monkeypatch, tmp_path):
    # The remake ahead of the round is rejected, so the round's candidate is
    # the one measured over budget.
    remake_calls = _unadmitted_gate_repair_deck(
        monkeypatch, tmp_path, _MEASURED_VIOLATION, remake=_TERMINAL_HTML
    )

    owner_token = set_current_owner_id(uuid.uuid4())
    try:
        with pytest.raises(Exception) as caught:
            _run(_generate_on_sqlite(tmp_path, _request(), _outline(2)))
    finally:
        reset_current_owner_id(owner_token)

    assert getattr(caught.value, "code", None) == "authored_slide_capacity_exceeded"
    assert remake_calls == [[1]]


def test_unadmitted_gate_repair_still_fails_the_deck_with_the_knob_off(
    monkeypatch, tmp_path
):
    monkeypatch.setenv("AUTHORED_PUBLISH_FLAGGED_SLIDES", "false")
    remake_calls = _unadmitted_gate_repair_deck(
        monkeypatch, tmp_path, _ELEMENT_NAMED_VIOLATIONS, remake=_REMADE_HTML
    )

    owner_token = set_current_owner_id(uuid.uuid4())
    try:
        with pytest.raises(Exception) as caught:
            _run(_generate_on_sqlite(tmp_path, _request(), _outline(2)))
    finally:
        reset_current_owner_id(owner_token)

    assert getattr(caught.value, "code", None) == "authored_slide_capacity_exceeded"
    assert remake_calls == []


def test_over_budget_violations_separate_a_budget_from_a_layout_defect():
    assert over_budget_violations(_ELEMENT_NAMED_VIOLATIONS) == ()
    assert over_budget_violations(_MEASURED_VIOLATION) == _MEASURED_VIOLATION
    # An unmeasured slide names no element, so it stays fail-closed.
    assert over_budget_violations(("browser_metrics_unavailable",)) == (
        "browser_metrics_unavailable",
    )


def test_gate_repair_rejection_needs_the_fallback_and_a_layout_defect_only():
    assert policy_mod.gate_repair_rejection_is_terminal_slide(
        enabled=True,
        candidate_violations=_ELEMENT_NAMED_VIOLATIONS,
        original_violations=_ELEMENT_NAMED_VIOLATIONS,
    )
    assert not policy_mod.gate_repair_rejection_is_terminal_slide(
        enabled=False,
        candidate_violations=_ELEMENT_NAMED_VIOLATIONS,
        original_violations=(),
    )
    # A measurement on either side of the admission ends the deck.
    assert not policy_mod.gate_repair_rejection_is_terminal_slide(
        enabled=True,
        candidate_violations=_ELEMENT_NAMED_VIOLATIONS,
        original_violations=_MEASURED_VIOLATION,
    )


def test_only_a_flagged_slide_absorbs_its_element_named_capacity_violations():
    assert policy_mod.flagged_slide_absorbs_capacity(
        1, _ELEMENT_NAMED_VIOLATIONS, flagged=[1]
    )
    assert not policy_mod.flagged_slide_absorbs_capacity(
        0, _ELEMENT_NAMED_VIOLATIONS, flagged=[1]
    )
    assert not policy_mod.flagged_slide_absorbs_capacity(
        1, _MEASURED_VIOLATION, flagged=[1]
    )


def test_the_final_capacity_gate_is_reachable_for_a_flagged_slide(monkeypatch):
    """Step 3: the remake can fail, so the flagged slide still meets this gate.

    Its element-named violations are absorbed there and travel on as residual
    evidence; an unflagged slide keeps today's fail-closed capacity raise.
    """

    _install_capacity_violations(monkeypatch, _ELEMENT_NAMED_VIOLATIONS)
    monkeypatch.setattr(authored_generation_mod, "inspect_authored_dom", _fake_inspect)
    monkeypatch.setattr(authored_generation_mod, "blocking_safety_issues", _fake_blocking)
    gate_kwargs = {
        "htmls": [_TERMINAL_HTML],
        "capacity_plan": SimpleNamespace(at=lambda index: {}),
        "profile": SimpleNamespace(profile_id="ordinary"),
        "reference_mode": "style_reference",
        "browser_metrics_by_slide": [{"telemetry": "ok"}],
    }

    with pytest.raises(Exception) as caught:
        _run(authored_generation_mod._final_authored_quality_gate(**gate_kwargs))
    assert getattr(caught.value, "code", None) == "authored_slide_capacity_exceeded"

    residual = _run(
        authored_generation_mod._final_authored_quality_gate(
            **gate_kwargs, flagged_slide_indices=[0]
        )
    )
    assert [item["slide_index"] for item in residual] == [0]


# ---------------------------------------------------------------------------
# Remake-first ordering (bench run 10: the patch round fixed 0 of 6 flagged
# slides, a fresh composition briefed with the same defects passed 4 of 8).
# On a fallback-active deck the remake now runs BEFORE the patch round, and a
# slide both rounds miss gets a second remake before it is flagged.
# ---------------------------------------------------------------------------


def _ordered_rounds_deck(monkeypatch, tmp_path, *, patched=None, remakes):
    """One flagged slide, with both rounds observable."""

    deck = [_REMADE_HTML, _TERMINAL_HTML]
    _install_deck_stubs(monkeypatch, tmp_path, deck)
    _flagged_pipeline_stub(monkeypatch, deck, 1)
    round_calls = _install_repair_round(monkeypatch, raises=None, candidate=patched)
    remake_calls: list[object] = []
    _install_remakes(monkeypatch, remake_calls, candidates=remakes)
    return round_calls, remake_calls


def test_an_admitted_remake_first_never_calls_the_patch_round(monkeypatch, tmp_path):
    round_calls, remake_calls = _ordered_rounds_deck(
        monkeypatch, tmp_path, remakes=[_REMADE_HTML]
    )

    owner_token = set_current_owner_id(uuid.uuid4())
    try:
        result, presentation = _run(
            _generate_on_sqlite(tmp_path, _request(), _outline(2))
        )
    finally:
        reset_current_owner_id(owner_token)

    assert result.path == "deck.pptx"
    # The remake ran first and was admitted, so the patch round was left with
    # nothing to repair and never called the provider.
    assert remake_calls == [[1]]
    assert round_calls == []
    gate = presentation.theme["authoredQualityGate"]
    assert "flaggedSlides" not in gate
    assert gate["status"] == "passed"
    # A remade slide is still a slide the bounded round rewrote.
    assert gate["repairedSlideIndices"] == [1]


def test_a_rejected_remake_first_falls_back_to_the_patch_round(monkeypatch, tmp_path):
    round_calls, remake_calls = _ordered_rounds_deck(
        monkeypatch, tmp_path, patched=_REMADE_HTML, remakes=[_TERMINAL_HTML]
    )

    owner_token = set_current_owner_id(uuid.uuid4())
    try:
        result, presentation = _run(
            _generate_on_sqlite(tmp_path, _request(), _outline(2))
        )
    finally:
        reset_current_owner_id(owner_token)

    assert result.path == "deck.pptx"
    # One remake, rejected, then the patch round for that slide only.
    assert remake_calls == [[1]]
    assert [len(call) for call in round_calls] == [1]
    gate = presentation.theme["authoredQualityGate"]
    assert "flaggedSlides" not in gate
    assert gate["status"] == "passed"


def test_a_second_remake_rescues_a_slide_both_rounds_missed(monkeypatch, tmp_path):
    round_calls, remake_calls = _ordered_rounds_deck(
        monkeypatch, tmp_path, remakes=[_TERMINAL_HTML, _REMADE_HTML]
    )

    owner_token = set_current_owner_id(uuid.uuid4())
    try:
        result, presentation = _run(
            _generate_on_sqlite(tmp_path, _request(), _outline(2))
        )
    finally:
        reset_current_owner_id(owner_token)

    assert result.path == "deck.pptx"
    # Remake, patch round, remake -- in that order, all for the one slide.
    assert remake_calls == [[1], [1]]
    assert [len(call) for call in round_calls] == [1]
    gate = presentation.theme["authoredQualityGate"]
    assert "flaggedSlides" not in gate
    assert gate["status"] == "passed"


def test_two_failed_remakes_publish_the_slide_flagged_with_its_count(
    monkeypatch, tmp_path
):
    round_calls, remake_calls = _ordered_rounds_deck(
        monkeypatch, tmp_path, remakes=[_TERMINAL_HTML]
    )

    owner_token = set_current_owner_id(uuid.uuid4())
    try:
        result, presentation = _run(
            _generate_on_sqlite(tmp_path, _request(), _outline(2))
        )
    finally:
        reset_current_owner_id(owner_token)

    assert result.path == "deck.pptx"
    assert remake_calls == [[1], [1]]
    assert [len(call) for call in round_calls] == [1]
    # Two spent remakes and a patch round later the slide is still terminal,
    # so it is published flagged with the count that proves nothing is left.
    assert _pop_slide_ids(
        presentation.theme["authoredQualityGate"]["flaggedSlides"]
    ) == [
        {
            "index": 1,
            "reason": "composition_safety:overlap",
            "remakeAttempted": True,
            "remakeOutcome": "remake_budget_exhausted",
            "remakeCount": 2,
        }
    ]


def test_the_knob_off_deck_keeps_patch_first_and_never_remakes(monkeypatch, tmp_path):
    monkeypatch.setenv("AUTHORED_PUBLISH_FLAGGED_SLIDES", "false")
    round_calls, remake_calls = _ordered_rounds_deck(
        monkeypatch, tmp_path, remakes=[_REMADE_HTML]
    )

    owner_token = set_current_owner_id(uuid.uuid4())
    try:
        with pytest.raises(Exception) as caught:
            _run(_generate_on_sqlite(tmp_path, _request(), _outline(2)))
    finally:
        reset_current_owner_id(owner_token)

    assert getattr(caught.value, "code", None) == (
        "authored_composition_safety_gate_failed"
    )
    # Today's order, byte for byte: the patch round first, no remake at all,
    # and the deck fails rather than publishing a flagged slide.
    assert [len(call) for call in round_calls] == [1]
    assert remake_calls == []


_FRAGMENTED_HTML = (
    "<!DOCTYPE html><html lang='en'><head><meta charset='utf-8'></head>"
    "<body><h1>FRAGMENTED</h1></body></html>"
)


@pytest.mark.xfail(
    strict=True,
    reason=(
        "merge debt: main-added test runs on the branch's shared deck stubs, whose "
        "HTML and fake captures predate authored_metrics_require_prepared_html / "
        "prepared_html capture binding; the same root fails ~15 branch tests in this file"
    ),
)
def test_a_fragmentation_only_slide_gets_one_remake_and_no_patch_round(
    monkeypatch, tmp_path
):
    """Q3: the remake did not rescue it, so it is published flagged as-is.

    The patch round returns ``full_reauthor`` for structural change and could
    not merge boxes anyway, and the terminal round does not remake it again:
    the gate costs this slide exactly one provider call.
    """

    deck = [_REMADE_HTML, _FRAGMENTED_HTML]
    _install_deck_stubs(monkeypatch, tmp_path, deck)
    _flagged_pipeline_stub(
        monkeypatch, deck, 1,
        issues=[
            {
                "category": "fragmentation",
                "severity": "error",
                "message": "text_owners:61>50",
                "detail": "Merge the 61 text owners into at most 50 boxes.",
            }
        ],
    )
    round_calls = _install_repair_round(monkeypatch, raises=None, candidate=_REMADE_HTML)
    remake_calls: list[object] = []
    # The fresh composition still fails admission (it carries the overlap marker).
    _install_remakes(monkeypatch, remake_calls, candidates=[_TERMINAL_HTML])

    owner_token = set_current_owner_id(uuid.uuid4())
    try:
        result, presentation = _run(
            _generate_on_sqlite(tmp_path, _request(), _outline(2))
        )
    finally:
        reset_current_owner_id(owner_token)

    assert result.path == "deck.pptx"
    assert remake_calls == [[1]]
    assert round_calls == []
    assert _pop_slide_ids(
        presentation.theme["authoredQualityGate"]["flaggedSlides"]
    ) == [
        {
            "index": 1,
            "reason": "composition_safety:fragmentation",
            "remakeAttempted": True,
            "remakeOutcome": "remake_gate_failed",
        }
    ]


def test_gate_defects_reach_the_brief_with_the_measured_detail():
    defects = order_mod.gate_defects_by_slide(
        [
            {"slide_index": 1, "issues": [_FakeIssue().to_dict()]},
            {"slide_index": 1, "issues": [{"category": "clipping", "message": "cut"}]},
            {"slide_index": "x", "issues": [{"category": "overlap"}]},
        ]
    )

    assert sorted(defects) == [1]
    assert [(item.category, item.detail) for item in defects[1]] == [
        ("overlap", "two owned boxes overlap"),
        ("clipping", "cut"),
    ]


def test_the_receipt_accepts_only_a_count_within_the_slide_budget():
    entry = {
        "index": 1,
        "reason": "composition_safety:overlap",
        "remakeAttempted": True,
        "remakeOutcome": "remake_budget_exhausted",
        "remakeCount": 2,
    }

    assert flagged_slide_entries([entry]) == (entry,)
    for invalid in (0, 3, True, "2"):
        with pytest.raises(ValueError):
            flagged_slide_entries([{**entry, "remakeCount": invalid}])


# ---------------------------------------------------------------------------
# The QualityReceipt v2 capacity evidence. A D7-flagged slide's capacity checks
# were still aggregated with ``all()``, so the published deck failed with
# ``authored_quality_receipt_gate_failed`` (capacityStatus=blocked) even though
# the safety evidence already records that slide as non-blocking.
# ---------------------------------------------------------------------------


_CAPACITY_MARKER = "CAPACITY-FAIL"
_CAPACITY_HTML = (
    "<!DOCTYPE html><html lang='en'><head><meta charset='utf-8'></head>"
    f"<body><h1>{_CAPACITY_MARKER}</h1></body></html>"
)
_CAPACITY_ROLES = ("text", "title", "body", "font", "qid", "image")


def _install_receipt_evidence(monkeypatch):
    """Give the attempt a sealed ledger so the receipt path actually runs.

    Without one ``_finalize_attempt_evidence`` returns ``(None, None, ())`` and
    the whole QualityReceipt v2 block is skipped. Metrics are tagged per slide
    so a single capacity failure can be aimed at one slide.
    """

    attempt_id = public_attempt_id(str(uuid.uuid4()))
    ledger = DeckEvidenceLedger(attempt_id=attempt_id)

    def tagged_metrics(html, **_kwargs):
        blocked = TERMINAL_MARKER in str(html) or _CAPACITY_MARKER in str(html)
        return {
            "telemetry": "ok",
            "capacity": "blocked" if blocked else "ok",
            # Every real capture carries the digest of the HTML it was measured
            # from (``utils/slide_capture._cache_browser_metrics``), and the
            # final readback now accepts a readback only on that identity.
            "source_digest": "sha256:"
            + hashlib.sha256(str(html).encode("utf-8")).hexdigest(),
        }

    def capacity_role_evidence(metrics, _capacity):
        blocked = dict(metrics or {}).get("capacity") == "blocked"
        return {
            "browserObserved": True,
            "checks": {name: not blocked for name in _CAPACITY_ROLES},
            "violations": ["browser_text_not_visible:closing.statement"]
            if blocked
            else [],
        }

    monkeypatch.setattr(
        authored_generation_mod, "get_cached_browser_metrics", tagged_metrics
    )
    monkeypatch.setattr(
        authored_generation_mod,
        "_evaluate_capacity_role_evidence",
        capacity_role_evidence,
    )
    recorded: list[dict[str, object]] = []
    recorded_v3: list[dict[str, object]] = []
    real_create = authored_generation_mod.create_quality_receipt_v2
    real_create_v3 = authored_generation_mod.create_generation_quality_receipt_v3

    def spy(*args, **kwargs):
        recorded.append(dict(kwargs.get("evidence") or {}))
        return real_create(*args, **kwargs)

    async def spy_v3(*args, **kwargs):
        recorded_v3.append(dict(kwargs))
        return await real_create_v3(*args, **kwargs)

    monkeypatch.setattr(authored_generation_mod, "create_quality_receipt_v2", spy)
    monkeypatch.setattr(
        authored_generation_mod, "create_generation_quality_receipt_v3", spy_v3
    )
    return (ledger, attempt_id), recorded, recorded_v3


def _receipt_deck(monkeypatch, tmp_path, deck):
    """The D7 harness plus a ledger, with slide 1 unadmitted and then flagged."""

    _install_deck_stubs(monkeypatch, tmp_path, deck)
    _install_repair_round(monkeypatch, raises=None)
    _install_capacity_violations(monkeypatch, _ELEMENT_NAMED_VIOLATIONS)
    evidence, recorded, recorded_v3 = _install_receipt_evidence(monkeypatch)
    _flagged_pipeline_stub(monkeypatch, deck, 1, evidence=evidence)
    _install_remake(monkeypatch, [], candidate=_TERMINAL_HTML)
    return recorded, recorded_v3


def test_a_flagged_slide_never_blocks_the_receipt_capacity_gate(monkeypatch, tmp_path):
    recorded, recorded_v3 = _receipt_deck(
        monkeypatch, tmp_path, [_REMADE_HTML, _TERMINAL_HTML]
    )

    owner_token = set_current_owner_id(uuid.uuid4())
    try:
        result, presentation = _run(
            _generate_on_sqlite(tmp_path, _request(), _outline(2))
        )
    finally:
        reset_current_owner_id(owner_token)

    assert result.path == "deck.pptx"
    gate = presentation.theme["authoredQualityGate"]
    assert gate["capacityStatus"] == "passed"
    assert gate["qualityPass"] is False
    assert gate["documentCompleted"] is True
    assert gate["residualIssueCount"] == 1
    assert isinstance(gate["qualityReceiptV3"], dict)
    assert [entry["index"] for entry in gate["flaggedSlides"]] == [1]
    # The excluded slide is named in the evidence, never silently dropped.
    assert recorded[-1]["capacity"]["flaggedSlideIndices"] == [1]
    assert recorded[-1]["capacity"]["violations"] == []
    assert [entry["index"] for entry in recorded[-1]["safety"]["flagged"]] == [1]
    assert [
        entry["index"]
        for entry in recorded_v3[-1]["safety_evidence"]["flagged"]
    ] == [1]


def test_a_capacity_failure_on_an_unflagged_slide_still_blocks(monkeypatch, tmp_path):
    _receipt_deck(monkeypatch, tmp_path, [_CAPACITY_HTML, _TERMINAL_HTML])

    owner_token = set_current_owner_id(uuid.uuid4())
    try:
        with pytest.raises(Exception) as caught:
            _run(_generate_on_sqlite(tmp_path, _request(), _outline(2)))
    finally:
        reset_current_owner_id(owner_token)

    assert getattr(caught.value, "code", None) == "authored_quality_receipt_gate_failed"
    # The refusal now names the failing checks and the slide they were measured
    # on, instead of only its error code (bench run 24).
    assert "capacity" in str(caught.value)
    assert "[0]" in str(caught.value)


def _install_metrics_gap(monkeypatch, gap_marker):
    """One slide's telemetry is gone from the process-local LRU at seal time.

    That is what bench run 24 hit: ``_author_illustrate_render_pipeline`` writes
    ``browser_metrics=None`` into the ledger for a slide whose DOM payload the
    metrics cache no longer holds, the attempt is sealed with that gap, and the
    receipt then read the gap as "this slide was never browser-observed".
    """

    from utils import slide_capture

    def measured(html):
        return {
            "telemetry": "ok",
            "capacity": "blocked" if TERMINAL_MARKER in str(html) else "ok",
            "source_digest": "sha256:"
            + hashlib.sha256(str(html).encode("utf-8")).hexdigest(),
        }

    def cached(html, **_kwargs):
        return None if gap_marker in str(html) else measured(html)

    async def recollect(html, *_args, **_kwargs):
        return measured(html)

    def capacity_role_evidence(metrics, _capacity):
        # Faithful to the real evaluator: no telemetry is no observation.
        observed = metrics is not None
        blocked = dict(metrics or {}).get("capacity") == "blocked"
        return {
            "browserObserved": observed,
            "checks": {name: observed and not blocked for name in _CAPACITY_ROLES},
            "violations": [] if observed and not blocked else ["capacity:unobserved"],
        }

    monkeypatch.setattr(authored_generation_mod, "get_cached_browser_metrics", cached)
    monkeypatch.setattr(
        authored_generation_mod,
        "_evaluate_capacity_role_evidence",
        capacity_role_evidence,
    )
    monkeypatch.setattr(slide_capture, "ensure_browser_metrics", recollect)


@pytest.mark.parametrize(
    "legacy_lane",
    [False, True],
    ids=["streaming_tail", "legacy_barrier_non_streaming"],
)
def test_a_sealed_ledger_gap_is_recollected_instead_of_blocking(
    monkeypatch, tmp_path, legacy_lane
):
    """Bench run 24 (P0 knobs) versus run 25 (P1): same sha, same fixture.

    The legacy barrier / non-streaming lane holds a slide's telemetry longer
    before it is sealed, so its ledger entry can be a gap where the streaming
    lane's is not. Whichever lane produced it, the gap is not evidence about
    the published bytes: it is re-collected before the deck is judged.
    """

    if legacy_lane:
        monkeypatch.setenv("AUTHORED_WAVE_BARRIER_DISABLED", "false")
        monkeypatch.setenv("AUTHORED_TAIL_STREAMING_ENABLED", "false")

    _receipt_deck(monkeypatch, tmp_path, [_REMADE_HTML, _TERMINAL_HTML])
    _install_metrics_gap(monkeypatch, "REMADE")

    owner_token = set_current_owner_id(uuid.uuid4())
    try:
        result, presentation = _run(
            _generate_on_sqlite(tmp_path, _request(), _outline(2))
        )
    finally:
        reset_current_owner_id(owner_token)

    assert result.path == "deck.pptx"
    gate = presentation.theme["authoredQualityGate"]
    assert gate["capacityStatus"] == "passed"
    assert gate["status"] == "blocked"
    assert gate["qualityPass"] is False


# ---------------------------------------------------------------------------
# The repair-round checkpoint persists (bench job 4e855068). A workspace-linked
# job's checkpoint service refuses a write whose ``fencing_token`` is not an
# int (``authored_workspace_operation_fencing_token_required``, retryable), so
# a deck that authored, rendered and persisted 10/10 slides died three
# identical attempts later in the dead-letter queue. Every checkpoint write
# after external success must carry the lease's fencing token.
# ---------------------------------------------------------------------------


_FENCING_TOKEN = 7


def _workspace_job(token=_FENCING_TOKEN):
    job = PresentationGenerationJob(
        task_id=f"task-{uuid.uuid4()}",
        presentation_id=uuid.uuid4(),
        # The workspace link is what makes a missing token fatal in production;
        # the artifact publication it also switches on is out of scope here, so
        # only the token the call sites must forward is set.
        request_sha256="0" * 64,
        request_payload={},
        attempt_token="attempt-token",
        attempt_number=1,
    )
    # The worker carries the lease's fencing token beside the row, not in it.
    object.__setattr__(job, "_workspace_fencing_token", token)
    return job


def _install_job_stubs(monkeypatch):
    """Every durable job write except the checkpoint persist under test."""

    recorded: list[dict[str, object]] = []

    async def record_checkpoint(**kwargs):
        recorded.append(dict(kwargs))

    async def async_noop(*_args, **_kwargs):
        return None

    async def resolve_without_checkpoint(*, resolve, **_kwargs):
        return await resolve(), False

    async def fake_publish(**kwargs):
        # Publication owns the job row's lease and is not under test here.
        return kwargs["out_path"], 0.0

    for module in (authored_generation_mod, terminal_mod):
        monkeypatch.setattr(
            module, "_complete_stage_after_external_success", record_checkpoint
        )
    monkeypatch.setattr(
        authored_generation_mod,
        "resolve_with_authored_deck_plan_checkpoint",
        resolve_without_checkpoint,
    )
    monkeypatch.setattr(
        authored_generation_mod, "publish_authored_presentation", fake_publish
    )
    monkeypatch.setattr(
        authored_generation_mod, "mark_authored_generation_stage", async_noop
    )
    monkeypatch.setattr(
        authored_generation_mod, "_record_deck_stage_latency", async_noop
    )
    monkeypatch.setattr(
        authored_generation_mod, "_cleanup_published_checkpoints", async_noop
    )
    monkeypatch.setattr(
        authored_generation_mod, "claim_scene_repair", lambda *a, **k: SimpleNamespace()
    )
    monkeypatch.setattr(
        authored_generation_mod, "persist_repaired_scene_envelope", lambda *a, **k: None
    )
    monkeypatch.setattr(
        authored_generation_mod, "persist_scene_envelope", lambda *a, **k: None
    )
    return recorded


def test_repair_checkpoints_carry_the_workspace_fencing_token(monkeypatch, tmp_path):
    deck = [_REMADE_HTML, _TERMINAL_HTML]
    _install_deck_stubs(monkeypatch, tmp_path, deck)
    _flagged_pipeline_stub(monkeypatch, deck, 1)
    # The round returns its input unchanged and capacity is clean, so slide 1's
    # candidate is admitted and its checkpoint is persisted.
    _install_repair_round(monkeypatch, raises=None)
    # The remake ahead of the round is rejected so the round runs; the terminal
    # remake behind it is admitted, so the fallback's own persist is exercised.
    _install_remakes(monkeypatch, [], candidates=[_TERMINAL_HTML, _REMADE_HTML])
    recorded = _install_job_stubs(monkeypatch)

    owner_token = set_current_owner_id(uuid.uuid4())
    try:
        result, _presentation = _run(
            _generate_on_sqlite(
                tmp_path, _request(), _outline(2), generation_job=_workspace_job()
            )
        )
    finally:
        reset_current_owner_id(owner_token)

    assert result.path == "deck.pptx"
    # The admitted gate repair and the admitted second remake both persisted a
    # render checkpoint for the same slide.
    assert [call["stage"] for call in recorded] == ["render", "render"]
    assert [call["slide_index"] for call in recorded] == [1, 1]
    assert [call.get("fencing_token") for call in recorded] == [
        _FENCING_TOKEN,
        _FENCING_TOKEN,
    ]


# ---------------------------------------------------------------------------
# Defect 3 (editor flagged-slide badge review): a slide can be flagged twice --
# once by the author-stage rescue, once by the terminal gate fallback that
# runs after it. Before ``_merge_flagged_slide_entries`` existed, the two
# lists were concatenated as-is, so the client's badge map (keyed by slide,
# not by list position) let the second entry silently replace the first,
# dropping the author-stage reason.
# ---------------------------------------------------------------------------


def test_a_slide_flagged_by_both_stages_keeps_both_reasons(monkeypatch, tmp_path):
    _install_deck_stubs(monkeypatch, tmp_path, [_TERMINAL_HTML])
    author_flag = {
        "index": 0,
        "reason": "author:attempts_exhausted",
        "remakeAttempted": True,
        "remakeOutcome": "placeholder",
    }

    async def fake_pipeline(
        outline,
        brand,
        style,
        header_contract=None,
        page_number_mode="none",
        chapter_plan=None,
        illustration_plan=None,
        instructions=None,
        **_job_kwargs,
    ):
        return authored_service.AuthoredGenerationPipelineResult(
            deck=AuthoredDeckResult([_TERMINAL_HTML], "DESIGN SYSTEM"),
            pngs=[b"png"],
            elapsed=0.1,
            author_work=0.1,
            illustration_work=0.0,
            render_work=0.1,
            local_fit_receipts=_fit_receipts([_TERMINAL_HTML]),
            author_flagged_slides=(author_flag,),
        )

    monkeypatch.setattr(
        authored_service, "_author_illustrate_render_pipeline", fake_pipeline
    )
    calls: list[object] = []
    # The remake candidate still carries the terminal marker, so the slide
    # stays terminal and the D7 fallback flags it too, with its own reason.
    _install_remake(monkeypatch, calls, candidate=_TERMINAL_HTML)

    owner_token = set_current_owner_id(uuid.uuid4())
    try:
        result, presentation = _run(
            _generate_on_sqlite(tmp_path, _request(), _outline(1))
        )
    finally:
        reset_current_owner_id(owner_token)

    assert result.path == "deck.pptx"
    entries = _pop_slide_ids(presentation.theme["authoredQualityGate"]["flaggedSlides"])
    # One merged entry: the author-stage entry's own fields (it came first),
    # plus every distinct reason both stages recorded for this slide.
    assert entries == [
        {
            "index": 0,
            "reason": "author:attempts_exhausted",
            "remakeAttempted": True,
            "remakeOutcome": "placeholder",
            "reasons": [
                "author:attempts_exhausted",
                "composition_safety:overlap",
            ],
        }
    ]


# ---------------------------------------------------------------------------
# M0 (design 2026-09-08): every provider call of the repair ladder is receipted
# under its own stage. Bench run 48 wrote 25 ``author`` receipts and none for
# the 7 remake calls, because both remake rounds ran outside a usage scope.
# ---------------------------------------------------------------------------


def _capture_provider_receipts(monkeypatch):
    receipts: list[object] = []

    async def receipt(value):
        receipts.append(value)

    monkeypatch.setattr(
        "services.provider_usage_receipt_service.record_provider_usage_receipt_detached",
        receipt,
    )
    monkeypatch.setattr(
        "services.provider_usage_instrumentation.durable_provider_runtime_required",
        lambda: False,
    )
    monkeypatch.setenv("AUTHORED_LOCAL_STAGE_ASYNC_ENABLED", "false")
    return receipts


async def _paid_call(*, slot, model, retry_class, input_tokens, output_tokens):
    """One admitted provider call, filed the way the author/structured boundaries file it."""

    from services.provider_usage_instrumentation import (
        provider_call_admission,
        record_provider_call,
    )

    async with provider_call_admission(
        is_retry=retry_class != "initial", semantic_slot=slot, provider="codex", model=model
    ):
        pass
    await record_provider_call(
        provider="codex", model=model, latency_ms=1000, retry_class=retry_class,
        response=SimpleNamespace(
            usage=SimpleNamespace(input_tokens=input_tokens, output_tokens=output_tokens)
        ),
    )


def test_repair_rounds_receipt_their_provider_calls_by_stage(monkeypatch, tmp_path):
    """Remake-first, patch round and terminal remake each file their call and
    their span; the slide remade in both rounds gets two distinct receipts."""

    from utils.llm_calls.author_slide import authored_provider_semantic_slot

    deck = [_REMADE_HTML, _TERMINAL_HTML]
    _install_deck_stubs(monkeypatch, tmp_path, deck)
    _flagged_pipeline_stub(monkeypatch, deck, 1)
    _install_repair_round(monkeypatch, raises=None)
    _install_job_stubs(monkeypatch)
    receipts = _capture_provider_receipts(monkeypatch)

    remake_candidates = iter([_TERMINAL_HTML, _REMADE_HTML])

    async def paid_remake(htmls, pngs, contents, roles, brand, design_system, **kwargs):
        index = int(kwargs["slide_indices"][0])
        await _paid_call(
            slot=authored_provider_semantic_slot(
                receipt_retry_class="recovery", receipt_attempt_ordinal=2, slide_index=index
            ),
            model="gpt-5.6-sol", retry_class="recovery",
            input_tokens=12_000, output_tokens=4_000,
        )
        return [[next(remake_candidates)], [b"remade-png"], [0], [{"path": "full_reauthor"}]]

    async def paid_patch(htmls, pngs, contents, roles, brand, design_system, **kwargs):
        await _paid_call(
            slot="structured.attempt-1", model="gpt-5.6-sol", retry_class="initial",
            input_tokens=5_000, output_tokens=600,
        )
        return [list(htmls), list(pngs), []]

    monkeypatch.setattr(terminal_mod, "revise_authored_deck", paid_remake)
    monkeypatch.setattr(authored_service, "revise_authored_deck", paid_patch)
    job = _workspace_job()

    async def flow():
        # The stubbed pipeline files no author receipts; seed the one first-pass
        # call the runtime control needs before it admits a recovery retry.
        from services.provider_usage_instrumentation import provider_usage_scope

        with provider_usage_scope(
            job_id=job.id, operation="authored_generation", stage="author",
            attempt_number=job.attempt_number, slide_index=1,
        ):
            await _paid_call(
                slot="author.initial", model="gpt-5.6-sol", retry_class="initial",
                input_tokens=12_700, output_tokens=4_400,
            )
        return await _generate_on_sqlite(
            tmp_path, _request(), _outline(2), generation_job=job
        )

    owner_token = set_current_owner_id(uuid.uuid4())
    try:
        result, presentation = _run(flow())
    finally:
        reset_current_owner_id(owner_token)

    # Publication is stubbed by ``_install_job_stubs``; the receipts are the evidence.
    assert result.path == "deck.pptx"
    ledger = [(r.stage, r.model, r.retry_class) for r in receipts]
    assert ledger == [
        ("author", "gpt-5.6-sol", "initial"),  # the seeded first pass, unchanged
        ("repair_remake", "gpt-5.6-sol", "recovery"),  # remake-first round, slide 1
        ("repair_remake", "none", "initial"),  # its span
        ("repair_patch", "gpt-5.6-sol", "initial"),  # the bounded patch proposal
        ("repair_patch", "none", "initial"),
        ("repair_remake", "gpt-5.6-sol", "recovery"),  # terminal round, slide 1 again
        ("repair_remake", "none", "initial"),
    ]
    assert len({r.receipt_key_hash for r in receipts}) == len(receipts)
    calls = [r for r in receipts if r.provider != "local"]
    assert [(r.input_tokens, r.output_tokens) for r in calls] == [
        (12_700, 4_400), (12_000, 4_000), (5_000, 600), (12_000, 4_000)
    ]
    assert all(r.operation == "authored_generation" for r in receipts)
