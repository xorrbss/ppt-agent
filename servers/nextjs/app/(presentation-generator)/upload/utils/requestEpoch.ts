/**
 * Start a request under a shared epoch counter. The returned check is true only while
 * the owner is still live and no later request (or an explicit bump such as Close)
 * has advanced the counter, so a stale response can neither update state nor release
 * a busy flag that a newer request holds.
 */
export function beginEpochRequest(epoch: { current: number }, isLive: () => boolean): () => boolean {
  const started = ++epoch.current;
  return () => isLive() && started === epoch.current;
}
