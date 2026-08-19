"""Source-image substitution in ordinary authored generation.

The model only ever writes an asset id; this module is where that id becomes a
picture, so these tests pin both halves: a known id is inlined, and anything
else is dropped without the filesystem being touched.
"""

import asyncio
import base64
from pathlib import Path
from types import SimpleNamespace

import pytest

from services.source_images.models import SourceImageCandidate
from utils.authored_illustrations import (
    MAX_INLINE_SOURCE_IMAGE_BYTES,
    apply_authored_illustration,
    apply_authored_illustrations,
    reserve_source_images_for_slide,
)
from utils.llm_calls.author_slide import validate_authored_slide_html

_PNG_BYTES = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNgYAAAAAMAASsJTYQAAAAASUVORK5CYII="
)
_PNG_DATA_URI = "data:image/png;base64," + base64.b64encode(_PNG_BYTES).decode()


def _run(coro):
    return asyncio.run(coro)


class _Service:
    """Scriptable stand-in for ImageGenerationService."""

    def __init__(self, result=None):
        self.result = result
        self.prompts = []

    async def generate_image(self, prompt):
        self.prompts.append(prompt)
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


def _style(prompt="clean isometric 3D illustration", **extra):
    return SimpleNamespace(id="iso-blueprint", illustration_prompt=prompt, **extra)


def _candidate(tmp_path: Path, asset_id="src-1", name=None, mime="image/png"):
    file = tmp_path / (name or f"{asset_id}.png")
    file.write_bytes(_PNG_BYTES)
    return SourceImageCandidate(
        asset_id=asset_id,
        path=file,
        mime_type=mime,
        width=1200,
        height=800,
        digest="d" * 16,
        source_slide_index=2,
        source_heading="원본 제목",
    )


def _asset_html(asset_id="src-1"):
    return (
        "<section><h1>실적 요약</h1>"
        f'<img data-asset-id="{asset_id}" style="width:640px;height:427px">'
        "<p>본문</p></section>"
    )


_ILLUST_HTML = (
    "<section><h1>통제 흐름</h1>"
    '<img data-illust-prompt="isometric pipeline with a gate" '
    'style="width:640px;height:480px">'
    "<p>라벨은 HTML</p></section>"
)


def _no_filesystem(monkeypatch):
    """Trip-wire: any path read during resolution fails the test loudly."""
    calls = []

    def _boom(self, *args, **kwargs):
        calls.append(str(self))
        raise AssertionError(f"filesystem touched with model-supplied id: {self}")

    monkeypatch.setattr(Path, "is_file", _boom)
    monkeypatch.setattr(Path, "read_bytes", _boom)
    monkeypatch.setattr(Path, "stat", _boom)
    return calls


def test_known_asset_id_is_inlined_and_no_image_is_generated(tmp_path: Path):
    candidate = _candidate(tmp_path)
    service = _Service(SimpleNamespace(path=str(tmp_path / "generated.png")))

    out = _run(
        apply_authored_illustration(
            _asset_html(),
            _style(),
            service,
            source_images={candidate.asset_id: candidate},
        )
    )

    assert _PNG_DATA_URI in out
    assert 'style="width:640px;height:427px"' in out  # layout attrs preserved
    assert "<h1>실적 요약</h1>" in out
    assert service.prompts == []  # a real picture beats an invented one


def test_source_image_wins_over_an_illustration_slot(tmp_path: Path):
    candidate = _candidate(tmp_path)
    service = _Service(SimpleNamespace(path=str(tmp_path / "generated.png")))
    html = (
        '<img data-illust-prompt="isometric pipeline">'
        f'<img data-asset-id="{candidate.asset_id}">'
        "<p>본문</p>"
    )

    out = _run(
        apply_authored_illustration(
            html, _style(), service, source_images={candidate.asset_id: candidate}
        )
    )

    assert out.count("<img") == 1
    assert _PNG_DATA_URI in out
    assert "data-illust-prompt" not in out
    assert service.prompts == []


@pytest.mark.parametrize(
    "crafted_id",
    [
        "unknown-id",
        "../../../../etc/passwd",
        "..\\..\\windows\\win.ini",
        "/app_data/userConfig.json",
        "C:/Windows/System32/drivers/etc/hosts",
        "",
    ],
)
def test_unknown_or_crafted_asset_id_drops_the_slot_and_reads_no_file(
    tmp_path: Path, monkeypatch, crafted_id
):
    candidate = _candidate(tmp_path)
    service = _Service()
    _no_filesystem(monkeypatch)

    out = _run(
        apply_authored_illustration(
            _asset_html(crafted_id),
            _style(prompt=None),
            service,
            source_images={candidate.asset_id: candidate},
        )
    )

    assert "<img" not in out
    assert "<h1>실적 요약</h1>" in out and "<p>본문</p>" in out
    assert service.prompts == []


def test_unknown_asset_id_falls_back_to_the_illustration_slot(tmp_path: Path):
    candidate = _candidate(tmp_path)
    generated = tmp_path / "generated.png"
    generated.write_bytes(_PNG_BYTES)
    service = _Service(SimpleNamespace(path=str(generated)))
    html = (
        '<img data-asset-id="hallucinated">'
        '<img data-illust-prompt="isometric pipeline with a gate">'
        "<p>본문</p>"
    )

    out = _run(
        apply_authored_illustration(
            html, _style(), service, source_images={candidate.asset_id: candidate}
        )
    )

    assert out.count("<img") == 1
    assert _PNG_DATA_URI in out
    assert "data-illust-prompt" in out  # the surviving slot is the generated one
    assert len(service.prompts) == 1


def test_unreadable_candidate_degrades_to_the_illustration_fallback(tmp_path: Path):
    missing = SourceImageCandidate(
        asset_id="src-gone",
        path=tmp_path / "gone.png",
        mime_type="image/png",
        width=900,
        height=600,
        digest="e" * 16,
    )
    generated = tmp_path / "generated.png"
    generated.write_bytes(_PNG_BYTES)
    service = _Service(SimpleNamespace(path=str(generated)))
    html = (
        '<img data-asset-id="src-gone">'
        '<img data-illust-prompt="isometric pipeline with a gate">'
    )

    out = _run(
        apply_authored_illustration(
            html, _style(), service, source_images={"src-gone": missing}
        )
    )

    assert out.count("<img") == 1
    assert _PNG_DATA_URI in out
    assert len(service.prompts) == 1


def test_unsupported_mime_type_is_refused(tmp_path: Path):
    candidate = _candidate(tmp_path, name="logo.svg", mime="image/svg+xml")
    service = _Service()

    out = _run(
        apply_authored_illustration(
            _asset_html(),
            _style(prompt=None),
            service,
            source_images={candidate.asset_id: candidate},
        )
    )

    assert "<img" not in out


def test_source_image_works_for_a_style_without_an_illustration_prompt(tmp_path: Path):
    candidate = _candidate(tmp_path)
    service = _Service()

    out = _run(
        apply_authored_illustration(
            _asset_html(),
            _style(prompt=None),
            service,
            source_images={candidate.asset_id: candidate},
        )
    )

    assert _PNG_DATA_URI in out
    assert service.prompts == []


def test_a_picture_is_placed_on_only_one_slide_of_the_deck(tmp_path: Path):
    candidate = _candidate(tmp_path)
    generated = tmp_path / "generated.png"
    generated.write_bytes(_PNG_BYTES)
    service = _Service(SimpleNamespace(path=str(generated)))
    htmls = [
        _asset_html(),
        _asset_html(),  # same picture requested again
        f'<img data-asset-id="{candidate.asset_id}">'
        '<img data-illust-prompt="isometric pipeline with a gate">',
    ]

    out = _run(
        apply_authored_illustrations(
            htmls,
            _style(),
            service,
            source_images={candidate.asset_id: candidate},
        )
    )

    assert _PNG_DATA_URI in out[0]
    assert "<img" not in out[1]  # duplicate use, and no illustration slot to fall back to
    assert out[2].count("<img") == 1
    assert "data-illust-prompt" in out[2]  # fell back to generation
    assert len(service.prompts) == 1


def test_per_slide_offers_are_honoured(tmp_path: Path):
    first = _candidate(tmp_path, asset_id="src-a")
    second = _candidate(tmp_path, asset_id="src-b")
    service = _Service()

    out = _run(
        apply_authored_illustrations(
            [_asset_html("src-a"), _asset_html("src-b"), _asset_html("src-a")],
            _style(prompt=None),
            service,
            source_images=[{"src-a": first}, {"src-b": second}, {"src-b": second}],
        )
    )

    assert _PNG_DATA_URI in out[0]
    assert _PNG_DATA_URI in out[1]
    # Slide 2 was never offered src-a, so its reference resolves to nothing.
    assert "<img" not in out[2]
    assert service.prompts == []


def test_illustration_only_deck_is_unchanged_by_the_feature(tmp_path: Path):
    generated = tmp_path / "generated.png"
    generated.write_bytes(_PNG_BYTES)
    service = _Service(SimpleNamespace(path=str(generated)))

    without = _run(apply_authored_illustrations([_ILLUST_HTML], _style(), service))
    with_feature = _run(
        apply_authored_illustrations(
            [_ILLUST_HTML], _style(), _Service(SimpleNamespace(path=str(generated))),
            source_images={},
        )
    )

    assert _PNG_DATA_URI in without[0]
    assert without == with_feature
    assert len(service.prompts) == 1


def test_asset_reference_is_inert_when_source_images_are_not_in_play():
    service = _Service()
    html = _asset_html()

    out = _run(apply_authored_illustrations([html], _style(prompt=None), service))

    assert out == [html]
    assert service.prompts == []


def test_exact_reconstruction_is_untouched(tmp_path: Path, monkeypatch):
    candidate = _candidate(tmp_path)
    service = _Service()
    _no_filesystem(monkeypatch)
    exact_style = _style(reference_mode="exact_reconstruction")
    html = _asset_html()

    single = _run(
        apply_authored_illustration(
            html, exact_style, service, source_images={candidate.asset_id: candidate}
        )
    )
    deck = _run(
        apply_authored_illustrations(
            [html], exact_style, service, source_images={candidate.asset_id: candidate}
        )
    )

    assert single == html
    assert deck == [html]
    assert service.prompts == []


def test_provider_failure_still_degrades_instead_of_raising(tmp_path: Path):
    candidate = _candidate(tmp_path, asset_id="src-1")
    service = _Service(RuntimeError("provider down"))
    html = (
        '<img data-asset-id="unknown">'
        '<img data-illust-prompt="isometric pipeline with a gate">'
        "<p>본문</p>"
    )

    out = _run(
        apply_authored_illustrations(
            [html], _style(), service, source_images={candidate.asset_id: candidate}
        )
    )

    assert "<img" not in out[0]
    assert "<p>본문</p>" in out[0]


def test_raise_on_failure_only_covers_the_generated_fallback(tmp_path: Path):
    candidate = _candidate(tmp_path)
    service = _Service(RuntimeError("provider down"))

    # A placed source image never reaches the provider, so nothing can raise.
    placed = _run(
        apply_authored_illustration(
            _asset_html(),
            _style(),
            service,
            raise_on_failure=True,
            source_images={candidate.asset_id: candidate},
        )
    )
    assert _PNG_DATA_URI in placed

    with pytest.raises(RuntimeError):
        _run(
            apply_authored_illustration(
                _ILLUST_HTML,
                _style(),
                service,
                raise_on_failure=True,
                source_images={candidate.asset_id: candidate},
            )
        )


def test_reserve_source_images_for_slide_claims_one_id_per_deck(tmp_path: Path):
    first = _candidate(tmp_path, asset_id="src-a")
    second = _candidate(tmp_path, asset_id="src-b")
    candidates = {"src-a": first, "src-b": second}
    placed = set()

    slide_one = reserve_source_images_for_slide(
        '<img data-asset-id="src-a"><img data-asset-id="src-b">', candidates, placed
    )
    slide_two = reserve_source_images_for_slide(
        '<img data-asset-id="src-a">', candidates, placed
    )
    slide_three = reserve_source_images_for_slide(
        '<img data-asset-id="src-b">', candidates, placed
    )

    assert slide_one == {"src-a": first}  # one picture per slide
    assert slide_two == {}  # already placed
    assert slide_three == {"src-b": second}
    assert placed == {"src-a", "src-b"}


# --------------------------------------------------------------------------
# renderer admission across the slot's lifetime
# --------------------------------------------------------------------------


_ADMISSIBLE_SHELL = (
    '<!doctype html><html lang="ko"><head><meta charset="utf-8"></head>'
    "<body>{body}</body></html>"
)


def test_asset_id_survives_author_admission_and_is_gone_once_finalized(
    tmp_path: Path,
):
    """The marker is authoring-only, exactly like the illustration prompt.

    Admission has no ``data-asset-id`` in its attribute policy, so the author
    stage has to validate around it and the finalized document has to be free of
    it. Without both halves every slide that places a source image is rejected as
    a renderer policy violation and falls back to a placeholder.
    """
    authored = _ADMISSIBLE_SHELL.format(body=_asset_html())

    kept = validate_authored_slide_html(authored, allow_illustration_slots=True)
    assert 'data-asset-id="src-1"' in kept

    service = _Service(SimpleNamespace(path=str(_candidate(tmp_path).path)))
    substituted = _run(
        apply_authored_illustration(
            kept,
            _style(),
            service,
            source_images={"src-1": _candidate(tmp_path)},
        )
    )
    assert _PNG_DATA_URI in substituted

    finalized = validate_authored_slide_html(substituted, finalized=True)
    assert "data-asset-id" not in finalized
    assert _PNG_DATA_URI in finalized


def test_an_unresolved_asset_slot_still_finalizes(tmp_path: Path):
    """A dropped slot leaves no marker behind for admission to trip over."""
    authored = _ADMISSIBLE_SHELL.format(body=_asset_html("never-offered"))
    kept = validate_authored_slide_html(authored, allow_illustration_slots=True)
    substituted = _run(
        apply_authored_illustration(
            kept, _style(prompt=None), _Service(), source_images={}
        )
    )
    finalized = validate_authored_slide_html(substituted, finalized=True)
    assert "data-asset-id" not in finalized
    assert "<img" not in finalized


def test_a_picture_too_large_to_inline_degrades_to_the_illustration(
    tmp_path: Path,
):
    """A multi-megabyte photo out of a real PPTX must not fail the slide.

    The renderer caps both a single asset and the whole document, so inlining an
    oversized picture would raise a non-retryable policy error at finalization
    and take the deck with it. Refusing it here is the same degradation an
    unreadable candidate already gets.
    """
    oversized = tmp_path / "photo.png"
    oversized.write_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * MAX_INLINE_SOURCE_IMAGE_BYTES)
    candidate = SourceImageCandidate(
        asset_id="huge",
        path=oversized,
        mime_type="image/png",
        width=4000,
        height=3000,
        digest="e" * 16,
        source_slide_index=1,
        source_heading="현장 사진",
    )
    html = (
        "<section>"
        '<img data-asset-id="huge" style="width:640px;height:480px">'
        '<img data-illust-prompt="isometric plant" style="width:640px;height:480px">'
        "</section>"
    )
    service = _Service(SimpleNamespace(path=str(_candidate(tmp_path).path)))

    out = _run(
        apply_authored_illustration(
            html, _style(), service, source_images={"huge": candidate}
        )
    )

    # The generated illustration took the slot instead, and the oversized
    # picture is nowhere in the document.
    assert len(service.prompts) == 1
    assert out.count("<img") == 1
    assert _PNG_DATA_URI in out
