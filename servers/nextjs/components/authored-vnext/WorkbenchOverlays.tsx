"use client";

import type { ReactNode } from "react";
import { useTranslations } from "next-intl";

import type {
  CasConflict,
  RegenerationCandidate,
  WorkbenchEntityId,
  WorkbenchStateDescriptor,
} from "./types";

export interface CasConflictBannerProps {
  conflict: CasConflict;
  onRebase: (operationId: WorkbenchEntityId) => void;
  onDismiss: (operationId: WorkbenchEntityId) => void;
}

export function CasConflictBanner({ conflict, onRebase, onDismiss }: CasConflictBannerProps) {
  const t = useTranslations("authoredVnext.workbench.conflict");
  return (
    <section aria-labelledby="cas-conflict-title" className="border-b border-amber-300 bg-amber-50 px-4 py-3 text-amber-950" role="alert">
      <div className="flex flex-wrap items-center gap-3">
        <div className="min-w-0 flex-1">
          <h2 className="text-sm font-semibold" id="cas-conflict-title">{t("title")}</h2>
          <p className="mt-0.5 text-xs">{t("detail", { message: conflict.message, expected: conflict.expectedRevision, actual: conflict.actualRevision })}</p>
        </div>
        <button className="rounded-md border border-amber-400 bg-white px-3 py-1.5 text-xs font-semibold hover:bg-amber-100 disabled:opacity-40" data-testid="authored-conflict-rebase" disabled={!conflict.canRebase} onClick={() => onRebase(conflict.operationId)} type="button">{t("rebase")}</button>
        <button className="rounded-md px-3 py-1.5 text-xs font-semibold hover:bg-amber-100" data-testid="authored-conflict-discard" onClick={() => onDismiss(conflict.operationId)} type="button">{t("discard")}</button>
      </div>
    </section>
  );
}

export interface RegenerationReviewProps {
  candidate: RegenerationCandidate;
  disabled?: boolean;
  onAccept: (candidateId: WorkbenchEntityId) => void;
  onReject: (candidateId: WorkbenchEntityId) => void;
}

export function RegenerationReview({ candidate, disabled = false, onAccept, onReject }: RegenerationReviewProps) {
  const t = useTranslations("authoredVnext.workbench.regeneration");
  return (
    <section aria-labelledby="regeneration-review-title" className="border-t border-violet-200 bg-violet-50 p-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h2 className="text-sm font-semibold text-violet-950" id="regeneration-review-title">{t("title", { label: candidate.label })}</h2>
          <p className="mt-1 text-xs text-violet-800">{t("detail", { summary: candidate.summary, revision: candidate.basedOnRevision })}</p>
        </div>
        <div className="flex gap-2">
          <button className="rounded-md border border-violet-300 bg-white px-3 py-1.5 text-xs font-semibold text-violet-900 hover:bg-violet-100 disabled:opacity-40" data-testid="authored-regeneration-reject" disabled={disabled} onClick={() => onReject(candidate.id)} type="button">{t("reject")}</button>
          <button className="rounded-md bg-violet-700 px-3 py-1.5 text-xs font-semibold text-white hover:bg-violet-800 disabled:opacity-40" data-testid="authored-regeneration-accept" disabled={disabled} onClick={() => onAccept(candidate.id)} type="button">{t("accept")}</button>
        </div>
      </div>
      <div className="mt-3 grid gap-3 md:grid-cols-2">
        <PreviewCard label={t("current")}>{candidate.before}</PreviewCard>
        <PreviewCard label={t("candidate")}>{candidate.candidate}</PreviewCard>
      </div>
      <p className="mt-2 text-[11px] text-violet-700">{t("lockedExcluded")}</p>
    </section>
  );
}

function PreviewCard({ label, children }: { label: string; children: ReactNode }) {
  return <figure className="overflow-hidden rounded-lg border border-violet-200 bg-white"><figcaption className="border-b border-violet-100 px-3 py-1.5 text-xs font-semibold text-violet-900">{label}</figcaption><div className="aspect-video overflow-hidden p-2">{children}</div></figure>;
}

export interface WorkbenchStateBoundaryProps {
  descriptor: WorkbenchStateDescriptor;
  children: ReactNode;
  onRetry?: () => void;
  onUseLastKnownGood?: () => void;
}

export function WorkbenchStateBoundary({ descriptor, children, onRetry, onUseLastKnownGood }: WorkbenchStateBoundaryProps) {
  const t = useTranslations("authoredVnext.workbench.state");
  if (descriptor.state === "loading") {
    return <StateCard busy title={descriptor.title ?? t("loadingTitle")} message={descriptor.message ?? t("loadingMessage")} />;
  }
  if (descriptor.state === "error") {
    return <StateCard action={onRetry ? { label: t("retry"), run: onRetry } : undefined} title={descriptor.title ?? t("errorTitle")} message={descriptor.message ?? t("errorMessage")} tone="error" />;
  }
  if (descriptor.state === "empty") {
    return <StateCard title={descriptor.title ?? t("emptyTitle")} message={descriptor.message ?? t("emptyMessage")} />;
  }
  return (
    <div className="flex min-h-0 flex-1 flex-col">
      {(descriptor.state === "stale" || descriptor.state === "last-known-good") && (
        <div className={`flex flex-wrap items-center gap-2 border-b px-4 py-2 text-xs ${descriptor.state === "stale" ? "border-amber-200 bg-amber-50 text-amber-900" : "border-blue-200 bg-blue-50 text-blue-900"}`} role="status">
          <strong>{descriptor.state === "stale" ? t("staleTitle") : t("lkgTitle")}</strong>
          <span>{descriptor.message ?? t("safeSnapshot")}</span>
          {descriptor.lastUpdatedLabel && <span className="text-current/70">{t("updated", { label: descriptor.lastUpdatedLabel })}</span>}
          {onRetry && <button className="ml-auto rounded border border-current/30 bg-white px-2 py-1 font-semibold" onClick={onRetry} type="button">{t("refresh")}</button>}
          {descriptor.state === "stale" && onUseLastKnownGood && <button className="rounded border border-current/30 bg-white px-2 py-1 font-semibold" onClick={onUseLastKnownGood} type="button">{t("viewLkg")}</button>}
        </div>
      )}
      {children}
    </div>
  );
}

function StateCard({ title, message, tone = "neutral", busy = false, action }: { title: string; message: string; tone?: "neutral" | "error"; busy?: boolean; action?: { label: string; run: () => void } }) {
  return (
    <section aria-busy={busy} className="grid min-h-[28rem] flex-1 place-items-center bg-slate-50 p-6" role={tone === "error" ? "alert" : "status"}>
      <div className={`max-w-md rounded-xl border bg-white p-6 text-center shadow-sm ${tone === "error" ? "border-red-200" : "border-slate-200"}`}>
        {busy && <span aria-hidden="true" className="mx-auto mb-3 block h-7 w-7 animate-spin rounded-full border-2 border-slate-300 border-t-blue-600" />}
        <h2 className={`font-semibold ${tone === "error" ? "text-red-800" : "text-slate-900"}`}>{title}</h2>
        <p className="mt-2 text-sm text-slate-600">{message}</p>
        {action && <button className="mt-4 rounded-md bg-slate-900 px-4 py-2 text-sm font-semibold text-white" onClick={action.run} type="button">{action.label}</button>}
      </div>
    </section>
  );
}
