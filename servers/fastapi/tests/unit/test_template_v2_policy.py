import pytest

from scripts.check_template_v2_canary import main as check_canary
from templates.v2.policy import (
    StructuredTemplatePolicyError,
    get_structured_template_policy,
)


def test_missing_configuration_is_default_off_and_not_canary_ready():
    policy = get_structured_template_policy({})

    assert policy.creation_enabled is False
    assert policy.allowed_template_ids == frozenset()
    assert policy.canary_readiness().as_dict() == {
        "ready": False,
        "code": "template_v2_feature_disabled",
        "feature_enabled": False,
        "configuration_valid": True,
        "allowlisted_template_count": 0,
        "pptx_analyzer": "deterministic",
    }


def test_exact_flag_and_explicit_allowlist_are_both_required_for_canary():
    no_allowlist = get_structured_template_policy({"ENABLE_TEMPLATE_V2": "true"})
    ready = get_structured_template_policy(
        {
            "ENABLE_TEMPLATE_V2": "true",
            "TEMPLATE_V2_TEMPLATE_ALLOWLIST": "canary-a, canary-b",
        }
    )

    assert no_allowlist.canary_readiness().code == "template_v2_allowlist_required"
    assert no_allowlist.canary_readiness().ready is False
    assert ready.canary_readiness().as_dict() == {
        "ready": True,
        "code": "template_v2_canary_ready",
        "feature_enabled": True,
        "configuration_valid": True,
        "allowlisted_template_count": 2,
        "pptx_analyzer": "deterministic",
    }
    assert ready.can_discover("canary-a") is True
    assert ready.can_discover("not-allowlisted") is False
    ready.require_write_enabled("canary-b")


@pytest.mark.parametrize("value", ["1", "yes", "on", "enabled", "tru"])
def test_ambiguous_truthy_values_fail_closed(value):
    policy = get_structured_template_policy(
        {
            "ENABLE_TEMPLATE_V2": value,
            "TEMPLATE_V2_TEMPLATE_ALLOWLIST": "canary-a",
        }
    )

    readiness = policy.canary_readiness()
    assert policy.creation_enabled is False
    assert policy.allowed_template_ids == frozenset()
    assert readiness.ready is False
    assert readiness.code == "template_v2_flag_invalid"
    assert readiness.configuration_valid is False
    with pytest.raises(
        StructuredTemplatePolicyError,
        match="template_v2_rollout_config_invalid",
    ):
        policy.require_write_enabled("canary-a")


@pytest.mark.parametrize(
    "allowlist",
    [
        "*",
        "canary-a,",
        ",canary-a",
        "canary-a,,canary-b",
        "canary-a,canary-a",
        "canary-a,\ncanary-b",
        "x" * 129,
    ],
)
def test_malformed_allowlist_is_never_partially_honored(allowlist):
    policy = get_structured_template_policy(
        {
            "ENABLE_TEMPLATE_V2": "true",
            "TEMPLATE_V2_TEMPLATE_ALLOWLIST": allowlist,
        }
    )

    readiness = policy.canary_readiness()
    assert policy.creation_enabled is False
    assert policy.allowed_template_ids == frozenset()
    assert policy.can_discover("canary-a") is False
    assert readiness.code == "template_v2_allowlist_invalid"
    assert readiness.allowlisted_template_count == 0
    with pytest.raises(
        StructuredTemplatePolicyError,
        match="template_v2_rollout_config_invalid",
    ):
        policy.require_write_enabled("canary-a")


@pytest.mark.parametrize("environ", [{}, {"TEMPLATE_V2_PPTX_ANALYZER": "   "}])
def test_unset_pptx_analyzer_defaults_to_the_deterministic_parser(environ):
    policy = get_structured_template_policy(environ)

    readiness = policy.canary_readiness()
    assert policy.pptx_analyzer == "deterministic"
    assert policy.configuration_error is None
    assert readiness.pptx_analyzer == "deterministic"
    assert readiness.configuration_valid is True


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("deterministic", "deterministic"),
        ("  DETERMINISTIC\n", "deterministic"),
        ("runtime", "runtime"),
        (" Runtime ", "runtime"),
    ],
)
def test_known_pptx_analyzers_are_selected_without_affecting_readiness(
    value, expected
):
    policy = get_structured_template_policy(
        {
            "ENABLE_TEMPLATE_V2": "true",
            "TEMPLATE_V2_TEMPLATE_ALLOWLIST": "canary-a",
            "TEMPLATE_V2_PPTX_ANALYZER": value,
        }
    )

    readiness = policy.canary_readiness()
    assert policy.pptx_analyzer == expected
    assert policy.configuration_error is None
    assert readiness.pptx_analyzer == expected
    assert readiness.ready is True
    assert readiness.code == "template_v2_canary_ready"


@pytest.mark.parametrize(
    "value", ["vision", "runtim", "true", "on", "deterministic,runtime"]
)
def test_unknown_pptx_analyzer_fails_closed_instead_of_using_the_default(value):
    policy = get_structured_template_policy(
        {
            "ENABLE_TEMPLATE_V2": "true",
            "TEMPLATE_V2_TEMPLATE_ALLOWLIST": "canary-a",
            "TEMPLATE_V2_PPTX_ANALYZER": value,
        }
    )

    readiness = policy.canary_readiness()
    assert policy.creation_enabled is False
    assert policy.allowed_template_ids == frozenset()
    assert policy.pptx_analyzer == "deterministic"
    assert policy.can_discover("canary-a") is False
    assert readiness.ready is False
    assert readiness.code == "template_v2_pptx_analyzer_invalid"
    assert readiness.configuration_valid is False
    with pytest.raises(
        StructuredTemplatePolicyError,
        match="template_v2_rollout_config_invalid",
    ):
        policy.require_write_enabled("canary-a")


def test_disabled_flag_can_stage_a_valid_allowlist_without_enabling_writes():
    policy = get_structured_template_policy(
        {
            "ENABLE_TEMPLATE_V2": "false",
            "TEMPLATE_V2_TEMPLATE_ALLOWLIST": "canary-a",
        }
    )

    readiness = policy.canary_readiness()
    assert readiness.ready is False
    assert readiness.code == "template_v2_feature_disabled"
    assert readiness.allowlisted_template_count == 1
    assert policy.can_discover("canary-a") is False
    with pytest.raises(
        StructuredTemplatePolicyError,
        match="template_v2_creation_disabled",
    ):
        policy.require_write_enabled("canary-a")


def test_rollout_policy_does_not_make_existing_v2_unreadable():
    disabled = get_structured_template_policy({})
    invalid = get_structured_template_policy(
        {
            "ENABLE_TEMPLATE_V2": "on",
            "TEMPLATE_V2_TEMPLATE_ALLOWLIST": "*",
        }
    )

    assert disabled.can_read({"version": "v2-standard"}) is True
    assert invalid.can_read({"version": "v2-standard"}) is True
    assert invalid.can_read({"version": "v1"}) is False


def test_readiness_command_is_content_free_and_returns_go(
    monkeypatch, capsys
):
    monkeypatch.setenv("ENABLE_TEMPLATE_V2", "true")
    monkeypatch.setenv("TEMPLATE_V2_TEMPLATE_ALLOWLIST", "secret-canary-id")

    assert check_canary() == 0
    output = capsys.readouterr().out
    assert '"ready": true' in output
    assert '"allowlisted_template_count": 1' in output
    assert "secret-canary-id" not in output


def test_readiness_command_returns_no_go_by_default(monkeypatch, capsys):
    monkeypatch.delenv("ENABLE_TEMPLATE_V2", raising=False)
    monkeypatch.delenv("TEMPLATE_V2_TEMPLATE_ALLOWLIST", raising=False)

    assert check_canary() == 2
    output = capsys.readouterr().out
    assert '"ready": false' in output
    assert "template_v2_feature_disabled" in output


def test_readiness_command_rejects_production_sqlite(monkeypatch, capsys):
    monkeypatch.setenv("ENABLE_TEMPLATE_V2", "true")
    monkeypatch.setenv("TEMPLATE_V2_TEMPLATE_ALLOWLIST", "secret-canary-id")
    monkeypatch.setenv("TEMPLATE_V2_DEPLOYMENT_TIER", "production")

    assert check_canary() == 2
    output = capsys.readouterr().out
    assert '"ready": false' in output
    assert "template_v2_managed_canary_requires_postgresql" in output
    assert "secret-canary-id" not in output
