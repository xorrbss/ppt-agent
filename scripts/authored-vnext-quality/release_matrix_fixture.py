"""Seed two fresh real product presentations for the release evidence matrix.

This wrapper deliberately reuses ``seed-authored-semantic-vertical-slice.py``
and its real semantic publication service. It does not call an LLM, fabricate
presentation IDs, or claim visual parity; the resulting receipt is only the
fresh product-seed input for the export/PowerPoint evidence stage.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import sys
from typing import Any


REQUIRED_LANGUAGES = {"ko", "zh", "ja"}
REQUIRED_ARCHETYPES = {
    "card",
    "process",
    "timeline",
    "comparison",
    "metric",
    "infographic",
    "table",
    "chart",
    "image",
    "architecture",
    "roadmap",
}


class MatrixFixtureFailure(RuntimeError):
    pass


def _read(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise MatrixFixtureFailure(f"manifest_invalid:{path}") from exc
    if not isinstance(value, dict):
        raise MatrixFixtureFailure("manifest_object_required")
    return value


def _validate_manifest(value: dict[str, Any]) -> None:
    if value.get("schemaVersion") != "authored-vnext-release-matrix/v1":
        raise MatrixFixtureFailure("matrix_schema_unsupported")
    if value.get("requiredSlideCount") != 8:
        raise MatrixFixtureFailure("matrix_eight_slide_seed_required")
    if set(value.get("requiredLanguages", [])) != REQUIRED_LANGUAGES:
        raise MatrixFixtureFailure("matrix_language_registry_incomplete")
    if set(value.get("requiredArchetypes", [])) != REQUIRED_ARCHETYPES:
        raise MatrixFixtureFailure("matrix_archetype_registry_incomplete")
    decks = value.get("decks")
    if not isinstance(decks, list) or len(decks) != 2:
        raise MatrixFixtureFailure("matrix_requires_two_fresh_decks")
    ids: set[str] = set()
    for deck in decks:
        if not isinstance(deck, dict):
            raise MatrixFixtureFailure("matrix_deck_object_required")
        deck_id = deck.get("matrixDeckId")
        if not isinstance(deck_id, str) or not deck_id or deck_id in ids:
            raise MatrixFixtureFailure("matrix_deck_id_invalid_or_reused")
        ids.add(deck_id)
        if set(deck.get("languages", [])) != REQUIRED_LANGUAGES or set(deck.get("archetypes", [])) != REQUIRED_ARCHETYPES:
            raise MatrixFixtureFailure(f"matrix_deck_registry_incomplete:{deck_id}")


def seed_matrix(
    manifest_path: Path,
    *,
    database_url: str,
    font_path: Path,
    cjk_sans_font_path: Path | None,
    cjk_serif_font_path: Path | None,
    owner_id: str | None,
    output_directory: Path,
) -> dict[str, Any]:
    manifest = _read(manifest_path)
    _validate_manifest(manifest)
    output_directory.mkdir(parents=True, exist_ok=True)
    seed_script = (manifest_path.parents[2] / str(manifest["seedScript"])).resolve()
    if not seed_script.is_file():
        raise MatrixFixtureFailure(f"seed_script_missing:{seed_script}")
    decks: list[dict[str, Any]] = []
    seen_presentations: set[str] = set()
    seen_attempts: set[str] = set()
    for deck in manifest["decks"]:
        receipt_path = output_directory / f"{deck['matrixDeckId']}-seed-receipt.json"
        command = [
            sys.executable,
            str(seed_script),
            "--database-url",
            database_url,
            "--font-path",
            str(font_path),
            "--slide-count",
            "8",
            "--receipt-path",
            str(receipt_path),
        ]
        if cjk_sans_font_path is not None:
            command.extend(["--cjk-sans-font-path", str(cjk_sans_font_path)])
        if cjk_serif_font_path is not None:
            command.extend(["--cjk-serif-font-path", str(cjk_serif_font_path)])
        if owner_id:
            command.extend(["--owner-id", owner_id])
        completed = subprocess.run(command, check=False, capture_output=True, text=True)
        if completed.returncode != 0:
            raise MatrixFixtureFailure(f"seed_failed:{deck['matrixDeckId']}:{completed.stderr[-1000:]}")
        receipt = _read(receipt_path)
        if receipt.get("schemaVersion") != "authored-semantic-vertical-slice-seed/v2" or receipt.get("slideCount") != 8:
            raise MatrixFixtureFailure(f"seed_receipt_contract_invalid:{deck['matrixDeckId']}")
        presentation_id = receipt.get("presentationId")
        attempt_id = receipt.get("generationAttemptId")
        if not isinstance(presentation_id, str) or presentation_id in seen_presentations:
            raise MatrixFixtureFailure("fresh_presentation_id_reused")
        if not isinstance(attempt_id, str) or attempt_id in seen_attempts:
            raise MatrixFixtureFailure("generation_attempt_reused")
        seen_presentations.add(presentation_id)
        seen_attempts.add(attempt_id)
        decks.append(
            {
                "matrixDeckId": deck["matrixDeckId"],
                "presentationId": presentation_id,
                "presentationRevision": receipt.get("presentationRevision"),
                "operationId": receipt.get("operationId"),
                "generationAttemptId": attempt_id,
                "manifestDigest": receipt.get("manifestDigest"),
                "seedReceipt": str(receipt_path),
                "status": "seeded-not-verified",
            }
        )
    return {
        "schemaVersion": "authored-vnext-release-matrix-receipt/v1",
        "status": "seeded-not-verified",
        "promotionReady": False,
        "matrixId": manifest["matrixId"],
        "createdAt": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "decks": decks,
        "nextStage": "export-and-powerpoint-evidence-required",
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", default=str(Path(__file__).with_name("release_matrix_manifest.json")))
    parser.add_argument("--database-url", required=True)
    parser.add_argument("--font-path", required=True)
    parser.add_argument("--cjk-sans-font-path")
    parser.add_argument("--cjk-serif-font-path")
    parser.add_argument("--owner-id")
    parser.add_argument("--output-directory", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args(argv)
    try:
        result = seed_matrix(
            Path(args.manifest).resolve(),
            database_url=args.database_url,
            font_path=Path(args.font_path).resolve(),
            cjk_sans_font_path=Path(args.cjk_sans_font_path).resolve() if args.cjk_sans_font_path else None,
            cjk_serif_font_path=Path(args.cjk_serif_font_path).resolve() if args.cjk_serif_font_path else None,
            owner_id=args.owner_id,
            output_directory=Path(args.output_directory).resolve(),
        )
    except MatrixFixtureFailure as exc:
        raise SystemExit(str(exc)) from exc
    output = Path(args.output).resolve()
    if output.exists():
        raise SystemExit(f"output_already_exists:{output}")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": result["status"], "output": str(output)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
