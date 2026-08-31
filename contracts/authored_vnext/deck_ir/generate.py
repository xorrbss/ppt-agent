"""Generate versioned Deck IR JSON Schemas and the canonical digest fixture."""

from __future__ import annotations

import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[3]
FASTAPI = ROOT / "servers" / "fastapi"
if str(FASTAPI) not in sys.path:
    sys.path.insert(0, str(FASTAPI))

from services.authored_vnext.deck_ir.canonical import canonical_digest, canonical_json  # noqa: E402
from services.authored_vnext.deck_ir.contracts import CONTRACT_MODELS  # noqa: E402


def _filename(model_name: str) -> str:
    stem = model_name.removesuffix("V1").removesuffix("V2")
    output: list[str] = []
    for index, char in enumerate(stem):
        if char.isupper() and index and (not stem[index - 1].isupper() or (index + 1 < len(stem) and stem[index + 1].islower())):
            output.append("-")
        output.append(char.lower())
    return "".join(output) + ".schema.json"


def generate() -> None:
    for model in CONTRACT_MODELS:
        version = "v2" if model.__name__.endswith("V2") else "v1"
        output = Path(__file__).resolve().parent / version
        output.mkdir(parents=True, exist_ok=True)
        schema = model.model_json_schema(by_alias=True, mode="validation")
        schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
        schema["$id"] = f"urn:presenton:authored-vnext:deck-ir:{'v2:' if version == 'v2' else ''}{_filename(model.__name__)}"
        (output / _filename(model.__name__)).write_text(
            json.dumps(schema, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    sample = {"a": [1.0, "e\u0301"], "z": {"enabled": True, "value": -0.0}}
    fixture = {
        "schemaVersion": "canonical-fixture/v1",
        "canonicalizationPolicy": "canonical-json-unicode-nfc-f64-v1",
        "input": sample,
        "canonicalJson": canonical_json(sample),
        "digest": canonical_digest(sample),
    }
    (Path(__file__).resolve().parent / "v1" / "canonical-fixture.json").write_text(
        json.dumps(fixture, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    generate()
