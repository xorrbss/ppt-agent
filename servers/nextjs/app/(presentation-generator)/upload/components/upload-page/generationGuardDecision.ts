// Pure decision helpers for the "another generation is already running" guard.
// No runtime imports of app modules so node:test can load this directly.
import type { GenerationActivityItem } from "../../../services/api/generation-activity-model";

export type GuardChoice = "view" | "queue" | "cancel_and_start";

/** Item the "view running deck" action opens: first running one, else first item. */
export function pickViewTarget(
  items: readonly GenerationActivityItem[],
): GenerationActivityItem | null {
  return items.find((item) => item.state === "running") ?? items[0] ?? null;
}

/** 409 means the item changed since the snapshot (finished, advanced, or already cancelled). */
export function isStalePreconditionError(error: unknown): boolean {
  return (
    typeof error === "object" &&
    error !== null &&
    (error as { status?: unknown }).status === 409
  );
}

export type CancelAllOutcome =
  | { ok: true }
  | { ok: false; stale: boolean; error: unknown };

/**
 * Cancels every item in order and stops at the first failure so the caller can
 * stay in the dialog. `cancel` receives a fresh idempotency key per item.
 */
export async function cancelAllItems(
  items: readonly GenerationActivityItem[],
  cancel: (request: {
    operationId: string;
    idempotencyKey: string;
    expectedRevision: number;
    expectedOperationVersion: number;
  }) => Promise<unknown>,
  newKey: () => string,
): Promise<CancelAllOutcome> {
  for (const item of items) {
    try {
      await cancel({
        operationId: item.operationId,
        idempotencyKey: newKey(),
        expectedRevision: item.presentationRevision,
        expectedOperationVersion: item.operationVersion,
      });
    } catch (error) {
      return { ok: false, stale: isStalePreconditionError(error), error };
    }
  }
  return { ok: true };
}

/** Pages label like "58/100"; the fraction is omitted when the total is unknown. */
export function pagesLabel(pages: { authored: number; total: number | null }): string {
  return pages.total === null ? String(pages.authored) : `${pages.authored}/${pages.total}`;
}
