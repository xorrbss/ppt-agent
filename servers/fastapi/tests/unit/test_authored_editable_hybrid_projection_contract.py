"""The scene projection is read by the exporter, so its keys are a contract.

`servers/nextjs/lib/authored-hybrid/presentation-mode.ts` refuses to grant the
editable-hybrid profile when the projection claims promotion or deployment. It
reads both keys together, and an omitted `promotionReady` used to read there as
a claim: every published deck was demoted to a raster-only Hybrid export even
though its own scene marked all elements `nativeEditable`. Writing both claims
explicitly keeps producer and consumer literal about the same fields.
"""

from __future__ import annotations

import ast
from pathlib import Path


PUBLICATION_SOURCE = (
    Path(__file__).parents[2]
    / "services"
    / "authored_presentation_publication.py"
)
PROJECTION_SCHEMA = "authored-editable-hybrid-scene-projection/v1"


def _scene_projection_literal() -> ast.Dict:
    tree = ast.parse(PUBLICATION_SOURCE.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if not isinstance(node, ast.Dict):
            continue
        for key, value in zip(node.keys, node.values, strict=True):
            if (
                isinstance(key, ast.Constant)
                and key.value == "schemaVersion"
                and isinstance(value, ast.Constant)
                and value.value == PROJECTION_SCHEMA
            ):
                return node
    raise AssertionError(f"no {PROJECTION_SCHEMA} literal in {PUBLICATION_SOURCE}")


