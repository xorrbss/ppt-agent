/**
 * Opt-in phase timing for one isolated Chrome capture.
 *
 * The isolated export lane and an out-of-lane Node run execute the same
 * capture code at very different speeds, and the only way to attribute that
 * is to time the same phases on both sides of the process boundary. The trace
 * is off unless `AUTHORED_HYBRID_CAPTURE_TRACE=1`, writes one JSON line per
 * phase to stderr, and never changes control flow.
 */

const TRACE_PREFIX = "[hybrid-trace]";

function traceEnabled(): boolean {
  return process.env.AUTHORED_HYBRID_CAPTURE_TRACE === "1";
}

export function traceEvent(name: string, fields: Record<string, unknown> = {}): void {
  if (!traceEnabled()) return;
  try {
    process.stderr.write(
      `${TRACE_PREFIX} ${JSON.stringify({ at: Math.round(performance.now()), name, ...fields })}\n`
    );
  } catch {
    // Diagnostics must never fail a capture.
  }
}

export async function tracePhase<T>(
  name: string,
  run: () => Promise<T>,
  fields: Record<string, unknown> = {}
): Promise<T> {
  if (!traceEnabled()) return run();
  const started = performance.now();
  try {
    return await run();
  } finally {
    traceEvent(name, { ...fields, ms: Math.round(performance.now() - started) });
  }
}
