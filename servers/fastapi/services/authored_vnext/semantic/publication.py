"""Atomic publication boundary for Authored semantic bundle graphs."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
import json
from typing import Any
import uuid

from pydantic import ValidationError
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from models.authored_vnext.semantic.contracts import (
    ArtifactPointerV1,
    AssetManifestV1,
    AuthoredDeckEmissionManifestV1,
    AuthoredEmissionBundleV1,
    AuthoredSemanticSourceV1,
    ElementIdentityRegistryV1,
    FontManifestV1,
    ImageElementV1,
    OrderedSlideBundleV1,
    ResolvedAssetV1,
    ResolvedFontV1,
    SemanticUnavailableReceiptV1,
    TextElementV1,
)
from models.sql.authored_semantic_bundle import (
    AuthoredDeckEmissionActivePointer,
    AuthoredDeckEmissionManifestRecord,
    AuthoredEmissionBundleRecord,
    AuthoredSemanticArtifact,
)
from models.sql.presentation import PresentationModel
from models.sql.slide import SlideModel

from .canonical import (
    SemanticContractError,
    bytes_digest,
    canonical_digest,
    canonical_json,
    seal_asset_manifest,
    seal_deck_manifest,
    seal_font_manifest,
    seal_identity_registry,
    seal_semantic_source,
    seal_unavailable_receipt,
    validate_and_seal_candidate,
)


DEFAULT_TENANT_ID = "default"
DEFAULT_QUALITY_POLICY = "authored-quality/v1"
DEFAULT_SEMANTIC_COMPILER = "authored-semantic-compiler/v1"


async def publish_authored_semantic_bundle_graph(
    session: AsyncSession,
    *,
    presentation_id: uuid.UUID,
    owner_id: uuid.UUID,
    expected_presentation_revision: int,
    operation_id: uuid.UUID,
    generation_attempt_id: uuid.UUID | None,
    slides: Sequence[SlideModel],
    htmls: Sequence[str],
    semantic_drafts: Sequence[Mapping[str, Any]],
    master_contract_digest: str,
    composition_digest: str,
    slide_asset_manifests: Sequence[AssetManifestV1] | None = None,
    slide_font_manifests: Sequence[FontManifestV1] | None = None,
    resolved_artifact_bytes: Mapping[str, bytes] | None = None,
    quality_policy_version: str = DEFAULT_QUALITY_POLICY,
    tenant_id: str = DEFAULT_TENANT_ID,
) -> AuthoredDeckEmissionManifestV1:
    """Seal a complete deck graph and CAS its pointer without committing.

    The caller owns the surrounding transaction.  Any failure raises a stable
    ``SemanticContractError`` so the presentation publication can roll back as
    one unit.  ``semantic_drafts`` may carry provisional slideIndex/htmlDigest
    fields, but declared QIDs and element data are never repaired or remapped.
    """

    count = len(slides)
    if not (count == len(htmls) == len(semantic_drafts)) or count == 0:
        raise SemanticContractError("authored_semantic_component_count_mismatch")
    if [slide.index for slide in slides] != list(range(count)):
        raise SemanticContractError("authored_manifest_slide_order_invalid")
    if any(slide.presentation != presentation_id for slide in slides):
        raise SemanticContractError("authored_manifest_presentation_mismatch")
    if any(slide.owner_id not in {None, owner_id} for slide in slides):
        raise SemanticContractError("authored_semantic_owner_mismatch")
    target_revision = expected_presentation_revision + 1
    assets = list(slide_asset_manifests or (AssetManifestV1() for _ in slides))
    fonts = list(slide_font_manifests or (FontManifestV1() for _ in slides))
    if len(assets) != count or len(fonts) != count:
        raise SemanticContractError("authored_semantic_manifest_count_mismatch")
    byte_map = dict(resolved_artifact_bytes or {})

    presentation = await session.scalar(
        select(PresentationModel).where(
            PresentationModel.id == presentation_id,
            PresentationModel.owner_id == owner_id,
        )
    )
    if presentation is None:
        raise SemanticContractError("authored_semantic_presentation_not_found")
    if presentation.revision != expected_presentation_revision:
        raise SemanticContractError("authored_semantic_cas_conflict")

    sealed_asset_manifests = [seal_asset_manifest(item) for item in assets]
    sealed_font_manifests = [seal_font_manifest(item) for item in fonts]
    await _persist_resolved_bytes(
        session,
        owner_id=owner_id,
        tenant_id=tenant_id,
        assets=sealed_asset_manifests,
        fonts=sealed_font_manifests,
        byte_map=byte_map,
    )

    bundles: list[AuthoredEmissionBundleV1] = []
    for slide, html, draft, asset_manifest, font_manifest in zip(
        slides,
        htmls,
        semantic_drafts,
        sealed_asset_manifests,
        sealed_font_manifests,
        strict=True,
    ):
        html_bytes = html.encode("utf-8")
        html_pointer = await _put_artifact(
            session,
            owner_id=owner_id,
            tenant_id=tenant_id,
            content=html_bytes,
            media_type="text/html;charset=utf-8",
            retention_class="active",
        )
        asset_pointer = await _put_json_artifact(
            session,
            owner_id=owner_id,
            tenant_id=tenant_id,
            value=asset_manifest,
            media_type="application/vnd.presenton.authored-assets+json;version=1",
        )
        font_pointer = await _put_json_artifact(
            session,
            owner_id=owner_id,
            tenant_id=tenant_id,
            value=font_manifest,
            media_type="application/vnd.presenton.authored-fonts+json;version=1",
        )
        component = _materialize_component(
            draft,
            presentation_id=presentation_id,
            slide_id=slide.id,
            slide_revision=target_revision,
            operation_id=operation_id,
            generation_attempt_id=generation_attempt_id,
            html_digest=html_pointer.digest,
        )
        if isinstance(component, AuthoredSemanticSourceV1):
            _validate_declared_resources(component, asset_manifest, font_manifest)
            registry_digest = component.identity_registry.digest
        else:
            empty_registry = seal_identity_registry(
                ElementIdentityRegistryV1(active_qids=[], retired_qids=[])
            )
            registry_digest = empty_registry.digest
        if registry_digest is None:
            raise SemanticContractError("authored_semantic_registry_digest_missing")
        bundle = AuthoredEmissionBundleV1(
            presentation_id=presentation_id,
            slide_id=slide.id,
            slide_revision=target_revision,
            operation_id=operation_id,
            origin_generation_attempt_id=generation_attempt_id,
            authored_html=html_pointer,
            semantic_component=component,
            asset_manifest=asset_pointer,
            font_manifest=font_pointer,
            master_contract_digest=master_contract_digest,
            composition_digest=composition_digest,
            element_identity_registry_digest=registry_digest,
            quality_policy_version=quality_policy_version,
        )
        sealed_candidate = validate_and_seal_candidate(html_bytes, bundle)
        sealed_bundle = sealed_candidate.bundle
        assert isinstance(sealed_bundle, AuthoredEmissionBundleV1)
        bundles.append(sealed_bundle)
        await _put_json_artifact(
            session,
            owner_id=owner_id,
            tenant_id=tenant_id,
            value=sealed_bundle.semantic_component,
            media_type="application/vnd.presenton.authored-semantic+json;version=1",
        )
        await _insert_bundle_record(
            session,
            owner_id=owner_id,
            tenant_id=tenant_id,
            bundle=sealed_bundle,
        )

    deck_asset_manifest = seal_asset_manifest(_union_assets(sealed_asset_manifests))
    deck_font_manifest = seal_font_manifest(_union_fonts(sealed_font_manifests))
    await _put_json_artifact(
        session,
        owner_id=owner_id,
        tenant_id=tenant_id,
        value=deck_asset_manifest,
        media_type="application/vnd.presenton.authored-assets+json;version=1",
    )
    await _put_json_artifact(
        session,
        owner_id=owner_id,
        tenant_id=tenant_id,
        value=deck_font_manifest,
        media_type="application/vnd.presenton.authored-fonts+json;version=1",
    )
    if deck_asset_manifest.digest is None or deck_font_manifest.digest is None:
        raise SemanticContractError("authored_semantic_manifest_digest_missing")
    manifest = seal_deck_manifest(
        AuthoredDeckEmissionManifestV1(
            presentation_id=presentation_id,
            presentation_revision=target_revision,
            operation_id=operation_id,
            generation_attempt_id=generation_attempt_id,
            ordered_slides=[
                OrderedSlideBundleV1(
                    slide_id=bundle.slide_id,
                    slide_revision=bundle.slide_revision,
                    bundle_kind=bundle.bundle_kind,
                    bundle_digest=_required(bundle.bundle_digest),
                )
                for bundle in bundles
            ],
            master_contract_digest=master_contract_digest,
            composition_digest=composition_digest,
            asset_manifest_digest=deck_asset_manifest.digest,
            font_manifest_digest=deck_font_manifest.digest,
            quality_policy_version=quality_policy_version,
        )
    )
    await _publish_manifest_cas(
        session,
        owner_id=owner_id,
        tenant_id=tenant_id,
        manifest=manifest,
        expected_presentation_revision=expected_presentation_revision,
    )
    return manifest


def _materialize_component(
    draft: Mapping[str, Any],
    *,
    presentation_id: uuid.UUID,
    slide_id: uuid.UUID,
    slide_revision: int,
    operation_id: uuid.UUID,
    generation_attempt_id: uuid.UUID | None,
    html_digest: str,
) -> AuthoredSemanticSourceV1 | SemanticUnavailableReceiptV1:
    if set(draft) == {"semanticSource"} and isinstance(draft["semanticSource"], Mapping):
        source = dict(draft["semanticSource"])
        claimed_html = source.pop("htmlDigest", source.pop("authoredHtmlDigest", html_digest))
        source.pop("slideIndex", None)
        if claimed_html != html_digest:
            raise SemanticContractError("authored_semantic_html_digest_mismatch")
        registry = source.pop("elementIdentityRegistry", source.pop("identityRegistry", None))
        source.update(
            {
                "schemaVersion": "authored-semantic-source/v1",
                "componentKind": "authoredSemanticSource",
                "presentationId": presentation_id,
                "slideId": slide_id,
                "slideRevision": slide_revision,
                "operationId": operation_id,
                "origin": "authored-declared-v1",
                "authoredHtmlDigest": html_digest,
                "identityRegistry": registry,
            }
        )
        try:
            return seal_semantic_source(AuthoredSemanticSourceV1.model_validate(source))
        except ValidationError as error:
            raise SemanticContractError("authored_semantic_validation_failed") from error
    if set(draft) == {"semanticUnavailableReceipt"} and isinstance(
        draft["semanticUnavailableReceipt"], Mapping
    ):
        unavailable = dict(draft["semanticUnavailableReceipt"])
        claimed_html = unavailable.get("htmlDigest", html_digest)
        if claimed_html != html_digest:
            raise SemanticContractError("authored_semantic_html_digest_mismatch")
        failure_code = str(unavailable.get("failureCode", "authored_semantic_validation_failed"))
        if failure_code not in {
            "authored_semantic_generation_failed",
            "authored_semantic_validation_failed",
            "authored_semantic_retry_exhausted",
            "legacy_semantic_unavailable",
        }:
            failure_code = "authored_semantic_validation_failed"
        return seal_unavailable_receipt(
            SemanticUnavailableReceiptV1(
                presentation_id=presentation_id,
                slide_id=slide_id,
                slide_revision=slide_revision,
                operation_id=operation_id,
                origin_generation_attempt_id=generation_attempt_id,
                authored_html_digest=html_digest,
                failure_code=failure_code,  # type: ignore[arg-type]
                bounded_attempt_count=max(
                    0,
                    min(8, int(unavailable.get("boundedAttemptCount", 1))),
                ),
                compiler_version=DEFAULT_SEMANTIC_COMPILER,
                policy_version="authored-semantic-bounded-retry/v1",
            )
        )
    raise SemanticContractError("authored_semantic_exact_one_component_required")


async def _persist_resolved_bytes(
    session: AsyncSession,
    *,
    owner_id: uuid.UUID,
    tenant_id: str,
    assets: Sequence[AssetManifestV1],
    fonts: Sequence[FontManifestV1],
    byte_map: Mapping[str, bytes],
) -> None:
    entries: list[ResolvedAssetV1 | ResolvedFontV1] = [
        *(item for manifest in assets for item in manifest.assets),
        *(item for manifest in fonts for item in manifest.fonts),
    ]
    for entry in entries:
        content = byte_map.get(entry.artifact.digest)
        if content is None:
            raise SemanticContractError("authored_semantic_asset_bytes_missing")
        if bytes_digest(content) != entry.artifact.digest:
            raise SemanticContractError("authored_semantic_asset_digest_mismatch")
        if len(content) != entry.artifact.byte_length:
            raise SemanticContractError("authored_semantic_asset_length_mismatch")
        await _put_artifact(
            session,
            owner_id=owner_id,
            tenant_id=tenant_id,
            content=content,
            media_type=entry.artifact.media_type,
            retention_class=entry.retention_class,
            original_locator_digest=entry.locator_digest,
            resolver_version=entry.resolver_version,
        )


def _validate_declared_resources(
    source: AuthoredSemanticSourceV1,
    assets: AssetManifestV1,
    fonts: FontManifestV1,
) -> None:
    asset_by_id = {item.asset_id: item for item in assets.assets}
    font_faces = {(item.family, item.face) for item in fonts.fonts}
    for element in source.elements:
        if isinstance(element, ImageElementV1):
            asset = asset_by_id.get(element.payload.asset_id)
            if asset is None or asset.artifact.digest != element.payload.asset_digest:
                raise SemanticContractError("authored_semantic_asset_binding_mismatch")
        elif isinstance(element, TextElementV1):
            for paragraph in element.payload.paragraphs:
                for run in paragraph.runs:
                    if (run.font_family, run.font_face) not in font_faces:
                        raise SemanticContractError("authored_semantic_font_binding_mismatch")


async def _put_json_artifact(
    session: AsyncSession,
    *,
    owner_id: uuid.UUID,
    tenant_id: str,
    value: Any,
    media_type: str,
) -> ArtifactPointerV1:
    content = canonical_json(value).encode("utf-8")
    return await _put_artifact(
        session,
        owner_id=owner_id,
        tenant_id=tenant_id,
        content=content,
        media_type=media_type,
        retention_class="active",
    )


async def _put_artifact(
    session: AsyncSession,
    *,
    owner_id: uuid.UUID,
    tenant_id: str,
    content: bytes,
    media_type: str,
    retention_class: str,
    original_locator_digest: str | None = None,
    resolver_version: str | None = None,
) -> ArtifactPointerV1:
    digest = bytes_digest(content)
    artifact_ref = "artifact://sha256/" + digest.removeprefix("sha256:")
    existing = await session.scalar(
        select(AuthoredSemanticArtifact).where(
            AuthoredSemanticArtifact.owner_id == owner_id,
            AuthoredSemanticArtifact.tenant_id == tenant_id,
            AuthoredSemanticArtifact.content_digest == digest,
        )
    )
    if existing is not None:
        if existing.content_bytes != content or existing.byte_length != len(content):
            raise SemanticContractError("authored_semantic_artifact_digest_collision")
    else:
        session.add(
            AuthoredSemanticArtifact(
                owner_id=owner_id,
                tenant_id=tenant_id,
                artifact_ref=artifact_ref,
                content_digest=digest,
                byte_length=len(content),
                media_type=media_type,
                original_locator_digest=original_locator_digest,
                resolver_version=resolver_version,
                retention_class=retention_class,
                content_bytes=content,
            )
        )
        await session.flush()
    return ArtifactPointerV1(
        artifact_ref=artifact_ref,
        digest=digest,
        byte_length=len(content),
        media_type=media_type,
    )


async def _insert_bundle_record(
    session: AsyncSession,
    *,
    owner_id: uuid.UUID,
    tenant_id: str,
    bundle: AuthoredEmissionBundleV1,
) -> None:
    digest = _required(bundle.bundle_digest)
    existing = await session.scalar(
        select(AuthoredEmissionBundleRecord).where(
            AuthoredEmissionBundleRecord.owner_id == owner_id,
            AuthoredEmissionBundleRecord.tenant_id == tenant_id,
            AuthoredEmissionBundleRecord.bundle_digest == digest,
        )
    )
    if existing is not None:
        if existing.payload != bundle.model_dump(mode="json", by_alias=True):
            raise SemanticContractError("authored_semantic_bundle_digest_collision")
        return
    component_digest = _required(bundle.semantic_component.digest)
    session.add(
        AuthoredEmissionBundleRecord(
            owner_id=owner_id,
            tenant_id=tenant_id,
            presentation_id=bundle.presentation_id,
            slide_id=bundle.slide_id,
            slide_revision=bundle.slide_revision,
            bundle_kind=bundle.bundle_kind,
            operation_id=bundle.operation_id,
            origin_generation_attempt_id=bundle.origin_generation_attempt_id,
            html_digest=bundle.authored_html.digest,
            semantic_component_digest=component_digest,
            asset_manifest_digest=bundle.asset_manifest.digest,
            font_manifest_digest=bundle.font_manifest.digest,
            master_contract_digest=bundle.master_contract_digest,
            composition_digest=bundle.composition_digest,
            identity_registry_digest=bundle.element_identity_registry_digest,
            quality_policy_version=bundle.quality_policy_version,
            bundle_digest=digest,
            payload=bundle.model_dump(mode="json", by_alias=True),
        )
    )
    await session.flush()


async def _publish_manifest_cas(
    session: AsyncSession,
    *,
    owner_id: uuid.UUID,
    tenant_id: str,
    manifest: AuthoredDeckEmissionManifestV1,
    expected_presentation_revision: int,
) -> None:
    canonical = _required(manifest.canonical_digest)
    record = AuthoredDeckEmissionManifestRecord(
        owner_id=owner_id,
        tenant_id=tenant_id,
        presentation_id=manifest.presentation_id,
        presentation_revision=manifest.presentation_revision,
        operation_id=manifest.operation_id,
        generation_attempt_id=manifest.generation_attempt_id,
        canonical_digest=canonical,
        master_contract_digest=manifest.master_contract_digest,
        composition_digest=manifest.composition_digest,
        asset_manifest_digest=manifest.asset_manifest_digest,
        font_manifest_digest=manifest.font_manifest_digest,
        quality_policy_version=manifest.quality_policy_version,
        payload=manifest.model_dump(mode="json", by_alias=True),
    )
    session.add(record)
    await session.flush()
    pointer = await session.scalar(
        select(AuthoredDeckEmissionActivePointer).where(
            AuthoredDeckEmissionActivePointer.owner_id == owner_id,
            AuthoredDeckEmissionActivePointer.tenant_id == tenant_id,
            AuthoredDeckEmissionActivePointer.presentation_id == manifest.presentation_id,
        )
    )
    if pointer is None:
        # The old HTML-only state remains readable.  Its absence from this table
        # is the compatibility representation, never an implicit bundle success.
        session.add(
            AuthoredDeckEmissionActivePointer(
                owner_id=owner_id,
                tenant_id=tenant_id,
                presentation_id=manifest.presentation_id,
                canonical_mode="bundleGraph",
                manifest_id=record.id,
                presentation_revision=manifest.presentation_revision,
                canonical_digest=canonical,
                generation=1,
                operation_id=manifest.operation_id,
            )
        )
        await session.flush()
        return
    result = await session.execute(
        update(AuthoredDeckEmissionActivePointer)
        .where(
            AuthoredDeckEmissionActivePointer.id == pointer.id,
            AuthoredDeckEmissionActivePointer.presentation_revision
            == expected_presentation_revision,
            AuthoredDeckEmissionActivePointer.canonical_digest == pointer.canonical_digest,
            AuthoredDeckEmissionActivePointer.generation == pointer.generation,
        )
        .values(
            canonical_mode="bundleGraph",
            manifest_id=record.id,
            presentation_revision=manifest.presentation_revision,
            canonical_digest=canonical,
            generation=AuthoredDeckEmissionActivePointer.generation + 1,
            operation_id=manifest.operation_id,
        )
    )
    if result.rowcount != 1:
        raise SemanticContractError("authored_semantic_cas_conflict")


def _union_assets(manifests: Sequence[AssetManifestV1]) -> AssetManifestV1:
    by_id: dict[str, ResolvedAssetV1] = {}
    for manifest in manifests:
        for asset in manifest.assets:
            existing = by_id.get(asset.asset_id)
            if existing is not None and existing.artifact.digest != asset.artifact.digest:
                raise SemanticContractError("authored_semantic_asset_id_conflict")
            by_id[asset.asset_id] = asset
    return AssetManifestV1(assets=[by_id[key] for key in sorted(by_id)])


def _union_fonts(manifests: Sequence[FontManifestV1]) -> FontManifestV1:
    by_id: dict[str, ResolvedFontV1] = {}
    for manifest in manifests:
        for font in manifest.fonts:
            existing = by_id.get(font.face_id)
            if existing is not None and existing.artifact.digest != font.artifact.digest:
                raise SemanticContractError("authored_semantic_font_id_conflict")
            by_id[font.face_id] = font
    return FontManifestV1(fonts=[by_id[key] for key in sorted(by_id)])


def _required(value: str | None) -> str:
    if value is None:
        raise SemanticContractError("authored_semantic_digest_missing")
    return value


__all__ = ["publish_authored_semantic_bundle_graph"]
