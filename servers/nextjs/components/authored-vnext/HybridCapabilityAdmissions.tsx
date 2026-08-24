"use client";

import type { ActiveSlideScene, HybridRenderOwnership, NativeCapabilityProjectionV1 } from "./types";

const DECISION_LABEL: Record<HybridRenderOwnership["decision"], string> = {
  nativeEditable: "Native",
  rasterIsland: "Raster island",
  publicationBlocked: "Publication blocked",
};

const DECISION_TONE: Record<HybridRenderOwnership["decision"], string> = {
  nativeEditable: "bg-emerald-50 text-emerald-900 ring-emerald-200",
  rasterIsland: "bg-amber-50 text-amber-950 ring-amber-200",
  publicationBlocked: "bg-rose-50 text-rose-950 ring-rose-200",
};

/**
 * Product-facing view of the sealed Scene Hybrid plan.  A missing receipt is
 * intentionally represented as unknown rather than guessed from canvas
 * preview support; it is not a native readiness signal.
 */
export function HybridCapabilityAdmissions({
  scene,
  projection,
}: {
  scene: ActiveSlideScene | null;
  projection?: NativeCapabilityProjectionV1 | null;
}) {
  if (!scene) return null;
  const entries = scene.bodyElements.map((element) => ({
    id: element.id,
    label: element.label,
    ownership: element.renderOwnership,
  }));
  const sealed = entries.filter((entry) => entry.ownership);
  const nativeCount = sealed.filter((entry) => entry.ownership?.decision === "nativeEditable").length;
  const rasterCount = sealed.filter((entry) => entry.ownership?.decision === "rasterIsland").length;
  const blockedCount = sealed.filter((entry) => entry.ownership?.decision === "publicationBlocked").length;
  const evaluatedCapabilities = projection?.capabilities.filter((entry) => entry.status !== "notEvaluated") ?? [];

  return (
    <section aria-label="Hybrid capability admissions" className="border-b border-slate-200 bg-slate-50 px-4 py-2" data-testid="authored-hybrid-capability-admissions">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-[11px] text-slate-700">
        <span className="font-semibold text-slate-900">Hybrid ownership receipt</span>
        {sealed.length === 0 ? (
          <span data-testid="authored-hybrid-admission-unsealed">서버 admission receipt 미수신 · 이 화면은 native readiness를 추정하지 않습니다.</span>
        ) : (
          <>
            <span data-testid="authored-hybrid-native-count">Native {nativeCount}</span>
            <span data-testid="authored-hybrid-raster-count">Raster island {rasterCount}</span>
            <span data-testid="authored-hybrid-blocked-count">Blocked {blockedCount}</span>
          </>
        )}
      </div>
      {sealed.length > 0 && (
        <ul className="mt-1 flex max-h-20 flex-wrap gap-1 overflow-y-auto" data-testid="authored-hybrid-admission-qids">
          {entries.map((entry) => {
            const ownership = entry.ownership;
            if (!ownership) {
              return <li className="rounded px-1.5 py-0.5 text-[10px] text-slate-500" data-qid={entry.id} key={entry.id}>{entry.id} · receipt missing</li>;
            }
            return (
              <li className={`rounded px-1.5 py-0.5 text-[10px] ring-1 ${DECISION_TONE[ownership.decision]}`} data-owner={ownership.decision} data-qid={entry.id} key={entry.id} title={`${entry.label} · ${ownership.capabilityId}${ownership.reasonCode ? ` · ${ownership.reasonCode}` : ""}`}>
                {entry.id} · {DECISION_LABEL[ownership.decision]} · {ownership.capabilityId}
                {ownership.rasterIslandId ? ` · ${ownership.rasterIslandId}` : ""}
                {ownership.reasonCode ? ` · ${ownership.reasonCode}` : ""}
              </li>
            );
          })}
        </ul>
      )}
      {evaluatedCapabilities.length > 0 && (
        <ul className="mt-1 flex max-h-20 flex-wrap gap-1 overflow-y-auto" data-testid="authored-native-capability-readiness">
          {evaluatedCapabilities.map((capability) => (
            <li
              className={`rounded px-1.5 py-0.5 text-[10px] ring-1 ${capability.ready ? DECISION_TONE.nativeEditable : capability.status === "blocked" ? DECISION_TONE.publicationBlocked : DECISION_TONE.rasterIsland}`}
              data-capability-id={capability.capabilityId}
              data-capability-status={capability.status}
              key={capability.capabilityId}
              title={`${capability.reasonCode} · native ${capability.nativeOwnerCount} · raster ${capability.rasterOwnerCount} · blocked ${capability.blockedOwnerCount}`}
            >
              {capability.capabilityId} · {capability.status}
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
