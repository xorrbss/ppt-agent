from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path, PurePosixPath
import re
import stat
from zipfile import BadZipFile, ZipFile, ZipInfo
import xml.etree.ElementTree as ET


class UnsafePptxPackage(ValueError):
    """Stable, content-free rejection for an unsafe or invalid OOXML package."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class PackageLimits:
    max_entries: int = 5_000
    max_member_bytes: int = 32 * 1024 * 1024
    max_total_uncompressed_bytes: int = 256 * 1024 * 1024
    max_compression_ratio: int = 200
    max_xml_bytes: int = 8 * 1024 * 1024


REQUIRED_PARTS = frozenset(
    {
        "[Content_Types].xml",
        "ppt/presentation.xml",
        "ppt/_rels/presentation.xml.rels",
    }
)
_DRIVE_PREFIX = re.compile(r"^[A-Za-z]:")
_FORBIDDEN_XML_DECLARATIONS = (b"<!DOCTYPE", b"<!ENTITY")


def _canonical_member_name(name: str) -> str:
    normalized = name.replace("\\", "/")
    if (
        not normalized
        or "\x00" in normalized
        or normalized.startswith("/")
        or normalized.startswith("//")
        or _DRIVE_PREFIX.match(normalized)
    ):
        raise UnsafePptxPackage("unsafe_zip_member_path")
    parts = PurePosixPath(normalized).parts
    if any(part in {"", ".", ".."} for part in parts):
        raise UnsafePptxPackage("unsafe_zip_member_path")
    return "/".join(parts)


def _is_symlink(info: ZipInfo) -> bool:
    unix_mode = info.external_attr >> 16
    return stat.S_IFMT(unix_mode) == stat.S_IFLNK


class PptxPackageReader:
    """Preflight and read an OOXML ZIP without extracting any member."""

    def __init__(self, path: Path, limits: PackageLimits | None = None):
        self.path = Path(path)
        self.limits = limits or PackageLimits()
        self._members: dict[str, ZipInfo] = {}

    def preflight(self) -> None:
        try:
            with ZipFile(self.path) as archive:
                infos = archive.infolist()
                if len(infos) > self.limits.max_entries:
                    raise UnsafePptxPackage("zip_entry_limit_exceeded")
                members: dict[str, ZipInfo] = {}
                folded_names: set[str] = set()
                total_size = 0
                for info in infos:
                    name = _canonical_member_name(info.filename)
                    folded = name.casefold()
                    if folded in folded_names:
                        raise UnsafePptxPackage("duplicate_zip_member")
                    folded_names.add(folded)
                    if info.flag_bits & 0x1:
                        raise UnsafePptxPackage("encrypted_zip_member")
                    if _is_symlink(info):
                        raise UnsafePptxPackage("symlink_zip_member")
                    if info.file_size > self.limits.max_member_bytes:
                        raise UnsafePptxPackage("zip_member_size_limit_exceeded")
                    total_size += info.file_size
                    if total_size > self.limits.max_total_uncompressed_bytes:
                        raise UnsafePptxPackage("zip_total_size_limit_exceeded")
                    if (
                        (info.file_size > 0 and info.compress_size == 0)
                        or (
                            info.compress_size > 0
                            and info.file_size / info.compress_size
                            > self.limits.max_compression_ratio
                        )
                    ):
                        raise UnsafePptxPackage("zip_compression_ratio_exceeded")
                    members[name] = info
                if not REQUIRED_PARTS.issubset(members):
                    raise UnsafePptxPackage("pptx_required_parts_missing")
                if any(
                    name.casefold().endswith("vbaproject.bin")
                    for name in members
                ):
                    raise UnsafePptxPackage("macro_enabled_package_forbidden")
                self._members = members
        except BadZipFile as error:
            raise UnsafePptxPackage("invalid_zip_container") from error

    @property
    def member_names(self) -> frozenset[str]:
        if not self._members:
            raise RuntimeError("package_preflight_required")
        return frozenset(self._members)

    def read_member(self, name: str, *, max_bytes: int | None = None) -> bytes:
        if not self._members:
            raise RuntimeError("package_preflight_required")
        canonical = _canonical_member_name(name)
        info = self._members.get(canonical)
        if info is None:
            raise UnsafePptxPackage("referenced_package_part_missing")
        limit = max_bytes or self.limits.max_member_bytes
        if info.file_size > limit:
            raise UnsafePptxPackage("package_part_size_limit_exceeded")
        try:
            with ZipFile(self.path) as archive, archive.open(info) as stream:
                payload = stream.read(limit + 1)
        except BadZipFile as error:
            raise UnsafePptxPackage("invalid_zip_member") from error
        if len(payload) > limit:
            raise UnsafePptxPackage("package_part_size_limit_exceeded")
        return payload

    def read_xml(self, name: str) -> ET.Element:
        payload = self.read_member(name, max_bytes=self.limits.max_xml_bytes)
        upper = payload.upper()
        if any(marker in upper for marker in _FORBIDDEN_XML_DECLARATIONS):
            raise UnsafePptxPackage("unsafe_xml_declaration")
        try:
            return ET.fromstring(payload)
        except ET.ParseError as error:
            raise UnsafePptxPackage("invalid_ooxml_xml") from error
