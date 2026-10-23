"""Reference native-typography admission used by authored vNext release tests.

Production never admits typography itself; these tests need a deterministic
producer of admission receipts to drive the release-authority validators.
"""

from __future__ import annotations

import unicodedata

from models.authored_vnext.semantic.contracts import TextPayloadV1, TextRunV1
from models.authored_vnext.semantic.typography import (
    NativeTypographyAdmissionReceiptV1,
    NativeTypographyReadbackV1,
    NativeTypographyRunReceiptV1,
)
from services.authored_vnext.semantic.canonical import canonical_digest
from services.authored_vnext.semantic.typography import (
    NativeFontByteRegistry,
    NativeTypographyRegistryError,
    _RegisteredFace,
)


def _text_digest(text: str) -> str:
    return canonical_digest({"text": text})


def _visible_codepoints(text: str) -> set[int]:
    return {
        ord(character)
        for character in unicodedata.normalize("NFC", text)
        if not character.isspace() and unicodedata.category(character)[0] != "C"
    }


def _script_profile(run: TextRunV1, face_class: str | None) -> str:
    text = unicodedata.normalize("NFC", run.text)
    korean = any("HANGUL" in unicodedata.name(character, "") for character in text)
    latin = any("LATIN" in unicodedata.name(character, "") for character in text)
    number = any(character.isdecimal() for character in text)
    cjk = korean or any(
        marker in unicodedata.name(character, "")
        for character in text
        for marker in ("CJK", "HIRAGANA", "KATAKANA")
    )
    if korean and latin and number:
        return "mixedKoreanLatinNumber"
    if cjk:
        return {"serif": "cjkSerif", "mono": "cjkMono"}.get(
            face_class, "cjkSans"
        )
    if latin or number:
        return "latinNumber"
    return "common"


def _flatten_runs(payload: TextPayloadV1) -> list[TextRunV1]:
    return [run for paragraph in payload.paragraphs for run in paragraph.runs]


def _fallback_run(
    index: int,
    run: TextRunV1,
    *,
    face_class: str | None,
    reason: str,
    registered: _RegisteredFace | None = None,
) -> NativeTypographyRunReceiptV1:
    receipt = registered.receipt if registered else None
    return NativeTypographyRunReceiptV1(
        run_index=index,
        text_digest=_text_digest(run.text),
        script_profile=_script_profile(run, face_class),
        requested_family=run.font_family,
        requested_face=run.font_face,
        resolved_family=receipt.family if receipt else None,
        resolved_face=receipt.face if receipt else None,
        east_asian_typeface=receipt.family if receipt else None,
        font_byte_digest=receipt.font_byte_digest if receipt else None,
        state="rasterFallback",
        fallback_reason=reason,
    )


def admit_native_typography(
    *,
    element_id: str,
    payload: TextPayloadV1,
    registry: NativeFontByteRegistry,
    readback: NativeTypographyReadbackV1 | None,
) -> NativeTypographyAdmissionReceiptV1:
    """Admit a text payload only with byte, license, line and OOXML readback proof."""

    runs = _flatten_runs(payload)
    readback_digest: str | None = None
    readback_runs = {}
    line_rect_digests: list[str] = []
    overflow: bool | None = None
    readback_reason: str | None = None
    if readback is None or readback.element_id != element_id:
        readback_reason = "readbackUnavailable"
    else:
        payload_without_digest = readback.model_dump(mode="json", by_alias=True)
        supplied_digest = payload_without_digest.pop("readbackDigest", None)
        actual_digest = canonical_digest(payload_without_digest)
        if supplied_digest is not None and supplied_digest != actual_digest:
            raise NativeTypographyRegistryError(
                "authored_native_typography_readback_digest_mismatch"
            )
        readback_digest = actual_digest
        readback_runs = {item.run_index: item for item in readback.runs}
        if len(readback_runs) != len(readback.runs):
            raise NativeTypographyRegistryError(
                "authored_native_typography_readback_run_duplicate"
            )
        line_rect_digests = list(readback.line_rect_digests)
        overflow = readback.overflow
        if not line_rect_digests:
            readback_reason = "lineReceiptUnavailable"
        elif overflow:
            readback_reason = "nativeOverflow"

    results: list[NativeTypographyRunReceiptV1] = []
    for index, run in enumerate(runs):
        registered = registry.resolve(run.font_family, run.font_face)
        if registered is None:
            results.append(
                _fallback_run(
                    index,
                    run,
                    face_class=None,
                    reason="fontUnavailable",
                )
            )
            continue
        face_receipt = registered.receipt
        if not face_receipt.embedding_eligible:
            results.append(
                _fallback_run(
                    index,
                    run,
                    face_class=face_receipt.face_class,
                    reason="fontNonEmbeddable",
                    registered=registered,
                )
            )
            continue
        if not _visible_codepoints(run.text).issubset(registered.codepoints):
            results.append(
                _fallback_run(
                    index,
                    run,
                    face_class=face_receipt.face_class,
                    reason="glyphCoverageUnavailable",
                    registered=registered,
                )
            )
            continue
        observed = readback_runs.get(index)
        reason = readback_reason
        if reason is None and (
            observed is None
            or observed.text_digest != _text_digest(run.text)
            or observed.face.casefold() != face_receipt.family.casefold()
            or observed.east_asian_typeface.casefold()
            != face_receipt.family.casefold()
        ):
            reason = "readbackTypefaceMismatch"
        if reason is not None:
            results.append(
                _fallback_run(
                    index,
                    run,
                    face_class=face_receipt.face_class,
                    reason=reason,
                    registered=registered,
                )
            )
            continue
        results.append(
            NativeTypographyRunReceiptV1(
                run_index=index,
                text_digest=_text_digest(run.text),
                script_profile=_script_profile(run, face_receipt.face_class),
                requested_family=run.font_family,
                requested_face=run.font_face,
                resolved_family=face_receipt.family,
                resolved_face=face_receipt.face,
                east_asian_typeface=face_receipt.family,
                font_byte_digest=face_receipt.font_byte_digest,
                state="admitted",
            )
        )
    state = "admitted" if all(run.state == "admitted" for run in results) else "rasterFallback"
    receipt = NativeTypographyAdmissionReceiptV1(
        element_id=element_id,
        font_registry_digest=registry.digest,
        readback_digest=readback_digest,
        line_count=len(line_rect_digests),
        line_rect_digests=line_rect_digests,
        overflow=overflow,
        state=state,
        runs=results,
    )
    payload_without_digest = receipt.model_dump(mode="json", by_alias=True)
    payload_without_digest.pop("receiptDigest", None)
    return receipt.model_copy(update={"receipt_digest": canonical_digest(payload_without_digest)})
