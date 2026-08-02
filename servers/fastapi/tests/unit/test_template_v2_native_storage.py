from __future__ import annotations

from hashlib import sha256
from pathlib import Path

import pytest

from services import template_v2_native_storage as storage


def _configure_root(monkeypatch: pytest.MonkeyPatch, root: Path) -> None:
    monkeypatch.setattr(storage, "get_app_data_directory_env", lambda: str(root))


def test_native_source_is_promoted_and_read_only_in_exact_template_scope(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    _configure_root(monkeypatch, tmp_path / "app-data")
    payload = b"PK\x03\x04native-source-test"
    source = tmp_path / "source.pptx"
    source.write_bytes(payload)
    digest = sha256(payload).hexdigest()

    key = storage.promote_native_source(
        source,
        template_id="owned-template",
        revision=3,
        expected_sha256=digest,
        expected_size_bytes=len(payload),
    )

    assert key == "template-v2/native/owned-template/revision-3/source.pptx"
    assert storage.read_native_source(
        key,
        template_id="owned-template",
        revision=3,
        expected_sha256=digest,
        expected_size_bytes=len(payload),
    ) == payload
    with pytest.raises(storage.TemplateV2NativeStorageError) as mismatch:
        storage.read_native_source(
            key,
            template_id="other-template",
            revision=3,
            expected_sha256=digest,
            expected_size_bytes=len(payload),
        )
    assert mismatch.value.code == "template_v2_native_storage_scope_mismatch"


@pytest.mark.parametrize(
    ("digest", "size", "code"),
    [
        ("A" * 64, 10, "template_v2_native_source_sha256_invalid"),
        ("0" * 64, 0, "template_v2_native_source_size_invalid"),
        ("0" * 64, True, "template_v2_native_source_size_invalid"),
    ],
)
def test_native_source_rejects_invalid_integrity_contract(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    digest: str,
    size: int,
    code: str,
):
    _configure_root(monkeypatch, tmp_path / "app-data")
    source = tmp_path / "source.pptx"
    source.write_bytes(b"PK\x03\x04source")

    with pytest.raises(storage.TemplateV2NativeStorageError) as rejected:
        storage.promote_native_source(
            source,
            template_id="owned-template",
            revision=1,
            expected_sha256=digest,
            expected_size_bytes=size,
        )
    assert rejected.value.code == code
