// Pure view derivation for generation-activity rows, shared by the dashboard
// section and tested without React. No runtime imports beyond the model.

import {
  isGenerationStalled,
  lastProgressMs,
  minutesSince,
  type GenerationActivityItem,
} from "./generation-activity-model.ts";

export interface GenerationActivityRowView {
  /** Authored page count and the total when known. */
  pages: { authored: number; total: number | null };
  queued: boolean;
  cancelling: boolean;
  queuePosition: number | null;
  /** Whole minutes since the last observable progress; null when unknown or queued. */
  progressMinutes: number | null;
  stalled: boolean;
  /** Minutes the stall has lasted (same as progressMinutes when stalled). */
  stalledMinutes: number | null;
}

export function describeGenerationActivityItem(
  item: GenerationActivityItem,
  nowMs: number,
): GenerationActivityRowView {
  const queued = item.state === "queued";
  const cancelling = item.state === "cancelling";
  const progressIso = item.lastProgressAt ?? item.startedAt;
  const last = lastProgressMs(item);
  const progressMinutes = queued || cancelling || last === null ? null : minutesSince(progressIso, nowMs);
  const stalled = isGenerationStalled(item, nowMs);
  return {
    pages: { authored: item.pages.authored, total: item.pages.total },
    queued,
    cancelling,
    queuePosition: queued ? item.queuePosition : null,
    progressMinutes,
    stalled,
    stalledMinutes: stalled ? progressMinutes : null,
  };
}

export function presentationHref(presentationId: string): string {
  return `/presentation?id=${encodeURIComponent(presentationId)}`;
}

