/**
 * In-process progress for the authored-hybrid export.
 *
 * The long phases of an export run inside Next.js while the caller that reports
 * progress to the user (the FastAPI workspace operation runner) only holds a
 * single blocking HTTP request. This store is the read side of that gap: the
 * exporter records phase transitions here and the caller polls
 * `/api/internal/export-progress` to turn them into timeline events.
 *
 * It is deliberately in-memory. A restart loses progress, never an export: the
 * POST that actually produces the file is unaffected by anything here.
 */

export type ExportProgressPhase =
  | "queued"
  | "fidelity"
  | "slides"
  | "assembly"
  | "completed"
  | "failed";

export interface ExportProgressSnapshot {
  readonly phase: ExportProgressPhase;
  /** Slides finished in the per-slide native pass. Absent before it starts. */
  readonly completedSlides?: number;
  readonly totalSlides?: number;
  /** Milliseconds since the export began, for caller-side ETA estimates. */
  readonly elapsedMs: number;
  readonly updatedAt: number;
}

interface ExportProgressRecord {
  phase: ExportProgressPhase;
  completedSlides?: number;
  totalSlides?: number;
  startedAt: number;
  updatedAt: number;
}

/**
 * Concurrent duplicate exports are collapsed onto one job keyed by cache key
 * (see `inFlightHybridExports`), so progress is stored per cache key and every
 * operation that joined that job resolves to the same record. Keying by
 * operation id alone would leave the joining operation permanently silent.
 */
const progressByCacheKey = new Map<string, ExportProgressRecord>();
const cacheKeyByOperationId = new Map<string, string>();

/** Bounded so a long-lived server cannot accumulate finished exports. */
const MAX_TRACKED_EXPORTS = 32;
/** Finished records stay briefly so a final poll still observes the outcome. */
const COMPLETED_RETENTION_MS = 60_000;

function evictOldest(): void {
  while (progressByCacheKey.size > MAX_TRACKED_EXPORTS) {
    const oldestKey = progressByCacheKey.keys().next().value;
    if (typeof oldestKey !== "string") break;
    forgetExportProgress(oldestKey);
  }
}

function pruneExpired(now: number): void {
  for (const [cacheKey, record] of progressByCacheKey) {
    const terminal = record.phase === "completed" || record.phase === "failed";
    if (terminal && now - record.updatedAt > COMPLETED_RETENTION_MS) {
      forgetExportProgress(cacheKey);
    }
  }
}

export function beginExportProgress(
  cacheKey: string,
  operationId: string | undefined,
  startedAt: number,
): void {
  const now = Date.now();
  pruneExpired(now);
  if (operationId) cacheKeyByOperationId.set(operationId, cacheKey);
  const existing = progressByCacheKey.get(cacheKey);
  if (existing) return;
  progressByCacheKey.set(cacheKey, {
    phase: "queued",
    startedAt,
    updatedAt: now,
  });
  evictOldest();
}

export function recordExportProgress(
  cacheKey: string,
  update: {
    phase?: ExportProgressPhase;
    completedSlides?: number;
    totalSlides?: number;
  },
): void {
  const record = progressByCacheKey.get(cacheKey);
  if (!record) return;
  if (update.phase !== undefined) record.phase = update.phase;
  if (update.totalSlides !== undefined) record.totalSlides = update.totalSlides;
  if (update.completedSlides !== undefined) {
    // Slides finish concurrently; never let a late callback move the count back.
    record.completedSlides = Math.max(
      record.completedSlides ?? 0,
      update.completedSlides,
    );
  }
  record.updatedAt = Date.now();
}

export function readExportProgress(
  operationId: string,
): ExportProgressSnapshot | null {
  const now = Date.now();
  pruneExpired(now);
  const cacheKey = cacheKeyByOperationId.get(operationId);
  const record = cacheKey ? progressByCacheKey.get(cacheKey) : undefined;
  if (!record) return null;
  return {
    phase: record.phase,
    ...(record.completedSlides !== undefined
      ? { completedSlides: record.completedSlides }
      : {}),
    ...(record.totalSlides !== undefined
      ? { totalSlides: record.totalSlides }
      : {}),
    elapsedMs: Math.max(0, Math.round(now - record.startedAt)),
    updatedAt: record.updatedAt,
  };
}

export function forgetExportProgress(cacheKey: string): void {
  progressByCacheKey.delete(cacheKey);
  for (const [operationId, key] of cacheKeyByOperationId) {
    if (key === cacheKey) cacheKeyByOperationId.delete(operationId);
  }
}

/** Test-only reset so suites never inherit another test's export state. */
export function resetExportProgressForTesting(): void {
  progressByCacheKey.clear();
  cacheKeyByOperationId.clear();
}
