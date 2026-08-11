"""Compile-time reference providers with fail-closed normalization."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Iterable, Mapping
from dataclasses import dataclass
from datetime import timedelta, timezone
from hashlib import sha256
from html import escape
from html.parser import HTMLParser
import json
import re
from typing import Any
import uuid

import aiohttp
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from models.sql.template_v2 import TemplateV2
from models.sql.template_v2_revision import TemplateV2Revision
from services.assets import AssetGateway, AssetKind
from services.documents_loader import DocumentsLoader
from services.references.contracts import (
    NormalizedReference,
    ReferenceProviderError,
    ReferenceSourceProvider,
)
from services.workspace_asset_service import get_workspace_asset
from utils.datetime_utils import get_current_utc_datetime
from utils.owner_context import get_current_owner_id


COMMUNITY_ORIGIN = "https://api.presenton.ai"
COMMUNITY_PATH = "/api/v3/community/presentations"
MAX_COMMUNITY_REFERENCES = 3
MAX_COMMUNITY_SLIDES = 6
MAX_COMMUNITY_CHARACTERS = 90_000
MAX_COMMUNITY_RESPONSE_BYTES = 1_000_000
MAX_UPLOADED_REFERENCES = 16
MAX_UPLOADED_REFERENCE_CHARACTERS = 300_000

_DROP_TAGS = frozenset({"script", "style", "iframe", "object", "embed", "link", "meta"})
_SAFE_TAGS = frozenset(
    {
        "article",
        "aside",
        "b",
        "blockquote",
        "br",
        "caption",
        "code",
        "div",
        "em",
        "figcaption",
        "figure",
        "footer",
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
        "header",
        "hr",
        "i",
        "li",
        "main",
        "ol",
        "p",
        "pre",
        "section",
        "small",
        "span",
        "strong",
        "sub",
        "sup",
        "table",
        "tbody",
        "td",
        "tfoot",
        "th",
        "thead",
        "tr",
        "u",
        "ul",
    }
)
_SAFE_STYLE_PROPERTIES = frozenset(
    {
        "align-items",
        "background",
        "background-color",
        "border",
        "border-color",
        "border-radius",
        "border-style",
        "border-width",
        "color",
        "display",
        "flex",
        "flex-direction",
        "font-family",
        "font-size",
        "font-style",
        "font-weight",
        "gap",
        "grid-template-columns",
        "height",
        "justify-content",
        "letter-spacing",
        "line-height",
        "margin",
        "margin-bottom",
        "margin-left",
        "margin-right",
        "margin-top",
        "max-height",
        "max-width",
        "min-height",
        "min-width",
        "opacity",
        "padding",
        "padding-bottom",
        "padding-left",
        "padding-right",
        "padding-top",
        "text-align",
        "text-decoration",
        "text-transform",
        "width",
    }
)
_UNSAFE_CSS = re.compile(r"(?i)(?:url\s*\(|expression\s*\(|@import|javascript:|data:)")
_CONTROL_CHARACTERS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def _canonical_json(value: Any) -> str:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        )
    except (TypeError, ValueError) as error:
        raise ReferenceProviderError("reference_payload_not_json") from error


def _digest(value: Any) -> str:
    return sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _strict_keys(request: Mapping[str, Any], expected: frozenset[str]) -> None:
    unexpected = set(request) - expected
    missing = expected - set(request)
    if unexpected or missing:
        raise ReferenceProviderError("reference_request_shape_invalid")


def _safe_style(value: str) -> str:
    if _UNSAFE_CSS.search(value):
        return ""
    declarations: list[str] = []
    for declaration in value.split(";"):
        name, separator, content = declaration.partition(":")
        name = name.strip().lower()
        content = _CONTROL_CHARACTERS.sub("", content).strip()
        if (
            separator
            and name in _SAFE_STYLE_PROPERTIES
            and content
            and len(content) <= 256
            and not _UNSAFE_CSS.search(content)
        ):
            declarations.append(f"{name}:{content}")
    return ";".join(declarations)


class _CommunityHtmlSanitizer(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.output: list[str] = []
        self._drop_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        normalized = tag.lower()
        if normalized in _DROP_TAGS:
            self._drop_depth += 1
            return
        if self._drop_depth or normalized not in _SAFE_TAGS:
            return
        style = ""
        for name, value in attrs:
            if name.lower() == "style" and value:
                style = _safe_style(value)
        suffix = f' style="{escape(style, quote=True)}"' if style else ""
        self.output.append(f"<{normalized}{suffix}>")

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)
        if tag.lower() in _SAFE_TAGS and not self._drop_depth:
            self.output.append(f"</{tag.lower()}>")

    def handle_endtag(self, tag: str) -> None:
        normalized = tag.lower()
        if normalized in _DROP_TAGS:
            self._drop_depth = max(0, self._drop_depth - 1)
            return
        if not self._drop_depth and normalized in _SAFE_TAGS:
            self.output.append(f"</{normalized}>")

    def handle_data(self, data: str) -> None:
        if not self._drop_depth:
            self.output.append(escape(_CONTROL_CHARACTERS.sub("", data)))


def sanitize_community_html(value: str) -> str:
    parser = _CommunityHtmlSanitizer()
    parser.feed(value)
    parser.close()
    return "".join(parser.output).strip()


def _community_url(community_id: int) -> str:
    if community_id <= 0:
        raise ReferenceProviderError("community_reference_id_invalid")
    return f"{COMMUNITY_ORIGIN}{COMMUNITY_PATH}/{community_id}"


async def _fetch_community_reference(community_id: int) -> dict[str, Any]:
    timeout = aiohttp.ClientTimeout(total=20, connect=5, sock_read=10)
    async with aiohttp.ClientSession(
        timeout=timeout,
        headers={"User-Agent": "Presenton-Open-Source/1.0"},
        trust_env=False,
    ) as session:
        async with session.get(
            _community_url(community_id),
            allow_redirects=False,
        ) as response:
            if 300 <= response.status < 400:
                raise ReferenceProviderError("community_redirect_rejected", status_code=502)
            if response.status == 404:
                raise ReferenceProviderError("community_reference_not_found", status_code=404)
            if response.status >= 400:
                raise ReferenceProviderError(
                    "community_reference_unavailable", status_code=502, retryable=True
                )
            declared = response.content_length
            if declared is not None and declared > MAX_COMMUNITY_RESPONSE_BYTES:
                raise ReferenceProviderError("community_reference_too_large", status_code=502)
            chunks: list[bytes] = []
            total = 0
            async for chunk in response.content.iter_chunked(64 * 1024):
                total += len(chunk)
                if total > MAX_COMMUNITY_RESPONSE_BYTES:
                    raise ReferenceProviderError("community_reference_too_large", status_code=502)
                chunks.append(chunk)
    try:
        payload = json.loads(b"".join(chunks))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ReferenceProviderError("community_reference_invalid", status_code=502) from error
    if not isinstance(payload, dict):
        raise ReferenceProviderError("community_reference_invalid", status_code=502)
    return payload


@dataclass(frozen=True, slots=True)
class CommunityReferenceProvider:
    provider_id: str = "community"
    fetcher: Callable[[int], Awaitable[dict[str, Any]]] = _fetch_community_reference

    async def normalize(
        self,
        session: AsyncSession,
        *,
        request: dict[str, Any],
        asset_gateway: AssetGateway,
        conversation_id: uuid.UUID | None,
    ) -> NormalizedReference:
        del session, asset_gateway, conversation_id
        _strict_keys(request, frozenset({"community_ids"}))
        values = request["community_ids"]
        if not isinstance(values, list) or not values:
            raise ReferenceProviderError("community_reference_ids_invalid")
        ids: list[int] = []
        for value in values:
            if isinstance(value, bool):
                raise ReferenceProviderError("community_reference_id_invalid")
            try:
                normalized = int(value)
            except (TypeError, ValueError) as error:
                raise ReferenceProviderError("community_reference_id_invalid") from error
            _community_url(normalized)
            if normalized in ids:
                raise ReferenceProviderError("community_reference_duplicate")
            ids.append(normalized)
        if len(ids) > MAX_COMMUNITY_REFERENCES:
            raise ReferenceProviderError("community_reference_limit_exceeded")

        references: list[dict[str, Any]] = []
        remaining_slides = MAX_COMMUNITY_SLIDES
        remaining_characters = MAX_COMMUNITY_CHARACTERS
        for community_id in ids:
            payload = await self.fetcher(community_id)
            raw_slides = payload.get("slides")
            if not isinstance(raw_slides, list):
                raise ReferenceProviderError("community_reference_invalid", status_code=502)
            slides: list[str] = []
            for raw in raw_slides:
                if not isinstance(raw, str) or remaining_slides <= 0:
                    continue
                sanitized = sanitize_community_html(raw)
                if not sanitized:
                    continue
                if len(sanitized) > remaining_characters:
                    sanitized = sanitized[:remaining_characters]
                if not sanitized:
                    break
                slides.append(sanitized)
                remaining_slides -= 1
                remaining_characters -= len(sanitized)
                if remaining_slides <= 0 or remaining_characters <= 0:
                    break
            if not slides:
                raise ReferenceProviderError("community_reference_empty")
            title = _CONTROL_CHARACTERS.sub("", str(payload.get("title") or "Untitled"))
            raw_fonts = payload.get("fonts")
            font_families = sorted(
                str(name)[:128]
                for name in raw_fonts
                if isinstance(raw_fonts, dict) and isinstance(name, str)
            )
            reference = {
                "communityId": community_id,
                "title": title.strip()[:256],
                "slides": slides,
                "fontFamilies": font_families,
            }
            reference["contentDigest"] = _digest(reference)
            references.append(reference)

        normalized_payload = {
            "trust": "untrusted-style-only",
            "references": references,
        }
        return NormalizedReference(
            provider_id=self.provider_id,
            source_revision=_digest(normalized_payload),
            normalized_payload=normalized_payload,
            expires_at=get_current_utc_datetime() + timedelta(hours=24),
        )


@dataclass(frozen=True, slots=True)
class UploadedDocumentProvider:
    provider_id: str = "uploaded_document"

    async def normalize(
        self,
        session: AsyncSession,
        *,
        request: dict[str, Any],
        asset_gateway: AssetGateway,
        conversation_id: uuid.UUID | None,
    ) -> NormalizedReference:
        _strict_keys(request, frozenset({"asset_ids"}))
        values = request["asset_ids"]
        if not isinstance(values, list) or not values:
            raise ReferenceProviderError("uploaded_reference_assets_invalid")
        if len(values) > MAX_UPLOADED_REFERENCES:
            raise ReferenceProviderError("uploaded_reference_limit_exceeded")
        try:
            asset_ids = tuple(uuid.UUID(str(value)) for value in values)
        except (TypeError, ValueError, AttributeError) as error:
            raise ReferenceProviderError("uploaded_reference_asset_id_invalid") from error
        bindings = []
        extracted_documents: list[dict[str, str]] = []
        expiry_candidates = []
        remaining_characters = MAX_UPLOADED_REFERENCE_CHARACTERS
        for asset_id in asset_ids:
            binding, private_path = await asset_gateway.resolve_for_processing(
                session,
                asset_id=asset_id,
                kind=AssetKind.CONVERSATION_ATTACHMENT,
                conversation_id=conversation_id,
            )
            asset = await get_workspace_asset(
                session,
                asset_id=asset_id,
                conversation_id=conversation_id,
            )
            if asset.expires_at is not None:
                expiry_candidates.append(asset.expires_at)
            if private_path.suffix.lower() in {".txt", ".md", ".markdown", ".csv", ".tsv"}:
                raw = await asyncio.to_thread(private_path.read_bytes)
                try:
                    document = raw.decode("utf-8-sig")
                except UnicodeDecodeError:
                    try:
                        document = raw.decode("cp949")
                    except UnicodeDecodeError as error:
                        raise ReferenceProviderError(
                            "uploaded_reference_extraction_failed"
                        ) from error
            else:
                loader = DocumentsLoader(
                    [str(private_path)],
                    allow_context_truncation=True,
                )
                try:
                    await loader.load_documents()
                except Exception as error:
                    raise ReferenceProviderError(
                        "uploaded_reference_extraction_failed"
                    ) from error
                document = "\n\n".join(loader.documents)
            document = document.replace("\x00", "").strip()
            if not document:
                raise ReferenceProviderError("uploaded_reference_empty")
            if len(document) > remaining_characters:
                raise ReferenceProviderError("uploaded_reference_too_large")
            remaining_characters -= len(document)

            # Close the processing TOCTOU window by replaying byte/hash
            # admission after extraction. Persist only immutable identity and
            # normalized text, never this server-private locator.
            confirmed = await asset_gateway.bind(
                session,
                asset_id=asset_id,
                kind=AssetKind.CONVERSATION_ATTACHMENT,
                conversation_id=conversation_id,
            )
            if confirmed != binding:
                raise ReferenceProviderError(
                    "uploaded_reference_asset_changed", status_code=409
                )
            bindings.append(binding)
            extracted_documents.append(
                {
                    "assetId": str(asset_id),
                    "name": asset.original_name,
                    "contentSha256": sha256(document.encode("utf-8")).hexdigest(),
                    "text": document,
                }
            )
        identity = [binding.as_json() for binding in bindings]
        return NormalizedReference(
            provider_id=self.provider_id,
            source_revision=_digest(identity),
            normalized_payload={
                "purpose": "generation-reference",
                "assetCount": len(bindings),
                "documents": extracted_documents,
            },
            asset_bindings=tuple(bindings),
            expires_at=(
                min(
                    expiry_candidates,
                    key=lambda value: (
                        value
                        if value.tzinfo is not None
                        else value.replace(tzinfo=timezone.utc)
                    ),
                )
                if expiry_candidates
                else None
            ),
        )


@dataclass(frozen=True, slots=True)
class TemplateReferenceProvider:
    provider_id: str = "template"

    async def normalize(
        self,
        session: AsyncSession,
        *,
        request: dict[str, Any],
        asset_gateway: AssetGateway,
        conversation_id: uuid.UUID | None,
    ) -> NormalizedReference:
        del asset_gateway, conversation_id
        _strict_keys(request, frozenset({"template_id", "revision"}))
        template_id = request["template_id"]
        revision = request["revision"]
        if not isinstance(template_id, str) or not template_id.strip():
            raise ReferenceProviderError("template_reference_id_invalid")
        if isinstance(revision, bool) or not isinstance(revision, int) or revision < 1:
            raise ReferenceProviderError("template_reference_revision_invalid")

        owner_id = get_current_owner_id()
        owner_predicate = (
            TemplateV2.owner_id.is_(None)
            if owner_id is None
            else (
                (TemplateV2.owner_id == owner_id)
                | (TemplateV2.owner_id.is_(None) & TemplateV2.is_default.is_(True))
            )
        )

        snapshot = (
            await session.execute(
                select(TemplateV2Revision)
                .join(TemplateV2, TemplateV2.id == TemplateV2Revision.template_id)
                .where(
                    TemplateV2Revision.template_id == template_id,
                    TemplateV2Revision.revision == revision,
                    owner_predicate,
                )
            )
        ).scalar_one_or_none()
        if snapshot is None:
            raise ReferenceProviderError("template_reference_revision_not_found", status_code=404)

        exact_source = {
            "templateId": snapshot.template_id,
            "revision": snapshot.revision,
            "name": snapshot.name,
            "description": snapshot.description,
            "mergedComponents": snapshot.merged_components,
            "layouts": snapshot.layouts,
            "assets": snapshot.assets,
            "isDefault": snapshot.is_default,
        }
        content_digest = _digest(exact_source)
        return NormalizedReference(
            provider_id=self.provider_id,
            source_revision=f"{snapshot.template_id}:{snapshot.revision}",
            normalized_payload={
                "templateId": snapshot.template_id,
                "revision": snapshot.revision,
                "name": snapshot.name,
                "contentDigest": content_digest,
            },
        )


class ReferenceSourceProviderRegistry:
    def __init__(self, providers: Iterable[ReferenceSourceProvider]) -> None:
        entries: dict[str, ReferenceSourceProvider] = {}
        for provider in providers:
            if provider.provider_id in entries:
                raise ValueError(f"duplicate_reference_provider:{provider.provider_id}")
            entries[provider.provider_id] = provider
        self._providers = entries

    def require(self, provider_id: str) -> ReferenceSourceProvider:
        provider = self._providers.get(provider_id)
        if provider is None:
            raise ReferenceProviderError("reference_provider_unknown", status_code=422)
        return provider


def default_reference_source_registry() -> ReferenceSourceProviderRegistry:
    return ReferenceSourceProviderRegistry(
        (
            CommunityReferenceProvider(),
            UploadedDocumentProvider(),
            TemplateReferenceProvider(),
        )
    )
