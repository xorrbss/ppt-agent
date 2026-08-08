"""Relocation of `pptx-to-json` media into the import's private tree.

The runtime allocates its own output directory under `APP_DATA_DIRECTORY` and emits
element `data` as URLs into it. Those assets must end up in the same private
per-import directory as the source deck -- one retention path covers both -- and the
URLs must translate back to filesystem paths, because `json-to-image` renders an
unreachable asset as a silently blank region.
"""

from __future__ import annotations

from pathlib import Path
import uuid

import pytest

from services.template_v2_pptx_storage import (
    PRIVATE_ASSET_URL_PREFIX,
    PptxUploadRejected,
    private_asset_reference,
    private_import_root,
    private_source_storage_key,
    relocate_runtime_assets,
    resolve_private_asset,
)


def _runtime_output(app_data: Path, *asset_names: str) -> Path:
    """Reproduce the layout the runtime allocates for one `pptx-to-json` run."""

    output_directory = app_data / "pptx-to-json" / uuid.uuid4().hex
    images = output_directory / "images"
    images.mkdir(parents=True)
    (output_directory / "presentation.json").write_text("{}", encoding="utf-8")
    for asset_name in asset_names:
        (images / asset_name).write_bytes(asset_name.encode())
    return output_directory


def _assert_rejection(code: str, action) -> None:
    with pytest.raises(PptxUploadRejected) as caught:
        action()
    assert caught.value.code == code


def test_relocation_moves_runtime_media_out_of_the_served_mount(
    tmp_path: Path,
    monkeypatch,
) -> None:
    app_data = tmp_path / "app-data"
    monkeypatch.setenv("APP_DATA_DIRECTORY", str(app_data))
    import_id = uuid.uuid4()
    output_directory = _runtime_output(app_data, "image1-9f3.png", "image2-0ab.jpeg")

    relocated = relocate_runtime_assets(output_directory, import_id=import_id)

    assert relocated.asset_names == ("image1-9f3.png", "image2-0ab.jpeg")
    assert not (output_directory / "images").exists()
    root = private_import_root().resolve()
    for asset_name in relocated.asset_names:
        moved = resolve_private_asset(
            private_asset_reference(import_id, asset_name),
            expected_import_id=import_id,
        )
        assert moved.read_bytes() == asset_name.encode()
        assert moved.is_relative_to(root)
        assert not moved.is_relative_to(app_data.resolve())
        # The source deck for the same import is a sibling: one tree, one cleanup.
        assert moved.parent.parent.name == str(import_id)
        assert private_source_storage_key(import_id).startswith(f"{import_id}/")


def test_runtime_url_round_trips_to_a_private_filesystem_path(
    tmp_path: Path,
    monkeypatch,
) -> None:
    app_data = tmp_path / "app-data"
    monkeypatch.setenv("APP_DATA_DIRECTORY", str(app_data))
    import_id = uuid.uuid4()
    output_directory = _runtime_output(app_data, "image1-9f3.png")

    relocated = relocate_runtime_assets(output_directory, import_id=import_id)

    emitted = (
        f"http://127.0.0.1:8000/app_data/pptx-to-json/{output_directory.name}"
        "/images/image1-9f3.png"
    )
    reference = relocated.reference_for(emitted)
    assert reference == (
        f"{PRIVATE_ASSET_URL_PREFIX}/{import_id}/assets/image1-9f3.png"
    )
    assert resolve_private_asset(reference).read_bytes() == b"image1-9f3.png"
    assert relocated.reference_for(emitted.replace("image1-9f3", "absent")) is None
    assert relocated.reference_for(None) is None


def test_relocation_tolerates_a_media_free_run_but_not_a_missing_output(
    tmp_path: Path,
    monkeypatch,
) -> None:
    app_data = tmp_path / "app-data"
    monkeypatch.setenv("APP_DATA_DIRECTORY", str(app_data))
    output_directory = app_data / "pptx-to-json" / uuid.uuid4().hex
    output_directory.mkdir(parents=True)

    relocated = relocate_runtime_assets(output_directory, import_id=uuid.uuid4())

    assert relocated.asset_names == ()
    _assert_rejection(
        "runtime_output_directory_missing",
        lambda: relocate_runtime_assets(
            output_directory / "absent",
            import_id=uuid.uuid4(),
        ),
    )


def test_relocation_refuses_a_directory_the_runtime_did_not_allocate(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """Relocation deletes the run directory on every exit, so it must own it first.

    `output_dir` is `""` by default on the converter's response model and `Path("")`
    is the server's working directory -- a real directory, which the discard would
    otherwise remove.
    """

    app_data = tmp_path / "app-data"
    (app_data / "pptx-to-json").mkdir(parents=True)
    monkeypatch.setenv("APP_DATA_DIRECTORY", str(app_data))
    unowned = tmp_path / "server-working-directory"
    (unowned / "images").mkdir(parents=True)
    (unowned / "presentation.json").write_text("{}", encoding="utf-8")
    monkeypatch.chdir(unowned)

    _assert_rejection(
        "runtime_output_directory_untrusted",
        lambda: relocate_runtime_assets("", import_id=uuid.uuid4()),
    )
    _assert_rejection(
        "runtime_output_directory_untrusted",
        lambda: relocate_runtime_assets(unowned, import_id=uuid.uuid4()),
    )
    assert (unowned / "presentation.json").is_file()


@pytest.mark.parametrize(
    ("plant_directory", "entry_name", "code"),
    [
        (True, "nested", "unsupported_runtime_asset_entry"),
        (False, "notes.txt", "unsafe_runtime_asset_name"),
    ],
)
def test_a_rejected_relocation_still_discards_the_run_directory(
    tmp_path: Path,
    monkeypatch,
    plant_directory: bool,
    entry_name: str,
    code: str,
) -> None:
    """A rejection must not strand `presentation.json` on the served mount.

    The run directory holds the deck's full extracted text and lives under
    `/app_data`, outside the private tree retention manages; the bundled runtime
    only removes it when the converter itself fails. So a rejection that left it
    behind would outlive the cleanup that reclaims the source deck.
    """

    app_data = tmp_path / "app-data"
    monkeypatch.setenv("APP_DATA_DIRECTORY", str(app_data))
    output_directory = _runtime_output(app_data, "image1-9f3.png")
    planted = output_directory / "images" / entry_name
    if plant_directory:
        planted.mkdir()
    else:
        planted.write_bytes(b"not-an-image")

    _assert_rejection(
        code,
        lambda: relocate_runtime_assets(output_directory, import_id=uuid.uuid4()),
    )

    assert not (output_directory / "presentation.json").exists()
    assert not output_directory.exists()


def test_a_media_free_run_directory_is_discarded_as_well(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """The early return owns the run directory exactly like the success path."""

    app_data = tmp_path / "app-data"
    monkeypatch.setenv("APP_DATA_DIRECTORY", str(app_data))
    output_directory = app_data / "pptx-to-json" / uuid.uuid4().hex
    output_directory.mkdir(parents=True)
    (output_directory / "presentation.json").write_text("{}", encoding="utf-8")

    relocated = relocate_runtime_assets(output_directory, import_id=uuid.uuid4())

    assert relocated.asset_names == ()
    assert not (output_directory / "presentation.json").exists()
    assert not output_directory.exists()


@pytest.mark.parametrize(
    ("suffix", "code"),
    [
        ("assets/../../source.pptx", "invalid_private_asset_reference"),
        ("source.pptx", "invalid_private_asset_reference"),
        ("images/image1-9f3.png", "invalid_private_asset_reference"),
        ("assets/nested/image1-9f3.png", "invalid_private_asset_reference"),
        ("assets/..%2Fsource.pptx", "unsafe_runtime_asset_name"),
        ("assets/..", "unsafe_runtime_asset_name"),
        ("assets/.hidden.png", "unsafe_runtime_asset_name"),
        ("assets/image1-9f3.txt", "unsafe_runtime_asset_name"),
        ("assets/image1 9f3.png", "unsafe_runtime_asset_name"),
    ],
)
def test_asset_reference_rejects_traversal_and_unsafe_names(
    tmp_path: Path,
    monkeypatch,
    suffix: str,
    code: str,
) -> None:
    monkeypatch.setenv("APP_DATA_DIRECTORY", str(tmp_path / "app-data"))
    import_id = uuid.uuid4()

    _assert_rejection(
        code,
        lambda: resolve_private_asset(
            f"{PRIVATE_ASSET_URL_PREFIX}/{import_id}/{suffix}"
        ),
    )


def test_asset_reference_rejects_foreign_prefixes_and_owner_mismatch(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("APP_DATA_DIRECTORY", str(tmp_path / "app-data"))
    import_id = uuid.uuid4()
    reference = private_asset_reference(import_id, "image1-9f3.png")

    for foreign in [
        "/app_data/pptx-to-json/run/images/image1-9f3.png",
        f"{PRIVATE_ASSET_URL_PREFIX}/{import_id.hex}/assets/image1-9f3.png",
        reference.replace(PRIVATE_ASSET_URL_PREFIX, "/api/v1/ppt/files"),
    ]:
        _assert_rejection(
            "invalid_private_asset_reference",
            lambda foreign=foreign: resolve_private_asset(foreign),
        )
    _assert_rejection(
        "private_storage_owner_mismatch",
        lambda: resolve_private_asset(reference, expected_import_id=uuid.uuid4()),
    )


def test_relocation_and_resolution_refuse_to_follow_symlinks(
    tmp_path: Path,
    monkeypatch,
) -> None:
    app_data = tmp_path / "app-data"
    monkeypatch.setenv("APP_DATA_DIRECTORY", str(app_data))
    import_id = uuid.uuid4()
    outside = tmp_path / "outside"
    outside.mkdir()
    secret = outside / "image1-9f3.png"
    secret.write_bytes(b"secret")
    output_directory = _runtime_output(app_data)
    try:
        (output_directory / "images" / "image1-9f3.png").symlink_to(secret)
    except OSError as error:
        pytest.skip(f"symlinks are unavailable: {error}")

    _assert_rejection(
        "runtime_asset_symlink_forbidden",
        lambda: relocate_runtime_assets(output_directory, import_id=import_id),
    )

    linked_media = _runtime_output(app_data)
    (linked_media / "images").rmdir()
    (linked_media / "images").symlink_to(outside, target_is_directory=True)
    _assert_rejection(
        "runtime_asset_symlink_forbidden",
        lambda: relocate_runtime_assets(linked_media, import_id=import_id),
    )

    owner_directory = private_import_root() / str(import_id)
    owner_directory.mkdir(parents=True)
    (owner_directory / "assets").symlink_to(outside, target_is_directory=True)
    _assert_rejection(
        "private_storage_symlink_forbidden",
        lambda: resolve_private_asset(
            private_asset_reference(import_id, "image1-9f3.png")
        ),
    )
    assert secret.read_bytes() == b"secret"
