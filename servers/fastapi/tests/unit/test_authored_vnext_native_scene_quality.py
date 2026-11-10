from __future__ import annotations

import base64

import pytest

from models.authored_vnext.deck.scene import (
    SceneCapability,
)


_PNG_BYTES = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNgYAAAAAMAASsJTYQAAAAASUVORK5CYII="
)


def _capabilities() -> list[SceneCapability]:
    return [
        SceneCapability(
            kind=kind,
            preview="supported" if kind not in {"connector", "group"} else "partial",
            workbench_edit=(
                "supported"
                if kind in {"text", "image", "shape"}
                else "partial"
                if kind in {"connector", "table", "chart"}
                else "readOnly"
            ),
            native_pptx_export=("supported" if kind in {"text", "image", "shape", "table", "chart"} else "unsupported"),
            reason=("native connector/group export is not implemented" if kind in {"connector", "group"} else None),
        )
        for kind in ("text", "image", "shape", "connector", "table", "chart", "group")
    ]


@pytest.mark.parametrize("kind", ["connector", "group"])
def test_connector_and_group_native_capabilities_remain_explicitly_unsupported(
    kind: str,
) -> None:
    with pytest.raises(ValueError, match="must remain explicitly unsupported"):
        SceneCapability(
            kind=kind,
            preview="partial",
            workbench_edit="readOnly",
            native_pptx_export="supported",
            reason="must not be promoted without an implemented native renderer",
        )
