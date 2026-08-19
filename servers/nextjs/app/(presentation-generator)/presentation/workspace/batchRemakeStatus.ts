export const BATCH_REMAKE_PREVIEW_KIND = "authored_batch_remake_preview";

export type BatchRemakeSlideStatus = "pending" | "previewed" | "failed";

export interface BatchRemakeSlide {
  slideIndex: number;
  status: BatchRemakeSlideStatus;
  previewTaskId: string;
  candidateImage?: string;
  warning: boolean;
  reasonCode?: string;
}

export interface BatchRemakePayload {
  phase: "previewing" | "preview_ready";
  presentationId?: string;
  total: number;
  completed: number;
  slides: BatchRemakeSlide[];
}

export interface BatchRemakeReadyPreview {
  slide_index: number;
  preview_task_id: string;
}

type UnknownRecord = Record<string, unknown>;

function record(value: unknown): UnknownRecord | null {
  return typeof value === "object" && value !== null && !Array.isArray(value)
    ? (value as UnknownRecord)
    : null;
}

function slideIndex(value: unknown): number | null {
  return typeof value === "number" && Number.isInteger(value) && value >= 0
    ? value
    : null;
}

function counter(value: unknown): number {
  return typeof value === "number" && Number.isInteger(value) && value >= 0
    ? value
    : 0;
}

function text(value: unknown): string | undefined {
  return typeof value === "string" && value.trim() ? value : undefined;
}

function decodeSlide(value: unknown): BatchRemakeSlide | null {
  const source = record(value);
  if (!source) return null;
  const index = slideIndex(source.slide_index);
  const previewTaskId = text(source.preview_task_id);
  if (index === null || !previewTaskId) return null;

  const rawStatus = source.status;
  // An unrecognised status must not be presented as an approvable candidate.
  const status: BatchRemakeSlideStatus =
    rawStatus === "previewed" || rawStatus === "failed" ? rawStatus : "pending";
  const candidateImage = text(source.candidate_image);
  if (status === "previewed" && !candidateImage) return null;

  return {
    slideIndex: index,
    status,
    previewTaskId,
    candidateImage,
    warning: source.warning === true,
    reasonCode: text(source.reason_code),
  };
}

/**
 * Decodes the parent fan-out task payload. An undecodable envelope returns
 * null so the dialog keeps waiting rather than offering nothing to approve.
 */
export function decodeBatchRemakePayload(
  data: unknown,
): BatchRemakePayload | null {
  const source = record(data);
  if (!source || source.kind !== BATCH_REMAKE_PREVIEW_KIND) return null;

  const slides = (Array.isArray(source.slides) ? source.slides : [])
    .map(decodeSlide)
    .filter((slide): slide is BatchRemakeSlide => slide !== null)
    .sort((left, right) => left.slideIndex - right.slideIndex);

  return {
    phase: source.phase === "preview_ready" ? "preview_ready" : "previewing",
    presentationId: text(source.presentation_id),
    total: counter(source.total) || slides.length,
    completed: counter(source.completed),
    slides,
  };
}

/** Only a candidate the user could actually see may be submitted for publish. */
export function readyPreviews(
  payload: BatchRemakePayload | null,
): BatchRemakeReadyPreview[] {
  if (!payload) return [];
  const seen = new Set<number>();
  const previews: BatchRemakeReadyPreview[] = [];
  for (const slide of payload.slides) {
    if (slide.status !== "previewed" || seen.has(slide.slideIndex)) continue;
    seen.add(slide.slideIndex);
    previews.push({
      slide_index: slide.slideIndex,
      preview_task_id: slide.previewTaskId,
    });
  }
  return previews;
}

export function batchRemakeIsComplete(
  payload: BatchRemakePayload | null,
): boolean {
  if (!payload) return false;
  return payload.phase === "preview_ready" || payload.completed >= payload.total;
}

/**
 * How long the parent fan-out may sit at the same completed count before the
 * batch is presumed dead.
 *
 * A cancelled fan-out — a shutdown mid-batch, a killed worker — leaves the
 * parent task at `processing` forever: the per-slide `finally` that records an
 * outcome is itself awaited, so cancellation skips it, and nothing sweeps the
 * row afterwards. Without this the dialog sits on a frozen count until its
 * whole 15-minute deadline expires and then reports a plain timeout, discarding
 * candidates that were already finished. Four minutes is comfortably longer
 * than one slide takes (~45s) even when three siblings hold the concurrency
 * slots ahead of it.
 */
export const BATCH_REMAKE_STALL_MS = 4 * 60 * 1_000;

/**
 * Whether the batch has stopped advancing. `lastProgressAt` is when `completed`
 * last increased (or when polling began, if it never has).
 */
export function batchRemakeIsStalled(
  payload: BatchRemakePayload | null,
  { lastProgressAt, now }: { lastProgressAt: number; now: number },
): boolean {
  if (batchRemakeIsComplete(payload)) return false;
  return now - lastProgressAt >= BATCH_REMAKE_STALL_MS;
}
