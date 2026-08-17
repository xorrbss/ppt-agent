import { isKnownWorkspaceOperationType } from "./types.ts";
import type {
  WorkspaceAttachment,
  WorkspaceActionReceipt,
  WorkspaceAllowedAction,
  WorkspaceEventPage,
  WorkspaceMessageAnchor,
  WorkspaceOperationActor,
  WorkspaceOperationAnchor,
  WorkspaceOperationEvent,
  WorkspaceProjection,
  WorkspaceTimelineAnchor,
  WorkspaceTimelinePage,
  WorkspaceUnknownAnchor,
} from "./types.ts";

type UnknownRecord = Record<string, unknown>;
const SUPPORTED_SCHEMA_VERSION = 1;
const ACTION_IDS = new Set([
  "approve",
  "reject",
  "cancel",
  "retry",
  "undo",
  "download",
  "focus",
]);
const ACTION_VARIANTS = new Set(["primary", "secondary", "danger"]);
const READ_ONLY_ACTION_IDS = new Set(["download", "focus"]);
const ACTION_LABELS: Record<string, string> = {
  approve: "승인",
  reject: "거절",
  cancel: "취소",
  retry: "다시 시도",
  undo: "되돌리기",
  download: "다운로드",
  focus: "결과 보기",
};
const ACTION_REASON_LABELS: Record<string, string> = {
  approval_expired: "승인 기한이 만료되었습니다.",
  workspace_undo_not_supported: "이 작업은 되돌리기를 지원하지 않습니다.",
};

function asRecord(value: unknown): UnknownRecord {
  return value !== null && typeof value === "object" && !Array.isArray(value)
    ? (value as UnknownRecord)
    : {};
}

function stringValue(...values: unknown[]): string | undefined {
  return values.find(
    (value) => typeof value === "string" && value.length > 0,
  ) as string | undefined;
}

function numberValue(...values: unknown[]): number | undefined {
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

function safeIntegerValue(value: unknown, minimum: number): number | undefined {
  return typeof value === "number" &&
    Number.isSafeInteger(value) &&
    value >= minimum
    ? value
    : undefined;
}

const CANONICAL_CURSOR = /^(0|[1-9]\d*)$/;

function canonicalCursor(value: unknown): string | undefined {
  return typeof value === "string" && CANONICAL_CURSOR.test(value)
    ? value
    : undefined;
}

function cursorOrdinal(value: string | undefined): bigint | undefined {
  if (!value) return undefined;
  const match = value.match(/(?:^|[-:])(0|[1-9]\d*)$/);
  if (!match) return undefined;
  try {
    return BigInt(match[1]);
  } catch {
    return undefined;
  }
}

function monotonicEventHighWatermark(
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

function booleanValue(...values: unknown[]): boolean | undefined {
  return values.find((value): value is boolean => typeof value === "boolean");
}

function objectValue(...values: unknown[]): UnknownRecord | undefined {
  return values.find(
    (value) =>
      value !== null && typeof value === "object" && !Array.isArray(value),
  ) as UnknownRecord | undefined;
}

function operationStatus(...values: unknown[]): string | undefined {
  const status = stringValue(...values);
  if (!status) return undefined;
  if (status === "dead_lettered") return "dead_letter";
  if (status === "completed") return "succeeded";
  return status;
}

function eventStatus(
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

function explicitSafeError(...values: unknown[]): string | undefined {
  for (const value of values) {
    if (typeof value === "string" && value.length > 0) return value;
    const raw = asRecord(value);
    const message = stringValue(raw.safeMessage, raw.safe_message);
    if (message) return message;
  }
  return undefined;
}

function normalizeAttachment(
  value: unknown,
  index: number,
): WorkspaceAttachment {
  const raw = asRecord(value);
  const safeFailure = asRecord(
    raw.safeFailure ?? raw.safe_failure ?? raw.failure,
  );
  const links = asRecord(raw.links);
  const actions = asRecord(raw.actions);
  const downloadActionRecord = asRecord(actions.download);
  const previewActionRecord = asRecord(actions.preview);
  const unavailableReason = stringValue(
    raw.unavailableReason,
    raw.unavailable_reason,
    safeFailure.code,
    safeFailure.failureCode,
    safeFailure.failure_code,
  );
  const unavailableState: Record<string, string> = {
    workspace_asset_failed: "failed",
    workspace_asset_quarantined: "quarantined",
    workspace_asset_expired: "expired",
    workspace_asset_deleted: "deleted",
  };
  const stateAtTime =
    stringValue(
      raw.stateAtTime,
      raw.state_at_time,
      raw.assetStateAtTime,
      raw.asset_state_at_time,
      raw.snapshotState,
      raw.snapshot_state,
      raw.asset_state,
      raw.status,
      raw.asset_status,
    ) ?? "unknown";
  const currentState =
    stringValue(
      raw.currentState,
      raw.current_state,
      raw.liveState,
      raw.live_state,
      raw.currentStatus,
      raw.current_status,
      unavailableReason ? unavailableState[unavailableReason] : undefined,
      raw.status,
      raw.asset_status,
      stateAtTime,
    ) ?? "unknown";
  const sha256 = stringValue(raw.sha256, raw.content_sha256, raw.hash);
  const downloadAction = stringValue(
    raw.downloadAction,
    raw.download_action,
    actions.download,
    downloadActionRecord.id,
    downloadActionRecord.action,
  );
  const previewAction = stringValue(
    raw.previewAction,
    raw.preview_action,
    actions.preview,
    previewActionRecord.id,
    previewActionRecord.action,
  );
  return {
    id:
      stringValue(raw.id, raw.asset_id, raw.attachment_id) ??
      `attachment-${index}`,
    name:
      stringValue(raw.name, raw.filename, raw.file_name, raw.display_name) ??
      "첨부 파일",
    status: currentState,
    stateAtTime,
    currentState,
    mimeType: stringValue(
      raw.mimeType,
      raw.mime_type,
      raw.media_type,
      raw.content_type,
    ),
    size: numberValue(raw.size, raw.size_bytes),
    ...(sha256 ? { sha256 } : {}),
    error: explicitSafeError(
      raw.safeError,
      raw.safe_error,
      raw.safeMessage,
      raw.safe_message,
      raw.safeFailure,
      raw.safe_failure,
    ),
    failureCode: stringValue(
      raw.failureCode,
      raw.failure_code,
      raw.assetFailureCode,
      raw.asset_failure_code,
      safeFailure.code,
      safeFailure.failureCode,
      safeFailure.failure_code,
      unavailableReason,
    ),
    downloadUrl: stringValue(
      raw.downloadUrl,
      raw.download_url,
      links.download,
      downloadActionRecord.url,
      downloadActionRecord.href,
    ),
    previewUrl: stringValue(
      raw.previewUrl,
      raw.preview_url,
      links.preview,
      previewActionRecord.url,
      previewActionRecord.href,
    ),
    ...(downloadAction ? { downloadAction } : {}),
    ...(previewAction ? { previewAction } : {}),
  };
}

interface NormalizeOperationEventOptions {
  allowMissingSchema?: boolean;
}

export function normalizeOperationEvent(
  value: unknown,
  options: NormalizeOperationEventOptions = {},
): WorkspaceOperationEvent | null {
  const raw = asRecord(value);
  const schemaVersion = numberValue(raw.schemaVersion, raw.schema_version);
  if (
    schemaVersion !== SUPPORTED_SCHEMA_VERSION &&
    !(schemaVersion === undefined && options.allowMissingSchema === true)
  )
    return null;
  const operationId = stringValue(raw.operationId, raw.operation_id);
  const sequence = numberValue(raw.sequence, raw.seq);
  if (!operationId || sequence === undefined) return null;
  const payload = asRecord(raw.payload);
  const rawError = asRecord(raw.error);
  const rawErrorDetail = asRecord(raw.errorDetail ?? raw.error_detail);
  const payloadError = asRecord(payload.error);
  const payloadErrorDetail = asRecord(
    payload.errorDetail ?? payload.error_detail,
  );
  const explicitFailureMessage = explicitSafeError(
    raw.safeError,
    raw.safe_error,
    raw.safeMessage,
    raw.safe_message,
    rawError.safeMessage,
    rawError.safe_message,
    rawErrorDetail.safeMessage,
    rawErrorDetail.safe_message,
    payload.safeError,
    payload.safe_error,
    payload.safeMessage,
    payload.safe_message,
    payloadError.safeMessage,
    payloadError.safe_message,
    payloadErrorDetail.safeMessage,
    payloadErrorDetail.safe_message,
  );
  // Pydantic includes optional wire fields as `null` by default. A null error
  // is absence, not a failed operation; treating it as present made every live
  // progress event render the generic red failure banner.
  const hasFailure = [
    raw.error,
    raw.errorDetail,
    raw.error_detail,
    payload.error,
    payload.errorDetail,
    payload.error_detail,
  ].some((error) => error !== undefined && error !== null && error !== "");
  const safeFailureMessage = hasFailure
    ? (explicitFailureMessage ?? "작업을 완료하지 못했습니다.")
    : undefined;
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
    operationAnchorPosition: numberValue(
      raw.operationAnchorPosition,
      raw.operation_anchor_position,
    ),
    type: stringValue(raw.type, raw.event_type) ?? "unknown",
    status: eventStatus(raw, payload),
    // Failure text is rendered only from an explicitly public safe_message.
    // Provider/internal messages remain available server-side for diagnostics.
    message: hasFailure
      ? safeFailureMessage
      : stringValue(
          raw.safeMessage,
          raw.safe_message,
          payload.safeMessage,
          payload.safe_message,
          raw.message,
          payload.message,
        ),
    progress: numberValue(
      raw.progress,
      raw.progress_percent,
      payload.progress,
      payload.percent,
    ),
    error: safeFailureMessage,
    retryable: booleanValue(
      raw.retryable,
      rawError.retryable,
      rawErrorDetail.retryable,
      payload.retryable,
      payloadError.retryable,
      payloadErrorDetail.retryable,
    ),
    capacityWaiting: booleanValue(
      raw.capacityWaiting,
      raw.capacity_waiting,
      payload.capacityWaiting,
      payload.capacity_waiting,
    ),
    queuePosition: safeIntegerValue(
      raw.queuePosition ??
        raw.queue_position ??
        payload.queuePosition ??
        payload.queue_position,
      1,
    ),
    result: objectValue(raw.result, payload.result),
    createdAt: stringValue(raw.createdAt, raw.created_at),
  };
}

/**
 * Validate an event that is allowed to advance the durable stream cursor.
 * Timeline snapshots remain backwards compatible, but polling/SSE must use
 * the complete v1 shape so malformed frames are replayed instead of acked.
 */
export function normalizeCursorOperationEvent(
  value: unknown,
): WorkspaceOperationEvent | null {
  const raw = asRecord(value);
  const schemaVersion = safeIntegerValue(
    raw.schemaVersion ?? raw.schema_version,
    1,
  );
  const id = stringValue(raw.id, raw.event_id);
  const operationId = stringValue(raw.operationId, raw.operation_id);
  const operationVersion = safeIntegerValue(
    raw.operationVersion ?? raw.operation_version,
    0,
  );
  const sequence = safeIntegerValue(raw.sequence ?? raw.seq, 1);
  const cursor = canonicalCursor(raw.cursor ?? raw.event_cursor);
  const type = stringValue(raw.type, raw.event_type);
  const rawAnchorPosition =
    raw.operationAnchorPosition ?? raw.operation_anchor_position;
  const operationAnchorPosition =
    rawAnchorPosition === undefined
      ? undefined
      : safeIntegerValue(rawAnchorPosition, 1);
  if (
    schemaVersion !== SUPPORTED_SCHEMA_VERSION ||
    !id ||
    !operationId ||
    operationVersion === undefined ||
    sequence === undefined ||
    !cursor ||
    !type ||
    (rawAnchorPosition !== undefined && operationAnchorPosition === undefined)
  ) {
    return null;
  }
  const normalized = normalizeOperationEvent(raw);
  return normalized
    ? {
        ...normalized,
        id,
        operationId,
        operationVersion,
        sequence,
        cursor,
        operationAnchorPosition,
        type,
      }
    : null;
}

type ActionMode = "authoritative" | "read-only";

function normalizeAllowedAction(
  value: unknown,
  actionMode: ActionMode,
): WorkspaceAllowedAction | null {
  const raw = typeof value === "string" ? { id: value } : asRecord(value);
  const id = stringValue(raw.id, raw.action);
  if (!id || !ACTION_IDS.has(id)) return null;
  if (actionMode === "read-only" && !READ_ONLY_ACTION_IDS.has(id)) return null;
  const variant = stringValue(raw.variant);
  const rawReason = stringValue(
    raw.reason,
    raw.disabledReason,
    raw.disabled_reason,
  );
  return {
    id: id as WorkspaceAllowedAction["id"],
    // Action ids are the stable contract. Server-provided labels are not
    // trusted as localized UI copy and may expose internal/raw identifiers.
    label: ACTION_LABELS[id] ?? "작업",
    variant: (variant && ACTION_VARIANTS.has(variant)
      ? variant
      : "secondary") as WorkspaceAllowedAction["variant"],
    enabled: raw.enabled === true,
    reason: rawReason
      ? (ACTION_REASON_LABELS[rawReason] ??
        "현재 이 작업을 사용할 수 없습니다.")
      : raw.enabled === true
        ? undefined
        : "서버가 이 작업을 허용하지 않았습니다.",
  };
}

function normalizeOperationActor(
  raw: UnknownRecord,
): WorkspaceOperationActor | undefined {
  const value = raw.actor;
  const actor = asRecord(value);
  const type = stringValue(
    actor.type,
    actor.actorType,
    actor.actor_type,
    typeof value === "string" ? value : undefined,
    raw.actorType,
    raw.actor_type,
  );
  const id = stringValue(
    actor.id,
    actor.actorId,
    actor.actor_id,
    raw.actorId,
    raw.actor_id,
  );
  const label = stringValue(
    actor.label,
    actor.name,
    actor.displayName,
    actor.display_name,
    raw.actorLabel,
    raw.actor_label,
  );
  return type || id || label ? { type, id, label } : undefined;
}

function normalizeMessage(
  raw: UnknownRecord,
  id: string,
  position: number,
): WorkspaceMessageAnchor {
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
    role:
      role === "user" || role === "assistant" || role === "system"
        ? role
        : "unknown",
    content: stringValue(raw.content, raw.text, raw.message) ?? "",
    createdAt: stringValue(raw.createdAt, raw.created_at),
    attachments,
    generationSettings: objectValue(
      raw.generationSettings,
      raw.generation_settings,
      raw.settings,
    ),
  };
}

function normalizeOperation(
  raw: UnknownRecord,
  id: string,
  position: number,
  actionMode: ActionMode,
): WorkspaceOperationAnchor {
  const operationId =
    stringValue(raw.operationId, raw.operation_id, raw.id) ?? id;
  const rawError = asRecord(raw.error);
  const rawErrorDetail = asRecord(raw.errorDetail ?? raw.error_detail);
  const events = Array.isArray(raw.events)
    ? raw.events
        .map((event) =>
          normalizeOperationEvent(event, { allowMissingSchema: true }),
        )
        .filter((event): event is WorkspaceOperationEvent => event !== null)
        .sort((a, b) => a.sequence - b.sequence)
    : [];
  const operationType =
    stringValue(raw.operationType, raw.operation_type, raw.command_type) ??
    "unknown";
  const operationSupported = isKnownWorkspaceOperationType(operationType);
  return {
    anchorType: "operation",
    id,
    operationId,
    position,
    operationType,
    title: operationSupported
      ? (stringValue(raw.title, raw.label) ?? "작업")
      : "지원되지 않는 작업",
    status: operationStatus(raw.status, raw.state) ?? "unknown",
    retryOfOperationId: stringValue(
      raw.retryOfOperationId,
      raw.retry_of_operation_id,
    ),
    approvalExpiresAt: stringValue(
      raw.approvalExpiresAt,
      raw.approval_expires_at,
    ),
    version: numberValue(
      raw.version,
      raw.operation_version,
      raw.optimistic_version,
    ),
    progress: numberValue(raw.progress, raw.progress_percent),
    error: explicitSafeError(
      raw.safeError,
      raw.safe_error,
      raw.safeMessage,
      raw.safe_message,
      rawError.safeMessage,
      rawError.safe_message,
      rawErrorDetail.safeMessage,
      rawErrorDetail.safe_message,
      raw.errorMessage,
      raw.error_message,
    ),
    retryable: booleanValue(
      raw.retryable,
      rawError.retryable,
      rawErrorDetail.retryable,
      events[events.length - 1]?.retryable,
    ),
    capacityWaiting: booleanValue(
      raw.capacityWaiting,
      raw.capacity_waiting,
      events[events.length - 1]?.capacityWaiting,
    ),
    queuePosition: safeIntegerValue(
      raw.queuePosition ??
        raw.queue_position ??
        events[events.length - 1]?.queuePosition,
      1,
    ),
    result: objectValue(raw.result),
    actor: normalizeOperationActor(raw),
    target: objectValue(raw.target),
    expectedRevision: numberValue(raw.expectedRevision, raw.expected_revision),
    resultRevision: numberValue(
      raw.resultRevision,
      raw.result_revision,
      raw.appliedRevision,
      raw.applied_revision,
    ),
    activitySummary: stringValue(raw.activitySummary, raw.activity_summary),
    createdAt: stringValue(raw.createdAt, raw.created_at),
    updatedAt: stringValue(raw.updatedAt, raw.updated_at),
    events,
    allowedActions: operationSupported
      ? (Array.isArray(raw.allowedActions)
          ? raw.allowedActions
          : Array.isArray(raw.allowed_actions)
            ? raw.allowed_actions
            : []
        )
          .map((action) => normalizeAllowedAction(action, actionMode))
          .filter((action): action is WorkspaceAllowedAction => action !== null)
      : [],
  };
}

interface NormalizeTimelineAnchorOptions {
  actionMode?: ActionMode;
}

export function normalizeTimelineAnchor(
  value: unknown,
  index: number,
  options: NormalizeTimelineAnchorOptions = {},
): WorkspaceTimelineAnchor {
  const raw = asRecord(value);
  const id =
    stringValue(raw.id, raw.message_id, raw.operation_id) ?? `unknown-${index}`;
  const position =
    numberValue(raw.position, raw.anchor_position) ?? Number.MAX_SAFE_INTEGER;
  const rawType =
    stringValue(raw.anchorType, raw.anchor_type, raw.kind, raw.type) ??
    "unknown";
  const schemaVersion = numberValue(raw.schemaVersion, raw.schema_version);
  if (
    schemaVersion !== undefined &&
    schemaVersion !== SUPPORTED_SCHEMA_VERSION
  ) {
    return {
      anchorType: "unknown",
      id,
      position,
      rawType: `${rawType}@v${schemaVersion}`,
    };
  }
  if (
    rawType === "operation" ||
    rawType === "operation_anchor" ||
    (rawType === "unknown" &&
      (raw.operation_id !== undefined ||
        raw.status !== undefined ||
        raw.state !== undefined))
  ) {
    const actionMode =
      options.actionMode ??
      (schemaVersion === SUPPORTED_SCHEMA_VERSION
        ? "authoritative"
        : "read-only");
    return normalizeOperation(raw, id, position, actionMode);
  }
  if (
    rawType === "message" ||
    (rawType === "unknown" &&
      (raw.message_id !== undefined || raw.role !== undefined))
  ) {
    return normalizeMessage(raw, id, position);
  }
  const unknown: WorkspaceUnknownAnchor = {
    anchorType: "unknown",
    id,
    position,
    rawType,
  };
  return unknown;
}

function unwrap(value: unknown): UnknownRecord {
  const raw = asRecord(value);
  const data =
    Object.keys(asRecord(raw.data)).length > 0 ? asRecord(raw.data) : raw;
  return Object.keys(asRecord(data.timeline)).length > 0
    ? asRecord(data.timeline)
    : data;
}

function envelopeSchemaVersion(value: unknown): number | undefined {
  const envelope = asRecord(value);
  const data = asRecord(envelope.data);
  const timeline = asRecord(data.timeline);
  return numberValue(
    envelope.schemaVersion,
    envelope.schema_version,
    data.schemaVersion,
    data.schema_version,
    timeline.schemaVersion,
    timeline.schema_version,
  );
}

export function normalizeTimelinePage(value: unknown): WorkspaceTimelinePage {
  const raw = unwrap(value);
  const rawItems = Array.isArray(raw.items)
    ? raw.items
    : [
        ...(Array.isArray(raw.messages) ? raw.messages : []),
        ...(Array.isArray(raw.operations) ? raw.operations : []),
      ];
  const nextBeforePosition = numberValue(
    raw.nextBeforePosition,
    raw.next_before_position,
  );
  const schemaVersion =
    envelopeSchemaVersion(value) ??
    numberValue(raw.schemaVersion, raw.schema_version);
  const schemaSupported =
    schemaVersion === undefined || schemaVersion === SUPPORTED_SCHEMA_VERSION;
  const schemaCurrent = schemaVersion === SUPPORTED_SCHEMA_VERSION;
  return {
    items: schemaSupported
      ? rawItems.map((item, index) =>
          normalizeTimelineAnchor(item, index, {
            actionMode: schemaCurrent ? "authoritative" : "read-only",
          }),
        )
      : rawItems.map((item, index) => {
          const itemRaw = asRecord(item);
          return {
            anchorType: "unknown" as const,
            id:
              stringValue(
                itemRaw.id,
                itemRaw.message_id,
                itemRaw.operation_id,
              ) ?? `unknown-${index}`,
            position:
              numberValue(itemRaw.position, itemRaw.anchor_position) ??
              Number.MAX_SAFE_INTEGER,
            rawType: `schema@v${schemaVersion}`,
          };
        }),
    nextBeforePosition,
    eventHighWatermark: schemaCurrent
      ? stringValue(raw.eventHighWatermark, raw.event_high_watermark)
      : undefined,
    hasMore:
      typeof raw.hasMore === "boolean"
        ? raw.hasMore
        : typeof raw.has_more === "boolean"
          ? raw.has_more
          : nextBeforePosition !== undefined,
    revision: numberValue(raw.revision),
    writesSafe: schemaCurrent,
  };
}

export function normalizeEventPage(value: unknown): WorkspaceEventPage {
  const raw = unwrap(value);
  const source = Array.isArray(value)
    ? value
    : Array.isArray(raw.events)
      ? raw.events
      : [];
  const schemaVersion =
    envelopeSchemaVersion(value) ??
    numberValue(raw.schemaVersion, raw.schema_version);
  const schemaSupported =
    schemaVersion === undefined || schemaVersion === SUPPORTED_SCHEMA_VERSION;
  const normalized = schemaSupported
    ? schemaVersion === SUPPORTED_SCHEMA_VERSION
      ? source.map(normalizeCursorOperationEvent)
      : source.map((event) =>
          normalizeOperationEvent(event, { allowMissingSchema: true }),
        )
    : source.map(() => null);
  const highWatermark = canonicalCursor(
    raw.eventHighWatermark ?? raw.event_high_watermark ?? raw.cursor,
  );
  const highWatermarkOrdinal = cursorOrdinal(highWatermark);
  const cursorSafe =
    schemaVersion === SUPPORTED_SCHEMA_VERSION &&
    highWatermark !== undefined &&
    normalized.every((event) => {
      if (!event?.cursor || highWatermarkOrdinal === undefined) return false;
      const eventOrdinal = cursorOrdinal(event.cursor);
      return eventOrdinal !== undefined && eventOrdinal <= highWatermarkOrdinal;
    });
  return {
    events: normalized.filter(
      (event): event is WorkspaceOperationEvent => event !== null,
    ),
    eventHighWatermark: cursorSafe ? highWatermark : undefined,
    cursorSafe,
  };
}

export function normalizeWorkspaceActionReceipt(
  value: unknown,
): WorkspaceActionReceipt {
  const envelope = asRecord(value);
  const data =
    Object.keys(asRecord(envelope.data)).length > 0
      ? asRecord(envelope.data)
      : envelope;
  const timelineValue = asRecord(data.timeline);
  if (Object.keys(timelineValue).length === 0) {
    return { contract: "legacy", timeline: normalizeTimelinePage(value) };
  }

  const schemaVersion = safeIntegerValue(
    data.schemaVersion ?? data.schema_version,
    1,
  );
  const receiptId = stringValue(data.receiptId, data.receipt_id);
  const operationId = stringValue(data.operationId, data.operation_id);
  const status = stringValue(data.status);
  const duplicate = booleanValue(data.duplicate);
  const normalizedTimeline = normalizeTimelinePage(timelineValue);
  const valid =
    schemaVersion === SUPPORTED_SCHEMA_VERSION &&
    receiptId !== undefined &&
    operationId !== undefined &&
    status !== undefined &&
    duplicate !== undefined &&
    normalizedTimeline.writesSafe;
  return {
    contract: "v1",
    schemaVersion,
    receiptId,
    operationId,
    status,
    duplicate,
    timeline: valid
      ? normalizedTimeline
      : {
          ...normalizedTimeline,
          eventHighWatermark: undefined,
          writesSafe: false,
        },
  };
}

function eventKey(event: WorkspaceOperationEvent): string {
  return `${event.operationId}:${event.sequence}`;
}

function sortAnchors(
  items: WorkspaceTimelineAnchor[],
): WorkspaceTimelineAnchor[] {
  return [...items].sort(
    (a, b) => a.position - b.position || a.id.localeCompare(b.id),
  );
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

function latestEventSequence(operation: WorkspaceOperationAnchor): number {
  return operation.events.reduce(
    (latest, event) => Math.max(latest, event.sequence),
    -1,
  );
}

function mergeOperationSnapshot(
  current: WorkspaceOperationAnchor,
  incoming: WorkspaceOperationAnchor,
): WorkspaceOperationAnchor {
  const currentVersion = current.version ?? -1;
  const incomingVersion = incoming.version ?? -1;
  const incomingIsOlder =
    incomingVersion < currentVersion ||
    (incomingVersion === currentVersion &&
      latestEventSequence(incoming) < latestEventSequence(current));
  const primary = incomingIsOlder ? current : incoming;
  const eventsByKey = new Map(
    [...current.events, ...incoming.events].map((event) => [
      eventKey(event),
      event,
    ]),
  );
  return {
    ...primary,
    // Position belongs to the durable snapshot anchor, never to an event.
    position: current.position,
    events: [...eventsByKey.values()].sort((a, b) => a.sequence - b.sequence),
  };
}

function incomingPageCanRefreshLiveAttachments(
  projection: WorkspaceProjection,
  page: WorkspaceTimelinePage,
): boolean {
  if (projection.revision !== undefined && page.revision === undefined) {
    return false;
  }
  if (
    projection.revision !== undefined &&
    page.revision !== undefined &&
    page.revision < projection.revision
  ) {
    return false;
  }
  const currentCursor = cursorOrdinal(projection.eventHighWatermark);
  const incomingCursor = cursorOrdinal(page.eventHighWatermark);
  if (currentCursor !== undefined && incomingCursor === undefined) {
    return false;
  }
  return !(
    currentCursor !== undefined &&
    incomingCursor !== undefined &&
    incomingCursor < currentCursor
  );
}

function mergeMessageSnapshot(
  current: WorkspaceMessageAnchor,
  incoming: WorkspaceMessageAnchor,
  refreshLiveAttachments: boolean,
): WorkspaceMessageAnchor {
  if (!refreshLiveAttachments) return current;
  const incomingById = new Map(
    incoming.attachments.map((attachment) => [attachment.id, attachment]),
  );
  return {
    ...current,
    attachments: current.attachments.map((snapshot) => {
      const live = incomingById.get(snapshot.id);
      if (!live) return snapshot;
      return {
        ...snapshot,
        // Snapshot identity and metadata never follow later asset-row changes.
        mimeType: snapshot.mimeType ?? live.mimeType,
        size: snapshot.size ?? live.size,
        sha256: snapshot.sha256 ?? live.sha256,
        // The live projection is replaceable and may intentionally clear stale
        // URLs or failures after processing/retry transitions.
        status: live.currentState,
        currentState: live.currentState,
        error: live.error,
        failureCode: live.failureCode,
        downloadUrl: live.downloadUrl,
        previewUrl: live.previewUrl,
        downloadAction: live.downloadAction,
        previewAction: live.previewAction,
      };
    }),
  };
}

function monotonicRevision(
  current?: number,
  incoming?: number,
): number | undefined {
  if (current === undefined) return incoming;
  if (incoming === undefined) return current;
  return Math.max(current, incoming);
}

export function mergeTimelinePage(
  projection: WorkspaceProjection,
  page: WorkspaceTimelinePage,
  options: {
    preservePagination?: boolean;
    preserveEventHighWatermark?: boolean;
  } = {},
): WorkspaceProjection {
  const byId = new Map(projection.items.map((item) => [item.id, item]));
  const refreshLiveAttachments = incomingPageCanRefreshLiveAttachments(
    projection,
    page,
  );
  for (const item of page.items) {
    const current = byId.get(item.id);
    byId.set(
      item.id,
      current?.anchorType === "operation" && item.anchorType === "operation"
        ? mergeOperationSnapshot(current, item)
        : current?.anchorType === "message" && item.anchorType === "message"
          ? mergeMessageSnapshot(current, item, refreshLiveAttachments)
          : item,
    );
  }
  const seenEventKeys = { ...projection.seenEventKeys };
  for (const item of page.items) {
    if (item.anchorType !== "operation") continue;
    for (const event of item.events) {
      seenEventKeys[eventKey(event)] = true;
      if (event.id) seenEventKeys[event.id] = true;
    }
  }
  const projectedOperationIds = new Set(
    [...byId.values()]
      .filter(
        (item): item is WorkspaceOperationAnchor =>
          item.anchorType === "operation",
      )
      .map((item) => item.operationId),
  );
  const orphanOperationIds = projection.orphanOperationIds.filter(
    (operationId) => !projectedOperationIds.has(operationId),
  );
  const rebaseRequired = orphanOperationIds.length > 0;
  const writesSafe =
    projection.writesSafe === undefined
      ? page.writesSafe
      : projection.writesSafe && page.writesSafe;
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
      : options.preserveEventHighWatermark
        ? projection.eventHighWatermark
        : monotonicEventHighWatermark(
            projection.eventHighWatermark,
            page.eventHighWatermark,
          ),
    revision: monotonicRevision(projection.revision, page.revision),
    hasMore: options.preservePagination ? projection.hasMore : page.hasMore,
    seenEventKeys,
    rebaseRequired,
    orphanOperationIds,
    writesSafe,
  };
}

function patchOperation(
  operation: WorkspaceOperationAnchor,
  event: WorkspaceOperationEvent,
): WorkspaceOperationAnchor {
  const currentSequence = latestEventSequence(operation);
  const currentVersion = operation.version;
  const eventIsOlder =
    event.operationVersion !== undefined &&
    currentVersion !== undefined &&
    event.operationVersion < currentVersion;
  const eventCanProject =
    !eventIsOlder &&
    (event.operationVersion !== undefined &&
    currentVersion !== undefined &&
    event.operationVersion > currentVersion
      ? true
      : event.sequence > currentSequence);
  const events = [...operation.events, event].sort(
    (a, b) => a.sequence - b.sequence,
  );
  if (!eventCanProject) return { ...operation, events };
  return {
    ...operation,
    status: event.status ?? operation.status,
    // The event version is the operation row's post-transition CAS version.
    version: event.operationVersion ?? operation.version,
    progress: event.progress ?? operation.progress,
    error: event.error ?? operation.error,
    retryable: event.retryable ?? operation.retryable,
    capacityWaiting: event.capacityWaiting ?? operation.capacityWaiting,
    queuePosition: event.queuePosition ?? operation.queuePosition,
    result: event.result ?? operation.result,
    updatedAt: event.createdAt ?? operation.updatedAt,
    events,
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
      .filter(
        (item): item is WorkspaceOperationAnchor =>
          item.anchorType === "operation",
      )
      .map((item) => item.operationId),
  );
  const orphanOperationIds = new Set(
    projection.orphanOperationIds.filter(
      (operationId) => !projectedOperationIds.has(operationId),
    ),
  );
  for (const event of page.events) {
    const key = eventKey(event);
    if (seenEventKeys[key] || (event.id && seenEventKeys[event.id])) continue;
    if (!projectedOperationIds.has(event.operationId)) {
      const isDurablyOffscreen =
        projection.messageBeforePosition !== undefined &&
        event.operationAnchorPosition !== undefined &&
        event.operationAnchorPosition < projection.messageBeforePosition;
      if (isDurablyOffscreen) {
        seenEventKeys[key] = true;
        if (event.id) seenEventKeys[event.id] = true;
        continue;
      }
      // Do not acknowledge an event until its top-level anchor is present. The
      // hook observes rebaseRequired, reloads/backfills the timeline, and polls
      // again from the unchanged high-watermark.
      orphanOperationIds.add(event.operationId);
      continue;
    }
    seenEventKeys[key] = true;
    if (event.id) seenEventKeys[event.id] = true;
    const pending = pendingByOperation.get(event.operationId) ?? [];
    pending.push(event);
    pendingByOperation.set(event.operationId, pending);
  }
  const items = projection.items.map((item) => {
    if (item.anchorType !== "operation") return item;
    const events = pendingByOperation.get(item.operationId);
    if (!events) return item;
    return events
      .sort((a, b) => a.sequence - b.sequence)
      .reduce(patchOperation, item);
  });
  const unresolvedOperationIds = [...orphanOperationIds].sort();
  const rebaseRequired = unresolvedOperationIds.length > 0;
  return {
    ...projection,
    items,
    eventHighWatermark:
      rebaseRequired || page.cursorSafe === false
        ? projection.eventHighWatermark
        : monotonicEventHighWatermark(
            projection.eventHighWatermark,
            page.eventHighWatermark,
          ),
    seenEventKeys,
    rebaseRequired,
    orphanOperationIds: unresolvedOperationIds,
  };
}
