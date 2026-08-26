from __future__ import annotations

import asyncio

import pytest

from services.source_content.coverage import SourceContentCoverageError
from services.source_content.publication import publish_verified_source_content


def test_source_block_omission_never_reaches_atomic_publication() -> None:
    published = False

    async def atomic_publish(_receipt: object) -> None:
        nonlocal published
        published = True

    manifest = {
        "manifestDigest": f"sha256:{'1' * 64}",
        "objectInventory": {
            "discoveredObjectCount": 1,
            "classifiedObjectCount": 1,
            "unclassifiedObjectIds": [],
            "unsupportedContentObjectIds": [],
        },
        "units": [
            {
                "blocks": [
                    {
                        "sourceBlockId": "source-slide:1:text:1",
                        "kind": "text",
                        "order": 0,
                        "contentDigest": f"sha256:{'2' * 64}",
                        "payload": {"text": "원본", "runs": ["원본"]},
                    }
                ]
            }
        ],
    }
    plan = {
        "planDigest": f"sha256:{'3' * 64}",
        "slides": [
            {
                "outputIndex": 0,
                "placements": [
                    {
                        "sourceBlockId": "source-slide:1:text:1",
                        "sourceRange": {
                            "kind": "atomic",
                            "start": 0,
                            "endExclusive": 1,
                        },
                    }
                ],
            }
        ],
    }
    envelope_with_omitted_binding = {
        "scene": {"sceneDigest": f"sha256:{'4' * 64}", "slides": []},
        "sourceBindings": [],
    }

    with pytest.raises(SourceContentCoverageError) as captured:
        asyncio.run(
            publish_verified_source_content(
                presentation_id="presentation-1",
                presentation_revision=1,
                manifest=manifest,
                placement_plan=plan,
                materialized_scene=envelope_with_omitted_binding,
                candidate_pptx=b"not-inspected-because-scene-gate-fails-first",
                render_evidence={},
                atomic_publish=atomic_publish,
            )
        )

    assert captured.value.code == "source_content_coverage_failed"
    assert "bindings" in captured.value.detail
    assert published is False
