"""Run-6 empty panels are composition findings, never removed source content."""

import copy
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from services.source_content import pipeline
from services.source_content.contracts import SourceContentManifestPin
from services.source_content.outline import SourceDocumentOutlineBuilder
from utils.authored_empty_media import find_empty_media_panels

FIXTURES = Path(__file__).parent / "fixtures" / "empty_media_run6"


def _fixture(page):
    html = (FIXTURES / f"slide-{page}-baseline.html").read_text(encoding="utf-8")
    metrics = json.loads((FIXTURES / f"slide-{page}-baseline-metrics.json").read_text(encoding="utf-8"))
    return html, metrics


def _panel():
    _, metrics = _fixture(31)
    return copy.deepcopy(next(item for item in metrics["elements"] if item["qid"] == "q-image-card-30"))


def _find(html, item, **kwargs):
    return find_empty_media_panels(html, [item], width=1280, height=720, **kwargs)


@pytest.mark.parametrize("qid,role", [("q-photo-panel-7", "decoration"), ("unrelated", "image"), ("media.slot", "decoration")])
def test_rule_generalizes_beyond_fixture_names_and_white_color(qid, role):
    item = _panel()
    item.update(qid=qid, semantic_role=role)
    item["computed_style"]["backgroundColor"] = "rgb(30, 40, 50)"
    html = f'<div data-qid="{qid}" data-semantic-role="{role}"> \n </div>'
    assert _find(html, item) == (qid,)


@pytest.mark.parametrize("content", ["Source text", "<span>Source text</span>", '<img src="data:image/png;base64,AA==">', "<svg></svg>", "<div></div>"])
def test_text_media_and_css_drawing_children_are_not_empty(content):
    item = _panel()
    assert not _find(f'<div data-qid="q-image-card-30">{content}</div>', item)


@pytest.mark.parametrize("attribute", ['data-source-block-id="source-1"', 'data-source-image-id="asset-1"', 'data-asset-id="asset-1"', 'data-illust-prompt="photo"'])
def test_bound_source_or_pending_image_is_not_flagged(attribute):
    assert not _find(f'<div data-qid="q-image-card-30" {attribute}></div>', _panel())


@pytest.mark.parametrize("change", [
    {"intentional": True}, {"css_visible": False}, {"has_owned_paint": False},
    {"effective_opacity": 0}, {"viewport_intersection_ratio": 0.1},
    {"rendered_text": "Painted caption"}, {"owned_text": "Source caption"},
    {"bbox": {"width": 100, "height": 100}}, {"bbox": {"width": float("nan"), "height": 300}},
    {"computed_style": {"backgroundImage": "url(source.png)"}},
    {"computed_style": {"backgroundImage": "none", "maskImage": "url(mask.svg)"}},
    {"computed_style": None},
])
def test_missing_or_nonempty_paint_evidence_does_not_flag(change):
    item = _panel()
    item.update(change)
    assert not _find('<div data-qid="q-image-card-30"></div>', item)


def test_exact_reference_and_unmarked_decoration_are_preserved():
    item = _panel()
    html = '<div data-qid="q-image-card-30"></div>'
    assert not _find(html, item, exact_reconstruction=True)
    item["unintentional"] = True
    assert _find(html, item, exact_reconstruction=True)
    item.update(qid="q-decoration", semantic_role="decoration")
    assert not _find('<div data-qid="q-decoration"></div>', item)


def test_ambiguous_markup_and_missing_measurements_do_not_flag():
    assert not _find('<div data-qid="q-image-card-30">', _panel())
    assert not find_empty_media_panels(
        '<div data-qid="q-image-card-30"></div>', [], width=1280, height=720,
    )


@pytest.mark.parametrize("text_mode", ["verbatim", "rephrase"])
@pytest.mark.parametrize("image_pages,expected", [((), "1, 2"), ((2,), "1"), ((1, 2), None)])
def test_source_reflow_prompt_uses_manifest_occurrences_and_preserves_text(text_mode, image_pages, expected):
    units = []
    for page in (1, 2):
        blocks = [{"sourceBlockId": f"text-{page}", "kind": "text", "order": 0,
                   "semanticRole": "title", "payload": {"text": f"Source {page}"}}]
        if page in image_pages:
            blocks.append({"sourceBlockId": f"image-{page}", "kind": "image", "order": 1,
                           "semanticRole": "image", "payload": {"assetId": "shared-image"}})
        units.append({"sourceUnitId": f"unit-{page}", "sourceIndex": page - 1, "blocks": blocks})
    manifest = {"manifestId": "manifest", "manifestDigest": "digest", "units": units}
    outline = SourceDocumentOutlineBuilder().build(
        manifest=manifest, manifest_pin=SourceContentManifestPin(manifest_id="manifest", digest="digest"),
    )
    # Use a model-like object while retaining the real manifest projection.
    request = SimpleNamespace(
        _source_content_manifest=SimpleNamespace(reference_snapshot_id="snapshot", model_dump=lambda **_: manifest),
        _source_document_outline=outline, content_policy=SimpleNamespace(text=text_mode),
        instructions="Use the selected template.",
    )
    assert pipeline.prepare_source_content_authored_reflow(request) == "snapshot"
    assert request.slides_markdown == ["# Source 1", "# Source 2"]
    assert request.n_slides == 2
    assert request.instructions.startswith("Use the selected template.")
    clause = "do not create image placeholders, image cards, or empty media panels"
    if expected is None:
        assert clause not in request.instructions
    else:
        assert f"zero on output pages {expected}." in request.instructions
        assert clause in request.instructions
        assert "Do not reserve an image column or blank media area" in request.instructions
