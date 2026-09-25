"""Tests for DocumentConversionService.convert_office_to_pdf.

Covers:
  * command construction — every invocation gets its own, valid
    ``-env:UserInstallation=`` LibreOffice profile argument, and the
    temporary profile directory it points at is cleaned up afterwards
    (success or failure).
  * an integration-style test that runs two *real* soffice conversions at
    the same time to reproduce the reported race (second call exits
    non-zero because it shares the default profile). Skipped with a clear
    reason when soffice is not installed locally.
"""

from __future__ import annotations

import shutil
import subprocess
import threading
import time
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import url2pathname

import pytest

from services.document_conversion_service import (
    DocumentConversionError,
    DocumentConversionService,
)


def _profile_dir_from_command(command: list[str]) -> Path:
    arg = next(
        part for part in command if part.startswith("-env:UserInstallation=")
    )
    uri = arg[len("-env:UserInstallation=") :]
    parsed = urlparse(uri)
    assert parsed.scheme == "file"
    return Path(url2pathname(parsed.path))


def _fake_success_run(captured_commands: list[list[str]]):
    def fake_run(command, **kwargs):
        captured_commands.append(list(command))
        outdir = command[command.index("--outdir") + 1]
        stem = Path(command[-1]).stem
        (Path(outdir) / f"{stem}.pdf").write_bytes(b"%PDF-1.4\n%%EOF")
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    return fake_run


def test_command_includes_a_user_installation_arg_between_headless_and_convert_to(
    tmp_path, monkeypatch
):
    service = DocumentConversionService()
    monkeypatch.setattr(service, "soffice_binary", "soffice.exe")
    captured: list[list[str]] = []
    monkeypatch.setattr(
        "services.document_conversion_service.subprocess.run",
        _fake_success_run(captured),
    )

    source = tmp_path / "input.pptx"
    source.write_bytes(b"fake-pptx")
    output_dir = tmp_path / "out"

    service.convert_office_to_pdf(str(source), str(output_dir))

    assert len(captured) == 1
    command = captured[0]
    assert command[0] == "soffice.exe"
    assert command[1] == "--headless"
    assert command[2].startswith("-env:UserInstallation=file:")
    assert command[3] == "--convert-to"
    assert command[4] == "pdf"
    assert command[5] == "--outdir"
    assert command[6] == str(output_dir)
    assert command[7] == str(source)


def test_two_calls_get_distinct_profile_directories(tmp_path, monkeypatch):
    service = DocumentConversionService()
    monkeypatch.setattr(service, "soffice_binary", "soffice.exe")
    captured: list[list[str]] = []
    monkeypatch.setattr(
        "services.document_conversion_service.subprocess.run",
        _fake_success_run(captured),
    )

    source_a = tmp_path / "a.pptx"
    source_a.write_bytes(b"fake-pptx-a")
    source_b = tmp_path / "b.pptx"
    source_b.write_bytes(b"fake-pptx-b")

    service.convert_office_to_pdf(str(source_a), str(tmp_path / "out-a"))
    service.convert_office_to_pdf(str(source_b), str(tmp_path / "out-b"))

    assert len(captured) == 2
    first_profile = _profile_dir_from_command(captured[0])
    second_profile = _profile_dir_from_command(captured[1])
    assert first_profile != second_profile


def test_profile_directory_is_removed_after_a_successful_conversion(
    tmp_path, monkeypatch
):
    service = DocumentConversionService()
    monkeypatch.setattr(service, "soffice_binary", "soffice.exe")
    captured: list[list[str]] = []
    monkeypatch.setattr(
        "services.document_conversion_service.subprocess.run",
        _fake_success_run(captured),
    )

    source = tmp_path / "input.pptx"
    source.write_bytes(b"fake-pptx")

    service.convert_office_to_pdf(str(source), str(tmp_path / "out"))

    profile_dir = _profile_dir_from_command(captured[0])
    assert not profile_dir.exists()


def test_profile_directory_is_removed_after_a_failed_conversion(
    tmp_path, monkeypatch
):
    service = DocumentConversionService()
    monkeypatch.setattr(service, "soffice_binary", "soffice.exe")
    captured: list[list[str]] = []

    def fake_run(command, **kwargs):
        captured.append(list(command))
        raise subprocess.CalledProcessError(
            returncode=1, cmd=command, output="", stderr=""
        )

    monkeypatch.setattr(
        "services.document_conversion_service.subprocess.run", fake_run
    )

    source = tmp_path / "input.pptx"
    source.write_bytes(b"fake-pptx")

    with pytest.raises(DocumentConversionError) as exc:
        service.convert_office_to_pdf(str(source), str(tmp_path / "out"))

    assert "exit_code=1" in str(exc.value)
    profile_dir = _profile_dir_from_command(captured[0])
    assert not profile_dir.exists()


def _resolve_local_soffice() -> str | None:
    service = DocumentConversionService()
    if not service.is_soffice_available():
        return None
    return shutil.which(service.soffice_binary)


def _build_image_only_pptx(path: Path) -> None:
    """A minimal PPTX with a single slide holding only a picture (no text)."""
    from io import BytesIO

    from PIL import Image
    from pptx import Presentation
    from pptx.util import Inches

    image_buffer = BytesIO()
    Image.new("RGB", (64, 64), color=(200, 30, 30)).save(image_buffer, format="PNG")
    image_buffer.seek(0)

    presentation = Presentation()
    blank_layout = presentation.slide_layouts[6]
    slide = presentation.slides.add_slide(blank_layout)
    slide.shapes.add_picture(
        image_buffer, Inches(1), Inches(1), width=Inches(4), height=Inches(4)
    )
    presentation.save(str(path))


@pytest.mark.skipif(
    _resolve_local_soffice() is None,
    reason=(
        "soffice not found via SOFFICE_PATH/PATH; concurrent-conversion "
        "integration test requires a local LibreOffice install"
    ),
)
def test_two_concurrent_real_conversions_both_succeed(tmp_path):
    """Reproduces the reported race: two soffice conversions 'at the same time'.

    Before the per-call UserInstallation fix, the second of two concurrent
    soffice invocations against the shared default profile could exit
    non-zero with empty stdout/stderr. With isolated profiles both must
    succeed independently.
    """
    service = DocumentConversionService()

    source_a = tmp_path / "image_only_a.pptx"
    source_b = tmp_path / "image_only_b.pptx"
    _build_image_only_pptx(source_a)
    _build_image_only_pptx(source_b)

    results: dict[str, object] = {}
    barrier = threading.Barrier(2, timeout=30)

    def convert(name: str, source: Path, output_dir: Path) -> None:
        try:
            barrier.wait()
            pdf_path = service.convert_office_to_pdf(
                str(source), str(output_dir), timeout_seconds=120
            )
            results[name] = pdf_path
        except Exception as exc:  # noqa: BLE001 - captured for assertion below
            results[name] = exc

    thread_a = threading.Thread(
        target=convert, args=("a", source_a, tmp_path / "out-a")
    )
    thread_b = threading.Thread(
        target=convert, args=("b", source_b, tmp_path / "out-b")
    )
    start = time.monotonic()
    thread_a.start()
    thread_b.start()
    thread_a.join(timeout=150)
    thread_b.join(timeout=150)
    elapsed = time.monotonic() - start

    assert not thread_a.is_alive(), "conversion a did not finish in time"
    assert not thread_b.is_alive(), f"conversion b did not finish in time ({elapsed:.1f}s)"

    for name in ("a", "b"):
        outcome = results.get(name)
        assert isinstance(outcome, str), (
            f"conversion {name} failed instead of succeeding: {outcome!r}"
        )
        assert Path(outcome).is_file()
        assert Path(outcome).stat().st_size > 0
