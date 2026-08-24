"use client";

import type { EditableHybridMvpReadiness } from "./types";

const STATUS_LABEL: Record<EditableHybridMvpReadiness["status"], string> = {
  "not-evaluated": "평가 전",
  shadow: "Shadow",
  ready: "준비됨",
  blocked: "차단됨",
  expired: "만료됨",
};

export function EditableHybridReadiness({
  readiness,
}: {
  /** The server alone issues this v4 product-readiness projection. */
  readiness?: EditableHybridMvpReadiness | null;
}) {
  if (!readiness) {
    return <span className="rounded-full bg-slate-100 px-2 py-1 text-[10px] font-semibold text-slate-600" data-testid="editable-hybrid-mvp-readiness" title="서버 v4 readiness projection을 아직 받지 못했습니다.">편집 가능한 Hybrid MVP · 상태 미수신</span>;
  }
  const tone = readiness.editableHybridMvpReady && readiness.nativeEditableReady
    ? "bg-emerald-50 text-emerald-800"
    : readiness.status === "blocked" || readiness.status === "expired"
      ? "bg-amber-50 text-amber-900"
      : "bg-slate-100 text-slate-700";
  return <span className={`rounded-full px-2 py-1 text-[10px] font-semibold ${tone}`} data-testid="editable-hybrid-mvp-readiness" title={`${readiness.profileId} · ${readiness.releaseScope} · ${readiness.reasonCode}`}>편집 가능한 Hybrid MVP · {STATUS_LABEL[readiness.status]}</span>;
}
