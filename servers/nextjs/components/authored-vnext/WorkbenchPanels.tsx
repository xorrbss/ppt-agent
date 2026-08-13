"use client";

import type { ReactNode } from "react";
import { useTranslations } from "next-intl";

import type {
  WorkbenchEntityId,
  WorkbenchPanelData,
  WorkbenchPanelKind,
} from "./types";

export interface WorkbenchPanelsProps {
  activePanel: WorkbenchPanelKind;
  data: WorkbenchPanelData;
  onPanelChange: (panel: WorkbenchPanelKind) => void;
  onSelectQualityIssue?: (elementId: WorkbenchEntityId) => void;
}

const OPERATION_TONE = {
  applied: "bg-emerald-100 text-emerald-800",
  conflict: "bg-amber-100 text-amber-800",
  failed: "bg-red-100 text-red-800",
  queued: "bg-slate-100 text-slate-700",
  sending: "bg-blue-100 text-blue-800",
} as const;

export function WorkbenchPanels({
  activePanel,
  data,
  onPanelChange,
  onSelectQualityIssue,
}: WorkbenchPanelsProps) {
  const t = useTranslations("authoredVnext.workbench.panels");
  return (
    <section aria-label={t("aria")} className="flex min-h-0 flex-1 flex-col bg-white">
      <div aria-label={t("tablistAria")} className="grid grid-cols-4 border-b border-slate-200" role="tablist">
        {(["history", "operations", "quality", "sources"] as WorkbenchPanelKind[]).map((panel) => (
          <button
            aria-controls={`workbench-panel-${panel}`}
            aria-selected={activePanel === panel}
            className={`border-b-2 px-1 py-2.5 text-[11px] font-semibold ${activePanel === panel ? "border-blue-600 text-blue-700" : "border-transparent text-slate-500 hover:text-slate-800"}`}
            id={`workbench-tab-${panel}`}
            key={panel}
            onClick={() => onPanelChange(panel)}
            role="tab"
            type="button"
          >
            {t(`tabs.${panel}`)}
          </button>
        ))}
      </div>
      <div
        aria-labelledby={`workbench-tab-${activePanel}`}
        className="min-h-0 flex-1 overflow-y-auto p-3"
        id={`workbench-panel-${activePanel}`}
        role="tabpanel"
        tabIndex={0}
      >
        {activePanel === "sources" && (
          <PanelList empty={t("sourcesEmpty")}>
            {data.sources.map((item) => (
              <li className="rounded-lg border border-slate-200 p-3" key={item.id}>
                <p className="text-sm font-medium text-slate-900">{item.title}</p>
                <p className="mt-1 text-xs text-slate-600">{item.detail}</p>
                {item.href && <a className="mt-2 inline-block text-xs font-semibold text-blue-700 underline-offset-2 hover:underline" href={item.href} rel="noreferrer" target="_blank">{t("openSource")}</a>}
              </li>
            ))}
          </PanelList>
        )}
        {activePanel === "history" && (
          <PanelList empty={t("historyEmpty")}>
            {data.history.map((item) => (
              <li className="relative border-l-2 border-slate-200 py-1 pl-3" key={item.id}>
                <span aria-hidden="true" className={`absolute -left-[5px] top-2 h-2 w-2 rounded-full ${item.current ? "bg-blue-600" : "bg-slate-300"}`} />
                <div className="flex justify-between gap-2">
                  <p className="text-sm font-medium text-slate-900">{item.title}</p>
                  <time className="shrink-0 text-[10px] text-slate-500">{item.timestampLabel}</time>
                </div>
                <p className="mt-1 text-xs text-slate-600">{item.detail}</p>
              </li>
            ))}
          </PanelList>
        )}
        {activePanel === "quality" && (
          <PanelList empty={t("qualityEmpty")}>
            {data.quality.map((item) => (
              <li className={`rounded-lg border p-3 ${item.severity === "error" ? "border-red-200 bg-red-50" : item.severity === "warning" ? "border-amber-200 bg-amber-50" : "border-blue-200 bg-blue-50"}`} key={item.id}>
                <p className="text-sm font-medium text-slate-900">{item.title}</p>
                <p className="mt-1 text-xs text-slate-700">{item.detail}</p>
                {item.elementId && onSelectQualityIssue && (
                  <button className="mt-2 text-xs font-semibold text-blue-700 underline-offset-2 hover:underline" onClick={() => item.elementId && onSelectQualityIssue(item.elementId)} type="button">{t("viewOnCanvas")}</button>
                )}
              </li>
            ))}
          </PanelList>
        )}
        {activePanel === "operations" && (
          <PanelList empty={t("operationsEmpty")}>
            {data.operations.map((item) => (
              <li className="rounded-lg border border-slate-200 p-3" key={item.id}>
                <div className="flex items-start justify-between gap-2">
                  <div>
                    <p className="text-sm font-medium text-slate-900">{item.label}</p>
                    <p className="mt-0.5 font-mono text-[10px] text-slate-500">{item.kind}</p>
                  </div>
                  <span className={`rounded-full px-2 py-1 text-[10px] font-semibold ${OPERATION_TONE[item.status]}`}>{t(`status.${item.status}`)}</span>
                </div>
                {item.detail && <p className="mt-2 text-xs text-slate-600">{item.detail}</p>}
              </li>
            ))}
          </PanelList>
        )}
      </div>
    </section>
  );
}

function PanelList({ children, empty }: { children: readonly ReactNode[]; empty: string }) {
  if (children.length === 0) return <p className="rounded-lg border border-dashed border-slate-300 p-4 text-center text-sm text-slate-500">{empty}</p>;
  return <ul className="space-y-2">{children}</ul>;
}

export interface PatchHistoryControlsProps {
  canUndo: boolean;
  canRedo: boolean;
  pendingOperationCount: number;
  onUndo: () => void;
  onRedo: () => void;
}

export function PatchHistoryControls({ canUndo, canRedo, pendingOperationCount, onUndo, onRedo }: PatchHistoryControlsProps) {
  const t = useTranslations("authoredVnext.workbench.history");
  return (
    <div aria-label={t("aria")} className="flex items-center gap-2">
      <button aria-label={t("undoAria")} className="rounded-md border border-slate-300 px-3 py-1.5 text-xs font-semibold hover:bg-slate-50 disabled:opacity-40" data-testid="authored-undo" disabled={!canUndo} onClick={onUndo} type="button">{t("undo")}</button>
      <button aria-label={t("redoAria")} className="rounded-md border border-slate-300 px-3 py-1.5 text-xs font-semibold hover:bg-slate-50 disabled:opacity-40" data-testid="authored-redo" disabled={!canRedo} onClick={onRedo} type="button">{t("redo")}</button>
      <span aria-live="polite" className="rounded-full bg-slate-100 px-2 py-1 text-[10px] font-semibold text-slate-600">{t("outbox", { count: pendingOperationCount })}</span>
    </div>
  );
}
