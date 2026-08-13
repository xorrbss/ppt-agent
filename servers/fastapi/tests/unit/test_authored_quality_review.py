import asyncio
import json
import logging
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlmodel import SQLModel, select

from models.presentation_from_template import AuthoredQualityReviewRequest
from models.generate_presentation_request import GeneratePresentationRequest
from models.presentation_outline_model import (
    PresentationOutlineModel,
    SlideOutlineModel,
)
from models.sql.async_presentation_generation_status import (
    AsyncPresentationGenerationTaskModel,
)
from models.sql.authored_exact_provenance import AuthoredExactProvenance
from models.sql.presentation import PresentationModel
from models.sql.presentation_version import PresentationVersionModel
from models.sql.slide import SlideModel
from services import authored_presentation_service as authored_service
from services import authored_quality_review_service as quality_service
from services import presentation_version_service as version_service
from services.authored_exact_provenance import (
    ExactProvenanceError,
    build_exact_provenance,
)
from services.authored_review_asset_store import publish_reviewed_png
from tests.unit.authored_quality_test_metrics import complete_browser_metrics
from utils.authored_styles import (
    AuthoredExactReconstructionUnavailableError,
    AuthoredStyle,
)
from utils.authored_ocr_quality import OcrQualityResult
from utils.llm_calls.author_deck import AuthoredDeckResult
from utils.llm_calls.author_slide import (
    EXACT_RECONSTRUCTION,
    STYLE_REFERENCE,
    Brand,
)
from utils.llm_calls.critique_slide import CritiqueIssue, SlideCritique


def test_slidespec_merge_preserves_slide_index_on_flattened_issue_details():
    request = AuthoredQualityReviewRequest()
    merged = quality_service._merge_slidespec_review_data(
        presentation_id=uuid.uuid4(),
        request=request,
        legacy=None,
        canonical={
            "issue_slide_count": 1,
            "detailed_issue_count": 1,
            "confirmed_issue_count": 1,
            "remaining_issue_count": 1,
            "unresolved_confirmed_issue_count": 1,
            "slide_results": [
                {
                    "slide_index": 27,
                    "confirmed_issues": [
                        {"code": "dom.clipping", "severity": "high"}
                    ],
                    "unresolved_confirmed_issue_count": 1,
                }
            ],
            # Producer-level aggregate aliases historically omitted the index.
            "issues": [{"code": "dom.clipping", "severity": "high"}],
            "remaining_issues": [
                {"code": "dom.clipping", "severity": "high"}
            ],
        },
    )

    assert merged["issues"] == [
        {"code": "dom.clipping", "severity": "high", "slide_index": 27}
    ]
    assert merged["remaining_issues"] == merged["issues"]


@pytest.fixture(autouse=True)
def _complete_browser_telemetry(monkeypatch):
    monkeypatch.setattr(
        quality_service,
        "get_cached_browser_metrics",
        complete_browser_metrics,
    )


def _run(coro):
    return asyncio.run(coro)


def _mock_text_preserving_ocr(monkeypatch) -> None:
    async def fake_ocr(
        _image, *, expected_text=None, language="eng", cache=None, **_kwargs
    ):
        cache.calls += 1
        return OcrQualityResult(
            available=True,
            status="success",
            failure_reason=None,
            text="preserved slide text",
            text_digest="preserved-slide-text",
            metrics={},
            calls=cache.calls,
            cache_hits=cache.cache_hits,
            elapsed_ms=0.0,
        )

    monkeypatch.setattr(quality_service, "inspect_authored_ocr", fake_ocr)


@pytest.mark.parametrize(
    "error, expected",
    [
        (
            ExactProvenanceError("private path C:/secret/source.png and digest"),
            "authored_exact_reconstruction_provenance_mismatch",
        ),
        (
            AuthoredExactReconstructionUnavailableError("private-exact-style"),
            "authored_exact_reconstruction_unavailable",
        ),
    ],
)
def test_exact_review_error_detail_hides_private_metadata(error, expected):
    assert quality_service._public_review_error_detail(error) == expected


def test_style_review_error_detail_hides_private_metadata():
    error = RuntimeError(
        "write failed for C:/private/review/slide.png digest=0123456789abcdef"
    )

    assert (
        quality_service._public_review_error_detail(error)
        == "authored_quality_review_failed"
    )


async def _make_engine(db_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}")
    async with engine.begin() as connection:
        await connection.run_sync(SQLModel.metadata.create_all)
    return engine


def _presentation() -> PresentationModel:
    outline = PresentationOutlineModel(
        slides=[
            SlideOutlineModel(content="Cover content"),
            SlideOutlineModel(content="Detail content"),
        ]
    )
    return PresentationModel(
        content="Quality review",
        n_slides=2,
        language="Korean",
        title="Quality review",
        outlines=outline.model_dump(mode="json"),
        mode="authored",
        theme={
            "mode": "authored",
            "style": "default",
            "primary": "#2563EB",
            "fonts": "Noto Sans KR",
        },
    )


def _slides(presentation_id):
    return [
        SlideModel(
            presentation=presentation_id,
            layout_group="authored",
            layout="authored:COVER",
            index=0,
            content={
                "__authored__": True,
                "image": "authored/original/slide_0.png",
                "role": "COVER",
            },
            html_content="<html><body>old cover</body></html>",
            properties={"image": "authored/original/slide_0.png"},
        ),
        SlideModel(
            presentation=presentation_id,
            layout_group="authored",
            layout="authored:CLOSING",
            index=1,
            content={
                "__authored__": True,
                "image": "authored/original/slide_1.png",
                "role": "CLOSING",
            },
            html_content="<html><body>old detail</body></html>",
            properties={"image": "authored/original/slide_1.png"},
        ),
    ]


def test_unimportable_authored_deck_is_preserved_without_page_regeneration(
    tmp_path, monkeypatch
):
    _mock_text_preserving_ocr(monkeypatch)

    async def runner():
        engine = await _make_engine(tmp_path / "quality-review.db")
        presentation = _presentation()
        task = AsyncPresentationGenerationTaskModel(
            status="pending", message="queued"
        )

        async with AsyncSession(engine, expire_on_commit=False) as session:
            session.add(presentation)
            session.add_all(_slides(presentation.id))
            session.add(task)
            await session.commit()

            review_calls = 0

            async def fake_render(html):
                return html.encode()

            async def fake_critique(pngs, contexts=None):
                nonlocal review_calls
                review_calls += 1
                return [
                    SlideCritique(
                        needs_fix=True,
                        issues=[
                            CritiqueIssue(
                                type="overflow",
                                severity="high",
                                detail="Title is clipped",
                            )
                        ],
                    ),
                    SlideCritique(needs_fix=False),
                ]

            async def fake_compare(
                baselines, candidates, contexts=None, **kwargs
            ):
                assert baselines == [b"<html><body>old cover</body></html>"]
                assert candidates == [b"fixed-cover"]
                return [SlideCritique(needs_fix=False)]

            async def fake_revise(
                htmls,
                pngs,
                contents,
                roles,
                brand,
                design_system,
                **kwargs,
            ):
                assert kwargs["slide_indices"] == [0, 1]
                assert kwargs["total_slides"] == 2
                return [
                    "<html><body>fixed cover</body></html>",
                    htmls[1],
                ], [b"fixed-cover", pngs[1]], [0]

            monkeypatch.setattr(
                quality_service, "resolve_authored_style", lambda style_id: object()
            )
            monkeypatch.setattr(
                quality_service,
                "prepare_authored_deck",
                lambda outline, brand, style: SimpleNamespace(
                    roles=("COVER", "CLOSING"),
                    brand=Brand(topic="Quality review"),
                    design_system="DESIGN",
                ),
            )
            monkeypatch.setattr(
                quality_service, "render_html_to_png", fake_render
            )
            monkeypatch.setattr(
                quality_service, "critique_authored", fake_critique
            )
            monkeypatch.setattr(
                quality_service,
                "critique_authored_style_comparison",
                fake_compare,
            )
            monkeypatch.setattr(
                quality_service, "revise_authored_deck", fake_revise
            )
            monkeypatch.setattr(
                quality_service,
                "_write_reviewed_png",
                lambda presentation_id, index, png: (
                    f"authored/{presentation_id}/slide_{index}.png"
                ),
            )

            await quality_service.run_authored_quality_review(
                presentation.id,
                AuthoredQualityReviewRequest(
                    scope="all", mode="analyze_and_fix"
                ),
                task,
                session,
            )

            live_slides = list(
                await session.scalars(
                    select(SlideModel)
                    .where(SlideModel.presentation == presentation.id)
                    .order_by(SlideModel.index)
                )
            )
            versions = list(
                await session.scalars(
                    select(PresentationVersionModel).where(
                        PresentationVersionModel.presentation_id
                        == presentation.id
                    )
                )
            )

            # Maximum mode never falls back to the legacy full-page HTML
            # rewriter. This minimal fixture has no importable DOM nodes, so it
            # remains unresolved and byte-preserved.
            assert live_slides[0].html_content == (
                "<html><body>old cover</body></html>"
            )
            assert live_slides[1].html_content == (
                "<html><body>old detail</body></html>"
            )
            assert versions == []
            assert task.status == "completed"
            assert task.data["fixed_slide_indices"] == []
            assert task.data["remaining_issue_count"] == 2
            assert task.data["rejection_reasons"] == {"import.dom.empty": 2}
            assert task.data["metrics"]["automatic_page_regeneration_calls"] == 0
            assert task.data["version_saved"] is False

        await engine.dispose()

    _run(runner())


def _exact_plan(source_paths):
    return SimpleNamespace(
        roles=("CONTENT", "CONTENT"),
        reference_images=tuple((path,) for path in source_paths),
        reference_mode=EXACT_RECONSTRUCTION,
        brand=Brand(topic="Quality review"),
        design_system="DESIGN",
    )


def _exact_style(source_paths) -> AuthoredStyle:
    return AuthoredStyle(
        id="exact-source",
        name="Exact source",
        description="Private test preset",
        preview_bg="#FFFFFF",
        preview_accent="#000000",
        brief="Reconstruct the supplied sources.",
        reference_mode=EXACT_RECONSTRUCTION,
        slide_reference_images=tuple(source_paths),
        slide_reference_indices=tuple(range(len(source_paths))),
    )


def _exact_provenance_row(presentation_id, style) -> AuthoredExactProvenance:
    provenance, _ = build_exact_provenance(style)
    return AuthoredExactProvenance(
        presentation_id=presentation_id,
        provenance=provenance,
    )


def test_unimportable_exact_deck_preserves_original_png_bytes_without_snapshot(
    tmp_path, monkeypatch
):
    app_data = tmp_path / "app-data"
    monkeypatch.setenv("APP_DATA_DIRECTORY", str(app_data))

    async def runner():
        engine = await _make_engine(tmp_path / "exact-restore-undo.db")
        presentation = _presentation()
        presentation.theme["style"] = "exact-source"
        presentation_id = presentation.id
        source_paths = (tmp_path / "source-0.png", tmp_path / "source-1.png")
        source_paths[0].write_bytes(b"private-source-zero")
        source_paths[1].write_bytes(b"private-source-one")
        style = _exact_style(source_paths)
        old_pngs = (b"old-render-zero", b"old-render-one")
        old_references = tuple(
            publish_reviewed_png(presentation_id, index, png)
            for index, png in enumerate(old_pngs)
        )
        slides = _slides(presentation_id)
        for index, slide in enumerate(slides):
            slide.content["image"] = old_references[index]
            slide.properties["image"] = old_references[index]
        task = AsyncPresentationGenerationTaskModel(
            status="pending", message="queued"
        )
        critique_count = 0

        async def fake_critique(pngs, contexts=None, **kwargs):
            nonlocal critique_count
            critique_count += 1
            if critique_count == 1:
                return [
                    SlideCritique(
                        needs_fix=True,
                        issues=[
                            CritiqueIssue(
                                type="other",
                                severity="high",
                                detail="candidate differs from source",
                            )
                        ],
                    ),
                    SlideCritique(needs_fix=False),
                ]
            assert pngs == [b"strictly-improved-zero"]
            return [SlideCritique(needs_fix=False)]

        async def fake_revise(htmls, pngs, *args, **kwargs):
            return (
                ["<html><body>fixed exact zero</body></html>", htmls[1]],
                [b"strictly-improved-zero", pngs[1]],
                [0],
            )

        async with AsyncSession(engine, expire_on_commit=False) as session:
            session.add(presentation)
            session.add_all(slides)
            session.add(_exact_provenance_row(presentation_id, style))
            session.add(task)
            await session.commit()

            monkeypatch.setattr(
                quality_service, "resolve_authored_style", lambda _: style
            )
            monkeypatch.setattr(
                quality_service,
                "prepare_authored_deck",
                lambda *args: _exact_plan(source_paths),
            )
            monkeypatch.setattr(
                quality_service,
                "render_html_to_png",
                lambda html: _async_bytes(
                    old_pngs[0] if "cover" in html else old_pngs[1]
                ),
            )
            monkeypatch.setattr(
                quality_service, "critique_authored", fake_critique
            )
            monkeypatch.setattr(
                quality_service, "revise_authored_deck", fake_revise
            )

            await quality_service.run_authored_quality_review(
                presentation_id,
                AuthoredQualityReviewRequest(
                    scope="all", mode="analyze_and_fix"
                ),
                task,
                session,
            )

            review_data = dict(task.data)
            session.expire_all()
            fixed_slides = list(
                await session.scalars(
                    select(SlideModel)
                    .where(SlideModel.presentation == presentation_id)
                    .order_by(SlideModel.index)
                )
            )
            fixed_reference = fixed_slides[0].content["image"]
            assert fixed_reference == old_references[0]
            assert fixed_slides[0].properties["image"] == fixed_reference
            assert fixed_slides[1].content["image"] == old_references[1]
            assert (
                app_data / "images" / Path(fixed_reference)
            ).read_bytes() == old_pngs[0]

            versions = await version_service.list_versions(
                session, presentation_id
            )
            assert versions == []
            assert review_data["fixed_count"] == 0
            assert review_data["rejection_reasons"] == {"import.dom.empty": 2}
            assert review_data["metrics"]["automatic_page_regeneration_calls"] == 0

        await engine.dispose()

    _run(runner())


def test_exact_generation_provenance_survives_unimportable_review_without_model_call(
    tmp_path, monkeypatch
):
    async def runner():
        engine = await _make_engine(tmp_path / "exact-generation-review.db")
        source_paths = (
            tmp_path / "private-source-0.png",
            tmp_path / "private-source-1.png",
        )
        Image.new("RGB", (1280, 720), "white").save(source_paths[0])
        Image.new("RGB", (1280, 720), "navy").save(source_paths[1])
        style = AuthoredStyle(
            id="exact-source",
            name="Exact source",
            description="Private test preset",
            preview_bg="#FFFFFF",
            preview_accent="#000000",
            brief="Reconstruct the supplied sources.",
            reference_mode=EXACT_RECONSTRUCTION,
            assets_root=tmp_path,
            slide_reference_images=source_paths,
            slide_reference_indices=(0, 1),
        )
        outline = PresentationOutlineModel(
            slides=[
                SlideOutlineModel(content="Cover content"),
                SlideOutlineModel(content="Detail content"),
            ]
        )
        pipeline = authored_service.AuthoredGenerationPipelineResult(
            deck=AuthoredDeckResult(
                [
                    "<html><body>generated cover</body></html>",
                    "<html><body>generated detail</body></html>",
                ],
                "EXACT DESIGN",
            ),
            pngs=[b"generated-cover", b"generated-detail"],
            elapsed=0.1,
            author_work=0.1,
            illustration_work=0.0,
            render_work=0.1,
        )

        monkeypatch.setenv("AUTHORED_EXACT_RECONSTRUCTION_ENABLED", "1")
        monkeypatch.setenv("AUTHORED_EXACT_STYLE_IDS", "exact-source")
        monkeypatch.setattr(
            authored_service, "resolve_authored_style", lambda _: style
        )
        monkeypatch.setattr(
            authored_service,
            "_author_illustrate_render_pipeline",
            lambda *args, **kwargs: _async_value(pipeline),
        )
        monkeypatch.setattr(authored_service, "find_chrome", lambda: True)
        monkeypatch.setattr(
            authored_service,
            "_save_slide_pngs",
            lambda presentation_id, pngs: [
                "authored/generated/slide-0.png",
                "authored/generated/slide-1.png",
            ],
        )
        monkeypatch.setattr(
            authored_service,
            "_build_authored_export",
            lambda *args, **kwargs: "generated.pptx",
        )
        from utils import llm_provider

        monkeypatch.setattr(
            llm_provider,
            "get_llm_provider",
            lambda: SimpleNamespace(value="stub"),
        )
        monkeypatch.setattr(llm_provider, "get_model", lambda: "stub-model")

        snapshot_id = "a" * 32
        materialize_calls = []

        async def materialize_snapshot(
            resolved_style, saved_snapshot_id, *, expected_slide_count
        ):
            materialize_calls.append(
                (resolved_style.id, saved_snapshot_id, expected_slide_count)
            )
            return style

        monkeypatch.setattr(
            authored_service,
            "materialize_exact_source_style",
            materialize_snapshot,
        )
        monkeypatch.setattr(
            quality_service,
            "materialize_exact_source_style",
            materialize_snapshot,
        )

        presentation_id = uuid.uuid4()
        async with AsyncSession(engine, expire_on_commit=False) as session:
            generation_request = GeneratePresentationRequest(
                content="Exact topic",
                template="authored",
                authored_style="exact-source",
            )
            generation_request._authored_exact_snapshot_id = snapshot_id
            await authored_service.generate_authored_presentation(
                generation_request,
                presentation_id,
                outline,
                "English",
                session,
            )

            saved = await session.get(PresentationModel, presentation_id)
            provenance = await session.get(
                AuthoredExactProvenance, presentation_id
            )
            assert saved.theme["style"] == "exact-source"
            assert "reference_mode" not in saved.theme
            assert str(tmp_path) not in str(saved.theme)
            assert provenance is not None
            assert str(tmp_path) not in str(provenance.provenance)
            assert provenance.provenance["source_snapshot_id"] == snapshot_id

            task = AsyncPresentationGenerationTaskModel(
                status="pending", message="queued"
            )
            session.add(task)
            await session.commit()
            critique_calls = []

            async def fake_critique(pngs, contexts=None, **kwargs):
                critique_calls.append((list(pngs), kwargs))
                return [SlideCritique(needs_fix=False)]

            monkeypatch.setattr(
                quality_service, "resolve_authored_style", lambda _: style
            )
            monkeypatch.setattr(
                quality_service,
                "prepare_authored_deck",
                lambda *args: SimpleNamespace(
                    roles=("COVER", "CLOSING"),
                    reference_images=tuple((path,) for path in source_paths),
                    reference_mode=EXACT_RECONSTRUCTION,
                    brand=Brand(topic="Exact topic"),
                    design_system="EXACT DESIGN",
                ),
            )
            monkeypatch.setattr(
                quality_service,
                "render_html_to_png",
                lambda html: _async_bytes(html.encode()),
            )
            monkeypatch.setattr(
                quality_service, "critique_authored", fake_critique
            )
            monkeypatch.setattr(
                quality_service,
                "_observe_critique_cache",
                lambda images, contexts, **_: (0, len(images)),
            )

            await quality_service.run_authored_quality_review(
                presentation_id,
                AuthoredQualityReviewRequest(
                    scope="current", slide_indices=[1]
                ),
                task,
                session,
            )

            assert critique_calls == []
            assert task.data["reviewed_count"] == 1
            assert task.data["fixed_count"] == 0
            assert task.data["rejection_reasons"] == {"import.dom.empty": 1}
            assert task.data["metrics"]["automatic_page_regeneration_calls"] == 0
            assert materialize_calls == [
                ("exact-source", snapshot_id, 2),
            ]

        await engine.dispose()

    _run(runner())


def test_unimportable_legacy_exact_theme_stops_before_render_or_model(
    tmp_path, monkeypatch
):
    async def runner():
        engine = await _make_engine(tmp_path / "legacy-exact-missing.db")
        presentation = _presentation()
        presentation_id = presentation.id
        presentation.theme["style"] = "removed-exact-style"
        presentation.theme["reference_mode"] = EXACT_RECONSTRUCTION
        task = AsyncPresentationGenerationTaskModel(
            status="pending", message="queued"
        )
        async with AsyncSession(engine, expire_on_commit=False) as session:
            session.add(presentation)
            session.add_all(_slides(presentation.id))
            session.add(task)
            await session.commit()

            monkeypatch.setattr(
                quality_service,
                "resolve_authored_style",
                lambda *_: pytest.fail("preset resolution must not enable fallback"),
            )
            monkeypatch.setattr(
                quality_service,
                "render_html_to_png",
                lambda *_: pytest.fail("render must not run"),
            )
            monkeypatch.setattr(
                quality_service,
                "critique_authored",
                lambda *_args, **_kwargs: pytest.fail("model must not run"),
            )

            await quality_service.run_authored_quality_review(
                presentation.id,
                AuthoredQualityReviewRequest(scope="all"),
                task,
                session,
            )
            assert task.status == "completed"
            assert task.data["fixed_count"] == 0
            assert task.data["rejection_reasons"] == {"import.dom.empty": 2}
            assert task.data["metrics"]["automatic_page_regeneration_calls"] == 0

        await engine.dispose()

    _run(runner())


def test_analyze_and_fix_uses_process_local_lock(monkeypatch):
    async def runner():
        presentation_id = uuid.uuid4()
        active = 0
        peak = 0
        first_entered = asyncio.Event()
        release_first = asyncio.Event()

        async def fake_unlocked(*_args, **_kwargs):
            nonlocal active, peak
            active += 1
            peak = max(peak, active)
            if not first_entered.is_set():
                first_entered.set()
                await release_first.wait()
            active -= 1

        monkeypatch.setattr(
            quality_service,
            "_run_authored_quality_review_unlocked",
            fake_unlocked,
        )
        request = AuthoredQualityReviewRequest(
            scope="all", mode="analyze_and_fix"
        )
        first = asyncio.create_task(
            quality_service.run_authored_quality_review(
                presentation_id, request, SimpleNamespace(), SimpleNamespace()
            )
        )
        await first_entered.wait()
        second = asyncio.create_task(
            quality_service.run_authored_quality_review(
                presentation_id, request, SimpleNamespace(), SimpleNamespace()
            )
        )
        await asyncio.sleep(0)
        assert peak == 1
        release_first.set()
        await asyncio.gather(first, second)
        assert peak == 1

    _run(runner())


@pytest.mark.parametrize("competing_change", ["html", "image"])
def test_unimportable_review_never_reaches_legacy_revise_or_cas(
    tmp_path, monkeypatch, competing_change
):
    _mock_text_preserving_ocr(monkeypatch)

    async def runner():
        engine = await _make_engine(tmp_path / "quality-review-cas.db")
        presentation = _presentation()
        presentation_id = presentation.id
        task = AsyncPresentationGenerationTaskModel(
            status="pending", message="queued"
        )
        async with AsyncSession(engine, expire_on_commit=False) as session:
            session.add(presentation)
            session.add_all(_slides(presentation_id))
            session.add(task)
            await session.commit()

            style = SimpleNamespace(
                id="default", reference_mode=STYLE_REFERENCE
            )
            plan = SimpleNamespace(
                roles=("COVER", "CLOSING"),
                reference_images=((), ()),
                reference_mode=STYLE_REFERENCE,
                brand=Brand(topic="Quality review"),
                design_system="DESIGN",
            )
            critique_count = 0

            async def fake_critique(pngs, contexts=None, **kwargs):
                nonlocal critique_count
                critique_count += 1
                return [
                    SlideCritique(
                        needs_fix=True,
                        issues=[
                            CritiqueIssue(
                                type="overflow",
                                severity="high",
                                detail="clipped",
                            )
                        ],
                    ),
                    SlideCritique(needs_fix=False),
                ]

            async def fake_compare(
                baselines, candidates, contexts=None, **kwargs
            ):
                return [SlideCritique(needs_fix=False)]

            async def fake_revise(htmls, pngs, *args, **kwargs):
                async with AsyncSession(engine) as competing_session:
                    competing_slide = (
                        await competing_session.scalars(
                            select(SlideModel)
                            .where(
                                SlideModel.presentation == presentation_id
                            )
                            .where(SlideModel.index == 0)
                        )
                    ).one()
                    if competing_change == "html":
                        competing_slide.html_content = (
                            "<html><body>concurrent editor update</body></html>"
                        )
                    else:
                        competing_slide.content = {
                            **competing_slide.content,
                            "image": "authored/concurrent-editor.png",
                        }
                        competing_slide.properties = {
                            **competing_slide.properties,
                            "image": "authored/concurrent-editor.png",
                        }
                    competing_session.add(competing_slide)
                    await competing_session.commit()
                return (
                    ["<html><body>review candidate</body></html>", htmls[1]],
                    [b"review-candidate", pngs[1]],
                    [0],
                )

            monkeypatch.setattr(
                quality_service, "resolve_authored_style", lambda _: style
            )
            monkeypatch.setattr(
                quality_service, "prepare_authored_deck", lambda *args: plan
            )
            monkeypatch.setattr(
                quality_service,
                "render_html_to_png",
                lambda html: _async_bytes(html.encode()),
            )
            monkeypatch.setattr(
                quality_service, "critique_authored", fake_critique
            )
            monkeypatch.setattr(
                quality_service,
                "critique_authored_style_comparison",
                fake_compare,
            )
            monkeypatch.setattr(
                quality_service, "revise_authored_deck", fake_revise
            )
            monkeypatch.setattr(
                quality_service,
                "_write_reviewed_png",
                lambda *_: "authored/reviewed/immutable.png",
            )

            await quality_service.run_authored_quality_review(
                presentation_id,
                AuthoredQualityReviewRequest(
                    scope="all", mode="analyze_and_fix"
                ),
                task,
                session,
            )
            assert task.data["fixed_count"] == 0
            assert task.data["rejection_reasons"] == {"import.dom.empty": 2}
            assert task.data["metrics"]["automatic_page_regeneration_calls"] == 0

        async with AsyncSession(engine) as verification_session:
            live_slide = (
                await verification_session.scalars(
                    select(SlideModel)
                    .where(SlideModel.presentation == presentation_id)
                    .where(SlideModel.index == 0)
                )
            ).one()
            versions = list(
                await verification_session.scalars(
                    select(PresentationVersionModel).where(
                        PresentationVersionModel.presentation_id
                        == presentation_id
                    )
                )
            )
            assert live_slide.html_content == (
                "<html><body>old cover</body></html>"
            )
            assert live_slide.content["image"] == (
                "authored/original/slide_0.png"
            )
            assert versions == []
        await engine.dispose()

    _run(runner())


def test_unimportable_exact_review_does_not_call_legacy_image_critique(
    tmp_path, monkeypatch, caplog
):
    caplog.set_level(logging.INFO, logger=quality_service.__name__)
    async def runner():
        engine = await _make_engine(tmp_path / "exact-analyze.db")
        presentation = _presentation()
        presentation.theme["style"] = "exact-source"
        task = AsyncPresentationGenerationTaskModel(status="pending", message="queued")
        source_paths = (tmp_path / "source-0.png", tmp_path / "source-1.png")
        source_paths[0].write_bytes(b"source-0")
        source_paths[1].write_bytes(b"source-1")
        style = _exact_style(source_paths)
        critique_calls = []

        async with AsyncSession(engine, expire_on_commit=False) as session:
            session.add(presentation)
            session.add_all(_slides(presentation.id))
            session.add(_exact_provenance_row(presentation.id, style))
            session.add(task)
            await session.commit()

            async def fake_render(html):
                return html.encode()

            async def fake_critique(pngs, contexts=None, **kwargs):
                critique_calls.append((list(pngs), list(contexts or ()), kwargs))
                return [SlideCritique(needs_fix=False) for _ in pngs]

            monkeypatch.setattr(quality_service, "resolve_authored_style", lambda _: style)
            monkeypatch.setattr(
                quality_service, "prepare_authored_deck", lambda *args: _exact_plan(source_paths)
            )
            monkeypatch.setattr(quality_service, "render_html_to_png", fake_render)
            monkeypatch.setattr(quality_service, "critique_authored", fake_critique)
            monkeypatch.setattr(
                quality_service,
                "_observe_critique_cache",
                lambda images, contexts, **_: (0, len(images)),
            )

            await quality_service.run_authored_quality_review(
                presentation.id,
                AuthoredQualityReviewRequest(scope="all"),
                task,
                session,
            )

            assert critique_calls == []
            assert task.data["fixed_slide_indices"] == []
            assert task.data["rejection_reasons"] == {"import.dom.empty": 2}
            assert task.data["metrics"]["automatic_page_regeneration_calls"] == 0
            assert task.data["version_saved"] is False

        await engine.dispose()

    _run(runner())
    review_events = [
        json.loads(record.getMessage().partition(" ")[2])
        for record in caplog.records
        if record.getMessage().startswith("authored_quality_review ")
    ]
    assert review_events == []


def test_unimportable_exact_review_does_not_accept_legacy_html_rewrite(
    tmp_path, monkeypatch, caplog
):
    caplog.set_level(logging.INFO, logger=quality_service.__name__)
    _mock_text_preserving_ocr(monkeypatch)

    async def runner():
        engine = await _make_engine(tmp_path / "exact-fix.db")
        presentation = _presentation()
        presentation.theme["style"] = "exact-source"
        task = AsyncPresentationGenerationTaskModel(status="pending", message="queued")
        source_paths = (tmp_path / "source-0.png", tmp_path / "source-1.png")
        source_paths[0].write_bytes(b"source-0")
        source_paths[1].write_bytes(b"source-1")
        style = _exact_style(source_paths)
        critique_calls = []

        async with AsyncSession(engine, expire_on_commit=False) as session:
            session.add(presentation)
            session.add_all(_slides(presentation.id))
            session.add(_exact_provenance_row(presentation.id, style))
            session.add(task)
            await session.commit()

            async def fake_render(html):
                return html.encode()

            async def fake_critique(pngs, contexts=None, **kwargs):
                critique_calls.append((list(pngs), kwargs))
                if len(critique_calls) == 1:
                    return [
                        SlideCritique(
                            needs_fix=True,
                            issues=[CritiqueIssue(type="overflow", severity="high", detail="clipped")],
                        ),
                        SlideCritique(needs_fix=False),
                    ]
                return [SlideCritique(needs_fix=False)]

            async def fake_revise(*args, **kwargs):
                assert kwargs["initial_critiques"][0].needs_fix is True
                assert kwargs["reference_images_by_slide"] == [
                    (source_paths[0],), (source_paths[1],)
                ]
                return ["<html><body>fixed</body></html>", args[0][1]], [b"fixed", args[1][1]], [0]

            monkeypatch.setattr(quality_service, "resolve_authored_style", lambda _: style)
            monkeypatch.setattr(quality_service, "prepare_authored_deck", lambda *args: _exact_plan(source_paths))
            monkeypatch.setattr(quality_service, "render_html_to_png", fake_render)
            monkeypatch.setattr(quality_service, "critique_authored", fake_critique)
            monkeypatch.setattr(quality_service, "revise_authored_deck", fake_revise)
            monkeypatch.setattr(quality_service, "_write_reviewed_png", lambda *_: "authored/fixed.png")
            monkeypatch.setattr(
                quality_service,
                "_observe_critique_cache",
                lambda images, contexts, **_: (0, len(images)),
            )

            await quality_service.run_authored_quality_review(
                presentation.id,
                AuthoredQualityReviewRequest(scope="all", mode="analyze_and_fix"),
                task,
                session,
            )

            live_slides = list(await session.scalars(select(SlideModel).where(SlideModel.presentation == presentation.id).order_by(SlideModel.index)))
            versions = list(await session.scalars(select(PresentationVersionModel).where(PresentationVersionModel.presentation_id == presentation.id)))
            assert critique_calls == []
            assert live_slides[0].html_content == "<html><body>old cover</body></html>"
            assert live_slides[1].html_content == "<html><body>old detail</body></html>"
            assert versions == []
            assert task.data["fixed_slide_indices"] == []
            assert task.data["remaining_issue_count"] == 2
            assert task.data["rejection_reasons"] == {"import.dom.empty": 2}
            assert task.data["metrics"]["automatic_page_regeneration_calls"] == 0

        await engine.dispose()

    _run(runner())
    review_events = [
        json.loads(record.getMessage().partition(" ")[2])
        for record in caplog.records
        if record.getMessage().startswith("authored_quality_review ")
    ]
    assert review_events == []


@pytest.mark.parametrize(
    "final_critique",
    [
        SlideCritique(
            needs_fix=True,
            issues=[
                CritiqueIssue(
                    type="overflow",
                    severity="high",
                    detail="still clipped",
                )
            ],
        ),
        SlideCritique(
            needs_fix=False,
            issues=[
                CritiqueIssue(
                    type="other",
                    severity="low",
                    detail="inconsistent structured result",
                )
            ],
        ),
        None,
    ],
    ids=["not-improved", "inconsistent", "indeterminate"],
)
def test_exact_quality_review_rejects_non_improving_candidate_without_snapshot(
    tmp_path, monkeypatch, final_critique
):
    async def runner():
        engine = await _make_engine(tmp_path / "exact-reject.db")
        presentation = _presentation()
        presentation.theme["style"] = "exact-source"
        task = AsyncPresentationGenerationTaskModel(status="pending", message="queued")
        source_paths = (tmp_path / "source-0.png", tmp_path / "source-1.png")
        source_paths[0].write_bytes(b"source-0")
        source_paths[1].write_bytes(b"source-1")
        style = _exact_style(source_paths)

        async with AsyncSession(engine, expire_on_commit=False) as session:
            session.add(presentation)
            session.add_all(_slides(presentation.id))
            session.add(_exact_provenance_row(presentation.id, style))
            session.add(task)
            await session.commit()

            critique_calls = 0

            async def fake_critique(pngs, contexts=None, **kwargs):
                nonlocal critique_calls
                critique_calls += 1
                if critique_calls == 1:
                    return [
                        SlideCritique(
                            needs_fix=True,
                            issues=[
                                CritiqueIssue(
                                    type="overflow",
                                    severity="high",
                                    detail="clipped",
                                )
                            ],
                        )
                        for _ in pngs
                    ]
                return [final_critique]

            async def fake_revise(htmls, pngs, *args, **kwargs):
                return ["<html><body>worse</body></html>", htmls[1]], [b"worse", pngs[1]], [0]

            monkeypatch.setattr(quality_service, "resolve_authored_style", lambda _: style)
            monkeypatch.setattr(quality_service, "prepare_authored_deck", lambda *args: _exact_plan(source_paths))
            monkeypatch.setattr(quality_service, "render_html_to_png", lambda html: _async_bytes(html.encode()))
            monkeypatch.setattr(quality_service, "critique_authored", fake_critique)
            monkeypatch.setattr(quality_service, "revise_authored_deck", fake_revise)

            await quality_service.run_authored_quality_review(
                presentation.id,
                AuthoredQualityReviewRequest(scope="all", mode="analyze_and_fix"),
                task,
                session,
            )

            live_slide = (await session.scalars(
                select(SlideModel)
                .where(SlideModel.presentation == presentation.id)
                .where(SlideModel.index == 0)
            )).one()
            versions = list(await session.scalars(select(PresentationVersionModel).where(PresentationVersionModel.presentation_id == presentation.id)))
            assert live_slide.html_content == "<html><body>old cover</body></html>"
            assert versions == []
            assert task.data["fixed_slide_indices"] == []
            assert task.data["version_saved"] is False

        await engine.dispose()

    _run(runner())


async def _async_bytes(value):
    return value


async def _async_value(value):
    return value
