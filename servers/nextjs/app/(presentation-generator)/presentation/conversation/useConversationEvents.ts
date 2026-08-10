"use client";

import { useEffect, useRef, useState } from "react";
import { applyConversationEvent } from "./timeline";
import type { ConversationEvent, ConversationTimeline, ConversationTransport } from "./types";

function cursorRecoveryRequired(reason: unknown): boolean {
  if (!reason || typeof reason !== "object") return false;
  const failure = reason as { status?: unknown; code?: unknown };
  return failure.status === 410
    || failure.code === "event_cursor_expired"
    || failure.code === "cursor_expired"
    || failure.code === "invalid_event_cursor";
}

/** Reconnects from a durable cursor; checkpoints reconstruct assistant output after refresh. */
export function useConversationEvents(options: {
  transport: ConversationTransport;
  conversationId?: string;
  timeline: ConversationTimeline;
  onTimeline: (update: (current: ConversationTimeline) => ConversationTimeline) => void;
  onPresentationPublished?: (revision?: number) => void;
}) {
  const { transport, conversationId, timeline, onTimeline, onPresentationPublished } = options;
  const [connection, setConnection] = useState<"connecting" | "live" | "offline">("connecting");
  const cursorRef = useRef(timeline.cursor);
  useEffect(() => {
    cursorRef.current = timeline.cursor;
  }, [timeline.cursor]);

  useEffect(() => {
    if (!conversationId) return;
    const controller = new AbortController();
    let retries = 0;
    const listen = async () => {
      while (!controller.signal.aborted) {
        setConnection(retries === 0 ? "connecting" : "offline");
        try {
          await transport.streamEvents(conversationId, {
            after: cursorRef.current,
            signal: controller.signal,
            onEvent: (event: ConversationEvent) => {
              if (event.type === "presentation.published") onPresentationPublished?.(event.revision);
              onTimeline((current) => applyConversationEvent(current, event));
              if (event.cursor) cursorRef.current = event.cursor;
              setConnection("live");
            },
          });
          // A proxy/browser can close an otherwise healthy SSE response. Do
          // not spin a new connection immediately: resume from the durable
          // cursor after a short, bounded delay just as we do after failures.
          retries += 1;
        } catch (reason) {
          if (controller.signal.aborted) return;
          if (cursorRecoveryRequired(reason)) {
            try {
              // Retention and malformed/ahead cursors must rebase from the
              // canonical timeline. Its high-watermark replaces the stale
              // Last-Event-ID before the next connection attempt.
              const recovered = await transport.getTimeline(conversationId, { signal: controller.signal });
              if (controller.signal.aborted) return;
              onTimeline(() => recovered);
              cursorRef.current = recovered.cursor;
              retries = 0;
            } catch {
              // Keep the existing cursor until the next retry; the failure is
              // visible through the reconnect state rather than losing local UI.
            }
          }
          retries += 1;
        }
        if (controller.signal.aborted) return;
        setConnection("offline");
        await new Promise((resolve) => window.setTimeout(resolve, Math.min(500 * 2 ** Math.min(retries, 4), 8_000)));
      }
    };
    void listen();
    return () => controller.abort();
  }, [conversationId, onPresentationPublished, onTimeline, transport]);
  return { connection };
}
