"""Pure capability analysis for deterministic presentation export routing."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal
import uuid

from models.authored_vnext.deck.scene import SceneAssetManifestEntry
from templates.native_ui.models.layouts import SlideLayout


PresentationExportAdapterId = Literal[
    "native_clone",
    "native_reconstructed",
    "general",
]


class PresentationExportContractError(ValueError):
    """Stable, non-fallback export contract failure."""

    def __init__(self, code: str, *, path: str | None = None) -> None:
        self.code = code
        self.path = path
        super().__init__(f"{code}: {path}" if path else code)


class PresentationExportFreshnessError(PresentationExportContractError):
    """Revision, snapshot, asset, or package identity changed."""


class UnsupportedCapability(PresentationExportContractError):
    """The only exception that permits the router to try another adapter."""

    def __init__(
        self,
        code: str,
        *,
        adapter: PresentationExportAdapterId,
        path: str | None = None,
    ) -> None:
        self.adapter = adapter
        super().__init__(code, path=path)


@dataclass(frozen=True, slots=True)
class PresentationExportSlideInput:
    layout: SlideLayout
    content: Mapping[str, Any]
    source_part: str | None = None


@dataclass(frozen=True, slots=True)
class PresentationExportCapabilityAnalysis:
    candidate_chain: tuple[PresentationExportAdapterId, ...]
    capability_evidence: dict[str, Any]
    asset_digests: dict[str, str]

    @property
    def selected_adapter(self) -> PresentationExportAdapterId:
        return self.candidate_chain[0]


def _collect_asset_digests(value: Any, *, path: str = "content") -> dict[str, str]:
    digests: dict[str, str] = {}
    scene_assets: dict[str, tuple[str, str]] = {}
    scene_manifest_lists: set[int] = set()

    def collect_scene_manifests(candidate: Any, candidate_path: str) -> None:
        if isinstance(candidate, list):
            for index, child in enumerate(candidate):
                collect_scene_manifests(child, f"{candidate_path}.{index}")
            return
        if not isinstance(candidate, Mapping):
            return
        for key, child in candidate.items():
            child_path = f"{candidate_path}.{key}"
            if key in {"assetManifest", "asset_manifest"} and isinstance(child, list):
                is_scene_manifest = not child or all(
                    isinstance(item, Mapping)
                    and (
                        "storageReference" in item
                        or "storage_reference" in item
                    )
                    for item in child
                )
                if is_scene_manifest:
                    scene_manifest_lists.add(id(child))
                    for index, item in enumerate(child):
                        try:
                            entry = SceneAssetManifestEntry.model_validate(item)
                        except ValueError as error:
                            raise PresentationExportContractError(
                                "presentation_export_scene_asset_manifest_invalid",
                                path=f"{child_path}.{index}",
                            ) from error
                        normalized = entry.asset_digest.removeprefix("sha256:")
                        current = scene_assets.get(entry.asset_id)
                        sealed = (normalized, entry.storage_reference)
                        if current is not None and current != sealed:
                            raise PresentationExportFreshnessError(
                                "presentation_export_asset_digest_mismatch",
                                path=f"{child_path}.{index}",
                            )
                        scene_assets[entry.asset_id] = sealed
                    continue
            collect_scene_manifests(child, child_path)

    collect_scene_manifests(value, path)
    digests.update(
        {asset_id: digest for asset_id, (digest, _reference) in scene_assets.items()}
    )

    def visit(candidate: Any, candidate_path: str) -> None:
        if isinstance(candidate, list):
            for index, child in enumerate(candidate):
                visit(child, f"{candidate_path}.{index}")
            return
        if not isinstance(candidate, Mapping):
            return
        asset_id = candidate.get("asset_id", candidate.get("assetId"))
        digest = candidate.get(
            "sha256",
            candidate.get(
                "source_sha256",
                candidate.get(
                    "assetDigest",
                    candidate.get("asset_digest", candidate.get("digest")),
                ),
            ),
        )
        if asset_id is not None:
            if not isinstance(asset_id, str) or not asset_id.strip():
                raise PresentationExportContractError(
                    "presentation_export_asset_id_invalid",
                    path=candidate_path,
                )
            normalized_asset_id = asset_id.strip()
            if normalized_asset_id.startswith("asset:"):
                declared = scene_assets.get(normalized_asset_id)
                if declared is None:
                    raise PresentationExportContractError(
                        "presentation_export_scene_asset_undeclared",
                        path=candidate_path,
                    )
                if not isinstance(digest, str) or not digest.startswith("sha256:"):
                    raise PresentationExportFreshnessError(
                        "presentation_export_asset_digest_required",
                        path=candidate_path,
                    )
                normalized = digest.removeprefix("sha256:").lower()
                if normalized != declared[0]:
                    raise PresentationExportFreshnessError(
                        "presentation_export_asset_digest_mismatch",
                        path=candidate_path,
                    )
            else:
                try:
                    normalized_asset_id = str(uuid.UUID(normalized_asset_id))
                except ValueError as error:
                    raise PresentationExportContractError(
                        "presentation_export_asset_id_invalid",
                        path=candidate_path,
                    ) from error
                if (
                    not isinstance(digest, str)
                    or len(digest) != 64
                    or any(
                        character not in "0123456789abcdefABCDEF"
                        for character in digest
                    )
                ):
                    raise PresentationExportFreshnessError(
                        "presentation_export_asset_digest_required",
                        path=candidate_path,
                    )
                normalized = digest.lower()
            if (
                len(normalized) != 64
                or any(character not in "0123456789abcdef" for character in normalized)
            ):
                raise PresentationExportFreshnessError(
                    "presentation_export_asset_digest_required",
                    path=candidate_path,
                )
            previous = digests.get(normalized_asset_id)
            if previous is not None and previous != normalized:
                raise PresentationExportFreshnessError(
                    "presentation_export_asset_digest_mismatch",
                    path=candidate_path,
                )
            digests[normalized_asset_id] = normalized
        for key, child in candidate.items():
            if id(child) in scene_manifest_lists:
                continue
            visit(child, f"{candidate_path}.{key}")

    visit(value, path)
    return dict(sorted(digests.items()))


def analyze_presentation_export_capabilities(
    *,
    export_format: Literal["pptx", "pdf"],
    persisted_slide_payloads: Sequence[Mapping[str, Any]] = (),
) -> PresentationExportCapabilityAnalysis:
    """Return a deterministic, side-effect-free adapter chain of at most three.

    The only remaining candidate is ``general``: the native template-driven
    adapters (``native_clone``/``native_reconstructed``) required a
    ``PresentationExportSlideInput`` sequence that nothing has produced since
    Template V2 was removed on 2026-09-25.
    """

    if export_format not in {"pptx", "pdf"}:
        raise PresentationExportContractError("presentation_export_format_invalid")
    asset_digests = _collect_asset_digests(
        list(persisted_slide_payloads),
        path="persisted",
    )

    return PresentationExportCapabilityAnalysis(
        candidate_chain=("general",),
        capability_evidence={
            "analysis_version": 1,
            "format": export_format,
            "reason": "general_only",
        },
        asset_digests=dict(sorted(asset_digests.items())),
    )


__all__ = [
    "PresentationExportAdapterId",
    "PresentationExportCapabilityAnalysis",
    "PresentationExportContractError",
    "PresentationExportFreshnessError",
    "PresentationExportSlideInput",
    "UnsupportedCapability",
    "analyze_presentation_export_capabilities",
]
