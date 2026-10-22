// Pure model for the owner's in-flight generation operations. No runtime
// imports so node:test can load it directly.

/** A running generation with no completed page for this long is shown as stalled. */
export const GENERATION_STALL_THRESHOLD_MS = 10 * 60 * 1000;

/** `cancelling` still holds the owner's slot until the worker stops. */
export type GenerationActivityState = "running" | "cancelling" | "queued";

export interface GenerationActivityPages {
  authored: number;
  rendered: number;
  persisted: number;
  total: number | null;
}

export interface GenerationActivityItem {
  operationId: string;
  presentationId: string;
  state: GenerationActivityState;
  presentationTitle: string | null;
  sourceFileName: string | null;
  pages: GenerationActivityPages;
  stage: string | null;
  queuePosition: number | null;
  capacityWaiting: boolean;
  createdAt: string;
  startedAt: string | null;
  lastProgressAt: string | null;
  operationVersion: number;
  presentationRevision: number;
  /** Set on a resume; identifies the failed operation it continues. */
  retryOfOperationId: string | null;
}

export interface GenerationActivitySnapshot {
  items: GenerationActivityItem[];
  /** Server clock at response time, used to compute ages without client skew. */
  serverTime: string;
  /** Client epoch ms when the response arrived. */
  receivedAt: number;
}

function record(value: unknown): Record<string, unknown> | null {
  return value && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : null;
}

function text(value: unknown): string | null {
  return typeof value === "string" && value.trim() ? value : null;
}

function count(value: unknown): number | null {
  return typeof value === "number" && Number.isInteger(value) && value >= 0 ? value : null;
}

function timestamp(value: unknown): string | null {
  const raw = text(value);
  return raw && Number.isFinite(Date.parse(raw)) ? raw : null;
}

function normalizeItem(value: unknown): GenerationActivityItem | null {
  const raw = record(value);
  if (!raw) return null;
  const operationId = text(raw.operation_id);
  const presentationId = text(raw.presentation_id);
  const state = raw.state === "running" || raw.state === "cancelling" || raw.state === "queued"
    ? raw.state : null;
  const createdAt = timestamp(raw.created_at);
  const operationVersion = count(raw.operation_version);
  const presentationRevision = count(raw.presentation_revision);
  if (!operationId || !presentationId || !state || !createdAt
      || operationVersion === null || presentationRevision === null) {
    return null;
  }
  const pages = record(raw.pages) ?? {};
  const queuePosition = count(raw.queue_position);
  return {
    operationId,
    presentationId,
    state,
    presentationTitle: text(raw.presentation_title),
    sourceFileName: text(raw.source_file_name),
    pages: {
      authored: count(pages.authored) ?? 0,
      rendered: count(pages.rendered) ?? 0,
      persisted: count(pages.persisted) ?? 0,
      total: count(pages.total),
    },
    stage: text(raw.stage),
    queuePosition: queuePosition !== null && queuePosition >= 1 ? queuePosition : null,
    capacityWaiting: raw.capacity_waiting === true,
    createdAt,
    startedAt: timestamp(raw.started_at),
    lastProgressAt: timestamp(raw.last_progress_at),
    operationVersion,
    presentationRevision,
    retryOfOperationId: text(raw.retry_of_operation_id),
  };
}

/** Accepts the bare body or a `{data: ...}` envelope; drops malformed items. */
export function normalizeGenerationActivity(
  payload: unknown,
  receivedAt: number = Date.now(),
): GenerationActivitySnapshot {
  const outer = record(payload) ?? {};
  const body = record(outer.data) ?? outer;
  const items = Array.isArray(body.items)
    ? body.items.map(normalizeItem).filter((item): item is GenerationActivityItem => item !== null)
    : [];
  const serverTime = timestamp(body.server_time) ?? new Date(receivedAt).toISOString();
  return { items, serverTime, receivedAt };
}

/** Server-clock estimate of "now" for a snapshot. */
export function serverNowMs(
  snapshot: Pick<GenerationActivitySnapshot, "serverTime" | "receivedAt">,
  clientNow: number = Date.now(),
): number {
  return Date.parse(snapshot.serverTime) + Math.max(0, clientNow - snapshot.receivedAt);
}

/** Last observable progress: a completed page, else the operation start. */
export function lastProgressMs(
  item: Pick<GenerationActivityItem, "lastProgressAt" | "startedAt">,
): number | null {
  const raw = item.lastProgressAt ?? item.startedAt;
  if (!raw) return null;
  const parsed = Date.parse(raw);
  return Number.isFinite(parsed) ? parsed : null;
}

/** Whole minutes elapsed since `iso`, never negative; null when unknown. */
export function minutesSince(iso: string | null, nowMs: number): number | null {
  if (!iso) return null;
  const parsed = Date.parse(iso);
  if (!Number.isFinite(parsed)) return null;
  return Math.max(0, Math.floor((nowMs - parsed) / 60000));
}

/** Items that are still going to produce a deck (a cancelling one is winding down). */
export function activeGenerationItems<T extends Pick<GenerationActivityItem, "state">>(
  items: readonly T[],
): T[] {
  return items.filter((item) => item.state !== "cancelling");
}

export function isGenerationStalled(
  item: Pick<GenerationActivityItem, "state" | "lastProgressAt" | "startedAt">,
  nowMs: number,
  thresholdMs: number = GENERATION_STALL_THRESHOLD_MS,
): boolean {
  if (item.state !== "running") return false;
  const last = lastProgressMs(item);
  return last !== null && nowMs - last >= thresholdMs;
}
