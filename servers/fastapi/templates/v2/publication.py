"""Fail-closed publication policy for Template V2 catalog entries.

Rollout allowlists answer *which* template identifiers may be considered.  This
module answers the separate question of whether a considered template carries
enough durable evidence to be shown to new users.  The evidence lives in the
``assets`` JSON column so legacy rows remain readable without a schema migration.

Two preservation modes are admitted:

* ``native_clone`` proves that the retained source PPTX and every required native
  editing capability were preflighted.
* ``reconstructed`` proves that the deterministic native compiler successfully
  compiled the reconstructed layout without an unsupported fallback.

Missing, malformed, blocked, or future/unknown evidence is deliberately hidden
from discovery.  This policy does not affect reads of already persisted decks.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import re
from typing import Any


TEMPLATE_V2_NATIVE_SOURCE_SCHEMA = "presenton.template-v2-native-source/v1"
TEMPLATE_V2_PUBLICATION_SCHEMA = "presenton.template-v2-publication/v1"
TEMPLATE_V2_NATIVE_PPTX_SCHEMA = "presenton.template-v2-native-pptx/v1"
TEMPLATE_V2_NATIVE_PPTX_COMPILER_NAME = "presenton-template-v2-native-ooxml"
TEMPLATE_V2_NATIVE_PPTX_COMPILER_VERSION = "1"

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_PREFIXED_SHA256_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_SLIDE_PART_RE = re.compile(r"^ppt/slides/slide[1-9][0-9]*\.xml$")
_REQUIRED_NATIVE_CLONE_CAPABILITIES = frozenset(
    {
        "native_clone",
        "text_patch",
        "table_patch",
        "master_layout_theme",
        "relationships_media",
    }
)


@dataclass(frozen=True, slots=True)
class TemplateV2PublicationDecision:
    """Content-free decision suitable for filtering and telemetry."""

    publishable: bool
    reason_code: str
    preservation_mode: str | None = None


def _deny(
    reason_code: str,
    *,
    preservation_mode: str | None = None,
) -> TemplateV2PublicationDecision:
    return TemplateV2PublicationDecision(
        publishable=False,
        reason_code=reason_code,
        preservation_mode=preservation_mode,
    )


def _allow(preservation_mode: str) -> TemplateV2PublicationDecision:
    return TemplateV2PublicationDecision(
        publishable=True,
        reason_code="template_v2_publication_ready",
        preservation_mode=preservation_mode,
    )


def _nonempty_string(value: object, *, maximum: int) -> bool:
    return (
        isinstance(value, str)
        and value == value.strip()
        and 0 < len(value) <= maximum
        and value.isprintable()
    )


def _publication_metadata(assets: Mapping[str, Any]) -> Mapping[str, Any] | None:
    nested = assets.get("native_pptx")
    if nested is None:
        return assets
    if not isinstance(nested, Mapping):
        return None
    return nested


def _publication_is_explicit(metadata: Mapping[str, Any]) -> bool:
    if metadata.get("publication_status") != "publishable":
        return False
    explicit = metadata.get("publishable")
    return explicit is None or explicit is True


def _native_clone_evidence_is_valid(metadata: Mapping[str, Any]) -> bool:
    if metadata.get("schema_version") != TEMPLATE_V2_NATIVE_SOURCE_SCHEMA:
        return False
    if not _nonempty_string(metadata.get("storage_key"), maximum=1024):
        return False
    if not isinstance(metadata.get("source_sha256"), str) or not _SHA256_RE.fullmatch(
        metadata["source_sha256"]
    ):
        return False
    source_size = metadata.get("source_size_bytes")
    if (
        not isinstance(source_size, int)
        or isinstance(source_size, bool)
        or source_size <= 0
    ):
        return False

    source_parts = metadata.get("layout_source_parts")
    if not isinstance(source_parts, Mapping) or not source_parts:
        return False
    normalized_parts: list[str] = []
    for layout_id, source_part in source_parts.items():
        if not _nonempty_string(layout_id, maximum=128):
            return False
        if not isinstance(source_part, str) or not _SLIDE_PART_RE.fullmatch(
            source_part
        ):
            return False
        normalized_parts.append(source_part)
    if len(normalized_parts) != len(set(normalized_parts)):
        return False

    capabilities = metadata.get("capabilities")
    if not isinstance(capabilities, Mapping):
        return False
    if frozenset(capabilities) != _REQUIRED_NATIVE_CLONE_CAPABILITIES:
        return False
    return all(capabilities[name] is True for name in capabilities)


def _reconstructed_evidence_is_valid(metadata: Mapping[str, Any]) -> bool:
    if metadata.get("schema_version") != TEMPLATE_V2_PUBLICATION_SCHEMA:
        return False
    # Reconstructed publication always needs an explicit boolean opt-in.  This
    # is stricter than native-clone rows, whose versioned source contract uses
    # publication_status as the opt-in for backward compatibility.
    if metadata.get("publishable") is not True:
        return False
    preflight = metadata.get("native_pptx_preflight")
    if not isinstance(preflight, Mapping):
        return False
    if preflight.get("schema_version") != TEMPLATE_V2_NATIVE_PPTX_SCHEMA:
        return False
    if (
        preflight.get("compiler_name")
        != TEMPLATE_V2_NATIVE_PPTX_COMPILER_NAME
        or preflight.get("compiler_version")
        != TEMPLATE_V2_NATIVE_PPTX_COMPILER_VERSION
    ):
        return False
    if preflight.get("preservation_mode") != "reconstructed":
        return False
    if preflight.get("status") != "compiled":
        return False
    if preflight.get("unsupported") is not None:
        return False
    for digest_name in ("structural_sha256", "package_sha256"):
        digest = preflight.get(digest_name)
        if not isinstance(digest, str) or not _PREFIXED_SHA256_RE.fullmatch(digest):
            return False
    return True


def evaluate_template_v2_publication(
    assets: object,
) -> TemplateV2PublicationDecision:
    """Evaluate catalog publication evidence without mutating it.

    The caller should combine this decision with the existing exact rollout
    allowlist.  A false result hides the definition from *new* discovery only;
    direct reads remain governed by the existing compatibility policy.
    """

    if not isinstance(assets, Mapping):
        return _deny("template_v2_publication_evidence_missing")
    metadata = _publication_metadata(assets)
    if metadata is None:
        return _deny("template_v2_publication_evidence_invalid")

    preservation_mode = metadata.get("preservation_mode")
    if not isinstance(preservation_mode, str):
        return _deny("template_v2_publication_mode_missing")
    if not _publication_is_explicit(metadata):
        return _deny(
            "template_v2_publication_not_approved",
            preservation_mode=preservation_mode,
        )

    if preservation_mode == "native_clone":
        valid = _native_clone_evidence_is_valid(metadata)
    elif preservation_mode == "reconstructed":
        valid = _reconstructed_evidence_is_valid(metadata)
    else:
        return _deny(
            "template_v2_publication_mode_unsupported",
            preservation_mode=preservation_mode,
        )

    if not valid:
        return _deny(
            "template_v2_publication_evidence_invalid",
            preservation_mode=preservation_mode,
        )
    return _allow(preservation_mode)


__all__ = [
    "TEMPLATE_V2_NATIVE_PPTX_COMPILER_NAME",
    "TEMPLATE_V2_NATIVE_PPTX_COMPILER_VERSION",
    "TEMPLATE_V2_NATIVE_PPTX_SCHEMA",
    "TEMPLATE_V2_NATIVE_SOURCE_SCHEMA",
    "TEMPLATE_V2_PUBLICATION_SCHEMA",
    "TemplateV2PublicationDecision",
    "evaluate_template_v2_publication",
]
