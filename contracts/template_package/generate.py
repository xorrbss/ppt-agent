"""Generate the TemplatePackage manifest JSON schema from its Pydantic root."""

from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / "servers" / "fastapi"
OUTPUT = ROOT / "contracts" / "template_package" / "v1" / "schema.json"


def content() -> bytes:
    sys.path.insert(0, str(BACKEND))
    from templates.packages.models import TemplatePackageManifest

    schema = TemplatePackageManifest.model_json_schema(by_alias=True)
    return (json.dumps(schema, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode()


def _normalize_eol(data: bytes) -> bytes:
    """Windows checkouts materialise CRLF (core.autocrlf=true); the generator
    always emits LF. Normalize both sides before comparing so a line-ending-only
    difference is not reported as drift, while real content differences still are.
    """
    return data.replace(b"\r\n", b"\n")


def main() -> int:
    expected = content()
    if "--check" in sys.argv:
        if not OUTPUT.is_file() or _normalize_eol(OUTPUT.read_bytes()) != _normalize_eol(expected):
            print("TemplatePackage schema is stale", file=sys.stderr)
            return 1
        print("TemplatePackage schema is up to date.")
        return 0
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(dir=OUTPUT.parent, prefix=".schema.")
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(expected)
        os.replace(temporary_name, OUTPUT)
    finally:
        Path(temporary_name).unlink(missing_ok=True)
    print("Generated TemplatePackage manifest schema.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
