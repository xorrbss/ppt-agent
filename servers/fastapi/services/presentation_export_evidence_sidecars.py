"""Immutable quality/coverage sidecar publication for Hybrid exports."""

from __future__ import annotations

from hashlib import sha256
import hmac
import json
import os
from pathlib import Path
from typing import Any, Callable
import uuid

from services.presentation_export_capabilities import PresentationExportContractError


def require_matching_hybrid_sidecars(
    artifact: Path,
    *,
    quality: dict[str, Any],
    coverage: dict[str, Any],
    canonical_digest: Callable[[dict[str, Any]], str],
) -> None:
    pointer = Path(f"{artifact}.evidence.json")
    if pointer.exists():
        try:
            manifest = json.loads(pointer.read_text(encoding="utf-8"))
            members: dict[str, dict[str, Any]] = {}
            for kind in ("quality", "coverage"):
                descriptor = manifest[kind]
                filename = descriptor["file"]
                if Path(filename).name != filename:
                    raise ValueError("unsafe evidence member")
                content = (artifact.parent / filename).read_bytes()
                if not hmac.compare_digest(sha256(content).hexdigest(), descriptor["sha256"]):
                    raise ValueError("evidence member digest mismatch")
                members[kind] = json.loads(content)
            pair_digest = sha256(
                f"{manifest['quality']['sha256']}:{manifest['coverage']['sha256']}".encode(
                    "ascii"
                )
            ).hexdigest()
            if (
                manifest.get("schemaVersion") != "presenton.hybrid-evidence-pointer/v1"
                or not hmac.compare_digest(pair_digest, manifest["pairDigest"])
            ):
                raise ValueError("evidence pointer digest mismatch")
        except (KeyError, OSError, TypeError, ValueError, json.JSONDecodeError) as error:
            raise PresentationExportContractError(
                "presentation_export_evidence_sidecar_invalid"
            ) from error
        if all(
            hmac.compare_digest(canonical_digest(members[kind]), canonical_digest(expected))
            for kind, expected in (("quality", quality), ("coverage", coverage))
        ):
            return
    for suffix, expected in ((".quality.json", quality), (".coverage.json", coverage)):
        sidecar = Path(f"{artifact}{suffix}")
        try:
            persisted = json.loads(sidecar.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise PresentationExportContractError(
                "presentation_export_evidence_sidecar_invalid"
            ) from error
        if not hmac.compare_digest(canonical_digest(persisted), canonical_digest(expected)):
            raise PresentationExportContractError(
                "presentation_export_evidence_sidecar_mismatch"
            )


def publish_finalized_hybrid_sidecars(
    artifact: Path,
    *,
    quality: dict[str, Any],
    coverage: dict[str, Any],
) -> dict[str, Any]:
    """Publish an immutable pair, then atomically move its authority pointer."""

    def canonical_bytes(payload: dict[str, Any]) -> bytes:
        try:
            return json.dumps(
                payload,
                allow_nan=False,
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            ).encode("utf-8")
        except (TypeError, ValueError) as error:
            raise PresentationExportContractError(
                "presentation_export_evidence_sidecar_finalization_failed"
            ) from error

    quality_bytes = canonical_bytes(quality)
    coverage_bytes = canonical_bytes(coverage)
    quality_digest = sha256(quality_bytes).hexdigest()
    coverage_digest = sha256(coverage_bytes).hexdigest()
    pair_digest = sha256(f"{quality_digest}:{coverage_digest}".encode("ascii")).hexdigest()
    quality_target = Path(f"{artifact}.evidence-{pair_digest}.quality.json")
    coverage_target = Path(f"{artifact}.evidence-{pair_digest}.coverage.json")
    pointer_target = Path(f"{artifact}.evidence.json")
    manifest = {
        "schemaVersion": "presenton.hybrid-evidence-pointer/v1",
        "pairDigest": pair_digest,
        "quality": {"file": quality_target.name, "sha256": quality_digest},
        "coverage": {"file": coverage_target.name, "sha256": coverage_digest},
    }
    pointer_bytes = canonical_bytes(manifest)
    staged: list[tuple[Path, Path, bytes]] = []
    token = uuid.uuid4().hex
    try:
        for target, payload in (
            (quality_target, quality_bytes),
            (coverage_target, coverage_bytes),
        ):
            if target.exists():
                if not hmac.compare_digest(target.read_bytes(), payload):
                    raise PresentationExportContractError(
                        "presentation_export_evidence_sidecar_cas_conflict"
                    )
                continue
            temporary = Path(f"{target}.{token}.tmp")
            temporary.write_bytes(payload)
            staged.append((temporary, target, payload))
        for temporary, target, _ in staged:
            os.replace(temporary, target)
        pointer_temporary = Path(f"{pointer_target}.{token}.tmp")
        pointer_temporary.write_bytes(pointer_bytes)
        os.replace(pointer_temporary, pointer_target)
    except (OSError, PresentationExportContractError) as error:
        for temporary, _, _ in staged:
            temporary.unlink(missing_ok=True)
        Path(f"{pointer_target}.{token}.tmp").unlink(missing_ok=True)
        if isinstance(error, PresentationExportContractError):
            raise
        raise PresentationExportContractError(
            "presentation_export_evidence_sidecar_finalization_failed"
        ) from error
    return manifest
