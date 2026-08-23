"use client";

import type {
  AuthoredEmissionCapabilityProjection,
  AuthoredEmissionReadinessStatus,
  AuthoredReleaseReadinessProjectionV3,
  AuthoredSemanticDecision,
} from "../../../services/api/authored-emission-capability";

export type AuthoredEmissionCapabilityStatus =
  | { state: "checking" }
  | { state: "unavailable"; reason?: string }
  | { state: "ready"; readiness: AuthoredEmissionReadinessStatus };

const releaseCapabilityLabels: Record<string, string> = {
  "shape.gradient": "그라데이션",
  "shape.shadow": "그림자",
  "layout.transform": "Transform",
  "layout.flex": "Flex",
  "layout.grid": "Grid",
  "shape.borderRadius": "모서리 반경",
  "shape.opacity": "Opacity",
  "shape.clip": "Clip",
  "receipt.cjk": "CJK receipt",
  "receipt.chart": "Chart receipt",
  "receipt.zBand": "z-band receipt",
  "receipt.degradation": "Degradation receipt",
  "receipt.table": "Table receipt",
  "receipt.imageCrop": "Image crop receipt",
  "ci.requiredChecks": "CI required checks",
  "renderer.attestation": "Renderer attestation",
  "evidence.visual": "Visual evidence",
};

const capabilityLabels: Record<string, string> = {
  "shape.gradient": "그라데이션",
  "shape.shadow": "그림자",
  "layout.transform": "변환",
  "layout.flex": "Flex",
  "layout.grid": "Grid",
  "shape.borderRadius": "모서리 반경",
  "shape.opacity": "불투명도",
  "shape.clip": "클리핑",
};

const receiptLabels: Record<string, string> = {
  cjk: "CJK 글꼴",
  chart: "차트",
  zBand: "z-band",
  degradation: "degradation",
};

const decisionLabels: Record<AuthoredSemanticDecision, string> = {
  native: "Native",
  representationFallback: "표현 fallback",
  identityFailure: "ID 검증 실패",
  ownershipFailure: "소유권 불일치",
  assetPublicationFailure: "asset 게시 실패",
  blocked: "차단됨",
};

function decisionClass(decision: AuthoredSemanticDecision): string {
  if (decision === "native") return "text-emerald-800";
  if (decision === "representationFallback") return "text-amber-800";
  return "text-rose-800";
}

export function authoredHybridUnavailableReason(
  status: AuthoredEmissionCapabilityStatus,
  expectedRevision?: number,
): string | undefined {
  if (status.state === "checking") return "capability projection을 확인하는 중입니다.";
  if (status.state === "unavailable") return status.reason ?? "Hybrid capability projection을 확인하지 못했습니다.";
  const { readiness } = status;
  if (readiness.version === "v3" || readiness.version === "v2") {
    const { projection } = readiness;
    if (expectedRevision !== undefined && projection.presentationRevision !== expectedRevision) {
      return `현재 revision과 ${readiness.version} release readiness binding이 일치하지 않습니다.`;
    }
    const activeKillSwitch = projection.killSwitches.find((item) => item.active);
    if (activeKillSwitch) return `${activeKillSwitch.capabilityId}: ${activeKillSwitch.reasonCode}`;
    if (projection.degradedFidelityOnly) return "semantic source가 Fidelity 전용으로 강등되었습니다.";
    if (projection.canonicalMode !== "bundleGraph") return "legacy HTML revision은 Hybrid admission을 제공하지 않습니다.";
    if (!projection.canonicalDigest || !projection.manifestDigest) return "immutable manifest binding digest가 없습니다.";
    return undefined;
  }
  const projection = readiness.projection;
  if (expectedRevision !== undefined && projection.presentationRevision !== expectedRevision) {
    return "현재 revision과 capability projection의 manifest binding이 일치하지 않습니다.";
  }
  if (projection.degradedFidelityOnly) {
    return "semantic source를 사용할 수 없어 이 revision은 Fidelity 전용입니다.";
  }
  if (projection.canonicalMode !== "bundleGraph") {
    return "legacy HTML revision은 declared Hybrid admission을 제공하지 않습니다.";
  }
  if (!projection.canonicalDigest || !projection.manifestDigest) {
    return "immutable manifest binding digest가 없어 Hybrid를 시작할 수 없습니다.";
  }
  const activeKillSwitch = projection.killSwitches.find((item) => item.active);
  if (activeKillSwitch) {
    return activeKillSwitch.reason ?? `${activeKillSwitch.capabilityId} kill switch가 활성화되었습니다.`;
  }
  const readyKinds = [projection.hybrid.text, projection.hybrid.image, projection.hybrid.shape]
    .filter((capability) => capability.state === "ready");
  if (readyKinds.length === 0) {
    return projection.hybrid.text.reason ?? projection.hybrid.image.reason ?? projection.hybrid.shape.reason ?? "Hybrid capability receipt가 ready 상태가 아닙니다.";
  }
  return undefined;
}

export function authoredV3HybridStatusMessage(projection: AuthoredReleaseReadinessProjectionV3): string {
  if (
    projection.releaseProfile.promotionReady &&
    projection.exportAuthorityClaim?.kind === "promotion" &&
    projection.exportAuthorityClaim.state === "ready"
  ) {
    return "Hybrid (raster fallback) export 가능 · native subset은 지원하지 않으며 server promotion receipt가 ready입니다.";
  }
  return "Hybrid (raster fallback) 진단 export 가능 · native subset은 지원하지 않으며 promotion not ready입니다.";
}

export default function AuthoredEmissionCapabilityStatusView({
  status,
  expectedRevision,
}: {
  status: AuthoredEmissionCapabilityStatus;
  expectedRevision?: number;
}) {
  if (status.state === "checking") {
    return <p className="text-[11px] leading-4 text-slate-500" data-testid="authored-capability-status">Authored export capability를 확인하는 중입니다.</p>;
  }
  if (status.state === "unavailable") {
    return <p className="text-[11px] leading-4 text-amber-800" data-testid="authored-capability-status">Fidelity만 사용할 수 있습니다. {status.reason ?? "Hybrid capability projection을 확인하지 못했습니다."}</p>;
  }
  const { readiness } = status;
  if (readiness.version === "v3" || readiness.version === "v2") {
    const { projection } = readiness;
    const hybridReason = authoredHybridUnavailableReason(status, expectedRevision);
    const activeKillSwitches = projection.killSwitches.filter((item) => item.active);
    return (
      <div className="space-y-1 text-[11px] leading-4" data-testid="authored-capability-status" data-readiness-version={readiness.version}>
        <p className={hybridReason ? "text-amber-800" : "text-emerald-800"}>
          {hybridReason
            ? `Hybrid 제한: ${hybridReason}`
            : readiness.version === "v3"
              ? authoredV3HybridStatusMessage(projection)
              : `Hybrid 진단 export 가능 · immutable ${readiness.version} manifest binding 확인. Promotion은 서버 receipt만 결정합니다.`}
        </p>
        <details className="rounded border border-slate-200 bg-white/70 px-2 py-1" data-testid="authored-release-evidence-details">
          <summary className="cursor-pointer select-none font-medium text-slate-700">
            {readiness.version === "v3"
              ? `릴리스 증거 상세 · deck ${String(readiness.projection.releaseProfile.deckEvidenceReady)} · serializer ${String(readiness.projection.releaseProfile.serializerProfileReady)} · native N/A · commit ${String(readiness.projection.releaseProfile.releaseCommitReady)} · promotion ${String(readiness.projection.releaseProfile.promotionReady)}`
              : `릴리스 증거 상세 · promotion ${String(projection.releaseProfile.promotionReady)} · bounded native ${String(projection.releaseProfile.boundedNativeParityReady)}`}
          </summary>
          <div className="mt-1 space-y-1">
        <p className="break-all text-slate-500">
          profile {projection.releaseProfile.profileId} · {projection.releaseProfile.platformScope.platform}/{projection.releaseProfile.platformScope.renderer} · {projection.releaseProfile.reasonCode}
        </p>
        <p className="break-all text-slate-500">
          binding claim: {projection.canonicalMode} · canonical {projection.canonicalDigest ?? "미기록"} · manifest {projection.manifestDigest ?? "미기록"} · projection {projection.releaseProjectionDigest ?? "미발행"}
        </p>
        {readiness.version === "v3" ? (
          <>
            <p className="text-slate-500">
              deckEvidenceReady: {String(readiness.projection.releaseProfile.deckEvidenceReady)} · serializerProfileReady: {String(readiness.projection.releaseProfile.serializerProfileReady)} · native subset: 지원 안 함 ({readiness.projection.releaseProfile.nativeSubsetStatus})
            </p>
            <p className="text-slate-500">
              releaseCommitReady: {String(readiness.projection.releaseProfile.releaseCommitReady)} · promotionReady: {String(readiness.projection.releaseProfile.promotionReady)} · deploymentAuthorized: {String(readiness.projection.releaseProfile.deploymentAuthorized)}
            </p>
            <p className="text-slate-500">
              deprecated status: fullNativeParity {readiness.projection.releaseProfile.fullNativeParityStatus} · independentVnextGeneration {readiness.projection.releaseProfile.independentVnextGenerationStatus}. promotionReady는 배포 승인이 아닙니다.
            </p>
          </>
        ) : (
          <p className="text-slate-500">
            promotionReady: {String(projection.releaseProfile.promotionReady)} · boundedNativeParityReady: {String(projection.releaseProfile.boundedNativeParityReady)} · independentGenerationReady: {String(projection.releaseProfile.independentGenerationReady)}
          </p>
        )}
        <p className="break-all text-slate-500">
          exact pins: profile {projection.releaseProfile.profileDefinitionDigest} · claim {projection.exportAuthorityClaim ? `${projection.exportAuthorityClaim.kind}/${projection.exportAuthorityClaim.state}` : "미발행"} · authority receipts {projection.releaseAuthorityReceiptDigests.length}
        </p>
        <p className="text-slate-500">
          release projection digest와 export authority claim은 ready authority가 발행한 경우에만 표시됩니다. 비승격 상태의 미발행은 client-side readiness 판단이 아닙니다.
        </p>
        {readiness.version === "v3" ? (
          <p className="break-all text-slate-500">
            deck evidence [{readiness.projection.releaseProfile.deckEvidenceCapabilityIds.join(", ")}] · serializer profile [{readiness.projection.releaseProfile.serializerProfileCapabilityIds.join(", ")}] · native subset [지원 안 함] · unsupported native [{readiness.projection.releaseProfile.unsupportedNativeCapabilityIds.join(", ")}] · release commit [{readiness.projection.releaseProfile.releaseCommitCapabilityIds.join(", ")}] · all required [{readiness.projection.releaseProfile.allRequiredCapabilityIds.join(", ")}]
          </p>
        ) : (
          <p className="break-all text-slate-500">
            promotion [{projection.releaseProfile.promotionCapabilityIds.join(", ")}] · bounded native [{projection.releaseProfile.boundedNativeCapabilityIds.join(", ")}]
          </p>
        )}
        <section className="border-t border-slate-100 pt-1.5" aria-label={`서버 발행 ${readiness.version} scoped capabilities`}>
          <p className="font-medium text-slate-700">서버 발행 scoped capabilities</p>
          <div className="mt-1 grid gap-x-3 gap-y-1 sm:grid-cols-2">
            {projection.capabilities.map((capability) => (
              <div key={capability.capabilityId} className="min-w-0" data-testid={`authored-release-capability-${capability.capabilityId}`}>
                <span className="text-slate-600">{releaseCapabilityLabels[capability.capabilityId]}: </span>
                <span className={capability.state === "ready" ? "text-emerald-800" : capability.state === "shadow" ? "text-amber-800" : "text-rose-800"}>{capability.state}</span>
                <span className="text-slate-500"> · {capability.reasonCode} · {capability.scope.platform}/{capability.scope.renderer}</span>
                <span className="sr-only"> · receipt {capability.receiptDigest}</span>
              </div>
            ))}
          </div>
        </section>
        <section className="border-t border-slate-100 pt-1.5" aria-label="서버 발행 renderer attestation">
          <p className="font-medium text-slate-700">renderer attestation</p>
          <p className={projection.rendererAttestation.state === "attested" ? "text-emerald-800" : "text-amber-800"}>
            {projection.rendererAttestation.state} · {projection.rendererAttestation.reasonCode}
            <span className="sr-only"> · attestation {projection.rendererAttestation.attestationDigest}</span>
          </p>
        </section>
        <section className="border-t border-slate-100 pt-1.5" aria-label="서버 발행 capability kill switches">
          <p className="font-medium text-slate-700">capability kill switches</p>
          {activeKillSwitches.length ? activeKillSwitches.map((item) => (
            <p key={item.capabilityId} className="text-rose-800">{item.capabilityId} · active · {item.reasonCode}</p>
          )) : <p className="text-slate-500">서버가 보고한 active kill switch 없음</p>}
        </section>
          </div>
        </details>
      </div>
    );
  }
  const projection: AuthoredEmissionCapabilityProjection = readiness.projection;
  const hybridReason = authoredHybridUnavailableReason(status, expectedRevision);
  return (
    <div className="space-y-1 text-[11px] leading-4" data-testid="authored-capability-status">
      <p className={hybridReason ? "text-amber-800" : "text-emerald-800"}>
        {hybridReason ? `Fidelity 전용: ${hybridReason}` : "Fidelity와 Hybrid가 같은 immutable manifest에 묶여 있습니다."}
      </p>
      <p className="break-all text-slate-500">
        revision {projection.presentationRevision} · manifest {projection.manifestDigest ?? "미기록"}
      </p>
      <p className="text-slate-500">
        promotionReady: false · fullNativeParityReady: false · independentVnextGenerationReady: false
      </p>
      <section className="border-t border-slate-100 pt-1.5" aria-label="서버 발행 semantic capability matrix">
        <p className="font-medium text-slate-700">서버 발행 capability matrix</p>
        <div className="mt-1 grid gap-x-3 gap-y-1 sm:grid-cols-2">
          {projection.capabilityMatrix.map((capability) => (
            <div key={capability.capabilityId} className="min-w-0" data-testid={`authored-capability-${capability.capabilityId}`}>
              <span className="text-slate-600">{capabilityLabels[capability.capabilityId]}: </span>
              <span className={decisionClass(capability.decision)}>{decisionLabels[capability.decision]}</span>
              <span className="text-slate-500"> · {capability.state} · {capability.reasonCode}</span>
              <span className="sr-only"> · receipt {capability.receiptDigest}</span>
            </div>
          ))}
        </div>
      </section>
      <section className="border-t border-slate-100 pt-1.5" aria-label="서버 발행 semantic receipts">
        <p className="font-medium text-slate-700">서버 발행 receipts</p>
        <div className="mt-1 grid gap-x-3 gap-y-1 sm:grid-cols-2">
          {projection.receipts.map((receipt) => (
            <div key={receipt.receiptId} className="min-w-0" data-testid={`authored-receipt-${receipt.receiptId}`}>
              <span className="text-slate-600">{receiptLabels[receipt.receiptId]}: </span>
              <span className={decisionClass(receipt.decision)}>{decisionLabels[receipt.decision]}</span>
              <span className="text-slate-500"> · {receipt.state} · {receipt.reasonCode}</span>
              <span className="sr-only"> · receipt {receipt.receiptDigest}</span>
            </div>
          ))}
        </div>
      </section>
    </div>
  );
}
