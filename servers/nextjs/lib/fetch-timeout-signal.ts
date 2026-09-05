/**
 * Bounded AbortSignals for server-side fetches to FastAPI.
 *
 * A route handler that awaits FastAPI without a signal hangs for as long as
 * the socket does, holding a Next.js worker and the browser request with it.
 * Every internal fetch therefore carries a per-call timeout, optionally
 * combined with the incoming request's own signal so a client disconnect also
 * cancels the upstream call.
 */

type AbortSignalWithAny = typeof AbortSignal & {
  any?: (signals: AbortSignal[]) => AbortSignal;
};

/** Portable fallback for `AbortSignal.any` (Node < 20.3). */
export function combineAbortSignals(signals: AbortSignal[]): AbortSignal {
  const controller = new AbortController();
  for (const signal of signals) {
    if (signal.aborted) {
      controller.abort(signal.reason);
      return controller.signal;
    }
  }
  for (const signal of signals) {
    signal.addEventListener(
      "abort",
      () => {
        if (!controller.signal.aborted) controller.abort(signal.reason);
      },
      { once: true },
    );
  }
  return controller.signal;
}

/**
 * A signal that aborts after `timeoutMs` (with a DOMException "TimeoutError",
 * exactly like `AbortSignal.timeout`) or as soon as `parent` aborts.
 */
export function fetchTimeoutSignal(
  timeoutMs: number,
  parent?: AbortSignal | null,
): AbortSignal {
  if (!Number.isFinite(timeoutMs) || timeoutMs <= 0) {
    throw new RangeError("fetch timeout must be a positive number of milliseconds");
  }
  const timeout = AbortSignal.timeout(timeoutMs);
  if (!parent) return timeout;
  const { any } = AbortSignal as AbortSignalWithAny;
  if (typeof any === "function") {
    return any.call(AbortSignal, [parent, timeout]);
  }
  return combineAbortSignals([parent, timeout]);
}
