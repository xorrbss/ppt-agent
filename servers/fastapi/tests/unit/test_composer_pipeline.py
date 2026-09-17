"""Composer pipeline: admission flag, deck loop with fakes, assertions, receipt, publisher."""

from __future__ import annotations

import asyncio
import base64
import html as html_module
import json
import uuid
from types import SimpleNamespace
from typing import Any

import pytest

from models.generate_presentation_request import GeneratePresentationRequest
from services.composer.assets import in_memory_asset_resolver
from services.composer.contracts import Measurement
from services.composer.pipeline import ComposerAssertionError, assert_slide_bindings, run_composer_restyle
from services.composer.pipeline_inputs import build_deck_context, deck_title, deck_title_px
from services.composer.plan import (
    TITLE_FLOOR_PX,
    ComposerComponent,
    ComposerSlidePlan,
    ComposerSlideResult,
    ComposerTokens,
    FitParameters,
    FitReceipt,
    Paragraph,
    SourceRunRef,
    TextRun,
)
from services.composer.publish_semantic import semantic_draft_from_html
from services.composer.receipt import build_composer_receipt, composer_receipt_path, is_accepted_residual
from services.source_content import pipeline as source_pipeline
from services.source_content.source_stamp_dom import deterministic_source_qid
from utils.get_env import get_composer_restyle_enabled
from utils.runtime_settings import RuntimeSettings

#: A real, minimally valid 1x1 transparent PNG -- ``admit_authored_html``
#: decodes every image it admits, so a fake byte string will not do.
_PNG_1X1 = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY"
    "42YAAAAASUVORK5CYII="
)

PAGE_EMU = {"cx": 12192000, "cy": 6858000}
PAGES: list[list[tuple[str, str, str]]] = [
    [("u1:text:1", "분기 실적 요약", "title"), ("u1:text:2", "매출 120억 원 달성", "body")],
    [("u2:text:1", "고객 수 증가 추세", "body")],
    [("u3:text:1", "다음 분기 계획", "title"), ("u3:text:2", "신규 시장 진출", "body")],
]


def _manifest() -> dict[str, Any]:
    units = []
    for index, page in enumerate(PAGES):
        blocks = [
            {
                "sourceBlockId": block_id,
                "kind": "text",
                "semanticRole": role,
                "order": order,
                "payload": {"text": text, "runs": [text], "paragraphs": [{"runs": [text], "text": text}]},
                "contentDigest": "sha256:" + "0" * 64,
            }
            for order, (block_id, text, role) in enumerate(page)
        ]
        units.append({"sourceUnitId": f"u{index + 1}", "sourceIndex": index, "blocks": blocks})
    return {"manifestId": "manifest-1", "units": units, "slideSize": dict(PAGE_EMU)}


def _outline() -> dict[str, Any]:
    slides = [
        {"sourceUnitId": f"u{index + 1}", "sourceIndex": index, "readingOrder": [block_id for block_id, _, _ in page]}
        for index, page in enumerate(PAGES)
    ]
    return {"sourceSlideCount": len(slides), "slides": slides}


def _style() -> SimpleNamespace:
    return SimpleNamespace(
        id="style-a",
        preview_palette=["#FFFFFF", "#123456"],
        background_color="#FFFFFF",
        preview_bg="#FFFFFF",
        primary_color="#2563EB",
        preview_accent="#2563EB",
        heading_font="Noto Sans KR",
        body_font="Noto Sans KR",
    )


def _request() -> SimpleNamespace:
    return SimpleNamespace(
        _source_content_manifest=_manifest(),
        _source_document_outline=_outline(),
        _authored_style_snapshot=_style(),
        language="ko",
        authored_style="style-a",
    )


def fake_classifier(*, unit: dict[str, Any], slide_index: int, deck_context: dict[str, Any]) -> ComposerSlidePlan:
    assert deck_context["page_count"] == len(PAGES)
    assert deck_context["source_page_emu"] == (PAGE_EMU["cx"], PAGE_EMU["cy"])
    components = []
    for block in unit["blocks"]:
        block_id = block["sourceBlockId"]
        text = block["payload"]["text"]
        components.append(
            ComposerComponent(
                component_id=block_id,
                kind="paragraph",
                semantic_role=block["semanticRole"],
                paragraphs=[
                    Paragraph(
                        text=text,
                        runs=[TextRun(text=text)],
                        source=SourceRunRef(block_id=block_id, binding_id=block_id),
                    )
                ],
            )
        )
    return ComposerSlidePlan(
        slide_index=slide_index, source_unit_id=unit["sourceUnitId"], archetype="text", components=components
    )


def _render(plan: ComposerSlidePlan, *, drop: str | None = None) -> str:
    parts = []
    for component in plan.components:
        for paragraph in component.paragraphs:
            binding = paragraph.source.binding_id
            if binding == drop:
                continue
            parts.append(
                f'<p data-source-block-id="{binding}" data-qid="{deterministic_source_qid(binding)}"'
                f' data-semantic-role="{component.semantic_role}">{html_module.escape(paragraph.text)}</p>'
            )
    body = "".join(parts)
    return f'<html><body style="width:1280px;height:720px;margin:0;overflow:hidden">{body}</body></html>'


def fake_renderer(plan, tokens, params, deck) -> str:
    return _render(plan)


def dropping_renderer(plan, tokens, params, deck) -> str:
    return _render(plan, drop="u2:text:1" if plan.slide_index == 1 else None)


def image_marker_renderer(plan, tokens, params, deck) -> str:
    """The authoring-only marker ``render_slide`` leaves unresolved, on every slide."""

    html = _render(plan)
    marker = '<img data-asset-id="asset-1" data-qid="img-q1" data-semantic-role="image" alt="">'
    return html.replace("</body>", marker + "</body>")


def fake_estimator(plan, tokens, params, deck) -> bool:
    return True


# --- pipeline_inputs: deck_title_px --------------------------------------------------------


def _tokens() -> ComposerTokens:
    return ComposerTokens(
        background="#FFFFFF", surface="#F4F6F8", text_primary="#111827", text_secondary="#4B5563",
        accent="#0F62FE", accent_text="#0F62FE", accent_soft="#D0E2FF", rule="#E5E7EB",
    )


def _title_component(text: str) -> ComposerComponent:
    return ComposerComponent(
        component_id="title",
        kind="title",
        semantic_role="title",
        paragraphs=[
            Paragraph(
                text=text, runs=[TextRun(text=text)],
                source=SourceRunRef(block_id="t0", binding_id="t0"),
            )
        ],
    )


def _titled_plan(text: str, *, slide_index: int = 0, archetype: str = "text") -> ComposerSlidePlan:
    return ComposerSlidePlan(
        slide_index=slide_index, source_unit_id=f"unit-{slide_index}", archetype=archetype,
        title=_title_component(text),
    )


def test_deck_title_px_keeps_the_template_size_when_the_longest_title_fits() -> None:
    plans = [_titled_plan("2026 사업 계획"), _titled_plan("다음 분기 계획", slide_index=1)]
    assert deck_title_px(plans, _tokens()) == 40.0


def test_deck_title_px_steps_down_for_a_long_title() -> None:
    plans = [_titled_plan("2026 사업 계획"), _titled_plan("가" * 60, slide_index=1)]
    size = deck_title_px(plans, _tokens())
    assert size is not None and TITLE_FLOOR_PX <= size < 40.0


def test_deck_title_px_never_goes_below_the_floor() -> None:
    plans = [_titled_plan("가" * 200)]
    assert deck_title_px(plans, _tokens()) == TITLE_FLOOR_PX


def test_deck_title_px_ignores_cover_slides_and_returns_none_without_a_title() -> None:
    cover_only = [_titled_plan("가" * 200, archetype="cover")]
    assert deck_title_px(cover_only, _tokens()) is None
    untitled = [ComposerSlidePlan(slide_index=0, source_unit_id="unit-0", archetype="text")]
    assert deck_title_px(untitled, _tokens()) is None


# --- pipeline_inputs: deck_title -----------------------------------------------------------


def _cover_unit(blocks: list[dict[str, Any]]) -> dict[str, Any]:
    return {"sourceUnitId": "u1", "sourceIndex": 0, "blocks": blocks}


def _text_block(block_id: str, text: str, role: str, order: int) -> dict[str, Any]:
    return {
        "sourceBlockId": block_id,
        "kind": "text",
        "semanticRole": role,
        "order": order,
        "payload": {"text": text},
        "contentDigest": "sha256:" + "0" * 64,
    }


def test_deck_title_returns_the_cover_page_title_block_verbatim() -> None:
    units = [
        _cover_unit(
            [
                _text_block("u1:text:1", "2026 Growth Strategy", "title", 0),
                _text_block("u1:text:2", "Confidential", "body", 1),
            ]
        )
    ]
    assert deck_title(units) == "2026 Growth Strategy"


def test_deck_title_collapses_whitespace_and_caps_length() -> None:
    padded = "  Quarterly   Report  \n\nfor   Investors  "
    units = [_cover_unit([_text_block("u1:text:1", padded, "title", 0)])]
    assert deck_title(units) == "Quarterly Report for Investors"

    overlong = "가" * 400
    units = [_cover_unit([_text_block("u1:text:1", overlong, "title", 0)])]
    result = deck_title(units)
    assert result == "가" * 200
    assert len(result) == 200


def test_deck_title_falls_back_to_untitled_when_the_cover_page_has_no_title_role() -> None:
    # A cover page that classified only body/subtitle text -- no source
    # document title survives to be resolved from the manifest alone.
    units = [
        _cover_unit(
            [
                _text_block("u1:text:1", "Prepared by the finance team", "subtitle", 0),
                _text_block("u1:text:2", "Internal use only", "body", 1),
            ]
        )
    ]
    assert deck_title(units) == "Untitled Presentation"


def test_deck_title_falls_back_to_untitled_when_there_are_no_units() -> None:
    assert deck_title([]) == "Untitled Presentation"


def test_deck_title_falls_back_to_document_title_when_no_cover_title_role() -> None:
    units = [_cover_unit([_text_block("u1:text:1", "Prepared by the finance team", "subtitle", 0)])]
    assert deck_title(units, document_title="  Quarterly   Deck  ") == "Quarterly Deck"


def test_deck_title_falls_back_to_file_name_title_when_no_cover_or_document_title() -> None:
    units = [_cover_unit([_text_block("u1:text:1", "Prepared by the finance team", "subtitle", 0)])]
    assert deck_title(units, file_name_title="Q3 Board Deck") == "Q3 Board Deck"


def test_deck_title_document_title_outranks_file_name_title() -> None:
    units = [_cover_unit([_text_block("u1:text:1", "Prepared by the finance team", "subtitle", 0)])]
    assert (
        deck_title(units, document_title="Doc Title", file_name_title="File Title")
        == "Doc Title"
    )


def test_deck_title_cover_page_title_outranks_document_and_file_name_title() -> None:
    units = [_cover_unit([_text_block("u1:text:1", "Cover Title", "title", 0)])]
    assert (
        deck_title(units, document_title="Doc Title", file_name_title="File Title")
        == "Cover Title"
    )


def test_deck_title_treats_a_blank_document_title_as_absent() -> None:
    units = [_cover_unit([_text_block("u1:text:1", "Prepared by the finance team", "subtitle", 0)])]
    assert deck_title(units, document_title="   ", file_name_title="File Title") == "File Title"


def test_build_deck_context_reads_document_and_file_name_title_from_the_manifest() -> None:
    units = [_cover_unit([_text_block("u1:text:1", "Prepared by the finance team", "subtitle", 0)])]
    with_document_title = {
        "manifestId": "manifest-1",
        "units": units,
        "documentTitle": "Quarterly Deck",
        "sourceFileTitle": "Q3 Board Deck",
    }
    assert build_deck_context(with_document_title, units).deck_title == "Quarterly Deck"

    without_document_title = {
        "manifestId": "manifest-1",
        "units": units,
        "sourceFileTitle": "Q3 Board Deck",
    }
    assert build_deck_context(without_document_title, units).deck_title == "Q3 Board Deck"


class CountingMeasurer:
    def __init__(self) -> None:
        self.in_flight = 0
        self.max_in_flight = 0
        self.calls = 0

    async def __call__(self, html: str, plan: ComposerSlidePlan) -> Measurement:
        self.in_flight += 1
        self.max_in_flight = max(self.max_in_flight, self.in_flight)
        self.calls += 1
        await asyncio.sleep(0.01)
        self.in_flight -= 1
        return Measurement(metrics={"width": 1280}, png=b"\x89PNG" + html.encode("utf-8")[:8])


class RecordingHTMLMeasurer:
    """Records the exact HTML each call receives, for admission assertions."""

    def __init__(self) -> None:
        self.seen_html: list[str] = []

    async def __call__(self, html: str, plan: ComposerSlidePlan) -> Measurement:
        self.seen_html.append(html)
        return Measurement(metrics={"width": 1280}, png=b"\x89PNG" + html.encode("utf-8")[:8])


class RecordingPublisher:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def __call__(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(kwargs)
        return {
            "revision": 1,
            "manifestDigest": "sha256:" + "a" * 64,
            "bundleCount": len(kwargs["htmls"]),
            "generationRoute": "composer",
            "editPath": f"/presentation?id={kwargs['presentation_id']}",
        }


async def _run(
    monkeypatch, tmp_path, *, renderer, publisher, measurer, concurrency: str, reporter=None,
    extend_lease=None,
) -> Any:
    monkeypatch.setenv("AUTHORED_QUALITY_RECEIPT_DIRECTORY", str(tmp_path))
    monkeypatch.setenv("PUBLIC_RENDER_MAX_CONCURRENCY", concurrency)
    progress: list[dict[str, Any]] = []

    async def default_reporter(**update: Any) -> bool:
        progress.append(update)
        return True

    presentation_id = uuid.uuid4()
    job = SimpleNamespace(id=uuid.uuid4(), workspace_operation_id=None, attempt_token=None)
    result = await run_composer_restyle(
        request=_request(),
        presentation_id=presentation_id,
        generation_job=job,
        progress_reporter=reporter or default_reporter,
        sql_session=object(),
        async_status=None,
        measurer=measurer,
        classifier=fake_classifier,
        renderer=renderer,
        estimator=fake_estimator,
        publisher=publisher,
        extend_lease=extend_lease,
    )
    return result, presentation_id, job, progress


def test_get_composer_restyle_enabled_default_false_and_truthy() -> None:
    assert get_composer_restyle_enabled(RuntimeSettings.from_sources(environ={})) is False
    enabled = RuntimeSettings.from_sources(environ={"COMPOSER_RESTYLE_ENABLED": "true"})
    assert get_composer_restyle_enabled(enabled) is True
    disabled = RuntimeSettings.from_sources(environ={"COMPOSER_RESTYLE_ENABLED": "0"})
    assert get_composer_restyle_enabled(disabled) is False


def test_run_composer_restyle_publishes_every_page_and_writes_receipt(monkeypatch, tmp_path) -> None:
    measurer = CountingMeasurer()
    publisher = RecordingPublisher()
    result, presentation_id, job, progress = asyncio.run(
        _run(monkeypatch, tmp_path, renderer=fake_renderer, publisher=publisher, measurer=measurer, concurrency="2")
    )

    assert result.presentation_id == presentation_id
    assert result.edit_path == f"/presentation?id={presentation_id}"
    assert len(publisher.calls) == 1
    call = publisher.calls[0]
    assert len(call["htmls"]) == len(PAGES) == len(call["pngs"]) == len(call["results"])
    assert [item.slide_index for item in call["results"]] == [0, 1, 2]
    assert [plan.source_unit_id for plan in call["deck"].slides] == ["u1", "u2", "u3"]
    assert call["deck"].source_page_emu == (PAGE_EMU["cx"], PAGE_EMU["cy"])
    assert call["deck"].tokens.deck_title == "분기 실적 요약"
    assert measurer.calls == len(PAGES)
    assert measurer.max_in_flight == 2
    assert [update["stage"] for update in progress][0] == "authored_outline"
    assert [update["stage"] for update in progress][-1] == "authored_publication"
    assert sum(1 for update in progress if update["stage"] == "authored_render") == len(PAGES)

    receipt_path = composer_receipt_path(job.id, presentation_id)
    assert receipt_path == tmp_path / f"job-{job.id}-composer.json"
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    assert receipt["schemaVersion"] == "composer-receipt/v1"
    assert receipt["status"] == "succeeded"
    assert receipt["pageCount"] == len(PAGES)
    assert receipt["rungHistogram"] == {"0": len(PAGES)}
    assert [slide["index"] for slide in receipt["slides"]] == [0, 1, 2]
    assert all(slide["renders"] == 1 and slide["blockingIssues"] == [] for slide in receipt["slides"])
    assert set(receipt["timingsMs"]) >= {"classify", "fit", "assert", "publish", "total"}
    assert receipt["publication"]["generationRoute"] == "composer"


def test_run_composer_restyle_honours_render_concurrency_limit(monkeypatch, tmp_path) -> None:
    measurer = CountingMeasurer()
    asyncio.run(
        _run(
            monkeypatch, tmp_path, renderer=fake_renderer, publisher=RecordingPublisher(), measurer=measurer,
            concurrency="1",
        )
    )
    assert measurer.max_in_flight == 1


def test_fit_deck_runs_the_real_thread_path_once_per_slide_by_default(monkeypatch, tmp_path) -> None:
    """``fit_deck`` submits every slide through ``asyncio.to_thread`` by default.

    The fit ladder must run off the calling event loop (production defect:
    a blocking Chromium render on the worker's own loop starved its lease
    heartbeat). Patch the dynamically-referenced ``asyncio.to_thread`` seam
    and delegate to the real implementation so the ladder still executes
    correctly; only the per-slide submission is observed.
    """

    from services.composer import pipeline_fit

    real_to_thread = asyncio.to_thread
    submitted: list[int] = []

    async def recording_to_thread(func, /, *args, **kwargs):
        if getattr(func, "__name__", "") == "_run_fit_slide_in_thread":
            submitted.append(args[0].slide_index)
        return await real_to_thread(func, *args, **kwargs)

    monkeypatch.setattr(pipeline_fit.asyncio, "to_thread", recording_to_thread)

    asyncio.run(
        _run(
            monkeypatch, tmp_path, renderer=fake_renderer, publisher=RecordingPublisher(),
            measurer=CountingMeasurer(), concurrency="2",
        )
    )

    assert sorted(submitted) == [0, 1, 2]


def test_run_composer_restyle_serializes_on_slide_progress_reports(monkeypatch, tmp_path) -> None:
    """Two slides finishing close together must never call the reporter concurrently.

    A concurrent call races the workspace operation's optimistic-concurrency
    progress write: one caller's CAS loses and ``_report`` turns that into a
    spurious ``operation_lease_lost`` (confirmed live: operation 6ca5b1f0
    attempt 1). ``fit_deck`` serializes ``on_slide`` behind one lock even
    though slides are fit in parallel across real threads.
    """

    busy = False
    overlapped = False

    async def reporter(**update: Any) -> bool:
        nonlocal busy, overlapped
        if update.get("stage") != "authored_render":
            return True
        if busy:
            overlapped = True
        busy = True
        await asyncio.sleep(0.02)
        busy = False
        return True

    asyncio.run(
        _run(
            monkeypatch, tmp_path, renderer=fake_renderer, publisher=RecordingPublisher(),
            measurer=CountingMeasurer(), concurrency="2", reporter=reporter,
        )
    )

    assert overlapped is False


def test_run_composer_restyle_extends_lease_once_per_slide_during_fit(monkeypatch, tmp_path) -> None:
    """The workspace operation's lease is widened from the fit loop, not just before publish.

    Before this, ``run_composer_restyle`` accepted ``extend_lease`` but only
    called it once, right before the publish transaction; the caller never
    even wired one through (docs/generation-quality-root-cause-and-composer-
    architecture-20260904.md, section 6a "Known follow-ups"). A 43-page deck's
    fit stage alone took 223s against a 300s lease, so a heavier deck -- or
    several running at once -- could lose the lease mid-render. ``fit_deck``
    (services.composer.pipeline_fit) now calls ``extend_lease`` once per
    completed slide, from the same lock-serialized path as ``on_slide``.
    """

    from services.composer.pipeline import EXTEND_LEASE_SECONDS_FOR_PUBLISH
    from services.composer.pipeline_fit import EXTEND_LEASE_SECONDS_FOR_FIT

    calls: list[int] = []

    async def extend_lease(*, seconds: int) -> bool:
        calls.append(seconds)
        return True

    asyncio.run(
        _run(
            monkeypatch, tmp_path, renderer=fake_renderer, publisher=RecordingPublisher(),
            measurer=CountingMeasurer(), concurrency="2", extend_lease=extend_lease,
        )
    )

    # Once per slide during the fit loop, plus once more before publish.
    assert len(calls) == len(PAGES) + 1
    assert calls[:-1] == [EXTEND_LEASE_SECONDS_FOR_FIT] * len(PAGES)
    assert calls[-1] == EXTEND_LEASE_SECONDS_FOR_PUBLISH


def test_run_composer_restyle_survives_a_failing_lease_extension(monkeypatch, tmp_path) -> None:
    """A lease extension that returns ``False`` or raises never fails the deck.

    ``maybe_extend_lease`` is best-effort by design (services.composer.
    pipeline_fit): the fit loop's own readback/binding assertions and
    publication's own fencing protect correctness regardless of whether the
    lease was actually widened.
    """

    async def flaky_extend_lease(*, seconds: int) -> bool:
        raise RuntimeError("simulated lease-store outage")

    result, presentation_id, _job, _progress = asyncio.run(
        _run(
            monkeypatch, tmp_path, renderer=fake_renderer, publisher=RecordingPublisher(),
            measurer=CountingMeasurer(), concurrency="2", extend_lease=flaky_extend_lease,
        )
    )
    assert result.presentation_id == presentation_id


def test_run_composer_restyle_dropped_run_fails_closed_with_slide_index(monkeypatch, tmp_path) -> None:
    publisher = RecordingPublisher()
    with pytest.raises(ComposerAssertionError) as raised:
        asyncio.run(
            _run(
                monkeypatch, tmp_path, renderer=dropping_renderer, publisher=publisher,
                measurer=CountingMeasurer(), concurrency="2",
            )
        )
    assert raised.value.slide_index == 1
    assert raised.value.retryable is False
    assert raised.value.code == "source_content_browser_readback_failed"
    assert publisher.calls == []
    receipts = list(tmp_path.glob("job-*-composer.json"))
    assert len(receipts) == 1
    receipt = json.loads(receipts[0].read_text(encoding="utf-8"))
    assert receipt["status"] == "failed"
    assert receipt["failure"]["slideIndex"] == 1
    assert receipt["failure"]["code"] == "source_content_browser_readback_failed"
    assert len(receipt["slides"]) == len(PAGES)


def test_semantic_draft_declares_qids_in_document_order() -> None:
    plan = fake_classifier(unit=_manifest()["units"][0], slide_index=0, deck_context={
        "page_count": len(PAGES), "source_page_emu": (PAGE_EMU["cx"], PAGE_EMU["cy"]),
    })
    html = _render(plan).replace("</body>", '<img data-qid="img-1" data-semantic-role="image" alt=""></body>')
    draft = semantic_draft_from_html(
        html, language="ko", font_family="Malgun Gothic", font_face="Regular", tokens=_tokens()
    )
    elements = draft["semanticSource"]["elements"]
    assert [item["kind"] for item in elements] == ["text", "text", "shape"]
    assert [item["semanticRole"] for item in elements] == ["title", "body", "image"]
    assert elements[0]["payload"]["paragraphs"][0]["runs"][0]["text"] == "분기 실적 요약"
    assert elements[0]["payload"]["paragraphs"][0]["runs"][0]["script"] == "cjk"
    assert draft["semanticSource"]["identityRegistry"]["activeQids"] == sorted(item["elementId"] for item in elements)


def _admission_request() -> GeneratePresentationRequest:
    return GeneratePresentationRequest(
        content="restyle",
        template="authored",
        n_slides=10,
        authored_style="style-a",
        sourceMode="source_content_restyle",
        sourceContentManifestPin={"manifestId": "manifest-1", "digest": "sha256:" + "0" * 64},
        contentPolicy={"text": "verbatim"},
        instructions="깔끔한 파란색 톤",
    )


def _patch_admission(monkeypatch, *, enabled: bool) -> None:
    from services.source_content import manifest_store

    manifest = SimpleNamespace(source_type="pptx", reference_snapshot_id=str(uuid.uuid4()), units=())

    async def admit_pin(session, *, owner_id, manifest_id, digest):
        assert manifest_id == "manifest-1"
        return manifest

    class FakeOutlineBuilder:
        def build(self, *, manifest, manifest_pin):
            return SimpleNamespace(source_slide_count=3, slides=(1, 2, 3))

    monkeypatch.setattr(manifest_store, "admit_source_content_manifest_pin", admit_pin)
    monkeypatch.setattr(source_pipeline, "SourceDocumentOutlineBuilder", FakeOutlineBuilder)
    monkeypatch.setattr(source_pipeline, "get_composer_restyle_enabled", lambda: enabled)


def test_admission_with_composer_flag_keeps_source_mode_and_skips_authored_rewrite(monkeypatch) -> None:
    _patch_admission(monkeypatch, enabled=True)
    request = _admission_request()
    asyncio.run(source_pipeline.admit_source_content_request(request, sql_session=object(), owner_id="owner-a"))

    assert request.source_mode == "source_content_restyle"
    assert request._composer_restyle is True
    assert request._source_content_authored_reflow is False
    assert request.n_slides == 3

    snapshot_id = source_pipeline.prepare_source_content_authored_reflow(request)
    assert snapshot_id == request._source_content_manifest.reference_snapshot_id
    assert request.source_mode == "source_content_restyle"
    assert request.slides_markdown is None
    assert request.instructions == "깔끔한 파란색 톤"
    assert request.vision_qa is False
    assert request._source_content_authored_reflow is False


def test_admission_without_composer_flag_leaves_request_for_authored_reflow(monkeypatch) -> None:
    _patch_admission(monkeypatch, enabled=False)
    request = _admission_request()
    asyncio.run(source_pipeline.admit_source_content_request(request, sql_session=object(), owner_id="owner-a"))

    assert request._composer_restyle is False
    assert request.n_slides == 10
    assert request.source_mode == "source_content_restyle"



@pytest.mark.parametrize("text_policy", ["verbatim", "outline"])
@pytest.mark.parametrize("revision,floor", [
    ("authored-local-fit/v1.3", 9.5),
    ("authored-local-fit/v1.4", 9.0),
    ("authored-local-fit/v1.5", 9.0),
])
def test_admission_restores_sealed_composer_route_before_live_switch(
    monkeypatch, text_policy, revision, floor,
) -> None:
    from api.v1.ppt.endpoints import presentation_generate as generation
    from services.authored_local_fit_policy import policy_floor_pt
    from services.public_generation_route import pin_source_restyle_authoring

    _patch_admission(monkeypatch, enabled=False)
    request = _admission_request()
    request.content_policy = request.content_policy.model_copy(update={"text": text_policy})
    receipt = pin_source_restyle_authoring(request)
    assert receipt is not None
    assert receipt["fallbackReason"] == "source-content-composer-reflow"
    request._authored_local_fit_policy_revision = revision
    durable = generation._durable_request_payload(request)
    durable["generation_route_receipt"] = receipt
    restored = generation._request_from_durable_payload(durable)
    # The private admission flag does not round-trip through public Pydantic
    # fields; the handler must restore the sealed receipt before readmission.
    assert not getattr(restored, "_source_content_sealed_authored_route", False)
    restored._composer_restyle = False
    _patch_admission(monkeypatch, enabled=False)

    async def master_admission(request):
        pass

    async def source_admission(request, session):
        await source_pipeline.admit_source_content_request(
            request, sql_session=session, owner_id="owner-a",
        )

    class AdmissionObserved(Exception):
        pass

    async def reference_admission(request, session):
        assert request._composer_restyle is True
        assert request._source_content_sealed_authored_route is False
        assert request._authored_local_fit_policy_revision == revision
        assert policy_floor_pt(request._authored_local_fit_policy_revision) == floor
        raise AdmissionObserved

    monkeypatch.setattr(generation, "_validate_authored_master_pin_admission", master_admission)
    monkeypatch.setattr(generation, "_admit_source_content_generation", source_admission)
    monkeypatch.setattr(generation, "_admit_source_content_reference_snapshot", reference_admission)
    with pytest.raises(AdmissionObserved):
        asyncio.run(generation.generate_presentation_handler.__wrapped__(
            request=restored, presentation_id=uuid.uuid4(), async_status=None,
            generation_job=SimpleNamespace(request_payload=durable),
            sql_session=object(),
        ))


def test_admission_historical_job_without_sealed_route_keeps_composer(monkeypatch) -> None:
    from api.v1.ppt.endpoints.presentation_generate import (
        _durable_request_payload, _request_from_durable_payload,
    )
    from services.public_generation_route import restore_source_restyle_route

    _patch_admission(monkeypatch, enabled=True)
    durable = _durable_request_payload(_admission_request())
    restored = _request_from_durable_payload(durable)
    restore_source_restyle_route(restored, durable)
    asyncio.run(source_pipeline.admit_source_content_request(
        restored, sql_session=object(), owner_id="owner-a",
    ))
    assert restored._composer_restyle is True
    assert not restored._source_content_sealed_authored_route


def test_admission_invalid_sealed_route_fails_closed() -> None:
    from pydantic import ValidationError
    from services.public_generation_route import restore_source_restyle_route

    with pytest.raises(ValidationError):
        restore_source_restyle_route(_admission_request(), {
            "generation_route_receipt": {
                "selectedRoute": "authored-fallback",
                "fallbackReason": "source-content-authored-reflow",
                "receiptDigest": "sha256:" + "0" * 64,
            },
        })

# --- source image markers: defect 1 --------------------------------------------------


def test_run_composer_restyle_inlines_source_image_markers_before_measuring(monkeypatch, tmp_path) -> None:
    """Every measured and published HTML is admitted; ``data-asset-id`` never survives."""

    measurer = RecordingHTMLMeasurer()
    publisher = RecordingPublisher()
    resolver = in_memory_asset_resolver({"asset-1": _PNG_1X1})
    monkeypatch.setenv("AUTHORED_QUALITY_RECEIPT_DIRECTORY", str(tmp_path))
    monkeypatch.setenv("PUBLIC_RENDER_MAX_CONCURRENCY", "2")

    async def reporter(**update: Any) -> bool:
        return True

    presentation_id = uuid.uuid4()
    job = SimpleNamespace(id=uuid.uuid4(), workspace_operation_id=None, attempt_token=None)
    asyncio.run(
        run_composer_restyle(
            request=_request(), presentation_id=presentation_id, generation_job=job,
            progress_reporter=reporter, sql_session=object(), async_status=None,
            measurer=measurer, classifier=fake_classifier, renderer=image_marker_renderer,
            estimator=fake_estimator, publisher=publisher, asset_resolver=resolver,
        )
    )

    assert measurer.seen_html and len(measurer.seen_html) == len(PAGES)
    assert all("data-asset-id" not in html for html in measurer.seen_html)
    assert all("data:image/png;base64," in html for html in measurer.seen_html)
    assert all('data-qid="img-q1"' in html for html in measurer.seen_html)

    call = publisher.calls[0]
    assert all("data-asset-id" not in html for html in call["htmls"])
    assert all("data:image/png;base64," in html for html in call["htmls"])


def test_run_composer_restyle_missing_asset_bytes_raise_composer_asset_error(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("AUTHORED_QUALITY_RECEIPT_DIRECTORY", str(tmp_path))
    monkeypatch.setenv("PUBLIC_RENDER_MAX_CONCURRENCY", "1")

    async def reporter(**update: Any) -> bool:
        return True

    presentation_id = uuid.uuid4()
    job = SimpleNamespace(id=uuid.uuid4(), workspace_operation_id=None, attempt_token=None)
    resolver = in_memory_asset_resolver({})

    with pytest.raises(ComposerAssertionError) as raised:
        asyncio.run(
            run_composer_restyle(
                request=_request(), presentation_id=presentation_id, generation_job=job,
                progress_reporter=reporter, sql_session=object(), async_status=None,
                measurer=CountingMeasurer(), classifier=fake_classifier, renderer=image_marker_renderer,
                estimator=fake_estimator, publisher=RecordingPublisher(), asset_resolver=resolver,
            )
        )
    assert raised.value.code == "composer_asset_unresolved"
    assert raised.value.retryable is False
    receipts = list(tmp_path.glob("job-*-composer.json"))
    assert len(receipts) == 1
    receipt = json.loads(receipts[0].read_text(encoding="utf-8"))
    assert receipt["status"] == "failed"
    assert receipt["failure"]["code"] == "composer_asset_unresolved"


# --- rung 5 residual-overlap acceptance: defect 2 --------------------------------------


def _bound_html(block_id: str, text: str) -> str:
    qid = deterministic_source_qid(block_id)
    return (
        '<html><body style="width:1280px;height:720px;margin:0;overflow:hidden">'
        f'<p data-source-block-id="{block_id}" data-qid="{qid}" data-semantic-role="body">'
        f'{html_module.escape(text)}</p></body></html>'
    )


def _slide_result(index: int, *, rung: int, blocking_issues: list[str]) -> ComposerSlideResult:
    params = FitParameters(rung=rung, source_geometry=rung == 5, page_scale=0.90 if rung == 5 else 1.0)
    receipt = FitReceipt(
        slide_index=index, rung=rung, parameters=params, renders=1, estimate_used=False,
        blocking_issues=blocking_issues, trail=[],
    )
    return ComposerSlideResult(
        slide_index=index, html=_bound_html("b1", "본문 내용"), receipt=receipt, metrics={}, png_path=None,
    )


def test_is_accepted_residual_only_rung_five_and_overlap_only() -> None:
    assert is_accepted_residual(5, ["overlap: boxes overlap (q1, q2)"]) is True
    assert is_accepted_residual(5, ["overlap: a (q1)", "overlap: b (q2)"]) is True
    assert is_accepted_residual(5, []) is False
    assert is_accepted_residual(5, ["clipping: text clipped (q1)"]) is False
    assert is_accepted_residual(5, ["overlap: a (q1)", "clipping: b (q2)"]) is False
    assert is_accepted_residual(5, ["off_canvas: a (q1)"]) is False
    assert is_accepted_residual(5, ["overflow: a (q1)"]) is False
    assert is_accepted_residual(3, ["overlap: a (q1)"]) is False
    assert is_accepted_residual(0, ["overlap: a (q1)"]) is False


def test_assert_slide_bindings_accepts_rung5_overlap_only_residual() -> None:
    result = _slide_result(0, rung=5, blocking_issues=["overlap: boxes overlap (q1, q2)"])
    stamping: dict[int, bool] = {}

    assert_slide_bindings([result], [[("b1", "본문 내용")]], stamping)

    assert stamping == {0: False}


def test_assert_slide_bindings_rejects_rung5_clipping_residual() -> None:
    result = _slide_result(0, rung=5, blocking_issues=["clipping: text clipped by its box (q1)"])

    with pytest.raises(ComposerAssertionError) as raised:
        assert_slide_bindings([result], [[("b1", "본문 내용")]])

    assert raised.value.code == "composer_blocking_issues_remaining"
    assert raised.value.slide_index == 0


def test_assert_slide_bindings_rejects_residual_before_rung_five() -> None:
    result = _slide_result(0, rung=3, blocking_issues=["overlap: boxes overlap (q1, q2)"])

    with pytest.raises(ComposerAssertionError) as raised:
        assert_slide_bindings([result], [[("b1", "본문 내용")]])

    assert raised.value.code == "composer_blocking_issues_remaining"
    assert raised.value.slide_index == 0


def test_build_composer_receipt_records_residual_issues_and_stamping_rebound() -> None:
    result = _slide_result(0, rung=5, blocking_issues=["overlap: boxes overlap (q1, q2)"])

    receipt = build_composer_receipt(
        presentation_id="p", job_id="j", page_count=1, results=[result], timings_ms={},
        stamping_rebound={0: True},
    )

    slide = receipt["slides"][0]
    assert slide["blockingIssues"] == []
    assert slide["residualIssues"] == ["overlap: boxes overlap (q1, q2)"]
    assert slide["stampingRebound"] is True
    assert receipt["rungHistogram"] == {"5": 1}
