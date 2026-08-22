from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
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
    CapabilityScopeV2,
)
from models.sql.authored_capability_receipt import AuthoredCapabilityReceiptRecordV2
from services.authored_vnext.semantic.canonical import SemanticContractError, canonical_digest
from services.authored_vnext.semantic import readiness
from services.authored_vnext.semantic.readiness import (
    PROMOTION_REQUIRED_CAPABILITY_IDS_V2,
    REQUIRED_CAPABILITY_IDS_V2,
    ReleaseAuthorityTargetV2,
    get_workbench_capability_projection_v2,
    issue_capability_receipt,
    revoke_capability_receipt,
    validate_attempt_receipt_pins,
    validate_visual_evidence_authority,
)


def _digest(seed: str) -> str:
    return canonical_digest({"seed": seed})


def _target() -> ReleaseAuthorityTargetV2:
    return ReleaseAuthorityTargetV2(
        profile_id="windows-powerpoint-prod",
        scope=CapabilityScopeV2(
            platform="windows-x64",
            renderer="presentation-export",
            archetypes=["cover", "section", "standard"],
        ),
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
    return CapabilityReceiptIssueRequestV2(
        capability_id=capability_id,
        state="ready",
        scope=target.scope,
        environment=target.environment,
        serializer_id="presentation-export",
        serializer_version="2",
        feature_set_digest=_digest(f"feature:{capability_id}"),
        evidence_policy_id="authored-release-policy/v2",
        policy_digest=_digest("policy"),
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


async def _exercise_readiness(monkeypatch: pytest.MonkeyPatch) -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(AuthoredCapabilityReceiptRecordV2.__table__.create)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    now = datetime(2026, 8, 23, 1, 0, tzinfo=timezone.utc)
    expiry = now + timedelta(days=2)
    owner_id = uuid.uuid4()
    presentation_id = uuid.uuid4()
    target = _target()
    visual_calls: list[str] = []

    def reviewed_visual_gate(
        request: CapabilityReceiptIssueRequestV2,
        exact_target: ReleaseAuthorityTargetV2,
    ) -> None:
        assert request.scope == exact_target.scope
        assert request.environment == exact_target.environment
        assert request.evidence_digest == _digest("evidence:evidence.visual")
        assert request.evidence_policy_id == "authored-release-policy/v2"
        assert request.expires_at == expiry
        visual_calls.append(request.evidence_digest)

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

        active: dict[str, str] = {}
        for capability_id in PROMOTION_REQUIRED_CAPABILITY_IDS_V2:
            receipt = await issue_capability_receipt(
                session,
                request=_request(capability_id, target=target, expires_at=expiry),
                issuer_principal_id=owner_id,
                issuer_is_admin=True,
                release_target=target,
                visual_evidence_validator=reviewed_visual_gate,
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
                now=now,
            )
            active[capability_id] = receipt.receipt_digest
        await session.flush()

        bounded = await get_workbench_capability_projection_v2(
            session,
            presentation_id=presentation_id,
            owner_id=owner_id,
            target=target,
            now=now + timedelta(minutes=2),
        )
        assert bounded.release_profile.promotion_ready is True
        assert bounded.release_profile.bounded_native_parity_ready is True

        await validate_attempt_receipt_pins(
            session,
            tenant_id="default",
            receipt_digests=list(active.values()),
            serializer_id="presentation-export",
            serializer_version="2",
            renderer_source_digest=target.environment.renderer_image_digest,
            now=now + timedelta(minutes=2),
        )
        with pytest.raises(
            SemanticContractError, match="authored_export_receipt_pins_incomplete"
        ):
            await validate_attempt_receipt_pins(
                session,
                tenant_id="default",
                receipt_digests=list(active.values())[:-1],
                serializer_id="presentation-export",
                serializer_version="2",
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
                receipt_digests=list(active.values()),
                serializer_id="presentation-export",
                serializer_version="2",
                renderer_source_digest=target.environment.renderer_image_digest,
                now=now + timedelta(minutes=4),
            )

    monkeypatch.delenv("AUTHORED_RELEASE_VISUAL_POLICY_ID", raising=False)
    with pytest.raises(
        SemanticContractError, match="authored_visual_evidence_authority_invalid"
    ):
        validate_visual_evidence_authority(
            _request("evidence.visual", target=target, expires_at=expiry), target
        )

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
        "/authored-emission/capability-receipts-v2/{receipt_digest}/revoke" in paths
    )
    await engine.dispose()
