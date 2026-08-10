import type { AgentRun, ConversationEvent, ConversationTimeline, ConversationTimelineItem } from "./types";

function updateRun(items: ConversationTimelineItem[], runId: string, patch: Partial<AgentRun>) {
  return items.map((item) =>
    item.kind === "run" && item.run.id === runId
      ? { ...item, run: { ...item.run, ...patch } }
      : item,
  );
}

/** Applies replay-safe durable state and ephemeral assistant output separately. */
export function applyConversationEvent(
  timeline: ConversationTimeline,
  event: ConversationEvent,
): ConversationTimeline {
  let items = timeline.items;
  if (event.message) {
    const incomingMessage = event.message;
    const existing = items.find((item) => item.kind === "message" && item.message.id === incomingMessage.id);
    items = existing
      ? items.map((item) => item.kind === "message" && item.message.id === incomingMessage.id
        ? { ...item, message: { ...item.message, ...incomingMessage, attachments: incomingMessage.attachments.length ? incomingMessage.attachments : item.message.attachments } }
        : item)
      : [...items, { kind: "message", position: Number.MAX_SAFE_INTEGER, message: incomingMessage }];
  }
  if (event.run) {
    const incomingRun = event.run;
    const existing = items.find((item) => item.kind === "run" && item.run.id === incomingRun.id);
    items = existing
      ? items.map((item) => item.kind === "run" && item.run.id === incomingRun.id
        ? { ...item, run: { ...item.run, ...incomingRun, actions: incomingRun.actions ?? item.run.actions } }
        : item)
      : [...items, { kind: "run", position: Number.MAX_SAFE_INTEGER, run: incomingRun }];
  }
  if (event.tool) {
    const existing = items.find((item) => item.kind === "tool" && item.tool.id === event.tool!.id);
    items = existing
      ? items.map((item) => item.kind === "tool" && item.tool.id === event.tool!.id
        ? { ...item, tool: { ...item.tool, ...event.tool } }
        : item)
      : [...items, { kind: "tool", position: Number.MAX_SAFE_INTEGER, tool: event.tool }];
  }
  if (event.runId && event.status) items = updateRun(items, event.runId, { status: event.status });
  if (event.runId && (event.delta !== undefined || event.checkpoint !== undefined)) {
    const content = event.checkpoint ?? event.delta ?? "";
    // The durable contract identifies assistant output by message_id. Legacy
    // events without it retain the run-scoped fallback, but cannot duplicate a
    // recovered checkpoint that already has its canonical message identity.
    const id = event.messageId ?? `assistant-${event.runId}`;
    const current = items.find((item) => item.kind === "message" && item.message.id === id);
    const message = current?.kind === "message" ? current.message : { id, role: "assistant" as const, content: "", attachments: [] };
    const nextContent = event.checkpoint ?? `${message.content}${content}`;
    const next = { ...message, content: nextContent, streaming: event.type !== "run.completed" && event.type !== "run.failed" };
    items = current
      ? items.map((item) => item.kind === "message" && item.message.id === id ? { ...item, message: next } : item)
      : [...items, { kind: "message", position: Number.MAX_SAFE_INTEGER, message: next }];
  }
  if (event.runId && (event.type === "run.completed" || event.type === "run.failed")) {
    const assistantId = event.messageId ?? `assistant-${event.runId}`;
    items = items.map((item) => item.kind === "message" && item.message.id === assistantId ? { ...item, message: { ...item.message, streaming: false } } : item);
  }
  return { ...timeline, items, cursor: event.cursor ?? timeline.cursor, revision: event.revision ?? timeline.revision };
}

export function mergeConversationTimeline(current: ConversationTimeline, incoming: ConversationTimeline): ConversationTimeline {
  const indexed = new Map<string, ConversationTimelineItem>();
  for (const item of current.items) indexed.set(`${item.kind}:${item.kind === "message" ? item.message.id : item.kind === "run" ? item.run.id : item.tool.id}`, item);
  for (const item of incoming.items) indexed.set(`${item.kind}:${item.kind === "message" ? item.message.id : item.kind === "run" ? item.run.id : item.tool.id}`, item);
  return { ...incoming, items: [...indexed.values()].sort((a, b) => a.position - b.position), cursor: incoming.cursor ?? current.cursor };
}
