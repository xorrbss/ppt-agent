#!/usr/bin/env python3
"""Deterministic source/contract attestation for the authored renderer image."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from source_manifest import source_files

CONTRACT_ROOT = Path(__file__).resolve().parent
REPOSITORY_ROOT = CONTRACT_ROOT.parents[1]

_STATIC_IMAGE_SOURCES = (
    "Dockerfile",
    "contracts/authored-renderer/requirements.lock",
    "contracts/authored-renderer/import_closure.py",
    "contracts/authored-renderer/source_manifest.py",
    "contracts/authored-renderer/stage_sources.py",
)
def image_sources(root: Path = REPOSITORY_ROOT) -> tuple[str, ...]:
    fastapi_root = root / "servers" / "fastapi"
    modules = tuple(f"servers/fastapi/{name}" for name in source_files(fastapi_root))
    return (*_STATIC_IMAGE_SOURCES, *modules)


IMAGE_SOURCES = image_sources()

BOUNDARY_SOURCES = (
    "contracts/authored-renderer/attestation.py",
    "contracts/authored-renderer/import_closure.py",
    "contracts/authored-renderer/source_manifest.py",
    "contracts/authored-renderer/stage_sources.py",
    "contracts/authored-renderer/verify_source_stage.py",
    "contracts/authored-renderer/contract.json",
    "contracts/authored-renderer/compose.authored-renderer.json",
    "contracts/authored-renderer/chromium-seccomp.json",
    "contracts/authored-renderer/invoke.py",
    "contracts/authored-renderer/run_dynamic_gate.py",
)


def _digest_files(root: Path, paths: tuple[str, ...]) -> str:
    digest = hashlib.sha256()
    for relative in paths:
        payload = (root / relative).read_bytes()
        encoded = relative.replace("\\", "/").encode("utf-8")
        digest.update(len(encoded).to_bytes(4, "big"))
        digest.update(encoded)
        digest.update(len(payload).to_bytes(8, "big"))
        digest.update(payload)
    return "sha256:" + digest.hexdigest()


def source_tree_digest(root: Path = REPOSITORY_ROOT) -> str:
    return _digest_files(root, image_sources(root))


def contract_digest(root: Path = REPOSITORY_ROOT) -> str:
    return _digest_files(root, BOUNDARY_SOURCES)


def deployment_attestation(
    image_id: str,
    source_revision: str,
    image_sources: str,
    boundary_contract: str,
) -> str:
    value = json.dumps(
        {
            "contract_digest": boundary_contract,
            "image_id": image_id,
            "source_revision": source_revision,
            "source_tree_digest": image_sources,
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(value).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image-id", default="")
    parser.add_argument("--source-revision", default="")
    parser.add_argument("--format", choices=("json", "env"), default="json")
    args = parser.parse_args()
    image_sources = source_tree_digest()
    boundary_contract = contract_digest()
    values = {
        "AUTHORED_RENDERER_SOURCE_TREE_DIGEST": image_sources,
        "AUTHORED_RENDERER_CONTRACT_DIGEST": boundary_contract,
    }
    if args.image_id or args.source_revision:
        if not args.image_id or not args.source_revision:
            parser.error("--image-id and --source-revision are required together")
        values["AUTHORED_RENDERER_DEPLOYMENT_ATTESTATION"] = deployment_attestation(
            args.image_id, args.source_revision, image_sources, boundary_contract
        )
    if args.format == "env":
        for name, value in values.items():
            print(f"{name}={value}")
    else:
        print(json.dumps(values, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
