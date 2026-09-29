/**
 * Reads the anonymous-telemetry opt-in state shown by the privacy toggles.
 * Fails closed: an unreachable or malformed probe reads as disabled so the
 * UI never displays tracking as on without the server saying so.
 */
export async function fetchTelemetryStatus(): Promise<boolean> {
  try {
    const data = await fetch("/api/telemetry-status").then((res) => res.json());
    return data?.telemetryEnabled === true;
  } catch {
    return false;
  }
}
