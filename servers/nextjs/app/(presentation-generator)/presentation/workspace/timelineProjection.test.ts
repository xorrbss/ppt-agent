import assert from "node:assert/strict";
import test from "node:test";

import {
  applyOperationEvents,
  createWorkspaceProjection,
  mergeTimelinePage,
  normalizeEventPage,
  normalizeOperationEvent,
  normalizeTimelineAnchor,
  normalizeTimelinePage,
} from "./timelineProjection.ts";

test("normalizes and permanently projects the bootstrap user message inputs", () => {
  const page = normalizeTimelinePage({
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

  assert.deepEqual(projection.items.map((item) => item.id), ["message-1", "anchor-op"]);
  assert.equal(projection.messageBeforePosition, 5);
  assert.equal(projection.eventHighWatermark, "event-4");
  assert.equal(projection.revision, 7);
  const message = projection.items[0];
  assert.equal(message.anchorType, "message");
  if (message.anchorType !== "message") return;
  assert.deepEqual(message.attachments[0], {
    id: "asset-1",
    name: "strategy.pdf",
    status: "scanning",
    mimeType: undefined,
    size: undefined,
    error: undefined,
  });
  assert.deepEqual(message.generationSettings, { slide_count: 8, language: "ko" });
});

test("operation events update one card without creating or reordering anchors", () => {
  const initial = mergeTimelinePage(
    createWorkspaceProjection(),
    normalizeTimelinePage({
      items: [
        { type: "message", id: "m1", position: 10, role: "user", content: "start" },
        {
          type: "operation",
          id: "a1",
          operation_id: "op-1",
          position: 20,
          status: "running",
        },
        { type: "message", id: "m2", position: 30, role: "assistant", content: "noted" },
      ],
      event_high_watermark: "cursor-1",
    }),
  );
  const updated = applyOperationEvents(
    initial,
    normalizeEventPage({
      events: [
        {
          id: "event-2",
          operation_id: "op-1",
          operation_version: 4,
          sequence: 2,
          status: "succeeded",
          progress: 100,
        },
        {
          id: "event-orphan",
          operation_id: "op-not-projected",
          operation_version: 9,
          sequence: 1,
          status: "running",
        },
      ],
      event_high_watermark: "cursor-3",
    }),
  );

  assert.deepEqual(updated.items.map((item) => [item.id, item.position]), [
    ["m1", 10],
    ["a1", 20],
    ["m2", 30],
  ]);
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
    assert.deepEqual(operation.events.map((event) => event.id), ["event-2"]);
  }

  const rebased = mergeTimelinePage(
    updated,
    normalizeTimelinePage({
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
      items: [
        { type: "operation", id: "a1", operation_id: "op-1", position: 1, status: "running" },
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
    assert.deepEqual(operation.events.map((event) => event.sequence), [1, 2]);
    assert.equal(operation.progress, 50);
  }
});

test("message pagination and event high-watermark remain separate", () => {
  const initial = mergeTimelinePage(
    createWorkspaceProjection(),
    normalizeTimelinePage({
      items: [{ type: "message", id: "new", position: 20, role: "user" }],
      next_before_position: 20,
      event_high_watermark: "event-10",
      has_more: true,
    }),
  );
  const older = mergeTimelinePage(
    initial,
    normalizeTimelinePage({
      items: [{ type: "message", id: "old", position: 10, role: "user" }],
      next_before_position: 10,
      has_more: true,
    }),
  );
  const commandResponse = mergeTimelinePage(
    older,
    normalizeTimelinePage({
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
    normalizeTimelinePage({ items: [{ type: "surprise", id: "x", position: 1, url: "file:///secret" }] }),
  );
  assert.deepEqual(projection.items[0], {
    anchorType: "unknown",
    id: "x",
    position: 1,
    rawType: "surprise",
  });
});

test("normalizes nested action timelines and backend event state aliases", () => {
  const page = normalizeTimelinePage({
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
  });
  const projection = mergeTimelinePage(createWorkspaceProjection(), page);
  const updated = applyOperationEvents(
    projection,
    normalizeEventPage({
      events: [
        { id: "event-13", operation_id: "op-1", sequence: 13, type: "progress", payload: { percent: 75 } },
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
    { error_message: "다시 실행할 수 없습니다.", error_detail: { retryable: false } },
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
    if (anchor.anchorType === "operation") assert.equal(anchor.retryable, false);
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
      events: [
        {
          id: "event-non-retryable",
          operation_id: "op-event-gate",
          sequence: 1,
          type: "operation.error",
          payload: {
            status: "failed",
            error: { safe_message: "결정적 오류입니다.", retryable: false },
          },
        },
      ],
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

test("preserves actionable operation states and only normalizes wire aliases", () => {
  const states = ["queued", "blocked", "cancelling", "dead_letter", "approval_required"];
  for (const state of states) {
    const anchor = normalizeTimelineAnchor(
      { kind: "operation_anchor", operation_id: `op-${state}`, position: 1, state },
      0,
    );
    assert.equal(anchor.anchorType, "operation");
    if (anchor.anchorType === "operation") assert.equal(anchor.status, state);
  }

  const deadLettered = normalizeOperationEvent({
    id: "event-dead",
    operation_id: "op-dead",
    sequence: 1,
    type: "dead_lettered",
  });
  assert.equal(deadLettered?.status, "dead_letter");

  const versioned = normalizeOperationEvent({
    id: "event-versioned",
    operation_id: "op-versioned",
    operation_version: 12,
    sequence: 3,
    type: "progress",
  });
  assert.equal(versioned?.operationVersion, 12);
});
