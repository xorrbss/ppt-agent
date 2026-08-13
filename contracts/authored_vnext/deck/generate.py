"""Generate closed authored vNext deck JSON schemas from Pydantic models."""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from collections.abc import Mapping, Sequence
from importlib import import_module
from pathlib import Path
from typing import Any

REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
FASTAPI_ROOT = REPOSITORY_ROOT / "servers" / "fastapi"
OUTPUT_DIRECTORY = Path("contracts/authored_vnext/deck/v1")
MODELS: dict[str, tuple[str, str]] = {
    "outline-input.schema.json": ("OutlineInput", "outline-input:v1"),
    "outline-assignment-set.schema.json": (
        "OutlineAssignmentSet",
        "outline-assignment-set:v1",
    ),
    "slide-visual-plan.schema.json": ("SlideVisualPlan", "slide-visual-plan:v1"),
    "validated-slidespec.schema.json": (
        "ValidatedSlideSpecV2",
        "validated-slidespec:v2",
    ),
    "generation-checkpoint.schema.json": (
        "GenerationCheckpoint",
        "deck-generation-checkpoint:v1",
    ),
    "deck-quality-report.schema.json": (
        "DeckQualityReport",
        "deck-quality-report:v1",
    ),
    "montage-manifest.schema.json": ("MontageManifest", "deck-montage-manifest:v1"),
    "generate-deck-request.schema.json": (
        "GenerateDeckRequest",
        "generate-deck-request:v1",
    ),
    "regenerate-body-command.schema.json": (
        "RegenerateBodyCommand",
        "regenerate-body-command:v1",
    ),
    "resume-generation-request.schema.json": (
        "ResumeGenerationRequest",
        "resume-generation-request:v1",
    ),
    "rollback-deck-request.schema.json": (
        "RollbackDeckRequest",
        "rollback-deck-request:v1",
    ),
    "generation-result.schema.json": ("GenerationResult", "generation-result:v1"),
    "regeneration-result.schema.json": (
        "RegenerationResult",
        "regeneration-result:v1",
    ),
}


def _deck_models() -> Any:
    backend_path = str(FASTAPI_ROOT)
    if backend_path not in sys.path:
        sys.path.insert(0, backend_path)
    return import_module("models.authored_vnext.deck")


def _strict_schema(model: type[Any], schema_id: str) -> dict[str, Any]:
    config: Mapping[str, Any] = model.model_config
    if config.get("extra") != "forbid" or config.get("strict") is not True:
        raise RuntimeError(f"{model.__name__} is not a strict closed model")
    if config.get("frozen") is not True:
        raise RuntimeError(f"{model.__name__} is not immutable")
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": f"urn:presenton:authored-vnext:{schema_id}",
        "x-pydantic-strict": True,
        **model.model_json_schema(by_alias=True, mode="validation"),
    }


def render_outputs(repository_root: Path = REPOSITORY_ROOT) -> dict[Path, bytes]:
    module = _deck_models()
    outputs: dict[Path, bytes] = {}
    for filename, (model_name, schema_id) in MODELS.items():
        schema = _strict_schema(getattr(module, model_name), schema_id)
        # Compact generated files keep this contract set reviewable under the
        # repository's 500-line per-new-file constraint.
        content = json.dumps(schema, ensure_ascii=False, sort_keys=True) + "\n"
        outputs[repository_root / OUTPUT_DIRECTORY / filename] = content.encode()
    return outputs


def _atomic_write(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, path)
    finally:
        temporary_path.unlink(missing_ok=True)


def generate(*, check: bool, repository_root: Path = REPOSITORY_ROOT) -> int:
    outputs = render_outputs(repository_root)
    stale = [path for path, content in outputs.items() if not path.is_file() or path.read_bytes() != content]
    if check:
        if stale:
            print("Authored vNext deck generated contracts are stale:", file=sys.stderr)
            for path in stale:
                print(f"  {path.relative_to(repository_root)}", file=sys.stderr)
            return 1
        print("Authored vNext deck generated contracts are up to date.")
        return 0
    for path, content in outputs.items():
        _atomic_write(path, content)
    print(f"Generated {len(outputs)} authored vNext deck contract files.")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--repository-root", type=Path, default=REPOSITORY_ROOT)
    args = parser.parse_args(argv)
    return generate(check=args.check, repository_root=args.repository_root.resolve())


if __name__ == "__main__":
    raise SystemExit(main())
