from pathlib import Path

import pytest
from fastapi import HTTPException

from services.temp_file_service import TempFileService


def _assert_http_error(
    expected_status: int,
    operation,
) -> None:
    with pytest.raises(HTTPException) as exc_info:
        operation()
    assert exc_info.value.status_code == expected_status


def test_temp_file_service_confines_names_and_round_trips_content(
    tmp_path: Path,
) -> None:
    base_dir = tmp_path / "temp-root"
    service = TempFileService(str(base_dir))

    work_dir = Path(service.create_temp_dir("../job"))
    assert work_dir == base_dir / "job"

    file_path = Path(
        service.create_temp_file(
            r"..\outside.txt",
            "confined",
            str(work_dir),
        )
    )
    assert file_path == work_dir / "outside.txt"
    assert service.read_temp_file(str(file_path), binary=False) == "confined"
    assert not (tmp_path / "outside.txt").exists()


def test_temp_file_service_rejects_external_parent_for_create_and_read(
    tmp_path: Path,
) -> None:
    service = TempFileService(str(tmp_path / "temp-root"))
    outside_dir = tmp_path / "outside"
    outside_dir.mkdir()
    outside_file = outside_dir / "private.txt"
    outside_file.write_text("private", encoding="utf-8")

    _assert_http_error(
        400,
        lambda: service.create_dir_in_dir(str(outside_dir), "job"),
    )
    _assert_http_error(
        400,
        lambda: service.create_temp_file_path("copied.txt", str(outside_dir)),
    )
    _assert_http_error(400, lambda: service.read_temp_file(str(outside_file)))


def test_temp_file_service_resolves_existing_paths_and_rejects_missing_files(
    tmp_path: Path,
) -> None:
    service = TempFileService(str(tmp_path / "temp-root"))
    first_path = service.create_temp_file("first.txt", b"first")
    second_path = service.create_temp_file("second.txt", b"second")

    assert service.resolve_existing_temp_paths([first_path, second_path]) == [
        first_path,
        second_path,
    ]
    assert service.resolve_existing_temp_paths(None) == []
    _assert_http_error(
        404,
        lambda: service.resolve_temp_path("missing.txt", must_exist=True),
    )


def test_temp_file_service_rejects_symlink_escape(tmp_path: Path) -> None:
    service = TempFileService(str(tmp_path / "temp-root"))
    outside_file = tmp_path / "private.txt"
    outside_file.write_text("private", encoding="utf-8")
    linked_file = Path(service.base_dir) / "linked-private.txt"
    try:
        linked_file.symlink_to(outside_file)
    except OSError:
        pytest.skip("symlinks are not available on this platform")

    _assert_http_error(
        400,
        lambda: service.resolve_temp_path(str(linked_file), must_exist=True),
    )


def test_temp_file_service_cleanup_cannot_remove_external_paths(
    tmp_path: Path,
) -> None:
    service = TempFileService(str(tmp_path / "temp-root"))
    outside_file = tmp_path / "keep.txt"
    outside_file.write_text("keep", encoding="utf-8")
    outside_dir = tmp_path / "keep-dir"
    outside_dir.mkdir()

    _assert_http_error(400, lambda: service.cleanup_temp_file(str(outside_file)))
    _assert_http_error(400, lambda: service.cleanup_temp_dir(str(outside_dir)))

    assert outside_file.read_text(encoding="utf-8") == "keep"
    assert outside_dir.is_dir()


def test_temp_file_service_recursively_cleans_only_its_own_directory(
    tmp_path: Path,
) -> None:
    base_dir = tmp_path / "temp-root"
    service = TempFileService(str(base_dir))
    work_dir = Path(service.create_temp_dir("job"))
    nested_dir = work_dir / "nested"
    nested_dir.mkdir()
    (nested_dir / "data.bin").write_bytes(b"data")

    service.cleanup_temp_dir(str(work_dir))

    assert not work_dir.exists()
    assert base_dir.is_dir()


@pytest.mark.parametrize("invalid_name", ["", " ", ".", ".."])
def test_temp_file_service_rejects_empty_or_dot_names(
    tmp_path: Path,
    invalid_name: str,
) -> None:
    service = TempFileService(str(tmp_path / "temp-root"))

    _assert_http_error(
        400,
        lambda: service.create_temp_file_path(invalid_name),
    )
