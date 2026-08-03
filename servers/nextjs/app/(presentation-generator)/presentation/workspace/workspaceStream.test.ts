import assert from "node:assert/strict";
import test from "node:test";

import {
  consumeWorkspaceSseStream,
  parseWorkspaceSseFrame,
  WorkspaceReconnectBackoff,
  workspaceReconnectDelay,
  WorkspaceStreamError,
} from "./workspaceStream.ts";

function streamOf(...chunks: string[]): ReadableStream<Uint8Array> {
  const encoder = new TextEncoder();
  return new ReadableStream({
    start(controller) {
      chunks.forEach((chunk) => controller.enqueue(encoder.encode(chunk)));
      controller.close();
    },
  });
}

test("only a valid workspace_event exposes an event cursor", () => {
  const valid = parseWorkspaceSseFrame(
    [
      "event: workspace_event",
      "id: 42",
      'data: {"schema_version":1,"id":"operation-1:2","operation_id":"operation-1","operation_version":2,"sequence":2,"cursor":"42","type":"progress","status":"running"}',
    ].join("\n"),
  );
  assert.equal(valid.kind, "workspace_event");
  if (valid.kind === "workspace_event") assert.equal(valid.cursor, "42");

  for (const block of [
    'event: future_event\nid: 43\ndata: {"operation_id":"operation-1","sequence":3}',
    "event: workspace_event\nid: 44\ndata: {not-json}",
    'event: workspace_event\nid: 45\ndata: {"operation_id":"operation-1"}',
    'event: workspace_event\nid: 45-schema-less\ndata: {"operation_id":"operation-1","sequence":4}',
    'event: workspace_event\nid: 46\ndata: {"schema_version":2,"operation_id":"operation-1","sequence":4}',
    'event: workspace_event\ndata: {"schema_version":1,"operation_id":"operation-1","sequence":5}',
    'event: workspace_event\nid: 47\ndata: {"schema_version":1,"id":"operation-1:4","operation_id":"operation-1","operation_version":4,"sequence":4,"cursor":"48","type":"progress"}',
    'event: workspace_event\nid: 48\ndata: {"schema_version":1,"id":"operation-1:4","operation_id":"operation-1","operation_version":-1,"sequence":4,"cursor":"48","type":"progress"}',
    'event: workspace_event\nid: 49\ndata: {"schema_version":1,"id":"operation-1:4","operation_id":"operation-1","operation_version":4,"sequence":4.5,"cursor":"49","type":"progress"}',
    'event: workspace_event\nid: 050\ndata: {"schema_version":1,"id":"operation-1:5","operation_id":"operation-1","operation_version":5,"sequence":5,"cursor":"050","type":"progress"}',
  ]) {
    const frame = parseWorkspaceSseFrame(block);
    assert.deepEqual(frame, {
      kind: "stream_error",
      code: "stream_protocol_error",
      retryable: true,
    });
  }
});

test("heartbeats do not expose or advance a cursor", () => {
  assert.deepEqual(
    parseWorkspaceSseFrame('event: heartbeat\nid: 999\ndata: {"cursor":"999"}'),
    { kind: "heartbeat" },
  );
  assert.deepEqual(parseWorkspaceSseFrame(": keep-alive"), {
    kind: "heartbeat",
  });
});

test("stream errors expose only the registered safe message", () => {
  assert.deepEqual(
    parseWorkspaceSseFrame(
      [
        "event: stream_error",
        'data: {"schema_version":1,"error":{"code":"timeline_unavailable","safe_message":"기록을 잠시 불러올 수 없습니다.","retryable":true},"message":"provider secret"}',
      ].join("\n"),
    ),
    {
      kind: "stream_error",
      code: "timeline_unavailable",
      message: "기록을 잠시 불러올 수 없습니다.",
      retryable: true,
    },
  );

  assert.deepEqual(
    parseWorkspaceSseFrame(
      'event: stream_error\ndata: {"code":"stream_error","message":"provider secret","retryable":false}',
    ),
    {
      kind: "stream_error",
      code: "stream_error",
      message: undefined,
      retryable: false,
    },
  );
});

test("reconnect delay is capped jittered exponential backoff", () => {
  assert.equal(
    workspaceReconnectDelay(1, () => 0),
    375,
  );
  assert.equal(
    workspaceReconnectDelay(1, () => 1),
    625,
  );
  assert.equal(
    workspaceReconnectDelay(2, () => 0.5),
    1000,
  );
  assert.equal(
    workspaceReconnectDelay(20, () => 0.5),
    10_000,
  );
});

test("clean EOF backoff keeps escalating even when every stream delivered an event", async () => {
  const backoff = new WorkspaceReconnectBackoff();
  const delays: number[] = [];
  for (let sequence = 1; sequence <= 3; sequence += 1) {
    await consumeWorkspaceSseStream(
      streamOf(
        `event: workspace_event\nid: ${sequence}\ndata: {"schema_version":1,"id":"operation-1:${sequence}","operation_id":"operation-1","operation_version":${sequence},"sequence":${sequence},"cursor":"${sequence}","type":"progress"}\n\n`,
      ),
      () => undefined,
    );
    delays.push(backoff.recordDisconnect(() => 0.5).delayMs);
  }
  assert.deepEqual(delays, [500, 1_000, 2_000]);

  backoff.resetAfterRebase();
  assert.equal(backoff.recordDisconnect(() => 0.5).delayMs, 500);
});

test("stream consumption rejects malformed and partial frames before acknowledging a cursor", async () => {
  for (const stream of [
    streamOf("event: workspace_event\nid: 80\ndata: {not-json}\n\n"),
    streamOf(
      'event: workspace_event\nid: 81\ndata: {"schema_version":1,"id":"operation-1:1","operation_id":"operation-1","operation_version":1,"sequence":1,"cursor":"81","type":"queued"}',
    ),
  ]) {
    const cursors: Array<string | undefined> = [];
    await assert.rejects(
      consumeWorkspaceSseStream(stream, (_event, cursor) => {
        cursors.push(cursor);
      }),
      (error: unknown) =>
        error instanceof WorkspaceStreamError &&
        error.code === "stream_protocol_error" &&
        error.retryable === true,
    );
    assert.deepEqual(cursors, []);
  }
});

test("stream events await projection work and cannot race a later sequence", async () => {
  const order: string[] = [];
  await consumeWorkspaceSseStream(
    streamOf(
      'event: workspace_event\nid: 1\ndata: {"schema_version":1,"id":"operation-1:1","operation_id":"operation-1","operation_version":1,"sequence":1,"cursor":"1","type":"queued"}\n\n',
      'event: workspace_event\nid: 2\ndata: {"schema_version":1,"id":"operation-1:2","operation_id":"operation-1","operation_version":2,"sequence":2,"cursor":"2","type":"progress"}\n\n',
    ),
    async (event) => {
      order.push(`start-${event.sequence}`);
      if (event.sequence === 1)
        await new Promise((resolve) => setTimeout(resolve, 5));
      order.push(`end-${event.sequence}`);
    },
  );
  assert.deepEqual(order, ["start-1", "end-1", "start-2", "end-2"]);
});
