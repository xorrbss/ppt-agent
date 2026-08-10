"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { ChatSurface } from "./ChatSurface";
import { useConversationEvents } from "./useConversationEvents";
import { useConversationTimeline } from "./useConversationTimeline";
import type { ConversationAsset, ConversationCommandDispatcher, ConversationContext, ConversationTransport } from "./types";

export function ConversationController(props: {
  presentationId: string;
  transport: ConversationTransport;
  context?: ConversationContext;
  onPresentationPublished?: (revision?: number) => void;
  onSendReady?: (send: ConversationCommandDispatcher | null) => void;
  onAgentActivityChange?: (working: boolean) => void;
}) {
  const { presentationId, transport, context, onPresentationPublished } = props;
  const {
    conversation,
    timeline,
    loading,
    submitting,
    error,
    send: sendTimeline,
    performAction,
    updateTimeline,
  } = useConversationTimeline({ presentationId, transport });
  const onSendReadyRef = useRef(props.onSendReady);
  const onAgentActivityChangeRef = useRef(props.onAgentActivityChange);
  useEffect(() => {
    onSendReadyRef.current = props.onSendReady;
    onAgentActivityChangeRef.current = props.onAgentActivityChange;
  }, [props.onAgentActivityChange, props.onSendReady]);
  const [pendingAssets, setPendingAssets] = useState<ConversationAsset[]>([]);
  const safeContext = useMemo<ConversationContext | undefined>(() => {
    if (!context) return undefined;
    return { ...context, assetIds: pendingAssets.filter((asset) => asset.status === "ready" || asset.status === "scanned").map((asset) => asset.id) };
  }, [context, pendingAssets]);
  const { connection } = useConversationEvents({ transport, conversationId: conversation?.id, timeline, onTimeline: updateTimeline, onPresentationPublished });
  const send = useCallback<ConversationCommandDispatcher>(async (content, options) => {
    await sendTimeline(
      content,
      safeContext,
      options?.assetIds ?? safeContext?.assetIds,
      options?.commandType,
      options?.context,
    );
    setPendingAssets([]);
  }, [safeContext, sendTimeline]);
  useEffect(() => {
    if (!conversation) {
      onSendReadyRef.current?.(null);
      return;
    }
    onSendReadyRef.current?.(send);
    return () => onSendReadyRef.current?.(null);
  }, [conversation, send]);
  useEffect(() => {
    const working = timeline.items.some(
      (item) => item.kind === "run" && ["queued", "running", "cancelling"].includes(item.run.status),
    );
    onAgentActivityChangeRef.current?.(working);
  }, [timeline.items]);
  const upload = useCallback(async (files: File[]) => {
    if (!transport.uploadAsset) throw new Error("Attachments are unavailable");
    if (!conversation) throw new Error("Conversation is not ready");
    const uploaded = await Promise.all(files.map((file) => transport.uploadAsset!(conversation.id, file)));
    setPendingAssets((current) => [...current, ...uploaded.filter((asset) => asset.status === "ready" || asset.status === "scanned")]);
  }, [conversation, transport]);
  return <ChatSurface timeline={timeline} loading={loading} submitting={submitting} error={error} connection={connection} pendingAssets={pendingAssets} context={safeContext} onUpload={transport.uploadAsset ? upload : undefined} onRemoveAsset={(assetId) => setPendingAssets((current) => current.filter((asset) => asset.id !== assetId))} onSend={send} onAction={performAction} />;
}
