"use client";

import { useCallback, useEffect, useSyncExternalStore } from "react";

import { getWorkspaceCapabilities } from "@/app/(presentation-generator)/services/api/workspace";
import type { WorkspaceCapability } from "@/app/(presentation-generator)/presentation/workspace/types";
import {
  isChatFirstWorkbenchEnabled,
  resolveChatFirstWorkbenchRollout,
  shouldApplyChatFirstCapabilityBroadcast,
  shouldCommitChatFirstCapabilityRequest,
  type ChatFirstRolloutMode,
} from "./chat-first-workbench";

const REVALIDATE_MS = 5_000;
const GLOBAL_CAPABILITY_KEY = "__global__";
const CAPABILITY_CHANNEL_NAME = "presenton-chat-first-capability-v1";
const EMPTY_CAPABILITY: WorkspaceCapability = {
  supported: false,
  timeline: false,
  commands: false,
  events: false,
};

interface CapabilitySnapshot {
  capability?: WorkspaceCapability;
  checkedAt: number;
}

const INITIAL_SNAPSHOT: CapabilitySnapshot = { checkedAt: 0 };
const snapshots = new Map<string, CapabilitySnapshot>();
const snapshotGenerations = new Map<string, number>();
const requests = new Map<string, Promise<void>>();
const listeners = new Set<() => void>();
const channels = new Set<BroadcastChannel>();

interface CapabilityBroadcast {
  kind: "capability";
  key: string;
  snapshot: CapabilitySnapshot;
}

function capabilityKey(presentationId?: string): string {
  return presentationId || GLOBAL_CAPABILITY_KEY;
}

function publish(
  key: string,
  next: CapabilitySnapshot,
  { broadcast = true }: { broadcast?: boolean } = {},
): void {
  snapshots.set(key, next);
  snapshotGenerations.set(key, (snapshotGenerations.get(key) ?? 0) + 1);
  listeners.forEach((listener) => listener());
  if (broadcast) {
    const message: CapabilityBroadcast = {
      kind: "capability",
      key,
      snapshot: next,
    };
    channels.forEach((channel) => channel.postMessage(message));
  }
}

function subscribe(listener: () => void): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

function getSnapshot(key: string): CapabilitySnapshot {
  return snapshots.get(key) ?? INITIAL_SNAPSHOT;
}

function getServerSnapshot(): CapabilitySnapshot {
  return INITIAL_SNAPSHOT;
}

function ensureCapability(
  presentationId?: string,
  force = false,
): Promise<void> {
  const key = capabilityKey(presentationId);
  const existingRequest = requests.get(key);
  if (existingRequest) return existingRequest;
  const current = getSnapshot(key);
  if (
    !force &&
    current.checkedAt > 0 &&
    Date.now() - current.checkedAt < REVALIDATE_MS
  ) {
    return Promise.resolve();
  }
  const startedAtGeneration = snapshotGenerations.get(key) ?? 0;
  const publishIfCurrent = (next: CapabilitySnapshot): void => {
    const currentGeneration = snapshotGenerations.get(key) ?? 0;
    if (
      !shouldCommitChatFirstCapabilityRequest(
        startedAtGeneration,
        currentGeneration,
        next.capability,
      )
    )
      return;
    publish(key, next);
  };
  const pending = getWorkspaceCapabilities(undefined, presentationId)
    .then((capability) =>
      publishIfCurrent({ capability, checkedAt: Date.now() }),
    )
    .catch((error: unknown) => {
      console.warn(
        "Workspace capability check failed; all mutations remain disabled.",
        error,
      );
      publishIfCurrent({
        capability: { ...EMPTY_CAPABILITY, reason: "unavailable" },
        checkedAt: Date.now(),
      });
    })
    .finally(() => {
      if (requests.get(key) === pending) requests.delete(key);
    });
  requests.set(key, pending);
  return pending;
}

export interface ChatFirstWorkbenchRollout {
  mode: ChatFirstRolloutMode;
  reason?: string;
  capability: WorkspaceCapability;
  refresh: () => Promise<void>;
}

/**
 * Shared runtime rollout resolver. Concurrent pages/components reuse one
 * authenticated capability request and periodically revalidate so a backend
 * kill switch takes effect without rebuilding the frontend.
 */
export function useChatFirstWorkbenchRollout(
  buildEnabled = isChatFirstWorkbenchEnabled(),
  presentationId?: string,
): ChatFirstWorkbenchRollout {
  const key = capabilityKey(presentationId);
  const readSnapshot = useCallback(() => getSnapshot(key), [key]);
  const current = useSyncExternalStore(
    subscribe,
    readSnapshot,
    getServerSnapshot,
  );

  useEffect(() => {
    if (!buildEnabled) return;
    let channel: BroadcastChannel | undefined;
    if (typeof BroadcastChannel !== "undefined") {
      channel = new BroadcastChannel(CAPABILITY_CHANNEL_NAME);
      channels.add(channel);
      channel.addEventListener("message", (event: MessageEvent<unknown>) => {
        if (
          !shouldApplyChatFirstCapabilityBroadcast(
            event.data,
            key,
            getSnapshot(key).checkedAt,
            getSnapshot(key).capability,
          )
        )
          return;
        publish(key, (event.data as CapabilityBroadcast).snapshot, {
          broadcast: false,
        });
      });
    }
    void ensureCapability(presentationId);
    const refreshImmediately = () =>
      void ensureCapability(presentationId, true);
    const interval = window.setInterval(refreshImmediately, REVALIDATE_MS);
    const onVisibilityChange = () => {
      if (document.visibilityState === "visible") refreshImmediately();
    };
    document.addEventListener("visibilitychange", onVisibilityChange);
    window.addEventListener("focus", refreshImmediately);
    window.addEventListener("online", refreshImmediately);
    window.addEventListener("pageshow", refreshImmediately);
    return () => {
      window.clearInterval(interval);
      document.removeEventListener("visibilitychange", onVisibilityChange);
      window.removeEventListener("focus", refreshImmediately);
      window.removeEventListener("online", refreshImmediately);
      window.removeEventListener("pageshow", refreshImmediately);
      if (channel) {
        channels.delete(channel);
        channel.close();
      }
    };
  }, [buildEnabled, key, presentationId]);

  const decision = resolveChatFirstWorkbenchRollout(
    buildEnabled,
    current.capability,
  );
  return {
    ...decision,
    capability: current.capability ?? EMPTY_CAPABILITY,
    refresh: () => ensureCapability(presentationId, true),
  };
}

/** Test-only reset for component isolation; application code must revalidate. */
export function resetChatFirstWorkbenchRolloutForTests(): void {
  requests.clear();
  snapshots.clear();
  snapshotGenerations.clear();
  channels.forEach((channel) => channel.close());
  channels.clear();
  listeners.forEach((listener) => listener());
}
