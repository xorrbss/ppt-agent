export function retryAudit(events) {
  let maximumExplicitRetryCount = null;
  let maximumAttemptNumber = null;
  let providerAttemptCount = null;
  let retryEventCount = 0;
  const recoveryReasons = new Set();
  const recoveryStatuses = new Set();
  for (const event of events) {
    const stack = [event];
    const visited = new Set();
    let eventExplicitRetryCount = null;
    let eventAttemptNumber = null;
    while (stack.length) {
      const value = stack.pop();
      if (!value || typeof value !== "object" || visited.has(value)) continue;
      visited.add(value);
      for (const [rawKey, nested] of Object.entries(value)) {
        const key = rawKey.replace(/[_-]/g, "").toLowerCase();
        if (typeof nested === "number" && Number.isFinite(nested)) {
          if (key === "providerattemptcount") providerAttemptCount = Math.max(providerAttemptCount ?? nested, nested);
          else if (/(retrycount|retries|retryattempts)/.test(key)) eventExplicitRetryCount = Math.max(eventExplicitRetryCount ?? nested, nested);
          else if (/(attemptnumber|retryattempt)/.test(key)) eventAttemptNumber = Math.max(eventAttemptNumber ?? nested, nested);
        }
        if (typeof nested === "string" && /(recoveryreason|retryreason|requeuereason)/.test(key)
          && /^[A-Za-z0-9_.-]{1,128}$/.test(nested.trim())) recoveryReasons.add(nested.trim());
        if (typeof nested === "string" && key === "recoverystatus"
          && ["not_required", "required", "scheduled", "in_progress", "recovered", "failed", "unavailable"].includes(nested)) {
          recoveryStatuses.add(nested);
        }
        if (nested && typeof nested === "object") stack.push(nested);
      }
    }
    const eventLabel = JSON.stringify({ type: event?.type, status: event?.status, result: event?.result?.status || event?.result?.stage }).toLowerCase();
    if (/retry|requeued|requeue/.test(eventLabel)) retryEventCount += 1;
    if (eventExplicitRetryCount !== null) maximumExplicitRetryCount = Math.max(maximumExplicitRetryCount ?? eventExplicitRetryCount, eventExplicitRetryCount);
    if (eventAttemptNumber !== null) maximumAttemptNumber = Math.max(maximumAttemptNumber ?? eventAttemptNumber, eventAttemptNumber);
  }
  const retryEvidence = [maximumExplicitRetryCount, maximumAttemptNumber === null ? null : Math.max(0, maximumAttemptNumber - 1), retryEventCount || null]
    .filter((value) => value !== null);
  const retryCount = retryEvidence.length ? Math.max(...retryEvidence) : null;
  const unavailable = [];
  if (retryCount === null) unavailable.push("retryCount");
  if (providerAttemptCount === null) unavailable.push("providerAttemptCount");
  if (maximumAttemptNumber === null) unavailable.push("attemptNumber");
  if (!recoveryReasons.size) unavailable.push("recoveryReason");
  const recoveryStatus = recoveryStatuses.has("recovered") ? "recovered"
    : recoveryStatuses.has("failed") ? "failed"
      : recoveryStatuses.has("in_progress") ? "in_progress"
        : recoveryStatuses.has("scheduled") ? "scheduled"
          : recoveryStatuses.has("required") ? "required"
            : recoveryStatuses.has("not_required") ? "not_required" : null;
  if (recoveryStatus === null) unavailable.push("recoveryStatus");
  return {
    status: unavailable.length ? (unavailable.length === 5 ? "unavailable" : "partial") : "available",
    providerAttemptCount,
    retryCount,
    eventCount: events.length,
    maximumExplicitRetryCount,
    maximumAttemptNumber,
    retryEventCount,
    recoveryReason: recoveryReasons.size ? [...recoveryReasons].at(-1) : null,
    recoveryReasons: recoveryReasons.size ? [...recoveryReasons] : null,
    recoveryStatus,
    unavailable,
  };
}

export function requestFailureSummary(failures) {
  const knownAudits = failures.map((failure) => failure.retryAuditDetail)
    .filter((audit) => Number.isFinite(audit?.retryCount));
  const auditStatus = failures.length === 0 ? "not_applicable"
    : knownAudits.length === 0 ? "unavailable"
      : knownAudits.length === failures.length ? "observed" : "partial";
  return {
    proxyFetchFailed: failures.filter((failure) => failure.classification === "proxy_fetch_failed").length,
    listenerInterrupted: failures.filter((failure) => ["listener_interrupted", "proxy_or_listener_http_failure"].includes(failure.classification)).length,
    hiddenInternalRetries: failures.length && knownAudits.length === 0
      ? null : knownAudits.filter((audit) => audit.retryCount > 0).length,
    automaticRetries: failures.length && knownAudits.length === 0
      ? null : knownAudits.reduce((sum, audit) => sum + audit.retryCount, 0),
    providerAttempts: failures.length && failures.every((failure) => !Number.isFinite(failure.retryAuditDetail?.providerAttemptCount))
      ? null : failures.reduce((sum, failure) => sum + (Number.isFinite(failure.retryAuditDetail?.providerAttemptCount)
        ? failure.retryAuditDetail.providerAttemptCount : 0), 0),
    retryAuditStatus: auditStatus,
    listenerRestartedByHarness: false,
  };
}
