import asyncio
import hashlib
import io
import json
from unittest.mock import AsyncMock, patch

import pytest
from models.sql.slide import SlideModel
from PIL import Image
from services.authored_exact_source_snapshot import (
    ExactSourceSnapshotError,
    create_exact_source_snapshot,
    materialize_exact_source_style,
    private_exact_snapshot_root,
)
from utils.authored_styles import AuthoredStyle
from utils.llm_calls.author_slide import EXACT_RECONSTRUCTION


def _png(color: str) -> bytes:
    output = io.BytesIO()
    Image.new("RGB", (1280, 720), color=color).save(output, format="PNG")
    return output.getvalue()


def _slides() -> list[SlideModel]:
    return [
        SlideModel(
            presentation="11111111-1111-1111-1111-111111111111",
            layout_group="authored",
            layout="authored:content",
            index=index,
            content={"__authored__": True, "image": "images/stale.png"},
            properties={"image": "images/stale.png"},
            html_content=f"<section>fresh lower card {index}</section>",
        )
        for index in range(2)
    ]


def _style() -> AuthoredStyle:
    return AuthoredStyle(
        id="exact-private",
        name="Exact",
        description="Private exact style",
        preview_bg="#FFFFFF",
        preview_accent="#111111",
        brief="Preserve every source pixel.",
        reference_mode=EXACT_RECONSTRUCTION,
    )


def test_snapshot_uses_fresh_html_and_publishes_immutable_pngs(tmp_path, monkeypatch):
    app_data = tmp_path / "app-data"
    monkeypatch.setenv("APP_DATA_DIRECTORY", str(app_data))
    rendered = [_png("red"), _png("blue")]
    render = AsyncMock(side_effect=rendered)

    with patch(
        "services.authored_exact_source_snapshot.render_html_to_png",
        new=render,
    ):
        snapshot_id = asyncio.run(
            create_exact_source_snapshot(
                "exact-private", _slides(), expected_slide_count=2
            )
        )

    assert render.await_count == 2
    assert render.await_args_list[0].args == ("<section>fresh lower card 0</section>",)
    assert "stale.png" not in str(render.await_args_list)
    directory = private_exact_snapshot_root() / snapshot_id
    assert not directory.is_relative_to(app_data)
    manifest = json.loads((directory / "manifest.json").read_text("utf-8"))
    assert manifest["viewport"] == {"width": 1280, "height": 720}
    assert [item["slide_index"] for item in manifest["slides"]] == [0, 1]
    assert [
        (directory / item["source_png_file"]).read_bytes()
        for item in manifest["slides"]
    ] == rendered

    materialized = asyncio.run(
        materialize_exact_source_style(_style(), snapshot_id, expected_slide_count=2)
    )
    assert tuple(path.read_bytes() for path in materialized.slide_reference_images) == (
        rendered[0],
        rendered[1],
    )
    assert materialized.slide_reference_indices == (0, 1)
    assert materialized.assets_root == directory.resolve()


def test_snapshot_manifest_is_published_only_after_all_renders(tmp_path, monkeypatch):
    monkeypatch.setenv("APP_DATA_DIRECTORY", str(tmp_path / "app-data"))
    render = AsyncMock(side_effect=[_png("red"), RuntimeError("chrome failed")])

    with (
        patch(
            "services.authored_exact_source_snapshot.render_html_to_png",
            new=render,
        ),
        pytest.raises(ExactSourceSnapshotError, match="rendering failed"),
    ):
        asyncio.run(
            create_exact_source_snapshot(
                "exact-private", _slides(), expected_slide_count=2
            )
        )

    root = private_exact_snapshot_root()
    assert not list(root.glob("*/manifest.json"))


def test_snapshot_write_failure_never_publishes_manifest(tmp_path, monkeypatch):
    monkeypatch.setenv("APP_DATA_DIRECTORY", str(tmp_path / "app-data"))

    with (
        patch(
            "services.authored_exact_source_snapshot.render_html_to_png",
            new=AsyncMock(side_effect=[_png("red"), _png("blue")]),
        ),
        patch(
            "services.authored_exact_source_snapshot.atomic_write",
            side_effect=OSError("disk full"),
        ),
        pytest.raises(ExactSourceSnapshotError, match="storage failed"),
    ):
        asyncio.run(
            create_exact_source_snapshot(
                "exact-private", _slides(), expected_slide_count=2
            )
        )

    assert not list(private_exact_snapshot_root().glob("*/manifest.json"))


def test_snapshot_fails_closed_when_html_or_png_changes(tmp_path, monkeypatch):
    monkeypatch.setenv("APP_DATA_DIRECTORY", str(tmp_path / "app-data"))
    with patch(
        "services.authored_exact_source_snapshot.render_html_to_png",
        new=AsyncMock(side_effect=[_png("red"), _png("blue")]),
    ):
        snapshot_id = asyncio.run(
            create_exact_source_snapshot(
                "exact-private", _slides(), expected_slide_count=2
            )
        )
    directory = private_exact_snapshot_root() / snapshot_id
    manifest = json.loads((directory / "manifest.json").read_text("utf-8"))
    html_path = directory / manifest["slides"][0]["html_file"]
    html_path.write_text("changed", encoding="utf-8")

    with pytest.raises(ExactSourceSnapshotError, match="source HTML changed"):
        asyncio.run(
            materialize_exact_source_style(
                _style(), snapshot_id, expected_slide_count=2
            )
        )

    html_path.write_text("<section>fresh lower card 0</section>", encoding="utf-8")
    assert (
        hashlib.sha256(html_path.read_bytes()).hexdigest()
        == manifest["slides"][0]["html_sha256"]
    )
    png_path = directory / manifest["slides"][0]["source_png_file"]
    png_path.write_bytes(_png("green"))
    with pytest.raises(ExactSourceSnapshotError, match="source image changed"):
        asyncio.run(
            materialize_exact_source_style(
                _style(), snapshot_id, expected_slide_count=2
            )
        )


@pytest.mark.parametrize(
    "slides,count,message",
    [
        (_slides()[:1], 2, "slide count"),
        ([*_slides()[1:], _slides()[0]], 2, "slide order"),
    ],
)
def test_snapshot_rejects_incomplete_or_reordered_sources(
    tmp_path, monkeypatch, slides, count, message
):
    monkeypatch.setenv("APP_DATA_DIRECTORY", str(tmp_path / "app-data"))
    with pytest.raises(ExactSourceSnapshotError, match=message):
        asyncio.run(
            create_exact_source_snapshot(
                "exact-private", slides, expected_slide_count=count
            )
        )
