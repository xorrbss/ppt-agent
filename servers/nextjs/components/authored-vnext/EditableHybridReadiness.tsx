"use client";

import { useTranslations } from "next-intl";

import type { EditableHybridMvpReadiness } from "./types";

const STATUS_KEY: Record<EditableHybridMvpReadiness["status"], "notEvaluated" | "shadow" | "ready" | "blocked" | "expired"> = {
  "not-evaluated": "notEvaluated",
  shadow: "shadow",
  ready: "ready",
  blocked: "blocked",
  expired: "expired",
};

export function EditableHybridReadiness({
  readiness,
}: {
  /** The server alone issues this v4 product-readiness projection. */
  readiness?: EditableHybridMvpReadiness | null;
}) {
  const t = useTranslations("a2cHybridChrome");
  if (!readiness) {
    return <span className="rounded-full bg-slate-100 px-2 py-1 text-[10px] font-semibold text-slate-600" data-testid="editable-hybrid-mvp-readiness" title={t("readinessMissingTitle")}>{t("readinessMissing")}</span>;
  }
  const tone = readiness.editableHybridMvpReady && readiness.nativeEditableReady
    ? "bg-emerald-50 text-emerald-800"
    : readiness.status === "blocked" || readiness.status === "expired"
      ? "bg-amber-50 text-amber-900"
      : "bg-slate-100 text-slate-700";
  return <span className={`rounded-full px-2 py-1 text-[10px] font-semibold ${tone}`} data-testid="editable-hybrid-mvp-readiness" title={`${readiness.profileId} · ${readiness.releaseScope} · ${readiness.reasonCode}`}>{t("readinessStatus", { status: t(STATUS_KEY[readiness.status]) })}</span>;
}
