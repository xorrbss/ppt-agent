"""Release-authority pins, attestation digests and signing material.

Covers every ``AUTHORED_RELEASE_*`` and ``AUTHORED_GATE0_STRUCTURAL_*`` knob
read by ``services/authored_vnext/semantic/`` (readiness v2/v3, release
authority v3, Gate 0 structural v3, evidence completion v3, CI evidence v3 and
the editable-MVP seal import).

Layering: this module may import only stdlib, ``utils.get_env``,
``utils.env_parse`` and (type only) ``utils.runtime_settings``.  It never
imports ``services`` or ``api``.  Nothing is read at import time.

Value contract
--------------
Every field of :class:`AuthoredReleaseSettings` and
:class:`AuthoredGate0Settings` is the *raw* environment text, ``None`` when
the variable is unset.  Consumers differ on what an unset pin means (``""``
and fail closed, the all-zero digest, a synthetic Gate 0 placeholder), so the
normalisation stays with the caller and the two helpers below:

* :func:`stripped` -- ``(value or "").strip()`` for pins that must be
  non-empty to be trusted.
* :func:`digest_or_zero` -- the legacy ``os.getenv(name, ZERO_DIGEST)``
  contract of the readiness projections: unset yields :data:`ZERO_DIGEST`,
  a set-but-blank value stays blank.

Two fields are normalised in the accessor because every reader agrees on
the rule: ``deployment_environment`` (``development`` unless exactly
``staging``/``production``) and ``kill_switches`` (comma-split, stripped,
blank items dropped, sorted, de-duplicated).

Key material (``*_KEY*``, ``*_HMAC_KEY``, the trust bundle) is returned as
text for the caller to decode; it is never logged here.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from utils.get_env import get_setting_env

if TYPE_CHECKING:
    from utils.runtime_settings import RuntimeSettings

ZERO_DIGEST = "sha256:" + "0" * 64
DEPLOYMENT_ENVIRONMENTS = frozenset({"development", "staging", "production"})
DEFAULT_DEPLOYMENT_ENVIRONMENT = "development"

VISUAL_GATE_SIGNING_KEY_ENV = "AUTHORED_RELEASE_VISUAL_GATE_HMAC_KEY"
EDITABLE_MVP_TRUST_DIGEST_ENV = "AUTHORED_EDITABLE_MVP_TRUST_SHA256"

ENV_NAMES: tuple[str, ...] = (
    "AUTHORED_RELEASE_DEPLOYMENT_ENVIRONMENT",
    "AUTHORED_RELEASE_OS_BUILD",
    "AUTHORED_RELEASE_OFFICE_BUILD",
    "AUTHORED_RELEASE_RENDERER_IMAGE_DIGEST",
    "AUTHORED_RELEASE_FONT_INVENTORY_DIGEST",
    "AUTHORED_RELEASE_BACKEND_BUILD_DIGEST",
    "AUTHORED_RELEASE_KILL_SWITCHES",
    "AUTHORED_RELEASE_ENVIRONMENT_ATTESTATION_DIGEST",
    "AUTHORED_RELEASE_WORKER_ATTESTATION_DIGEST",
    "AUTHORED_RELEASE_FEATURE_SET_DIGEST",
    "AUTHORED_RELEASE_EVIDENCE_POLICY_DIGEST",
    "AUTHORED_RELEASE_SOURCE_TREE_CENSUS_DIGEST",
    "AUTHORED_RELEASE_SOURCE_HEAD_COMMIT",
    "AUTHORED_RELEASE_GATE_MINUS1_MANIFEST_DIGEST",
    "AUTHORED_RELEASE_GATE_MINUS1_RESULT_SET_DIGEST",
    "AUTHORED_RELEASE_GATE_MINUS1_RECEIPT_ARTIFACT_DIGEST",
    "AUTHORED_RELEASE_GATE_MINUS1_VALIDATOR_CONTRACT_DIGEST",
    "AUTHORED_RELEASE_SIGNER_TRUST_BUNDLE_JSON",
    "AUTHORED_RELEASE_GATE1_EXECUTION_NONCE",
    "AUTHORED_RELEASE_READBACK_ED25519_PRIVATE_KEY_BASE64",
    "AUTHORED_RELEASE_AUTHORITY_ENVIRONMENT_ID",
    "AUTHORED_RELEASE_CI_EVIDENCE_HMAC_KEY",
    "AUTHORED_RELEASE_BACKEND_COMMIT_SHA",
    "AUTHORED_RELEASE_VISUAL_GATE_HMAC_KEY",
    "AUTHORED_GATE0_STRUCTURAL_MANIFEST_DIGEST",
    "AUTHORED_GATE0_STRUCTURAL_ED25519_PRIVATE_KEY_BASE64",
    "AUTHORED_GATE0_STRUCTURAL_SIGNER_KEY_ID",
    "AUTHORED_EDITABLE_MVP_TRUST_SHA256",
)


def stripped(value: str | None) -> str:
    """Pin text with surrounding whitespace removed; unset becomes ``""``."""
    return (value or "").strip()


def digest_or_zero(value: str | None) -> str:
    """Unset -> :data:`ZERO_DIGEST`; any set value (even blank) is kept as is."""
    return ZERO_DIGEST if value is None else value


def normalise_deployment_environment(value: str | None) -> str:
    """Only ``staging``/``production`` are honoured; anything else is development."""
    if value in DEPLOYMENT_ENVIRONMENTS:
        return value  # type: ignore[return-value]
    return DEFAULT_DEPLOYMENT_ENVIRONMENT


def parse_kill_switches(value: str | None) -> tuple[str, ...]:
    """Comma-separated capability ids; blanks dropped, sorted, de-duplicated."""
    return tuple(sorted({item.strip() for item in (value or "").split(",") if item.strip()}))


@dataclass(frozen=True, slots=True)
class AuthoredReleaseSettings:
    """Raw ``AUTHORED_RELEASE_*`` values; ``None`` means unset (see module doc)."""

    deployment_environment: str
    os_build: str | None
    office_build: str | None
    renderer_image_digest: str | None
    font_inventory_digest: str | None
    backend_build_digest: str | None
    kill_switches: tuple[str, ...]
    environment_attestation_digest: str | None
    worker_attestation_digest: str | None
    feature_set_digest: str | None
    evidence_policy_digest: str | None
    source_tree_census_digest: str | None
    source_head_commit: str | None
    gate_minus1_manifest_digest: str | None
    gate_minus1_result_set_digest: str | None
    gate_minus1_receipt_artifact_digest: str | None
    gate_minus1_validator_contract_digest: str | None
    signer_trust_bundle_json: str | None
    gate1_execution_nonce: str | None
    readback_ed25519_private_key_base64: str | None
    authority_environment_id: str | None
    ci_evidence_hmac_key: str | None
    backend_commit_sha: str | None
    visual_gate_hmac_key: str | None

    @property
    def environment_digests(self) -> tuple[str, str, str]:
        """Renderer image, font inventory and backend build digests, zero-filled when unset."""
        return (
            digest_or_zero(self.renderer_image_digest),
            digest_or_zero(self.font_inventory_digest),
            digest_or_zero(self.backend_build_digest),
        )


def get_authored_release_settings(
    settings: RuntimeSettings | None = None,
) -> AuthoredReleaseSettings:
    def read_env(name: str) -> str | None:
        return get_setting_env(name, settings)

    return AuthoredReleaseSettings(
        deployment_environment=normalise_deployment_environment(
            read_env("AUTHORED_RELEASE_DEPLOYMENT_ENVIRONMENT")
        ),
        os_build=read_env("AUTHORED_RELEASE_OS_BUILD"),
        office_build=read_env("AUTHORED_RELEASE_OFFICE_BUILD"),
        renderer_image_digest=read_env("AUTHORED_RELEASE_RENDERER_IMAGE_DIGEST"),
        font_inventory_digest=read_env("AUTHORED_RELEASE_FONT_INVENTORY_DIGEST"),
        backend_build_digest=read_env("AUTHORED_RELEASE_BACKEND_BUILD_DIGEST"),
        kill_switches=parse_kill_switches(read_env("AUTHORED_RELEASE_KILL_SWITCHES")),
        environment_attestation_digest=read_env(
            "AUTHORED_RELEASE_ENVIRONMENT_ATTESTATION_DIGEST"
        ),
        worker_attestation_digest=read_env("AUTHORED_RELEASE_WORKER_ATTESTATION_DIGEST"),
        feature_set_digest=read_env("AUTHORED_RELEASE_FEATURE_SET_DIGEST"),
        evidence_policy_digest=read_env("AUTHORED_RELEASE_EVIDENCE_POLICY_DIGEST"),
        source_tree_census_digest=read_env("AUTHORED_RELEASE_SOURCE_TREE_CENSUS_DIGEST"),
        source_head_commit=read_env("AUTHORED_RELEASE_SOURCE_HEAD_COMMIT"),
        gate_minus1_manifest_digest=read_env(
            "AUTHORED_RELEASE_GATE_MINUS1_MANIFEST_DIGEST"
        ),
        gate_minus1_result_set_digest=read_env(
            "AUTHORED_RELEASE_GATE_MINUS1_RESULT_SET_DIGEST"
        ),
        gate_minus1_receipt_artifact_digest=read_env(
            "AUTHORED_RELEASE_GATE_MINUS1_RECEIPT_ARTIFACT_DIGEST"
        ),
        gate_minus1_validator_contract_digest=read_env(
            "AUTHORED_RELEASE_GATE_MINUS1_VALIDATOR_CONTRACT_DIGEST"
        ),
        signer_trust_bundle_json=read_env("AUTHORED_RELEASE_SIGNER_TRUST_BUNDLE_JSON"),
        gate1_execution_nonce=read_env("AUTHORED_RELEASE_GATE1_EXECUTION_NONCE"),
        readback_ed25519_private_key_base64=read_env(
            "AUTHORED_RELEASE_READBACK_ED25519_PRIVATE_KEY_BASE64"
        ),
        authority_environment_id=read_env("AUTHORED_RELEASE_AUTHORITY_ENVIRONMENT_ID"),
        ci_evidence_hmac_key=read_env("AUTHORED_RELEASE_CI_EVIDENCE_HMAC_KEY"),
        backend_commit_sha=read_env("AUTHORED_RELEASE_BACKEND_COMMIT_SHA"),
        visual_gate_hmac_key=read_env("AUTHORED_RELEASE_VISUAL_GATE_HMAC_KEY"),
    )


@dataclass(frozen=True, slots=True)
class AuthoredGate0Settings:
    """Raw ``AUTHORED_GATE0_STRUCTURAL_*`` values; ``None`` means unset."""

    manifest_digest: str | None
    ed25519_private_key_base64: str | None
    signer_key_id: str | None


def get_authored_gate0_settings(
    settings: RuntimeSettings | None = None,
) -> AuthoredGate0Settings:
    return AuthoredGate0Settings(
        manifest_digest=get_setting_env(
            "AUTHORED_GATE0_STRUCTURAL_MANIFEST_DIGEST", settings
        ),
        ed25519_private_key_base64=get_setting_env(
            "AUTHORED_GATE0_STRUCTURAL_ED25519_PRIVATE_KEY_BASE64", settings
        ),
        signer_key_id=get_setting_env(
            "AUTHORED_GATE0_STRUCTURAL_SIGNER_KEY_ID", settings
        ),
    )


def get_editable_mvp_trust_digest(settings: RuntimeSettings | None = None) -> str:
    """Pinned sha256 of the editable-MVP verifier trust file, stripped (``""`` unset)."""
    return stripped(get_setting_env("AUTHORED_EDITABLE_MVP_TRUST_SHA256", settings))
