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

type CapabilityKey = keyof (typeof import("@/i18n/messages/en.json"))["presentationCapability"];
type CapabilityTranslator = (key: CapabilityKey, values?: Record<string, string | number>) => string;

const releaseCapabilityLabels: Record<string, CapabilityKey> = {
  "shape.gradient": "gradient", "shape.shadow": "shadow",
  "layout.transform": "transform", "layout.flex": "flex", "layout.grid": "grid",
  "shape.borderRadius": "borderRadius", "shape.opacity": "opacity", "shape.clip": "clip",
  "receipt.cjk": "cjkReceipt", "receipt.chart": "chartReceipt",
  "receipt.zBand": "zBandReceipt", "receipt.degradation": "degradationReceipt",
  "receipt.table": "tableReceipt", "receipt.imageCrop": "imageCropReceipt",
  "ci.requiredChecks": "ciChecks", "renderer.attestation": "rendererAttestation",
  "evidence.visual": "visualEvidence",
};

const capabilityLabels: Record<string, CapabilityKey> = {
  "shape.gradient": "gradient", "shape.shadow": "shadow",
  "layout.transform": "transformation", "layout.flex": "flex", "layout.grid": "grid",
  "shape.borderRadius": "borderRadius", "shape.opacity": "opacityKorean",
  "shape.clip": "clipping",
};

const receiptLabels: Record<string, CapabilityKey> = {
  cjk: "cjkFonts", chart: "chart", zBand: "zBand", degradation: "degradation",
};

const decisionLabels: Record<AuthoredSemanticDecision, CapabilityKey> = {
  native: "native", representationFallback: "representationFallback",
  identityFailure: "identityFailure", ownershipFailure: "ownershipFailure",
  assetPublicationFailure: "assetPublicationFailure", blocked: "blocked",
};

function decisionClass(decision: AuthoredSemanticDecision): string {
  if (decision === "native") return "text-emerald-800";
  if (decision === "representationFallback") return "text-amber-800";
  return "text-rose-800";
}

export function authoredHybridUnavailableReason(
  status: AuthoredEmissionCapabilityStatus,
  expectedRevision?: number,
  _presentation?: unknown,
  t?: CapabilityTranslator,
): string | undefined {
  // Quality is advisory for original-source export. The capability and immutable
  // manifest checks below remain admission guards for editable Hybrid output.
  if (status.state === "checking") return t?.("checkingProjection");
  if (status.state === "unavailable") return status.reason ?? t?.("projectionUnavailable");
  const { readiness } = status;
  if (readiness.version === "v3" || readiness.version === "v2") {
    const { projection } = readiness;
    if (expectedRevision !== undefined && projection.presentationRevision !== expectedRevision) {
      return t?.("releaseBindingMismatch", { version: readiness.version });
    }
    const activeKillSwitch = projection.killSwitches.find((item) => item.active);
    if (activeKillSwitch) return `${activeKillSwitch.capabilityId}: ${activeKillSwitch.reasonCode}`;
    if (projection.degradedFidelityOnly) return t?.("semanticFidelityOnly");
    if (projection.canonicalMode !== "bundleGraph") return t?.("legacyNoHybrid");
    if (!projection.canonicalDigest || !projection.manifestDigest) return t?.("manifestDigestMissing");
    // `nativeSubsetStatus` is a promotion-claim scope, not an export capability:
    // it names the receipts (CJK, chart, table, z-band, image crop) this bounded
    // release does not certify. It is a `Literal["notApplicable"]` on the server,
    // so treating it as a blocker retired Hybrid from this editor outright and
    // made `authoredV3HybridStatusMessage` -- which states the opposite, that
    // raster-fallback Hybrid exports while the native subset stays unclaimed --
    // unreachable. Hybrid availability is decided by the checks above; the
    // status line below carries the uncertified scope to the user.
    return undefined;
  }
  const projection = readiness.projection;
  if (expectedRevision !== undefined && projection.presentationRevision !== expectedRevision) {
    return t?.("capabilityBindingMismatch");
  }
  if (projection.degradedFidelityOnly) {
    return t?.("sourceFidelityOnly");
  }
  if (projection.canonicalMode !== "bundleGraph") {
    return t?.("legacyDeclaredNoHybrid");
  }
  if (!projection.canonicalDigest || !projection.manifestDigest) {
    return t?.("cannotStartWithoutDigest");
  }
  const activeKillSwitch = projection.killSwitches.find((item) => item.active);
  if (activeKillSwitch) {
    return activeKillSwitch.reason ?? t?.("killSwitchActive", { id: activeKillSwitch.capabilityId });
  }
  const readyKinds = [projection.hybrid.text, projection.hybrid.image, projection.hybrid.shape]
    .filter((capability) => capability.state === "ready");
  if (readyKinds.length === 0) {
    return projection.hybrid.text.reason ?? projection.hybrid.image.reason ?? projection.hybrid.shape.reason ?? t?.("receiptNotReady");
  }
  return undefined;
}

export function authoredV3HybridStatusMessage(projection: AuthoredReleaseReadinessProjectionV3, t?: CapabilityTranslator): string {
  if (
    projection.releaseProfile.promotionReady &&
    projection.exportAuthorityClaim?.kind === "promotion" &&
    projection.exportAuthorityClaim.state === "ready"
  ) {
    return t?.("hybridReady") ?? "Hybrid (raster fallback) export 가능 · native subset은 지원하지 않으며 server promotion receipt가 ready입니다.";
  }
  return t?.("hybridDiagnostic") ?? "Hybrid (raster fallback) 진단 export 가능 · native subset은 지원하지 않으며 promotion not ready입니다.";
}

export default function AuthoredEmissionCapabilityStatusView({
  status,
  expectedRevision,
  t,
}: {
  status: AuthoredEmissionCapabilityStatus;
  expectedRevision?: number;
  t: CapabilityTranslator;
}) {
  if (status.state === "checking") {
    return <p className="text-[11px] leading-4 text-slate-500" data-testid="authored-capability-status">{t("checkingExportCapability")}</p>;
  }
  if (status.state === "unavailable") {
    return <p className="text-[11px] leading-4 text-amber-800" data-testid="authored-capability-status">{t("fidelityOnly", { reason: status.reason ?? t("projectionUnavailable") })}</p>;
  }
  const { readiness } = status;
  if (readiness.version === "v3" || readiness.version === "v2") {
    const { projection } = readiness;
    const hybridReason = authoredHybridUnavailableReason(status, expectedRevision, undefined, t);
    const activeKillSwitches = projection.killSwitches.filter((item) => item.active);
    let hybridStatusMessage: string;
    if (hybridReason) {
      hybridStatusMessage = t("hybridLimited", { reason: hybridReason });
    } else if (readiness.version === "v3") {
      hybridStatusMessage = authoredV3HybridStatusMessage(readiness.projection, t);
    } else {
      hybridStatusMessage = t("hybridDiagnosticBinding", { version: readiness.version });
    }
    return (
      <div className="space-y-1 text-[11px] leading-4" data-testid="authored-capability-status" data-readiness-version={readiness.version}>
        <p className={hybridReason ? "text-amber-800" : "text-emerald-800"}>
          {hybridStatusMessage}
        </p>
        <details className="rounded border border-slate-200 bg-white/70 px-2 py-1" data-testid="authored-release-evidence-details">
          <summary className="cursor-pointer select-none font-medium text-slate-700">
            {readiness.version === "v3"
              ? t("releaseEvidenceV3", { deck: String(readiness.projection.releaseProfile.deckEvidenceReady), serializer: String(readiness.projection.releaseProfile.serializerProfileReady), commit: String(readiness.projection.releaseProfile.releaseCommitReady), promotion: String(readiness.projection.releaseProfile.promotionReady) })
              : t("releaseEvidenceV2", { promotion: String(readiness.projection.releaseProfile.promotionReady), native: String(readiness.projection.releaseProfile.boundedNativeParityReady) })}
          </summary>
          <div className="mt-1 space-y-1">
        <p className="break-all text-slate-500">
          {t("profile", { id: projection.releaseProfile.profileId, platform: projection.releaseProfile.platformScope.platform, renderer: projection.releaseProfile.platformScope.renderer, reason: projection.releaseProfile.reasonCode })}
        </p>
        <p className="break-all text-slate-500">
          {t("bindingClaim", { mode: projection.canonicalMode, canonical: projection.canonicalDigest ?? t("notRecorded"), manifest: projection.manifestDigest ?? t("notRecorded"), projection: projection.releaseProjectionDigest ?? t("notIssued") })}
        </p>
        {readiness.version === "v3" ? (
          <>
            <p className="text-slate-500">
              {t("deckReadiness", { deck: String(readiness.projection.releaseProfile.deckEvidenceReady), serializer: String(readiness.projection.releaseProfile.serializerProfileReady), native: readiness.projection.releaseProfile.nativeSubsetStatus })}
            </p>
            <p className="text-slate-500">
              {t("releaseReadiness", { commit: String(readiness.projection.releaseProfile.releaseCommitReady), promotion: String(readiness.projection.releaseProfile.promotionReady), deployment: String(readiness.projection.releaseProfile.deploymentAuthorized) })}
            </p>
            <p className="text-slate-500">
              {t("deprecatedStatus", { parity: readiness.projection.releaseProfile.fullNativeParityStatus, generation: readiness.projection.releaseProfile.independentVnextGenerationStatus })}
            </p>
          </>
        ) : (
          <p className="text-slate-500">
            {t("v2Readiness", { promotion: String(readiness.projection.releaseProfile.promotionReady), native: String(readiness.projection.releaseProfile.boundedNativeParityReady), generation: String(readiness.projection.releaseProfile.independentGenerationReady) })}
          </p>
        )}
        <p className="break-all text-slate-500">
          {t("exactPins", { profile: projection.releaseProfile.profileDefinitionDigest, claim: projection.exportAuthorityClaim ? `${projection.exportAuthorityClaim.kind}/${projection.exportAuthorityClaim.state}` : t("notIssued"), receipts: projection.releaseAuthorityReceiptDigests.length })}
        </p>
        <p className="text-slate-500">
          {t("authorityClaimNote")}
        </p>
        {readiness.version === "v3" ? (
          <p className="break-all text-slate-500">
            {t("capabilityGroupsV3", { deck: readiness.projection.releaseProfile.deckEvidenceCapabilityIds.join(", "), serializer: readiness.projection.releaseProfile.serializerProfileCapabilityIds.join(", "), unsupported: readiness.projection.releaseProfile.unsupportedNativeCapabilityIds.join(", "), commit: readiness.projection.releaseProfile.releaseCommitCapabilityIds.join(", "), required: readiness.projection.releaseProfile.allRequiredCapabilityIds.join(", ") })}
          </p>
        ) : (
          <p className="break-all text-slate-500">
            {t("capabilityGroupsV2", { promotion: readiness.projection.releaseProfile.promotionCapabilityIds.join(", "), native: readiness.projection.releaseProfile.boundedNativeCapabilityIds.join(", ") })}
          </p>
        )}
        <section className="border-t border-slate-100 pt-1.5" aria-label={t("scopedCapabilitiesAria", { version: readiness.version })}>
          <p className="font-medium text-slate-700">{t("scopedCapabilities")}</p>
          <div className="mt-1 grid gap-x-3 gap-y-1 sm:grid-cols-2">
            {projection.capabilities.map((capability) => (
              <div key={capability.capabilityId} className="min-w-0" data-testid={`authored-release-capability-${capability.capabilityId}`}>
                <span className="text-slate-600">{t(releaseCapabilityLabels[capability.capabilityId] ?? "unknownCapability")}: </span>
                <span className={capability.state === "ready" ? "text-emerald-800" : capability.state === "shadow" ? "text-amber-800" : "text-rose-800"}>{capability.state}</span>
                <span className="text-slate-500"> · {capability.reasonCode} · {capability.scope.platform}/{capability.scope.renderer}</span>
                <span className="sr-only">{t("receiptDigest", { digest: capability.receiptDigest ?? "" })}</span>
              </div>
            ))}
          </div>
        </section>
        <section className="border-t border-slate-100 pt-1.5" aria-label={t("rendererAttestationAria")}>
          <p className="font-medium text-slate-700">{t("rendererAttestation")}</p>
          <p className={projection.rendererAttestation.state === "attested" ? "text-emerald-800" : "text-amber-800"}>
            {projection.rendererAttestation.state} · {projection.rendererAttestation.reasonCode}
            <span className="sr-only">{t("attestationDigest", { digest: projection.rendererAttestation.attestationDigest ?? "" })}</span>
          </p>
        </section>
        <section className="border-t border-slate-100 pt-1.5" aria-label={t("killSwitchesAria")}>
          <p className="font-medium text-slate-700">{t("killSwitches")}</p>
          {activeKillSwitches.length ? activeKillSwitches.map((item) => (
            <p key={item.capabilityId} className="text-rose-800">{t("activeKillSwitch", { id: item.capabilityId, reason: item.reasonCode })}</p>
          )) : <p className="text-slate-500">{t("noActiveKillSwitches")}</p>}
        </section>
          </div>
        </details>
      </div>
    );
  }
  const projection: AuthoredEmissionCapabilityProjection = readiness.projection;
  const hybridReason = authoredHybridUnavailableReason(status, expectedRevision, undefined, t);
  return (
    <div className="space-y-1 text-[11px] leading-4" data-testid="authored-capability-status">
      <p className={hybridReason ? "text-amber-800" : "text-emerald-800"}>
        {hybridReason ? t("fidelityReason", { reason: hybridReason }) : t("sameManifest")}
      </p>
      <p className="break-all text-slate-500">
        {t("revisionManifest", { revision: projection.presentationRevision, manifest: projection.manifestDigest ?? t("notRecorded") })}
      </p>
      <p className="text-slate-500">
        {t("legacyReadiness")}
      </p>
      <section className="border-t border-slate-100 pt-1.5" aria-label={t("semanticCapabilityMatrixAria")}>
        <p className="font-medium text-slate-700">{t("semanticCapabilityMatrix")}</p>
        <div className="mt-1 grid gap-x-3 gap-y-1 sm:grid-cols-2">
          {projection.capabilityMatrix.map((capability) => (
            <div key={capability.capabilityId} className="min-w-0" data-testid={`authored-capability-${capability.capabilityId}`}>
              <span className="text-slate-600">{t(capabilityLabels[capability.capabilityId] ?? "unknownCapability")}: </span>
              <span className={decisionClass(capability.decision)}>{t(decisionLabels[capability.decision])}</span>
              <span className="text-slate-500"> · {capability.state} · {capability.reasonCode}</span>
              <span className="sr-only">{t("receiptDigest", { digest: capability.receiptDigest })}</span>
            </div>
          ))}
        </div>
      </section>
      <section className="border-t border-slate-100 pt-1.5" aria-label={t("semanticReceiptsAria")}>
        <p className="font-medium text-slate-700">{t("semanticReceipts")}</p>
        <div className="mt-1 grid gap-x-3 gap-y-1 sm:grid-cols-2">
          {projection.receipts.map((receipt) => (
            <div key={receipt.receiptId} className="min-w-0" data-testid={`authored-receipt-${receipt.receiptId}`}>
              <span className="text-slate-600">{t(receiptLabels[receipt.receiptId] ?? "unknownCapability")}: </span>
              <span className={decisionClass(receipt.decision)}>{t(decisionLabels[receipt.decision])}</span>
              <span className="text-slate-500"> · {receipt.state} · {receipt.reasonCode}</span>
              <span className="sr-only">{t("receiptDigest", { digest: receipt.receiptDigest })}</span>
            </div>
          ))}
        </div>
      </section>
    </div>
  );
}
