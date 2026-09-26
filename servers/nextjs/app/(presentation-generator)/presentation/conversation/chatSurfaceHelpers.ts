import type { ConversationRunAction } from "./types";

export const actionMessageKey: Record<
  ConversationRunAction,
  | "action.retry"
  | "action.cancel"
  | "action.approve"
  | "action.reject"
  | "action.undo"
> = {
  retry: "action.retry",
  cancel: "action.cancel",
  approve: "action.approve",
  reject: "action.reject",
  undo: "action.undo",
};

export const runMessageKey: Record<
  string,
  | "runType.presentationGenerate" | "runType.presentationEdit"
  | "runType.presentationDelete" | "runType.slideEdit"
  | "runType.slideRegenerate" | "runType.slideRegenerateBatch"
  | "runType.slideDelete" | "runType.versionRestore"
  | "runType.templateApply" | "runType.qualityReview"
  | "runType.exportPptx" | "runType.exportPdf"
> = {
  "presentation.generate": "runType.presentationGenerate",
  "presentation.edit": "runType.presentationEdit",
  "presentation.delete": "runType.presentationDelete",
  "slide.edit": "runType.slideEdit",
  "slide.regenerate": "runType.slideRegenerate",
  "slide.regenerate.batch": "runType.slideRegenerateBatch",
  "slide.delete": "runType.slideDelete",
  "version.restore": "runType.versionRestore",
  "template.apply": "runType.templateApply",
  "design.apply": "runType.templateApply",
  "quality.review": "runType.qualityReview",
  "export.pptx": "runType.exportPptx",
  "export.pdf": "runType.exportPdf",
};

export const statusMessageKey: Record<
  string,
  | "status.queued"
  | "status.waiting"
  | "status.running"
  | "status.approval"
  | "status.cancelling"
  | "status.succeeded"
  | "status.failed"
  | "status.cancelled"
> = {
  queued: "status.queued",
  waiting_for_assets: "status.waiting",
  blocked: "status.waiting",
  running: "status.running",
  approval_required: "status.approval",
  awaiting_approval: "status.approval",
  cancelling: "status.cancelling",
  succeeded: "status.succeeded",
  failed: "status.failed",
  cancelled: "status.cancelled",
  dead_letter: "status.failed",
};

export const statusClass: Record<string, string> = {
  queued: "bg-slate-100 text-slate-600",
  waiting_for_assets: "bg-amber-50 text-amber-700",
  blocked: "bg-amber-50 text-amber-700",
  running: "bg-blue-50 text-blue-700",
  approval_required: "bg-amber-50 text-amber-700",
  awaiting_approval: "bg-amber-50 text-amber-700",
  cancelling: "bg-slate-100 text-slate-600",
  succeeded: "bg-emerald-50 text-emerald-700",
  failed: "bg-red-50 text-red-700",
  cancelled: "bg-slate-100 text-slate-600",
  dead_letter: "bg-red-50 text-red-700",
};

export function formatBytes(size?: number): string | undefined {
  if (size === undefined || !Number.isFinite(size) || size < 0) {
    return undefined;
  }
  if (size < 1_024) return `${size} B`;
  if (size < 1_048_576) return `${(size / 1_024).toFixed(1)} KB`;
  if (size < 1_073_741_824) return `${(size / 1_048_576).toFixed(1)} MB`;
  return `${(size / 1_073_741_824).toFixed(1)} GB`;
}

export function safeAssetUrl(value?: string): string | undefined {
  if (!value || value.startsWith("//")) return undefined;
  try {
    const parsed = new URL(value, "https://workspace.local");
    return parsed.protocol === "http:" || parsed.protocol === "https:"
      ? value
      : undefined;
  } catch {
    return undefined;
  }
}

