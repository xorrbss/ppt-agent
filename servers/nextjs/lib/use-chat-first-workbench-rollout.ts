"use client";

import { useEffect, useSyncExternalStore } from "react";

import { getWorkspaceCapabilities } from "@/app/(presentation-generator)/services/api/workspace";
import type { WorkspaceCapability } from "@/app/(presentation-generator)/presentation/workspace/types";
import {
  isChatFirstWorkbenchEnabled,
  resolveChatFirstWorkbenchRollout,
  type ChatFirstRolloutMode,
} from "./chat-first-workbench";

const REVALIDATE_MS = 30_000;
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
let snapshot = INITIAL_SNAPSHOT;
let request: Promise<void> | null = null;
const listeners = new Set<() => void>();

function publish(next: CapabilitySnapshot): void {
  snapshot = next;
  listeners.forEach((listener) => listener());
}

function subscribe(listener: () => void): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

function getSnapshot(): CapabilitySnapshot {
  return snapshot;
}

function getServerSnapshot(): CapabilitySnapshot {
  return INITIAL_SNAPSHOT;
}

function ensureCapability(force = false): Promise<void> {
  if (request) return request;
  if (!force && snapshot.checkedAt > 0 && Date.now() - snapshot.checkedAt < REVALIDATE_MS) {
    return Promise.resolve();
  }
  const pending = getWorkspaceCapabilities()
    .then((capability) => publish({ capability, checkedAt: Date.now() }))
    .catch((error: unknown) => {
      console.warn("Workspace capability check failed; all mutations remain disabled.", error);
      publish({
        capability: { ...EMPTY_CAPABILITY, reason: "unavailable" },
        checkedAt: Date.now(),
      });
    })
    .finally(() => {
      if (request === pending) request = null;
    });
  request = pending;
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
): ChatFirstWorkbenchRollout {
  const current = useSyncExternalStore(subscribe, getSnapshot, getServerSnapshot);

  useEffect(() => {
    if (!buildEnabled) return;
    void ensureCapability();
    const interval = window.setInterval(() => void ensureCapability(true), REVALIDATE_MS);
    const onVisibilityChange = () => {
      if (document.visibilityState === "visible") void ensureCapability();
    };
    document.addEventListener("visibilitychange", onVisibilityChange);
    return () => {
      window.clearInterval(interval);
      document.removeEventListener("visibilitychange", onVisibilityChange);
    };
  }, [buildEnabled]);

  const decision = resolveChatFirstWorkbenchRollout(buildEnabled, current.capability);
  return {
    ...decision,
    capability: current.capability ?? EMPTY_CAPABILITY,
    refresh: () => ensureCapability(true),
  };
}

/** Test-only reset for component isolation; application code must revalidate. */
export function resetChatFirstWorkbenchRolloutForTests(): void {
  request = null;
  publish(INITIAL_SNAPSHOT);
}
