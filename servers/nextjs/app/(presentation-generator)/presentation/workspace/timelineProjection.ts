import type {
  WorkspaceAttachment,
  WorkspaceEventPage,
  WorkspaceMessageAnchor,
  WorkspaceOperationAnchor,
  WorkspaceOperationEvent,
  WorkspaceProjection,
  WorkspaceTimelineAnchor,
  WorkspaceTimelinePage,
  WorkspaceUnknownAnchor,
} from "./types.ts";

type UnknownRecord = Record<string, unknown>;

function asRecord(value: unknown): UnknownRecord {
  return value !== null && typeof value === "object" && !Array.isArray(value)
    ? (value as UnknownRecord)
    : {};
}

function stringValue(...values: unknown[]): string | undefined {
  return values.find((value) => typeof value === "string" && value.length > 0) as
    | string
    | undefined;
}

function numberValue(...values: unknown[]): number | undefined {
  for (const value of values) {
    const parsed = typeof value === "number" ? value : typeof value === "string" ? Number(value) : NaN;
    if (Number.isFinite(parsed)) return parsed;
  }
  return undefined;
}

function booleanValue(...values: unknown[]): boolean | undefined {
  return values.find((value): value is boolean => typeof value === "boolean");
}

function objectValue(...values: unknown[]): UnknownRecord | undefined {
  return values.find(
    (value) => value !== null && typeof value === "object" && !Array.isArray(value),
  ) as UnknownRecord | undefined;
}

function operationStatus(...values: unknown[]): string | undefined {
  const status = stringValue(...values);
  if (!status) return undefined;
  if (status === "dead_lettered") return "dead_letter";
  if (status === "completed") return "succeeded";
  return status;
}

function eventStatus(raw: UnknownRecord, payload: UnknownRecord): string | undefined {
  const explicit = operationStatus(raw.status, raw.state, payload.status, payload.state);
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

function safeError(...values: unknown[]): string | undefined {
  for (const value of values) {
    if (typeof value === "string" && value.length > 0) return value;
    const raw = asRecord(value);
    const message = stringValue(raw.safe_message, raw.message, raw.detail);
    if (message) return message;
  }
  return undefined;
}

function normalizeAttachment(value: unknown, index: number): WorkspaceAttachment {
  const raw = asRecord(value);
  return {
    id: stringValue(raw.id, raw.asset_id, raw.attachment_id) ?? `attachment-${index}`,
    name: stringValue(raw.name, raw.filename, raw.file_name, raw.display_name) ?? "첨부 파일",
    status: stringValue(raw.status, raw.asset_status) ?? "unknown",
    mimeType: stringValue(raw.mimeType, raw.mime_type, raw.media_type, raw.content_type),
    size: numberValue(raw.size, raw.size_bytes),
    error: safeError(raw.error, raw.error_message),
  };
}

export function normalizeOperationEvent(value: unknown): WorkspaceOperationEvent | null {
  const raw = asRecord(value);
  const operationId = stringValue(raw.operationId, raw.operation_id);
  const sequence = numberValue(raw.sequence, raw.seq);
  if (!operationId || sequence === undefined) return null;
  const payload = asRecord(raw.payload);
  const rawError = asRecord(raw.error);
  const rawErrorDetail = asRecord(raw.errorDetail ?? raw.error_detail);
  const payloadError = asRecord(payload.error);
  const payloadErrorDetail = asRecord(payload.errorDetail ?? payload.error_detail);
  return {
    id:
      stringValue(raw.id, raw.event_id) ??
      `${operationId}:${sequence}:${stringValue(raw.type, raw.event_type) ?? "event"}`,
    operationId,
    operationVersion: numberValue(
      raw.operationVersion,
      raw.operation_version,
      payload.operationVersion,
      payload.operation_version,
    ),
    sequence,
    cursor: stringValue(raw.cursor, raw.event_cursor),
    type: stringValue(raw.type, raw.event_type) ?? "unknown",
    status: eventStatus(raw, payload),
    message: stringValue(raw.message, payload.message),
    progress: numberValue(raw.progress, raw.progress_percent, payload.progress, payload.percent),
    error: safeError(raw.error, raw.error_message, payload.error, payload.error_message),
    retryable: booleanValue(
      raw.retryable,
      rawError.retryable,
      rawErrorDetail.retryable,
      payload.retryable,
      payloadError.retryable,
      payloadErrorDetail.retryable,
    ),
    result: objectValue(raw.result, payload.result),
    createdAt: stringValue(raw.createdAt, raw.created_at),
  };
}

function normalizeMessage(raw: UnknownRecord, id: string, position: number): WorkspaceMessageAnchor {
  const attachments = Array.isArray(raw.attachments)
    ? raw.attachments.map(normalizeAttachment)
    : Array.isArray(raw.assets)
      ? raw.assets.map(normalizeAttachment)
      : [];
  const role = stringValue(raw.role);
  return {
    anchorType: "message",
    id,
    position,
    role: role === "user" || role === "assistant" || role === "system" ? role : "unknown",
    content: stringValue(raw.content, raw.text, raw.message) ?? "",
    createdAt: stringValue(raw.createdAt, raw.created_at),
    attachments,
    generationSettings: objectValue(raw.generationSettings, raw.generation_settings, raw.settings),
  };
}

function normalizeOperation(raw: UnknownRecord, id: string, position: number): WorkspaceOperationAnchor {
  const operationId = stringValue(raw.operationId, raw.operation_id, raw.id) ?? id;
  const rawError = asRecord(raw.error);
  const rawErrorDetail = asRecord(raw.errorDetail ?? raw.error_detail);
  const events = Array.isArray(raw.events)
    ? raw.events
        .map(normalizeOperationEvent)
        .filter((event): event is WorkspaceOperationEvent => event !== null)
        .sort((a, b) => a.sequence - b.sequence)
    : [];
  return {
    anchorType: "operation",
    id,
    operationId,
    position,
    operationType: stringValue(raw.operationType, raw.operation_type, raw.command_type) ?? "unknown",
    title: stringValue(raw.title, raw.label) ?? "작업",
    status: operationStatus(raw.status, raw.state) ?? "unknown",
    retryOfOperationId: stringValue(raw.retryOfOperationId, raw.retry_of_operation_id),
    approvalExpiresAt: stringValue(raw.approvalExpiresAt, raw.approval_expires_at),
    version: numberValue(raw.version, raw.operation_version, raw.optimistic_version),
    progress: numberValue(raw.progress, raw.progress_percent),
    error: safeError(raw.error, raw.error_message),
    retryable: booleanValue(
      raw.retryable,
      rawError.retryable,
      rawErrorDetail.retryable,
      events[events.length - 1]?.retryable,
    ),
    result: objectValue(raw.result),
    createdAt: stringValue(raw.createdAt, raw.created_at),
    updatedAt: stringValue(raw.updatedAt, raw.updated_at),
    events,
  };
}

export function normalizeTimelineAnchor(value: unknown, index: number): WorkspaceTimelineAnchor {
  const raw = asRecord(value);
  const id = stringValue(raw.id, raw.message_id, raw.operation_id) ?? `unknown-${index}`;
  const position = numberValue(raw.position, raw.anchor_position) ?? Number.MAX_SAFE_INTEGER;
  const rawType = stringValue(raw.anchorType, raw.anchor_type, raw.kind, raw.type) ?? "unknown";
  if (
    rawType === "operation" ||
    rawType === "operation_anchor" ||
    raw.operation_id !== undefined ||
    raw.status !== undefined ||
    raw.state !== undefined
  ) {
    return normalizeOperation(raw, id, position);
  }
  if (rawType === "message" || raw.message_id !== undefined || raw.role !== undefined) {
    return normalizeMessage(raw, id, position);
  }
  const unknown: WorkspaceUnknownAnchor = { anchorType: "unknown", id, position, rawType };
  return unknown;
}

function unwrap(value: unknown): UnknownRecord {
  const raw = asRecord(value);
  const data = Object.keys(asRecord(raw.data)).length > 0 ? asRecord(raw.data) : raw;
  return Object.keys(asRecord(data.timeline)).length > 0 ? asRecord(data.timeline) : data;
}

export function normalizeTimelinePage(value: unknown): WorkspaceTimelinePage {
  const raw = unwrap(value);
  const rawItems = Array.isArray(raw.items)
    ? raw.items
    : [
        ...(Array.isArray(raw.messages) ? raw.messages : []),
        ...(Array.isArray(raw.operations) ? raw.operations : []),
      ];
  const nextBeforePosition = numberValue(raw.nextBeforePosition, raw.next_before_position);
  return {
    items: rawItems.map(normalizeTimelineAnchor),
    nextBeforePosition,
    eventHighWatermark: stringValue(raw.eventHighWatermark, raw.event_high_watermark),
    hasMore:
      typeof raw.hasMore === "boolean"
        ? raw.hasMore
        : typeof raw.has_more === "boolean"
          ? raw.has_more
          : nextBeforePosition !== undefined,
    revision: numberValue(raw.revision),
  };
}

export function normalizeEventPage(value: unknown): WorkspaceEventPage {
  const raw = unwrap(value);
  const source = Array.isArray(value) ? value : Array.isArray(raw.events) ? raw.events : [];
  return {
    events: source
      .map(normalizeOperationEvent)
      .filter((event): event is WorkspaceOperationEvent => event !== null),
    eventHighWatermark: stringValue(raw.eventHighWatermark, raw.event_high_watermark, raw.cursor),
  };
}

function eventKey(event: WorkspaceOperationEvent): string {
  return event.id || `${event.operationId}:${event.sequence}`;
}

function sortAnchors(items: WorkspaceTimelineAnchor[]): WorkspaceTimelineAnchor[] {
  return [...items].sort((a, b) => a.position - b.position || a.id.localeCompare(b.id));
}

export function createWorkspaceProjection(): WorkspaceProjection {
  return {
    items: [],
    hasMore: false,
    seenEventKeys: {},
    rebaseRequired: false,
    orphanOperationIds: [],
  };
}

export function mergeTimelinePage(
  projection: WorkspaceProjection,
  page: WorkspaceTimelinePage,
  options: { preservePagination?: boolean } = {},
): WorkspaceProjection {
  const byId = new Map(projection.items.map((item) => [item.id, item]));
  for (const item of page.items) byId.set(item.id, item);
  const seenEventKeys = { ...projection.seenEventKeys };
  for (const item of page.items) {
    if (item.anchorType !== "operation") continue;
    for (const event of item.events) seenEventKeys[eventKey(event)] = true;
  }
  const projectedOperationIds = new Set(
    [...byId.values()]
      .filter((item): item is WorkspaceOperationAnchor => item.anchorType === "operation")
      .map((item) => item.operationId),
  );
  const orphanOperationIds = projection.orphanOperationIds.filter(
    (operationId) => !projectedOperationIds.has(operationId),
  );
  const rebaseRequired = orphanOperationIds.length > 0;
  return {
    items: sortAnchors([...byId.values()]),
    messageBeforePosition: options.preservePagination
      ? projection.messageBeforePosition
      : page.nextBeforePosition,
    // A timeline snapshot contains every event for each returned operation card.
    // It may therefore resolve a prior orphan safely. If the card is still absent,
    // keep the old cursor so polling/backfill can replay the unprojected event.
    eventHighWatermark: rebaseRequired
      ? projection.eventHighWatermark
      : page.eventHighWatermark ?? projection.eventHighWatermark,
    revision: page.revision ?? projection.revision,
    hasMore: options.preservePagination ? projection.hasMore : page.hasMore,
    seenEventKeys,
    rebaseRequired,
    orphanOperationIds,
  };
}

function patchOperation(
  operation: WorkspaceOperationAnchor,
  event: WorkspaceOperationEvent,
): WorkspaceOperationAnchor {
  return {
    ...operation,
    status: event.status ?? operation.status,
    // The event version is the operation row's post-transition CAS version.
    version: event.operationVersion ?? operation.version,
    progress: event.progress ?? operation.progress,
    error: event.error ?? operation.error,
    retryable: event.retryable ?? operation.retryable,
    result: event.result ?? operation.result,
    updatedAt: event.createdAt ?? operation.updatedAt,
    events: [...operation.events, event].sort((a, b) => a.sequence - b.sequence),
  };
}

/**
 * ADR-003: events only mutate their existing operation card. They never create
 * or reposition a top-level timeline anchor.
 */
export function applyOperationEvents(
  projection: WorkspaceProjection,
  page: WorkspaceEventPage,
): WorkspaceProjection {
  const seenEventKeys = { ...projection.seenEventKeys };
  const pendingByOperation = new Map<string, WorkspaceOperationEvent[]>();
  const projectedOperationIds = new Set(
    projection.items
      .filter((item): item is WorkspaceOperationAnchor => item.anchorType === "operation")
      .map((item) => item.operationId),
  );
  const orphanOperationIds = new Set(
    projection.orphanOperationIds.filter((operationId) => !projectedOperationIds.has(operationId)),
  );
  for (const event of page.events) {
    const key = eventKey(event);
    if (seenEventKeys[key]) continue;
    if (!projectedOperationIds.has(event.operationId)) {
      // Do not acknowledge an event until its top-level anchor is present. The
      // hook observes rebaseRequired, reloads/backfills the timeline, and polls
      // again from the unchanged high-watermark.
      orphanOperationIds.add(event.operationId);
      continue;
    }
    seenEventKeys[key] = true;
    const pending = pendingByOperation.get(event.operationId) ?? [];
    pending.push(event);
    pendingByOperation.set(event.operationId, pending);
  }
  const items = projection.items.map((item) => {
    if (item.anchorType !== "operation") return item;
    const events = pendingByOperation.get(item.operationId);
    if (!events) return item;
    return events.sort((a, b) => a.sequence - b.sequence).reduce(patchOperation, item);
  });
  const unresolvedOperationIds = [...orphanOperationIds].sort();
  const rebaseRequired = unresolvedOperationIds.length > 0;
  return {
    ...projection,
    items,
    eventHighWatermark: rebaseRequired
      ? projection.eventHighWatermark
      : page.eventHighWatermark ?? projection.eventHighWatermark,
    seenEventKeys,
    rebaseRequired,
    orphanOperationIds: unresolvedOperationIds,
  };
}
