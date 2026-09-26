type UnknownRecord = Record<string, unknown>;

export function asRecord(value: unknown): UnknownRecord {
  return value !== null && typeof value === "object" && !Array.isArray(value)
    ? (value as UnknownRecord)
    : {};
}

export function stringValue(...values: unknown[]): string | undefined {
  return values.find(
    (value) => typeof value === "string" && value.length > 0,
  ) as string | undefined;
}

export function numberValue(...values: unknown[]): number | undefined {
  for (const value of values) {
    const parsed =
      typeof value === "number"
        ? value
        : typeof value === "string"
          ? Number(value)
          : NaN;
    if (Number.isFinite(parsed)) return parsed;
  }
  return undefined;
}

export function safeIntegerValue(value: unknown, minimum: number): number | undefined {
  return typeof value === "number" &&
    Number.isSafeInteger(value) &&
    value >= minimum
    ? value
    : undefined;
}

const CANONICAL_CURSOR = /^(0|[1-9]\d*)$/;

export function canonicalCursor(value: unknown): string | undefined {
  return typeof value === "string" && CANONICAL_CURSOR.test(value)
    ? value
    : undefined;
}

export function cursorOrdinal(value: string | undefined): bigint | undefined {
  if (!value) return undefined;
  const match = value.match(/(?:^|[-:])(0|[1-9]\d*)$/);
  if (!match) return undefined;
  try {
    return BigInt(match[1]);
  } catch {
    return undefined;
  }
}

export function monotonicEventHighWatermark(
  current: string | undefined,
  incoming: string | undefined,
): string | undefined {
  if (current === undefined) return incoming;
  if (incoming === undefined || incoming === current) return current;
  const currentOrdinal = cursorOrdinal(current);
  const incomingOrdinal = cursorOrdinal(incoming);
  if (currentOrdinal === undefined || incomingOrdinal === undefined)
    return current;
  return incomingOrdinal > currentOrdinal ? incoming : current;
}

export function booleanValue(...values: unknown[]): boolean | undefined {
  return values.find((value): value is boolean => typeof value === "boolean");
}

export function objectValue(...values: unknown[]): UnknownRecord | undefined {
  return values.find(
    (value) =>
      value !== null && typeof value === "object" && !Array.isArray(value),
  ) as UnknownRecord | undefined;
}

export function operationStatus(...values: unknown[]): string | undefined {
  const status = stringValue(...values);
  if (!status) return undefined;
  if (status === "dead_lettered") return "dead_letter";
  if (status === "completed") return "succeeded";
  return status;
}

export function eventStatus(
  raw: UnknownRecord,
  payload: UnknownRecord,
): string | undefined {
  const explicit = operationStatus(
    raw.status,
    raw.state,
    payload.status,
    payload.state,
  );
  if (explicit) return explicit;
  const eventType = stringValue(raw.type, raw.event_type);
  if (
    eventType === "queued" ||
    eventType === "completed" ||
    eventType === "succeeded" ||
    eventType === "failed" ||
    eventType === "cancelled" ||
    eventType === "approval_required" ||
    eventType === "awaiting_approval" ||
    eventType === "blocked" ||
    eventType === "dead_lettered"
  ) {
    return operationStatus(eventType);
  }
  return undefined;
}

export function explicitSafeError(...values: unknown[]): string | undefined {
  for (const value of values) {
    if (typeof value === "string" && value.length > 0) return value;
    const raw = asRecord(value);
    const message = stringValue(raw.safeMessage, raw.safe_message);
    if (message) return message;
  }
  return undefined;
}

