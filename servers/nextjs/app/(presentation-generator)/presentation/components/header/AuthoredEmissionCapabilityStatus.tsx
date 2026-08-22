"use client";

import type { AuthoredEmissionCapabilityProjection } from "../../../services/api/authored-emission-capability";

export type AuthoredEmissionCapabilityStatus =
  | { state: "checking" }
  | { state: "unavailable"; reason?: string }
  | { state: "ready"; projection: AuthoredEmissionCapabilityProjection };

export function authoredHybridUnavailableReason(
  status: AuthoredEmissionCapabilityStatus,
  expectedRevision?: number,
): string | undefined {
  if (status.state === "checking") return "capability projection을 확인하는 중입니다.";
  if (status.state === "unavailable") return status.reason ?? "Hybrid capability projection을 확인하지 못했습니다.";
  const { projection } = status;
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
  const { projection } = status;
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
    </div>
  );
}
