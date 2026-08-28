import assert from "node:assert/strict";
import test from "node:test";

import {
  applyOperationEvents,
  createWorkspaceProjection,
  mergeTimelinePage,
  normalizeCursorOperationEvent,
  normalizeEventPage,
  normalizeOperationEvent,
  normalizeTimelineAnchor,
  normalizeTimelinePage,
  normalizeWorkspaceActionReceipt,
} from "./timelineProjection.ts";

test("normalizes and permanently projects the bootstrap user message inputs", () => {
  const page = normalizeTimelinePage({
    schema_version: 1,
    items: [
      {
        anchor_type: "operation",
        id: "anchor-op",
        operation_id: "op-1",
        position: 20,
        title: "프레젠테이션 생성",
        status: "waiting_for_assets",
      },
      {
        anchor_type: "message",
        id: "message-1",
        position: 10,
        role: "user",
        content: "분기 전략을 8장으로 만들어 줘",
        attachments: [
          { asset_id: "asset-1", filename: "strategy.pdf", status: "scanning" },
        ],
        generation_settings: { slide_count: 8, language: "ko" },
      },
    ],
    next_before_position: 5,
    event_high_watermark: "event-4",
    has_more: true,
    revision: 7,
  });
  const projection = mergeTimelinePage(createWorkspaceProjection(), page);

  assert.deepEqual(
    projection.items.map((item) => item.id),
    ["message-1", "anchor-op"],
  );
  assert.equal(projection.messageBeforePosition, 5);
  assert.equal(projection.writesSafe, true);
  assert.equal(projection.eventHighWatermark, "event-4");
  assert.equal(projection.revision, 7);
  const message = projection.items[0];
  assert.equal(message.anchorType, "message");
  if (message.anchorType !== "message") return;
  assert.deepEqual(message.attachments[0], {
    id: "asset-1",
    name: "strategy.pdf",
    status: "scanning",
    stateAtTime: "scanning",
    currentState: "scanning",
    mimeType: undefined,
    size: undefined,
    error: undefined,
    failureCode: undefined,
    downloadUrl: undefined,
    previewUrl: undefined,
  });
  assert.deepEqual(message.generationSettings, {
    slide_count: 8,
    language: "ko",
  });
});

test("normalizes attachment snapshots separately from live state across lifecycle wire shapes", () => {
  const anchor = normalizeTimelineAnchor(
    {
      anchor_type: "message",
      id: "message-assets",
      position: 1,
      role: "user",
      content: "첨부 상태 확인",
      attachments: [
        {
          asset_id: "ready",
          file_name: "ready.pdf",
          state_at_time: "ready",
          current_state: "ready",
          mime_type: "application/pdf",
          size_bytes: 2_048,
          download_url: "/api/workspace/assets/ready/download",
          preview_url: "https://files.example.test/ready",
        },
        {
          id: "failed",
          filename: "failed.csv",
          stateAtTime: "processing",
          currentState: "failed",
          safeFailure: {
            code: "extract_failed",
            safe_message: "파일 내용을 읽지 못했습니다.",
          },
        },
        {
          id: "quarantined",
          name: "unsafe.zip",
          snapshot_state: "uploaded",
          live_state: "quarantined",
        },
        {
          id: "expired",
          name: "old.pptx",
          asset_state: "ready",
          current_status: "expired",
        },
        { id: "deleted", name: "gone.docx", status: "deleted" },
        {
          asset_id: "deleted-row",
          filename: "removed.pdf",
          status: "ready",
          current_state: null,
          unavailable_reason: "workspace_asset_deleted",
          safe_failure: {
            failure_code: "workspace_asset_deleted",
            safe_message: "이 첨부 파일은 삭제되어 다시 첨부해야 합니다.",
            retryable: false,
          },
          download_url: null,
          preview_url: null,
        },
      ],
    },
    0,
  );

  assert.equal(anchor.anchorType, "message");
  if (anchor.anchorType !== "message") return;
  assert.deepEqual(
    anchor.attachments.map(({ id, stateAtTime, currentState }) => ({
      id,
      stateAtTime,
      currentState,
    })),
    [
      { id: "ready", stateAtTime: "ready", currentState: "ready" },
      { id: "failed", stateAtTime: "processing", currentState: "failed" },
      {
        id: "quarantined",
        stateAtTime: "uploaded",
        currentState: "quarantined",
      },
      { id: "expired", stateAtTime: "ready", currentState: "expired" },
      { id: "deleted", stateAtTime: "deleted", currentState: "deleted" },
      { id: "deleted-row", stateAtTime: "ready", currentState: "deleted" },
    ],
  );
  assert.equal(anchor.attachments[0].mimeType, "application/pdf");
  assert.equal(anchor.attachments[0].size, 2_048);
  assert.equal(
    anchor.attachments[0].downloadUrl,
    "/api/workspace/assets/ready/download",
  );
  assert.equal(anchor.attachments[1].failureCode, "extract_failed");
  assert.equal(anchor.attachments[1].error, "파일 내용을 읽지 못했습니다.");
  assert.equal(anchor.attachments[5].failureCode, "workspace_asset_deleted");
  assert.equal(
    anchor.attachments[5].error,
    "이 첨부 파일은 삭제되어 다시 첨부해야 합니다.",
  );
  assert.equal(anchor.attachments[5].downloadUrl, undefined);
});

test("operation events update one card without creating or reordering anchors", () => {
  const initial = mergeTimelinePage(
    createWorkspaceProjection(),
    normalizeTimelinePage({
      schema_version: 1,
      items: [
        {
          type: "message",
          id: "m1",
          position: 10,
          role: "user",
          content: "start",
        },
        {
          type: "operation",
          id: "a1",
          operation_id: "op-1",
          position: 20,
          status: "running",
        },
        {
          type: "message",
          id: "m2",
          position: 30,
          role: "assistant",
          content: "noted",
        },
      ],
      event_high_watermark: "cursor-1",
    }),
  );
  const updated = applyOperationEvents(
    initial,
    normalizeEventPage({
      schema_version: 1,
      events: [
        {
          schema_version: 1,
          id: "event-2",
          cursor: "2",
          operation_id: "op-1",
          operation_version: 4,
          sequence: 2,
          type: "completed",
          status: "succeeded",
          progress: 100,
        },
        {
          schema_version: 1,
          id: "event-orphan",
          cursor: "3",
          operation_id: "op-not-projected",
          operation_version: 9,
          sequence: 1,
          type: "progress",
          status: "running",
        },
      ],
      event_high_watermark: "3",
    }),
  );

  assert.deepEqual(
    updated.items.map((item) => [item.id, item.position]),
    [
      ["m1", 10],
      ["a1", 20],
      ["m2", 30],
    ],
  );
  assert.equal(updated.items.length, 3);
  assert.equal(updated.eventHighWatermark, "cursor-1");
  assert.equal(updated.rebaseRequired, true);
  assert.deepEqual(updated.orphanOperationIds, ["op-not-projected"]);
  assert.equal(updated.seenEventKeys["event-orphan"], undefined);
  assert.equal(updated.messageBeforePosition, initial.messageBeforePosition);
  const operation = updated.items[1];
  assert.equal(operation.anchorType, "operation");
  if (operation.anchorType === "operation") {
    assert.equal(operation.status, "succeeded");
    assert.equal(operation.version, 4);
    assert.equal(operation.progress, 100);
    assert.deepEqual(
      operation.events.map((event) => event.id),
      ["event-2"],
    );
  }

  const rebased = mergeTimelinePage(
    updated,
    normalizeTimelinePage({
      schema_version: 1,
      items: [
        {
          type: "operation",
          id: "a-orphan",
          operation_id: "op-not-projected",
          position: 40,
          status: "running",
          version: 9,
          events: [
            {
              id: "event-orphan",
              operation_id: "op-not-projected",
              operation_version: 9,
              sequence: 1,
              status: "running",
            },
          ],
        },
      ],
      event_high_watermark: "cursor-3",
    }),
    { preservePagination: true },
  );
  assert.equal(rebased.rebaseRequired, false);
  assert.deepEqual(rebased.orphanOperationIds, []);
  assert.equal(rebased.eventHighWatermark, "cursor-3");
  assert.equal(rebased.seenEventKeys["event-orphan"], true);
});

test("replayed and out-of-order events are deduplicated and sequence ordered", () => {
  const initial = mergeTimelinePage(
    createWorkspaceProjection(),
    normalizeTimelinePage({
      schema_version: 1,
      items: [
        {
          type: "operation",
          id: "a1",
          operation_id: "op-1",
          position: 1,
          status: "running",
        },
      ],
    }),
  );
  const events = normalizeEventPage([
    { id: "e2", operation_id: "op-1", sequence: 2, progress: 50 },
    { id: "e1", operation_id: "op-1", sequence: 1, progress: 10 },
    { id: "e2", operation_id: "op-1", sequence: 2, progress: 50 },
  ]);
  const once = applyOperationEvents(initial, events);
  const twice = applyOperationEvents(once, events);
  const operation = twice.items[0];

  assert.equal(operation.anchorType, "operation");
  if (operation.anchorType === "operation") {
    assert.deepEqual(
      operation.events.map((event) => event.sequence),
      [1, 2],
    );
    assert.equal(operation.progress, 50);
  }
});

test("message pagination and event high-watermark remain separate", () => {
  const initial = mergeTimelinePage(
    createWorkspaceProjection(),
    normalizeTimelinePage({
      schema_version: 1,
      items: [{ type: "message", id: "new", position: 20, role: "user" }],
      next_before_position: 20,
      event_high_watermark: "event-10",
      has_more: true,
    }),
  );
  const older = mergeTimelinePage(
    initial,
    normalizeTimelinePage({
      schema_version: 1,
      items: [{ type: "message", id: "old", position: 10, role: "user" }],
      next_before_position: 10,
      has_more: true,
    }),
  );
  const commandResponse = mergeTimelinePage(
    older,
    normalizeTimelinePage({
      schema_version: 1,
      items: [{ type: "operation", id: "op", position: 30, status: "running" }],
      event_high_watermark: "event-11",
    }),
    { preservePagination: true },
  );

  assert.equal(commandResponse.messageBeforePosition, 10);
  assert.equal(commandResponse.eventHighWatermark, "event-11");
  assert.equal(commandResponse.hasMore, true);
});

test("unknown timeline payloads fail closed", () => {
  const projection = mergeTimelinePage(
    createWorkspaceProjection(),
    normalizeTimelinePage({
      items: [
        { type: "surprise", id: "x", position: 1, url: "file:///secret" },
      ],
    }),
  );
  assert.deepEqual(projection.items[0], {
    anchorType: "unknown",
    id: "x",
    position: 1,
    rawType: "surprise",
  });
  assert.equal(projection.writesSafe, false);
});

test("normalizes nested action timelines and backend event state aliases", () => {
  const page = normalizeTimelinePage({
    data: {
      schema_version: 1,
      receipt_id: "receipt-1",
      timeline: {
        items: [
          {
            kind: "operation_anchor",
            message_id: "anchor-1",
            operation_id: "op-1",
            retry_of_operation_id: "op-source",
            position: 2,
            state: "awaiting_approval",
          },
        ],
        event_high_watermark: "12",
      },
    },
  });
  const projection = mergeTimelinePage(createWorkspaceProjection(), page);
  const updated = applyOperationEvents(
    projection,
    normalizeEventPage({
      schema_version: 1,
      events: [
        {
          schema_version: 1,
          id: "event-13",
          cursor: "13",
          operation_id: "op-1",
          operation_version: 1,
          sequence: 13,
          type: "progress",
          payload: { percent: 75 },
        },
      ],
      event_high_watermark: "13",
    }),
  );
  const operation = updated.items[0];

  assert.equal(operation.anchorType, "operation");
  if (operation.anchorType === "operation") {
    assert.equal(operation.status, "awaiting_approval");
    assert.equal(operation.retryOfOperationId, "op-source");
    assert.equal(operation.progress, 75);
  }
});

test("normalizes non-retryable errors from safe, nested, and event wire shapes", () => {
  const shapes = [
    { retryable: false, error: "deterministic_failure" },
    { error: { safe_message: "잘못된 입력입니다.", retryable: false } },
    {
      error_message: "다시 실행할 수 없습니다.",
      error_detail: { retryable: false },
    },
  ];

  for (const [index, shape] of shapes.entries()) {
    const anchor = normalizeTimelineAnchor(
      {
        anchor_type: "operation",
        operation_id: `op-non-retryable-${index}`,
        position: index + 1,
        status: "failed",
        ...shape,
      },
      index,
    );
    assert.equal(anchor.anchorType, "operation");
    if (anchor.anchorType === "operation") {
      assert.equal(anchor.retryable, false);
      assert.equal(
        anchor.error,
        [undefined, "잘못된 입력입니다.", "다시 실행할 수 없습니다."][index],
      );
    }
  }

  const initial = mergeTimelinePage(
    createWorkspaceProjection(),
    normalizeTimelinePage({
      items: [
        {
          anchor_type: "operation",
          operation_id: "op-event-gate",
          position: 1,
          status: "running",
        },
      ],
    }),
  );
  const updated = applyOperationEvents(
    initial,
    normalizeEventPage({
      schema_version: 1,
      events: [
        {
          schema_version: 1,
          id: "event-non-retryable",
          cursor: "1",
          operation_id: "op-event-gate",
          operation_version: 1,
          sequence: 1,
          type: "operation.error",
          payload: {
            status: "failed",
            error: { safe_message: "결정적 오류입니다.", retryable: false },
          },
        },
      ],
      event_high_watermark: "1",
    }),
  );
  const operation = updated.items[0];
  assert.equal(operation.anchorType, "operation");
  if (operation.anchorType === "operation") {
    assert.equal(operation.status, "failed");
    assert.equal(operation.retryable, false);
    assert.equal(operation.error, "결정적 오류입니다.");
  }
});

test("new progress clears a prior retryable failure from the live operation card", () => {
  const initial = mergeTimelinePage(
    createWorkspaceProjection(),
    normalizeTimelinePage({
      schema_version: 1,
      items: [
        {
          anchor_type: "operation",
          operation_id: "op-recovered-progress",
          position: 1,
          status: "running",
        },
      ],
    }),
  );
  const updated = applyOperationEvents(
    initial,
    normalizeEventPage({
      schema_version: 1,
      events: [
        {
          schema_version: 1,
          id: "event-retryable-failure",
          cursor: "1",
          operation_id: "op-recovered-progress",
          operation_version: 1,
          operation_anchor_position: 1,
          sequence: 1,
          type: "operation.error",
          error: "workspace_request_failed",
          safe_message: "The workspace request could not be completed.",
          retryable: true,
        },
        {
          schema_version: 1,
          id: "event-recovered-progress",
          cursor: "2",
          operation_id: "op-recovered-progress",
          operation_version: 2,
          operation_anchor_position: 1,
          sequence: 2,
          type: "operation.progress",
          progress: 70,
          error: null,
          retryable: null,
        },
      ],
      event_high_watermark: "2",
    }),
  );
  const operation = updated.items[0];

  assert.equal(operation.anchorType, "operation");
  if (operation.anchorType === "operation") {
    assert.equal(operation.status, "running");
    assert.equal(operation.progress, 70);
    assert.equal(operation.error, undefined);
    assert.equal(operation.retryable, undefined);
  }
});

test("preserves actionable operation states and only normalizes wire aliases", () => {
  const states = [
    "queued",
    "blocked",
    "cancelling",
    "dead_letter",
    "approval_required",
  ];
  for (const state of states) {
    const anchor = normalizeTimelineAnchor(
      {
        kind: "operation_anchor",
        operation_id: `op-${state}`,
        position: 1,
        state,
      },
      0,
    );
    assert.equal(anchor.anchorType, "operation");
    if (anchor.anchorType === "operation") assert.equal(anchor.status, state);
  }

  const deadLettered = normalizeOperationEvent({
    schema_version: 1,
    id: "event-dead",
    operation_id: "op-dead",
    sequence: 1,
    type: "dead_lettered",
  });
  assert.equal(deadLettered?.status, "dead_letter");

  const versioned = normalizeOperationEvent({
    schema_version: 1,
    id: "event-versioned",
    operation_id: "op-versioned",
    operation_version: 12,
    sequence: 3,
    type: "progress",
  });
  assert.equal(versioned?.operationVersion, 12);
});

test("never projects raw operation or attachment error fields as user-safe text", () => {
  const message = normalizeTimelineAnchor(
    {
      anchor_type: "message",
      id: "message-safe-errors",
      position: 1,
      role: "user",
      content: "첨부",
      attachments: [
        {
          asset_id: "asset-safe-errors",
          file_name: "source.pdf",
          status: "failed",
          error: "storage path C:/private/source.pdf",
          error_message: "stack trace must stay private",
          safe_message: "파일을 처리하지 못했습니다.",
        },
      ],
    },
    0,
  );
  assert.equal(message.anchorType, "message");
  if (message.anchorType === "message") {
    assert.equal(message.attachments[0].error, "파일을 처리하지 못했습니다.");
  }

  const operation = normalizeTimelineAnchor(
    {
      anchor_type: "operation",
      operation_id: "operation-safe-errors",
      position: 2,
      status: "failed",
      error: "database connection string",
      error_detail: "internal traceback",
    },
    1,
  );
  assert.equal(operation.anchorType, "operation");
  if (operation.anchorType === "operation")
    assert.equal(operation.error, undefined);
});

test("live failure events expose only explicit safe text or a generic fallback", () => {
  const explicitlySafe = normalizeOperationEvent({
    schema_version: 1,
    id: "event-safe-failure",
    operation_id: "operation-safe-failure",
    operation_version: 2,
    sequence: 3,
    type: "operation.failed",
    message: "provider traceback at C:/private/presentation.pptx",
    error: {
      code: "provider_failed",
      message: "secret provider response",
      safe_message: "작업 서비스가 일시적으로 응답하지 않았습니다.",
    },
  });
  assert.equal(
    explicitlySafe?.message,
    "작업 서비스가 일시적으로 응답하지 않았습니다.",
  );
  assert.equal(explicitlySafe?.error, explicitlySafe?.message);
  assert.doesNotMatch(
    explicitlySafe?.message ?? "",
    /private|traceback|secret/i,
  );

  const fallback = normalizeOperationEvent({
    schema_version: 1,
    id: "event-generic-failure",
    operation_id: "operation-generic-failure",
    operation_version: 1,
    sequence: 1,
    type: "operation.failed",
    message: "database password and internal stack",
    error_detail: "C:/private/provider.log",
  });
  assert.equal(fallback?.message, "작업을 완료하지 못했습니다.");
  assert.equal(fallback?.error, "작업을 완료하지 못했습니다.");
  assert.doesNotMatch(fallback?.message ?? "", /password|stack|private/i);
});

test("null error fields on live progress events do not project a failure", () => {
  const progress = normalizeOperationEvent({
    schema_version: 1,
    id: "event-progress-null-error",
    operation_id: "operation-progress-null-error",
    operation_version: 3,
    sequence: 4,
    type: "operation.progress",
    status: "running",
    message: "Presentation quality review in progress.",
    progress: 29,
    error: null,
    error_detail: null,
    payload: { error: null, error_detail: null },
  });

  assert.equal(progress?.status, "running");
  assert.equal(progress?.progress, 29);
  assert.equal(progress?.message, "Presentation quality review in progress.");
  assert.equal(progress?.error, undefined);
});

test("late events and stale snapshots cannot regress operation state or revision", () => {
  const live = applyOperationEvents(
    mergeTimelinePage(
      createWorkspaceProjection(),
      normalizeTimelinePage({
        schema_version: 1,
        items: [
          {
            type: "operation",
            id: "anchor-1",
            operation_id: "operation-1",
            position: 20,
            status: "running",
            version: 4,
          },
        ],
        revision: 10,
      }),
    ),
    normalizeEventPage({
      schema_version: 1,
      events: [
        {
          schema_version: 1,
          id: "event-current",
          cursor: "20",
          operation_id: "operation-1",
          operation_version: 5,
          sequence: 5,
          type: "completed",
          status: "succeeded",
          progress: 100,
          result: { revision: 10 },
        },
      ],
      event_high_watermark: "20",
    }),
  );

  const withLateEvent = applyOperationEvents(
    live,
    normalizeEventPage({
      schema_version: 1,
      events: [
        {
          schema_version: 1,
          id: "event-late",
          cursor: "21",
          operation_id: "operation-1",
          operation_version: 4,
          sequence: 99,
          type: "progress",
          status: "running",
          progress: 15,
        },
      ],
      event_high_watermark: "21",
    }),
  );
  const afterStaleSnapshot = mergeTimelinePage(
    withLateEvent,
    normalizeTimelinePage({
      schema_version: 1,
      items: [
        {
          type: "operation",
          id: "anchor-1",
          operation_id: "operation-1",
          position: 999,
          status: "failed",
          version: 3,
          progress: 2,
        },
      ],
      revision: 8,
    }),
    { preservePagination: true },
  );
  const operation = afterStaleSnapshot.items[0];

  assert.equal(operation.anchorType, "operation");
  if (operation.anchorType === "operation") {
    assert.equal(operation.status, "succeeded");
    assert.equal(operation.version, 5);
    assert.equal(operation.progress, 100);
    assert.equal(operation.position, 20);
    assert.deepEqual(
      operation.events.map((event) => event.sequence),
      [5, 99],
    );
  }
  assert.equal(afterStaleSnapshot.revision, 10);
  assert.equal(afterStaleSnapshot.eventHighWatermark, "21");
});

test("historical pagination never lowers the live event high-watermark", () => {
  const current = mergeTimelinePage(
    createWorkspaceProjection(),
    normalizeTimelinePage({
      schema_version: 1,
      items: [{ type: "message", id: "new", position: 20, role: "user" }],
      event_high_watermark: "100",
      next_before_position: 20,
      has_more: true,
    }),
  );
  const paginated = mergeTimelinePage(
    current,
    normalizeTimelinePage({
      schema_version: 1,
      items: [{ type: "message", id: "old", position: 10, role: "user" }],
      event_high_watermark: "12",
      next_before_position: 10,
      has_more: false,
    }),
    { preserveEventHighWatermark: true },
  );

  assert.equal(paginated.eventHighWatermark, "100");
  assert.equal(paginated.messageBeforePosition, 10);
  assert.equal(paginated.hasMore, false);
});

test("unsupported schema versions render fallbacks and cannot acknowledge events", () => {
  const timeline = normalizeTimelinePage({
    schema_version: 2,
    items: [{ type: "message", id: "future", position: 1, content: "future" }],
    event_high_watermark: "99",
  });
  assert.equal(timeline.items[0]?.anchorType, "unknown");
  assert.equal(timeline.eventHighWatermark, undefined);
  assert.equal(timeline.writesSafe, false);

  const events = normalizeEventPage({
    schema_version: 2,
    events: [{ operation_id: "operation-1", sequence: 1 }],
    event_high_watermark: "99",
  });
  assert.deepEqual(events.events, []);
  assert.equal(events.cursorSafe, false);
  assert.equal(events.eventHighWatermark, undefined);

  const nestedTimeline = normalizeTimelinePage({
    schema_version: 2,
    data: {
      timeline: {
        items: [{ type: "message", id: "nested-future", position: 2 }],
        event_high_watermark: "100",
      },
    },
  });
  assert.equal(nestedTimeline.items[0]?.anchorType, "unknown");
  assert.equal(nestedTimeline.eventHighWatermark, undefined);

  const nestedEvents = normalizeEventPage({
    schema_version: 2,
    data: {
      events: [{ operation_id: "operation-1", sequence: 2 }],
      event_high_watermark: "100",
    },
  });
  assert.equal(nestedEvents.cursorSafe, false);
  assert.equal(nestedEvents.eventHighWatermark, undefined);
});

test("an explicitly unknown operation anchor renders a safe fallback", () => {
  const anchor = normalizeTimelineAnchor(
    {
      type: "future_operation",
      id: "future-operation",
      operation_id: "operation-future",
      position: 9,
      status: "running",
    },
    0,
  );

  assert.equal(anchor.anchorType, "unknown");
  if (anchor.anchorType === "unknown")
    assert.equal(anchor.rawType, "future_operation");
});

test("normalizes server-authoritative action availability with client-localized copy", () => {
  const anchor = normalizeTimelineAnchor(
    {
      type: "operation",
      schema_version: 1,
      operation_type: "presentation.edit",
      id: "anchor-actions",
      operation_id: "operation-actions",
      position: 1,
      status: "awaiting_approval",
      allowed_actions: [
        {
          id: "approve",
          label: "검토 승인",
          variant: "primary",
          enabled: true,
        },
        {
          id: "cancel",
          label: "취소",
          variant: "danger",
          enabled: false,
          reason: "잠김",
        },
        "undo",
        { id: "future-action", label: "미래 액션", enabled: true },
      ],
    },
    0,
  );
  assert.equal(anchor.anchorType, "operation");
  if (anchor.anchorType !== "operation") return;
  assert.deepEqual(anchor.allowedActions, [
    {
      id: "approve",
      label: "승인",
      variant: "primary",
      enabled: true,
      reason: undefined,
    },
    {
      id: "cancel",
      label: "취소",
      variant: "danger",
      enabled: false,
      reason: "현재 이 작업을 사용할 수 없습니다.",
    },
    {
      id: "undo",
      label: "되돌리기",
      variant: "secondary",
      enabled: false,
      reason: "서버가 이 작업을 허용하지 않았습니다.",
    },
  ]);
});

test("missing-version timelines stay readable but cannot advance cursors or mutate", () => {
  const timeline = normalizeTimelinePage({
    items: [
      {
        type: "operation",
        operation_id: "legacy-export",
        operation_type: "export.pptx",
        position: 1,
        status: "succeeded",
        allowed_actions: [
          { id: "download", enabled: true },
          { id: "retry", enabled: true },
        ],
      },
    ],
    event_high_watermark: "legacy-20",
  });
  assert.equal(timeline.items[0]?.anchorType, "operation");
  if (timeline.items[0]?.anchorType === "operation") {
    assert.deepEqual(
      timeline.items[0].allowedActions.map((action) => action.id),
      ["download"],
    );
    assert.equal(timeline.items[0].allowedActions[0]?.enabled, true);
  }
  assert.equal(timeline.eventHighWatermark, undefined);
  assert.equal(timeline.writesSafe, false);

  const projection = mergeTimelinePage(createWorkspaceProjection(), timeline);
  assert.equal(projection.writesSafe, false);

  const laterVersionedPage = mergeTimelinePage(
    projection,
    normalizeTimelinePage({ schema_version: 1, items: [] }),
    { preservePagination: true },
  );
  assert.equal(laterVersionedPage.writesSafe, false);

  const events = normalizeEventPage({
    events: [
      { operation_id: "legacy-export", sequence: 1, status: "succeeded" },
    ],
    event_high_watermark: "legacy-21",
  });
  assert.equal(events.events.length, 1);
  assert.equal(events.cursorSafe, false);
  assert.equal(events.eventHighWatermark, undefined);
});

test("unknown operation kinds render a generic read-only card contract", () => {
  const anchor = normalizeTimelineAnchor(
    {
      type: "operation",
      schema_version: 1,
      operation_id: "operation-future-kind",
      operation_type: "presentation.teleport",
      title: "Do not trust this title",
      position: 10,
      status: "awaiting_approval",
      allowed_actions: [{ id: "approve", enabled: true }],
    },
    0,
  );

  assert.equal(anchor.anchorType, "operation");
  if (anchor.anchorType !== "operation") return;
  assert.equal(anchor.operationType, "presentation.teleport");
  assert.equal(anchor.title, "지원되지 않는 작업");
  assert.deepEqual(anchor.allowedActions, []);
});

test("normalizes operation audit metadata and disabled action reasons across naming conventions", () => {
  const snake = normalizeTimelineAnchor(
    {
      type: "operation",
      schema_version: 1,
      operation_type: "presentation.edit",
      id: "anchor-audit-snake",
      operation_id: "operation-audit-snake",
      position: 1,
      status: "succeeded",
      actor: {
        actor_type: "worker",
        actor_id: "worker-7",
        display_name: "렌더 작업자",
      },
      target: { slide_ids: ["slide-1"], element_ids: ["element-2"] },
      expected_revision: 4,
      result_revision: 5,
      activity_summary: "슬라이드 1장을 수정했습니다.",
      created_at: "2026-08-03T01:00:00Z",
      updated_at: "2026-08-03T01:01:00Z",
      allowed_actions: [
        {
          id: "undo",
          enabled: false,
          disabled_reason: "workspace_undo_not_supported",
        },
      ],
    },
    0,
  );
  assert.equal(snake.anchorType, "operation");
  if (snake.anchorType !== "operation") return;
  assert.deepEqual(snake.actor, {
    type: "worker",
    id: "worker-7",
    label: "렌더 작업자",
  });
  assert.deepEqual(snake.target, {
    slide_ids: ["slide-1"],
    element_ids: ["element-2"],
  });
  assert.equal(snake.expectedRevision, 4);
  assert.equal(snake.resultRevision, 5);
  assert.equal(snake.activitySummary, "슬라이드 1장을 수정했습니다.");
  assert.equal(snake.createdAt, "2026-08-03T01:00:00Z");
  assert.equal(snake.updatedAt, "2026-08-03T01:01:00Z");
  assert.deepEqual(snake.allowedActions[0], {
    id: "undo",
    label: "되돌리기",
    variant: "secondary",
    enabled: false,
    reason: "이 작업은 되돌리기를 지원하지 않습니다.",
  });

  const camel = normalizeTimelineAnchor(
    {
      type: "operation",
      schema_version: 1,
      operation_type: "presentation.edit",
      id: "anchor-audit-camel",
      operationId: "operation-audit-camel",
      position: 2,
      status: "running",
      actor: { type: "user", id: "user-1", displayName: "검토자" },
      target: { slideIds: ["slide-3"] },
      expectedRevision: 5,
      resultRevision: 6,
      activitySummary: "테마를 적용하고 있습니다.",
    },
    0,
  );
  assert.equal(camel.anchorType, "operation");
  if (camel.anchorType !== "operation") return;
  assert.deepEqual(camel.actor, {
    type: "user",
    id: "user-1",
    label: "검토자",
  });
  assert.equal(camel.expectedRevision, 5);
  assert.equal(camel.resultRevision, 6);
  assert.equal(camel.activitySummary, "테마를 적용하고 있습니다.");
});

test("cursor-bearing v1 events require the complete canonical contract", () => {
  const valid = {
    schema_version: 1,
    id: "event-1",
    cursor: "1",
    operation_id: "operation-1",
    operation_version: 0,
    operation_anchor_position: 12,
    sequence: 1,
    type: "queued",
  };
  assert.deepEqual(normalizeCursorOperationEvent(valid), {
    id: "event-1",
    cursor: "1",
    operationId: "operation-1",
    operationVersion: 0,
    operationAnchorPosition: 12,
    sequence: 1,
    type: "queued",
    status: "queued",
    message: undefined,
    progress: undefined,
    error: undefined,
    retryable: undefined,
    result: undefined,
    createdAt: undefined,
  });

  for (const invalid of [
    { ...valid, schema_version: 2 },
    { ...valid, id: "" },
    { ...valid, cursor: "01" },
    { ...valid, cursor: "event-1" },
    { ...valid, operation_id: "" },
    { ...valid, operation_version: -1 },
    { ...valid, operation_version: 1.5 },
    { ...valid, operation_anchor_position: 0 },
    { ...valid, sequence: 0 },
    { ...valid, sequence: Number.MAX_SAFE_INTEGER + 1 },
    { ...valid, type: "" },
  ]) {
    assert.equal(normalizeCursorOperationEvent(invalid), null);
  }

  const invalidPage = normalizeEventPage({
    schema_version: 1,
    events: [{ ...valid, cursor: "not-canonical" }],
    event_high_watermark: "2",
  });
  assert.equal(invalidPage.cursorSafe, false);
  assert.equal(invalidPage.eventHighWatermark, undefined);
  assert.deepEqual(invalidPage.events, []);
});

test("event high-watermarks are monotonic across snapshots and polling", () => {
  const current = mergeTimelinePage(
    createWorkspaceProjection(),
    normalizeTimelinePage({
      schema_version: 1,
      items: [],
      event_high_watermark: "100",
    }),
  );
  const afterHistoricalSnapshot = mergeTimelinePage(
    current,
    normalizeTimelinePage({
      schema_version: 1,
      items: [],
      event_high_watermark: "12",
    }),
  );
  assert.equal(afterHistoricalSnapshot.eventHighWatermark, "100");

  const afterLowerPoll = applyOperationEvents(
    afterHistoricalSnapshot,
    normalizeEventPage({
      schema_version: 1,
      events: [],
      event_high_watermark: "99",
    }),
  );
  assert.equal(afterLowerPoll.eventHighWatermark, "100");
});

test("offscreen old-operation events are acknowledged only with a durable anchor position", () => {
  const projection = mergeTimelinePage(
    createWorkspaceProjection(),
    normalizeTimelinePage({
      schema_version: 1,
      items: [{ type: "message", id: "newest", position: 100, role: "user" }],
      next_before_position: 50,
      event_high_watermark: "5",
      has_more: true,
    }),
  );
  const event = {
    schema_version: 1,
    id: "event-old",
    cursor: "6",
    operation_id: "operation-old",
    operation_version: 1,
    operation_anchor_position: 20,
    sequence: 1,
    type: "completed",
  };
  const acknowledged = applyOperationEvents(
    projection,
    normalizeEventPage({
      schema_version: 1,
      events: [event],
      event_high_watermark: "6",
    }),
  );
  assert.equal(acknowledged.rebaseRequired, false);
  assert.equal(acknowledged.eventHighWatermark, "6");
  assert.equal(acknowledged.seenEventKeys["event-old"], true);

  const unproven = applyOperationEvents(
    projection,
    normalizeEventPage({
      schema_version: 1,
      events: [{ ...event, operation_anchor_position: 50 }],
      event_high_watermark: "6",
    }),
  );
  assert.equal(unproven.rebaseRequired, true);
  assert.equal(unproven.eventHighWatermark, "5");
  assert.deepEqual(unproven.orphanOperationIds, ["operation-old"]);
  assert.equal(unproven.seenEventKeys["event-old"], undefined);
});

test("versioned action receipts preserve duplicate metadata with a legacy fallback", () => {
  const receipt = normalizeWorkspaceActionReceipt({
    schema_version: 1,
    receipt_id: "receipt-1",
    operation_id: "operation-1",
    status: "accepted",
    duplicate: true,
    timeline: {
      schema_version: 1,
      items: [],
      event_high_watermark: "8",
    },
  });
  assert.equal(receipt.contract, "v1");
  assert.equal(receipt.receiptId, "receipt-1");
  assert.equal(receipt.operationId, "operation-1");
  assert.equal(receipt.status, "accepted");
  assert.equal(receipt.duplicate, true);
  assert.equal(receipt.timeline.eventHighWatermark, "8");
  assert.equal(receipt.timeline.writesSafe, true);

  const legacy = normalizeWorkspaceActionReceipt({
    schema_version: 1,
    items: [],
    event_high_watermark: "7",
  });
  assert.equal(legacy.contract, "legacy");
  assert.equal(legacy.timeline.eventHighWatermark, "7");

  const malformed = normalizeWorkspaceActionReceipt({
    schema_version: 2,
    receipt_id: "receipt-2",
    operation_id: "operation-2",
    status: "accepted",
    duplicate: false,
    timeline: {
      schema_version: 1,
      items: [],
      event_high_watermark: "9",
    },
  });
  assert.equal(malformed.contract, "v1");
  assert.equal(malformed.timeline.writesSafe, false);
  assert.equal(malformed.timeline.eventHighWatermark, undefined);
});

test("message snapshots stay immutable while newer pages refresh live attachment state", () => {
  const initial = mergeTimelinePage(
    createWorkspaceProjection(),
    normalizeTimelinePage({
      schema_version: 1,
      revision: 4,
      event_high_watermark: "10",
      items: [
        {
          schema_version: 1,
          anchor_type: "message",
          id: "message-attachment",
          position: 1,
          role: "user",
          content: "Create from this file",
          attachments: [
            {
              asset_id: "asset-1",
              filename: "original.pdf",
              mime_type: "application/pdf",
              size_bytes: 1024,
              sha256: "a".repeat(64),
              state_at_time: "uploaded",
              current_state: "processing",
              safe_failure: {
                code: "workspace_asset_processing",
                safe_message: "Still processing",
              },
            },
          ],
        },
      ],
    }),
  );
  const refreshed = mergeTimelinePage(
    initial,
    normalizeTimelinePage({
      schema_version: 1,
      revision: 4,
      event_high_watermark: "11",
      items: [
        {
          schema_version: 1,
          anchor_type: "message",
          id: "message-attachment",
          position: 1,
          role: "user",
          content: "mutated content must not replace the snapshot",
          attachments: [
            {
              asset_id: "asset-1",
              filename: "renamed.exe",
              mime_type: "application/octet-stream",
              size_bytes: 9999,
              sha256: "b".repeat(64),
              state_at_time: "deleted",
              current_state: "ready",
              download_url: "/assets/asset-1/download",
              preview_url: "/assets/asset-1/preview",
              download_action: "download",
              preview_action: "preview",
            },
          ],
        },
      ],
    }),
  );

  const message = refreshed.items[0];
  assert.equal(message.anchorType, "message");
  if (message.anchorType !== "message") return;
  assert.equal(message.content, "Create from this file");
  assert.deepEqual(message.attachments[0], {
    id: "asset-1",
    name: "original.pdf",
    status: "ready",
    stateAtTime: "uploaded",
    currentState: "ready",
    mimeType: "application/pdf",
    size: 1024,
    sha256: "a".repeat(64),
    error: undefined,
    failureCode: undefined,
    downloadUrl: "/assets/asset-1/download",
    previewUrl: "/assets/asset-1/preview",
    downloadAction: "download",
    previewAction: "preview",
  });
});

test("a stale lower page cannot regress a message's live attachment projection", () => {
  const current = mergeTimelinePage(
    createWorkspaceProjection(),
    normalizeTimelinePage({
      schema_version: 1,
      revision: 8,
      event_high_watermark: "20",
      items: [
        {
          schema_version: 1,
          anchor_type: "message",
          id: "message-attachment",
          position: 1,
          role: "user",
          content: "Attachment",
          attachments: [
            {
              asset_id: "asset-1",
              filename: "source.pdf",
              state_at_time: "uploaded",
              current_state: "quarantined",
              safe_failure: {
                code: "workspace_asset_quarantined",
                safe_message: "This file cannot be used safely.",
              },
            },
          ],
        },
      ],
    }),
  );
  const afterStalePage = mergeTimelinePage(
    current,
    normalizeTimelinePage({
      schema_version: 1,
      revision: 7,
      event_high_watermark: "19",
      items: [
        {
          schema_version: 1,
          anchor_type: "message",
          id: "message-attachment",
          position: 1,
          role: "user",
          content: "Attachment",
          attachments: [
            {
              asset_id: "asset-1",
              filename: "source.pdf",
              state_at_time: "uploaded",
              current_state: "processing",
            },
          ],
        },
      ],
    }),
  );

  const message = afterStalePage.items[0];
  assert.equal(message.anchorType, "message");
  if (message.anchorType !== "message") return;
  assert.equal(message.attachments[0].currentState, "quarantined");
  assert.equal(message.attachments[0].failureCode, "workspace_asset_quarantined");
  assert.equal(message.attachments[0].error, "This file cannot be used safely.");
  assert.equal(afterStalePage.revision, 8);
  assert.equal(afterStalePage.eventHighWatermark, "20");
});

test("an unversioned page cannot replace a versioned live attachment projection", () => {
  const current = mergeTimelinePage(
    createWorkspaceProjection(),
    normalizeTimelinePage({
      schema_version: 1,
      revision: 8,
      event_high_watermark: "20",
      items: [
        {
          schema_version: 1,
          anchor_type: "message",
          id: "message-attachment",
          position: 1,
          role: "user",
          attachments: [
            {
              asset_id: "asset-1",
              filename: "source.pdf",
              state_at_time: "uploaded",
              current_state: "ready",
              download_url: "/assets/asset-1/download",
            },
          ],
        },
      ],
    }),
  );

  const afterUnversionedPage = mergeTimelinePage(
    current,
    normalizeTimelinePage({
      schema_version: 1,
      items: [
        {
          schema_version: 1,
          anchor_type: "message",
          id: "message-attachment",
          position: 1,
          role: "user",
          attachments: [
            {
              asset_id: "asset-1",
              filename: "source.pdf",
              state_at_time: "uploaded",
              current_state: "processing",
            },
          ],
        },
      ],
    }),
  );

  const message = afterUnversionedPage.items[0];
  assert.equal(message.anchorType, "message");
  if (message.anchorType !== "message") return;
  assert.equal(message.attachments[0].currentState, "ready");
  assert.equal(
    message.attachments[0].downloadUrl,
    "/assets/asset-1/download",
  );
  assert.equal(afterUnversionedPage.revision, 8);
  assert.equal(afterUnversionedPage.eventHighWatermark, "20");
});
