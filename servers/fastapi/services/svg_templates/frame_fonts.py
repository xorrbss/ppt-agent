"""Request-scoped byte-backed fonts; never changes the global SVG registry."""
from __future__ import annotations

import base64
import hashlib
import json
from contextlib import contextmanager
from contextvars import ContextVar
from io import BytesIO

from fontTools.ttLib import TTFont
from PIL import ImageFont

from models.svg_template_frame import SvgTemplateFontAsset, SvgTemplateFontManifest
from services.authored_vnext.semantic.typography import NativeFontByteRegistry

MAX_FONT_BYTES = 64 * 1024 * 1024
_FONT_VALIDATION_CACHE: ContextVar[dict | None] = ContextVar("svg_template_font_validation_cache", default=None)


@contextmanager
def font_validation_scope():
    """Bound expensive metadata parsing to one synchronous operation/context."""
    existing = _FONT_VALIDATION_CACHE.get()
    token = _FONT_VALIDATION_CACHE.set({}) if existing is None else None
    try:
        yield
    finally:
        if token is not None:
            _FONT_VALIDATION_CACHE.reset(token)


def artifact_digest(value: object) -> str:
    if hasattr(value, "model_dump"):
        value = value.model_dump(mode="json", exclude={"digest"})
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def bytes_digest(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


def verify_font_asset(asset: SvgTemplateFontAsset) -> bytes:
    cache = _FONT_VALIDATION_CACHE.get()
    cache_key = (asset.digest, asset.family, asset.face, asset.weight, asset.collection_index, asset.family_aliases)
    if cache is not None and cache_key in cache:
        cached_asset, content = cache[cache_key]
        if cached_asset.data_base64 == asset.data_base64:
            return content
    if len(asset.data_base64) > (MAX_FONT_BYTES * 4 // 3 + 4):
        raise ValueError("svg_template_font_bytes_exceeded")
    try:
        content = base64.b64decode(asset.data_base64, validate=True)
    except ValueError as error:
        raise ValueError("svg_template_font_bytes_invalid") from error
    if bytes_digest(content) != asset.digest:
        raise ValueError("svg_template_font_digest_mismatch")
    registry = NativeFontByteRegistry()
    receipt = registry.register(content, collection_index=asset.collection_index)
    if (receipt.family.casefold() != asset.family.casefold()
            or receipt.face.casefold() != asset.face.casefold()):
        raise ValueError("svg_template_font_face_mismatch")
    if not receipt.embedding_eligible:
        raise ValueError("svg_template_font_embedding_unavailable")
    with TTFont(BytesIO(content), fontNumber=asset.collection_index) as font:
        if int(font["OS/2"].usWeightClass) != asset.weight:
            raise ValueError("svg_template_font_weight_mismatch")
        if (int(font["OS/2"].fsSelection) & (1 | 512)
                or bool(getattr(font.get("post"), "italicAngle", 0))):
            raise ValueError("svg_template_font_style_unsupported")
        if "fvar" in font:
            raise ValueError("svg_template_variable_font_unsupported")
        names = {record.toUnicode().casefold() for record in font["name"].names if record.nameID in {1, 16}}
        if not all(alias.casefold() in names for alias in asset.family_aliases):
            raise ValueError("svg_template_font_alias_mismatch")
    if cache is not None:
        cache[cache_key] = (asset, content)
    return content


def build_font_manifest(assets: tuple[SvgTemplateFontAsset, ...], *,
                        required_families: tuple[str, ...] = ()) -> SvgTemplateFontManifest:
    seen: set[tuple[str, int]] = set()
    total = 0
    for asset in assets:
        total += len(verify_font_asset(asset))
        key = (asset.family.casefold(), asset.weight)
        if key in seen:
            raise ValueError("svg_template_font_face_conflict")
        seen.add(key)
    if total > MAX_FONT_BYTES:
        raise ValueError("svg_template_font_manifest_bytes_exceeded")
    payload = dict(assets=tuple(sorted(assets, key=lambda a: (a.family, a.weight, a.digest))),
                   required_families=tuple(sorted(set(required_families))), digest="")
    manifest = SvgTemplateFontManifest(**payload)
    return manifest.model_copy(update={"digest": artifact_digest(manifest)})


def resolve_font_manifest(value: SvgTemplateFontManifest | dict) -> tuple[tuple[SvgTemplateFontAsset, bytes], ...]:
    """Caller first resolves owner scope; this verifies immutable identity/bytes."""
    manifest = SvgTemplateFontManifest.model_validate(value)
    if artifact_digest(manifest) != manifest.digest:
        raise ValueError("svg_template_font_manifest_digest_mismatch")
    entries = tuple((asset, verify_font_asset(asset)) for asset in manifest.assets)
    known = {family.casefold() for asset, _ in entries for family in (asset.family, *asset.family_aliases)}
    if any(family.casefold() not in known for family in manifest.required_families):
        raise ValueError("svg_template_font_unavailable")
    return entries


def font_asset_for(manifest: SvgTemplateFontManifest, family: str, weight: int) -> SvgTemplateFontAsset:
    for asset in manifest.assets:
        if family.casefold() in {name.casefold() for name in (asset.family, *asset.family_aliases)} and asset.weight == weight:
            return asset
    raise ValueError("svg_template_font_unavailable")


def verify_font_text(asset: SvgTemplateFontAsset, text: str) -> None:
    content = verify_font_asset(asset)
    cache = _FONT_VALIDATION_CACHE.get()
    key = ("cmap", asset.digest, asset.collection_index)
    cmap = cache.get(key) if cache is not None else None
    if cmap is None:
        with TTFont(BytesIO(content), fontNumber=asset.collection_index) as font:
            cmap = frozenset((font.getBestCmap() or {}).keys())
        if cache is not None:
            cache[key] = cmap
    if any(ord(char) not in cmap for char in text if not char.isspace()):
        raise ValueError("svg_template_font_glyph_missing")


def measure_text(asset: SvgTemplateFontAsset, text: str, size: float) -> tuple[float, float, float]:
    verify_font_text(asset, text)
    # Four-times sampling avoids integer point-size truncation at physical sizes.
    font = ImageFont.truetype(BytesIO(verify_font_asset(asset)), max(1, round(size * 4)),
                              index=asset.collection_index)
    ascent, descent = font.getmetrics()
    return float(font.getlength(text)) / 4, ascent / 4, descent / 4


def bundled_font_assets() -> tuple[SvgTemplateFontAsset, ...]:
    from services.svg_trusted_fonts import FONT_FAMILY, read_trusted_pretendard_faces
    assets = []
    for weight, _verified_digest, content in read_trusted_pretendard_faces():
        digest = bytes_digest(content)
        face = NativeFontByteRegistry().register(content).face
        assets.append(SvgTemplateFontAsset(asset_id="font-" + digest[7:23], digest=digest,
            family=FONT_FAMILY, face=face, weight=weight,
            data_base64=base64.b64encode(content).decode("ascii")))
    return tuple(assets)


def font_asset_from_bytes(content: bytes, *, collection_index: int = 0) -> SvgTemplateFontAsset:
    receipt = NativeFontByteRegistry().register(content, collection_index=collection_index)
    with TTFont(BytesIO(content), fontNumber=collection_index) as font:
        weight = int(font["OS/2"].usWeightClass)
        aliases = tuple(sorted({record.toUnicode() for record in font["name"].names if record.nameID in {1, 16}}))
    asset = SvgTemplateFontAsset(asset_id="font-" + receipt.font_byte_digest[7:23],
        digest=receipt.font_byte_digest, family=receipt.family, face=receipt.face, weight=weight,
        collection_index=collection_index, family_aliases=aliases, data_base64=base64.b64encode(content).decode("ascii"))
    verify_font_asset(asset)
    return asset


def embedded_font_assets(package) -> tuple[SvgTemplateFontAsset, ...]:
    """Only actual licensed sfnt font bytes qualify; unsupported encodings stay unavailable."""
    from services.source_content.pptx_extraction_constants import NS, R_NS
    from services.source_content.pptx_package_parts import _relationship_map
    from services.source_content.security import secure_xml
    root = secure_xml(package.read("ppt/presentation.xml"), part_name="ppt/presentation.xml")
    relationships = _relationship_map(package, "ppt/presentation.xml")
    result = {}
    total = 0
    for embedded in root.findall("p:embeddedFontLst/p:embeddedFont", NS):
        for face in embedded:
            relation = relationships.get(face.get(f"{{{R_NS}}}id", ""))
            if relation is None or not relation[1].endswith("/font"):
                continue
            size = package.getinfo(relation[0]).file_size
            total += size
            if total > MAX_FONT_BYTES:
                raise ValueError("svg_template_font_manifest_bytes_exceeded")
            try:
                asset = font_asset_from_bytes(package.read(relation[0]))
            except ValueError:
                # Names never turn an unverified/obfuscated embedded font into
                # available bytes; the corresponding required face stays missing.
                continue
            result[(asset.family.casefold(), asset.weight)] = asset
    return tuple(result.values())


MAX_INSTALLED_FONT_CANDIDATES = 2048
MAX_INSTALLED_COLLECTION_FACES = 32


#: Installed font directories on a non-Windows server (fontconfig's defaults).
_POSIX_FONT_ROOTS = ("/usr/share/fonts", "/usr/local/share/fonts")
_POSIX_USER_FONT_DIRS = (".local/share/fonts", ".fonts")


def _installed_posix_font_paths():
    """Installed font files under the system and user font directories.

    Only these fixed directories are read, never a caller-supplied path, and
    symlinks must resolve inside one of them. The walk is bounded like the
    Windows inventory.
    """
    import os
    from pathlib import Path
    roots = [Path(root) for root in _POSIX_FONT_ROOTS]
    home = os.environ.get("HOME")
    if home:
        roots.extend(Path(home) / relative for relative in _POSIX_USER_FONT_DIRS)
    roots = [root.resolve() for root in roots if root.is_dir()]
    candidates = set()
    for root in roots:
        for directory, _subdirectories, files in os.walk(root):
            for name in files:
                path = Path(directory) / name
                if path.suffix.casefold() not in {".ttf", ".otf", ".ttc"}:
                    continue
                try:
                    resolved = path.resolve(strict=True)
                except OSError:
                    continue
                if not any(resolved.is_relative_to(base) for base in roots) or not resolved.is_file():
                    continue
                candidates.add(resolved)
                if len(candidates) > MAX_INSTALLED_FONT_CANDIDATES:
                    raise ValueError("svg_template_installed_font_inventory_limit")
    return tuple(sorted(candidates, key=lambda path: str(path).casefold()))


def _installed_font_paths():
    """Read only installed font locations, never caller-supplied paths."""
    import os
    from pathlib import Path
    if os.name != "nt":
        return _installed_posix_font_paths()
    import winreg
    from itertools import islice
    roots = [Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts"]
    local_data = os.environ.get("LOCALAPPDATA")
    if local_data:
        roots.append(Path(local_data) / "Microsoft" / "Windows" / "Fonts")
    roots = [root.resolve() for root in roots if root.is_dir()]
    candidates = set()

    def collect(path):
        if path.suffix.casefold() not in {".ttf", ".otf", ".ttc"} or str(path).startswith("\\\\"):
            return
        try:
            resolved = path.resolve(strict=True)
        except OSError:
            return
        if not any(resolved.is_relative_to(root) for root in roots) or not resolved.is_file():
            return
        candidates.add(resolved)
        if len(candidates) > MAX_INSTALLED_FONT_CANDIDATES:
            raise ValueError("svg_template_installed_font_inventory_limit")

    for hive in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
        try:
            with winreg.OpenKey(hive, r"SOFTWARE\Microsoft\Windows NT\CurrentVersion\Fonts", 0, winreg.KEY_READ) as key:
                if winreg.QueryInfoKey(key)[1] > MAX_INSTALLED_FONT_CANDIDATES:
                    raise ValueError("svg_template_installed_font_inventory_limit")
                for index in range(winreg.QueryInfoKey(key)[1]):
                    _name, value, kind = winreg.EnumValue(key, index)
                    if kind not in {winreg.REG_SZ, winreg.REG_EXPAND_SZ} or not isinstance(value, str):
                        continue
                    path = Path(os.path.expandvars(value))
                    if path.is_absolute():
                        collect(path)
                    else:
                        for root in roots:
                            collect(root / path)
        except OSError:
            continue
    # Registered font files may also appear without a readable per-user registry
    # key. Bound the two installed directories and still inspect real metadata.
    for root in roots:
        paths = list(islice(root.iterdir(), MAX_INSTALLED_FONT_CANDIDATES + 1))
        if len(paths) > MAX_INSTALLED_FONT_CANDIDATES:
            raise ValueError("svg_template_installed_font_inventory_limit")
        for path in paths:
            collect(path)
    return tuple(sorted(candidates, key=lambda path: str(path).casefold()))


def verified_server_font_assets(required_faces: set[tuple[str, int]], *,
                                diagnostics: dict[tuple[str, int], str] | None = None) -> tuple[SvgTemplateFontAsset, ...]:
    """Discover exact installed metadata, then apply unchanged embedding policy.

    Installation alone is never an embedding permission. An installed Arial
    face can therefore be identified yet remain explicitly policy-ineligible.
    """
    import struct
    from fontTools.ttLib import TTLibError
    if not required_faces:
        return ()
    wanted = {(family.casefold(), weight): (family, weight) for family, weight in required_faces}
    diagnostics = diagnostics if diagnostics is not None else {}
    result = {}
    for path in _installed_font_paths():
        try:
            if not 0 < path.stat().st_size <= MAX_FONT_BYTES:
                continue
            with path.open("rb") as stream:
                header = stream.read(12)
            is_collection = header[:4] == b"ttcf"
            count = struct.unpack(">I", header[8:12])[0] if is_collection and len(header) == 12 else 1
            if count > MAX_INSTALLED_COLLECTION_FACES:
                continue
        except OSError:
            continue
        for index in range(count):
            try:
                with TTFont(str(path), fontNumber=index, lazy=True) as font:
                    names = {record.toUnicode().casefold() for record in font["name"].names if record.nameID in {1, 16}}
                    weight = int(font["OS/2"].usWeightClass)
                    matches = [original for key, original in wanted.items() if key[0] in names and key[1] == weight]
                    if not matches:
                        continue
                    italic = bool(int(font["OS/2"].fsSelection) & (1 | 512)) or bool(getattr(font.get("post"), "italicAngle", 0))
                    unsupported = ("svg_template_font_collection_unsupported" if is_collection else
                                   "svg_template_font_style_unsupported" if italic else
                                   "svg_template_variable_font_unsupported" if "fvar" in font else None)
                if unsupported:
                    for match in matches:
                        diagnostics.setdefault(match, unsupported)
                    continue
                try:
                    asset = font_asset_from_bytes(path.read_bytes())
                except ValueError as error:
                    for match in matches:
                        diagnostics.setdefault(match, str(error))
                    continue
            except (OSError, TTLibError, KeyError, ValueError, UnicodeError):
                continue
            # Stable installed-path order resolves duplicate eligible faces;
            # source review separately proves the actual Office-loaded bytes.
            result.setdefault((asset.family.casefold(), asset.weight), asset)
            for match in matches:
                diagnostics.pop(match, None)
    return tuple(result.values())
