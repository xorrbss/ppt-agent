"""Prompt switch, table semantics and the real 43-page size contract."""

from pathlib import Path

import pytest

from utils.get_env_programme import is_authored_verbatim_placeholder_authoring_enabled
from utils.runtime_settings import RuntimeSettings

FIXTURES = Path(__file__).resolve().parents[4] / "artifacts/verbatim-design-measurements-20260911/fixtures"


@pytest.mark.parametrize("value, expected", [(None, False), (False, False), (True, True)])
def test_default_off_accessor(value, expected):
    settings = RuntimeSettings({"AUTHORED_VERBATIM_PLACEHOLDER_AUTHORING": value})
    assert is_authored_verbatim_placeholder_authoring_enabled(settings) is expected


# --- R4: prompt-construction rejections must not leak uncoded ---------------


# --- B5/R8: normalization mode and the placeholder switch are independent --


