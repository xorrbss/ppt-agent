"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { mergeConversationTimeline } from "./timeline";
import type { Conversation, ConversationContext, ConversationRunAction, ConversationTimeline, ConversationTransport } from "./types";

const EMPTY_TIMELINE: ConversationTimeline = { items: [], hasMore: false };

function idempotencyKey(): string {
  return typeof crypto !== "undefined" && typeof crypto.randomUUID === "function"
    ? crypto.randomUUID()
    : `${Date.now()}-${Math.random().toString(36).slice(2)}`;
}

export function useConversationTimeline(options: {
  transport: ConversationTransport;
  presentationId: string;
  enabled?: boolean;
}) {
  const { transport, presentationId, enabled = true } = options;
  const [conversation, setConversation] = useState<Conversation | null>(null);
  const [timeline, setTimeline] = useState<ConversationTimeline>(EMPTY_TIMELINE);
  const [loading, setLoading] = useState(enabled);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const timelineRef = useRef(timeline);
  useEffect(() => {
    timelineRef.current = timeline;
  }, [timeline]);

  const refresh = useCallback(async (signal?: AbortSignal) => {
    if (!enabled) return null;
    const nextConversation = await transport.getOrCreate(presentationId, signal);
    const nextTimeline = await transport.getTimeline(nextConversation.id, { signal });
    if (signal?.aborted) return null;
    setConversation(nextConversation);
    setTimeline(nextTimeline);
    return nextConversation;
  }, [enabled, presentationId, transport]);

  useEffect(() => {
    if (!enabled) {
      setConversation(null);
      setTimeline(EMPTY_TIMELINE);
      setLoading(false);
      return;
    }
    const controller = new AbortController();
    setLoading(true);
    setError(null);
    void refresh(controller.signal)
      .catch((reason: unknown) => {
        if (!controller.signal.aborted) setError(reason instanceof Error ? reason.message : "Unable to load conversation");
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });
    return () => controller.abort();
  }, [enabled, refresh]);

  const send = useCallback(async (
    content: string,
    context?: ConversationContext,
    assetIds?: string[],
    commandType?: string,
    operationContext?: Record<string, unknown>,
  ) => {
    if (!conversation) throw new Error("Conversation is not ready");
    setSubmitting(true);
    setError(null);
    try {
      const incoming = await transport.sendMessage(conversation.id, {
        content,
        context,
        assetIds,
        idempotencyKey: idempotencyKey(),
        expectedRevision: timelineRef.current.revision,
        commandType,
        operationContext,
      });
      setTimeline((current) => mergeConversationTimeline(current, incoming));
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Unable to send message");
      throw reason;
    } finally {
      setSubmitting(false);
    }
  }, [conversation, transport]);

  const performAction = useCallback(async (runId: string, action: ConversationRunAction) => {
    if (!conversation) throw new Error("Conversation is not ready");
    const run = timelineRef.current.items.find((item) => item.kind === "run" && item.run.id === runId);
    setSubmitting(true);
    setError(null);
    try {
      const incoming = await transport.performRunAction(conversation.id, runId, action, {
        idempotencyKey: idempotencyKey(),
        expectedRevision: timelineRef.current.revision,
        expectedRunVersion: run?.kind === "run" ? run.run.version : undefined,
      });
      setTimeline((current) => mergeConversationTimeline(current, incoming));
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Unable to update run");
      throw reason;
    } finally {
      setSubmitting(false);
    }
  }, [conversation, transport]);

  const updateTimeline = useCallback(
    (update: (current: ConversationTimeline) => ConversationTimeline) => setTimeline(update),
    [],
  );

  return { conversation, timeline, loading, submitting, error, refresh, send, performAction, updateTimeline };
}
