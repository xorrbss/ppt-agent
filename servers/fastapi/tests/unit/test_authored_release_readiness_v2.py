from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import hmac
from types import SimpleNamespace
import uuid

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from api.v1.ppt.endpoints.authored_vnext.semantic.router import AUTHORED_SEMANTIC_ROUTER
from models.authored_vnext.semantic.contracts import ProductReadinessProjectionV1
from models.authored_vnext.semantic.readiness import (
    CapabilityEnvironmentV2,
    CapabilityReceiptIssueRequestV2,
    CapabilityReceiptRevokeRequestV2,
    VisualEvidenceArtifactPublishRequestV2,
    VisualParityGateIdentityV1,
    VisualParityGateReportV1,
)
from models.sql.authored_capability_receipt import AuthoredCapabilityReceiptRecordV2
from models.sql.authored_semantic_bundle import AuthoredSemanticArtifact
from models.sql.presentation import PresentationModel
from services.authored_vnext.semantic.canonical import (
    SemanticContractError,
    canonical_digest,
    canonical_json,
)
from services.authored_vnext.semantic import readiness
from services.authored_vnext.semantic.readiness import (
    PROMOTION_REQUIRED_CAPABILITY_IDS_V2,
    REQUIRED_CAPABILITY_IDS_V2,
    ReleaseAuthorityTargetV2,
    build_export_authority_claim,
    get_workbench_capability_projection_v2,
    issue_capability_receipt,
    load_release_profile_definition,
    publish_visual_evidence_artifact,
    revoke_capability_receipt,
    validate_attempt_receipt_pins,
)
from services.presentation_export_direct_attempt_service import (
    _completed_export_binding,
    _release_authority_pin_from_projection,
)
from models.authored_vnext.semantic.contracts import ExportAttemptDigestBindingV1
from models.authored_vnext.semantic.readiness import ReleasePinnedExportAttemptDigestBindingV2


def _digest(seed: str) -> str:
    return canonical_digest({"seed": seed})


def _target() -> ReleaseAuthorityTargetV2:
    definition, definition_digest = load_release_profile_definition()
    return ReleaseAuthorityTargetV2(
        profile_id=definition.profile_id,
        profile_definition=definition,
        profile_definition_digest=definition_digest,
        scope=definition.platform_scope,
        environment=CapabilityEnvironmentV2(
            deployment_environment="production",
            os_build="windows-11-24h2",
            office_build="powerpoint-16.0.19029",
            renderer_image_digest=_digest("renderer"),
            font_inventory_digest=_digest("fonts"),
            backend_build_digest=_digest("backend"),
        ),
        configured=True,
    )


def _request(
    capability_id: str,
    *,
    target: ReleaseAuthorityTargetV2,
    expires_at: datetime,
    supersedes: str | None = None,
) -> CapabilityReceiptIssueRequestV2:
    definition, definition_digest = load_release_profile_definition()
    is_visual = capability_id == definition.visual_evidence_capability_id
    return CapabilityReceiptIssueRequestV2(
        capability_id=capability_id,
        state="ready",
        scope=target.scope,
        environment=target.environment,
        serializer_id="presentation-export",
        serializer_version="2",
        feature_set_digest=_digest(f"feature:{capability_id}"),
        evidence_policy_id=(
            definition.visual_evidence_policy.policy_id
            if is_visual
            else "authored-release-policy/v2"
        ),
        policy_digest=definition_digest if is_visual else _digest("policy"),
        evidence_bundle_id=uuid.uuid4(),
        evidence_digest=_digest(f"evidence:{capability_id}"),
        expires_at=expires_at,
        reason_code="verified_evidence",
        supersedes_receipt_digest=supersedes,
    )


def test_durable_readiness_v2_authority_formula_transitions_and_attempt_pins(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    asyncio.run(_exercise_readiness(monkeypatch))


def test_visual_gate_report_requires_server_held_signature(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    key = "release-gate-test-key-with-at-least-32-bytes"
    monkeypatch.setenv("AUTHORED_RELEASE_VISUAL_GATE_HMAC_KEY", key)
    identity = VisualParityGateIdentityV1(
        gate_id="gate-signature",
        presentation_id=str(uuid.uuid4()),
        presentation_revision=1,
        operation_id=str(uuid.uuid4()),
        generation_attempt_id=str(uuid.uuid4()),
        registry_digest=_digest("registry"),
        semantic_proof={"path": "semantic-proof.json", "sha256": _digest("proof")},
        slide_count=3,
        modes=[{"mode": name} for name in ("authored", "fidelity", "hybrid")],
        diffs={"status": "passed"},
        ledger=[{"status": "passed"}],
        release_authority={"status": "authorized"},
        db_attempt_resolution={"status": "resolved"},
        freshness={"generatedAt": datetime.now(timezone.utc).isoformat()},
        atomic_binding_digest=_digest("atomic"),
    )
    unsigned = VisualParityGateReportV1(
        schema_version="authored-vnext-visual-parity-gate-report/v1",
        status="passed",
        promotion_ready=False,
        visual_parity="passed",
        languages=["ja", "ko", "zh"],
        archetypes=["chart"],
        identity=identity,
        evidence_digest=canonical_digest(identity),
    )
    payload = unsigned.model_dump(mode="json", by_alias=True, exclude_none=True)
    signature = "hmac-sha256:" + hmac.new(
        key.encode(), canonical_json(payload).encode(), sha256
    ).hexdigest()
    signed = unsigned.model_copy(update={"authority_signature": signature})
    readiness._validate_visual_gate_signature(signed)
    with pytest.raises(
        SemanticContractError, match="authored_visual_evidence_signature_invalid"
    ):
        readiness._validate_visual_gate_signature(
            signed.model_copy(update={"authority_signature": "hmac-sha256:" + "0" * 64})
        )


async def _exercise_readiness(monkeypatch: pytest.MonkeyPatch) -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(AuthoredCapabilityReceiptRecordV2.__table__.create)
        await connection.run_sync(PresentationModel.__table__.create)
        await connection.run_sync(AuthoredSemanticArtifact.__table__.create)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    now = datetime(2026, 8, 23, 1, 0, tzinfo=timezone.utc)
    expiry = now + timedelta(days=2)
    owner_id = uuid.uuid4()
    presentation_id = uuid.uuid4()
    target = _target()
    visual_calls: list[str] = []
    capability_calls: list[str] = []

    async def reviewed_visual_gate(
        _session,
        request: CapabilityReceiptIssueRequestV2,
        exact_target: ReleaseAuthorityTargetV2,
        tenant_id: str,
    ) -> None:
        assert tenant_id == "default"
        assert request.scope == exact_target.scope
        assert request.environment == exact_target.environment
        assert request.evidence_digest == _digest("evidence:evidence.visual")
        assert request.evidence_policy_id == exact_target.profile_definition.visual_evidence_policy.policy_id
        assert request.expires_at == expiry
        visual_calls.append(request.evidence_digest)

    async def reviewed_capability_gate(
        _session,
        request: CapabilityReceiptIssueRequestV2,
        exact_target: ReleaseAuthorityTargetV2,
        tenant_id: str,
    ) -> None:
        assert tenant_id == "default"
        assert request.capability_id != "evidence.visual"
        assert request.scope == exact_target.scope
        assert request.environment == exact_target.environment
        assert request.evidence_digest == _digest(
            f"evidence:{request.capability_id}"
        )
        capability_calls.append(request.capability_id)

    trusted_capability_validators = {
        capability_id: reviewed_capability_gate
        for capability_id in REQUIRED_CAPABILITY_IDS_V2
        if capability_id != "evidence.visual"
    }

    async def healthy_active_emission(*_args, **_kwargs):
        manifest_digest = _digest("manifest")
        return SimpleNamespace(
            canonical_mode="bundleGraph",
            presentation_revision=7,
            canonical_digest=manifest_digest,
            manifest=SimpleNamespace(canonical_digest=manifest_digest),
            bundles=[],
        )

    monkeypatch.setattr(readiness, "get_active_authored_emission", healthy_active_emission)

    async with sessions() as session:
        with pytest.raises(
            SemanticContractError,
            match="authored_capability_receipt_admin_required",
        ):
            await issue_capability_receipt(
                session,
                request=_request("receipt.cjk", target=target, expires_at=expiry),
                issuer_principal_id=owner_id,
                issuer_is_admin=False,
                release_target=target,
                now=now,
            )

        wrong_scope = target.scope.model_copy(update={"platform": "linux-x64"})
        with pytest.raises(
            SemanticContractError,
            match="authored_capability_receipt_authority_scope_mismatch",
        ):
            await issue_capability_receipt(
                session,
                request=_request(
                    "receipt.cjk", target=target, expires_at=expiry
                ).model_copy(update={"scope": wrong_scope}),
                issuer_principal_id=owner_id,
                issuer_is_admin=True,
                release_target=target,
                now=now,
            )

        with pytest.raises(
            SemanticContractError,
            match="authored_capability_receipt_trusted_validator_unavailable",
        ):
            await issue_capability_receipt(
                session,
                request=_request("receipt.cjk", target=target, expires_at=expiry),
                issuer_principal_id=owner_id,
                issuer_is_admin=True,
                release_target=target,
                now=now,
            )

        active: dict[str, str] = {}
        for capability_id in PROMOTION_REQUIRED_CAPABILITY_IDS_V2:
            receipt = await issue_capability_receipt(
                session,
                request=_request(capability_id, target=target, expires_at=expiry),
                issuer_principal_id=owner_id,
                issuer_is_admin=True,
                release_target=target,
                visual_evidence_validator=reviewed_visual_gate,
                capability_validators=trusted_capability_validators,
                now=now,
            )
            active[capability_id] = receipt.receipt_digest
        await session.flush()
        assert len(visual_calls) == 1

        promotion = await get_workbench_capability_projection_v2(
            session,
            presentation_id=presentation_id,
            owner_id=owner_id,
            target=target,
            now=now,
        )
        assert promotion.schema_version == "workbench-capability-projection/v2"
        assert promotion.canonical_mode == "bundleGraph"
        assert promotion.manifest_digest == _digest("manifest")
        assert promotion.degraded_fidelity_only is False
        assert promotion.release_profile.promotion_ready is True
        assert promotion.release_profile.bounded_native_parity_ready is False
        assert promotion.release_profile.promotion_capability_ids == list(
            PROMOTION_REQUIRED_CAPABILITY_IDS_V2
        )
        assert promotion.release_profile.required_capability_ids == list(
            PROMOTION_REQUIRED_CAPABILITY_IDS_V2
        )
        assert promotion.export_authority_claim is not None
        assert promotion.export_authority_claim.kind == "promotion"
        assert promotion.release_profile.independent_generation_ready is False
        assert promotion.renderer_attestation.state == "attested"
        assert promotion.renderer_attestation.attestation_digest == active[
            "renderer.attestation"
        ]

        chart_old = active["receipt.chart"]
        chart_new = await issue_capability_receipt(
            session,
            request=_request(
                "receipt.chart",
                target=target,
                expires_at=expiry,
                supersedes=chart_old,
            ),
            issuer_principal_id=owner_id,
            issuer_is_admin=True,
            release_target=target,
            capability_validators=trusted_capability_validators,
            now=now + timedelta(minutes=1),
        )
        active["receipt.chart"] = chart_new.receipt_digest

        for capability_id in (
            set(REQUIRED_CAPABILITY_IDS_V2)
            - set(PROMOTION_REQUIRED_CAPABILITY_IDS_V2)
        ):
            receipt = await issue_capability_receipt(
                session,
                request=_request(capability_id, target=target, expires_at=expiry),
                issuer_principal_id=owner_id,
                issuer_is_admin=True,
                release_target=target,
                capability_validators=trusted_capability_validators,
                now=now,
            )
            active[capability_id] = receipt.receipt_digest
        await session.flush()
        assert set(capability_calls) == set(REQUIRED_CAPABILITY_IDS_V2) - {
            "evidence.visual"
        }

        bounded = await get_workbench_capability_projection_v2(
            session,
            presentation_id=presentation_id,
            owner_id=owner_id,
            target=target,
            now=now + timedelta(minutes=2),
        )
        assert bounded.release_profile.promotion_ready is True
        assert bounded.release_profile.bounded_native_parity_ready is True
        assert bounded.export_authority_claim is not None
        assert bounded.export_authority_claim.kind == "boundedNative"
        assert bounded.release_authority_receipt_digests == (
            bounded.release_profile.bounded_native_receipt_digests
        )
        bounded_claim = build_export_authority_claim(
            bounded, kind="boundedNative"
        )

        await validate_attempt_receipt_pins(
            session,
            tenant_id="default",
            receipt_digests=bounded_claim.receipt_digests,
            claim=bounded_claim,
            renderer_source_digest=target.environment.renderer_image_digest,
            now=now + timedelta(minutes=2),
        )
        with pytest.raises(
            SemanticContractError, match="authored_export_receipt_pins_incomplete"
        ):
            await validate_attempt_receipt_pins(
                session,
                tenant_id="default",
                receipt_digests=bounded_claim.receipt_digests[:-1],
                claim=bounded_claim,
                renderer_source_digest=target.environment.renderer_image_digest,
                now=now + timedelta(minutes=2),
            )

        before_revoke = await session.scalar(
            select(func.count()).select_from(AuthoredCapabilityReceiptRecordV2)
        )
        revoked = await revoke_capability_receipt(
            session,
            receipt_digest=active["receipt.cjk"],
            request=CapabilityReceiptRevokeRequestV2(
                reason_code="evidence_revoked"
            ),
            issuer_principal_id=owner_id,
            issuer_is_admin=True,
            now=now + timedelta(minutes=3),
        )
        after_revoke = await session.scalar(
            select(func.count()).select_from(AuthoredCapabilityReceiptRecordV2)
        )
        assert revoked.transition_kind == "revoke"
        assert revoked.revokes_receipt_digest == active["receipt.cjk"]
        assert after_revoke == before_revoke + 1
        failed = await get_workbench_capability_projection_v2(
            session,
            presentation_id=presentation_id,
            owner_id=owner_id,
            target=target,
            now=now + timedelta(minutes=4),
        )
        assert failed.release_profile.promotion_ready is False
        assert failed.release_profile.bounded_native_parity_ready is False
        with pytest.raises(
            SemanticContractError, match="authored_export_receipt_pin_inactive"
        ):
            await validate_attempt_receipt_pins(
                session,
                tenant_id="default",
                receipt_digests=bounded_claim.receipt_digests,
                claim=bounded_claim,
                renderer_source_digest=target.environment.renderer_image_digest,
                now=now + timedelta(minutes=4),
            )

        unconfigured_target = ReleaseAuthorityTargetV2(
            profile_id=target.profile_id,
            profile_definition=target.profile_definition,
            profile_definition_digest=target.profile_definition_digest,
            scope=target.scope,
            environment=target.environment,
            configured=False,
        )
        unconfigured = await get_workbench_capability_projection_v2(
            session,
            presentation_id=presentation_id,
            owner_id=owner_id,
            target=unconfigured_target,
            now=now + timedelta(minutes=4),
        )
        assert unconfigured.release_authority_receipt_digests == []
        assert unconfigured.export_authority_claim is None
        assert _release_authority_pin_from_projection(
            unconfigured, export_mode="hybrid"
        )["state"] == "nonPromotable"

        # Hybrid may ship through the promotion subset with bounded raster
        # fallback; it upgrades to boundedNative only when that larger set is ready.
        promotion_pin = _release_authority_pin_from_projection(
            promotion, export_mode="hybrid"
        )
        assert promotion_pin["state"] == "ready"
        assert promotion_pin["kind"] == "promotion"
        assert _release_authority_pin_from_projection(
            bounded, export_mode="hybrid"
        )["kind"] == "boundedNative"

        identity = VisualParityGateIdentityV1(
            gate_id="gate-1",
            presentation_id=str(presentation_id),
            presentation_revision=7,
            operation_id=str(uuid.uuid4()),
            generation_attempt_id=str(uuid.uuid4()),
            registry_digest=_digest("registry"),
            semantic_proof={},
            slide_count=8,
            modes=[{"mode": name} for name in ("fidelity", "hybrid", "powerpoint")],
            diffs={"status": "passed"},
            ledger=[{"status": "passed"}],
            release_authority={"status": "passed"},
            db_attempt_resolution={"status": "passed"},
            freshness={"generatedAt": now.isoformat()},
            atomic_binding_digest=_digest("atomic"),
        )
        report = VisualParityGateReportV1(
            schema_version="authored-vnext-visual-parity-gate-report/v1",
            status="passed",
            promotion_ready=False,
            visual_parity="passed",
            languages=target.profile_definition.visual_evidence_policy.required_languages,
            archetypes=target.profile_definition.visual_evidence_policy.required_archetypes,
            identity=identity,
            evidence_digest=canonical_digest(identity),
        )
        session.add(
            PresentationModel(
                id=presentation_id,
                owner_id=owner_id,
                content="",
                n_slides=8,
                language="ko",
                mode="authored",
                theme={"mode": "authored"},
                revision=7,
            )
        )
        await session.flush()
        publish_request = VisualEvidenceArtifactPublishRequestV2(report=report)
        artifact = await publish_visual_evidence_artifact(
            session,
            presentation_id=presentation_id,
            request=publish_request,
            publisher_principal_id=owner_id,
            publisher_is_admin=True,
        )
        duplicate = await publish_visual_evidence_artifact(
            session,
            presentation_id=presentation_id,
            request=publish_request,
            publisher_principal_id=owner_id,
            publisher_is_admin=True,
        )
        assert duplicate == artifact
        assert await session.scalar(
            select(func.count()).select_from(AuthoredSemanticArtifact)
        ) == 1
        with pytest.raises(
            SemanticContractError,
            match="authored_visual_evidence_presentation_not_found",
        ):
            await publish_visual_evidence_artifact(
                session,
                presentation_id=presentation_id,
                request=publish_request,
                publisher_principal_id=uuid.uuid4(),
                publisher_is_admin=True,
            )

        binding_values = {
            "export_attempt_id": uuid.uuid4(),
            "presentation_id": presentation_id,
            "presentation_revision": 7,
            "operation_id": uuid.uuid4(),
            "generation_attempt_id": uuid.uuid4(),
            "slide_origin_generation_attempt_ids": [],
            "canonical_digest": _digest("canonical"),
            "manifest_digest": _digest("manifest-binding"),
            "semantic_digests": [],
            "master_contract_digest": _digest("master"),
            "composition_digest": _digest("composition"),
            "font_manifest_digest": _digest("font"),
            "asset_manifest_digest": _digest("asset"),
            "quality_policy_digest": _digest("quality"),
            "mode": "hybrid",
            "renderer_source_digest": target.environment.renderer_image_digest,
            "serializer_id": "authored-hybrid",
            "serializer_version": "2",
            "receipt_digests": [],
            "pptx_digest": _digest("pptx"),
        }
        legacy_binding = _completed_export_binding(binding_values, None)
        assert type(legacy_binding) is ExportAttemptDigestBindingV1
        strict_binding = _completed_export_binding(binding_values, bounded_claim)
        assert isinstance(strict_binding, ReleasePinnedExportAttemptDigestBindingV2)
        assert strict_binding.release_authority_receipt_digests == bounded_claim.receipt_digests
        assert strict_binding.release_projection_digest == bounded.projection_digest

    v1 = ProductReadinessProjectionV1()
    assert v1.promotion_ready is False
    assert v1.full_native_parity_ready is False
    assert v1.independent_vnext_generation_ready is False

    paths = {route.path for route in AUTHORED_SEMANTIC_ROUTER.routes}
    assert (
        "/presentation/{presentation_id}/authored-emission/capability-projection-v2"
        in paths
    )
    assert (
        "/presentation/{presentation_id}/authored-emission/export-attempt-bindings-v2"
        in paths
    )
    assert "/authored-emission/capability-receipts-v2" in paths
    assert (
        "/presentation/{presentation_id}/authored-emission/visual-evidence-artifacts-v2"
        in paths
    )
    assert (
        "/authored-emission/capability-receipts-v2/{receipt_digest}/revoke" in paths
    )
    await engine.dispose()
