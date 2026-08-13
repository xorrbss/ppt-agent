"use client";

import type { ReactNode } from "react";
import { useTranslations } from "next-intl";

import { LayeredSlideCanvas } from "./LayeredSlideCanvas";
import { TypedInspector } from "./TypedInspector";
import { VirtualSlideRail } from "./VirtualSlideRail";
import { PatchHistoryControls, WorkbenchPanels } from "./WorkbenchPanels";
import {
  CasConflictBanner,
  RegenerationReview,
  WorkbenchStateBoundary,
} from "./WorkbenchOverlays";
import type {
  ActiveSlideScene,
  AuthoredWorkbenchCallbacks,
  CasConflict,
  InspectorModel,
  RegenerationCandidate,
  SlideRailItem,
  VirtualRailWindow,
  WorkbenchEntityId,
  WorkbenchPanelData,
  WorkbenchPanelKind,
  WorkbenchStateDescriptor,
} from "./types";

export interface AuthoredWorkbenchProps {
  featureEnabled: boolean;
  fallback?: ReactNode;
  state: WorkbenchStateDescriptor;
  slides: readonly SlideRailItem[];
  activeSlideId: WorkbenchEntityId | null;
  railWindow: VirtualRailWindow;
  scene: ActiveSlideScene | null;
  selectedElementId: WorkbenchEntityId | null;
  inspector: InspectorModel;
  activePanel: WorkbenchPanelKind;
  panels: WorkbenchPanelData;
  regenerationCandidate?: RegenerationCandidate | null;
  conflict?: CasConflict | null;
  canUndo: boolean;
  canRedo: boolean;
  readOnly?: boolean;
  createOperationId: () => WorkbenchEntityId;
  callbacks: AuthoredWorkbenchCallbacks;
  onPanelChange: (panel: WorkbenchPanelKind) => void;
  onRailWindowChange?: (startIndex: number) => void;
}

export function AuthoredWorkbench({
  featureEnabled,
  fallback = null,
  state,
  slides,
  activeSlideId,
  railWindow,
  scene,
  selectedElementId,
  inspector,
  activePanel,
  panels,
  regenerationCandidate,
  conflict,
  canUndo,
  canRedo,
  readOnly = false,
  createOperationId,
  callbacks,
  onPanelChange,
  onRailWindowChange,
}: AuthoredWorkbenchProps) {
  const t = useTranslations("authoredVnext.workbench");
  if (!featureEnabled) return <>{fallback}</>;

  const pendingOperationCount = panels.operations.filter((operation) =>
    operation.status === "queued" || operation.status === "sending",
  ).length;
  const surfaceState = scene
    ? state
    : state.state === "ready"
      ? {
          state: "empty" as const,
          title: t("empty.title"),
          message: t("empty.message"),
        }
      : state;
  const mutationDisabled = readOnly || state.state === "stale" || state.state === "last-known-good";

  return (
    <main className="flex h-[calc(100vh-4rem)] min-h-[40rem] flex-col overflow-hidden bg-slate-100 text-slate-950" data-testid="authored-workbench">
      <header className="flex flex-wrap items-center gap-3 border-b border-slate-200 bg-white px-4 py-2.5">
        <div className="min-w-0 flex-1">
          <p className="text-[10px] font-semibold uppercase tracking-[0.18em] text-blue-700">{t("eyebrow")}</p>
          <h1 className="truncate text-sm font-semibold">{t("title")}</h1>
        </div>
        <span className="rounded-full bg-emerald-50 px-2 py-1 text-[10px] font-semibold text-emerald-800">{t("patchOnly")}</span>
        <PatchHistoryControls canRedo={canRedo && !mutationDisabled} canUndo={canUndo && !mutationDisabled} onRedo={callbacks.onRedo} onUndo={callbacks.onUndo} pendingOperationCount={pendingOperationCount} />
        {callbacks.onRequestExport && (
          <button className="rounded-md bg-slate-900 px-3 py-1.5 text-xs font-semibold text-white hover:bg-slate-800" data-testid="authored-export" onClick={callbacks.onRequestExport} type="button">{t("export")}</button>
        )}
      </header>
      {conflict && <CasConflictBanner conflict={conflict} onDismiss={callbacks.onDismissConflict} onRebase={callbacks.onRebaseConflict} />}
      <WorkbenchStateBoundary descriptor={surfaceState} onRetry={callbacks.onRetry} onUseLastKnownGood={callbacks.onUseLastKnownGood}>
        {scene && (
          <div className="grid min-h-0 flex-1 grid-cols-[15rem_minmax(0,1fr)_20rem]">
            <VirtualSlideRail activeSlideId={activeSlideId} disabled={false} onReorder={callbacks.onReorderSlide} onSelect={callbacks.onSelectSlide} onWindowChange={onRailWindowChange} slides={slides} window={railWindow} />
            <div className="flex min-h-0 flex-col border-r border-slate-200">
              <LayeredSlideCanvas createOperationId={createOperationId} disabled={mutationDisabled} onCommand={callbacks.onCommand} onSelectElement={callbacks.onSelectElement} scene={scene} selectedElementId={selectedElementId} />
              {regenerationCandidate && <RegenerationReview candidate={regenerationCandidate} disabled={mutationDisabled} onAccept={callbacks.onAcceptRegeneration} onReject={callbacks.onRejectRegeneration} />}
            </div>
            <aside className="flex min-h-0 flex-col">
              <TypedInspector createOperationId={createOperationId} disabled={mutationDisabled} model={inspector} onCommand={callbacks.onCommand} />
              <WorkbenchPanels activePanel={activePanel} data={panels} onPanelChange={onPanelChange} onSelectQualityIssue={callbacks.onSelectElement} />
            </aside>
          </div>
        )}
      </WorkbenchStateBoundary>
    </main>
  );
}
