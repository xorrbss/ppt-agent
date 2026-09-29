"""Pins the single env-parsing contract that replaced eighteen private copies.

Each ``on_invalid`` policy is exercised for malformed text, out-of-range
numbers, whitespace and the unset/blank case, and the ``get_env`` wrappers are
shown to read through ``get_setting_env`` so admin-managed values win over
the process environment for registered keys.
"""

from __future__ import annotations

import logging

import pytest

from utils import get_env
from utils.env_parse import (
    FALSY_VALUES,
    TRUTHY_VALUES,
    EnvValueError,
    is_truthy,
    parse_bool,
    parse_float,
    parse_int,
)
from utils.runtime_settings import RuntimeSettings


# --- parse_int -------------------------------------------------------------


@pytest.mark.parametrize("raw", [None, "", "   ", "\t\n"])
@pytest.mark.parametrize("policy", ["default", "warn", "raise", "clamp"])
def test_parse_int_unset_or_blank_is_default_under_every_policy(raw, policy):
    assert parse_int(raw, name="K", default=7, minimum=1, on_invalid=policy) == 7


def test_parse_int_strips_whitespace():
    assert parse_int("  42 \n", name="K", default=0) == 42
    assert parse_int(" -3 ", name="K", default=0) == -3


@pytest.mark.parametrize("raw", ["abc", "1.5", "0x10", "1_000_000_000_000_x", "nan"])
def test_parse_int_malformed_default_policy_is_silent(raw, caplog):
    with caplog.at_level(logging.WARNING, logger="utils.env_parse"):
        assert parse_int(raw, name="K", default=5) == 5
    assert caplog.records == []


def test_parse_int_malformed_warn_policy_logs_once(caplog):
    with caplog.at_level(logging.WARNING, logger="utils.env_parse"):
        assert parse_int("abc", name="K", default=5, on_invalid="warn") == 5
    assert len(caplog.records) == 1
    assert "K" in caplog.records[0].getMessage()
    assert "abc" in caplog.records[0].getMessage()


def test_parse_int_malformed_raise_policy_has_stable_code():
    with pytest.raises(EnvValueError) as info:
        parse_int("abc", name="K", default=5, minimum=1, on_invalid="raise")
    assert info.value.code == "K_must_be_a_positive_integer"
    assert str(info.value) == "K_must_be_a_positive_integer"
    assert info.value.raw == "abc"
    assert isinstance(info.value, ValueError)


def test_parse_int_malformed_clamp_policy_falls_back_to_default():
    assert parse_int("abc", name="K", default=5, minimum=1, on_invalid="clamp") == 5


def test_parse_int_range_default_policy_discards():
    assert parse_int("0", name="K", default=5, minimum=1) == 5
    assert parse_int("900", name="K", default=5, minimum=1, maximum=500) == 5
    assert parse_int("-1", name="K", default=5, minimum=0) == 5


def test_parse_int_range_warn_policy_logs_out_of_range(caplog):
    with caplog.at_level(logging.WARNING, logger="utils.env_parse"):
        assert parse_int("0", name="K", default=5, minimum=1, on_invalid="warn") == 5
    assert "out-of-range" in caplog.records[0].getMessage()


def test_parse_int_range_clamp_policy_clamps_to_nearest_bound():
    assert parse_int("0", name="K", default=5, minimum=1, on_invalid="clamp") == 1
    assert parse_int("-9", name="K", default=5, minimum=1, on_invalid="clamp") == 1
    assert (
        parse_int("900", name="K", default=5, minimum=1, maximum=500, on_invalid="clamp")
        == 500
    )
    assert parse_int("250", name="K", default=5, minimum=1, maximum=500, on_invalid="clamp") == 250


@pytest.mark.parametrize(
    ("raw", "minimum", "maximum", "code"),
    [
        ("0", 1, None, "K_must_be_a_positive_integer"),
        ("0", 1, 500, "K_between_1_and_500"),
        ("900", 1, 500, "K_between_1_and_500"),
        ("-1", 0, None, "K_must_be_a_nonnegative_integer"),
        ("1", 5, None, "K_must_be_at_least_5"),
        ("9", None, 8, "K_must_be_at_most_8"),
        ("abc", None, None, "K_must_be_a_integer"),
    ],
)
def test_parse_int_raise_policy_codes(raw, minimum, maximum, code):
    with pytest.raises(EnvValueError) as info:
        parse_int(raw, name="K", default=5, minimum=minimum, maximum=maximum, on_invalid="raise")
    assert info.value.code == code


def test_parse_int_in_range_is_returned_under_every_policy():
    for policy in ("default", "warn", "raise", "clamp"):
        assert parse_int("3", name="K", default=5, minimum=1, maximum=8, on_invalid=policy) == 3


def test_parse_int_none_default_supports_optional_knobs():
    assert parse_int(None, name="K", default=None, minimum=1) is None
    assert parse_int("0", name="K", default=None, minimum=1) is None
    assert parse_int("abc", name="K", default=None, minimum=1) is None
    assert parse_int("4", name="K", default=None, minimum=1) == 4


# --- parse_float -----------------------------------------------------------


def test_parse_float_parses_and_strips():
    assert parse_float(" 0.25 ", name="K", default=1.0) == 0.25
    assert parse_float("3", name="K", default=1.0) == 3.0


@pytest.mark.parametrize("raw", ["abc", "nan", "inf", "-inf", "1e400"])
def test_parse_float_non_finite_is_invalid(raw):
    assert parse_float(raw, name="K", default=1.5) == 1.5
    with pytest.raises(EnvValueError) as info:
        parse_float(raw, name="K", default=1.5, on_invalid="raise")
    assert info.value.code == "K_must_be_a_number"


def test_parse_float_range_policies():
    assert parse_float("0.01", name="K", default=1.0, minimum=0.05) == 1.0
    assert parse_float("0.01", name="K", default=1.0, minimum=0.05, on_invalid="clamp") == 0.05
    with pytest.raises(EnvValueError) as info:
        parse_float("-1", name="K", default=1.0, minimum=0, on_invalid="raise")
    assert info.value.code == "K_must_be_a_nonnegative_number"


# --- parse_bool / is_truthy ------------------------------------------------


def test_bool_value_sets_are_the_single_source():
    assert TRUTHY_VALUES == frozenset({"1", "true", "yes", "on"})
    assert FALSY_VALUES == frozenset({"0", "false", "no", "off"})


@pytest.mark.parametrize("raw", ["1", "true", "TRUE", " Yes ", "on", "On\n"])
def test_parse_bool_truthy(raw):
    assert parse_bool(raw, default=False) is True
    assert is_truthy(raw) is True


@pytest.mark.parametrize("raw", ["0", "false", "FALSE", " no ", "off", "Off\n"])
def test_parse_bool_falsy(raw):
    assert parse_bool(raw, default=True) is False
    assert is_truthy(raw) is False


@pytest.mark.parametrize("raw", [None, "", "  ", "maybe", "2", "enabled"])
def test_parse_bool_unrecognised_keeps_default_while_is_truthy_is_strict(raw):
    assert parse_bool(raw, default=True) is True
    assert parse_bool(raw, default=False) is False
    assert is_truthy(raw) is False


# --- get_env wrappers ------------------------------------------------------


def test_env_int_reads_unregistered_key_from_process_environment(monkeypatch):
    monkeypatch.setenv("PRESENTON_TEST_UNREGISTERED_INT", " 12 ")
    assert get_env.env_int("PRESENTON_TEST_UNREGISTERED_INT", 3, minimum=1) == 12
    monkeypatch.setenv("PRESENTON_TEST_UNREGISTERED_INT", "x")
    assert get_env.env_int("PRESENTON_TEST_UNREGISTERED_INT", 3, minimum=1) == 3
    with pytest.raises(EnvValueError):
        get_env.env_int("PRESENTON_TEST_UNREGISTERED_INT", 3, minimum=1, on_invalid="raise")
    monkeypatch.delenv("PRESENTON_TEST_UNREGISTERED_INT")
    assert get_env.env_int("PRESENTON_TEST_UNREGISTERED_INT", 3, minimum=1) == 3


def test_env_int_honours_admin_snapshot_for_registered_key(monkeypatch):
    # MEM0_TIMEOUT_SECONDS is a registered fastapi.runtime integer.
    monkeypatch.setenv("MEM0_TIMEOUT_SECONDS", "40")
    assert get_env.env_int("MEM0_TIMEOUT_SECONDS", 15) == 40
    managed = RuntimeSettings.from_sources(environ={}, user_config={"MEM0_TIMEOUT_SECONDS": 25})
    assert get_env.env_int("MEM0_TIMEOUT_SECONDS", 15, settings=managed) == 25


def test_env_float_and_env_bool_wrappers(monkeypatch):
    monkeypatch.setenv("PRESENTON_TEST_UNREGISTERED_FLOAT", "0.5")
    assert get_env.env_float("PRESENTON_TEST_UNREGISTERED_FLOAT", 2.0, minimum=0.05) == 0.5
    monkeypatch.setenv("PRESENTON_TEST_UNREGISTERED_FLOAT", "0.001")
    assert (
        get_env.env_float("PRESENTON_TEST_UNREGISTERED_FLOAT", 2.0, minimum=0.05, on_invalid="clamp")
        == 0.05
    )
    monkeypatch.setenv("PRESENTON_TEST_UNREGISTERED_BOOL", "off")
    assert get_env.env_bool("PRESENTON_TEST_UNREGISTERED_BOOL", True) is False
    monkeypatch.setenv("PRESENTON_TEST_UNREGISTERED_BOOL", "garbage")
    assert get_env.env_bool("PRESENTON_TEST_UNREGISTERED_BOOL", True) is True
    monkeypatch.delenv("PRESENTON_TEST_UNREGISTERED_BOOL")
    assert get_env.env_bool("PRESENTON_TEST_UNREGISTERED_BOOL", False) is False


def test_env_bool_honours_registered_boolean_snapshot():
    managed = RuntimeSettings.from_sources(environ={}, user_config={"MEM0_ENABLED": False})
    assert get_env.env_bool("MEM0_ENABLED", True, settings=managed) is False


def test_optional_positive_int_setting_keeps_none_contract(monkeypatch):
    # A registered integer key is coerced by RuntimeSettings.from_sources
    # before it reaches the parser, so malformed text is a snapshot error
    # there (pre-existing contract); only the range contract is pinned here.
    monkeypatch.setenv("AUTHORED_AUTHOR_CONCURRENCY", "0")
    assert get_env.get_authored_author_concurrency_env() is None
    monkeypatch.setenv("AUTHORED_AUTHOR_CONCURRENCY", "-2")
    assert get_env.get_authored_author_concurrency_env() is None
    monkeypatch.setenv("AUTHORED_AUTHOR_CONCURRENCY", "6")
    assert get_env.get_authored_author_concurrency_env() == 6


def test_optional_nonnegative_float_setting_keeps_none_contract(monkeypatch):
    monkeypatch.setenv("CONTENT_BRIEF_TIMEOUT_SECONDS", "-1")
    assert get_env.get_content_brief_timeout_seconds_env() is None
    monkeypatch.setenv("CONTENT_BRIEF_TIMEOUT_SECONDS", "0")
    assert get_env.get_content_brief_timeout_seconds_env() == 0.0


def test_get_vnext_tenant_id_defaults_and_strips(monkeypatch):
    monkeypatch.delenv("AUTHORED_VNEXT_TENANT_ID", raising=False)
    assert get_env.get_vnext_tenant_id() == "default"
    monkeypatch.setenv("AUTHORED_VNEXT_TENANT_ID", "")
    assert get_env.get_vnext_tenant_id() == "default"
    monkeypatch.setenv("AUTHORED_VNEXT_TENANT_ID", " acme ")
    assert get_env.get_vnext_tenant_id() == "acme"


def test_get_environment_name_normalises_and_takes_caller_default(monkeypatch):
    monkeypatch.delenv("ENVIRONMENT", raising=False)
    assert get_env.get_environment_name() == ""
    assert get_env.get_environment_name("production") == "production"
    monkeypatch.setenv("ENVIRONMENT", " Staging ")
    assert get_env.get_environment_name("production") == "staging"
