from models.slidespec_v1 import (
    SLIDESPEC_SCHEMA,
    AssetManifest,
    Bounds,
    ContentNode,
    FontManifest,
    ImportantToken,
    ImportedHtmlBinding,
    ImportedHtmlSource,
    ImportedPixelBounds,
    LayoutNode,
    RenderRequest,
    RendererManifest,
    SlideDigests,
    SlideSpec,
)
from utils.slidespec_core import ZERO_DIGEST, digest, seal_slidespec
from utils.slidespec_deck_constraints import evaluate_deck_consistency


def _request(
    index: int,
    *,
    font_size: float = 32,
    title_y: float = 0.1,
    viewport_width: float = 1280,
    renderer_version: str = "1",
    tokens: tuple[ImportantToken, ...] = (),
) -> RenderRequest:
    semantic_id = f"sem:title-{index}"
    layout_id = f"lay:title-{index}"
    spec = seal_slidespec(
        SlideSpec(
            schemaUri=SLIDESPEC_SCHEMA,
            specRevision=1,
            slideId=f"deck-slide-{index}",
            content=(
                ContentNode(
                    semanticId=semantic_id,
                    kind="title",
                    text=f"Title {index}",
                    readingOrder=0,
                ),
            ),
            layout=LayoutNode(
                layoutId=f"lay:root-{index}",
                kind="canvas",
                children=(
                    LayoutNode(
                        layoutId=layout_id,
                        kind="content",
                        contentSemanticId=semantic_id,
                        bounds=Bounds(x=0.1, y=title_y, width=0.6, height=0.1),
                    ),
                ),
            ),
            tokens=tokens,
            digests=SlideDigests(
                content=ZERO_DIGEST,
                hierarchy=ZERO_DIGEST,
                importantTokens=ZERO_DIGEST,
                spec=ZERO_DIGEST,
            ),
        )
    )
    html = f'<html><body><h1 data-qid="title-{index}">Title {index}</h1></body></html>'
    binding = ImportedHtmlBinding(
        semanticId=semantic_id,
        layoutId=layout_id,
        qid=f"title-{index}",
        tag="h1",
        bounds=Bounds(x=0.1, y=title_y, width=0.6, height=0.1),
        sourceBoundsPx=ImportedPixelBounds(
            x=128, y=title_y * 720, width=768, height=72
        ),
        fontSizePx=font_size,
    )
    source = ImportedHtmlSource(
        html=html,
        htmlHash=digest(html),
        viewportWidth=viewport_width,
        viewportHeight=720,
        bindings=(binding,),
    )
    return RenderRequest(
        spec=spec,
        renderer=RendererManifest(
            name="slidespec-authored-html",
            version=renderer_version,
            runtime="browser",
        ),
        fonts=FontManifest(digest=digest(("Arial",)), fonts=("Arial",)),
        assets=AssetManifest(digest=digest(())),
        source=source,
    )


def test_consistent_deck_has_stable_zero_cost_report() -> None:
    requests = tuple((index, _request(index)) for index in range(3))

    first = evaluate_deck_consistency(requests)
    second = evaluate_deck_consistency(tuple(reversed(requests)))

    assert first == second
    assert first.hard_failure is False
    assert first.soft_cost == 0
    assert first.metrics() == {
        "slide_count": 3,
        "violation_count": 0,
        "hard_failure": False,
        "hard_count": 0,
        "soft_count": 0,
        "soft_cost": 0.0,
    }


def test_deck_incompatible_viewport_renderer_and_token_fail_closed() -> None:
    token_blue = (ImportantToken(name="themeColor", value="#0055aa"),)
    token_red = (ImportantToken(name="themeColor", value="#cc0000"),)
    first = _request(0, tokens=token_blue)
    second = _request(
        1,
        viewport_width=1920,
        renderer_version="2",
        tokens=token_red,
    )

    report = evaluate_deck_consistency(((0, first), (1, second)))

    assert report.hard_failure is True
    assert [item.code for item in report.violations] == [
        "deck.important_token.conflict",
        "deck.renderer.mismatch",
        "deck.viewport.mismatch",
    ]


def test_deck_title_outliers_are_soft_and_explainable() -> None:
    report = evaluate_deck_consistency(
        (
            (0, _request(0)),
            (1, _request(1)),
            (2, _request(2, font_size=18, title_y=0.25)),
        )
    )

    assert report.hard_failure is False
    assert report.soft_cost == 2
    assert [item.code for item in report.violations] == [
        "deck.title_anchor.outlier",
        "deck.title_typography.outlier",
    ]
    assert all(item.slide_indices == (2,) for item in report.violations)
