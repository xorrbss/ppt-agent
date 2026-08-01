from pathlib import Path

from PIL import Image
import pytest
import yaml

from utils.authored_styles import (
    AuthoredExactReconstructionUnavailableError,
    load_authored_styles,
    resolve_authored_style,
    validate_exact_reconstruction_style,
)


def _enable_exact(monkeypatch, *style_ids: str) -> None:
    monkeypatch.setenv("AUTHORED_EXACT_RECONSTRUCTION_ENABLED", "true")
    monkeypatch.setenv("AUTHORED_EXACT_STYLE_IDS", ",".join(style_ids))


def _write_style(
    styles_dir: Path,
    style_id: str,
    *,
    mode: str = "exact_reconstruction",
    entries: list[object] | None = None,
) -> None:
    data = {
        "id": style_id,
        "name": style_id.title(),
        "description": "Test preset",
        "preview": {"bg": "#FFFFFF", "accent": "#000000"},
        "brief": "private brief",
        "reference_mode": mode,
    }
    if entries is not None:
        data["slide_reference_images"] = entries
    (styles_dir / f"{style_id}.yaml").write_text(
        yaml.safe_dump(data, sort_keys=False),
        encoding="utf-8",
    )


def _write_image(
    path: Path,
    *,
    image_format: str = "PNG",
    size: tuple[int, int] = (1280, 720),
    color: str = "#123456",
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", size, color).save(path, format=image_format)


def _preset_dirs(tmp_path: Path) -> tuple[Path, Path]:
    styles_dir = tmp_path / "styles"
    assets_dir = tmp_path / "authored_style_assets"
    styles_dir.mkdir()
    assets_dir.mkdir()
    return styles_dir, assets_dir


def test_exact_gate_defaults_off_and_never_silently_falls_back(
    tmp_path, monkeypatch
):
    monkeypatch.delenv("AUTHORED_EXACT_RECONSTRUCTION_ENABLED", raising=False)
    monkeypatch.delenv("AUTHORED_EXACT_STYLE_IDS", raising=False)
    styles_dir, assets_dir = _preset_dirs(tmp_path)
    _write_style(styles_dir, "default", mode="style_reference", entries=None)
    _write_image(assets_dir / "exact" / "source.png")
    _write_style(styles_dir, "exact", entries=["exact/source.png"])

    assert [style.id for style in load_authored_styles(styles_dir)] == ["default"]
    assert resolve_authored_style("default", styles_dir).id == "default"
    with pytest.raises(
        AuthoredExactReconstructionUnavailableError,
        match="disabled or not allow-listed",
    ):
        resolve_authored_style("exact", styles_dir)


def test_exact_requires_both_global_gate_and_style_allow_list(tmp_path, monkeypatch):
    styles_dir, assets_dir = _preset_dirs(tmp_path)
    _write_image(assets_dir / "exact" / "source.png")
    _write_style(styles_dir, "exact", entries=["exact/source.png"])

    monkeypatch.setenv("AUTHORED_EXACT_RECONSTRUCTION_ENABLED", "true")
    monkeypatch.setenv("AUTHORED_EXACT_STYLE_IDS", "another-style")

    assert load_authored_styles(styles_dir) == []
    with pytest.raises(AuthoredExactReconstructionUnavailableError):
        resolve_authored_style("exact", styles_dir)


@pytest.mark.parametrize(
    ("suffix", "image_format"),
    [("png", "PNG"), ("jpg", "JPEG"), ("webp", "WEBP")],
)
def test_enabled_exact_accepts_supported_magic_and_keeps_sources_private(
    tmp_path, monkeypatch, suffix, image_format
):
    _enable_exact(monkeypatch, "exact")
    styles_dir, assets_dir = _preset_dirs(tmp_path)
    relative = f"exact/source.{suffix}"
    _write_image(assets_dir / relative, image_format=image_format)
    _write_style(
        styles_dir,
        "exact",
        entries=[{"slide_index": 0, "source": relative}],
    )

    style = resolve_authored_style("exact", styles_dir)

    assert style.slide_reference_indices == (0,)
    validate_exact_reconstruction_style(style, slide_count=1, roles=["CONTENT"])
    public = style.public_dict()
    assert "reference_mode" not in public
    assert "slide_reference_images" not in public
    assert "slide_reference_indices" not in public
    assert "assets_root" not in public
    assert str(assets_dir) not in repr(public)


@pytest.mark.parametrize(
    "entries",
    [
        [
            {"slide_index": 1, "source": "exact/one.png"},
            {"slide_index": 0, "source": "exact/two.png"},
        ],
        [
            {"slide_index": 0, "source": "exact/one.png"},
            {"slide_index": 0, "source": "exact/two.png"},
        ],
    ],
)
def test_exact_rejects_non_positional_or_duplicate_stable_indices(
    tmp_path, monkeypatch, entries
):
    _enable_exact(monkeypatch, "exact")
    styles_dir, assets_dir = _preset_dirs(tmp_path)
    _write_image(assets_dir / "exact" / "one.png", color="#111111")
    _write_image(assets_dir / "exact" / "two.png", color="#222222")
    _write_style(styles_dir, "exact", entries=entries)

    assert load_authored_styles(styles_dir) == []
    with pytest.raises(
        AuthoredExactReconstructionUnavailableError,
        match="failed preset validation",
    ):
        resolve_authored_style("exact", styles_dir)


def test_exact_validator_requires_one_source_for_each_outline_position(
    tmp_path, monkeypatch
):
    _enable_exact(monkeypatch, "exact")
    styles_dir, assets_dir = _preset_dirs(tmp_path)
    _write_image(assets_dir / "exact" / "source.png")
    _write_style(styles_dir, "exact", entries=["exact/source.png"])
    style = resolve_authored_style("exact", styles_dir)

    with pytest.raises(ValueError, match="exactly 2 source images"):
        validate_exact_reconstruction_style(
            style,
            slide_count=2,
            roles=["CONTENT", "CONTENT"],
        )


def test_exact_rejects_duplicate_source_paths_and_duplicate_bytes(
    tmp_path, monkeypatch
):
    _enable_exact(monkeypatch, "same-path", "same-bytes")
    styles_dir, assets_dir = _preset_dirs(tmp_path)
    first = assets_dir / "exact" / "one.png"
    second = assets_dir / "exact" / "two.png"
    _write_image(first)
    second.parent.mkdir(parents=True, exist_ok=True)
    second.write_bytes(first.read_bytes())
    _write_style(
        styles_dir,
        "same-path",
        entries=["exact/one.png", "exact/one.png"],
    )
    _write_style(
        styles_dir,
        "same-bytes",
        entries=["exact/one.png", "exact/two.png"],
    )

    assert load_authored_styles(styles_dir) == []
    for style_id in ("same-path", "same-bytes"):
        with pytest.raises(AuthoredExactReconstructionUnavailableError):
            resolve_authored_style(style_id, styles_dir)


@pytest.mark.parametrize("failure", ["missing", "bad-magic", "bad-size"])
def test_exact_rejects_missing_corrupt_or_wrong_canvas_source(
    tmp_path, monkeypatch, failure
):
    _enable_exact(monkeypatch, "exact")
    styles_dir, assets_dir = _preset_dirs(tmp_path)
    source = assets_dir / "exact" / "source.png"
    if failure == "bad-magic":
        source.parent.mkdir(parents=True)
        source.write_bytes(b"not an image")
    elif failure == "bad-size":
        _write_image(source, size=(640, 360))
    _write_style(styles_dir, "exact", entries=["exact/source.png"])

    assert load_authored_styles(styles_dir) == []
    with pytest.raises(AuthoredExactReconstructionUnavailableError):
        resolve_authored_style("exact", styles_dir)


def test_exact_rejects_asset_path_traversal(tmp_path, monkeypatch):
    _enable_exact(monkeypatch, "exact")
    styles_dir, _ = _preset_dirs(tmp_path)
    outside = tmp_path / "outside.png"
    _write_image(outside)
    _write_style(styles_dir, "exact", entries=["../outside.png"])

    assert load_authored_styles(styles_dir) == []
    with pytest.raises(AuthoredExactReconstructionUnavailableError):
        resolve_authored_style("exact", styles_dir)


def test_exact_runtime_recheck_fails_closed_if_source_changes(
    tmp_path, monkeypatch
):
    _enable_exact(monkeypatch, "exact")
    styles_dir, assets_dir = _preset_dirs(tmp_path)
    source = assets_dir / "exact" / "source.png"
    _write_image(source)
    _write_style(styles_dir, "exact", entries=["exact/source.png"])
    style = resolve_authored_style("exact", styles_dir)

    source.write_bytes(b"changed after catalogue load")

    with pytest.raises(ValueError, match="magic bytes"):
        validate_exact_reconstruction_style(
            style,
            slide_count=1,
            roles=["CONTENT"],
        )


def test_same_role_slides_receive_distinct_positional_sources(
    tmp_path, monkeypatch
):
    _enable_exact(monkeypatch, "exact")
    styles_dir, assets_dir = _preset_dirs(tmp_path)
    _write_image(assets_dir / "exact" / "one.png", color="#111111")
    _write_image(assets_dir / "exact" / "two.png", color="#222222")
    _write_style(
        styles_dir,
        "exact",
        entries=["exact/one.png", "exact/two.png"],
    )
    style = resolve_authored_style("exact", styles_dir)

    validate_exact_reconstruction_style(
        style,
        slide_count=2,
        roles=["CONTENT", "CONTENT"],
    )

    assert style.slide_reference_images[0] != style.slide_reference_images[1]
    assert style.slide_reference_indices == (0, 1)
