from __future__ import annotations

import asyncio
from io import BytesIO
from pathlib import Path

import pytest
from fastapi import HTTPException, UploadFile

from utils.upload_limits import (
    DEFAULT_SINGLE_UPLOAD_MIB,
    DEFAULT_TOTAL_UPLOAD_MIB,
    DEFAULT_IMAGE_UPLOAD_MIB,
    HARD_IMAGE_UPLOAD_MIB,
    HARD_SINGLE_UPLOAD_MIB,
    HARD_TOTAL_UPLOAD_MIB,
    MIB,
    get_image_upload_limit_bytes,
    get_single_upload_limit_bytes,
    get_total_upload_limit_bytes,
    stream_upload_to_file,
    upload_limits_payload,
)


def _upload(data: bytes, *, declared_size: int | None = None) -> UploadFile:
    return UploadFile(filename="sample.pptx", file=BytesIO(data), size=declared_size)


def test_default_limits(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("PRESENTON_MAX_UPLOAD_MB", raising=False)
    monkeypatch.delenv("PRESENTON_MAX_UPLOAD_TOTAL_MB", raising=False)
    monkeypatch.delenv("PRESENTON_MAX_IMAGE_UPLOAD_MB", raising=False)

    assert get_single_upload_limit_bytes() == DEFAULT_SINGLE_UPLOAD_MIB * MIB
    assert get_total_upload_limit_bytes() == DEFAULT_TOTAL_UPLOAD_MIB * MIB
    assert get_image_upload_limit_bytes() == DEFAULT_IMAGE_UPLOAD_MIB * MIB


def test_config_override_and_hard_cap(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PRESENTON_MAX_UPLOAD_MB", "256")
    assert get_single_upload_limit_bytes() == 256 * MIB

    monkeypatch.setenv("PRESENTON_MAX_UPLOAD_MB", "9999")
    assert get_single_upload_limit_bytes() == HARD_SINGLE_UPLOAD_MIB * MIB

    monkeypatch.setenv("PRESENTON_MAX_IMAGE_UPLOAD_MB", "9999")
    assert get_image_upload_limit_bytes() == HARD_IMAGE_UPLOAD_MIB * MIB

    monkeypatch.setenv("PRESENTON_MAX_UPLOAD_TOTAL_MB", "9999")
    assert get_total_upload_limit_bytes() == HARD_TOTAL_UPLOAD_MIB * MIB


@pytest.mark.parametrize("configured", ["", "0", "-1", "not-a-number"])
def test_invalid_config_fails_closed_to_default(
    monkeypatch: pytest.MonkeyPatch,
    configured: str,
) -> None:
    monkeypatch.setenv("PRESENTON_MAX_UPLOAD_MB", configured)
    monkeypatch.setenv("PRESENTON_MAX_IMAGE_UPLOAD_MB", configured)
    assert get_single_upload_limit_bytes() == DEFAULT_SINGLE_UPLOAD_MIB * MIB
    assert get_image_upload_limit_bytes() == DEFAULT_IMAGE_UPLOAD_MIB * MIB


def test_total_limit_never_below_single_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PRESENTON_MAX_UPLOAD_MB", "256")
    monkeypatch.setenv("PRESENTON_MAX_UPLOAD_TOTAL_MB", "128")
    assert get_total_upload_limit_bytes() == 256 * MIB


def test_effective_limits_are_exposed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PRESENTON_MAX_UPLOAD_MB", "192")
    monkeypatch.setenv("PRESENTON_MAX_IMAGE_UPLOAD_MB", "24")
    payload = upload_limits_payload()
    assert payload["single_file_mb"] == 192
    assert payload["single_file_bytes"] == 192 * MIB
    assert payload["image_mb"] == 24
    assert payload["hard_request_total_mb"] == HARD_TOTAL_UPLOAD_MIB
    assert "denial-of-service" in str(payload["reason"])


def test_stream_allows_exact_boundary(tmp_path: Path) -> None:
    target = tmp_path / "exact.bin"
    written = asyncio.run(
        stream_upload_to_file(
            _upload(b"abcd"),
            target,
            limit_bytes=4,
        )
    )
    assert written == 4
    assert target.read_bytes() == b"abcd"


def test_stream_rejects_one_byte_over_and_removes_partial(tmp_path: Path) -> None:
    target = tmp_path / "too-large.bin"
    with pytest.raises(HTTPException) as error:
        asyncio.run(
            stream_upload_to_file(
                _upload(b"abcde"),
                target,
                limit_bytes=4,
            )
        )
    assert error.value.status_code == 413
    assert "4 bytes" in str(error.value.detail)
    assert not target.exists()


def test_declared_oversize_is_rejected_before_read(tmp_path: Path) -> None:
    target = tmp_path / "declared-too-large.bin"
    with pytest.raises(HTTPException) as error:
        asyncio.run(
            stream_upload_to_file(
                _upload(b"small", declared_size=5),
                target,
                limit_bytes=4,
            )
        )
    assert error.value.status_code == 413
    assert not target.exists()
