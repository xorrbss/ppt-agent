/**
 * Shared client/server timing constants. Values are the ones the former
 * per-file copies agreed on; change them here, not at a call site.
 */

/** Fetching a persisted presentation for export. */
export const PRESENTATION_FETCH_TIMEOUT_MS = 20_000;

/** Polling cadence for long-running operations (remake, template change, codex auth). */
export const POLL_INTERVAL_MS = 2_000;

/** How long a remake/preview poll keeps waiting before it reports a timeout. */
export const PREVIEW_TIMEOUT_MS = 15 * 60 * 1_000;
