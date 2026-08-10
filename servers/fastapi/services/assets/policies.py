"""Compile-time registry for asset-kind security policies."""

from __future__ import annotations

from collections.abc import Iterable

from services.assets.contracts import AssetGatewayError, AssetKind, AssetPolicy


_DOCUMENT_MEDIA_TYPES = frozenset(
    {
        "application/pdf",
        "text/plain",
        "text/markdown",
        "text/csv",
        "application/csv",
        "text/tab-separated-values",
        "text/tsv",
        "application/msword",
        "application/vnd.ms-powerpoint",
        "application/vnd.ms-excel",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "application/vnd.ms-word.document.macroenabled.12",
        "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        "application/vnd.ms-powerpoint.presentation.macroenabled.12",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "application/vnd.ms-excel.sheet.macroenabled.12",
        "application/vnd.oasis.opendocument.text",
        "application/vnd.oasis.opendocument.presentation",
        "application/vnd.oasis.opendocument.spreadsheet",
        "application/rtf",
        "text/rtf",
    }
)
_DOCUMENT_EXTENSIONS = frozenset(
    {
        ".pdf",
        ".txt",
        ".md",
        ".markdown",
        ".csv",
        ".tsv",
        ".doc",
        ".docx",
        ".docm",
        ".ppt",
        ".pptx",
        ".pptm",
        ".xls",
        ".xlsx",
        ".xlsm",
        ".odt",
        ".odp",
        ".ods",
        ".rtf",
    }
)
_IMAGE_MEDIA_TYPES = frozenset(
    {
        "image/jpeg",
        "image/png",
        "image/gif",
        "image/bmp",
        "image/tiff",
        "image/webp",
        "image/svg+xml",
    }
)
_IMAGE_EXTENSIONS = frozenset(
    {".jpg", ".jpeg", ".png", ".gif", ".bmp", ".tif", ".tiff", ".webp", ".svg"}
)
_FONT_MEDIA_TYPES = frozenset(
    {
        "font/ttf",
        "font/otf",
        "font/woff",
        "font/woff2",
        "application/font-sfnt",
        "application/vnd.ms-opentype",
    }
)
_FONT_EXTENSIONS = frozenset({".ttf", ".otf", ".woff", ".woff2"})


class AssetPolicyRegistry:
    def __init__(self, policies: Iterable[AssetPolicy]) -> None:
        entries: dict[AssetKind, AssetPolicy] = {}
        for policy in policies:
            if policy.kind in entries:
                raise ValueError(f"duplicate_asset_policy:{policy.kind.value}")
            if policy.max_bytes <= 0:
                raise ValueError(f"invalid_asset_policy_size:{policy.kind.value}")
            entries[policy.kind] = policy
        missing = set(AssetKind) - set(entries)
        if missing:
            names = ",".join(sorted(kind.value for kind in missing))
            raise ValueError(f"missing_asset_policies:{names}")
        self._policies = entries

    def require(self, kind: AssetKind | str) -> AssetPolicy:
        try:
            normalized = kind if isinstance(kind, AssetKind) else AssetKind(kind)
            return self._policies[normalized]
        except (KeyError, ValueError) as error:
            raise AssetGatewayError(
                "asset_kind_unsupported", status_code=422
            ) from error

    @property
    def kinds(self) -> tuple[AssetKind, ...]:
        return tuple(self._policies)


DEFAULT_ASSET_POLICY_REGISTRY = AssetPolicyRegistry(
    (
        AssetPolicy(
            AssetKind.FILE,
            _DOCUMENT_MEDIA_TYPES,
            _DOCUMENT_EXTENSIONS,
            50 * 1024 * 1024,
            True,
            True,
            30 * 86400,
        ),
        AssetPolicy(
            AssetKind.IMAGE,
            _IMAGE_MEDIA_TYPES,
            _IMAGE_EXTENSIONS,
            20 * 1024 * 1024,
            True,
            False,
            30 * 86400,
        ),
        AssetPolicy(
            AssetKind.FONT,
            _FONT_MEDIA_TYPES,
            _FONT_EXTENSIONS,
            20 * 1024 * 1024,
            True,
            False,
            None,
        ),
        AssetPolicy(
            AssetKind.CONVERSATION_ATTACHMENT,
            _DOCUMENT_MEDIA_TYPES | _IMAGE_MEDIA_TYPES,
            _DOCUMENT_EXTENSIONS | _IMAGE_EXTENSIONS,
            50 * 1024 * 1024,
            True,
            True,
            30 * 86400,
        ),
        AssetPolicy(
            AssetKind.TEMPLATE_PACKAGE,
            frozenset({"application/json", "application/zip"}),
            frozenset({".json", ".zip"}),
            100 * 1024 * 1024,
            True,
            True,
            None,
        ),
        AssetPolicy(
            AssetKind.GENERATED,
            _IMAGE_MEDIA_TYPES | frozenset({"application/pdf"}),
            _IMAGE_EXTENSIONS | frozenset({".pdf"}),
            50 * 1024 * 1024,
            True,
            False,
            30 * 86400,
        ),
    )
)
