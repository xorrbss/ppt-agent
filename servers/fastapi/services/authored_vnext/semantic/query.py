"""Owner-scoped active graph and Workbench capability projections."""

from __future__ import annotations

import json
import os
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from models.authored_vnext.semantic.contracts import (
    ActiveAuthoredEmissionResponseV1,
    AuthoredDeckEmissionManifestV1,
    AuthoredEmissionBundleV1,
    CapabilityAvailabilityV1,
    EditableElementProjectionV1,
    EmissionBundleV1,
    HybridAvailabilityV1,
    KillSwitchProjectionV1,
    LegacyHtmlBundleV1,
    ImageElementV1,
    ShapeElementV1,
    SemanticUnavailableReceiptV1,
    TextElementV1,
    WorkbenchCapabilityProjectionV1,
)
from models.sql.authored_semantic_bundle import (
    AuthoredDeckEmissionActivePointer,
    AuthoredDeckEmissionManifestRecord,
    AuthoredEmissionBundleRecord,
)
from models.sql.presentation import PresentationModel

from .canonical import SemanticContractError, canonical_digest


async def get_active_authored_emission(
    session: AsyncSession,
    *,
    presentation_id: uuid.UUID,
    owner_id: uuid.UUID,
    tenant_id: str = "default",
) -> ActiveAuthoredEmissionResponseV1:
    presentation = await session.scalar(
        select(PresentationModel).where(
            PresentationModel.id == presentation_id,
            PresentationModel.owner_id == owner_id,
        )
    )
    if presentation is None:
        raise SemanticContractError("authored_semantic_presentation_not_found")
    pointer = await session.scalar(
        select(AuthoredDeckEmissionActivePointer).where(
            AuthoredDeckEmissionActivePointer.owner_id == owner_id,
            AuthoredDeckEmissionActivePointer.tenant_id == tenant_id,
            AuthoredDeckEmissionActivePointer.presentation_id == presentation_id,
        )
    )
    if pointer is None or pointer.canonical_mode == "legacyHtml":
        return ActiveAuthoredEmissionResponseV1(
            presentation_id=presentation_id,
            canonical_mode="legacyHtml",
            presentation_revision=presentation.revision,
        )
    manifest_record = await session.scalar(
        select(AuthoredDeckEmissionManifestRecord).where(
            AuthoredDeckEmissionManifestRecord.id == pointer.manifest_id,
            AuthoredDeckEmissionManifestRecord.owner_id == owner_id,
            AuthoredDeckEmissionManifestRecord.tenant_id == tenant_id,
        )
    )
    if manifest_record is None:
        raise SemanticContractError("authored_semantic_active_manifest_unresolvable")
    manifest = AuthoredDeckEmissionManifestV1.model_validate_json(
        json.dumps(manifest_record.payload, ensure_ascii=False)
    )
    if manifest.canonical_digest != pointer.canonical_digest:
        raise SemanticContractError("authored_semantic_active_manifest_digest_mismatch")
    bundle_records = list(
        await session.scalars(
            select(AuthoredEmissionBundleRecord).where(
                AuthoredEmissionBundleRecord.owner_id == owner_id,
                AuthoredEmissionBundleRecord.tenant_id == tenant_id,
                AuthoredEmissionBundleRecord.bundle_digest.in_(
                    [item.bundle_digest for item in manifest.ordered_slides]
                ),
            )
        )
    )
    records_by_digest = {item.bundle_digest: item for item in bundle_records}
    bundles: list[EmissionBundleV1] = []
    for entry in manifest.ordered_slides:
        record = records_by_digest.get(entry.bundle_digest)
        if record is None or record.bundle_kind != entry.bundle_kind:
            raise SemanticContractError("authored_semantic_active_bundle_unresolvable")
        model = (
            AuthoredEmissionBundleV1
            if entry.bundle_kind == "authored"
            else LegacyHtmlBundleV1
        )
        bundle = model.model_validate_json(json.dumps(record.payload, ensure_ascii=False))
        if bundle.bundle_digest != entry.bundle_digest or bundle.slide_id != entry.slide_id:
            raise SemanticContractError("authored_semantic_active_bundle_digest_mismatch")
        bundles.append(bundle)
    return ActiveAuthoredEmissionResponseV1(
        presentation_id=presentation_id,
        canonical_mode="bundleGraph",
        presentation_revision=pointer.presentation_revision,
        canonical_digest=pointer.canonical_digest,
        manifest=manifest,
        bundles=bundles,
    )


async def get_workbench_capability_projection(
    session: AsyncSession,
    *,
    presentation_id: uuid.UUID,
    owner_id: uuid.UUID,
    tenant_id: str = "default",
) -> WorkbenchCapabilityProjectionV1:
    active = await get_active_authored_emission(
        session,
        presentation_id=presentation_id,
        owner_id=owner_id,
        tenant_id=tenant_id,
    )
    degraded = any(
        (
            isinstance(bundle, AuthoredEmissionBundleV1)
            and isinstance(bundle.semantic_component, SemanticUnavailableReceiptV1)
        )
        or (
            isinstance(bundle, LegacyHtmlBundleV1)
            and isinstance(bundle.compatibility_component, SemanticUnavailableReceiptV1)
        )
        for bundle in active.bundles
    )
    elements: list[EditableElementProjectionV1] = []
    declared_kinds: set[str] = set()
    for bundle in active.bundles:
        if not isinstance(bundle, AuthoredEmissionBundleV1) or isinstance(
            bundle.semantic_component, SemanticUnavailableReceiptV1
        ):
            continue
        for element in bundle.semantic_component.elements:
            if isinstance(element, TextElementV1):
                declared_kinds.add("text")
                editable_fields = ["text"]
                read_only_reason = None
            elif isinstance(element, ImageElementV1):
                declared_kinds.add("image")
                editable_fields = ["imageAsset"]
                read_only_reason = None
            elif isinstance(element, ShapeElementV1):
                declared_kinds.add("shape")
                editable_fields = ["fill", "stroke"]
                read_only_reason = None
            else:
                editable_fields = []
                read_only_reason = "serializer_kind_unsupported"
            elements.append(
                EditableElementProjectionV1(
                    element_id=element.element_id,
                    editable_fields=editable_fields,
                    read_only_reason=read_only_reason,
                )
            )
    kill_active = os.getenv("AUTHORED_SEMANTIC_HYBRID_KILL_SWITCH", "false").lower() == "true"
    disabled = kill_active or degraded or active.canonical_mode == "legacyHtml"
    disabled_reason = (
        "authored_semantic_kill_switch_active"
        if kill_active
        else "semantic_unavailable"
        if degraded
        else "legacy_html_compatibility"
        if active.canonical_mode == "legacyHtml"
        else "server_admission_shadow_only"
    )

    def receipt_digest(capability_id: str, state: str, reason: str | None) -> str:
        return canonical_digest(
            {
                "schemaVersion": "authored-capability-projection-receipt/v1",
                "capabilityId": capability_id,
                "state": state,
                "reason": reason,
                "presentationId": str(presentation_id),
                "presentationRevision": active.presentation_revision,
                "manifestDigest": active.canonical_digest,
                "registryVersion": "semantic-native-registry/v1",
            }
        )

    def availability(kind: str) -> CapabilityAvailabilityV1:
        if disabled:
            return CapabilityAvailabilityV1(
                state="disabled",
                reason=disabled_reason,
                receipt_digest=receipt_digest(f"hybrid.{kind}", "disabled", disabled_reason),
            )
        if kind in declared_kinds:
            return CapabilityAvailabilityV1(
                state="ready",
                receipt_digest=receipt_digest(f"hybrid.{kind}", "ready", None),
            )
        return CapabilityAvailabilityV1(
            state="shadow",
            reason="no_declared_element_for_capability",
            receipt_digest=receipt_digest(
                f"hybrid.{kind}", "shadow", "no_declared_element_for_capability"
            ),
        )

    projection = WorkbenchCapabilityProjectionV1(
        presentation_id=presentation_id,
        canonical_mode=active.canonical_mode,
        presentation_revision=active.presentation_revision,
        canonical_digest=active.canonical_digest,
        manifest_digest=active.manifest.canonical_digest if active.manifest else None,
        degraded_fidelity_only=degraded,
        fidelity=CapabilityAvailabilityV1(
            state="ready",
            receipt_digest=receipt_digest("fidelity", "ready", None),
        ),
        hybrid=HybridAvailabilityV1(
            text=availability("text"),
            image=availability("image"),
            shape=availability("shape"),
        ),
        kill_switches=[
            KillSwitchProjectionV1(
                capability_id="hybrid.all",
                active=kill_active,
                reason="authored_semantic_kill_switch_active" if kill_active else None,
            )
        ],
        elements=elements,
    )
    payload = projection.model_dump(mode="json", by_alias=True)
    payload.pop("projectionDigest", None)
    return projection.model_copy(update={"projection_digest": canonical_digest(payload)})


__all__ = ["get_active_authored_emission", "get_workbench_capability_projection"]
