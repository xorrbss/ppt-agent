from __future__ import annotations

import pytest

from services.source_content.readback import (
    SourceContentReadbackError,
    verify_source_verbatim_readback,
    visible_text,
)


def _manifest(*texts: tuple[str, str]) -> dict[str, object]:
    blocks = [
        {
            "sourceBlockId": block_id,
            "kind": "text",
            "semanticRole": "body",
            "order": index,
            "payload": {"text": text},
            "contentDigest": f"digest-{block_id}",
        }
        for index, (block_id, text) in enumerate(texts)
    ]
    return {
        "manifestId": "manifest-1",
        "manifestDigest": "manifest-digest",
        "units": [
            {"sourceUnitId": "unit-1", "sourceIndex": 0, "blocks": blocks}
        ],
    }


def _outline(*block_ids: str) -> dict[str, object]:
    return {
        "slides": [
            {
                "sourceUnitId": "unit-1",
                "sourceIndex": 0,
                "readingOrder": list(block_ids),
            }
        ]
    }


def test_visible_text_ignores_hidden_markup_and_decodes_entities() -> None:
    assert visible_text(
        "<h1>원문 &amp; 제목</h1><script>generated()</script><p>본문</p>"
    ) == "원문 & 제목 본문"


def test_verbatim_readback_accepts_recomposed_markup() -> None:
    result = verify_source_verbatim_readback(
        manifest=_manifest(("b1", "원문 제목"), ("b2", "중요한 본문")),
        outline=_outline("b1", "b2"),
        htmls=["<section><h1>원문 제목</h1><div><span>중요한</span> 본문</div></section>"],
    )
    assert result.passed
    assert result.checked_block_count == 2


def test_verbatim_readback_fails_closed_when_text_is_missing() -> None:
    with pytest.raises(SourceContentReadbackError) as error:
        verify_source_verbatim_readback(
            manifest=_manifest(("b1", "원문 제목"), ("b2", "중요한 본문")),
            outline=_outline("b1", "b2"),
            htmls=["<h1>원문 제목</h1><p>본문 누락</p>"],
        )
    assert error.value.code == "source_content_verbatim_readback_failed"
    assert error.value.block_ids == ("b2",)


def test_verbatim_readback_detects_source_unit_cardinality_drift() -> None:
    with pytest.raises(SourceContentReadbackError) as error:
        verify_source_verbatim_readback(
            manifest=_manifest(("b1", "원문")),
            outline={"slides": []},
            htmls=["<p>원문</p>"],
        )
    assert error.value.code == "source_content_verbatim_readback_failed"


def test_verbatim_readback_rejects_outline_order_or_omission() -> None:
    with pytest.raises(SourceContentReadbackError) as error:
        verify_source_verbatim_readback(
            manifest=_manifest(("b1", "첫 문장"), ("b2", "둘째 문장")),
            outline=_outline("b2"),
            htmls=["<p>첫 문장 둘째 문장</p>"],
        )
    assert error.value.order_violations == 1
    assert error.value.block_ids == ("b1",)
    assert error.value.slide_indices == (0,)
