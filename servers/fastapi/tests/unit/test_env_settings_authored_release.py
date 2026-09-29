import ast
from pathlib import Path

import pytest

from utils.env_settings import authored_release
from utils.env_settings.authored_release import (
    ZERO_DIGEST,
    digest_or_zero,
    get_authored_gate0_settings,
    get_authored_release_settings,
    get_editable_mvp_trust_digest,
    normalise_deployment_environment,
    parse_kill_switches,
    stripped,
)


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    for name in authored_release.ENV_NAMES:
        monkeypatch.delenv(name, raising=False)


def test_defaults_are_unset():
    release = get_authored_release_settings()
    assert release.deployment_environment == "development"
    assert release.kill_switches == ()
    assert release.environment_digests == (ZERO_DIGEST, ZERO_DIGEST, ZERO_DIGEST)
    for field in (
        "os_build",
        "office_build",
        "renderer_image_digest",
        "font_inventory_digest",
        "backend_build_digest",
        "environment_attestation_digest",
        "worker_attestation_digest",
        "feature_set_digest",
        "evidence_policy_digest",
        "source_tree_census_digest",
        "source_head_commit",
        "gate_minus1_manifest_digest",
        "gate_minus1_result_set_digest",
        "gate_minus1_receipt_artifact_digest",
        "gate_minus1_validator_contract_digest",
        "signer_trust_bundle_json",
        "gate1_execution_nonce",
        "readback_ed25519_private_key_base64",
        "authority_environment_id",
        "ci_evidence_hmac_key",
        "backend_commit_sha",
        "visual_gate_hmac_key",
    ):
        assert getattr(release, field) is None, field
    gate0 = get_authored_gate0_settings()
    assert (gate0.manifest_digest, gate0.ed25519_private_key_base64, gate0.signer_key_id) == (
        None,
        None,
        None,
    )
    assert get_editable_mvp_trust_digest() == ""


def test_every_env_name_maps_to_a_field(monkeypatch):
    """Each AUTHORED_RELEASE_* / GATE0 name lands in the matching dataclass attribute."""
    for name in authored_release.ENV_NAMES:
        monkeypatch.setenv(name, f" value-of-{name} ")
    monkeypatch.setenv("AUTHORED_RELEASE_DEPLOYMENT_ENVIRONMENT", "staging")
    monkeypatch.setenv("AUTHORED_RELEASE_KILL_SWITCHES", "b, a ,,a")
    release = get_authored_release_settings()
    gate0 = get_authored_gate0_settings()
    for name in authored_release.ENV_NAMES:
        if name in {
            "AUTHORED_RELEASE_DEPLOYMENT_ENVIRONMENT",
            "AUTHORED_RELEASE_KILL_SWITCHES",
            "AUTHORED_EDITABLE_MVP_TRUST_SHA256",
        }:
            continue
        if name.startswith("AUTHORED_GATE0_STRUCTURAL_"):
            field = name.removeprefix("AUTHORED_GATE0_STRUCTURAL_").lower()
            assert getattr(gate0, field) == f" value-of-{name} ", name
        else:
            field = name.removeprefix("AUTHORED_RELEASE_").lower()
            assert getattr(release, field) == f" value-of-{name} ", name
    assert release.deployment_environment == "staging"
    assert release.kill_switches == ("a", "b")
    assert get_editable_mvp_trust_digest() == "value-of-AUTHORED_EDITABLE_MVP_TRUST_SHA256"


def test_raw_values_are_not_normalised(monkeypatch):
    monkeypatch.setenv("AUTHORED_RELEASE_FONT_INVENTORY_DIGEST", " sha256:AB ")
    monkeypatch.setenv("AUTHORED_RELEASE_OS_BUILD", "")
    release = get_authored_release_settings()
    assert release.font_inventory_digest == " sha256:AB "
    assert stripped(release.font_inventory_digest) == "sha256:AB"
    assert release.os_build == ""
    assert not release.os_build
    # os.getenv(name, ZERO) semantics: unset -> zero, blank stays blank.
    assert release.environment_digests == (ZERO_DIGEST, " sha256:AB ", ZERO_DIGEST)
    assert digest_or_zero("") == ""
    assert digest_or_zero(None) == ZERO_DIGEST


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (None, "development"),
        ("", "development"),
        ("production", "production"),
        ("staging", "staging"),
        ("Production", "development"),
        (" production", "development"),
        ("prod", "development"),
    ],
)
def test_deployment_environment_is_exact_match_only(raw, expected):
    assert normalise_deployment_environment(raw) == expected


def test_kill_switch_parsing():
    assert parse_kill_switches(None) == ()
    assert parse_kill_switches(" , ,") == ()
    assert parse_kill_switches("x.export, a.b,x.export") == ("a.b", "x.export")


def test_release_projection_reads_through_accessor(monkeypatch):
    from services.authored_vnext.semantic.readiness import (
        release_authority_target_from_environment,
    )

    target = release_authority_target_from_environment()
    assert target.configured is False
    assert target.environment.os_build == "unconfigured"
    assert target.environment.renderer_image_digest == ZERO_DIGEST
    monkeypatch.setenv("AUTHORED_RELEASE_DEPLOYMENT_ENVIRONMENT", "production")
    monkeypatch.setenv("AUTHORED_RELEASE_OS_BUILD", "os-1")
    monkeypatch.setenv("AUTHORED_RELEASE_OFFICE_BUILD", "office-1")
    monkeypatch.setenv("AUTHORED_RELEASE_RENDERER_IMAGE_DIGEST", "sha256:" + "1" * 64)
    monkeypatch.setenv("AUTHORED_RELEASE_FONT_INVENTORY_DIGEST", "sha256:" + "2" * 64)
    monkeypatch.setenv("AUTHORED_RELEASE_BACKEND_BUILD_DIGEST", "sha256:" + "3" * 64)
    target = release_authority_target_from_environment()
    assert target.configured is True
    assert target.environment.deployment_environment == "production"
    assert target.environment.os_build == "os-1"
    assert target.environment.font_inventory_digest == "sha256:" + "2" * 64


def test_module_layering_and_no_import_time_reads():
    source = Path(authored_release.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    modules = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules |= {alias.name for alias in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
    assert not any(m == "services" or m.startswith("services.") for m in modules)
    assert not any(m == "api" or m.startswith("api.") for m in modules)
    assert "os" not in modules
    assert set(authored_release.ENV_NAMES) == {
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and node.value.startswith(("AUTHORED_RELEASE_", "AUTHORED_GATE0_", "AUTHORED_EDITABLE_"))
    }
