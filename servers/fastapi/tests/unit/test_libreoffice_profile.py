from pathlib import Path
from urllib.parse import urlparse
from urllib.request import url2pathname

import pytest

from services.libreoffice_profile import unique_user_installation_arg


def _profile_dir_from_arg(arg: str) -> Path:
    assert arg.startswith("-env:UserInstallation=")
    uri = arg[len("-env:UserInstallation=") :]
    parsed = urlparse(uri)
    assert parsed.scheme == "file"
    return Path(url2pathname(parsed.path))


def test_arg_is_a_well_formed_file_uri_pointing_at_an_existing_directory():
    with unique_user_installation_arg() as arg:
        profile_dir = _profile_dir_from_arg(arg)
        assert profile_dir.is_dir()


def test_directory_is_removed_after_the_context_exits():
    with unique_user_installation_arg() as arg:
        profile_dir = _profile_dir_from_arg(arg)
        assert profile_dir.is_dir()

    assert not profile_dir.exists()


def test_directory_is_removed_even_when_the_body_raises():
    captured_dir = None

    with pytest.raises(RuntimeError):
        with unique_user_installation_arg() as arg:
            captured_dir = _profile_dir_from_arg(arg)
            raise RuntimeError("simulated conversion failure")

    assert captured_dir is not None
    assert not captured_dir.exists()


def test_two_calls_yield_distinct_profile_directories():
    with unique_user_installation_arg() as first_arg:
        first_dir = _profile_dir_from_arg(first_arg)
        with unique_user_installation_arg() as second_arg:
            second_dir = _profile_dir_from_arg(second_arg)
            assert first_dir != second_dir
            assert first_dir.is_dir()
            assert second_dir.is_dir()


def test_prefix_is_applied_to_the_generated_directory_name():
    with unique_user_installation_arg(prefix="custom-prefix-") as arg:
        profile_dir = _profile_dir_from_arg(arg)
        assert profile_dir.name.startswith("custom-prefix-")
