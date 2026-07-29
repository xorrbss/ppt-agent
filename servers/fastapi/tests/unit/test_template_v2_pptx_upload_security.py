from __future__ import annotations

import asyncio
import hashlib
import io
from pathlib import Path
import uuid
import zipfile

from fastapi import UploadFile
import pytest
from starlette.datastructures import Headers

from services import template_v2_pptx_storage as storage
from services.template_v2_pptx_storage import (
    PPTX_MEDIA_TYPE,
    PptxUploadRejected,
    private_import_root,
    private_source_storage_key,
    resolve_private_source,
    store_private_pptx,
    verify_private_source,
)
from templates.v2.pptx.package_reader import PptxPackageReader, UnsafePptxPackage
from templates.v2.pptx.source_inventory import (
    HashedInventoryItem,
    SecretFreeSourceMetadata,
    SourceInventory,
    candidate_inventory_item,
    hashed_inventory_item,
)


PRESENTATION_XML = b"""\
<p:presentation
 xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"
 xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
 <p:sldIdLst/>
</p:presentation>"""
PRESENTATION_RELS = (
    b'<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/'
    b'relationships"/>'
)


def _pptx_bytes(
    *,
    extra: list[tuple[str, bytes]] | None = None,
    compression: int = zipfile.ZIP_DEFLATED,
) -> bytes:
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", compression=compression) as archive:
        archive.writestr("[Content_Types].xml", b"<Types/>")
        archive.writestr("ppt/presentation.xml", PRESENTATION_XML)
        archive.writestr(
            "ppt/_rels/presentation.xml.rels",
            PRESENTATION_RELS,
        )
        for name, payload in extra or []:
            archive.writestr(name, payload)
    return stream.getvalue()


def _upload(
    payload: bytes,
    *,
    filename: str = "source.pptx",
    media_type: str = PPTX_MEDIA_TYPE,
) -> UploadFile:
    return UploadFile(
        file=io.BytesIO(payload),
        filename=filename,
        headers=Headers({"content-type": media_type}),
    )


def _assert_rejection(code: str, action) -> None:
    with pytest.raises(PptxUploadRejected) as caught:
        action()
    assert caught.value.code == code


def _assert_package_rejection(code: str, action) -> None:
    with pytest.raises(UnsafePptxPackage) as caught:
        action()
    assert caught.value.code == code


def test_upload_filename_is_leaf_normalized_bounded_and_secret_free(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("APP_DATA_DIRECTORY", str(tmp_path / "public-app-data"))
    payload = _pptx_bytes()
    filename = (
        "C:\\fakepath\\ignored\\"
        + ("Quarterly" * 40)
        + "\N{RIGHT-TO-LEFT OVERRIDE}.pptx"
    )

    stored = asyncio.run(
        store_private_pptx(
            _upload(payload, filename=filename),
            import_id=uuid.uuid4(),
        )
    )

    assert len(stored.display_filename) <= 240
    assert stored.display_filename.endswith(".pptx")
    assert "/" not in stored.display_filename
    assert "\\" not in stored.display_filename
    assert "\N{RIGHT-TO-LEFT OVERRIDE}" not in stored.display_filename
    assert stored.secret_free_metadata().to_manifest() == {
        "display_filename": stored.display_filename,
        "media_type": PPTX_MEDIA_TYPE,
        "size_bytes": len(payload),
        "sha256": hashlib.sha256(payload).hexdigest(),
    }
    assert "storage_key" not in stored.secret_free_metadata().to_manifest()


@pytest.mark.parametrize(
    ("filename", "media_type", "code"),
    [
        ("source.zip", PPTX_MEDIA_TYPE, "pptx_extension_required"),
        ("source.pptx", "application/zip", "pptx_media_type_required"),
        ("source.pptx", "", "pptx_media_type_required"),
    ],
)
def test_upload_requires_the_pptx_extension_and_media_type(
    tmp_path: Path,
    monkeypatch,
    filename: str,
    media_type: str,
    code: str,
) -> None:
    monkeypatch.setenv("APP_DATA_DIRECTORY", str(tmp_path / "app-data"))

    _assert_rejection(
        code,
        lambda: asyncio.run(
            store_private_pptx(
                _upload(
                    _pptx_bytes(),
                    filename=filename,
                    media_type=media_type,
                ),
                import_id=uuid.uuid4(),
            )
        ),
    )


def test_upload_limit_cannot_be_raised_above_the_hard_cap(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("APP_DATA_DIRECTORY", str(tmp_path / "app-data"))
    monkeypatch.setattr(storage, "MAX_PPTX_UPLOAD_BYTES", 128)

    _assert_rejection(
        "pptx_upload_size_limit_exceeded",
        lambda: asyncio.run(
            store_private_pptx(
                _upload(_pptx_bytes()),
                import_id=uuid.uuid4(),
                max_bytes=10_000,
            )
        ),
    )


def test_upload_preflight_rejects_unsafe_zip_and_removes_partial_source(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("APP_DATA_DIRECTORY", str(tmp_path / "app-data"))
    import_id = uuid.uuid4()

    _assert_rejection(
        "unsafe_zip_member_path",
        lambda: asyncio.run(
            store_private_pptx(
                _upload(_pptx_bytes(extra=[("../escape.xml", b"escape")])),
                import_id=import_id,
            )
        ),
    )

    source = resolve_private_source(
        private_source_storage_key(import_id),
        expected_import_id=import_id,
    )
    assert not source.exists()
    assert not source.with_suffix(".uploading").exists()


@pytest.mark.parametrize(
    "storage_key",
    [
        "not-a-uuid/source.pptx",
        "00000000-0000-0000-0000-000000000000/preview.png",
        "00000000-0000-0000-0000-000000000000//source.pptx",
        "00000000-0000-0000-0000-000000000000/../source.pptx",
        "00000000-0000-0000-0000-000000000000\\source.pptx",
        "00000000000000000000000000000000/source.pptx",
        "00000000-0000-0000-0000-000000000000/SOURCE.PPTX",
    ],
)
def test_private_storage_key_has_exact_owner_scoped_shape(
    tmp_path: Path,
    monkeypatch,
    storage_key: str,
) -> None:
    monkeypatch.setenv("APP_DATA_DIRECTORY", str(tmp_path / "app-data"))
    _assert_rejection(
        "invalid_private_storage_key",
        lambda: resolve_private_source(storage_key),
    )


def test_private_source_rejects_owner_mismatch_and_symlink(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("APP_DATA_DIRECTORY", str(tmp_path / "app-data"))
    owner = uuid.uuid4()
    different_owner = uuid.uuid4()
    key = private_source_storage_key(owner)

    _assert_rejection(
        "private_storage_owner_mismatch",
        lambda: resolve_private_source(key, expected_import_id=different_owner),
    )

    root = private_import_root()
    root.mkdir(parents=True)
    outside = tmp_path / "outside"
    outside.mkdir()
    owner_link = root / str(owner)
    try:
        owner_link.symlink_to(outside, target_is_directory=True)
    except OSError as error:
        pytest.skip(f"directory symlinks are unavailable: {error}")
    _assert_rejection(
        "private_storage_symlink_forbidden",
        lambda: resolve_private_source(key, expected_import_id=owner),
    )


def test_private_source_verification_binds_owner_size_limit_and_hash(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("APP_DATA_DIRECTORY", str(tmp_path / "app-data"))
    import_id = uuid.uuid4()
    payload = _pptx_bytes()
    stored = asyncio.run(
        store_private_pptx(_upload(payload), import_id=import_id)
    )

    assert verify_private_source(
        stored.storage_key,
        stored.sha256,
        expected_import_id=import_id,
        expected_size_bytes=stored.size_bytes,
    ).is_file()
    _assert_rejection(
        "private_source_size_mismatch",
        lambda: verify_private_source(
            stored.storage_key,
            stored.sha256,
            expected_size_bytes=stored.size_bytes + 1,
        ),
    )
    _assert_rejection(
        "private_source_size_limit_exceeded",
        lambda: verify_private_source(
            stored.storage_key,
            stored.sha256,
            max_bytes=stored.size_bytes - 1,
        ),
    )
    _assert_rejection(
        "invalid_source_sha256",
        lambda: verify_private_source(stored.storage_key, stored.sha256.upper()),
    )
    _assert_rejection(
        "private_storage_owner_mismatch",
        lambda: verify_private_source(
            stored.storage_key,
            stored.sha256,
            expected_import_id=uuid.uuid4(),
        ),
    )


@pytest.mark.parametrize(
    "member_name",
    [
        "../escape.xml",
        "/absolute.xml",
        "C:/drive.xml",
        "ppt\\..\\escape.xml",
        "ppt//ambiguous.xml",
        "ppt/./ambiguous.xml",
        "ppt/trailing/",
        "ppt/\x00hidden.xml",
    ],
)
def test_package_preflight_rejects_noncanonical_member_paths(
    tmp_path: Path,
    member_name: str,
) -> None:
    source = tmp_path / f"unsafe-{uuid.uuid4()}.pptx"
    source.write_bytes(_pptx_bytes(extra=[(member_name, b"x")]))

    _assert_package_rejection(
        "unsafe_zip_member_path",
        lambda: PptxPackageReader(source).preflight(),
    )


def test_package_preflight_rejects_duplicate_and_unsupported_compression(
    tmp_path: Path,
) -> None:
    duplicate = tmp_path / "duplicate.pptx"
    duplicate.write_bytes(
        _pptx_bytes(
            extra=[("ppt/custom.xml", b"a"), ("PPT/CUSTOM.XML", b"b")]
        )
    )
    _assert_package_rejection(
        "duplicate_zip_member",
        lambda: PptxPackageReader(duplicate).preflight(),
    )

    unsupported = tmp_path / "unsupported.pptx"
    unsupported.write_bytes(_pptx_bytes(compression=zipfile.ZIP_BZIP2))
    _assert_package_rejection(
        "unsupported_zip_compression",
        lambda: PptxPackageReader(unsupported).preflight(),
    )


@pytest.mark.parametrize("encoding", ["utf-16", "utf-16-le", "utf-16-be", "utf-32"])
def test_xml_reader_rejects_wide_encoding_doctype(
    tmp_path: Path,
    encoding: str,
) -> None:
    source = tmp_path / "wide-doctype.pptx"
    xml = '<!DOCTYPE foo [<!ENTITY xxe SYSTEM "file:///secret">]><foo/>'
    source.write_bytes(
        _pptx_bytes(extra=[("ppt/custom.xml", xml.encode(encoding))])
    )
    reader = PptxPackageReader(source)
    reader.preflight()

    _assert_package_rejection(
        "unsafe_xml_declaration",
        lambda: reader.read_xml("ppt/custom.xml"),
    )


def test_artifact_inventory_is_hashed_sorted_and_detects_package_changes(
    tmp_path: Path,
) -> None:
    source = tmp_path / "inventory.pptx"
    source.write_bytes(_pptx_bytes(extra=[("ppt/z.bin", b"z"), ("a.bin", b"a")]))
    reader = PptxPackageReader(source)
    reader.preflight()

    inventory = reader.artifact_inventory()
    identifiers = [item.identifier for item in inventory]
    assert identifiers == sorted(identifiers)
    artifact = next(item for item in inventory if item.identifier == "a.bin")
    assert artifact == HashedInventoryItem(
        scope="artifact",
        identifier="a.bin",
        size_bytes=1,
        sha256=hashlib.sha256(b"a").hexdigest(),
    )

    source.write_bytes(_pptx_bytes(extra=[("ppt/z.bin", b"z"), ("a.bin", b"b")]))
    _assert_package_rejection(
        "pptx_package_changed_after_preflight",
        reader.artifact_inventory,
    )
    _assert_package_rejection(
        "pptx_package_changed_after_preflight",
        lambda: reader.read_member("a.bin"),
    )

    added_member = tmp_path / "added-member.pptx"
    added_member.write_bytes(_pptx_bytes())
    added_member_reader = PptxPackageReader(added_member)
    added_member_reader.preflight()
    added_member.write_bytes(
        _pptx_bytes(extra=[("unexpected.bin", b"unexpected")])
    )
    _assert_package_rejection(
        "pptx_package_changed_after_preflight",
        lambda: added_member_reader.read_member("ppt/presentation.xml"),
    )


def test_source_artifact_and_candidate_inventory_are_separate_and_secret_free() -> None:
    source = SecretFreeSourceMetadata(
        display_filename="Quarterly.pptx",
        media_type=PPTX_MEDIA_TYPE,
        size_bytes=123,
        sha256="a" * 64,
    )
    artifact = hashed_inventory_item(
        "artifact",
        "ppt/presentation.xml",
        b"<presentation/>",
        media_type="application/xml",
    )
    first_candidate = candidate_inventory_item(
        "slide/1",
        {"text": "internal candidate body", "order": 1},
    )
    second_candidate = candidate_inventory_item(
        "slide/1",
        {"order": 1, "text": "internal candidate body"},
    )
    assert first_candidate == second_candidate

    manifest = SourceInventory(
        source=source,
        artifacts=(artifact,),
        candidates=(first_candidate,),
    ).to_manifest()

    assert manifest["source"] == {
        "display_filename": "Quarterly.pptx",
        "media_type": PPTX_MEDIA_TYPE,
        "size_bytes": 123,
        "sha256": "a" * 64,
    }
    assert manifest["artifacts"] == [artifact.to_manifest()]
    assert manifest["candidates"] == [first_candidate.to_manifest()]
    assert set(manifest["source"]) == {
        "display_filename",
        "media_type",
        "size_bytes",
        "sha256",
    }
    assert "internal candidate body" not in repr(manifest)
    assert "storage_key" not in repr(manifest)
    assert "source_path" not in repr(manifest)
    assert "render_url" not in repr(manifest)
    assert "token" not in repr(manifest)


def test_source_inventory_rejects_bad_metadata_scope_and_duplicates() -> None:
    with pytest.raises(ValueError, match="invalid_source_display_filename"):
        SecretFreeSourceMetadata(
            display_filename="../source.pptx",
            media_type=PPTX_MEDIA_TYPE,
            size_bytes=1,
            sha256="a" * 64,
        )

    source = SecretFreeSourceMetadata(
        display_filename="source.pptx",
        media_type=PPTX_MEDIA_TYPE,
        size_bytes=1,
        sha256="a" * 64,
    )
    artifact = hashed_inventory_item("artifact", "same", b"a")
    with pytest.raises(ValueError, match="duplicate_inventory_identifier"):
        SourceInventory(
            source=source,
            artifacts=(artifact, artifact),
        )
    with pytest.raises(ValueError, match="inventory_scope_group_mismatch"):
        SourceInventory(
            source=source,
            candidates=(artifact,),
        )
    with pytest.raises(TypeError, match="inventory_items_must_be_tuple"):
        SourceInventory(source=source, artifacts=[artifact])  # type: ignore[arg-type]
