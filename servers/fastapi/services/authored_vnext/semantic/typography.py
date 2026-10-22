"""Actual-font-byte registry and CJK native text admission.

Family labels never establish availability.  A face is usable only after its
TTF/TTC bytes have been parsed, glyph coverage checked, its OS/2 embedding bits
accepted for editable documents, and its embedded license metadata matched by
the bounded reviewed policy below.
"""

from __future__ import annotations

from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
import unicodedata

from fontTools.ttLib import TTFont, TTLibError

from models.authored_vnext.semantic.typography import (
    NativeFontFaceReceiptV1,
    NativeTypographyAdmissionReceiptV1,
)
from services.authored_vnext.semantic.canonical import canonical_digest
from utils.hashing import sha256_prefixed


_MICROSOFT_DOCUMENT_FONT_FAMILIES = frozenset(
    {
        "batang",
        "batangche",
        "consolas",
        "dotum",
        "dotumche",
        "gulim",
        "gulimche",
        "gungsuh",
        "gungsuhche",
        "malgun gothic",
        "segoe ui symbol",
    }
)
_RESTRICTED_LICENSE_BIT = 0x0002
_EDITABLE_BIT = 0x0008
_NO_SUBSETTING_BIT = 0x0100
_BITMAP_ONLY_BIT = 0x0200


class NativeTypographyRegistryError(ValueError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _font_name(font: TTFont, *name_ids: int) -> str:
    table = font.get("name")
    if table is None:
        return ""
    for name_id in name_ids:
        values: list[str] = []
        for record in table.names:
            if record.nameID != name_id:
                continue
            try:
                value = unicodedata.normalize("NFC", record.toUnicode()).strip()
            except (UnicodeDecodeError, AttributeError):
                continue
            if value and value not in values:
                values.append(value)
        if values:
            return values[0]
    return ""


def _face_class(font: TTFont) -> str:
    post = font.get("post")
    if post is not None and bool(getattr(post, "isFixedPitch", 0)):
        return "mono"
    os2 = font.get("OS/2")
    panose = getattr(os2, "panose", None)
    serif_style = int(getattr(panose, "bSerifStyle", 0) or 0)
    return "serif" if 2 <= serif_style <= 10 else "sans"


def _license_policy(font: TTFont, family: str) -> tuple[str, str | None]:
    license_text = _font_name(font, 13)
    if not license_text:
        return "missing", None
    normalized = " ".join(license_text.casefold().split())
    evidence = canonical_digest(
        {"licenseText": license_text, "licenseUrl": _font_name(font, 14) or None}
    )
    if "sil open font license" in normalized or "open font license" in normalized:
        return "ofl-1.1", evidence
    if (
        family.casefold() in _MICROSOFT_DOCUMENT_FONT_FAMILIES
        and "microsoft supplied font" in normalized
        and "embedding restrictions included in this font" in normalized
    ):
        return "microsoft-windows-font-eula", evidence
    return "unreviewed", None


@dataclass(frozen=True)
class _RegisteredFace:
    receipt: NativeFontFaceReceiptV1
    codepoints: frozenset[int]
    content: bytes


class NativeFontByteRegistry:
    """In-memory registry whose public identity contains byte-backed receipts."""

    def __init__(self) -> None:
        self._faces: dict[tuple[str, str], _RegisteredFace] = {}

    def register(self, content: bytes, *, collection_index: int = 0) -> NativeFontFaceReceiptV1:
        if not content or len(content) > 64 * 1024 * 1024:
            raise NativeTypographyRegistryError("authored_native_font_bytes_invalid")
        if collection_index < 0 or collection_index > 255:
            raise NativeTypographyRegistryError("authored_native_font_collection_index_invalid")
        try:
            font = TTFont(BytesIO(content), fontNumber=collection_index, lazy=False)
        except (TTLibError, IndexError, KeyError, ValueError) as error:
            raise NativeTypographyRegistryError("authored_native_font_bytes_invalid") from error
        try:
            family = _font_name(font, 16, 1)
            face = _font_name(font, 17, 2)
            if not family or not face or font.get("cmap") is None or font.get("OS/2") is None:
                raise NativeTypographyRegistryError("authored_native_font_metadata_invalid")
            codepoints = frozenset(
                codepoint
                for table in font["cmap"].tables
                if table.isUnicode()
                for codepoint in table.cmap
            )
            if not codepoints:
                raise NativeTypographyRegistryError("authored_native_font_cmap_unavailable")
            fs_type = int(font["OS/2"].fsType)
            embedding_permitted = not bool(
                fs_type & (_RESTRICTED_LICENSE_BIT | _BITMAP_ONLY_BIT)
            )
            editable_embedding_permitted = embedding_permitted and (
                fs_type == 0 or bool(fs_type & _EDITABLE_BIT)
            )
            license_policy, license_evidence_digest = _license_policy(font, family)
            reviewed = license_policy in {
                "ofl-1.1",
                "microsoft-windows-font-eula",
            }
            receipt = NativeFontFaceReceiptV1(
                family=family,
                face=face,
                collection_index=collection_index,
                font_byte_digest=sha256_prefixed(content),
                size_bytes=len(content),
                face_class=_face_class(font),
                license_policy=license_policy,
                license_evidence_digest=license_evidence_digest,
                fs_type=fs_type,
                embedding_permitted=embedding_permitted,
                editable_embedding_permitted=editable_embedding_permitted,
                subsetting_permitted=not bool(fs_type & _NO_SUBSETTING_BIT),
                embedding_eligible=reviewed and editable_embedding_permitted,
            )
        finally:
            font.close()
        key = (family.casefold(), face.casefold())
        existing = self._faces.get(key)
        if existing is not None and existing.receipt.font_byte_digest != receipt.font_byte_digest:
            raise NativeTypographyRegistryError("authored_native_font_face_conflict")
        self._faces[key] = _RegisteredFace(receipt=receipt, codepoints=codepoints, content=bytes(content))
        return receipt

    def resolve(self, family: str, face: str) -> _RegisteredFace | None:
        return self._faces.get((family.casefold(), face.casefold()))

    def font_bytes(self, family: str, face: str) -> bytes:
        registered = self.resolve(family, face)
        if registered is None or not registered.receipt.embedding_eligible:
            raise NativeTypographyRegistryError("authored_native_font_material_unavailable")
        return registered.content

    @property
    def digest(self) -> str:
        return canonical_digest(
            {
                "schemaVersion": "native-font-byte-registry/v1",
                "faces": [
                    registered.receipt.model_dump(mode="json", by_alias=True)
                    for _key, registered in sorted(self._faces.items())
                ],
            }
        )

    @property
    def receipts(self) -> tuple[NativeFontFaceReceiptV1, ...]:
        return tuple(
            registered.receipt
            for _key, registered in sorted(self._faces.items())
        )


_WINDOWS_CJK_FONT_DIRECTORY = Path(r"C:\Windows\Fonts")


def load_bounded_windows_symbol_registry() -> NativeFontByteRegistry:
    """Resolve one fixed OS face through the existing editable-font policy."""
    try:
        content = (_WINDOWS_CJK_FONT_DIRECTORY / "seguisym.ttf").read_bytes()
    except OSError as error:
        raise NativeTypographyRegistryError("authored_native_symbol_font_bytes_unavailable") from error
    registry = NativeFontByteRegistry()
    receipt = registry.register(content)
    with TTFont(BytesIO(content)) as font:
        regular_weight = int(font["OS/2"].usWeightClass) == 400
    if ((receipt.family.casefold(), receipt.face.casefold()) != ("segoe ui symbol", "normal")
            or not regular_weight or not receipt.embedding_eligible):
        raise NativeTypographyRegistryError("authored_native_symbol_font_identity_or_license_invalid")
    return registry


def load_bounded_windows_cjk_registry(
    font_directory: Path = _WINDOWS_CJK_FONT_DIRECTORY,
) -> NativeFontByteRegistry:
    """Rebuild the bounded registry from the exact installed font bytes."""

    try:
        malgun = (font_directory / "malgun.ttf").read_bytes()
        batang = (font_directory / "batang.ttc").read_bytes()
        consolas = (font_directory / "consola.ttf").read_bytes()
    except OSError as error:
        raise NativeTypographyRegistryError(
            "authored_native_cjk_font_bytes_unavailable"
        ) from error
    registry = NativeFontByteRegistry()
    registry.register(malgun)
    registry.register(batang, collection_index=0)
    registry.register(batang, collection_index=1)
    registry.register(consolas)
    expected = {
        ("malgun gothic", "regular"),
        ("batang", "regular"),
        ("batangche", "regular"),
        ("consolas", "regular"),
    }
    actual = {
        (item.family.casefold(), item.face.casefold()) for item in registry.receipts
    }
    if actual != expected:
        raise NativeTypographyRegistryError(
            "authored_native_cjk_font_face_inventory_mismatch"
        )
    return registry


def native_typography_com_reopen_digest(
    *,
    font_registry_digest: str,
    admission_receipts: list[NativeTypographyAdmissionReceiptV1],
) -> str:
    """Digest the exact semantic payload the COM reopen artifact must contain."""

    return canonical_digest(
        {
            "schemaVersion": "native-typography-com-reopen-readback/v1",
            "fontRegistryDigest": font_registry_digest,
            "admissionReceipts": admission_receipts,
        }
    )


__all__ = [
    "NativeFontByteRegistry",
    "NativeTypographyRegistryError",
    "load_bounded_windows_cjk_registry",
    "native_typography_com_reopen_digest",
]
