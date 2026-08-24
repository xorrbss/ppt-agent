from __future__ import annotations

from io import BytesIO
from pathlib import Path

import pytest
from fontTools.ttLib import TTFont

from models.authored_vnext.semantic.contracts import (
    TextParagraphV1,
    TextPayloadV1,
    TextRunV1,
)
from models.authored_vnext.semantic.typography import (
    NativeTypographyReadbackV1,
    NativeTypographyRunReadbackV1,
)
from services.authored_vnext.semantic.canonical import canonical_digest
from services.authored_vnext.semantic.typography import (
    NativeFontByteRegistry,
    admit_native_typography,
)


_FONT_DIR = Path(r"C:\Windows\Fonts")
_MALGUN = _FONT_DIR / "malgun.ttf"
_BATANG = _FONT_DIR / "batang.ttc"
pytestmark = pytest.mark.skipif(
    not (_MALGUN.is_file() and _BATANG.is_file()),
    reason="Windows CJK font-byte contract requires Malgun Gothic and Batang",
)


def _run(text: str, family: str, face: str = "Regular") -> TextRunV1:
    return TextRunV1(
        text=text,
        language="ko-KR",
        script="cjk",
        font_family=family,
        font_face=face,
        font_size_pt=18,
        weight=400,
        color="#102A56",
    )


def _payload(*runs: TextRunV1) -> TextPayloadV1:
    return TextPayloadV1(paragraphs=[TextParagraphV1(runs=list(runs))])


def _readback(element_id: str, runs: list[TextRunV1], *, overflow: bool = False):
    return NativeTypographyReadbackV1(
        element_id=element_id,
        runs=[
            NativeTypographyRunReadbackV1(
                run_index=index,
                text_digest=canonical_digest({"text": run.text}),
                face=run.font_family,
                east_asian_typeface=run.font_family,
            )
            for index, run in enumerate(runs)
        ],
        line_rect_digests=[
            canonical_digest({"line": 0, "elementId": element_id})
        ],
        overflow=overflow,
    )


def test_actual_windows_font_bytes_admit_mixed_serif_and_mono_cjk_runs() -> None:
    registry = NativeFontByteRegistry()
    malgun = registry.register(_MALGUN.read_bytes())
    batang_bytes = _BATANG.read_bytes()
    batang = registry.register(batang_bytes, collection_index=0)
    batangche = registry.register(batang_bytes, collection_index=1)

    assert (malgun.family, malgun.face_class, malgun.license_policy) == (
        "Malgun Gothic",
        "sans",
        "microsoft-windows-font-eula",
    )
    assert (batang.family, batang.face_class) == ("Batang", "serif")
    assert (batangche.family, batangche.face_class) == ("BatangChe", "mono")
    assert all(item.embedding_eligible for item in registry.receipts)
    assert all(item.fs_type == 8 for item in registry.receipts)

    runs = [
        _run("한국 AI 2026", "Malgun Gothic"),
        _run("전략과 실행", "Batang"),
        _run("코드 123", "BatangChe"),
    ]
    receipt = admit_native_typography(
        element_id="text.cjk.matrix",
        payload=_payload(*runs),
        registry=registry,
        readback=_readback("text.cjk.matrix", runs),
    )

    assert receipt.state == "admitted"
    assert [item.script_profile for item in receipt.runs] == [
        "mixedKoreanLatinNumber",
        "cjkSerif",
        "cjkMono",
    ]
    assert [item.east_asian_typeface for item in receipt.runs] == [
        "Malgun Gothic",
        "Batang",
        "BatangChe",
    ]
    assert receipt.line_count == 1
    assert receipt.overflow is False
    assert receipt.readback_digest is not None
    assert receipt.receipt_digest is not None
    assert receipt.promotion_ready is False
    assert receipt.full_native_parity_ready is False
    assert receipt.independent_vnext_generation_ready is False


def test_unavailable_non_embeddable_and_overflow_are_explicit_raster_fallbacks() -> None:
    unavailable_registry = NativeFontByteRegistry()
    missing = _run("한국 AI 2026", "Malgun Gothic")
    unavailable = admit_native_typography(
        element_id="text.font.unavailable",
        payload=_payload(missing),
        registry=unavailable_registry,
        readback=_readback("text.font.unavailable", [missing]),
    )
    assert unavailable.state == "rasterFallback"
    assert unavailable.runs[0].fallback_reason == "fontUnavailable"

    source = TTFont(_MALGUN, lazy=False)
    source["OS/2"].fsType = 2
    restricted_bytes = BytesIO()
    source.save(restricted_bytes)
    source.close()
    restricted_registry = NativeFontByteRegistry()
    restricted_face = restricted_registry.register(restricted_bytes.getvalue())
    assert restricted_face.embedding_permitted is False
    assert restricted_face.embedding_eligible is False
    non_embeddable = admit_native_typography(
        element_id="text.font.restricted",
        payload=_payload(missing),
        registry=restricted_registry,
        readback=_readback("text.font.restricted", [missing]),
    )
    assert non_embeddable.state == "rasterFallback"
    assert non_embeddable.runs[0].fallback_reason == "fontNonEmbeddable"

    registry = NativeFontByteRegistry()
    registry.register(_MALGUN.read_bytes())
    overflow = admit_native_typography(
        element_id="text.native.overflow",
        payload=_payload(missing),
        registry=registry,
        readback=_readback("text.native.overflow", [missing], overflow=True),
    )
    assert overflow.state == "rasterFallback"
    assert overflow.runs[0].fallback_reason == "nativeOverflow"
    assert overflow.overflow is True
