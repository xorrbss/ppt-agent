import asyncio
import json
import os
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI
from PIL import Image

import utils.authored_style_samples as authored_style_samples_module
import utils.authored_style_catalogue as authored_style_catalogue_module
from api.v1.ppt.endpoints.authored import AUTHORED_ROUTER
from utils.authored_style_catalogue import authored_style_catalogue
from utils.authored_style_samples import (
    load_style_samples,
    read_pending_style_ids,
    sample_file_path,
    style_render_digest,
    topic_prompt_digest,
)
from scripts.check_authored_style_samples import main as check_main
from scripts.check_authored_style_samples import run_check


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _write_webp(path: Path, size: tuple[int, int], color=(10, 20, 30)) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", size, color).save(path, format="WEBP", quality=80)


def _write_noise_webp(path: Path, size: tuple[int, int]) -> None:
    """A lossless-compressed random image, reliably over both byte caps."""
    path.parent.mkdir(parents=True, exist_ok=True)
    pixels = os.urandom(size[0] * size[1] * 3)
    Image.frombytes("RGB", size, pixels).save(path, format="WEBP", lossless=True)


TOPIC_TEXT = """# Demo topic

## Prompt (Korean, 10 slides)

Fictitious company business plan, ten slides.

## Fixed request parameters

- strategy: svg
"""
# Placeholder for manifest-shape tests; check-script tests use a real topic file.
TOPIC_DIGEST = "sha256:" + "d" * 64


def _write_topic(directory: Path, text: str = TOPIC_TEXT, newline: str = "\n") -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "topic.md"
    path.write_bytes(text.replace("\n", newline).encode("utf-8"))
    return path


def _base_manifest(style_id: str = "pilot", **overrides) -> dict:
    manifest = {
        "schema": "authored-style-samples/v1",
        "styleId": style_id,
        "styleDigest": "sha256:" + "a" * 64,
        "topicDigest": TOPIC_DIGEST,
        "demoTopicId": "business-plan-v1",
        "canvas": {"width": 1280, "height": 720},
        "generatedAt": "2026-10-01T09:00:00Z",
        "generatorCommit": "a" * 40,
        "slides": [{"title": "Cover"}, {"title": "Market"}],
    }
    manifest.update(overrides)
    return manifest


def _write_manifest(directory: Path, manifest: dict) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")


def _build_valid_deck(
    samples_root: Path,
    style_id: str = "pilot",
    *,
    digest: str = "sha256:" + "b" * 64,
    topic_digest: str = TOPIC_DIGEST,
    generator_commit: str = "c" * 40,
    slide_titles=("Cover", "Market"),
) -> dict:
    directory = samples_root / style_id
    manifest = _base_manifest(
        style_id,
        styleDigest=digest,
        topicDigest=topic_digest,
        generatorCommit=generator_commit,
        slides=[{"title": title} for title in slide_titles],
    )
    _write_manifest(directory, manifest)
    for index, _ in enumerate(slide_titles, start=1):
        _write_webp(directory / f"{index:02d}.webp", (1280, 720))
        _write_webp(directory / f"{index:02d}.thumb.webp", (320, 180))
    return manifest


def _write_style_yaml(
    styles_dir: Path,
    style_id: str,
    *,
    brief: str = "Brief content",
    description: str = "Test style",
    reference_image_rel: str | None = None,
    svg_overrides: dict | None = None,
) -> Path:
    lines = [
        f"id: {style_id}",
        f"name: {style_id.title()}",
        f"description: {description}",
        "preview:",
        "  bg: '#FFFFFF'",
        "  accent: '#000000'",
        "design_tokens:",
        "  primary: '#000000'",
        f"brief: {brief}",
    ]
    if reference_image_rel:
        lines += ["reference_images:", "  cover:", f"    - {reference_image_rel}"]
    if svg_overrides:
        lines.append("svg_overrides:")
        for key, values in svg_overrides.items():
            lines.append(f"  {key}:")
            lines += [f"    - {value}" for value in values]
    path = styles_dir / f"{style_id}.yaml"
    _write(path, "\n".join(lines) + "\n")
    return path


# ---------------------------------------------------------------------------
# load_style_samples: manifest rules (§4, runtime-validated)
# ---------------------------------------------------------------------------


def test_load_style_samples_valid_manifest(tmp_path):
    _build_valid_deck(tmp_path, "pilot")

    samples = load_style_samples("pilot", tmp_path)

    assert samples is not None
    assert samples.style_id == "pilot"
    assert samples.canvas.width == 1280
    assert samples.canvas.height == 720
    assert [slide.index for slide in samples.slides] == [1, 2]
    assert [slide.title for slide in samples.slides] == ["Cover", "Market"]


def test_load_style_samples_missing_directory_returns_none_silently(tmp_path, caplog):
    with caplog.at_level("WARNING"):
        result = load_style_samples("does-not-exist", tmp_path)
    assert result is None
    assert caplog.text == ""


def test_load_style_samples_rejects_bad_id_shape(tmp_path):
    assert load_style_samples("Bad_ID", tmp_path) is None
    assert load_style_samples("-leading-dash", tmp_path) is None


@pytest.mark.parametrize(
    "overrides",
    [
        {"schema": "wrong-schema"},
        {"styleId": "other-id"},
        {"canvas": {"width": 100, "height": 720}},
        {"canvas": {"width": 900, "height": 700}},
        {"canvas": {"width": "1280", "height": 720}},
        {"slides": []},
        {"slides": [{"title": "x"} for _ in range(25)]},
        {"slides": [{"title": ""}]},
        {"slides": [{"title": "x" * 81}]},
        {"generatorCommit": "not-hex"},
        {"generatorCommit": "a" * 39},
        {"topicDigest": "not-a-digest"},
        {"topicDigest": ""},
        {"styleDigest": "sha256:" + "A" * 64},
        {"styleDigest": ""},
        {"demoTopicId": ""},
        {"generatedAt": ""},
    ],
)
def test_load_style_samples_rejects_invalid_manifests(tmp_path, overrides, caplog):
    manifest = _base_manifest("pilot", **overrides)
    _write_manifest(tmp_path / "pilot", manifest)

    with caplog.at_level("WARNING"):
        result = load_style_samples("pilot", tmp_path)

    assert result is None
    assert "pilot" in caplog.text


def test_load_style_samples_rejects_non_json_manifest(tmp_path, caplog):
    directory = tmp_path / "pilot"
    directory.mkdir()
    (directory / "manifest.json").write_text("{not json", encoding="utf-8")

    with caplog.at_level("WARNING"):
        result = load_style_samples("pilot", tmp_path)

    assert result is None
    assert "readable JSON" in caplog.text


def test_load_style_samples_rejects_non_object_manifest(tmp_path, caplog):
    directory = tmp_path / "pilot"
    directory.mkdir()
    (directory / "manifest.json").write_text("[1, 2, 3]", encoding="utf-8")

    with caplog.at_level("WARNING"):
        result = load_style_samples("pilot", tmp_path)

    assert result is None
    assert "JSON object" in caplog.text


# ---------------------------------------------------------------------------
# sample_file_path
# ---------------------------------------------------------------------------


def test_sample_file_path_returns_existing_files(tmp_path):
    _build_valid_deck(tmp_path, "pilot")

    full = sample_file_path("pilot", 1, "full", tmp_path)
    thumb = sample_file_path("pilot", 1, "thumb", tmp_path)

    assert full == (tmp_path / "pilot" / "01.webp").resolve()
    assert thumb == (tmp_path / "pilot" / "01.thumb.webp").resolve()


def test_sample_file_path_missing_file_returns_none(tmp_path):
    _build_valid_deck(tmp_path, "pilot")

    assert sample_file_path("pilot", 9, "full", tmp_path) is None


def test_sample_file_path_bad_id_shape_returns_none(tmp_path):
    assert sample_file_path("Bad_ID", 1, "full", tmp_path) is None


def test_sample_file_path_symlink_returns_none(tmp_path):
    _build_valid_deck(tmp_path, "pilot")
    outside = tmp_path / "outside.webp"
    _write_webp(outside, (1280, 720))
    link = tmp_path / "pilot" / "03.webp"
    try:
        link.symlink_to(outside)
    except OSError as exc:
        pytest.skip(f"Symlinks are unavailable: {exc}")

    manifest = json.loads((tmp_path / "pilot" / "manifest.json").read_text(encoding="utf-8"))
    manifest["slides"].append({"title": "Extra"})
    (tmp_path / "pilot" / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

    assert sample_file_path("pilot", 3, "full", tmp_path) is None


# ---------------------------------------------------------------------------
# style_render_digest (§5)
# ---------------------------------------------------------------------------


def test_style_render_digest_stable_across_line_endings(tmp_path):
    styles_dir = tmp_path / "styles"
    assets_dir = tmp_path / "authored_style_assets"
    path = _write_style_yaml(styles_dir, "pilot")

    digest_lf = style_render_digest(path, assets_dir)

    crlf_content = path.read_text(encoding="utf-8").replace("\n", "\r\n")
    path.write_bytes(crlf_content.encode("utf-8"))
    digest_crlf = style_render_digest(path, assets_dir)

    assert digest_lf == digest_crlf
    assert digest_lf.startswith("sha256:")


def test_style_render_digest_ignores_display_only_edits(tmp_path):
    styles_dir = tmp_path / "styles"
    assets_dir = tmp_path / "authored_style_assets"
    path = _write_style_yaml(styles_dir, "pilot", description="Original description")
    digest_before = style_render_digest(path, assets_dir)

    _write_style_yaml(styles_dir, "pilot", description="A totally different description")
    digest_after = style_render_digest(path, assets_dir)

    assert digest_before == digest_after


def test_style_render_digest_changes_on_brief_edit(tmp_path):
    styles_dir = tmp_path / "styles"
    assets_dir = tmp_path / "authored_style_assets"
    path = _write_style_yaml(styles_dir, "pilot", brief="Original brief")
    digest_before = style_render_digest(path, assets_dir)

    _write_style_yaml(styles_dir, "pilot", brief="Different brief")
    digest_after = style_render_digest(path, assets_dir)

    assert digest_before != digest_after


def test_style_render_digest_changes_on_svg_overrides_edit(tmp_path):
    styles_dir = tmp_path / "styles"
    assets_dir = tmp_path / "authored_style_assets"
    path = _write_style_yaml(styles_dir, "pilot", svg_overrides={"common_guidance": ["a"]})
    digest_before = style_render_digest(path, assets_dir)

    _write_style_yaml(styles_dir, "pilot", svg_overrides={"common_guidance": ["b"]})
    digest_after = style_render_digest(path, assets_dir)

    assert digest_before != digest_after


def test_style_render_digest_changes_on_reference_image_bytes_edit(tmp_path):
    styles_dir = tmp_path / "styles"
    assets_dir = tmp_path / "authored_style_assets"
    path = _write_style_yaml(styles_dir, "pilot", reference_image_rel="pilot/cover.png")
    (assets_dir / "pilot" / "cover.png").parent.mkdir(parents=True, exist_ok=True)
    (assets_dir / "pilot" / "cover.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"A")
    digest_before = style_render_digest(path, assets_dir)

    (assets_dir / "pilot" / "cover.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"B")
    digest_after = style_render_digest(path, assets_dir)

    assert digest_before != digest_after


def test_style_render_digest_raises_on_reference_path_escape(tmp_path):
    styles_dir = tmp_path / "styles"
    assets_dir = tmp_path / "authored_style_assets"
    assets_dir.mkdir(parents=True)
    path = _write_style_yaml(styles_dir, "pilot", reference_image_rel="../outside.png")
    (tmp_path / "outside.png").write_bytes(b"\x89PNG\r\n\x1a\n")

    with pytest.raises(ValueError, match="stay inside"):
        style_render_digest(path, assets_dir)


# ---------------------------------------------------------------------------
# Catalogue: presence/absence and alias omission (D4)
# ---------------------------------------------------------------------------


class _FakeStyle:
    def __init__(self, style_id: str):
        self.id = style_id

    def public_dict(self):
        return {
            "id": self.id,
            "name": self.id,
            "description": "d",
            "category": "general",
            "tags": [],
            "use_cases": [],
            "preview": {"bg": "#FFFFFF", "accent": "#000000"},
        }


def test_authored_style_catalogue_attaches_samples_when_present(tmp_path, monkeypatch):
    samples_root = tmp_path / "samples"
    monkeypatch.setattr(authored_style_samples_module, "AUTHORED_STYLE_SAMPLES_DIRECTORY", samples_root)
    monkeypatch.setattr(authored_style_catalogue_module, "AUTHORED_STYLE_ALIASES", {"alpha-old": "alpha"})
    _build_valid_deck(samples_root, "alpha", slide_titles=("Cover", "Market"))

    catalogue = authored_style_catalogue([_FakeStyle("alpha"), _FakeStyle("beta")])
    by_id = {style["id"]: style for style in catalogue}

    version = load_style_samples("alpha", tmp_path / "samples").cache_version
    assert "samples" in by_id["alpha"]
    samples = by_id["alpha"]["samples"]
    assert samples["canvas"] == {"width": 1280, "height": 720}
    assert samples["slides"][0] == {
        "index": 1,
        "title": "Cover",
        "thumb": f"/api/v1/ppt/authored/styles/alpha/samples/1?size=thumb&v={version}",
        "full": f"/api/v1/ppt/authored/styles/alpha/samples/1?size=full&v={version}",
    }


def test_authored_style_catalogue_omits_samples_when_absent(tmp_path, monkeypatch):
    samples_root = tmp_path / "samples"
    monkeypatch.setattr(authored_style_samples_module, "AUTHORED_STYLE_SAMPLES_DIRECTORY", samples_root)
    monkeypatch.setattr(authored_style_catalogue_module, "AUTHORED_STYLE_ALIASES", {})

    catalogue = authored_style_catalogue([_FakeStyle("beta")])

    assert "samples" not in catalogue[0]


def test_authored_style_catalogue_never_attaches_samples_via_alias_directory(tmp_path, monkeypatch):
    samples_root = tmp_path / "samples"
    monkeypatch.setattr(authored_style_samples_module, "AUTHORED_STYLE_SAMPLES_DIRECTORY", samples_root)
    monkeypatch.setattr(authored_style_catalogue_module, "AUTHORED_STYLE_ALIASES", {"alpha-old": "alpha"})
    # Only the alias id has samples on disk; the canonical id does not.
    _build_valid_deck(samples_root, "alpha-old", slide_titles=("Cover",))

    catalogue = authored_style_catalogue([_FakeStyle("alpha")])

    assert "samples" not in catalogue[0]


# ---------------------------------------------------------------------------
# Route: 200 and 404 behaviour
# ---------------------------------------------------------------------------


def _fetch(path: str) -> httpx.Response:
    app = FastAPI()
    app.include_router(AUTHORED_ROUTER, prefix="/api/v1/ppt")

    async def go():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            return await client.get(path)

    return asyncio.run(go())


def test_get_authored_style_sample_returns_full_and_thumb(tmp_path, monkeypatch):
    samples_root = tmp_path / "samples"
    monkeypatch.setattr(authored_style_samples_module, "AUTHORED_STYLE_SAMPLES_DIRECTORY", samples_root)
    _build_valid_deck(samples_root, "pilot")

    full_response = _fetch("/api/v1/ppt/authored/styles/pilot/samples/1?size=full")
    thumb_response = _fetch("/api/v1/ppt/authored/styles/pilot/samples/1?size=thumb")
    default_response = _fetch("/api/v1/ppt/authored/styles/pilot/samples/1")

    for response in (full_response, thumb_response, default_response):
        assert response.status_code == 200
        assert response.headers["content-type"] == "image/webp"
        assert response.headers["cache-control"] == "private, max-age=86400"


def test_get_authored_style_sample_404_unknown_id(tmp_path, monkeypatch):
    samples_root = tmp_path / "samples"
    monkeypatch.setattr(authored_style_samples_module, "AUTHORED_STYLE_SAMPLES_DIRECTORY", samples_root)

    response = _fetch("/api/v1/ppt/authored/styles/unknown-style/samples/1")

    assert response.status_code == 404


def test_get_authored_style_sample_404_bad_id_shape(tmp_path, monkeypatch):
    samples_root = tmp_path / "samples"
    monkeypatch.setattr(authored_style_samples_module, "AUTHORED_STYLE_SAMPLES_DIRECTORY", samples_root)
    _build_valid_deck(samples_root, "pilot")

    response = _fetch("/api/v1/ppt/authored/styles/Bad_ID/samples/1")

    assert response.status_code == 404


def test_get_authored_style_sample_404_out_of_range_index(tmp_path, monkeypatch):
    samples_root = tmp_path / "samples"
    monkeypatch.setattr(authored_style_samples_module, "AUTHORED_STYLE_SAMPLES_DIRECTORY", samples_root)
    _build_valid_deck(samples_root, "pilot", slide_titles=("Cover", "Market"))

    response = _fetch("/api/v1/ppt/authored/styles/pilot/samples/3")

    assert response.status_code == 404


def test_get_authored_style_sample_404_missing_file(tmp_path, monkeypatch):
    samples_root = tmp_path / "samples"
    monkeypatch.setattr(authored_style_samples_module, "AUTHORED_STYLE_SAMPLES_DIRECTORY", samples_root)
    _build_valid_deck(samples_root, "pilot")
    (samples_root / "pilot" / "01.webp").unlink()

    response = _fetch("/api/v1/ppt/authored/styles/pilot/samples/1?size=full")

    assert response.status_code == 404


# ---------------------------------------------------------------------------
# Check script
# ---------------------------------------------------------------------------


def _run(tmp_path, samples_root, styles_dir, assets_dir=None, topic_file=None):
    return run_check(
        samples_root=samples_root,
        styles_directory=styles_dir,
        assets_directory=assets_dir or (tmp_path / "authored_style_assets"),
        repo_root=tmp_path,
        topic_file=topic_file or _write_topic(tmp_path),
    )


def _real_topic_digest(tmp_path) -> str:
    return topic_prompt_digest(_write_topic(tmp_path))


def _canonical_style(styles_dir, style_id, brief="Brief content"):
    _write_style_yaml(styles_dir, style_id, brief=brief)


def test_check_script_empty_root_is_not_a_problem(tmp_path):
    problems, report = _run(tmp_path, tmp_path / "samples", tmp_path / "styles")
    assert problems == []
    assert report == []


def test_check_script_missing_root_is_not_a_problem(tmp_path):
    problems, report = _run(tmp_path, tmp_path / "does-not-exist", tmp_path / "styles")
    assert problems == []
    assert report == []


def test_check_script_reports_stale_digest(tmp_path):
    styles_dir = tmp_path / "styles"
    samples_root = tmp_path / "samples"
    style_path = _write_style_yaml(styles_dir, "pilot", brief="original")
    digest = style_render_digest(style_path, tmp_path / "authored_style_assets")
    _build_valid_deck(samples_root, "pilot", digest=digest)
    # Style changes after generation; the recorded digest goes stale.
    _write_style_yaml(styles_dir, "pilot", brief="changed after generation")

    problems, _ = _run(tmp_path, samples_root, styles_dir)

    assert any("stale" in p for p in problems)


def test_check_script_reports_extra_file(tmp_path):
    styles_dir = tmp_path / "styles"
    samples_root = tmp_path / "samples"
    style_path = _write_style_yaml(styles_dir, "pilot")
    digest = style_render_digest(style_path, tmp_path / "authored_style_assets")
    _build_valid_deck(samples_root, "pilot", digest=digest)
    (samples_root / "pilot" / "stray.txt").write_text("stray", encoding="utf-8")

    problems, _ = _run(tmp_path, samples_root, styles_dir)

    assert any("unexpected file" in p for p in problems)


def test_check_script_ignores_os_metadata_files(tmp_path):
    styles_dir = tmp_path / "styles"
    samples_root = tmp_path / "samples"
    style_path = _write_style_yaml(styles_dir, "pilot")
    digest = style_render_digest(style_path, tmp_path / "authored_style_assets")
    _build_valid_deck(samples_root, "pilot", digest=digest, topic_digest=_real_topic_digest(tmp_path))
    (samples_root / "pilot" / "Thumbs.db").write_bytes(b"\x00")
    (samples_root / "pilot" / ".DS_Store").write_bytes(b"\x00")

    problems, _ = _run(tmp_path, samples_root, styles_dir)

    assert problems == []


def test_check_script_reports_bad_magic_bytes(tmp_path):
    styles_dir = tmp_path / "styles"
    samples_root = tmp_path / "samples"
    style_path = _write_style_yaml(styles_dir, "pilot")
    digest = style_render_digest(style_path, tmp_path / "authored_style_assets")
    _build_valid_deck(samples_root, "pilot", digest=digest)
    (samples_root / "pilot" / "01.webp").write_bytes(b"not a webp file at all")

    problems, _ = _run(tmp_path, samples_root, styles_dir)

    assert any("magic bytes" in p for p in problems)


def test_check_script_reports_oversize_file(tmp_path):
    styles_dir = tmp_path / "styles"
    samples_root = tmp_path / "samples"
    style_path = _write_style_yaml(styles_dir, "pilot")
    digest = style_render_digest(style_path, tmp_path / "authored_style_assets")
    _build_valid_deck(samples_root, "pilot", digest=digest)
    _write_noise_webp(samples_root / "pilot" / "01.thumb.webp", (320, 180))

    problems, _ = _run(tmp_path, samples_root, styles_dir)

    assert any("byte cap" in p for p in problems)


def test_check_script_reports_alias_directory(tmp_path, monkeypatch):
    import scripts.check_authored_style_samples as check_module

    monkeypatch.setattr(check_module, "AUTHORED_STYLE_ALIASES", {"alpha-old": "alpha"})
    styles_dir = tmp_path / "styles"
    samples_root = tmp_path / "samples"
    _canonical_style(styles_dir, "alpha")
    style_path = styles_dir / "alpha.yaml"
    digest = style_render_digest(style_path, tmp_path / "authored_style_assets")
    _build_valid_deck(samples_root, "alpha", digest=digest)
    _build_valid_deck(samples_root, "alpha-old", digest=digest)

    problems, _ = _run(tmp_path, samples_root, styles_dir)

    assert any("alpha-old" in p and "not a public canonical style" in p for p in problems)


def test_check_script_reports_partial_set(tmp_path):
    styles_dir = tmp_path / "styles"
    samples_root = tmp_path / "samples"
    _canonical_style(styles_dir, "alpha")
    _canonical_style(styles_dir, "beta")
    style_path = styles_dir / "alpha.yaml"
    digest = style_render_digest(style_path, tmp_path / "authored_style_assets")
    _build_valid_deck(samples_root, "alpha", digest=digest)
    # "beta" is canonical but has no sample directory (D2 all-or-nothing).

    problems, _ = _run(tmp_path, samples_root, styles_dir)

    assert any("beta" in p and "missing sample directory" in p for p in problems)


def test_check_script_passes_for_a_complete_valid_set(tmp_path):
    styles_dir = tmp_path / "styles"
    samples_root = tmp_path / "samples"
    _canonical_style(styles_dir, "alpha")
    style_path = styles_dir / "alpha.yaml"
    digest = style_render_digest(style_path, tmp_path / "authored_style_assets")
    _build_valid_deck(samples_root, "alpha", digest=digest, topic_digest=_real_topic_digest(tmp_path))

    problems, report = _run(tmp_path, samples_root, styles_dir)

    assert problems == []
    assert any("alpha" in line for line in report)


def test_check_script_main_prints_no_samples_and_exits_zero(tmp_path, capsys):
    exit_code = check_main(
        [
            "--samples-root",
            str(tmp_path / "does-not-exist"),
            "--styles-dir",
            str(tmp_path / "styles"),
        ]
    )

    captured = capsys.readouterr()
    assert exit_code == 0
    assert "no authored style samples" in captured.out


def test_check_script_main_exits_one_on_problems(tmp_path, capsys):
    styles_dir = tmp_path / "styles"
    samples_root = tmp_path / "samples"
    _canonical_style(styles_dir, "alpha")
    _build_valid_deck(samples_root, "alpha", digest="sha256:" + "0" * 64)

    exit_code = check_main(
        [
            "--samples-root",
            str(samples_root),
            "--styles-dir",
            str(styles_dir),
            "--assets-dir",
            str(tmp_path / "authored_style_assets"),
        ]
    )

    captured = capsys.readouterr()
    assert exit_code == 1
    assert "stale" in captured.out


def test_check_script_against_real_repo_samples_root_is_clean_or_absent():
    """Guards against a partial/broken real samples root ever landing on this branch."""
    problems, _report = run_check()
    assert problems == []


# ---------------------------------------------------------------------------
# Topic digest, cache version, pending list
# ---------------------------------------------------------------------------


def test_topic_digest_ignores_line_endings_and_surrounding_notes(tmp_path):
    lf = _write_topic(tmp_path / "lf")
    crlf = _write_topic(tmp_path / "crlf", newline="\r\n")
    notes = _write_topic(tmp_path / "notes", TOPIC_TEXT.replace("# Demo topic", "# Demo topic (edited heading)"))

    assert topic_prompt_digest(lf) == topic_prompt_digest(crlf) == topic_prompt_digest(notes)


def test_topic_digest_changes_when_the_prompt_changes(tmp_path):
    before = topic_prompt_digest(_write_topic(tmp_path / "a"))
    after = topic_prompt_digest(_write_topic(tmp_path / "b", TOPIC_TEXT.replace("ten slides", "twelve slides")))
    assert before != after


def test_topic_digest_rejects_a_file_without_prompt_markers(tmp_path):
    path = tmp_path / "topic.md"
    path.write_text("# nothing here\n", encoding="utf-8")
    with pytest.raises(ValueError):
        topic_prompt_digest(path)


def test_cache_version_follows_style_and_topic_digests(tmp_path):
    _build_valid_deck(tmp_path, "pilot")
    base = load_style_samples("pilot", tmp_path)
    _build_valid_deck(tmp_path, "pilot", digest="sha256:" + "e" * 64)
    style_changed = load_style_samples("pilot", tmp_path)
    _build_valid_deck(tmp_path, "pilot", topic_digest="sha256:" + "f" * 64)
    topic_changed = load_style_samples("pilot", tmp_path)

    assert len(base.cache_version) == 12
    assert len({base.cache_version, style_changed.cache_version, topic_changed.cache_version}) == 3


def test_catalogue_sample_urls_carry_the_cache_version(tmp_path, monkeypatch):
    styles_dir = tmp_path / "styles"
    _canonical_style(styles_dir, "alpha")
    _build_valid_deck(tmp_path / "samples", "alpha")
    monkeypatch.setattr(authored_style_samples_module, "AUTHORED_STYLE_SAMPLES_DIRECTORY", tmp_path / "samples")
    from utils.authored_styles import load_authored_styles

    entry = next(s for s in authored_style_catalogue(load_authored_styles(styles_dir)) if s["id"] == "alpha")
    version = load_style_samples("alpha", tmp_path / "samples").cache_version

    assert entry["samples"]["slides"][0]["thumb"].endswith(f"?size=thumb&v={version}")
    assert entry["samples"]["slides"][0]["full"].endswith(f"?size=full&v={version}")


def test_route_ignores_the_cache_version_parameter(tmp_path, monkeypatch):
    _build_valid_deck(tmp_path, "pilot")
    monkeypatch.setattr(authored_style_samples_module, "AUTHORED_STYLE_SAMPLES_DIRECTORY", tmp_path)
    app = FastAPI()
    app.include_router(AUTHORED_ROUTER, prefix="/api/v1/ppt")

    async def fetch():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            return await client.get("/api/v1/ppt/authored/styles/pilot/samples/1?size=thumb&v=0123456789ab")

    response = asyncio.run(fetch())
    assert response.status_code == 200
    assert response.headers["content-type"] == "image/webp"


def test_check_script_reports_stale_topic(tmp_path):
    styles_dir = tmp_path / "styles"
    samples_root = tmp_path / "samples"
    _canonical_style(styles_dir, "alpha")
    digest = style_render_digest(styles_dir / "alpha.yaml", tmp_path / "authored_style_assets")
    _build_valid_deck(samples_root, "alpha", digest=digest, topic_digest=_real_topic_digest(tmp_path))
    edited = _write_topic(tmp_path / "edited", TOPIC_TEXT.replace("ten slides", "twelve slides"))

    problems, _ = _run(tmp_path, samples_root, styles_dir, topic_file=edited)

    assert any("topicDigest is stale" in p for p in problems)


def test_pending_list_parsing(tmp_path):
    (tmp_path / "pending.txt").write_text("alpha\n# comment\n\n beta # trailing\n", encoding="utf-8")
    assert read_pending_style_ids(tmp_path) == frozenset({"alpha", "beta"})
    assert read_pending_style_ids(tmp_path / "missing") == frozenset()


def test_check_script_allows_a_pending_style_to_have_no_samples(tmp_path):
    styles_dir = tmp_path / "styles"
    samples_root = tmp_path / "samples"
    _canonical_style(styles_dir, "alpha")
    _canonical_style(styles_dir, "beta")
    digest = style_render_digest(styles_dir / "alpha.yaml", tmp_path / "authored_style_assets")
    _build_valid_deck(samples_root, "alpha", digest=digest, topic_digest=_real_topic_digest(tmp_path))
    (samples_root / "pending.txt").write_text("beta\n", encoding="utf-8")

    problems, report = _run(tmp_path, samples_root, styles_dir)

    assert problems == []
    assert any("beta" in line and "pending" in line for line in report)


def test_check_script_rejects_pending_entries_that_are_stale_or_unknown(tmp_path):
    styles_dir = tmp_path / "styles"
    samples_root = tmp_path / "samples"
    _canonical_style(styles_dir, "alpha")
    digest = style_render_digest(styles_dir / "alpha.yaml", tmp_path / "authored_style_assets")
    _build_valid_deck(samples_root, "alpha", digest=digest, topic_digest=_real_topic_digest(tmp_path))
    (samples_root / "pending.txt").write_text("alpha\nghost\n", encoding="utf-8")

    problems, _ = _run(tmp_path, samples_root, styles_dir)

    assert any("alpha" in p and "remove it from pending.txt" in p for p in problems)
    assert any("ghost" in p and "not a public canonical style" in p for p in problems)
